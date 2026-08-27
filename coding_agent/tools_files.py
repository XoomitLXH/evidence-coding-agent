from __future__ import annotations

from pathlib import Path

from .policy import PolicyError, safe_path
from .state import AgentState


def list_dir(root: Path, path: str = ".") -> dict:
    directory = safe_path(root, path)
    if not directory.is_dir():
        raise PolicyError(f"not a directory: {path}")
    entries = []
    for item in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]:
        entries.append({"name": item.name, "type": "dir" if item.is_dir() else "file"})
    return {"path": path, "entries": entries}


def read_file(root: Path, path: str, start_line: int = 1, end_line: int = 240) -> dict:
    file_path = safe_path(root, path)
    if not file_path.is_file():
        raise PolicyError(f"not a file: {path}")
    if start_line < 1 or end_line < start_line or end_line - start_line > 500:
        raise PolicyError("invalid line range")
    text = file_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    selected = lines[start_line - 1:end_line]
    numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(selected, start_line))
    return {"path": path, "content": numbered, "line_start": start_line, "line_end": min(end_line, len(lines)), "total_lines": len(lines)}


def write_file(root: Path, state: AgentState, path: str, content: str) -> dict:
    file_path = safe_path(root, path)
    if len(content.encode("utf-8")) > 400_000:
        raise PolicyError("file content exceeds 400 KB limit")
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    rel = str(file_path.relative_to(root))
    state.mark_modified([rel])
    return {"ok": True, "path": rel, "bytes": len(content.encode("utf-8")), "next": "run a verification command"}


def apply_patch(root: Path, state: AgentState, path: str, old_text: str, new_text: str) -> dict:
    file_path = safe_path(root, path)
    if not old_text:
        raise PolicyError("old_text must be non-empty")
    original = file_path.read_text(encoding="utf-8")
    occurrences = original.count(old_text)
    if occurrences != 1:
        raise PolicyError(f"old_text must match exactly once (matched {occurrences})")
    file_path.write_text(original.replace(old_text, new_text, 1), encoding="utf-8")
    rel = str(file_path.relative_to(root))
    state.mark_modified([rel])
    return {"ok": True, "path": rel, "replacement_count": 1, "next": "run a verification command"}
