"""Paper -> markdown text, via the vendored OpenAIReview engine.

Born-digital papers (the common case) parse cleanly with pymupdf4llm's
layout-aware markdown and need no OCR — this yields fine-grained, heading-aware
paragraphs (≈170 for a Nature article vs ≈20 from a flat text dump), which is
what the in-line-comment anchoring needs. Scanned PDFs can opt into the vendored
parser's full OCR chain (Mistral / DeepSeek / Marker) via ``ocr=True``.

Returns ``(title, markdown_text)``. Degrades gracefully: if the layout path is
unavailable it falls back to the vendored ``parse_document``, then to plain
PyMuPDF text, then to an empty string (callers then render unanchored).
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional, Tuple


def _title_from_markdown(md: str) -> Optional[str]:
    """First markdown heading (stripped of emphasis markers), if any."""
    for line in md.splitlines():
        s = line.strip()
        if s.startswith("#"):
            return s.lstrip("#").strip().strip("*_ ").strip() or None
    return None


def parse_paper_markdown(
    pdf_path: Path, ocr: bool = False
) -> Tuple[Optional[str], str]:
    """Return ``(title, markdown_text)`` for a paper PDF.

    ``ocr=False`` (default): layout-aware markdown with OCR disabled — correct
    and fast for born-digital papers, no Tesseract/model needed.
    ``ocr=True``: delegate to the vendored engine's full auto-OCR chain (for
    scanned PDFs); requires the optional OCR deps / keys to be useful.
    """
    pdf_path = Path(pdf_path)

    if ocr:
        try:
            from veritas.review_engine.parsers import parse_document
            title, text, _ = parse_document(str(pdf_path), ocr="auto")
            if text and text.strip():
                return title, text
        except Exception as e:  # fall through to the no-OCR path
            print(f"  Note: OCR parse failed ({e}); falling back to no-OCR parse.")

    # Primary: pymupdf4llm layout markdown, OCR explicitly disabled.
    try:
        import pymupdf4llm
        md = pymupdf4llm.to_markdown(str(pdf_path), use_ocr=False)
        if md and md.strip():
            return _title_from_markdown(md), md
    except Exception as e:
        print(f"  Note: pymupdf4llm parse failed ({e}); falling back to plain text.")

    # Fallback: plain PyMuPDF page text.
    try:
        import pymupdf  # provided by the pymupdf dependency
        parts = []
        with pymupdf.open(str(pdf_path)) as doc:
            for page in doc:
                parts.append(page.get_text())
        text = "\n\n".join(parts)
        if text.strip():
            return _title_from_markdown(text), text
    except Exception as e:
        print(f"  Note: PyMuPDF text fallback failed ({e}).")

    return None, ""
