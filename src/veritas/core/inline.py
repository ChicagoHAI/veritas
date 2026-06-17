"""In-line comment subsystem: anchor findings into the paper and render a viewer.

Produces OpenAIReview-style in-line comments, but through veritas's replication
lens: every comment is either *claim-anchored* (an extracted claim plus its
read-mode assessment or run-mode verdict, shown where the claim appears in the
paper) or a *reviewer* comment (a technical / reproducibility finding from an
LLM pass). Comments are anchored to paper paragraphs by fuzzy quote matching,
then rendered into a single self-contained side-by-side HTML viewer.

Paper parsing (``veritas.core.paper_parse``) and the segmentation + fuzzy
quote-anchoring (``split_into_paragraphs`` / ``locate_comment_in_document``,
imported from ``veritas.review_engine.utils``) come from the vendored
OpenAIReview engine — single source of truth, re-exported here for callers/tests.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import jinja2

from veritas.core.models.paper_claims import ClaimVerdict, PaperClaims
from veritas.core.models.review import ClaimAssessment
from veritas.core.replication import _extract_json

# -- Comment model ----------------------------------------------------------

# Category drives the color/grouping in the viewer.
CommentCategory = str  # "claim-support" | "reproducibility" | "technical"
                       # | "data-availability" | "statistical"
CommentSeverity = str  # "major" | "moderate" | "minor" | "info"


@dataclass
class Comment:
    """One in-line comment anchored (by quote) to a paper paragraph."""
    id: str
    title: str
    quote: str
    explanation: str
    category: CommentCategory = "technical"
    severity: CommentSeverity = "moderate"
    paragraph_index: Optional[int] = None
    claim_id: Optional[str] = None
    # True when veritas actually EXECUTED the code to back this comment (run-mode
    # replication verdicts). Drives the "Verified by running" badge; all comments
    # otherwise render identically regardless of source (OpenAIReview vs claims).
    verified_by_run: bool = False

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "title": self.title,
            "quote": self.quote,
            "explanation": self.explanation,
            "category": self.category,
            "severity": self.severity,
            "paragraph_index": self.paragraph_index,
            "verified_by_run": self.verified_by_run,
        }
        if self.claim_id is not None:
            d["claim_id"] = self.claim_id
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Comment":
        return cls(
            id=str(data.get("id", "")),
            title=data.get("title", ""),
            quote=data.get("quote", ""),
            explanation=data.get("explanation", ""),
            category=data.get("category", "technical"),
            severity=data.get("severity", "moderate"),
            paragraph_index=data.get("paragraph_index"),
            claim_id=data.get("claim_id"),
            verified_by_run=bool(data.get("verified_by_run", False)),
        )


# -- Paper text -> paragraphs ----------------------------------------------
#
# Segmentation + fuzzy anchoring now come from the vendored OpenAIReview engine
# (single source of truth), re-exported here so existing call sites/tests keep
# importing them from this module.
from veritas.review_engine.utils import (  # noqa: E402
    locate_comment_in_document,
    split_into_paragraphs,
)


def extract_paper_text(pdf_path: Path) -> str:
    """Return the paper as markdown text via the vendored engine (no OCR).

    Layout-aware markdown gives fine-grained, heading-aware paragraphs for
    born-digital papers without needing Tesseract/OCR. Returns ``""`` on
    failure so the caller renders comments unanchored (graceful degrade).
    """
    from veritas.core.paper_parse import parse_paper_markdown
    try:
        _title, md = parse_paper_markdown(Path(pdf_path), ocr=False)
        return md or ""
    except Exception:
        return ""


def anchor_comments(comments: List[Comment], paragraphs: List[str]) -> None:
    """Set ``paragraph_index`` on each comment in place via fuzzy matching."""
    for c in comments:
        if c.paragraph_index is None:
            c.paragraph_index = locate_comment_in_document(c.quote, paragraphs)


# -- Claim-anchored comments (deterministic) -------------------------------

# Read-mode support level -> (severity, label).
_SUPPORT_SEVERITY = {
    "unsupported": "major",
    "partial": "moderate",
    "not_assessable": "minor",
    "supported": "info",
}
# Run-mode verdict status -> (severity, label).
_VERDICT_SEVERITY = {
    "no_match": "major",
    "not_attempted": "moderate",
    "partial": "moderate",
    "not_applicable": "minor",
    "match": "info",
}
_VERDICT_LABEL = {
    "match": "reproduced",
    "partial": "partially reproduced",
    "no_match": "did not reproduce",
    "not_attempted": "not attempted",
    "not_applicable": "not applicable",
}


def _claim_quote(claim) -> str:
    """Pick the best anchor quote for a claim (provenance quote, else description)."""
    if claim.provenance and claim.provenance.quote:
        return claim.provenance.quote
    return claim.description


def build_claim_comments(
    claims: PaperClaims,
    assessments: Optional[List[ClaimAssessment]] = None,
    verdicts: Optional[List[ClaimVerdict]] = None,
    drop_uninformative: bool = True,
) -> List[Comment]:
    """Build one claim-anchored comment per *informative* claim.

    ``drop_uninformative`` (default True, product-facing): skip claims veritas
    couldn't speak to — run-mode ``not_attempted`` / ``not_applicable`` and
    read-mode ``not_assessable`` — so the demo shows only useful comments.
    Comments carry ``verified_by_run=True`` when backed by actual execution.
    """
    out: List[Comment] = []
    a_by_id = {a.claim_id: a for a in (assessments or [])}
    v_by_id = {v.claim_id: v for v in (verdicts or [])}
    SKIP_STATUS = {"not_attempted", "not_applicable"}
    SKIP_SUPPORT = {"not_assessable"}

    for claim in claims.claims:
        a = a_by_id.get(claim.id)
        v = v_by_id.get(claim.id)
        quote = None
        if a is not None and a.anchor_quote:
            quote = a.anchor_quote
        if not quote:
            quote = _claim_quote(claim)

        verified_by_run = False
        if a is not None:
            level = a.support_level
            if drop_uninformative and level in SKIP_SUPPORT:
                continue
            severity = _SUPPORT_SEVERITY.get(level, "moderate")
            title = f"[{claim.id}] {level.replace('_', ' ')} · risk: {a.reproducibility_risk}"
            expl = a.rationale or ""
            if a.code_location:
                expl += f"\n\nComputed at: `{a.code_location}`"
            if a.issues:
                expl += "\n\nIssues: " + "; ".join(a.issues)
            category = "reproducibility"
        elif v is not None:
            status = v.status
            if drop_uninformative and status in SKIP_STATUS:
                continue
            severity = _VERDICT_SEVERITY.get(status, "moderate")
            title = f"[{claim.id}] {_VERDICT_LABEL.get(status, status)}"
            expl = v.rationale or ""
            # Surface the produced-vs-paper values the verifier extracted.
            struct = v.structured or {}
            rep = struct.get("replicated_value", struct.get("replicated_table"))
            pap = struct.get("paper_value", struct.get("paper_range", struct.get("paper_table")))
            if rep is not None or pap is not None:
                expl += f"\n\nReplicated: `{rep}`  ·  Paper: `{pap}`"
            category = "replication"
            verified_by_run = True  # a verdict means the code was executed
        else:
            if drop_uninformative:
                continue
            severity = "minor"
            title = f"[{claim.id}] no verdict"
            expl = "This claim was extracted but not assessed."
            category = "claim-support"

        expl = (expl or "(no rationale)").strip()
        expl = f"**Claim ({claim.tier}, {claim.type}):** {claim.description}\n\n{expl}"
        out.append(Comment(
            id=f"claim_{claim.id}",
            title=title,
            quote=quote,
            explanation=expl,
            category=category,
            severity=severity,
            claim_id=claim.id,
            verified_by_run=verified_by_run,
        ))
    return out


def parse_reviewer_comments(text: str, id_prefix: str = "rev") -> List[Comment]:
    """Parse the LLM reviewer pass output (a JSON array of comment objects)."""
    raw = _extract_json(text)
    data = json.loads(raw)
    if isinstance(data, dict) and "comments" in data:
        data = data["comments"]
    if not isinstance(data, list):
        raise ValueError("reviewer-comments output is not a JSON array")
    out: List[Comment] = []
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            continue
        out.append(Comment(
            id=f"{id_prefix}_{i}",
            title=item.get("title", "Comment"),
            quote=item.get("quote", ""),
            explanation=item.get("explanation", ""),
            category=item.get("category", "technical"),
            severity=item.get("severity", "moderate"),
            claim_id=item.get("claim_id"),
        ))
    return out


# -- Viewer rendering -------------------------------------------------------

def _templates_dir() -> Path:
    return Path(__file__).resolve().parents[3] / "templates"


def render_viewer(
    title: str,
    subtitle: str,
    paragraphs: List[str],
    comments: List[Comment],
) -> str:
    """Render the self-contained side-by-side viewer HTML.

    The paper paragraphs and comments are embedded as JSON in the page, so the
    file is a single shareable artifact (no server, no fetch).
    """
    payload = {
        "title": title,
        "subtitle": subtitle,
        "paragraphs": [{"index": i, "text": p} for i, p in enumerate(paragraphs)],
        "comments": [c.to_dict() for c in comments],
    }
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_templates_dir())),
        autoescape=False,  # the template injects JSON via |tojson; no autoescape needed
    )
    template = env.get_template("inline/viewer.html.j2")
    return template.render(
        page_title=title,
        data_json=json.dumps(payload, ensure_ascii=False),
    )


# -- Orchestrator -----------------------------------------------------------

def generate_inline_review(
    config,
    claims: PaperClaims,
    assessments: Optional[List[ClaimAssessment]] = None,
    verdicts: Optional[List[ClaimVerdict]] = None,
    prompt_generator=None,
    invoke_provider: Optional[Callable] = None,
) -> Path:
    """Produce inline_comments.json and the side-by-side viewer.

    Always emits the deterministic claim-anchored comments. When a provider is
    available, also runs an LLM reviewer pass for richer technical /
    reproducibility comments; failures there are non-fatal. Returns the viewer
    path.
    """
    config.inline_dir.mkdir(parents=True, exist_ok=True)

    # 1. Paper text -> paragraphs (for anchoring + the viewer's left pane).
    paragraphs: List[str] = []
    if config.has_paper:
        text = extract_paper_text(config.paper_path)
        paragraphs = split_into_paragraphs(text) if text else []
    config.paper_text_path.write_text(
        json.dumps({"paragraphs": paragraphs}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if not paragraphs:
        print("  Note: could not extract paper text; comments will be unanchored.")

    # 2. Deterministic claim-anchored comments.
    comments: List[Comment] = build_claim_comments(claims, assessments, verdicts)

    # 3. Reviewer comments. Prefer the vendored OpenAIReview engine (progressive
    #    method, needs a provider API key); fall back to the CLI reviewer pass.
    overall_feedback = ""
    engine_meta: Optional[dict] = None
    from veritas.core.paper_review import has_review_key
    if config.has_paper and has_review_key():
        try:
            overall_feedback, engine_comments, engine_meta = _run_review_engine(
                config, paragraphs
            )
            comments.extend(engine_comments)
        except Exception as e:
            print(f"  Note: review-engine pass failed ({e}); trying CLI reviewer pass.")
            engine_meta = None
    if engine_meta is None and invoke_provider is not None and prompt_generator is not None:
        try:
            comments.extend(_run_reviewer_pass(config, prompt_generator, invoke_provider))
        except Exception as e:
            print(f"  Note: reviewer-comment pass skipped: {e}")

    # 4. Anchor everything to paragraphs (engine comments already carry an index).
    anchor_comments(comments, paragraphs)

    # 5. Persist + render.
    config.inline_comments_path.write_text(
        json.dumps([c.to_dict() for c in comments], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    if overall_feedback:
        (config.inline_dir / "overall_feedback.md").write_text(
            overall_feedback, encoding="utf-8"
        )
    title = (claims.paper.get("title") if claims and claims.paper else None) or "Paper review"
    depth_label = "read-only review" if config.depth == "read" else "replication review"
    n_anchored = sum(1 for c in comments if c.paragraph_index is not None)
    html = render_viewer(
        title=title,
        subtitle=f"Veritas {depth_label} · {len(comments)} comments",
        paragraphs=paragraphs,
        comments=comments,
    )
    config.inline_viewer_path.write_text(html, encoding="utf-8")

    # Also emit the OpenAIReview viewer (markdown + MathJax, multi-method, metrics)
    # as a self-contained artifact, fed the same comments via the ReviewBundle.
    try:
        from veritas.core.oar_viewer import render_oar_viewer
        from veritas.core.review_bundle import ReviewBundle, to_oar_viz
        bundle = ReviewBundle(
            slug=Path(config.paper_path).stem if config.has_paper else "paper",
            title=title, mode=config.mode, depth=config.depth,
            paragraphs=paragraphs, comments=comments,
            overall_feedback=overall_feedback, engine_meta=engine_meta,
        )
        (config.inline_dir / "oar_review.html").write_text(
            render_oar_viewer(to_oar_viz(bundle)), encoding="utf-8"
        )
    except Exception as e:
        print(f"  Note: OpenAIReview viewer not emitted: {e}")
    print(
        f"  Inline comments: {len(comments)} total "
        f"({n_anchored} anchored to paragraphs). Viewer: {config.inline_viewer_path}"
    )
    return config.inline_viewer_path


def _run_review_engine(config, paragraphs: List[str]):
    """Run the vendored OpenAIReview progressive engine; return (feedback, comments, meta).

    Comments come back with ``paragraph_index`` already set against the same
    paragraph split the viewer uses (both go through ``parse_paper_markdown`` +
    ``split_into_paragraphs``), so no re-anchoring is needed for them.
    """
    from veritas.core.paper_review import review_paper

    slug = Path(config.paper_path).stem
    _title, _md, _paras, feedback, comments, meta = review_paper(
        Path(config.paper_path),
        slug=slug,
        model=config.review_model,
        provider=config.review_provider,
    )
    print(
        f"  Review engine [{meta.get('model')}]: {len(comments)} comments, "
        f"{meta.get('prompt_tokens')}+{meta.get('completion_tokens')} tokens"
    )
    return feedback, comments, meta


def _run_reviewer_pass(config, prompt_generator, invoke_provider) -> List[Comment]:
    """Run the LLM reviewer pass and parse its comments (raises on failure)."""
    prompt = prompt_generator.generate_inline_reviewer_prompt(
        paper_path=config.paper_path,
        output_dir=config.output_dir,
        repo_path=config.repo_path if config.has_repo else None,
        depth=config.depth,
    )
    prompt_path = config.prompts_dir / "inline_reviewer_prompt.txt"
    prompt_path.write_text(prompt, encoding="utf-8")

    out_path = config.inline_dir / "reviewer_comments.json"
    success = invoke_provider(
        prompt=prompt,
        working_dir=config.output_dir,
        log_path=config.inline_transcript_path,
        timeout=config.review_timeout,
    )
    if not success or not out_path.exists():
        raise RuntimeError("reviewer pass did not produce output")
    text = out_path.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("reviewer output empty")
    return parse_reviewer_comments(text)
