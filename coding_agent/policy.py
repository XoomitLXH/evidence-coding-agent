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


def command_decision(command: str) -> dict[str, str]:
    """Return a display-safe command risk decision for tools and the UI."""
    decision = classify_command(command)
    if decision == "allow":
        return {
            "decision": decision,
            "reason": "允许执行：该命令在本地安全命令白名单内。",
            "recommendation": "执行后请查看退出码和验证输出。",
        }
    if decision == "approval_required":
        return {
            "decision": decision,
            "reason": "该命令不在安全白名单内。",
            "recommendation": "需要确认后才能执行；可先改用测试、构建或静态检查命令。",
        }
    return {
        "decision": decision,
        "reason": "命令包含高风险操作或不安全的 Shell 控制符。",
        "recommendation": "请改为受限的单一安全命令，避免删除、提权、远程执行或命令拼接。",
    }


def explain_command(command: str) -> str:
    return command_decision(command)["reason"]
