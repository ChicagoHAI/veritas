"""Manager research sub-agents.

Research runs as narrow sub-agents the manager invokes, following the grounding
pattern established by prior research agents: propose a query, retrieve
deterministically, then use only the validated returned items, with citations
mandatory.

When the manager's verdict carries ``research_requests``, the runner dispatches a
narrow research sub-agent per honored request:

  * **resource-finder** — locate a missing dataset / script / dependency: a
    download script, a URL in the README/paper, a mirror, an install recipe.
  * **literature-finder** — locate a methodological detail or a standard
    implementation the paper underspecifies.

These are SEPARATE provider invocations (own prompt templates, web-search/fetch
access) from the manager and the replicate agent. They return findings +
provenance (source URLs), never raw reported-answer values.

THREE structural anti-leakage barriers, all enforced here / by the runner:

  a. **Intent allow-list** (:func:`honor_request`) — a request is honored only if
     its structured ``kind`` is ``resource`` / ``literature``. This gate is a
     small *structured* check on the request kind, NOT keyword matching on free
     text. An answer-seeking request carries no valid ``kind`` and is rejected.
  b. **Answer-value redaction BEFORE injection** — a two-layer redactor sits
     between the searcher and the replicate agent:
       * an **LLM/agent judgment** (run by the runner) that reads the finding and
         removes reported result / metric values, keeping methodology + resource
         info — this is the PRIMARY redactor;
       * a deterministic **exact-string** scrub of *known* ``paper_value`` strings
         (:func:`redact_known_values`) as belt-and-suspenders on top. This is the
         ONLY deterministic redaction allowed: an objective "does the finding
         contain this literal known answer" check — never a keyword bank guessing
         at what an answer "looks like".
  c. **Provenance-tagged injection + cheating monitor** — injected guidance is
     tagged with its source URLs (:func:`format_findings_for_guidance`); the
     existing post-verify cheating monitor watches the re-run trace for copied
     values. Searcher and replicate agent stay separate roles with redaction
     between them.

This module holds the *deterministic* pieces (parsing, the intent gate, the
exact-known-value scrub, provenance formatting, bounds/config) so they are
pure-function unit-testable. The LLM passes (the two finder sub-agents and the
LLM redactor) are driven by the runner, which injects them — this module never
imports the provider machinery.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from veritas.core.config_env import _env_int

# --- Request vocabulary -----------------------------------------------------

KIND_RESOURCE = "resource"
KIND_LITERATURE = "literature"
VALID_KINDS = {KIND_RESOURCE, KIND_LITERATURE}

# Map each honored kind to the research sub-agent (template) that serves it.
KIND_TEMPLATES = {
    KIND_RESOURCE: "research/resource_finder.md",
    KIND_LITERATURE: "research/literature_finder.md",
}


@dataclass
class ResearchRequest:
    """One research request emitted by the manager (a ``research_requests`` item).

    ``kind`` is the structured intent tag the intent allow-list gates on
    (barrier a). ``need`` is the manager's free-text description of the missing
    methodology/resource — it is what the sub-agent searches for, but it never
    gates honoring (the gate is on ``kind`` only, a structured check, so we never
    keyword-match the free text to decide intent).
    """

    kind: str
    need: str
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "need": self.need, "rationale": self.rationale}


def parse_research_requests(raw: Any) -> List[ResearchRequest]:
    """Parse the verdict's ``research_requests`` list into typed requests.

    Tolerant: skips non-dict entries and entries with no usable ``need``. Does
    NOT filter by kind here — honoring is the intent gate's job
    (:func:`honor_request`), kept separate so the gate is the single auditable
    point that decides what is allowed.
    """
    if not isinstance(raw, list):
        return []
    out: List[ResearchRequest] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        need = str(item.get("need", "") or "").strip()
        if not need:
            continue
        kind = str(item.get("kind", "") or "").strip().lower()
        rationale = str(item.get("rationale", "") or "").strip()
        out.append(ResearchRequest(kind=kind, need=need, rationale=rationale))
    return out


# --- Barrier (a): intent allow-list -----------------------------------------


def honor_request(request: ResearchRequest) -> bool:
    """Intent allow-list: honor a request ONLY if its kind is in the allow-list.

    This is barrier (a). It is a deliberately small *structured* check — it reads
    the request's ``kind`` enum, nothing else. It does NOT inspect ``need`` for
    keywords to guess intent (that fragile pattern is banned). A request whose
    kind is not ``resource``/``literature`` — e.g. an answer-seeking "find the
    reported value of X" the manager mislabels — is simply not honored, because
    such a request cannot carry a valid resource/literature kind.
    """
    return request.kind in VALID_KINDS


def split_requests(
    requests: List[ResearchRequest],
) -> tuple[List[ResearchRequest], List[ResearchRequest]]:
    """Partition into (honored, rejected) by the intent allow-list."""
    honored = [r for r in requests if honor_request(r)]
    rejected = [r for r in requests if not honor_request(r)]
    return honored, rejected


# --- Barrier (b), deterministic layer: exact known-value scrub --------------


@dataclass
class RedactionResult:
    """Outcome of redacting a finding before injection.

    ``redacted_text`` is what may reach the replicate agent. ``llm_removed`` is
    whether the LLM redactor reported removing any reported-value content;
    ``exact_hits`` lists the known ``paper_value`` strings the deterministic scrub
    additionally removed (belt-and-suspenders). Both are recorded in the workflow
    log so the audit trail shows exactly what was stripped.
    """

    redacted_text: str
    llm_removed: bool = False
    exact_hits: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "redacted_text": self.redacted_text,
            "llm_removed": self.llm_removed,
            "exact_hits": list(self.exact_hits),
        }


def known_value_strings(paper_values: Any) -> List[str]:
    """Flatten arbitrary ``paper_value`` payloads into literal strings to scrub.

    Accepts the heterogeneous ``PaperClaim.paper_value`` shapes (scalar, range
    pair, list, dict/table) and yields the literal string forms that could be an
    answer leak. Pure structural flattening — it makes NO judgment about whether a
    value "looks like" an answer; every known value is a fact to scrub. Empty /
    too-short tokens (length < 2) are dropped to avoid scrubbing trivia like "0".
    """
    out: List[str] = []

    def _walk(v: Any) -> None:
        if v is None:
            return
        if isinstance(v, (list, tuple)):
            for x in v:
                _walk(x)
            return
        if isinstance(v, dict):
            for x in v.values():
                _walk(x)
            return
        s = str(v).strip()
        if len(s) >= 2:
            out.append(s)

    _walk(paper_values)
    # Deduplicate, preserve order.
    seen = set()
    uniq: List[str] = []
    for s in out:
        if s not in seen:
            seen.add(s)
            uniq.append(s)
    return uniq


REDACTION_MARKER = "[redacted: reported value]"


def redact_known_values(text: str, known_values: List[str]) -> RedactionResult:
    """Deterministically scrub literal occurrences of *known* paper values.

    Barrier (b)'s deterministic layer — the ONLY deterministic redaction allowed.
    It replaces exact, literal occurrences of each known ``paper_value`` string
    with :data:`REDACTION_MARKER`. This is an objective string-containment fact
    ("does the finding contain this known answer literally"), NOT a keyword bank
    guessing at what an answer looks like. Case-sensitive and exact: we only ever
    remove a string we *know* to be a reported value.

    Returns the scrubbed text plus the list of known values that were hit. The
    LLM redactor (the primary, semantic layer) runs separately in the runner and
    its result is recorded on the :class:`RedactionResult` it builds.
    """
    if not text:
        return RedactionResult(redacted_text=text or "")
    redacted = text
    hits: List[str] = []
    # Longest-first so a value that is a substring of another is handled after
    # the longer one (avoids partial-overlap leaving fragments).
    for val in sorted(known_values, key=len, reverse=True):
        if val and val in redacted:
            redacted = redacted.replace(val, REDACTION_MARKER)
            hits.append(val)
    return RedactionResult(redacted_text=redacted, exact_hits=hits)


# --- Findings + barrier (c): provenance-tagged injection --------------------


@dataclass
class ResearchFinding:
    """A single research sub-agent's result (post-redaction).

    ``kind`` / ``need`` echo the originating request; ``finding`` is the
    redacted methodology/resource text; ``sources`` is the provenance (source
    URLs/citations) the sub-agent must attach. ``redaction`` records what the
    two-layer redactor did. This is the unit folded into the re-run guidance and
    logged to the workflow trajectory.
    """

    kind: str
    need: str
    finding: str
    sources: List[str] = field(default_factory=list)
    redaction: Optional[RedactionResult] = None
    error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "kind": self.kind,
            "need": self.need,
            "finding": self.finding,
            "sources": list(self.sources),
        }
        if self.redaction is not None:
            d["redaction"] = self.redaction.to_dict()
        if self.error:
            d["error"] = self.error
        return d


def format_findings_for_guidance(findings: List[ResearchFinding]) -> str:
    """Render honored, non-empty findings into a provenance-tagged guidance block.

    Barrier (c)'s injection side: every methodology/resource detail is rendered
    WITH its source URLs so the audit trail proves it is methodology, not an
    answer. Provenance is **mandatory** — a finding with no source is discarded
    (it cannot be audited as methodology vs. an unattributed value), matching the
    finder prompts' promise. Returns "" when there is nothing usable to inject (so
    the caller can skip folding entirely). The text is the post-redaction
    ``finding`` only — raw reported values never reach this string.
    """
    usable = [
        f for f in findings
        if f.finding.strip() and not f.error and any(s.strip() for s in f.sources)
    ]
    if not usable:
        return ""
    blocks: List[str] = [
        "Methodology/resource research was performed for this re-run. The "
        "following findings come from external sources (NOT the paper's "
        "reported results, which were redacted). Use the methodology/resources; "
        "every item is tagged with its provenance:",
    ]
    for i, f in enumerate(usable, 1):
        tag = "resource" if f.kind == KIND_RESOURCE else "literature"
        srcs = "; ".join(s for s in f.sources if s.strip())
        blocks.append(
            f"{i}. [{tag}] need: {f.need}\n"
            f"   finding: {f.finding.strip()}\n"
            f"   source(s): {srcs}"
        )
    return "\n".join(blocks)


# --- Bounds + config (env-overridable) --------------------------------------


@dataclass
class ResearchConfig:
    """Bounds for the manager's research sub-agents (env-overridable).

    All defaults are conservative and overridable via ``VERITAS_*`` so research
    is itself bounded (no unbounded fan-out) and no config is hardcoded. Resolved
    at construction from the environment, mirroring the env-var config pattern
    used elsewhere in veritas.
    """

    max_calls_per_iteration: int = 0

    @classmethod
    def from_env(cls) -> "ResearchConfig":
        return cls(
            # Cap on research sub-agent invocations per manager iteration.
            # Default 2 (one resource + one literature). Set 0 to disable research
            # even when the loop is on.
            max_calls_per_iteration=max(
                0, _env_int("VERITAS_RESEARCH_MAX_CALLS", 2)
            ),
        )
