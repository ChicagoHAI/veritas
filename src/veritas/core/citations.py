"""Deterministic, LLM-free reference/bibliographic verifier.

Verifies whether a paper's cited references exist and carry correct metadata
(title, authors, year, venue, identifiers) by querying free scholarly-metadata
APIs and comparing the returned record against the citation. The method is
adapted from the refchecker project (https://github.com/markrussinovich/refchecker,
MIT): a multi-source lookup plus deterministic mismatch filters (author overlap,
identifier conflict). It does NOT call an LLM and does NOT auto-correct entries;
it classifies each reference and attaches the authoritative record it found.

This module imports ONLY the Python standard library so it can be copied into an
agent's workspace and run standalone (the citation-check subagent invokes it as a
script). It must never import from the ``veritas`` package.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field, asdict
from difflib import SequenceMatcher
from typing import Any, Callable, Dict, List, Literal, Optional

STATUS_VERIFIED = "verified"
STATUS_METADATA_MISMATCH = "metadata_mismatch"
STATUS_UNRESOLVED = "unresolved"

# The three verdicts the deterministic resolver can emit. (The citation-check
# agent later adds escalation outcomes; those are not produced here.)
ResolverStatus = Literal["verified", "metadata_mismatch", "unresolved"]


@dataclass
class Reference:
    """One parsed citation from the paper's reference list."""
    raw: str = ""
    key: str = ""
    title: str = ""
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    venue: str = ""
    doi: str = ""
    arxiv_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Reference":
        year = d.get("year")
        try:
            year = int(year) if year not in (None, "") else None
        except (TypeError, ValueError):
            year = None
        return cls(
            raw=str(d.get("raw", "") or ""),
            key=str(d.get("key", "") or ""),
            title=str(d.get("title", "") or ""),
            authors=[str(a) for a in (d.get("authors") or []) if str(a).strip()],
            year=year,
            venue=str(d.get("venue", "") or ""),
            doi=str(d.get("doi", "") or ""),
            arxiv_id=str(d.get("arxiv_id", "") or ""),
        )


@dataclass
class SourceRecord:
    """A candidate record returned by one metadata source."""
    source: str
    title: str = ""
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    venue: str = ""
    doi: str = ""
    arxiv_id: str = ""
    url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CitationVerdict:
    """Verdict for one reference: resolved (with the matched record) or unresolved."""
    key: str
    title: str
    status: ResolverStatus
    matched_record: Optional[SourceRecord] = None
    mismatches: List[str] = field(default_factory=list)
    sources_queried: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        # Hand-rolled rather than asdict() because nested matched_record needs its own to_dict()/None handling.
        return {
            "key": self.key,
            "title": self.title,
            "status": self.status,
            "matched_record": self.matched_record.to_dict() if self.matched_record else None,
            "mismatches": list(self.mismatches),
            "sources_queried": list(self.sources_queried),
        }


def parse_references(raw: str) -> List[Reference]:
    """Parse the agent-produced references JSON (a list) into Reference objects.

    Tolerant: skips non-dict entries and entries with neither ``raw`` nor
    ``title`` content. Preserves order.
    """
    try:
        data = json.loads(raw)
    except (ValueError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    out: List[Reference] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        raw_text = str(item.get("raw", "") or "").strip()
        title_text = str(item.get("title", "") or "").strip()
        if not (raw_text or title_text):
            continue
        out.append(Reference.from_dict(item))
    return out


# ---------------------------------------------------------------------------
# Title / author normalization and matching helpers
# ---------------------------------------------------------------------------

_PUNCT_RE = re.compile(r"[^\w\s]", flags=re.UNICODE)
_WS_RE = re.compile(r"\s+")
_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})")


def normalize_title(title: str) -> str:
    """Lowercase, drop punctuation, collapse whitespace — for fuzzy matching."""
    if not title:
        return ""
    t = _PUNCT_RE.sub(" ", title.lower())
    return _WS_RE.sub(" ", t).strip()


def title_similarity(a: str, b: str) -> float:
    """Normalized-title similarity in [0, 1] (difflib ratio over normalized text)."""
    na, nb = normalize_title(a), normalize_title(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _last_name(author: str) -> str:
    """Best-effort surname: last whitespace-separated token, normalized."""
    parts = normalize_title(author).split()
    return parts[-1] if parts else ""


def author_overlap(cited: List[str], record: List[str]) -> float:
    """Fraction of cited authors whose surname appears in the record's authors.

    Returns 0.0 if either list is empty. Surname-based so initials vs full
    given names ("A. Vaswani" vs "Ashish Vaswani") still match.
    """
    cited_names = {_last_name(a) for a in cited if _last_name(a)}
    record_names = {_last_name(a) for a in record if _last_name(a)}
    if not cited_names or not record_names:
        return 0.0
    hits = sum(1 for n in cited_names if n in record_names)
    return hits / len(cited_names)


def normalize_arxiv_id(value: str) -> str:
    """Extract a bare arXiv id (no prefix, no version) from any arXiv string."""
    if not value:
        return ""
    m = _ARXIV_RE.search(value)
    return m.group(1) if m else ""
