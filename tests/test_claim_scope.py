"""PaperClaims.scope stamp round-trip and the enforce_claim_scope guard."""

from veritas.core.models.paper_claims import PaperClaim, PaperClaims
from veritas.core.paper_claims import enforce_claim_scope


def _claims(tiers):
    return PaperClaims(claims=[
        PaperClaim(id=f"C{i+1}", description="d", type="scalar", tier=t)
        for i, t in enumerate(tiers)
    ])


def test_scope_round_trip():
    claims = _claims(["headline"])
    claims.scope = "main"
    assert PaperClaims.from_dict(claims.to_dict()).scope == "main"


def test_scope_omitted_when_none():
    d = _claims(["headline"]).to_dict()
    assert "scope" not in d
    assert PaperClaims.from_dict(d).scope is None


def test_full_scope_passthrough():
    claims = _claims(["headline", "supporting"])
    kept, dropped, warnings = enforce_claim_scope(claims, "full")
    assert len(kept.claims) == 2 and dropped == [] and warnings == []


def test_main_scope_drops_supporting():
    claims = _claims(["headline", "supporting", "supporting"])
    kept, dropped, warnings = enforce_claim_scope(claims, "main")
    assert [c.id for c in kept.claims] == ["C1"]
    assert dropped == ["C2", "C3"] and warnings == []


def test_main_scope_all_supporting_keeps_all_with_warning():
    claims = _claims(["supporting", "supporting"])
    kept, dropped, warnings = enforce_claim_scope(claims, "main")
    assert len(kept.claims) == 2 and dropped == []
    assert warnings and "no headline-tier claims" in warnings[0]


def test_numeric_scope_trims_to_n():
    claims = _claims(["headline", "headline", "headline"])
    kept, dropped, warnings = enforce_claim_scope(claims, "2")
    assert [c.id for c in kept.claims] == ["C1", "C2"]
    assert dropped == ["C3"] and warnings == []


def test_numeric_scope_undercount_keeps_all_with_warning():
    claims = _claims(["headline"])
    kept, dropped, warnings = enforce_claim_scope(claims, "3")
    assert len(kept.claims) == 1 and dropped == []
    assert warnings and "only 1" in warnings[0]
