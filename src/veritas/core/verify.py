"""Replication Score computation from per-claim verdicts.

Pure functions — no I/O. The runner reads verdict files from disk and passes
them to ``compute_replication_score``; the score is then written to
``replication_score.json`` by the runner.
"""

from typing import Dict, List, Optional

from veritas.core.models.paper_claims import (
    ClaimVerdict,
    PaperClaim,
    PaperClaims,
    ReplicationScore,
    TIER_WEIGHTS,
    VERDICT_VALUES,
)


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

    where the sums range over claims whose verdict status is NOT
    ``not_applicable``. Claims with no verdict file (missing) are recorded
    in ``missing_verdicts`` and excluded from the score; the report flags
    them so they're not silently dropped.

    Edge cases:
    - All ``not_applicable`` (or no non-NA verdicts exist): ``score = None``,
      a flag is added.
    - Zero headline claims extracted: score still computes from supporting;
      a flag is added.
    - All ``not_attempted``: score = 0.0; a flag is added.
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
    for c in claims.claims:
        v = verdict_by_id.get(c.id)
        if v is None:
            continue  # missing — flagged, excluded
        if v.status == "not_applicable":
            continue  # excluded by design
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
