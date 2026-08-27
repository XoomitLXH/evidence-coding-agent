from __future__ import annotations

import os
import subprocess
import tempfile
import time
import shlex
from pathlib import Path

from .policy import PolicyError, classify_command, explain_command
from .state import AgentState
from .workspace import changed_files, snapshot


def run_command(root: Path, state: AgentState, command: str, timeout_seconds: int = 60) -> dict:
    decision = classify_command(command)
    if decision != "allow":
        raise PolicyError(explain_command(command))
    if timeout_seconds < 1 or timeout_seconds > 300:
        raise PolicyError("timeout_seconds must be between 1 and 300")
    before = snapshot(root)
    started = time.monotonic()
    try:
        environment = os.environ.copy()
        # Do not let a same-second, same-size source edit reuse a stale workspace .pyc.
        with tempfile.TemporaryDirectory(prefix="coding-agent-pycache-") as pycache_dir:
            environment["PYTHONPYCACHEPREFIX"] = pycache_dir
            completed = subprocess.run(
                shlex.split(command),
                cwd=root,
                text=True,
                capture_output=True,
                timeout=timeout_seconds,
                env=environment,
            )
        output = (completed.stdout + completed.stderr)[-12000:]
        exit_code = completed.returncode
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") + (exc.stderr or ""))[-12000:] + "\nTIMEOUT"
        exit_code = 124
    duration_ms = int((time.monotonic() - started) * 1000)
    changed = changed_files(root, before)
    if changed:
        state.mark_modified(changed)
    state.record_command(command, exit_code, output, duration_ms, workspace_changed=bool(changed))
    return {"command": command, "exit_code": exit_code, "duration_ms": duration_ms,
            "output": output, "changed_files": changed}
