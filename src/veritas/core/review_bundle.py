"""ReviewBundle: veritas's one canonical review asset + exporters.

A ReviewBundle is what the veritas backend engine emits for any review (read or
run mode): the parsed paper paragraphs, the anchored comments, an overall
feedback blurb, the referee verdict sections, and the mode-specific summary
(read-mode Reproducibility Assessment or run-mode Replication Score). Thin
exporters render the SAME bundle to:

  - the OpenAIReview viz viewer JSON (``to_oar_viz``)
  - the sai-web demo JSON (``to_saiweb_demo``) and richer ``Review`` (``to_saiweb_review``)

This is the modular seam the user asked for: one engine -> one bundle -> many
exporters; the static demo consumes the exports now, and a live demo can call
the same engine + exporters later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from veritas.core.inline import Comment

# -- field mappings between veritas and the two output schemas ---------------

# veritas comment.category -> OpenAIReview / sai-web comment_type (2-valued).
# Execution/numeric lenses read as "technical"; argumentation lenses as "logical".
def _comment_type(category: str) -> str:
    return "technical" if category in ("technical", "statistical", "replication") else "logical"


# veritas severity -> OpenAIReview viz severity (major|moderate|minor).
_OAR_SEVERITY = {"major": "major", "moderate": "moderate", "minor": "minor", "info": "minor"}
# veritas severity -> sai-web Review severity (Major|Minor|Info).
_SAIWEB_SEVERITY = {"major": "Major", "moderate": "Minor", "minor": "Minor", "info": "Info"}


@dataclass
class ReviewBundle:
    """Canonical, exporter-agnostic result of a veritas review."""
    slug: str
    title: str
    mode: str = "paper-only"
    depth: str = "read"
    paragraphs: List[str] = field(default_factory=list)
    comments: List[Comment] = field(default_factory=list)
    overall_feedback: str = ""
    # Referee verdict, as ordered (heading, body) sections.
    verdict_sections: List[Dict[str, str]] = field(default_factory=list)
    # Mode-specific headline summary (exactly one is typically set).
    reproducibility: Optional[Dict[str, Any]] = None  # read-mode aggregate
    score: Optional[Dict[str, Any]] = None            # run-mode replication score
    # Engine provenance (model, token counts).
    engine_meta: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "mode": self.mode,
            "depth": self.depth,
            "paragraphs": [{"index": i, "text": p} for i, p in enumerate(self.paragraphs)],
            "comments": [c.to_dict() for c in self.comments],
            "overall_feedback": self.overall_feedback,
            "verdict_sections": self.verdict_sections,
            "reproducibility": self.reproducibility,
            "score": self.score,
            "engine_meta": self.engine_meta,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "ReviewBundle":
        return cls(
            slug=d.get("slug", ""),
            title=d.get("title", ""),
            mode=d.get("mode", "paper-only"),
            depth=d.get("depth", "read"),
            paragraphs=[p["text"] for p in d.get("paragraphs", [])],
            comments=[Comment.from_dict(c) for c in d.get("comments", [])],
            overall_feedback=d.get("overall_feedback", ""),
            verdict_sections=d.get("verdict_sections", []),
            reproducibility=d.get("reproducibility"),
            score=d.get("score"),
            engine_meta=d.get("engine_meta"),
        )


# -- exporters --------------------------------------------------------------

def to_oar_viz(
    bundle: ReviewBundle,
    method_key: str = "veritas",
    method_label: Optional[str] = None,
) -> Dict[str, Any]:
    """Render the bundle to the OpenAIReview viewer JSON schema."""
    meta = bundle.engine_meta or {}
    model = meta.get("model", "veritas")
    label = method_label or f"Veritas ({bundle.depth})"
    comments = []
    for c in bundle.comments:
        comments.append({
            "id": c.id,
            "title": c.title,
            "quote": c.quote,
            "explanation": c.explanation,
            "comment_type": _comment_type(c.category),
            "paragraph_index": c.paragraph_index,
            "severity": _OAR_SEVERITY.get(c.severity, "moderate"),
        })
    return {
        "slug": bundle.slug,
        "title": bundle.title,
        "paragraphs": [{"index": i, "text": p} for i, p in enumerate(bundle.paragraphs)],
        "methods": {
            method_key: {
                "label": label,
                "model": model,
                "overall_feedback": bundle.overall_feedback,
                "comments": comments,
                "cost_usd": meta.get("cost_usd", 0),
                "prompt_tokens": meta.get("prompt_tokens", 0),
                "completion_tokens": meta.get("completion_tokens", 0),
            }
        },
    }


def to_saiweb_demo(bundle: ReviewBundle) -> Dict[str, Any]:
    """Render the bundle to sai-web's ``demo-paper.json`` shape (the demo page)."""
    commented = {c.paragraph_index for c in bundle.comments if c.paragraph_index is not None}
    return {
        "slug": bundle.slug,
        "title": bundle.title,
        "overallFeedback": bundle.overall_feedback,
        "paragraphs": [
            {"index": i, "text": p, "hasComment": i in commented}
            for i, p in enumerate(bundle.paragraphs)
        ],
        "comments": [
            {
                "id": c.id,
                "title": c.title,
                "quote": c.quote,
                "explanation": c.explanation,
                "commentType": _comment_type(c.category),
                "paragraphIndex": c.paragraph_index,
            }
            for c in bundle.comments
        ],
    }


def to_saiweb_review(bundle: ReviewBundle, project_id: str) -> Dict[str, Any]:
    """Render the bundle to sai-web's richer ``Review`` type (verdict + methods)."""
    recommendation = ""
    if bundle.reproducibility:
        recommendation = bundle.reproducibility.get("recommendation", "")
    meta = bundle.engine_meta or {}
    return {
        "id": f"review_{project_id}",
        "projectId": project_id,
        "title": bundle.title,
        "recommendation": recommendation,
        "sourceUrl": "",
        "inlineSummary": bundle.overall_feedback,
        "manuscript": [
            {"label": "", "text": p, "index": i,
             "hasComment": any(c.paragraph_index == i for c in bundle.comments)}
            for i, p in enumerate(bundle.paragraphs)
        ],
        "comments": [
            {
                "severity": _SAIWEB_SEVERITY.get(c.severity, "Minor"),
                "target": "",
                "title": c.title,
                "quote": c.quote,
                "text": c.explanation,
                "paragraphIndex": c.paragraph_index,
                "commentType": _comment_type(c.category),
            }
            for c in bundle.comments
        ],
        "verdict": list(bundle.verdict_sections),
        "methods": [
            {
                "id": "veritas",
                "label": f"Veritas ({bundle.depth})",
                "model": meta.get("model", "veritas"),
                "commentCount": len(bundle.comments),
                "promptTokens": meta.get("prompt_tokens"),
                "completionTokens": meta.get("completion_tokens"),
            }
        ],
    }


def write_bundle(bundle: ReviewBundle, out_dir: Path) -> Dict[str, Path]:
    """Write the canonical bundle + both exports into ``out_dir``. Returns paths."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "bundle": out_dir / "review_bundle.json",
        "oar_viz": out_dir / "oar_viz.json",
        "saiweb_demo": out_dir / "saiweb_demo.json",
    }
    paths["bundle"].write_text(json.dumps(bundle.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    paths["oar_viz"].write_text(json.dumps(to_oar_viz(bundle), indent=2, ensure_ascii=False), encoding="utf-8")
    paths["saiweb_demo"].write_text(json.dumps(to_saiweb_demo(bundle), indent=2, ensure_ascii=False), encoding="utf-8")
    return paths
