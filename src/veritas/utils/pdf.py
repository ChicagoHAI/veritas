"""PDF utilities for Veritas."""

from pathlib import Path
from typing import List, Dict, Optional


def read_pdf(path: Path) -> str:
    """
    Read text content from a PDF file.

    Args:
        path: Path to the PDF file

    Returns:
        Extracted text content
    """
    try:
        import pdfplumber

        text_parts = []
        with pdfplumber.open(path) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    text_parts.append(text)

        return "\n\n".join(text_parts)

    except ImportError:
        # Fallback to pypdf
        from pypdf import PdfReader

        reader = PdfReader(path)
        text_parts = []

        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_parts.append(text)

        return "\n\n".join(text_parts)


def read_pdf_pages(path: Path) -> List[Dict]:
    """
    Read PDF with page-level information.

    Args:
        path: Path to the PDF file

    Returns:
        List of dicts with page number and text
    """
    try:
        import pdfplumber

        pages = []
        with pdfplumber.open(path) as pdf:
            for i, page in enumerate(pdf.pages):
                pages.append({
                    "number": i + 1,
                    "text": page.extract_text() or ""
                })

        return pages

    except ImportError:
        from pypdf import PdfReader

        reader = PdfReader(path)
        pages = []

        for i, page in enumerate(reader.pages):
            pages.append({
                "number": i + 1,
                "text": page.extract_text() or ""
            })

        return pages


def find_text_in_pdf(path: Path, search_text: str) -> Optional[int]:
    """
    Find which page contains a text snippet.

    Args:
        path: Path to the PDF file
        search_text: Text to search for

    Returns:
        Page number (1-indexed) or None if not found
    """
    pages = read_pdf_pages(path)
    search_lower = search_text.lower()

    for page in pages:
        if search_lower in page["text"].lower():
            return page["number"]

    return None
