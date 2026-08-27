from __future__ import annotations

import re
import shlex
from pathlib import Path


class PolicyError(ValueError):
    pass


def safe_path(root: Path, user_path: str) -> Path:
    if not user_path or "\x00" in user_path:
        raise PolicyError("path must be a non-empty string without NUL bytes")
    candidate = (root / user_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError as exc:
        raise PolicyError("path escapes the workspace") from exc
    return candidate


_DENY_PATTERNS = (
    r"(^|[;&|])\s*rm\b",
    r"\brm\s+-[^\n]*r",
    r"\bsudo\b",
    r"\bshutdown\b|\breboot\b",
    r"\bgit\s+(push|reset\s+--hard|clean\s+-f)",
    r"\bmkfs\b|\bdd\s+if=",
    r"\bcurl\b[^\n]*\|\s*(sh|bash)",
)

_SHELL_CONTROL_TOKENS = ("&&", "||", ";", "|", ">", "<", "`", "$(", "\n", "\r")

_SAFE_PREFIXES = {
    "python", "python3", "pytest", "ruff", "mypy", "pyright", "go", "node",
    "npm", "yarn", "make", "git", "pwd", "ls", "find", "rg", "grep",
}


def classify_command(command: str) -> str:
    """Return allow, approval_required, or deny."""
    if not command.strip():
        return "deny"
    if any(token in command for token in _SHELL_CONTROL_TOKENS):
        return "deny"
    if any(re.search(pattern, command, flags=re.IGNORECASE) for pattern in _DENY_PATTERNS):
        return "deny"
    try:
        first = shlex.split(command)[0]
    except ValueError:
        return "deny"
    executable = Path(first).name
    return "allow" if executable in _SAFE_PREFIXES else "approval_required"


def explain_command(command: str) -> str:
    decision = classify_command(command)
    if decision == "deny":
        return "command rejected by safety policy"
    if decision == "approval_required":
        return "command requires explicit approval; use a test/build/static-check command"
    return "command allowed"
