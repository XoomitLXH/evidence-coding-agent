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


_DELETION_PATTERNS = (
    r"\bfind\b[^\n]*\s-delete\b",
    r"\bgit\s+clean\b",
    r"\b(?:os|shutil)\.(?:remove|unlink|rmdir|rmtree)\s*\(",
    r"\bPath\s*\([^)]*\)\.(?:unlink|rmdir)\s*\(",
)


def _contains_deletion(command: str) -> bool:
    """Detect deletion invocations without matching harmless prose or string values."""
    try:
        tokens = shlex.split(command)
    except ValueError:
        tokens = []
    # Resolve common command wrappers so wrapped deletion commands are covered.
    index = 0
    while index < len(tokens):
        executable = Path(tokens[index]).name.lower()
        if executable in {"rm", "rmdir", "unlink"}:
            return True
        if executable in {"command", "exec"}:
            index += 1
            continue
        if executable in {"sudo", "doas"}:
            index += 1
            while index < len(tokens) and tokens[index].startswith("-"):
                option = tokens[index]
                index += 1
                if option in {"-u", "--user", "-g", "--group", "-C", "--chdir"} and index < len(tokens):
                    index += 1
            continue
        if executable == "env":
            index += 1
            while index < len(tokens) and ("=" in tokens[index] or tokens[index].startswith("-")):
                index += 1
            continue
        break
    return any(re.search(pattern, command, flags=re.IGNORECASE) for pattern in _DELETION_PATTERNS)


def classify_command(command: str) -> str:
    """Allow commands by default, requiring confirmation only for deletion operations."""
    if not command.strip():
        return "deny"
    if _contains_deletion(command):
        return "approval_required"
    return "allow"


def command_decision(command: str) -> dict[str, str]:
    """Return a display-safe command risk decision for tools and the UI."""
    decision = classify_command(command)
    if decision == "allow":
        return {
            "decision": decision,
            "reason": "允许执行：默认放行非删除的本地命令。",
            "recommendation": "执行后请查看退出码和验证输出。",
        }
    if decision == "approval_required":
        return {
            "decision": decision,
            "reason": "该命令包含删除文件或目录的操作。",
            "recommendation": "请确认删除目标后点击“允许执行”；拒绝则不会修改工作区。",
        }
    return {
        "decision": decision,
        "reason": "命令无法执行：内容为空或不符合安全策略。",
        "recommendation": "请提供有效命令；删除操作会进入审批流程。",
    }


def explain_command(command: str) -> str:
    return command_decision(command)["reason"]
