from __future__ import annotations

import hashlib
from pathlib import Path


SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", "node_modules"}


def iter_files(root: Path):
    for path in root.rglob("*"):
        if not path.is_file() or any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        yield path


def snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in iter_files(root):
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        result[str(path.relative_to(root))] = digest
    return result


def changed_files(root: Path, before: dict[str, str]) -> list[str]:
    after = snapshot(root)
    return sorted({*before, *after} - {key for key in before.keys() & after.keys() if before[key] == after[key]})
