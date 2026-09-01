from __future__ import annotations

from pathlib import Path

from .draft import DraftChanges
from .policy import PolicyError, safe_path
from .state import AgentState


def list_dir(root: Path, path: str = ".", *, drafts: DraftChanges | None = None) -> dict:
    directory = safe_path(root, path)
    if not directory.is_dir():
        raise PolicyError(f"not a directory: {path}")
    entries = []
    for item in sorted(directory.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))[:200]:
        relative = str(item.relative_to(root.resolve()))
        entry = {"name": item.name, "type": "dir" if item.is_dir() else "file"}
        if drafts and relative in drafts.paths():
            entry["draft"] = True
        entries.append(entry)
    if drafts:
        relative_dir = str(directory.relative_to(root.resolve()))
        prefix = "" if relative_dir == "." else relative_dir.rstrip("/") + "/"
        known = {entry["name"] for entry in entries}
        for draft_path in drafts.paths():
            if not draft_path.startswith(prefix):
                continue
            remainder = draft_path[len(prefix):]
            child = remainder.split("/", 1)[0]
            if child and child not in known:
                entries.append({"name": child, "type": "dir" if "/" in remainder else "file", "draft": True})
                known.add(child)
            elif child and remainder == child:
                for entry in entries:
                    if entry["name"] == child:
                        entry["draft"] = True
        entries.sort(key=lambda item: (item["type"] != "dir", item["name"].lower()))
    return {"path": path, "entries": entries}


def read_file(root: Path, path: str, start_line: int = 1, end_line: int = 240, *, drafts: DraftChanges | None = None) -> dict:
    file_path = safe_path(root, path)
    relative = str(file_path.relative_to(root.resolve()))
    has_draft = bool(drafts and relative in drafts.paths())
    if not file_path.is_file() and not has_draft:
        raise PolicyError(f"not a file: {path}")
    if start_line < 1 or end_line < start_line or end_line - start_line > 500:
        raise PolicyError("invalid line range")
    text = drafts.read_text(relative) if drafts else file_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    selected = lines[start_line - 1:end_line]
    numbered = "\n".join(f"{i}: {line}" for i, line in enumerate(selected, start_line))
    return {"path": relative, "content": numbered, "line_start": start_line, "line_end": min(end_line, len(lines)), "total_lines": len(lines), "draft": has_draft}


def write_file(root: Path, state: AgentState, path: str, content: str, *, drafts: DraftChanges | None = None) -> dict:
    file_path = safe_path(root, path)
    if len(content.encode("utf-8")) > 400_000:
        raise PolicyError("file content exceeds 400 KB limit")
    rel = str(file_path.relative_to(root.resolve()))
    if drafts:
        result = drafts.write_file(rel, content)
    else:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        result = {"ok": True, "path": rel, "bytes": len(content.encode("utf-8"))}
    state.mark_modified([rel])
    result["next"] = "run a verification command"
    return result


def apply_patch(root: Path, state: AgentState, path: str, old_text: str, new_text: str, *, drafts: DraftChanges | None = None) -> dict:
    file_path = safe_path(root, path)
    if not old_text:
        raise PolicyError("old_text must be non-empty")
    rel = str(file_path.relative_to(root.resolve()))
    original = drafts.read_text(rel) if drafts else file_path.read_text(encoding="utf-8")
    occurrences = original.count(old_text)
    if occurrences != 1:
        raise PolicyError(f"old_text must match exactly once (matched {occurrences})")
    if drafts:
        result = drafts.apply_patch(rel, old_text, new_text)
    else:
        file_path.write_text(original.replace(old_text, new_text, 1), encoding="utf-8")
        result = {"ok": True, "path": rel, "replacement_count": 1}
    state.mark_modified([rel])
    result["next"] = "run a verification command"
    return result
