"""Paper review via the vendored OpenAIReview engine (functionality integration).

Runs OpenAIReview's progressive reviewer over a parsed paper and returns its
findings as veritas ``Comment`` objects, so the paper-only review mode produces
the same rich technical/logical in-line comments OpenAIReview does. LLM calls go
through the vendored engine's ``client`` (OpenAI-SDK over the configured
provider), which reads API keys from the environment (shared veritas ``.env``).

This is the "functionality" half of the OpenAIReview integration; the engine's
comments are anchored to the SAME paragraph split used by the viewer, so indices
line up without re-anchoring.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional, Tuple

from veritas.core.inline import Comment
from veritas.core.paper_parse import parse_paper_markdown
from veritas.review_engine.utils import split_into_paragraphs


# OpenAIReview emits comment_type in {"technical","logical"}; veritas uses a
# richer category vocab but keeps these two as-is so exporters can map back.
def _category(comment_type: Optional[str]) -> str:
    return "technical" if (comment_type or "").lower() == "technical" else "logical"


def has_review_key() -> bool:
    """True if any provider key the engine understands is in the environment."""
    return any(
        os.environ.get(k)
        for k in (
            "OPENROUTER_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
            "GEMINI_API_KEY", "MISTRAL_API_KEY",
        )
    )


def review_paper(
    paper_path: Path,
    slug: str,
    model: str = "openai/gpt-4o",
    provider: Optional[str] = None,
    ocr: bool = False,
    reasoning_effort: Optional[str] = None,
) -> Tuple[Optional[str], str, List[str], str, List[Comment], dict]:
    """Review a paper with the vendored progressive engine.

    Returns ``(title, markdown, paragraphs, overall_feedback, comments, meta)``.
    Raises if parsing yields no text or the engine call fails (callers decide
    whether to fall back).
    """
    title, md = parse_paper_markdown(Path(paper_path), ocr=ocr)
    if not md or not md.strip():
        raise RuntimeError(f"could not parse paper text from {paper_path}")

    # The engine's chat() resolves provider from REVIEW_PROVIDER env when not
    # auto-detecting; set it for this process when the caller forces one. (The
    # progressive method doesn't take a provider argument.)
    if provider:
        os.environ["REVIEW_PROVIDER"] = provider

    from veritas.review_engine.method_progressive import review_progressive

    consolidated, _full = review_progressive(
        paper_slug=slug,
        document_content=md,
        model=model,
        reasoning_effort=reasoning_effort,
    )

    paragraphs = split_into_paragraphs(md)
    comments: List[Comment] = []
    for i, c in enumerate(consolidated.comments):
        comments.append(Comment(
            id=f"oar_{i}",
            title=getattr(c, "title", None) or "Comment",
            quote=getattr(c, "quote", "") or "",
            explanation=getattr(c, "explanation", "") or "",
            category=_category(getattr(c, "comment_type", None)),
            severity="moderate",
            paragraph_index=getattr(c, "paragraph_index", None),
        ))

    meta = {
        "engine": "openaireview-progressive",
        "model": getattr(consolidated, "model", model),
        "prompt_tokens": getattr(consolidated, "total_prompt_tokens", 0),
        "completion_tokens": getattr(consolidated, "total_completion_tokens", 0),
        "num_comments": len(comments),
    }
    return title, md, paragraphs, consolidated.overall_feedback or "", comments, meta
