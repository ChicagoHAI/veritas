"""Tier-weighted Replication Score contract after setup-tier removal."""

from veritas.core.models.paper_claims import (
    PaperClaim, PaperClaims, ClaimVerdict, TIER_WEIGHTS,
)
from veritas.core.verify import compute_replication_score


def test_tier_weights_has_no_setup():
    assert set(TIER_WEIGHTS) == {"headline", "supporting"}


def test_score_dict_has_no_setup_key():
    claims = PaperClaims(claims=[
        PaperClaim(id="C1", description="d", type="scalar", tier="headline"),
    ])
    verdicts = [ClaimVerdict(claim_id="C1", status="match")]
    score = compute_replication_score(claims, verdicts)
    assert score.score == 1.0
    assert "setup" not in score.to_dict()
