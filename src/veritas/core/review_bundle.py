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


def _verdict_sections_run(score: Optional[dict], evaluation: Optional[dict]) -> List[Dict[str, str]]:
    """Referee verdict sections for a run-mode (execution) review."""
    out: List[Dict[str, str]] = []
    if score and score.get("score") is not None:
        pct = round(score["score"] * 100)
        verdict = "Reproduced" if pct >= 85 else "Partially reproduced" if pct >= 50 else "Not reproduced"
        out.append({"heading": "Replication verdict",
                    "body": f"{verdict} — Replication Score {pct}% "
                            f"({score.get('counted_claims', 0)} claims scored)."})
    rep = (evaluation or {}).get("report", {}) if evaluation else {}
    for heading, key in [
        ("Summary", "replication_summary"),
        ("Important claims", "important_claims"),
        ("What did not replicate", "did_not_replicate"),
        ("Methodology correspondence", "methodology_correspondence"),
        ("Limitations", "code_quality_limitations"),
    ]:
        val = rep.get(key)
        if isinstance(val, str) and val.strip():
            out.append({"heading": heading, "body": val.strip()})
    cm = (evaluation or {}).get("cheating_monitor", {}) if evaluation else {}
    if isinstance(cm.get("risk"), str) and cm["risk"].lower() in ("medium", "high"):
        out.append({"heading": f"Integrity flag — risk: {cm['risk']}",
                    "body": str(cm.get("rationale", "")).strip()})
    return out


def _verdict_sections_read(reproducibility: Optional[dict]) -> List[Dict[str, str]]:
    """Referee verdict sections for a read-mode (no-execution) review."""
    r = reproducibility or {}
    out: List[Dict[str, str]] = []
    if r.get("overall_risk"):
        out.append({"heading": "Reproducibility verdict",
                    "body": f"Overall risk: {r['overall_risk'].upper()}. "
                            f"Specification: {r.get('specification','?')}, code coverage: "
                            f"{r.get('code_coverage','?')}, data availability: {r.get('data_availability','?')}."})
    if r.get("summary"):
        out.append({"heading": "Summary", "body": r["summary"]})
    if r.get("weaknesses"):
        out.append({"heading": "Obstacles to reproduction",
                    "body": "\n".join(f"- {w}" for w in r["weaknesses"])})
    if r.get("recommendation"):
        out.append({"heading": "Recommendation", "body": r["recommendation"]})
    return out


def assemble_bundle_from_output(output_dir: Path, slug: Optional[str] = None) -> ReviewBundle:
    """Assemble a ReviewBundle by reading a completed veritas run's output dir.

    Pulls paragraphs + comments + overall_feedback from ``inline/``, the score
    (run) or reproducibility assessment (read) from their phase dirs, and builds
    referee verdict sections. Works for both depths; missing pieces degrade to
    empty. This is the single function the demo/exporters consume.
    """
    output_dir = Path(output_dir)

    def _load(path: Path):
        try:
            return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        except (OSError, json.JSONDecodeError):
            return None

    claims = _load(output_dir / "analyze" / "paper_claims.json") or {}
    title = (claims.get("paper", {}) or {}).get("title", "") or (slug or "Paper review")

    ptext = _load(output_dir / "inline" / "paper_text.json") or {}
    paragraphs = ptext.get("paragraphs", []) or []

    comments_raw = _load(output_dir / "inline" / "inline_comments.json") or []
    comments = [Comment.from_dict(c) for c in comments_raw]

    fb_path = output_dir / "inline" / "overall_feedback.md"
    overall = fb_path.read_text(encoding="utf-8") if fb_path.exists() else ""

    score = _load(output_dir / "verify" / "replication_score.json")
    reproducibility = _load(output_dir / "review" / "reproducibility_assessment.json")
    evaluation = _load(output_dir / "evaluation" / "contextual_evaluation.json")

    depth = "read" if reproducibility is not None and score is None else "run"
    if depth == "run":
        verdict_sections = _verdict_sections_run(score, evaluation)
    else:
        verdict_sections = _verdict_sections_read(reproducibility)

    return ReviewBundle(
        slug=slug or output_dir.name,
        title=title,
        mode="full" if depth == "run" else "paper-only",
        depth=depth,
        paragraphs=paragraphs,
        comments=comments,
        overall_feedback=overall,
        verdict_sections=verdict_sections,
        reproducibility=reproducibility,
        score=score,
    )


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
