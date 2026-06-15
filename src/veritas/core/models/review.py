"""Read-mode (``--depth read``) assessment dataclasses.

Read mode never executes code, so there is no produced value to grade against
the paper. Instead the static-review agent judges, by *reading* the paper and
(when supplied) the code/data, how reproducible each claim is and how well the
paper as a whole is specified. These dataclasses are the read-mode analogue of
``ClaimVerdict`` / ``ReplicationScore`` in the run-mode pipeline.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Literal, Optional

# How well a single claim is supported by what can be *read* (the described
# methodology and, when present, the provided code/data) — NOT by an execution.
SupportLevel = Literal[
    "supported",       # code/method present and complete enough to produce it
    "partial",         # partially present; gaps that threaten reproduction
    "unsupported",     # no code/method found that would produce it
    "not_assessable",  # cannot tell from what was provided
]

# Per-claim and overall reproducibility risk, read off the same reading.
RiskLevel = Literal["low", "medium", "high"]

# Coarse qualitative buckets for the aggregate axes shown in the headline card.
QualityBucket = Literal["good", "partial", "poor", "unknown"]


@dataclass
class ClaimAssessment:
    """The static-review agent's reading-based judgment of one claim.

    No value is produced or graded. ``anchor_quote`` is a verbatim snippet from
    the paper (defaulting to the claim's provenance quote) so the assessment can
    be surfaced as an in-line comment anchored at the claim's location.
    """
    claim_id: str
    support_level: SupportLevel
    reproducibility_risk: RiskLevel = "medium"
    rationale: str = ""
    # Where in the provided code the claim would be computed, if found
    # (e.g. ``analysis/fit.py:compute_effect``). None when no code was provided
    # or none was located.
    code_location: Optional[str] = None
    # Whether the data needed to produce the claim is shipped or clearly
    # fetchable. None when not applicable / undeterminable.
    data_available: Optional[bool] = None
    # Concrete reproducibility concerns for this claim (missing seed, undefined
    # hyperparameter, data not provided, method/code mismatch, ...).
    issues: List[str] = field(default_factory=list)
    # File/section pointers the reviewer relied on.
    evidence_refs: List[str] = field(default_factory=list)
    # Verbatim paper snippet to anchor an in-line comment to.
    anchor_quote: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "claim_id": self.claim_id,
            "support_level": self.support_level,
            "reproducibility_risk": self.reproducibility_risk,
            "rationale": self.rationale,
            "issues": self.issues,
            "evidence_refs": self.evidence_refs,
        }
        if self.code_location is not None:
            d["code_location"] = self.code_location
        if self.data_available is not None:
            d["data_available"] = self.data_available
        if self.anchor_quote is not None:
            d["anchor_quote"] = self.anchor_quote
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ClaimAssessment":
        return cls(
            claim_id=str(data["claim_id"]),
            support_level=data.get("support_level", "not_assessable"),
            reproducibility_risk=data.get("reproducibility_risk", "medium"),
            rationale=data.get("rationale", ""),
            code_location=data.get("code_location"),
            data_available=data.get("data_available"),
            issues=list(data.get("issues", []) or []),
            evidence_refs=list(data.get("evidence_refs", []) or []),
            anchor_quote=data.get("anchor_quote"),
        )


@dataclass
class ReproducibilityAssessment:
    """Aggregate read-mode verdict over all claim assessments.

    The headline card renders the three qualitative axes plus an overall risk;
    the body uses ``summary`` / ``strengths`` / ``weaknesses`` /
    ``recommendation`` and the per-claim breakdown counts.
    """
    overall_risk: RiskLevel = "medium"
    specification: QualityBucket = "unknown"      # is the method specified enough?
    code_coverage: QualityBucket = "unknown"      # do the artifacts cover the claims?
    data_availability: QualityBucket = "unknown"  # is the data present/fetchable?
    summary: str = ""
    strengths: List[str] = field(default_factory=list)
    weaknesses: List[str] = field(default_factory=list)
    recommendation: str = ""
    # Count of claim assessments by support level, e.g. {"supported": 3, ...}.
    support_breakdown: Dict[str, int] = field(default_factory=dict)
    total_claims: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "overall_risk": self.overall_risk,
            "specification": self.specification,
            "code_coverage": self.code_coverage,
            "data_availability": self.data_availability,
            "summary": self.summary,
            "strengths": self.strengths,
            "weaknesses": self.weaknesses,
            "recommendation": self.recommendation,
            "support_breakdown": self.support_breakdown,
            "total_claims": self.total_claims,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReproducibilityAssessment":
        return cls(
            overall_risk=data.get("overall_risk", "medium"),
            specification=data.get("specification", "unknown"),
            code_coverage=data.get("code_coverage", "unknown"),
            data_availability=data.get("data_availability", "unknown"),
            summary=data.get("summary", ""),
            strengths=list(data.get("strengths", []) or []),
            weaknesses=list(data.get("weaknesses", []) or []),
            recommendation=data.get("recommendation", ""),
            support_breakdown=dict(data.get("support_breakdown", {}) or {}),
            total_claims=int(data.get("total_claims", 0)),
        )


# Mapping from support level to the count-summary order / display label.
SUPPORT_LEVELS: List[str] = ["supported", "partial", "unsupported", "not_assessable"]


def summarize_support(assessments: List[ClaimAssessment]) -> Dict[str, int]:
    """Tally claim assessments by support level (deterministic, code-computed)."""
    counts = {level: 0 for level in SUPPORT_LEVELS}
    for a in assessments:
        counts[a.support_level] = counts.get(a.support_level, 0) + 1
    return counts
