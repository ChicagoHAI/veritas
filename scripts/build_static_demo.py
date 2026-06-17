#!/usr/bin/env python
"""Build the polished static demo from finished veritas runs.

For each (label, run_output_dir): assemble the ReviewBundle, clean the parsed
paragraphs, locate each comment's quote span for precise highlighting, and
render the redesigned in-line viewer (templates/demo/inline_viewer.html) + the
referee report (run mode → templates/demo/referee_report.html; read mode → the
run's reproducibility report). Then emit an index.html.

Usage:
  python scripts/build_static_demo.py --out OUT "Label:::/abs/run_dir" ...
"""
import argparse
import html as _html
import json
import re
import shutil
from pathlib import Path

from veritas.core.review_bundle import _comment_type, assemble_bundle_from_output

_TPL = Path(__file__).resolve().parent.parent / "templates" / "demo"


# -- paragraph cleaning (strip pymupdf4llm artifacts) -----------------------

def _flatten_md_table(t: str) -> str:
    cells = []
    for line in t.splitlines():
        s = line.strip()
        if not s.startswith("|"):
            cells.append(("raw", s))
            continue
        if re.fullmatch(r"\|?[\s:|-]*\|?", s):
            continue
        for part in s.strip("|").split("|"):
            part = part.replace("<br>", " ").strip()
            if part:
                cells.append(("cell", part))
    out = []
    for kind, val in cells:
        if kind == "raw":
            out.append(val)
        elif len(val) > 220:
            out.append(val)
        else:
            out.append("_" + val + "_")
    return "\n\n".join(out)


def clean_paragraph_text(text: str) -> str:
    t = text
    t = re.sub(r"\*{0,2}==>\s*picture\s*\[[^\]]*\]\s*intentionally omitted\s*<==\*{0,2}", "", t, flags=re.I)
    t = re.sub(r"\*{0,2}-+\s*Start of picture text\s*-+\*{0,2}.*?(?:\*{0,2}-+\s*End of picture text\s*-+\*{0,2}|$)",
               "", t, flags=re.I | re.S)
    t = re.sub(r"(?m)^\s*\**\d*\**\s*\|?\s*Nature\s*\|\s*www\.nature\.com\s*\|?\s*\**\d*\**\s*$", "", t)
    if re.search(r"(?m)^\s*\|.*\|\s*$", t) and "|---" in t:
        t = _flatten_md_table(t)
    t = t.replace("<br>", " ")
    t = re.sub(r"(?m)^\s*\|?[\s:|-]*\|?\s*$", "", t)
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    return t.strip()


# -- precise quote -> char-span location ------------------------------------

def _norm(s: str) -> str:
    s = s.lower()
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def _norm_with_map(raw: str):
    chars, idx = [], []
    prev_space = True
    for i, ch in enumerate(raw):
        c = ch.lower()
        if c.isalnum():
            chars.append(c)
            idx.append(i)
            prev_space = False
        elif not prev_space:
            chars.append(" ")
            idx.append(i)
            prev_space = True
    while chars and chars[-1] == " ":
        chars.pop()
        idx.pop()
    return "".join(chars), idx


def locate_quote(paragraph: str, quote: str):
    if not quote:
        return None
    i = paragraph.find(quote)
    if i >= 0:
        return [i, i + len(quote)]
    norm, idx = _norm_with_map(paragraph)
    nq = _norm(quote)
    if not nq or not norm:
        return None
    pos = norm.find(nq)
    if pos < 0:
        words = nq.split()
        if len(words) >= 4:
            for take in (12, 10, 8, 6, 5, 4):
                if take <= len(words):
                    frag = " ".join(words[:take])
                    pos = norm.find(frag)
                    if pos >= 0:
                        nq = frag
                        break
    if pos < 0:
        return None
    end = pos + len(nq) - 1
    if pos >= len(idx) or end >= len(idx) or end < 0:
        return None
    return [idx[pos], idx[end] + 1]


_SEV = {"major": "major", "moderate": "moderate", "minor": "minor", "info": "minor"}


def _inject(template_path: Path, payload: dict) -> str:
    data = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    return template_path.read_text(encoding="utf-8").replace("/*__DATA__*/", data)


# -- per-run rendering ------------------------------------------------------

def render_inline(bundle, dest: Path):
    title = bundle.title or ""
    ntitle = _norm(title)
    cleaned = []
    for p in bundle.paragraphs:
        ct = clean_paragraph_text(p)
        lines, kept = ct.split("\n"), []
        for ln in lines:
            bare = ln.strip().lstrip("#").strip().strip("*_ ")
            if not kept and ln.strip() in ("", "## Article", "Article", "# Article"):
                continue
            if not kept and bare and _norm(bare) == ntitle:
                continue
            kept.append(ln)
        cleaned.append("\n".join(kept).strip())

    comments, located = [], 0
    for c in bundle.comments:
        pi = c.paragraph_index
        span = locate_quote(cleaned[pi], c.quote) if (pi is not None and 0 <= pi < len(cleaned)) else None
        if span:
            located += 1
        comments.append({
            "id": c.id, "title": c.title, "quote": c.quote, "explanation": c.explanation,
            "severity": _SEV.get(c.severity, "minor"), "comment_type": _comment_type(c.category),
            "paragraph_index": pi, "quote_span": span, "verified_by_run": c.verified_by_run,
        })
    payload = {
        "title": title, "overall_feedback": bundle.overall_feedback,
        "paragraphs": [{"index": i, "text": t} for i, t in enumerate(cleaned)],
        "comments": comments,
        "stats": {"n_comments": len(comments), "n_located": located},
    }
    (dest / "inline.html").write_text(_inject(_TPL / "inline_viewer.html", payload), encoding="utf-8")
    return len(comments), located


def _section(verdict_sections, *needles):
    for s in verdict_sections:
        h = (s.get("heading") or "").lower()
        if any(n in h for n in needles):
            return s.get("body", "")
    return ""


def render_report(bundle, run_dir: Path, dest: Path) -> bool:
    """Run mode → polished referee report; read mode → copy the run's report."""
    if not bundle.score:
        src = run_dir / "report" / "replication_report.html"
        if src.exists():
            shutil.copyfile(src, dest / "report.html")
        return False
    vs = bundle.verdict_sections
    claims = {}
    cdoc = run_dir / "analyze" / "paper_claims.json"
    if cdoc.exists():
        claims = {c["id"]: c for c in json.loads(cdoc.read_text()).get("claims", [])}
    verdicts = []
    vpath = run_dir / "verify" / "verdicts.json"
    if vpath.exists():
        for v in json.loads(vpath.read_text()):
            cl = claims.get(v["claim_id"], {})
            verdicts.append({
                "id": v["claim_id"], "status": v.get("status", ""),
                "tier": cl.get("tier", ""), "type": cl.get("type", ""),
                "description": cl.get("description", ""), "rationale": v.get("rationale", ""),
                "structured": v.get("structured"), "evidence_refs": v.get("evidence_refs", []),
            })
    payload = {
        "paper_title": bundle.title,
        "score": bundle.score,
        "bottomline": _section(vs, "verdict") or (bundle.overall_feedback or "")[:400],
        "what_checked": _section(vs, "summary", "what was checked"),
        "what_not": _section(vs, "did not"),
        "consistency": _section(vs, "methodology", "consistency"),
        "divergence": _section(vs, "limitation", "divergence"),
        "rows": verdicts,
    }
    (dest / "report.html").write_text(_inject(_TPL / "referee_report.html", payload), encoding="utf-8")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("instances", nargs="+", help='each "Label:::/abs/run_dir"')
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)

    cards = []
    for spec in args.instances:
        label, _, run_dir = spec.partition(":::")
        run_dir = Path(run_dir)
        slug = run_dir.name
        dest = args.out / slug
        dest.mkdir(parents=True, exist_ok=True)

        bundle = assemble_bundle_from_output(run_dir, slug=slug)
        n, located = render_inline(bundle, dest)
        polished = render_report(bundle, run_dir, dest)
        has_report = (dest / "report.html").exists()

        if bundle.score and bundle.score.get("score") is not None:
            headline = f"Replication {round(bundle.score['score'] * 100)}%"
        elif bundle.reproducibility:
            headline = f"Reproducibility risk: {bundle.reproducibility.get('overall_risk', '?').upper()}"
        else:
            headline = ""
        cards.append({"label": label, "slug": slug, "depth": bundle.depth, "mode": bundle.mode,
                      "headline": headline, "n": n, "located": located,
                      "has_report": has_report, "title": bundle.title})
        print(f"[{label}] {slug}: {n} comments ({located} quote-located), "
              f"report={'polished' if polished else 'basic' if has_report else 'none'}")

    _write_index(args.out, cards)
    print(f"\nStatic demo: {args.out / 'index.html'}")


def _write_index(out: Path, cards: list) -> None:
    title = cards[0]["title"] if cards else "Veritas demo"
    rows = ""
    for c in cards:
        rep = f'<a href="{c["slug"]}/report.html">Referee report</a>' if c["has_report"] else '<span class="na">no report</span>'
        pct = f"{round(c['located'] * 100 / max(c['n'], 1))}%"
        rows += f"""
      <div class="card">
        <div class="lbl">{_html.escape(c['label'])}</div>
        <div class="sub">{c['mode']} · depth {c['depth']}</div>
        <div class="hl">{_html.escape(c['headline'])}</div>
        <div class="stat">{c['n']} in-line comments · {pct} on a precise quote</div>
        <div class="links"><a href="{c['slug']}/inline.html">In-line review</a> · {rep}</div>
      </div>"""
    (out / "index.html").write_text(f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Veritas demo — {_html.escape(title)}</title>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800&family=Source+Serif+4:wght@600&display=swap" rel="stylesheet">
<style>
 body{{font-family:'Inter',system-ui,sans-serif;background:#f7f9f9;color:#111827;margin:0;padding:40px 24px 80px;}}
 .head{{max-width:1000px;margin:0 auto 26px;}}
 .logo{{font-weight:800;color:#0f4f48;font-size:15px}}
 h1{{font-family:'Source Serif 4',Georgia,serif;font-weight:600;font-size:1.8rem;margin:6px 0 4px}}
 .psub{{color:#6b7280;font-size:.9rem;max-width:760px;line-height:1.55}}
 .grid{{display:flex;flex-wrap:wrap;gap:16px;max-width:1000px;margin:0 auto}}
 .card{{background:#fff;border:1px solid #e5e7eb;border-radius:14px;padding:18px 20px;flex:1 1 280px;
        box-shadow:0 1px 3px rgba(0,0,0,.06);border-top:3px solid #146c5f}}
 .lbl{{font-weight:800;font-size:1.02rem}} .sub{{color:#6b7280;font-size:.76rem;margin:3px 0 10px}}
 .hl{{font-weight:700;margin:6px 0;color:#0f4f48}} .stat{{font-size:.82rem;color:#6b7280;margin-bottom:12px}}
 .links a{{color:#146c5f;text-decoration:none;font-weight:700}} .na{{color:#9aa0a6}}
</style></head><body>
 <div class="head"><span class="logo">Veritas</span>
   <h1>{_html.escape(title)}</h1>
   <div class="psub">Three review depths. Each shows an in-line review (the paper with anchored comments) and a referee report. Comments are unified; a "verified by running" mark indicates findings veritas confirmed by executing the code.</div>
 </div>
 <div class="grid">{rows}
 </div>
</body></html>""", encoding="utf-8")


if __name__ == "__main__":
    main()
