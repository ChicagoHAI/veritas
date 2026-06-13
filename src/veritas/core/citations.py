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
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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


# Source preference for choosing which same-work record to compare against when
# several sources return the same paper. Lower number = more preferred for the
# fields that matter most here (venue/publication status).
_SOURCE_PRIORITY = {"dblp": 0, "acl": 1, "crossref": 2, "openalex": 3, "s2": 4, "arxiv": 5}


def _venue_looks_like_preprint(venue: str) -> bool:
    """True if the venue string names a known preprint server.

    Covers arXiv, bioRxiv, medRxiv, SSRN, and the generic 'preprint' label.
    """
    v = (venue or "").lower()
    return any(k in v for k in ("arxiv", "biorxiv", "medrxiv", "ssrn", "preprint"))


def _preferred_record(candidates: List[SourceRecord]) -> SourceRecord:
    """Pick the richest record among same-work candidates to compare against.

    Prefers a record with a real (non-preprint) venue, then a more authoritative
    source, then one that carries a year, then more authors. This keeps a
    metadata-poor record (e.g. an empty-venue hit) from hiding a
    published-vs-preprint discrepancy that a richer source would reveal.
    """
    def _key(r: SourceRecord):
        has_real_venue = bool(r.venue and not _venue_looks_like_preprint(r.venue))
        source_rank = -_SOURCE_PRIORITY.get(r.source, 99)
        return (has_real_venue, source_rank, bool(r.year), len(r.authors))

    return max(candidates, key=_key)


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
    candidates = [
        r for r in records
        if title_similarity(ref.title, r.title) >= TITLE_MATCH_THRESHOLD
    ]
    if not candidates:
        return CitationVerdict(
            key=ref.key, title=ref.title, status=STATUS_UNRESOLVED,
            matched_record=None, mismatches=[], sources_queried=sources_queried,
        )
    rec = _preferred_record(candidates)

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


# ---------------------------------------------------------------------------
# Source-specific API response adapters
# ---------------------------------------------------------------------------

def _first(seq: Any, default: str = "") -> str:
    """Return the first element of a list, or default if the list is empty/non-list."""
    return seq[0] if isinstance(seq, list) and seq else default


def parse_crossref(payload: Dict[str, Any]) -> List[SourceRecord]:
    """Parse a Crossref works/query JSON payload into SourceRecords."""
    out: List[SourceRecord] = []
    for item in (payload.get("message", {}) or {}).get("items", []) or []:
        authors = [
            (a.get("name") or f"{a.get('given', '')} {a.get('family', '')}".strip())
            for a in item.get("author", []) or []
        ]
        year = None
        parts = ((item.get("issued") or {}).get("date-parts") or [])
        if parts and parts[0]:
            try:
                year = int(parts[0][0])
            except (TypeError, ValueError, IndexError):
                year = None
        out.append(SourceRecord(
            source="crossref",
            title=_first(item.get("title", [])),
            authors=[a for a in authors if a],
            year=year,
            venue=_first(item.get("container-title", [])),
            doi=str(item.get("DOI", "") or ""),
            url=str(item.get("URL", "") or ""),
        ))
    return out


def parse_openalex(payload: Dict[str, Any]) -> List[SourceRecord]:
    """Parse an OpenAlex works search JSON payload into SourceRecords."""
    out: List[SourceRecord] = []
    for item in payload.get("results", []) or []:
        authors = [
            ((a.get("author") or {}).get("display_name") or "")
            for a in item.get("authorships", []) or []
        ]
        venue = (((item.get("primary_location") or {}).get("source") or {}).get("display_name") or "")
        doi = normalize_doi(item.get("doi", "") or "")
        out.append(SourceRecord(
            source="openalex",
            title=str(item.get("title", "") or ""),
            authors=[a for a in authors if a],
            year=item.get("publication_year"),
            venue=venue,
            doi=doi,
            url=str(item.get("id", "") or ""),
        ))
    return out


def parse_semantic_scholar(payload: Dict[str, Any]) -> List[SourceRecord]:
    """Parse a Semantic Scholar paper search JSON payload into SourceRecords."""
    out: List[SourceRecord] = []
    for item in payload.get("data", []) or []:
        ext = item.get("externalIds") or {}
        out.append(SourceRecord(
            source="s2",
            title=str(item.get("title", "") or ""),
            authors=[a.get("name", "") for a in item.get("authors", []) or [] if a.get("name")],
            year=item.get("year"),
            venue=str(item.get("venue", "") or ""),
            doi=str(ext.get("DOI", "") or ""),
            arxiv_id=str(ext.get("ArXiv", "") or ""),
            url=str(item.get("url", "") or ""),
        ))
    return out


def parse_dblp(payload: Dict[str, Any]) -> List[SourceRecord]:
    """Parse a DBLP search JSON payload into SourceRecords."""
    out: List[SourceRecord] = []
    hits = (((payload.get("result") or {}).get("hits") or {}).get("hit")) or []
    for hit in hits:
        info = hit.get("info", {}) or {}
        author_field = (info.get("authors") or {}).get("author") or []
        if isinstance(author_field, dict):
            author_field = [author_field]
        authors = [a.get("text", "") for a in author_field if a.get("text")]
        year = None
        try:
            year = int(info.get("year")) if info.get("year") else None
        except (TypeError, ValueError):
            year = None
        venue_field = info.get("venue", "")
        venue = str(_first(venue_field) or "") if isinstance(venue_field, list) else str(venue_field or "")
        out.append(SourceRecord(
            source="dblp",
            title=str(info.get("title", "") or "").rstrip("."),
            authors=authors,
            year=year,
            venue=venue,
            doi=str(info.get("doi", "") or ""),
            url=str(info.get("url", "") or ""),
        ))
    return out


def parse_arxiv_atom(atom_xml: str) -> List[SourceRecord]:
    """Parse an arXiv Atom feed XML string into SourceRecords."""
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out: List[SourceRecord] = []
    try:
        root = ET.fromstring(atom_xml)
    except ET.ParseError:
        return out
    for entry in root.findall("a:entry", ns):
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        authors = [
            (n.text or "").strip()
            for n in entry.findall("a:author/a:name", ns)
        ]
        published = entry.findtext("a:published", default="", namespaces=ns) or ""
        year = None
        if len(published) >= 4 and published[:4].isdigit():
            year = int(published[:4])
        id_url = entry.findtext("a:id", default="", namespaces=ns) or ""
        out.append(SourceRecord(
            source="arxiv",
            title=title,
            authors=[a for a in authors if a],
            year=year,
            venue="arXiv",
            arxiv_id=normalize_arxiv_id(id_url),
            url=id_url,
        ))
    return out


# ---------------------------------------------------------------------------
# HTTP fetchers
# ---------------------------------------------------------------------------

_DEFAULT_UA = "veritas-citation-check (https://github.com/ChicagoHAI/veritas)"
_TIMEOUT_S = 15


def _user_agent() -> str:
    email = os.environ.get("VERITAS_CONTACT_EMAIL", "").strip()
    return f"{_DEFAULT_UA}; mailto:{email}" if email else _DEFAULT_UA


def fetch_json(url: str) -> Optional[Dict[str, Any]]:
    """GET a URL and parse JSON. Returns None on any network/parse error."""
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent(), "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError, OSError):
        return None


def fetch_text(url: str) -> Optional[str]:
    """GET a URL and return text. Returns None on any network error."""
    req = urllib.request.Request(url, headers={"User-Agent": _user_agent()})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT_S) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None


# ---------------------------------------------------------------------------
# Multi-source lookup
# ---------------------------------------------------------------------------

def lookup_reference(ref: Reference) -> tuple[List[SourceRecord], List[str]]:
    """Query all sources for one reference; return (records, sources_queried).

    Title-based queries across Crossref, OpenAlex, Semantic Scholar, DBLP, plus
    an arXiv query when the citation carries an arXiv id. Keyless; failures in
    any one source are swallowed so a single outage never sinks the check.
    A source that errors or rate-limits (HTTP 429) yields no records (the fetch
    returns None), so the reference simply degrades toward 'unresolved' rather
    than failing the run.
    """
    title_q = urllib.parse.quote(ref.title, safe="") if ref.title else ""
    records: List[SourceRecord] = []
    queried: List[str] = []

    if title_q:
        queried.append("crossref")
        cr = fetch_json(f"https://api.crossref.org/works?query.bibliographic={title_q}&rows=3")
        if cr:
            records.extend(parse_crossref(cr))

        queried.append("openalex")
        oa = fetch_json(f"https://api.openalex.org/works?search={title_q}&per_page=3")
        if oa:
            records.extend(parse_openalex(oa))

        queried.append("s2")
        fields = "title,year,authors,venue,externalIds,url"
        s2 = fetch_json(
            f"https://api.semanticscholar.org/graph/v1/paper/search?query={title_q}&limit=3&fields={fields}"
        )
        if s2:
            records.extend(parse_semantic_scholar(s2))

        queried.append("dblp")
        dblp = fetch_json(f"https://dblp.org/search/publ/api?q={title_q}&format=json&h=3")
        if dblp:
            records.extend(parse_dblp(dblp))

    if ref.arxiv_id:
        queried.append("arxiv")
        atom = fetch_text(
            f"https://export.arxiv.org/api/query?id_list={urllib.parse.quote(normalize_arxiv_id(ref.arxiv_id), safe='')}"
        )
        if atom:
            records.extend(parse_arxiv_atom(atom))

    return records, queried


# ---------------------------------------------------------------------------
# Orchestrator and summary
# ---------------------------------------------------------------------------

def build_summary(verdicts: List[CitationVerdict]) -> Dict[str, int]:
    """Count verdicts per status (plus total). Unknown statuses are ignored."""
    summary = {"total": len(verdicts), STATUS_VERIFIED: 0, STATUS_METADATA_MISMATCH: 0, STATUS_UNRESOLVED: 0}
    for v in verdicts:
        if v.status in summary:
            summary[v.status] += 1
    return summary


def resolve_references(
    refs: List[Reference],
    *,
    lookup: Callable[[Reference], tuple[List[SourceRecord], List[str]]] = lookup_reference,
) -> Dict[str, Any]:
    """Resolve every reference and return the serializable result dict.

    ``lookup`` is injectable so tests pass a fake (no network). Production uses
    :func:`lookup_reference`.
    """
    verdicts: List[CitationVerdict] = []
    for ref in refs:
        records, queried = lookup(ref)
        verdicts.append(classify(ref, records, sources_queried=queried))
    return {
        "verdicts": [v.to_dict() for v in verdicts],
        "summary": build_summary(verdicts),
    }


# ---------------------------------------------------------------------------
# Standalone CLI entrypoint
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    """CLI: read references JSON, write resolver verdicts JSON.

    Usage: python resolve_references.py <references.json> <out.json>
    The citation-check subagent invokes this; its output is authoritative for
    the verified/metadata_mismatch/unresolved verdicts.
    """
    argv = argv if argv is not None else sys.argv[1:]
    if len(argv) != 2:
        print("usage: resolve_references.py <references.json> <out.json>", file=sys.stderr)
        return 2
    in_path, out_path = argv
    try:
        with open(in_path, encoding="utf-8") as f:
            raw = f.read()
    except OSError as exc:
        print(f"error: cannot read {in_path}: {exc}", file=sys.stderr)
        return 1
    refs = parse_references(raw)
    result = resolve_references(refs)
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(result, f, indent=2)
    except OSError as exc:
        print(f"error: cannot write {out_path}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result["summary"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
