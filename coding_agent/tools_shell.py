from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from .policy import PolicyError, command_decision
from .state import AgentState
from .workspace import snapshot

if TYPE_CHECKING:
    from .draft import DraftChanges


def _strip_workspace_cd_prefix(root: Path, command: str) -> str:
    """Accept one no-op `cd <workspace> &&` prefix without enabling shell chaining."""
    parts = command.split("&&")
    if len(parts) != 2:
        return command
    try:
        cd_args = shlex.split(parts[0])
    except ValueError:
        return command
    if len(cd_args) != 2 or cd_args[0] != "cd":
        return command
    requested = Path(cd_args[1]).expanduser()
    target = (requested if requested.is_absolute() else root / requested).resolve()
    if target != root.resolve():
        return command
    return parts[1].strip()


def _text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def _output_summary(output: str) -> str:
    cleaned = output.strip()
    if not cleaned:
        return "无输出。"
    if len(cleaned) <= 1200:
        return cleaned
    return f"{cleaned[-1200:]}\n（已省略较早输出）"


def _failure(failure_type: str, label: str, reason: str, recovery: str) -> dict[str, str]:
    return {
        "type": failure_type,
        "label": label,
        "reason": reason,
        "recovery": recovery,
    }


def _evidence(command: str, exit_code: int | None, duration_ms: int, output: str) -> dict[str, object]:
    return {
        "tool": "run_command",
        "parameters": f"command={command}",
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "output_summary": _output_summary(output),
    }


def _command_arguments(command: str) -> list[str]:
    arguments = shlex.split(command)
    # The policy permits Python aliases; map them to the active interpreter consistently.
    if arguments and arguments[0] in {"python", "python3"}:
        arguments[0] = sys.executable
    return arguments


def _command_pipeline(command: str) -> list[list[str]]:
    """Parse a small, shell-free pipeline while respecting quoted pipe characters."""
    segments: list[str] = []
    start = 0
    quote: str | None = None
    escaped = False
    for index, character in enumerate(command):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote != "'":
            escaped = True
            continue
        if quote:
            if character == quote:
                quote = None
            continue
        if character in {"'", '"'}:
            quote = character
        elif character == "|":
            segment = command[start:index].strip()
            if not segment:
                raise ValueError("pipeline contains an empty command")
            segments.append(segment)
            start = index + 1
    if quote:
        raise ValueError("unterminated quote in command")
    segment = command[start:].strip()
    if not segment:
        raise ValueError("pipeline contains an empty command")
    segments.append(segment)
    return [_command_arguments(segment) for segment in segments]


def _run_pipeline(
    commands: list[list[str]],
    *,
    cwd: Path,
    environment: dict[str, str],
    timeout_seconds: int,
) -> tuple[int, str]:
    """Run a parsed pipeline without invoking a shell."""
    processes: list[subprocess.Popen[str]] = []
    previous_stdout = None
    try:
        for index, arguments in enumerate(commands):
            process = subprocess.Popen(
                arguments,
                cwd=cwd,
                text=True,
                stdin=previous_stdout,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
            )
            if previous_stdout is not None:
                previous_stdout.close()
            previous_stdout = process.stdout
            processes.append(process)
        started = time.monotonic()
        stdout, stderr = processes[-1].communicate(timeout=timeout_seconds)
        # The final process has consumed the pipe; collect diagnostics from upstream stages.
        upstream_output: list[str] = []
        for process in processes[:-1]:
            _, process_stderr = process.communicate(timeout=max(0.1, timeout_seconds - (time.monotonic() - started)))
            if process_stderr:
                upstream_output.append(process_stderr)
        output = "".join(upstream_output) + (stdout or "") + (stderr or "")
        return processes[-1].returncode, output
    except subprocess.TimeoutExpired:
        for process in processes:
            process.kill()
        for process in processes:
            process.communicate()
        raise
    except BaseException:
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate()
        raise


def _write_drafts(workspace: Path, drafts: "DraftChanges") -> None:
    """Overlay the pending agent edits on an isolated validation workspace."""
    for entry in drafts.diff():
        path = workspace / entry["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry["after"], encoding="utf-8")


def run_command(
    root: Path,
    state: AgentState,
    command: str,
    timeout_seconds: int = 60,
    *,
    drafts: "DraftChanges | None" = None,
    approval_granted: bool = False,
) -> dict:
    executable_command = _strip_workspace_cd_prefix(root, command)
    risk = command_decision(executable_command)
    if risk["decision"] != "allow" and not (
        risk["decision"] == "approval_required" and approval_granted
    ):
        if risk["decision"] == "approval_required":
            failure = _failure(
                "awaiting_approval",
                "等待命令确认",
                risk["reason"],
                risk["recommendation"],
            )
        else:
            failure = _failure(
                "policy_rejected",
                "安全策略阻止执行",
                risk["reason"],
                risk["recommendation"],
            )
        return {
            "ok": False,
            "command": command,
            "exit_code": None,
            "duration_ms": 0,
            "output": "",
            "changed_files": [],
            "deleted_files": [],
            "approved_mutation": False,
            "risk": risk,
            "failure": failure,
            "evidence": _evidence(command, None, 0, failure["reason"]),
        }
    if timeout_seconds < 1 or timeout_seconds > 300:
        raise PolicyError("timeout_seconds must be between 1 and 300")
    started = time.monotonic()
    failure = None
    deleted: list[str] = []
    changed: list[str] = []
    try:
        environment = os.environ.copy()
        # Do not let a same-second, same-size source edit reuse a stale workspace .pyc.
        with tempfile.TemporaryDirectory(prefix="coding-agent-pycache-") as pycache_dir, tempfile.TemporaryDirectory(prefix="coding-agent-verify-") as temporary_root:
            environment["PYTHONPYCACHEPREFIX"] = pycache_dir
            command_root = root
            if drafts is not None:
                command_root = Path(temporary_root) / "workspace"
                shutil.copytree(root, command_root, symlinks=True)
                _write_drafts(command_root, drafts)
            before = snapshot(command_root)
            pipeline = _command_pipeline(executable_command)
            if len(pipeline) == 1:
                completed = subprocess.run(
                    pipeline[0],
                    cwd=command_root,
                    text=True,
                    capture_output=True,
                    timeout=timeout_seconds,
                    env=environment,
                )
                output = completed.stdout + completed.stderr
                exit_code = completed.returncode
            else:
                exit_code, output = _run_pipeline(
                    pipeline,
                    cwd=command_root,
                    environment=environment,
                    timeout_seconds=timeout_seconds,
                )
            after = snapshot(command_root)
            deleted = sorted(set(before) - set(after))
            changed = sorted(
                {*before, *after}
                - {key for key in before.keys() & after.keys() if before[key] == after[key]}
            )
        output = output[-12000:]
    except ValueError as exc:
        output = str(exc)
        exit_code = 2
        failure = _failure(
            "command_failed",
            "命令格式无效",
            f"无法解析命令：{exc}",
            "仅支持常规命令和简单的只读管道（命令之间使用单个 |）。",
        )
    except subprocess.TimeoutExpired as exc:
        output = (_text(exc.stdout) + _text(exc.stderr))[-12000:] + "\nTIMEOUT"
        exit_code = 124
        failure = _failure(
            "timeout",
            "命令执行超时",
            f"命令在 {timeout_seconds} 秒内没有完成。",
            "检查命令是否等待输入、依赖网络或需要更小的执行范围，然后重试。",
        )
    except OSError as exc:
        output = str(exc)
        exit_code = 127
        failure = _failure(
            "command_failed",
            "命令无法启动",
            f"无法启动命令：{exc}",
            "检查命令名称、项目依赖和本地运行环境后重试。",
        )
    duration_ms = int((time.monotonic() - started) * 1000)
    if changed and drafts is None:
        state.mark_modified(changed)
    approved_mutation = bool(
        approval_granted
        and risk["decision"] == "approval_required"
        and exit_code == 0
        and drafts is None
    )
    state.record_command(
        command,
        exit_code,
        output,
        duration_ms,
        workspace_changed=bool(changed),
        approved_mutation=approved_mutation,
    )
    if exit_code != 0 and failure is None:
        failure = _failure(
            "command_failed",
            "命令执行失败",
            f"命令以退出码 {exit_code} 结束。",
            "查看终端输出，修复问题后重新运行该命令。",
        )
    return {
        "ok": exit_code == 0,
        "command": command,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "output": output,
        "changed_files": changed,
        "deleted_files": deleted,
        "approved_mutation": approved_mutation,
        "risk": risk,
        "failure": failure,
        "evidence": _evidence(command, exit_code, duration_ms, output),
        "timed_out": failure is not None and failure["type"] == "timeout",
    }
