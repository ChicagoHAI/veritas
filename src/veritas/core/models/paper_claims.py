"""Paper-claim, verdict, and score dataclasses for the verification pipeline."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

from veritas.core.config_env import _env_float


ClaimType = Literal["scalar", "scalar_range", "table", "qualitative", "figure"]
ClaimTier = Literal["headline", "supporting"]
VerdictStatus = Literal[
    "match", "partial", "no_match", "not_attempted", "not_applicable"
]

# Tier weights for the Replication Score formula. Headline claims weigh most.
# Each weight is overridable via a ``VERITAS_TIER_WEIGHT_*`` env var (read from
# ``.env``, see ``config_env`` / ``.env.example``). Defaults are unchanged when
# unset.
TIER_WEIGHTS: Dict[str, float] = {
    "headline": _env_float("VERITAS_TIER_WEIGHT_HEADLINE", 3.0),
    "supporting": _env_float("VERITAS_TIER_WEIGHT_SUPPORTING", 2.0),
}

# Verdict-to-score mapping. ``not_applicable`` is excluded from the score
# (handled in ``verify.compute_replication_score``); the entry below is
# present so callers can iterate the enum but it is never consumed.
VERDICT_VALUES: Dict[str, float] = {
    "match": 1.0,
    "partial": 0.5,
    "no_match": 0.0,
    "not_attempted": 0.0,
    "not_applicable": 0.0,
}

# When the grader returns ``not_attempted`` it must say *why* it could not grade,
# so scoring can tell a real failure apart from a run/tooling limitation. The
# reason is set by the evidence-reading grader agent (see
# ``templates/verify/single_claim.md``) and must be backed by cited evidence.
#
#   authors_missing — the released artifact needed to check this claim was never
#                     shipped by the authors (no code/data/checkpoint in the
#                     codebase). A genuine reproducibility failure → SCORED 0,
#                     kept in the denominator.
#   blocked_infra   — the code exists but this run could not produce the evidence
#                     for environment reasons (OOM, timeout, missing GPU/hardware,
#                     the step was not executed). Not the paper's fault →
#                     EXCLUDED from the denominator, like ``not_applicable``.
#   no_evidence     — evidence is genuinely indeterminate despite the code being
#                     present and run (rare residual). Conservative: EXCLUDED from
#                     the denominator so a tooling gap never penalizes the paper.
NOT_ATTEMPTED_REASONS = frozenset({"authors_missing", "blocked_infra", "no_evidence"})
# Reasons that remove a ``not_attempted`` claim from the score denominator
# (our-side / indeterminate). ``authors_missing`` is NOT here — it counts as 0.
SCORE_EXCLUDED_REASONS = frozenset({"blocked_infra", "no_evidence"})


@dataclass
class Provenance:
    """Where a claim was found in the paper."""
    section: str
    page: int
    quote: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"section": self.section, "page": self.page}
        if self.quote is not None:
            d["quote"] = self.quote
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Provenance":
        return cls(
            section=data.get("section", ""),
            page=int(data.get("page", 0)),
            quote=data.get("quote"),
        )


@dataclass
class PaperClaim:
    """A single structured claim extracted from a paper."""
    id: str
    description: str
    type: ClaimType
    tier: ClaimTier = "supporting"
    paper_value: Any = None
    units: Optional[str] = None
    expected_output_file: Optional[str] = None
    provenance: Optional[Provenance] = None
    verification: str = ""
    notes: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "description": self.description,
            "type": self.type,
            "tier": self.tier,
            "verification": self.verification,
        }
        if self.paper_value is not None:
            d["paper_value"] = self.paper_value
        if self.units is not None:
            d["units"] = self.units
        if self.expected_output_file is not None:
            d["expected_output_file"] = self.expected_output_file
        if self.provenance is not None:
            d["provenance"] = self.provenance.to_dict()
        if self.notes is not None:
            d["notes"] = self.notes
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaperClaim":
        prov_data = data.get("provenance")
        return cls(
            id=str(data["id"]),
            description=data["description"],
            type=data["type"],
            # Normalized: tier now decides whether a claim is in scope (the
            # main-scope guard matches "headline" exactly), so case/whitespace
            # variants from the extractor or a hand-authored file must not
            # silently miss the match.
            tier=str(data.get("tier", "supporting")).strip().lower(),
            paper_value=data.get("paper_value"),
            units=data.get("units"),
            expected_output_file=data.get("expected_output_file"),
            provenance=Provenance.from_dict(prov_data) if prov_data else None,
            verification=data.get("verification", ""),
            notes=data.get("notes"),
        )


@dataclass
class PaperClaims:
    """The set of claims extracted from a paper plus light metadata."""
    paper: Dict[str, Any] = field(default_factory=dict)
    claims: List[PaperClaim] = field(default_factory=list)
    # Which claim scope produced this set: "main" | "full" | a numeric string
    # ("1", "2", ...) | "user" (user-supplied --claims file). None on files
    # written before claim scope existed.
    scope: Optional[str] = None

    def claim_ids(self) -> "set[str]":
        return {c.id for c in self.claims}

    def get_claim(self, claim_id: str) -> Optional[PaperClaim]:
        for c in self.claims:
            if c.id == claim_id:
                return c
        return None

    def by_tier(self, tier: str) -> List[PaperClaim]:
        return [c for c in self.claims if c.tier == tier]

    def by_type(self, claim_type: str) -> List[PaperClaim]:
        return [c for c in self.claims if c.type == claim_type]

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "paper": self.paper,
            "claims": [c.to_dict() for c in self.claims],
        }
        if self.scope is not None:
            d["scope"] = self.scope
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaperClaims":
        return cls(
            paper=data.get("paper", {}),
            claims=[PaperClaim.from_dict(c) for c in data.get("claims", [])],
            scope=data.get("scope"),
        )


@dataclass
class ClaimVerdict:
    """The verifier's adjudication of one claim against replication evidence."""
    claim_id: str
    status: VerdictStatus
    structured: Dict[str, Any] = field(default_factory=dict)
    rationale: str = ""
    evidence_refs: List[str] = field(default_factory=list)
    n_a_reason: Optional[str] = None  # populated only when status == "not_applicable"
    # Why the grader could not grade — populated only when status ==
    # "not_attempted"; one of ``NOT_ATTEMPTED_REASONS``. Drives whether the claim
    # counts against the paper or is excluded from the denominator (see
    # ``SCORE_EXCLUDED_REASONS`` and ``verify.compute_replication_score``).
    not_attempted_reason: Optional[str] = None
    # How the status was decided. "agent": the evidence-reading grader agent's own
    # adjudication (the current path for every claim type). "llm"/"deterministic"
    # appear only on verdicts produced by older runs.
    graded_by: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "claim_id": self.claim_id,
            "status": self.status,
            "structured": self.structured,
            "rationale": self.rationale,
            "evidence_refs": self.evidence_refs,
        }
        if self.n_a_reason is not None:
            d["n_a_reason"] = self.n_a_reason
        if self.not_attempted_reason is not None:
            d["not_attempted_reason"] = self.not_attempted_reason
        if self.graded_by is not None:
            d["graded_by"] = self.graded_by
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClaimVerdict":
        return cls(
            claim_id=data["claim_id"],
            status=data["status"],
            structured=data.get("structured", {}),
            rationale=data.get("rationale", ""),
            evidence_refs=data.get("evidence_refs", []),
            n_a_reason=data.get("n_a_reason"),
            not_attempted_reason=data.get("not_attempted_reason"),
            graded_by=data.get("graded_by"),
        )


@dataclass
class ReplicationScore:
    """Aggregate score over all claim verdicts, with tier breakdown and flags."""
    score: Optional[float]  # None when no verifiable (non-n/a) claims exist
    headline: Dict[str, int] = field(default_factory=dict)
    supporting: Dict[str, int] = field(default_factory=dict)
    total_claims: int = 0
    counted_claims: int = 0  # excludes ``not_applicable`` from denominator
    missing_verdicts: List[str] = field(default_factory=list)
    flags: List[str] = field(default_factory=list)
    # Claim scope of the graded set, copied from ``PaperClaims.scope`` — scores
    # produced under different scopes are not comparable, so the score artifact
    # must say which scope produced it. None on scores from before claim scope.
    scope: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "score": self.score,
            "headline": self.headline,
            "supporting": self.supporting,
            "total_claims": self.total_claims,
            "counted_claims": self.counted_claims,
            "missing_verdicts": self.missing_verdicts,
            "flags": self.flags,
        }
        if self.scope is not None:
            d["scope"] = self.scope
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReplicationScore":
        return cls(
            score=data.get("score"),
            headline=data.get("headline", {}),
            supporting=data.get("supporting", {}),
            total_claims=data.get("total_claims", 0),
            counted_claims=data.get("counted_claims", 0),
            missing_verdicts=data.get("missing_verdicts", []),
            flags=data.get("flags", []),
            scope=data.get("scope"),
        )
