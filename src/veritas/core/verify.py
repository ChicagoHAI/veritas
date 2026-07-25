"""Replication Score computation from per-claim verdicts.

Pure functions — no I/O. The runner reads verdict files from disk and passes
them to ``compute_replication_score``; the score is then written to
``replication_score.json`` by the runner.
"""

from typing import Dict, List, Optional

from veritas.core.config_env import _env_int
from veritas.core.models.paper_claims import (
    ClaimVerdict,
    PaperClaim,
    PaperClaims,
    ReplicationScore,
    SCORE_EXCLUDED_REASONS,
    TIER_WEIGHTS,
    VERDICT_VALUES,
)

# Minimum claims counted in the denominator for the score to be treated as
# reliable. Below this the score is still computed but flagged low-confidence
# (matches the handoff's "3+ judgeable claims" convention). Env-overridable.
MIN_JUDGEABLE_CLAIMS = _env_int("VERITAS_MIN_JUDGEABLE_CLAIMS", 3)


def _tier_breakdown(
    claims: List[PaperClaim],
    verdict_by_id: Dict[str, ClaimVerdict],
    tier: str,
) -> Dict[str, int]:
    """Count verdict statuses for claims of a given tier.

    Returned dict always contains every status key with an int count. Claims
    in this tier that have no verdict are counted under the synthetic
    ``"missing"`` key so the report can call them out.
    """
    counts: Dict[str, int] = {
        "match": 0,
        "partial": 0,
        "no_match": 0,
        "not_attempted": 0,
        "not_applicable": 0,
        "missing": 0,
    }
    for c in claims:
        if c.tier != tier:
            continue
        v = verdict_by_id.get(c.id)
        if v is None:
            counts["missing"] += 1
        else:
            counts[v.status] = counts.get(v.status, 0) + 1
    return counts


def compute_replication_score(
    claims: PaperClaims,
    verdicts: List[ClaimVerdict],
) -> ReplicationScore:
    """Compute the tier-weighted Replication Score.

    Formula::

        score = sum(tier_weight[c.tier] * verdict_value[v.status]) /
                sum(tier_weight[c.tier])

    where the sums exclude ``not_applicable`` claims and ``not_attempted``
    claims whose ``not_attempted_reason`` is in ``SCORE_EXCLUDED_REASONS``
    (``blocked_infra`` / ``no_evidence`` — run/tooling limitations, not charged
    against the paper). A ``not_attempted`` with reason ``authors_missing`` —
    or with no reason at all (legacy verdicts) — stays in the sums as a 0.
    Claims with no verdict file (missing) are recorded in ``missing_verdicts``
    and excluded from the score; the report flags them so they're not silently
    dropped.

    Edge cases:
    - Every verdict excluded (all ``not_applicable`` / excluded
      ``not_attempted``), or no verdicts at all: ``score = None``, a flag is
      added.
    - Zero headline claims extracted: score still computes from supporting;
      a flag is added.
    - Fewer than ``MIN_JUDGEABLE_CLAIMS`` claims counted: the score still
      computes but carries a low-confidence flag.
    """
    verdict_by_id = {v.claim_id: v for v in verdicts}

    headline = _tier_breakdown(claims.claims, verdict_by_id, "headline")
    supporting = _tier_breakdown(claims.claims, verdict_by_id, "supporting")

    missing_verdicts: List[str] = [
        c.id for c in claims.claims if c.id not in verdict_by_id
    ]

    numerator = 0.0
    denominator = 0.0
    counted = 0
    excluded_infra = 0  # not_attempted claims dropped from the denominator
    for c in claims.claims:
        v = verdict_by_id.get(c.id)
        if v is None:
            continue  # missing — flagged, excluded
        if v.status == "not_applicable":
            continue  # excluded by design
        # A ``not_attempted`` whose reason is our-side / indeterminate
        # (blocked_infra, no_evidence) is a run/tooling limitation, not a
        # property of the paper — exclude it from the denominator like
        # ``not_applicable`` rather than charging the paper a 0. ``authors_missing``
        # and a reasonless (legacy) not_attempted still count as a 0 failure.
        if v.status == "not_attempted" and v.not_attempted_reason in SCORE_EXCLUDED_REASONS:
            excluded_infra += 1
            continue
        weight = TIER_WEIGHTS.get(c.tier, TIER_WEIGHTS["supporting"])
        numerator += weight * VERDICT_VALUES[v.status]
        denominator += weight
        counted += 1

    score: Optional[float]
    flags: List[str] = []

    if denominator == 0.0:
        score = None
        flags.append(
            "Score not computable: no verdicts (or all not_applicable)."
        )
    else:
        score = numerator / denominator

    # Low-confidence guard: when few claims survive to the denominator (the rest
    # excluded as not_applicable / blocked_infra / no_evidence), the score is a
    # ratio over a tiny base and reads as more authoritative than it is — e.g.
    # a lone matching claim yields 1.0. Surface that rather than let it mislead.
    if score is not None and counted < MIN_JUDGEABLE_CLAIMS:
        flags.append(
            f"Low-confidence score: only {counted} judgeable claim(s) counted "
            f"(min {MIN_JUDGEABLE_CLAIMS} for a reliable score); the rest were "
            f"excluded (not_applicable / run-limited). Interpret with caution."
        )

    if not claims.by_tier("headline"):
        flags.append(
            "No headline claim extracted — review paper_claims.json."
        )

    # All-not-attempted check: only meaningful when we have verdicts at all.
    if verdicts and all(v.status == "not_attempted" for v in verdicts):
        flags.append(
            "Replication did not produce verifiable evidence "
            "(all verdicts not_attempted)."
        )

    if excluded_infra:
        flags.append(
            f"{excluded_infra} not_attempted claim(s) excluded from the "
            f"denominator as run/tooling-limited (blocked_infra / no_evidence), "
            f"not counted against the paper."
        )

    if missing_verdicts:
        flags.append(
            f"{len(missing_verdicts)} claim(s) have no verdict file — "
            f"retry recommended: {', '.join(missing_verdicts)}"
        )

    return ReplicationScore(
        score=score,
        headline=headline,
        supporting=supporting,
        total_claims=len(claims.claims),
        counted_claims=counted,
        missing_verdicts=missing_verdicts,
        flags=flags,
    )
