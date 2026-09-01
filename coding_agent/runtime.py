from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .policy import PolicyError, safe_path


DEFAULT_TIMEOUT_SECONDS = 10.0
MAX_TIMEOUT_SECONDS = 120.0


@dataclass(frozen=True)
class ExecutionResult:
    path: str
    command: str
    mode: str
    stdout: str
    stderr: str
    exit_code: int | None
    duration_ms: int
    timed_out: bool = False
    error: str | None = None
    debug_strategy: str | None = None
    test_path: str | None = None

    @property
    def ok(self) -> bool:
        return not self.timed_out and self.exit_code == 0

    @property
    def output(self) -> str:
        if self.stdout and self.stderr:
            return f"{self.stdout.rstrip()}\n{self.stderr}"
        return self.stdout or self.stderr

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "ok": self.ok,
            "path": self.path,
            "command": self.command,
            "mode": self.mode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "output": self.output,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
        }
        if self.timed_out:
            payload["timed_out"] = True
        if self.error:
            payload["error"] = self.error
        if self.debug_strategy:
            payload["debug_strategy"] = self.debug_strategy
        if self.test_path:
            payload["test_path"] = self.test_path
        return payload


def _timeout_seconds(value: object) -> float:
    if value is None:
        return DEFAULT_TIMEOUT_SECONDS
    if isinstance(value, bool):
        raise ValueError("timeout_seconds must be a number")
    try:
        timeout = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be a number") from exc
    if not 0.05 <= timeout <= MAX_TIMEOUT_SECONDS:
        raise ValueError("timeout_seconds must be between 0.05 and 120 seconds")
    return timeout


def _find_matching_test(root: Path, file_path: Path) -> Path | None:
    """Find a focused unittest module for a source file, if one exists."""
    stem = file_path.stem
    if stem.startswith("test_") or stem.endswith("_test"):
        names = {file_path.name}
    else:
        names = {f"test_{stem}.py", f"{stem}_test.py"}

    directories = [file_path.parent, root / "tests", root]
    seen: set[Path] = set()
    candidates: list[Path] = []
    for directory in directories:
        directory = directory.resolve()
        if directory in seen or not directory.is_dir():
            continue
        seen.add(directory)
        try:
            paths = directory.rglob("*.py")
        except OSError:
            continue
        for candidate in paths:
            if candidate.name not in names or not candidate.is_file():
                continue
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if any(part.startswith(".") for part in candidate.relative_to(root).parts):
                continue
            candidates.append(candidate.resolve())
    if not candidates:
        return None
    # Prefer a test next to the target, then the shallowest path, then stable order.
    return min(
        set(candidates),
        key=lambda candidate: (
            0 if candidate.parent == file_path.parent.resolve() else 1,
            len(candidate.relative_to(root).parts),
            str(candidate),
        ),
    )


def execute_python(
    root: Path,
    path: str,
    *,
    mode: str = "run",
    timeout_seconds: object = None,
) -> ExecutionResult:
    """Execute one workspace Python file without invoking a shell."""
    if mode not in {"run", "debug"}:
        raise ValueError("mode must be run or debug")
    if not isinstance(path, str):
        raise ValueError("path must be a string")
    file_path = safe_path(root, path)
    if file_path.suffix.lower() != ".py":
        raise PolicyError("运行和调试目前只支持 Python 文件（.py）")
    if not file_path.is_file():
        raise PolicyError(f"not a file: {path}")
    timeout = _timeout_seconds(timeout_seconds)
    relative_path = str(file_path.relative_to(root.resolve()))
    executable_args = [sys.executable]
    debug_strategy: str | None = None
    test_path: str | None = None
    if mode == "debug":
        matching_test = _find_matching_test(root.resolve(), file_path)
        if matching_test:
            debug_strategy = "tests"
            test_path = str(matching_test.relative_to(root.resolve()))
            test_dir = str(matching_test.parent.relative_to(root.resolve())) or "."
            executable_args.extend(["-m", "unittest", "discover", "-s", test_dir, "-p", matching_test.name, "-v"])
        else:
            debug_strategy = "faulthandler"
            executable_args.extend(["-X", "faulthandler"])
            executable_args.append(str(file_path))
    else:
        executable_args.append(str(file_path))
    # Keep the displayed command identical to the argv sent to Python. The
    # workspace-relative path is already sufficient because the process runs
    # with the workspace as its cwd.
    display_args = ["python3"]
    if mode == "debug" and debug_strategy == "tests":
        display_args.extend(["-m", "unittest", "discover", "-s", test_dir, "-p", matching_test.name, "-v"])
    elif mode == "debug":
        display_args.extend(["-X", "faulthandler"])
        display_args.append(relative_path)
    else:
        display_args.append(relative_path)
    command = shlex.join(display_args)
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="coding-agent-pycache-") as cache_dir:
        environment = os.environ.copy()
        environment["PYTHONPYCACHEPREFIX"] = cache_dir
        environment["PYTHONUNBUFFERED"] = "1"
        process = subprocess.Popen(
            executable_args,
            cwd=str(root.resolve()),
            env=environment,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            stdout, stderr = process.communicate()
            if isinstance(exc.stdout, str) and not stdout:
                stdout = exc.stdout
            if isinstance(exc.stderr, str) and not stderr:
                stderr = exc.stderr
            duration_ms = int((time.perf_counter() - started) * 1000)
            return ExecutionResult(
                path=relative_path,
                command=command,
                mode=mode,
                stdout=stdout or "",
                stderr=stderr or "",
                exit_code=None,
                duration_ms=duration_ms,
                timed_out=True,
                error=f"执行超时（超过 {timeout:g} 秒）",
                debug_strategy=debug_strategy,
                test_path=test_path,
            )
        except OSError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            return ExecutionResult(
                path=relative_path,
                command=command,
                mode=mode,
                stdout="",
                stderr="",
                exit_code=None,
                duration_ms=duration_ms,
                error=f"无法启动 Python 进程：{exc}",
                debug_strategy=debug_strategy,
                test_path=test_path,
            )
    duration_ms = int((time.perf_counter() - started) * 1000)
    return ExecutionResult(
        path=relative_path,
        command=command,
        mode=mode,
        stdout=stdout,
        stderr=stderr,
        exit_code=process.returncode,
        duration_ms=duration_ms,
        debug_strategy=debug_strategy,
        test_path=test_path,
    )
