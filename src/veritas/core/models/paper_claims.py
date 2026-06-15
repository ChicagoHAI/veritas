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
            tier=data.get("tier", "supporting"),
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
        return {
            "paper": self.paper,
            "claims": [c.to_dict() for c in self.claims],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "PaperClaims":
        return cls(
            paper=data.get("paper", {}),
            claims=[PaperClaim.from_dict(c) for c in data.get("claims", [])],
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
    # How the status was decided: "deterministic" (graded by core.grading from
    # the comparator's extracted value) or "llm" (the comparator's own judgment,
    # used for qualitative/figure claims and non-gradable table shapes).
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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": self.score,
            "headline": self.headline,
            "supporting": self.supporting,
            "total_claims": self.total_claims,
            "counted_claims": self.counted_claims,
            "missing_verdicts": self.missing_verdicts,
            "flags": self.flags,
        }

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
        )
