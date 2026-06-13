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
_WS_RE = re.compile(r"\s+", flags=re.UNICODE)
_ARXIV_RE = re.compile(r"(\d{4}\.\d{4,5})(?:v\d+)?")


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
    cited_names = {n for a in cited if (n := _last_name(a))}
    record_names = {n for a in record if (n := _last_name(a))}
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


_DOI_PREFIXES = (
    "https://doi.org/", "http://doi.org/",
    "https://dx.doi.org/", "http://dx.doi.org/", "doi:",
)


def normalize_doi(value: str) -> str:
    """Normalize a DOI to bare lowercase form (strip URL / 'doi:' prefixes)."""
    if not value:
        return ""
    s = value.strip().lower()
    for prefix in _DOI_PREFIXES:
        if s.startswith(prefix):
            s = s[len(prefix):]
            break
    return s.strip()


# ---------------------------------------------------------------------------
# Record matching and verdict classification
# ---------------------------------------------------------------------------

# Thresholds adapted from refchecker's deterministic pre-filter.
TITLE_MATCH_THRESHOLD = 0.90   # normalized-title similarity to call it the same work
AUTHOR_OVERLAP_THRESHOLD = 0.60  # below this (with a title match) -> author mismatch


def best_match(ref: Reference, records: List[SourceRecord]) -> tuple[Optional[SourceRecord], float]:
    """Return the record with the highest title similarity to the reference."""
    best: Optional[SourceRecord] = None
    best_sim = 0.0
    for rec in records:
        sim = title_similarity(ref.title, rec.title)
        if sim > best_sim:
            best, best_sim = rec, sim
    return best, best_sim


def _venue_looks_like_preprint(venue: str) -> bool:
    """True if the venue string names a known preprint server.

    Covers arXiv, bioRxiv, medRxiv, SSRN, and the generic 'preprint' label.
    """
    v = (venue or "").lower()
    return any(k in v for k in ("arxiv", "biorxiv", "medrxiv", "ssrn", "preprint"))


def classify(
    ref: Reference,
    records: List[SourceRecord],
    *,
    sources_queried: List[str],
) -> CitationVerdict:
    """Classify one reference against the candidate records from all sources.

    - No record with title similarity >= TITLE_MATCH_THRESHOLD -> ``unresolved``.
    - A title match with an author/venue/identifier disagreement ->
      ``metadata_mismatch`` (the authoritative record is attached).
    - Otherwise -> ``verified``.

    The verdict NEVER rewrites the citation; ``metadata_mismatch`` records the
    specific disagreements so a human can decide. Publication-status drift
    (cited as a preprint, but the record shows a published venue) is one such
    mismatch.
    """
    rec, sim = best_match(ref, records)
    if rec is None or sim < TITLE_MATCH_THRESHOLD:
        return CitationVerdict(
            key=ref.key, title=ref.title, status=STATUS_UNRESOLVED,
            matched_record=None, mismatches=[], sources_queried=sources_queried,
        )

    mismatches: List[str] = []

    # Author disagreement (only when both sides list authors).
    if ref.authors and rec.authors:
        ov = author_overlap(ref.authors, rec.authors)
        if ov < AUTHOR_OVERLAP_THRESHOLD:
            mismatches.append(
                f"authors: only {ov:.0%} of the cited authors match the record "
                f"({rec.source})"
            )

    # Identifier conflict (DOI / arXiv id present on both sides but differ).
    ref_doi, rec_doi = normalize_doi(ref.doi), normalize_doi(rec.doi)
    if ref_doi and rec_doi and ref_doi != rec_doi:
        mismatches.append(f"doi: cited '{ref.doi}' but record has '{rec.doi}' ({rec.source})")
    ref_arxiv, rec_arxiv = normalize_arxiv_id(ref.arxiv_id), normalize_arxiv_id(rec.arxiv_id)
    if ref_arxiv and rec_arxiv and ref_arxiv != rec_arxiv:
        mismatches.append(
            f"identifier: cited arXiv '{ref.arxiv_id}' but record has "
            f"'{rec.arxiv_id}' ({rec.source})"
        )

    # Publication-status drift, one direction only: cited as a preprint but the
    # record shows a published venue. The reverse (cited as published, only a
    # preprint record found) is deliberately NOT flagged — our lookup coverage
    # is incomplete, so it would produce false positives.
    if _venue_looks_like_preprint(ref.venue) and rec.venue and not _venue_looks_like_preprint(rec.venue):
        mismatches.append(
            f"venue: cited as '{ref.venue}' but published at "
            f"'{rec.venue}'{f' {rec.year}' if rec.year else ''} per {rec.source}"
        )

    # Year disagreement (>1 year apart, when both present and positive).
    if (ref.year is not None and rec.year is not None
            and ref.year > 0 and rec.year > 0
            and abs(int(ref.year) - int(rec.year)) > 1):
        mismatches.append(f"year: cited {ref.year} but record says {rec.year} ({rec.source})")

    status = STATUS_METADATA_MISMATCH if mismatches else STATUS_VERIFIED
    return CitationVerdict(
        key=ref.key, title=ref.title, status=status,
        matched_record=rec, mismatches=mismatches, sources_queried=sources_queried,
    )
