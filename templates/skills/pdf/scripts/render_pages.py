"""Render pages of a PDF to PNG images, one file per page.

Useful for visually inspecting figures, tables, equations, and layout, or
for preparing page images for OCR. Output files are named page_001.png,
page_002.png, ... (numbering is 1-based and matches the PDF page order).

Requires PyMuPDF: pip install pymupdf

Usage:
    python scripts/render_pages.py <paper.pdf> <output_dir> [--dpi N] [--pages SPEC]

    --dpi N       Render resolution in dots per inch (default: 150).
                  Use 300 for fine detail such as small axis labels.
    --pages SPEC  1-based page number or inclusive range, e.g. "3" or
                  "2-5". Default: all pages.

Examples:
    python scripts/render_pages.py paper.pdf pages/
    python scripts/render_pages.py paper.pdf pages/ --dpi 300 --pages 7
    python scripts/render_pages.py paper.pdf pages/ --dpi 200 --pages 3-6
"""

import argparse
import sys
from pathlib import Path

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF is required: pip install pymupdf")


def parse_page_spec(spec, page_count):
    """Parse a 1-based page spec like '3' or '2-5' into 0-based indices."""
    if spec is None:
        return list(range(page_count))
    try:
        if "-" in spec:
            first, last = spec.split("-", 1)
            start, end = int(first), int(last)
        else:
            start = end = int(spec)
    except ValueError:
        raise ValueError(f"Invalid page spec {spec!r}; expected 'N' or 'N-M'")
    if start < 1 or end > page_count or start > end:
        raise ValueError(
            f"Page spec {spec!r} is out of bounds for a "
            f"{page_count}-page document"
        )
    return list(range(start - 1, end))


def main():
    parser = argparse.ArgumentParser(
        description="Render pages of a PDF to PNG images."
    )
    parser.add_argument("pdf", help="path to the PDF file")
    parser.add_argument(
        "output_dir", help="directory for PNG files (created if missing)"
    )
    parser.add_argument(
        "--dpi", type=int, default=150,
        help="render resolution in dots per inch (default: 150)",
    )
    parser.add_argument(
        "--pages", default=None,
        help="1-based page or inclusive range, e.g. '3' or '2-5' "
             "(default: all pages)",
    )
    args = parser.parse_args()

    pdf_path = Path(args.pdf)
    if not pdf_path.is_file():
        sys.exit(f"No such file: {pdf_path}")

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    try:
        try:
            indices = parse_page_spec(args.pages, doc.page_count)
        except ValueError as exc:
            sys.exit(str(exc))

        pad = len(str(doc.page_count))
        for index in indices:
            pix = doc[index].get_pixmap(dpi=args.dpi)
            out_path = out_dir / f"page_{index + 1:0{pad}d}.png"
            pix.save(str(out_path))
            print(f"wrote {out_path} ({pix.width}x{pix.height})")
        print(f"Rendered {len(indices)} page(s) at {args.dpi} DPI")
    finally:
        doc.close()


if __name__ == "__main__":
    main()
