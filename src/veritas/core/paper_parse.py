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

import re
from pathlib import Path
from typing import Optional, Tuple

# pymupdf4llm emits placeholder markers for images/vector graphics it omits, e.g.
# "==> picture [255 x 238] intentionally omitted <==", plus image links and HR
# noise. These are ugly in the rendered viewer, so strip them from the markdown.
_ARTIFACT_PATTERNS = [
    re.compile(r"^.*?==>.*?<==.*$", re.MULTILINE),      # ==> ... intentionally omitted <==
    re.compile(r"!\[[^\]]*\]\([^)]*\)"),                # ![alt](data:...) image links
    re.compile(r"^\s*-{3,}\s*$", re.MULTILINE),         # bare horizontal rules
]


def _clean_markdown(md: str) -> str:
    """Strip pymupdf4llm artifacts and collapse the blank lines they leave."""
    for pat in _ARTIFACT_PATTERNS:
        md = pat.sub("", md)
    md = re.sub(r"\n{3,}", "\n\n", md)  # collapse runs of blank lines
    return md.strip()


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
        md = _clean_markdown(md or "")
        if md:
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
