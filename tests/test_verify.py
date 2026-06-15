"""Tier-weighted Replication Score: the two-tier (headline / supporting) contract."""

from veritas.core.models.paper_claims import (
    PaperClaim, PaperClaims, ClaimVerdict, TIER_WEIGHTS,
)
from veritas.core.verify import compute_replication_score


def test_tier_weights_match_claim_tiers():
    from veritas.core.models.paper_claims import ClaimTier
    assert set(TIER_WEIGHTS) == set(ClaimTier.__args__) == {"headline", "supporting"}
    assert TIER_WEIGHTS == {"headline": 3.0, "supporting": 2.0}


def test_score_dict_has_no_setup_key():
    claims = PaperClaims(claims=[
        PaperClaim(id="C1", description="d", type="scalar", tier="headline"),
    ])
    verdicts = [ClaimVerdict(claim_id="C1", status="match")]
    score = compute_replication_score(claims, verdicts)
    assert score.score == 1.0
    assert "setup" not in score.to_dict()
