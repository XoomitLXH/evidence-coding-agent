from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from coding_agent.pdf_tools import MAX_PDF_BYTES, read_pdf
from coding_agent.policy import PolicyError


class PdfToolsTests(unittest.TestCase):
    def test_rejects_outside_workspace_non_pdf_missing_and_invalid_ranges(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "notes.txt").write_text("text", encoding="utf-8")
            with self.assertRaisesRegex(PolicyError, "escapes"):
                read_pdf(root, "../outside.pdf")
            with self.assertRaisesRegex(PolicyError, "只接受"):
                read_pdf(root, "notes.txt")
            with self.assertRaisesRegex(PolicyError, "not a file"):
                read_pdf(root, "missing.pdf")
            (root / "ok.pdf").write_bytes(b"%PDF")
            with self.assertRaisesRegex(PolicyError, "页码范围"):
                read_pdf(root, "ok.pdf", page_start=0)

    def test_rejects_oversized_pdf(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "large.pdf"
            with path.open("wb") as handle:
                handle.truncate(MAX_PDF_BYTES + 1)
            with self.assertRaisesRegex(PolicyError, "20 MB"):
                read_pdf(root, "large.pdf")

    def test_reads_text_with_mocked_pypdf_backend(self) -> None:
        class Page:
            def __init__(self, text: str):
                self.text = text

            def extract_text(self) -> str:
                return self.text

        class Reader:
            def __init__(self, path: str):
                self.pages = [Page("first"), Page("second")]

        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.pdf").write_bytes(b"%PDF-1.7")
            fake = types.SimpleNamespace(PdfReader=Reader)
            with patch.dict(sys.modules, {"pypdf": fake}):
                result = read_pdf(root, "sample.pdf", page_start=1, page_end=2)

            self.assertEqual(result["backend"], "pypdf")
            self.assertEqual(result["pages_read"], 2)
            self.assertEqual(result["text"], "first\n\nsecond")

    def test_reports_missing_optional_dependencies(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "sample.pdf").write_bytes(b"%PDF-1.7")
            with patch.dict(sys.modules, {"pypdf": None, "pdfplumber": None}):
                with self.assertRaisesRegex(PolicyError, "可选依赖"):
                    read_pdf(root, "sample.pdf")


if __name__ == "__main__":
    unittest.main()
