from __future__ import annotations

import re
from pathlib import Path

from .policy import PolicyError, safe_path
from .workspace import SKIP_DIRS


def search_code(root: Path, query: str, path: str = ".", max_results: int = 80) -> dict:
    if not query or len(query) > 200:
        raise PolicyError("query must be 1-200 characters")
    base = safe_path(root, path)
    try:
        pattern = re.compile(query, re.IGNORECASE)
    except re.error:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
    results = []
    candidates = [base] if base.is_file() else base.rglob("*")
    for file_path in candidates:
        if len(results) >= max_results:
            break
        if not file_path.is_file() or any(part in SKIP_DIRS for part in file_path.relative_to(root).parts):
            continue
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeDecodeError):
            continue
        for line_no, line in enumerate(lines, 1):
            if pattern.search(line):
                results.append({"path": str(file_path.relative_to(root)), "line": line_no, "text": line[:300]})
                if len(results) >= max_results:
                    break
    return {"query": query, "matches": results, "truncated": len(results) >= max_results}
