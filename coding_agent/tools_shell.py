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
from .workspace import changed_files, snapshot

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
            "risk": risk,
            "failure": failure,
            "evidence": _evidence(command, None, 0, failure["reason"]),
        }
    if timeout_seconds < 1 or timeout_seconds > 300:
        raise PolicyError("timeout_seconds must be between 1 and 300")
    started = time.monotonic()
    failure = None
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
            completed = subprocess.run(
                _command_arguments(executable_command),
                cwd=command_root,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                env=environment,
            )
            changed = changed_files(command_root, before)
        output = (completed.stdout + completed.stderr)[-12000:]
        exit_code = completed.returncode
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
    if "changed" not in locals():
        changed = []
    duration_ms = int((time.monotonic() - started) * 1000)
    if changed and drafts is None:
        state.mark_modified(changed)
    state.record_command(command, exit_code, output, duration_ms, workspace_changed=bool(changed))
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
        "risk": risk,
        "failure": failure,
        "evidence": _evidence(command, exit_code, duration_ms, output),
        "timed_out": failure is not None and failure["type"] == "timeout",
    }
