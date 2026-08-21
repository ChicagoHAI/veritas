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


# -- not_attempted reason gate: denominator exclusion (issue #102) ---------

def _two_claim_score(v1_status, v2_status, v2_reason=None):
    claims = PaperClaims(claims=[
        PaperClaim(id="C1", description="d", type="scalar", tier="headline"),
        PaperClaim(id="C2", description="d", type="scalar", tier="headline"),
    ])
    verdicts = [
        ClaimVerdict(claim_id="C1", status=v1_status),
        ClaimVerdict(claim_id="C2", status=v2_status, not_attempted_reason=v2_reason),
    ]
    return compute_replication_score(claims, verdicts)


def test_blocked_infra_excluded_from_denominator():
    # C1 match, C2 not_attempted(blocked_infra) -> C2 dropped, score = 1.0 (not 0.5).
    score = _two_claim_score("match", "not_attempted", "blocked_infra")
    assert score.score == 1.0
    assert score.counted_claims == 1
    assert any("excluded from the denominator" in f for f in score.flags)


def test_no_evidence_excluded_from_denominator():
    score = _two_claim_score("match", "not_attempted", "no_evidence")
    assert score.score == 1.0
    assert score.counted_claims == 1


def test_authors_missing_counts_as_failure():
    # authors_missing stays in the denominator as a 0 -> score = 0.5.
    score = _two_claim_score("match", "not_attempted", "authors_missing")
    assert score.score == 0.5
    assert score.counted_claims == 2


def test_reasonless_not_attempted_still_counts_zero():
    # Legacy verdicts without a reason keep the old behavior (0 in denominator).
    score = _two_claim_score("match", "not_attempted", None)
    assert score.score == 0.5
    assert score.counted_claims == 2


def test_low_confidence_flag_when_few_judgeable():
    # 1 match + 1 excluded(blocked_infra) -> score 1.0 but only 1 judgeable claim.
    score = _two_claim_score("match", "not_attempted", "blocked_infra")
    assert score.score == 1.0 and score.counted_claims == 1
    assert any("Low-confidence score" in f for f in score.flags)


def test_no_low_confidence_flag_when_enough_judgeable():
    claims = PaperClaims(claims=[
        PaperClaim(id=f"C{i}", description="d", type="scalar", tier="headline")
        for i in range(3)
    ])
    verdicts = [ClaimVerdict(claim_id=f"C{i}", status="match") for i in range(3)]
    score = compute_replication_score(claims, verdicts)
    assert score.counted_claims == 3
    assert not any("Low-confidence" in f for f in score.flags)


# -- low-confidence flag: small-by-design vs exclusion-shrunk ---------------

def test_no_low_confidence_flag_when_small_by_design():
    # Two main-scope claims, both cleanly graded: the denominator is small
    # because the run was scoped small, not because claims dropped out.
    claims = PaperClaims(claims=[
        PaperClaim(id="C1", description="d", type="scalar", tier="headline"),
        PaperClaim(id="C2", description="d", type="scalar", tier="headline"),
    ], scope="main")
    verdicts = [
        ClaimVerdict(claim_id="C1", status="match"),
        ClaimVerdict(claim_id="C2", status="no_match"),
    ]
    score = compute_replication_score(claims, verdicts)
    assert score.counted_claims == 2
    assert not any("Low-confidence" in f for f in score.flags)


def test_no_low_confidence_flag_single_clean_claim():
    # Numeric scope 1: a single clean claim is the requested set, no warning.
    claims = PaperClaims(claims=[
        PaperClaim(id="C1", description="d", type="scalar", tier="headline"),
    ], scope="1")
    verdicts = [ClaimVerdict(claim_id="C1", status="match")]
    score = compute_replication_score(claims, verdicts)
    assert score.counted_claims == 1
    assert not any("Low-confidence" in f for f in score.flags)


def test_no_low_confidence_flag_user_supplied_set():
    claims = PaperClaims(claims=[
        PaperClaim(id="C1", description="d", type="scalar", tier="headline"),
    ], scope="user")
    verdicts = [ClaimVerdict(claim_id="C1", status="match")]
    score = compute_replication_score(claims, verdicts)
    assert not any("Low-confidence" in f for f in score.flags)


def test_low_confidence_flag_small_full_scope_set():
    # Full scope whose extraction simply found few claims is a thin base:
    # the flag fires even though nothing dropped out. Same for unstamped
    # (pre-scope) sets, which were always full-scope.
    for scope in ("full", None):
        claims = PaperClaims(claims=[
            PaperClaim(id="C1", description="d", type="scalar", tier="headline"),
            PaperClaim(id="C2", description="d", type="scalar", tier="supporting"),
        ], scope=scope)
        verdicts = [
            ClaimVerdict(claim_id="C1", status="match"),
            ClaimVerdict(claim_id="C2", status="match"),
        ]
        score = compute_replication_score(claims, verdicts)
        assert score.counted_claims == 2
        assert any("Low-confidence" in f for f in score.flags), scope


def test_low_confidence_flag_scoped_run_with_dropout():
    # Small-by-design does not exempt exclusion shrinkage: a main-scope run
    # that lost a claim to infra still warns about its lone survivor.
    claims = PaperClaims(claims=[
        PaperClaim(id="C1", description="d", type="scalar", tier="headline"),
        PaperClaim(id="C2", description="d", type="scalar", tier="headline"),
    ], scope="main")
    verdicts = [
        ClaimVerdict(claim_id="C1", status="match"),
        ClaimVerdict(
            claim_id="C2", status="not_attempted",
            not_attempted_reason="blocked_infra",
        ),
    ]
    score = compute_replication_score(claims, verdicts)
    assert score.counted_claims == 1
    assert any("Low-confidence" in f for f in score.flags)


def test_low_confidence_flag_when_verdict_missing():
    claims = PaperClaims(claims=[
        PaperClaim(id="C1", description="d", type="scalar", tier="headline"),
        PaperClaim(id="C2", description="d", type="scalar", tier="headline"),
    ])
    verdicts = [ClaimVerdict(claim_id="C1", status="match")]
    score = compute_replication_score(claims, verdicts)
    assert score.counted_claims == 1
    assert any("Low-confidence" in f for f in score.flags)
