# Extraction Reference

Deeper guidance for the operations introduced in SKILL.md: robust table
extraction, multi-column reading order, figure and region rendering, OCR for
scanned papers, and troubleshooting garbled output.

Install commands for every library used here are in SKILL.md under
Installation.

## Table Extraction in Depth

### How pdfplumber finds tables

pdfplumber builds a grid from detected vertical and horizontal separators,
then assigns characters to cells. The two main strategies:

- `"lines"` (default): separators come from ruling lines drawn in the PDF.
  Works well for fully ruled tables (common in biology and medicine).
- `"text"`: separators are inferred from the alignment of the text itself.
  Needed for booktabs-style tables (common in ML and physics papers) that
  have only a few horizontal rules and no vertical lines.

```python
import pdfplumber

with pdfplumber.open("paper.pdf") as pdf:
    page = pdf.pages[4]

    # Fully ruled table: defaults usually work
    ruled = page.extract_tables()

    # Booktabs-style table: infer the grid from text alignment
    unruled = page.extract_tables({
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
    })
```

Useful additional settings:

```python
settings = {
    "vertical_strategy": "text",
    "horizontal_strategy": "text",
    # Merge characters closer than this into one alignment group.
    # Raise it if columns split apart; lower it if columns merge.
    "text_x_tolerance": 2,
    "text_y_tolerance": 2,
    # With strategy "lines", ignore rules shorter than this many points.
    "min_words_vertical": 3,
    "min_words_horizontal": 1,
}
tables = page.extract_tables(settings)
```

### Isolating one table

`extract_tables` on a full page often picks up author blocks or column text
as false-positive "tables". Locate the real table first, then crop:

```python
import pdfplumber

with pdfplumber.open("paper.pdf") as pdf:
    page = pdf.pages[4]
    found = page.find_tables()          # list of Table objects
    for t in found:
        print(t.bbox)                   # (x0, top, x1, bottom)

    # Extract just the first detected table, via its bounding box
    if found:
        region = page.crop(found[0].bbox)
        table = region.extract_table()
        for row in table:
            print(row)
```

If detection misses the table entirely, find the caption instead and crop a
window below it (captions sit above tables in most styles):

```python
import pdfplumber

with pdfplumber.open("paper.pdf") as pdf:
    page = pdf.pages[4]
    words = page.extract_words()
    anchor = next((w for w in words if w["text"] == "Table" ), None)
    if anchor:
        top = anchor["bottom"]
        region = page.crop((0, top, page.width, min(top + 300, page.height)))
        table = region.extract_table({
            "vertical_strategy": "text",
            "horizontal_strategy": "text",
        })
```

### Visual debugging

When detection produces nonsense, look at what the detector saw:

```python
import pdfplumber

with pdfplumber.open("paper.pdf") as pdf:
    page = pdf.pages[4]
    im = page.to_image(resolution=150)
    im.debug_tablefinder({
        "vertical_strategy": "text",
        "horizontal_strategy": "text",
    })
    im.save("table_debug.png")
```

Open `table_debug.png`: detected rows, columns, and cells are drawn over the
page. Adjust tolerances until the overlay matches the visual table.

A second sanity check is layout-preserving text, which keeps horizontal
alignment with spaces so you can eyeball column boundaries:

```python
print(page.extract_text(layout=True))
```

### Cleaning extracted cells

Raw cells contain `None` for empty cells, embedded newlines from wrapped
headers, and formatting characters around numbers. Normalize before
comparing values:

```python
import re

def clean_cell(cell):
    if cell is None:
        return ""
    return re.sub(r"\s+", " ", cell).strip()

def parse_number(cell):
    """Return float for cells like '92.4', '1,204', '85.1 +- 0.3', else None."""
    text = clean_cell(cell).replace(",", "")
    m = re.match(r"^[~<>]?\s*(-?\d+(?:\.\d+)?)", text)
    return float(m.group(1)) if m else None

table = [[clean_cell(c) for c in row] for row in table]
# The "text" strategy often emits all-empty spacer rows; drop them.
table = [row for row in table if any(row)]
```

Common table hazards in papers:

- Multi-row headers: the first two or three rows may together form the
  header. Inspect before assuming row 0 is the header.
- Bold-face best results: bolding is invisible in extracted text; the cell
  string is just the number.
- Footnote markers: cells like `92.4*` or `92.4a`; strip trailing
  non-numeric characters before parsing.
- Plus-minus values: `85.1 +- 0.3` arrives with a Unicode plus-minus sign;
  keep the mean, and the deviation separately if needed.
- Tables split across pages or columns: extract both parts and concatenate
  rows manually.

### Handing off to pandas

Optional, if pandas is installed (`pip install pandas`):

```python
import pandas as pd

header, *rows = table
df = pd.DataFrame(rows, columns=header)
df.to_csv("table1.csv", index=False)
```

## Multi-Column Text

### Why naive extraction interleaves

Text extractors emit text roughly in top-to-bottom order across the whole
page width. In a two-column paper this alternates between a left-column line
and the right-column line beside it, producing shuffled sentences.

### Crop-based approach (pdfplumber)

Simple and usually sufficient for body pages:

```python
import pdfplumber

def two_column_text(page):
    mid = page.width / 2
    left = page.crop((0, 0, mid, page.height)).extract_text() or ""
    right = page.crop((mid, 0, page.width, page.height)).extract_text() or ""
    return left + "\n" + right

with pdfplumber.open("paper.pdf") as pdf:
    print(two_column_text(pdf.pages[2]))
```

Limitation: anything spanning both columns (the title block on page 1, wide
tables and figures) is sliced in half, each half attached to its column.

### Block-based approach (PyMuPDF)

`get_text("blocks")` returns paragraph-level blocks with coordinates, which
lets full-width blocks stay intact:

```python
import fitz

def column_ordered_text(page):
    mid = page.rect.width / 2
    blocks = [b for b in page.get_text("blocks") if b[6] == 0]  # text only
    full, left, right = [], [], []
    for b in blocks:
        x0, y0, x1, y1, text = b[0], b[1], b[2], b[3], b[4]
        if x0 < mid < x1:            # spans the midline: full-width block
            full.append((y0, text))
        elif x0 < mid:
            left.append((y0, text))
        else:
            right.append((y0, text))
    ordered = (
        [t for _, t in sorted(full)]
        + [t for _, t in sorted(left)]
        + [t for _, t in sorted(right)]
    )
    return "\n".join(t.strip() for t in ordered)

doc = fitz.open("paper.pdf")
print(column_ordered_text(doc[0]))
```

This puts full-width blocks (title, abstract banner) first, then the left
column, then the right column. For most verification tasks that ordering is
good enough; if precise interleaving of a full-width figure between column
segments matters, render the page instead and read it visually.

Three-column layouts (some proceedings): replace the midline test with
thirds, or cluster block x-centers to detect the column count:

```python
centers = sorted((b[0] + b[2]) / 2 for b in blocks)
```

A large gap in `centers` marks a column boundary.

## Figures and Page Rendering

### Extracting embedded raster images

```python
import fitz

doc = fitz.open("paper.pdf")
count = 0
for page_index in range(doc.page_count):
    for img in doc[page_index].get_images(full=True):
        xref = img[0]
        info = doc.extract_image(xref)
        # Skip tiny images: logos, icons, decorative rules
        if info["width"] < 100 or info["height"] < 100:
            continue
        count += 1
        name = f"page{page_index + 1}_x{xref}.{info['ext']}"
        with open(name, "wb") as f:
            f.write(info["image"])
        print(name, info["width"], "x", info["height"])
print(count, "images extracted")
```

Caveats:

- Transparency masks: some images carry a separate soft mask (the SMask
  entry). `extract_image` returns the base image; a figure that looks wrong
  when extracted may need to be rendered from the page instead.
- Color: CMYK or exotic colorspace images may look off after extraction.
  Rendering the region (below) sidesteps colorspace issues because PyMuPDF
  composites the page the way a viewer would.
- Vector figures: plots drawn as vector graphics (matplotlib PDF output,
  TikZ) contain no raster image at all. `get_images` finds nothing; render
  the region instead.

### Rendering pages and regions

```python
import fitz

doc = fitz.open("paper.pdf")
page = doc[4]

# Full page. dpi=150 is fine for reading; use 300 for fine detail.
page.get_pixmap(dpi=150).save("page5.png")

# Specific region in PDF points (origin at top-left; 72 points = 1 inch)
clip = fitz.Rect(50, 100, 550, 400)
page.get_pixmap(clip=clip, dpi=300).save("region.png")
```

### Locating a figure by its caption

Captions are searchable text even when the figure is vector art:

```python
import fitz

def render_figure(doc, caption, out_path, above=320, below=10, dpi=300):
    """Render the region above a caption like 'Figure 3' to a PNG.

    Assumes the figure sits above its caption (the usual layout). Adjust
    'above' if the crop cuts the figure off, or use below>above for
    styles that place captions on top.
    """
    for page in doc:
        hits = page.search_for(caption)
        if not hits:
            continue
        cap = hits[0]
        clip = fitz.Rect(
            0, max(cap.y0 - above, 0),
            page.rect.width, min(cap.y1 + below, page.rect.height),
        )
        page.get_pixmap(clip=clip, dpi=dpi).save(out_path)
        return page.number + 1
    return None

doc = fitz.open("paper.pdf")
page_no = render_figure(doc, "Figure 3", "figure3.png")
print("rendered from page", page_no)
```

Check the output image; if the crop clipped the figure, increase `above`
and re-render. In a two-column paper the figure may occupy only one column;
narrow the clip's x-range to the caption's column
(`cap.x0 - 20` to `page.rect.width / 2` or similar) to avoid grabbing
unrelated content.

## OCR for Scanned PDFs

### Step 1: Confirm OCR is needed

```python
import fitz

doc = fitz.open("paper.pdf")
needs_ocr = []
for i in range(doc.page_count):
    if len(doc[i].get_text().strip()) < 50:
        needs_ocr.append(i)
print(f"{len(needs_ocr)} of {doc.page_count} pages look scanned:",
      [i + 1 for i in needs_ocr])
```

Do not OCR pages that already have a text layer; the native text is more
accurate than OCR output.

### Step 2: Install the OCR stack

```bash
pip install pdf2image pytesseract
apt-get install -y poppler-utils tesseract-ocr   # prefix with sudo if not root
```

The default install includes English. Other languages need extra packs,
for example `apt-get install -y tesseract-ocr-deu`, then pass
`lang="deu"` to pytesseract.

### Step 3: Render and recognize

```python
from pdf2image import convert_from_path
import pytesseract

# first_page/last_page are 1-based and inclusive
images = convert_from_path("scanned.pdf", dpi=300, first_page=1, last_page=5)
for i, image in enumerate(images, start=1):
    text = pytesseract.image_to_string(image)
    with open(f"ocr_page_{i}.txt", "w", encoding="utf-8") as f:
        f.write(text)
    print(f"page {i}: {len(text)} chars")
```

Quality tips:

- Use `dpi=300`. Lower resolutions noticeably hurt accuracy; higher rarely
  helps and is slower.
- Grayscale can help on noisy scans: `image.convert("L")` before OCR.
- Page segmentation: `pytesseract.image_to_string(image, config="--psm 6")`
  treats the page as one uniform text block, which can help when Tesseract
  fragments a simple layout. The default (`--psm 3`, full auto) is right for
  normal papers.
- Word positions: `pytesseract.image_to_data(image,
  output_type=pytesseract.Output.DICT)` returns per-word boxes and
  confidence values, useful for filtering low-confidence junk.
- OCR of tables yields text, not structure. Expect to reconstruct columns
  manually from word positions; treat OCR-derived numbers with extra
  suspicion (0/O, 1/l, 5/S confusions).

### Variant without poppler

If `poppler-utils` cannot be installed, render pages with PyMuPDF and feed
PIL images to Tesseract directly (Tesseract itself is still required):

```python
import fitz
import pytesseract
from PIL import Image

doc = fitz.open("scanned.pdf")
for i in range(doc.page_count):
    pix = doc[i].get_pixmap(dpi=300)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    text = pytesseract.image_to_string(image)
    print(f"--- page {i + 1} ---\n{text[:500]}")
```

## Metadata and References Details

### Title when metadata is missing

Paper PDFs frequently ship with empty or wrong metadata (`meta.title` set to
the LaTeX filename, for example). The visually largest text on page 1 is a
reliable title candidate:

```python
import fitz

doc = fitz.open("paper.pdf")
spans = []
for block in doc[0].get_text("dict")["blocks"]:
    for line in block.get("lines", []):
        for span in line["spans"]:
            spans.append((span["size"], span["text"]))
if spans:
    max_size = max(s for s, _ in spans)
    title = " ".join(t for s, t in spans if s >= max_size - 0.5).strip()
    print("Title candidate:", title)
```

### Splitting reference entries

After locating the references section (SKILL.md shows the locator), split
into individual entries. The right pattern depends on the citation style:

```python
import re

# Bracketed numeric: [1] Author, ...
chunks = re.split(r"\n(?=\[\d+\]\s)", ref_text)
entries = [c for c in chunks if re.match(r"\[\d+\]", c.lstrip())]

# Plain numeric: 1. Author, ...
chunks = re.split(r"\n(?=\d{1,3}\.\s+[A-Z])", ref_text)
entries = [c for c in chunks if re.match(r"\d{1,3}\.", c.lstrip())]
```

Splitting keeps whatever precedes the first entry (the section heading and
any trailing body text) as the first chunk; the filter line drops it.

Author-year styles (APA and similar) have no reliable delimiter; entries are
separated only by hanging indents that plain text extraction discards. For
those, keep the section as raw text and let downstream matching work on the
whole block, or split on lines that start with a capitalized surname
followed by initials, accepting some errors.

Cleanup that always helps before matching references against bibliographic
databases:

```python
def clean_entry(entry):
    # Rejoin hyphenated line breaks, then collapse whitespace
    entry = re.sub(r"-\n(?=[a-z])", "", entry)
    return re.sub(r"\s+", " ", entry).strip()
```

Also strip running headers and page numbers that fall inside the section
text when the references span several pages (see Troubleshooting).

## Troubleshooting

### Output full of "(cid:123)" tokens

The PDF's fonts lack a usable character map, so the extractor cannot
translate glyphs to text. No text-extraction settings will fix this. Treat
the document like a scan: render the pages and OCR them.

### Ligatures and hyphenation

Extracted text often contains typographic ligatures and end-of-line
hyphenation from justified columns:

```python
import re

# Unicode ligature code points, written as escapes: ff fi fl ffi ffl
LIGATURES = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
}

def normalize_text(text):
    for lig, plain in LIGATURES.items():
        text = text.replace(lig, plain)
    # Rejoin words hyphenated across line breaks (lowercase continuation)
    return re.sub(r"-\n(?=[a-z])", "", text)
```

Apply this before searching extracted text for a phrase; a term like
"classifier" may be stored as "classi" + fi-ligature + "er" and a plain
substring search will miss it. PyMuPDF's `search_for` handles ligatures
itself, so prefer it for locating text.

### Repeated headers and footers

Running headers, footers, and page numbers repeat on every page and pollute
concatenated text. Crop the margins before extraction:

```python
import pdfplumber

with pdfplumber.open("paper.pdf") as pdf:
    page = pdf.pages[3]
    body = page.crop((0, 40, page.width, page.height - 40))
    print(body.extract_text())
```

Tune the 40-point margins to the paper's layout (check a rendered page).

### Rotated pages

Landscape tables are sometimes stored as rotated pages. Check
`page.rotation` in PyMuPDF (0, 90, 180, or 270). Rendering with
`get_pixmap` respects rotation automatically, so rendering plus visual
reading (or OCR) is the easy path for rotated content.

### Superscripts merged into words

Affiliation markers and footnote numbers sit in the text stream next to the
word they annotate, so author lines extract like "Jane Doe1,2". Strip
trailing digit/comma runs from name tokens when parsing author lists, and
strip trailing markers from numeric cells before parsing (see table
cleaning above).

### Numbers that do not match the rendered page

If an extracted value looks wrong, render that region of the page to PNG and
check visually before concluding anything. Extraction bugs (merged columns,
dropped minus signs, misassigned cells) are far more common than papers
printing the wrong number. The rendered page is ground truth for what the
paper says.
