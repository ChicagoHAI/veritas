#!/usr/bin/env python
"""Collect finished veritas runs into one static demo folder for quick viewing.

For each (label, run_output_dir): assemble a ReviewBundle, write the bundle +
exports (oar_viz.json, saiweb_demo.json), render the OpenAIReview inline viewer
(inline.html), copy the referee report (report.html), and finally emit an
index.html linking every instance's inline + report views side by side.

Usage:
  python scripts/build_static_demo.py --out OUT \
      "Paper only:::/abs/run1" "Paper+code (read):::/abs/run2" "Full run:::/abs/run3"
"""
import argparse
import json
import shutil
from pathlib import Path

from veritas.core.oar_viewer import render_oar_viewer
from veritas.core.review_bundle import (
    assemble_bundle_from_output,
    to_oar_viz,
    write_bundle,
)


def _counts(bundle):
    from collections import Counter
    c = Counter(cm.category for cm in bundle.comments)
    veritas = c.get("reproducibility", 0) + c.get("replication", 0) + c.get("claim-support", 0)
    review = sum(v for k, v in c.items() if k not in ("reproducibility", "replication", "claim-support"))
    return veritas, review


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
        write_bundle(bundle, dest)
        (dest / "inline.html").write_text(render_oar_viewer(to_oar_viz(bundle)), encoding="utf-8")

        report_src = run_dir / "report" / "replication_report.html"
        has_report = report_src.exists()
        if has_report:
            shutil.copyfile(report_src, dest / "report.html")

        veritas_n, review_n = _counts(bundle)
        headline = ""
        if bundle.score and bundle.score.get("score") is not None:
            headline = f"Replication Score {round(bundle.score['score']*100)}%"
        elif bundle.reproducibility:
            headline = f"Reproducibility risk: {bundle.reproducibility.get('overall_risk','?').upper()}"
        cards.append({
            "label": label, "slug": slug, "depth": bundle.depth, "mode": bundle.mode,
            "headline": headline, "title": bundle.title,
            "n_comments": len(bundle.comments), "veritas_n": veritas_n, "review_n": review_n,
            "has_report": has_report,
        })
        print(f"[{label}] {slug}: {len(bundle.comments)} comments "
              f"({veritas_n} veritas + {review_n} review), report={has_report}")

    _write_index(args.out, cards)
    print(f"\nStatic demo: {args.out / 'index.html'}")


def _write_index(out: Path, cards: list) -> None:
    rows = ""
    for c in cards:
        report_link = f'<a href="{c["slug"]}/report.html">Referee report</a>' if c["has_report"] else "<span class=na>no report</span>"
        rows += f"""
      <div class="card">
        <div class="lbl">{c['label']}</div>
        <div class="sub">{c['mode']} · depth: {c['depth']}</div>
        <div class="hl">{c['headline']}</div>
        <div class="stat">{c['n_comments']} in-line comments — <b>{c['veritas_n']}</b> veritas, <b>{c['review_n']}</b> OpenAIReview</div>
        <div class="links"><a href="{c['slug']}/inline.html">In-line review</a> · {report_link}</div>
      </div>"""
    title = cards[0]["title"] if cards else "Veritas demo"
    html = f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Veritas demo — {title}</title>
<style>
 body{{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;background:#f6f8fa;color:#1f2328;margin:0;padding:28px 22px 60px;}}
 h1{{font-size:22px;margin:0 0 2px}} .psub{{color:#57606a;font-size:13px;margin-bottom:18px}}
 .grid{{display:flex;flex-wrap:wrap;gap:16px;max-width:1000px}}
 .card{{background:#fff;border:1px solid #d0d7de;border-radius:12px;padding:16px 18px;flex:1 1 280px;box-shadow:0 1px 2px rgba(0,0,0,.04)}}
 .lbl{{font-weight:700;font-size:15px}} .sub{{color:#57606a;font-size:12px;margin:2px 0 8px}}
 .hl{{font-weight:600;margin:6px 0}} .stat{{font-size:13px;color:#57606a;margin-bottom:10px}}
 .links a{{color:#0969da;text-decoration:none;font-weight:600}} .na{{color:#9aa0a6}}
</style></head><body>
 <h1>Veritas demo — {title}</h1>
 <div class="psub">Each instance shows two outputs: the in-line review (OpenAIReview engine comments + veritas reproducibility/replication comments, anchored to the paper) and the referee report.</div>
 <div class="grid">{rows}
 </div>
</body></html>"""
    (out / "index.html").write_text(html, encoding="utf-8")


if __name__ == "__main__":
    main()
