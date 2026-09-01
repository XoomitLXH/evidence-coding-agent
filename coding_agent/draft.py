from __future__ import annotations

import difflib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .policy import PolicyError, safe_path


@dataclass
class _Draft:
    path: str
    before_exists: bool
    before: str
    content: str


class DraftChanges:
    """In-memory agent edits with optimistic-concurrency acceptance."""

    def __init__(self, root: Path, entries: dict[str, dict[str, Any]] | None = None):
        self.root = Path(root).resolve()
        self._entries: dict[str, _Draft] = {}
        if entries:
            for path, entry in entries.items():
                self._entries[path] = _Draft(
                    path=path,
                    before_exists=bool(entry.get("before_exists", True)),
                    before=str(entry.get("before", "")),
                    content=str(entry.get("content", "")),
                )

    def _path(self, path: str) -> Path:
        return safe_path(self.root, path)

    def _relative(self, path: str) -> str:
        return str(self._path(path).relative_to(self.root))

    def _entry(self, path: str) -> _Draft:
        rel = self._relative(path)
        entry = self._entries.get(rel)
        if entry is not None:
            return entry
        file_path = self._path(rel)
        if file_path.exists() and not file_path.is_file():
            raise PolicyError(f"not a file: {path}")
        exists = file_path.is_file()
        before = file_path.read_text(encoding="utf-8", errors="replace") if exists else ""
        entry = _Draft(rel, exists, before, before)
        self._entries[rel] = entry
        return entry

    def read_text(self, path: str) -> str:
        rel = self._relative(path)
        entry = self._entries.get(rel)
        if entry is not None:
            return entry.content
        file_path = self._path(rel)
        if not file_path.is_file():
            raise PolicyError(f"not a file: {path}")
        return file_path.read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> dict[str, Any]:
        if len(content.encode("utf-8")) > 400_000:
            raise PolicyError("file content exceeds 400 KB limit")
        entry = self._entry(path)
        entry.content = content
        return {"ok": True, "path": entry.path, "draft": True, "bytes": len(content.encode("utf-8"))}

    def apply_patch(self, path: str, old_text: str, new_text: str) -> dict[str, Any]:
        if not old_text:
            raise PolicyError("old_text must be non-empty")
        entry = self._entry(path)
        occurrences = entry.content.count(old_text)
        if occurrences != 1:
            raise PolicyError(f"old_text must match exactly once (matched {occurrences})")
        entry.content = entry.content.replace(old_text, new_text, 1)
        return {"ok": True, "path": entry.path, "draft": True, "replacement_count": 1}

    def paths(self) -> list[str]:
        return sorted(path for path, entry in self._entries.items() if entry.content != entry.before or not entry.before_exists)

    def diff(self, paths: list[str] | None = None) -> list[dict[str, Any]]:
        selected = paths if paths is not None else self.paths()
        result = []
        for path in selected:
            entry = self._entries.get(self._relative(path))
            if entry is None:
                continue
            before = entry.before if entry.before_exists else ""
            patch = "".join(difflib.unified_diff(
                before.splitlines(keepends=True),
                entry.content.splitlines(keepends=True),
                fromfile=f"a/{entry.path}",
                tofile=f"b/{entry.path}",
            ))
            result.append({"path": entry.path, "before": before, "after": entry.content, "exists_before": entry.before_exists, "diff": patch})
        return result

    def accept(self, paths: list[str] | None = None) -> dict[str, list[str]]:
        selected = paths if paths is not None else self.paths()
        accepted: list[str] = []
        conflicts: list[str] = []
        for path in selected:
            entry = self._entries.get(self._relative(path))
            if entry is None:
                continue
            file_path = self._path(entry.path)
            exists = file_path.is_file()
            current = file_path.read_text(encoding="utf-8", errors="replace") if exists else ""
            if exists != entry.before_exists or current != entry.before:
                conflicts.append(entry.path)
                continue
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(entry.content, encoding="utf-8")
            accepted.append(entry.path)
            del self._entries[entry.path]
        return {"accepted": accepted, "conflicts": conflicts}

    def reject(self, paths: list[str] | None = None) -> dict[str, list[str]]:
        selected = paths if paths is not None else self.paths()
        rejected = []
        for path in selected:
            rel = self._relative(path)
            if rel in self._entries:
                rejected.append(rel)
                del self._entries[rel]
        return {"rejected": sorted(rejected)}

    def to_dict(self) -> dict[str, dict[str, Any]]:
        return {path: {"before_exists": e.before_exists, "before": e.before, "content": e.content} for path, e in self._entries.items()}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, root: Path, payload: dict[str, dict[str, Any]]) -> "DraftChanges":
        return cls(root, payload)

    @classmethod
    def from_json(cls, root: Path, payload: str) -> "DraftChanges":
        return cls.from_dict(root, json.loads(payload))
