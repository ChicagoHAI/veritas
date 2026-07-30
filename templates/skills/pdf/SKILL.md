---
name: pdf
description: Programmatic, structured extraction from scientific-paper PDFs. Extract result tables as row/column data, pull embedded figures out to image files, render pages or regions to PNG, read document metadata and DOIs, locate and extract the references section, handle two-column layouts, find which page a term appears on, and OCR scanned papers. Use this skill when you need machine-readable structures, images, or precise page locations from a PDF. For simply reading a paper's prose, open the PDF directly (the agent reads PDF text natively). To convert an entire document to Markdown in one shot, use the markitdown skill instead.
allowed-tools: Read Write Edit Bash
---

# PDF - Structured Extraction from Scientific Papers

## Overview

Scientific papers arrive as PDFs, and reproducing a paper means getting specific
things out of that PDF in machine-checkable form: the numbers in a results
table, the image of a figure to compare against a regenerated plot, the DOI and
reference list for citation checking, or the text of a scanned appendix.

This skill covers targeted, programmatic extraction with Python libraries. It
is deliberately scoped to reading papers. It does not cover creating PDFs,
filling forms, merging or splitting documents, watermarks, or encryption; none
of those operations occur in a replication workflow.

## When to Use This Skill

Choose the lightest tool that answers the question:

| Goal | Approach |
|------|----------|
| Read or quote the paper's prose | Open the PDF directly; no code needed. The agent reads PDF text natively. |
| Convert the whole document to Markdown | Use the `markitdown` skill. |
| Get a table's cells as rows and columns | This skill (pdfplumber). |
| Save a figure as an image file, or render a page/region to PNG | This skill (PyMuPDF). |
| Title, authors, DOI, arXiv ID, references list | This skill (pypdf or PyMuPDF). |
| Find which page mentions "Table 3" or "learning rate" | This skill (PyMuPDF search). |
| Two-column text in correct reading order | This skill (see references/extraction.md). |
| A scan with no text layer | This skill (OCR section). |

## Installation

No PDF library is preinstalled in this environment. Install before importing:

```bash
# Core stack: metadata/pages, tables, images/rendering/search
pip install pypdf pdfplumber pymupdf

# OCR stack, only needed for scanned PDFs
pip install pdf2image pytesseract
apt-get install -y poppler-utils tesseract-ocr   # prefix with sudo if not root
```

Notes:

- PyMuPDF is imported as `fitz` (the PyPI package name is `pymupdf`).
- `pdf2image` needs the system package `poppler-utils`; `pytesseract` needs
  `tesseract-ocr`. If installing system packages is not possible, PyMuPDF can
  render the page images for OCR instead of pdf2image (see
  references/extraction.md), but Tesseract itself is always required for OCR.

## Library Roles

| Library | Import | Use for |
|---------|--------|---------|
| pypdf | `pypdf` | Metadata, page counts, quick per-page text |
| pdfplumber | `pdfplumber` | Tables as cell grids, layout-aware text, cropping |
| PyMuPDF | `fitz` | Fast text/blocks, text search, image extraction, page rendering |
| pdf2image + pytesseract | `pdf2image`, `pytesseract` | OCR of scanned pages |

## Quick Recipes

### Metadata and identifiers

```python
from pypdf import PdfReader

reader = PdfReader("paper.pdf")
print(len(reader.pages), "pages")
meta = reader.metadata
if meta:
    print("Title:", meta.title)
    print("Author:", meta.author)
```

PDF metadata is often empty or stale for papers. Pull identifiers from the
first page text instead:

```python
import re
import fitz  # PyMuPDF

doc = fitz.open("paper.pdf")
first_page = doc[0].get_text()

doi = re.search(r"10\.\d{4,9}/[^\s\"<>]+", first_page)
arxiv = re.search(r"arXiv:\d{4}\.\d{4,5}(v\d+)?", first_page)
print("DOI:", doi.group(0) if doi else None)
print("arXiv:", arxiv.group(0) if arxiv else None)
```

### Per-page and page-range text

For a long paper, extract only the pages you need:

```python
import fitz

doc = fitz.open("paper.pdf")
# Pages 5-8 (1-based) -> indices 4..7
text = "\n".join(doc[i].get_text() for i in range(4, 8))
print(text[:2000])
```

Caution: on two-column papers, plain text extraction can interleave the
columns. See "Multi-column text" below before trusting paragraph order.

### Find where something appears

```python
import fitz

doc = fitz.open("paper.pdf")
term = "Table 3"
for i in range(doc.page_count):
    hits = doc[i].search_for(term)
    if hits:
        print(f"'{term}' appears {len(hits)}x on page {i + 1}")
```

`search_for` returns the bounding rectangles of each hit, which is also how
you locate a region to render (see below).

### Extract a table as structured data

```python
import pdfplumber

with pdfplumber.open("paper.pdf") as pdf:
    page = pdf.pages[4]          # 0-based: page 5
    tables = page.extract_tables()
    for t_index, table in enumerate(tables):
        print(f"-- table {t_index}: {len(table)} rows --")
        for row in table:
            print(row)           # list of cell strings (None for empty cells)
```

Save one table to CSV:

```python
import csv
import pdfplumber

with pdfplumber.open("paper.pdf") as pdf:
    table = pdf.pages[4].extract_tables()[0]

with open("table1.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    for row in table:
        writer.writerow([(cell or "").replace("\n", " ") for cell in row])
```

Tables without ruled cell borders (common in ML papers) need the `"text"`
detection strategy:

```python
tables = page.extract_tables({
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
})
```

For cropping to a single table, tuning detection, and cleaning cells, see
references/extraction.md.

### Multi-column text

Most papers are two-column. The quickest correct approach is to crop each
column and extract separately:

```python
import pdfplumber

with pdfplumber.open("paper.pdf") as pdf:
    page = pdf.pages[2]
    mid = page.width / 2
    left = page.crop((0, 0, mid, page.height)).extract_text() or ""
    right = page.crop((mid, 0, page.width, page.height)).extract_text() or ""
    print(left + "\n" + right)
```

This misorders full-width elements (title block, wide tables). A block-based
approach that handles those is in references/extraction.md.

### Extract embedded figures

```python
import fitz

doc = fitz.open("paper.pdf")
for page_index in range(doc.page_count):
    for img_index, img in enumerate(doc[page_index].get_images(full=True)):
        xref = img[0]
        info = doc.extract_image(xref)
        name = f"page{page_index + 1}_img{img_index}.{info['ext']}"
        with open(name, "wb") as f:
            f.write(info["image"])
        print("wrote", name)
```

Important: this only finds raster images. Plots exported from matplotlib or
similar tools are usually vector drawings with no embedded image to extract.
For those, render the figure's region of the page instead (next recipe).

### Render a page or region to an image

```python
import fitz

doc = fitz.open("paper.pdf")

# Whole page at 200 DPI
pix = doc[2].get_pixmap(dpi=200)
pix.save("page3.png")

# Just the area around a figure caption
page = doc[2]
caption_hits = page.search_for("Figure 2")
if caption_hits:
    cap = caption_hits[0]
    # Figures usually sit above their caption; take the area above it.
    top = max(cap.y0 - 320, 0)
    bottom = min(cap.y1 + 8, page.rect.height)
    clip = fitz.Rect(0, top, page.rect.width, bottom)
    page.get_pixmap(clip=clip, dpi=300).save("figure2.png")
```

Rendered PNGs can then be inspected visually (the agent reads image files) or
compared against replication outputs. The helper
`scripts/render_pages.py` renders every page (or a range) in one command.

### Detect a scanned PDF and OCR it

A scanned paper has (almost) no extractable text:

```python
import fitz

doc = fitz.open("paper.pdf")
chars_per_page = sum(len(p.get_text()) for p in doc) / max(doc.page_count, 1)
print("likely scanned" if chars_per_page < 50 else "has a text layer")
```

OCR the whole document (needs the OCR stack from Installation):

```python
from pdf2image import convert_from_path
import pytesseract

images = convert_from_path("scanned.pdf", dpi=300)
full_text = []
for i, image in enumerate(images, start=1):
    text = pytesseract.image_to_string(image)
    full_text.append(f"--- page {i} ---\n{text}")
with open("ocr_output.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(full_text))
```

Papers are sometimes mixed (a born-digital body plus scanned appendix pages);
OCR only the pages that fall below the character threshold. A full
walkthrough, including a poppler-free variant, is in
references/extraction.md.

### Extract the references section

```python
import re
import fitz

doc = fitz.open("paper.pdf")

# Prefer the document outline if the paper has one
start_page = None
for level, title, page_no in doc.get_toc():
    if re.fullmatch(r"references|bibliography", title.strip(), re.I):
        start_page = page_no - 1
        break

# Otherwise scan backwards for a standalone heading line
if start_page is None:
    heading = re.compile(r"^\s*(references|bibliography)\s*$", re.I | re.M)
    for i in range(doc.page_count - 1, -1, -1):
        if heading.search(doc[i].get_text()):
            start_page = i
            break

if start_page is not None:
    ref_text = "\n".join(
        doc[i].get_text() for i in range(start_page, doc.page_count)
    )
    # For [1] [2] ... style bibliographies, split into entries.
    # The first chunk holds the heading and any preamble; drop non-entries.
    chunks = re.split(r"\n(?=\[\d+\]\s)", ref_text)
    entries = [c for c in chunks if re.match(r"\[\d+\]", c.lstrip())]
    print(f"references start on page {start_page + 1}; {len(entries)} entries")
```

Entry splitting is heuristic; author-year styles need different patterns. See
references/extraction.md for details.

## Limits: Math and Equations

Text extraction of display equations is unreliable and there is no
library-level fix. Math fonts often map glyphs to wrong or private-use
characters, superscripts and subscripts lose their positions, and built-up
fractions or matrices collapse into one line. Do not trust extracted equation
text for verification. When an equation matters, render its region to a PNG
(see the rendering recipe) and read it visually, or transcribe it from the
paper source if one is available.

## Resources

- `references/extraction.md` - table extraction tuning, multi-column
  handling, figure/region rendering, the full OCR walkthrough, and
  troubleshooting for garbled text.
- `scripts/render_pages.py` - render each page (or a page range) of a PDF to
  PNG files: `python scripts/render_pages.py paper.pdf pages/ --dpi 200`.
- The `markitdown` skill - whole-document conversion to Markdown when you
  want the full paper as one text file rather than targeted structures.
