from __future__ import annotations

from pathlib import Path
from typing import Any

from .policy import PolicyError, safe_path


MAX_PDF_BYTES = 20 * 1024 * 1024
MAX_PDF_PAGES = 20
MAX_PDF_TEXT = 160_000


def read_pdf(root: Path, path: str, page_start: int = 1, page_end: int = 20) -> dict[str, Any]:
    pdf_path = safe_path(root, path)
    if pdf_path.suffix.lower() != ".pdf":
        raise PolicyError("read_pdf 只接受 .pdf 文件")
    if not pdf_path.is_file():
        raise PolicyError(f"not a file: {path}")
    size = pdf_path.stat().st_size
    if size > MAX_PDF_BYTES:
        raise PolicyError("PDF 文件超过 20 MB 限制")
    if not isinstance(page_start, int) or not isinstance(page_end, int):
        raise PolicyError("page_start 和 page_end 必须是整数")
    if page_start < 1 or page_end < page_start or page_end - page_start + 1 > MAX_PDF_PAGES:
        raise PolicyError("页码范围必须从 1 开始且不超过 20 页")
    text = ""
    actual_end = page_end
    backend = ""
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(str(pdf_path))
        actual_end = min(page_end, len(reader.pages))
        if actual_end < page_start:
            raise PolicyError("page_start 超出 PDF 页数")
        text = "\n\n".join((reader.pages[index - 1].extract_text() or "") for index in range(page_start, actual_end + 1))
        backend = "pypdf"
    except ImportError:
        try:
            import pdfplumber  # type: ignore

            with pdfplumber.open(str(pdf_path)) as pdf:
                actual_end = min(page_end, len(pdf.pages))
                if actual_end < page_start:
                    raise PolicyError("page_start 超出 PDF 页数")
                text = "\n\n".join((pdf.pages[index - 1].extract_text() or "") for index in range(page_start, actual_end + 1))
            backend = "pdfplumber"
        except ImportError as exc:
            raise PolicyError("读取 PDF 需要安装可选依赖 pypdf 或 pdfplumber") from exc
    except PolicyError:
        raise
    except Exception as exc:
        raise PolicyError(f"PDF 读取失败: {exc}") from exc
    truncated = len(text) > MAX_PDF_TEXT
    if truncated:
        text = text[:MAX_PDF_TEXT]
    return {
        "path": str(pdf_path.relative_to(root.resolve())),
        "page_start": page_start,
        "page_end": actual_end,
        "pages_read": max(0, actual_end - page_start + 1),
        "backend": backend,
        "text": text,
        "truncated": truncated,
    }
