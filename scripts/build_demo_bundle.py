#!/usr/bin/env python
"""Assemble a ReviewBundle for the demo and export it to sai-web format.

Two input paths:
  - --engine-json FILE : reuse a saved engine review (comments + overall_feedback)
                         so we don't pay for a second LLM run.
  - (no --engine-json) : run the review engine live on --paper.

Writes the canonical bundle + exports (oar_viz.json, saiweb_demo.json) into
--out, and optionally copies the sai-web demo JSON to --saiweb-out.

Usage:
  python scripts/build_demo_bundle.py --paper P.pdf --slug cooperation \
      --engine-json demo-papers/sample-outputs/engine-test-comments.json \
      --out demo-papers/sample-outputs/cooperation-bundle \
      --saiweb-out ../sai-web/lib/reviews/cooperation.json
"""
import argparse
import json
from pathlib import Path

from veritas.core.inline import Comment
from veritas.core.paper_parse import parse_paper_markdown
from veritas.core.review_bundle import ReviewBundle, to_saiweb_demo, write_bundle
from veritas.review_engine.utils import split_into_paragraphs


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper", required=True, type=Path)
    ap.add_argument("--slug", required=True)
    ap.add_argument("--title", default=None)
    ap.add_argument("--engine-json", type=Path, default=None)
    ap.add_argument("--model", default="openai/gpt-4o")
    ap.add_argument("--provider", default="openrouter")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--saiweb-out", type=Path, default=None)
    args = ap.parse_args()

    title, md = parse_paper_markdown(args.paper, ocr=False)
    paragraphs = split_into_paragraphs(md)

    if args.engine_json and args.engine_json.exists():
        data = json.loads(args.engine_json.read_text())
        title = args.title or data.get("title") or title
        overall = data.get("overall_feedback", "")
        comments = [Comment.from_dict(c) for c in data.get("comments", [])]
        meta = data.get("meta", {})
    else:
        from veritas.core.paper_review import review_paper
        title2, _md, paragraphs, overall, comments, meta = review_paper(
            args.paper, slug=args.slug, model=args.model, provider=args.provider,
        )
        title = args.title or title2 or title

    bundle = ReviewBundle(
        slug=args.slug, title=title or args.slug, mode="paper-only", depth="read",
        paragraphs=paragraphs, comments=comments, overall_feedback=overall,
        engine_meta=meta,
    )
    paths = write_bundle(bundle, args.out)
    print(f"Wrote bundle artifacts to {args.out}:")
    for k, p in paths.items():
        print(f"  {k}: {p} ({p.stat().st_size} bytes)")
    print(f"  comments: {len(bundle.comments)} | paragraphs: {len(bundle.paragraphs)}")

    if args.saiweb_out:
        args.saiweb_out.parent.mkdir(parents=True, exist_ok=True)
        args.saiweb_out.write_text(
            json.dumps(to_saiweb_demo(bundle), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"  sai-web demo JSON -> {args.saiweb_out}")


if __name__ == "__main__":
    main()
