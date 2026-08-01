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


def test_numeric_scope_zero_keeps_all_with_warning():
    claims = _claims(["headline", "supporting"])
    kept, dropped, warnings = enforce_claim_scope(claims, "0")
    assert len(kept.claims) == 2 and dropped == []
    assert warnings and "not a positive count" in warnings[0]


from pathlib import Path

from veritas.templates.prompt_generator import PromptGenerator


def _render_prompt(claim_scope):
    return PromptGenerator().generate_paper_claims_prompt(
        repo_path=None,
        output_dir=Path("."),
        paper_path=Path("x.pdf"),
        claim_scope=claim_scope,
    )


def test_prompt_main_scope_branch():
    p = _render_prompt("main")
    assert "Extract only the central reproducible claims" in p
    assert "Typically 1-3" in p
    assert "favor `supporting`" not in p


def test_prompt_full_scope_branch():
    p = _render_prompt("full")
    assert "Identify every claim that:" in p
    assert "favor `supporting`" in p


def test_prompt_numeric_scope_branch():
    p = _render_prompt("2")
    assert "exactly 2 claim(s)" in p and "and only 2" in p
    assert "favor `supporting`" not in p


def test_prompt_default_scope_is_main():
    p = PromptGenerator().generate_paper_claims_prompt(
        repo_path=None, output_dir=Path("."), paper_path=Path("x.pdf")
    )
    assert "Extract only the central reproducible claims" in p
