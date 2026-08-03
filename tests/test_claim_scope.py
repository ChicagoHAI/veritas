"""PaperClaims.scope stamp round-trip and the enforce_claim_scope guard."""

from pathlib import Path

from veritas.core.config import Config
from veritas.core.models.paper_claims import PaperClaim, PaperClaims
from veritas.core.paper_claims import effective_claim_scope, enforce_claim_scope
from veritas.core.pipeline_state import PipelineState
from veritas.core.runner import FINGERPRINT_INVALIDATES, ReplicationRunner
from veritas.templates.prompt_generator import PromptGenerator


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


def test_effective_scope_main_with_headline_stays_main():
    kept, _, _ = enforce_claim_scope(_claims(["headline", "supporting"]), "main")
    assert effective_claim_scope("main", kept) == "main"


def test_effective_scope_main_without_headline_is_full():
    # main fell back to keeping every tier; the report must not call it "main".
    kept, _, _ = enforce_claim_scope(_claims(["supporting", "supporting"]), "main")
    assert effective_claim_scope("main", kept) == "full"


def test_effective_scope_full_and_numeric_unchanged():
    assert effective_claim_scope("full", _claims(["headline", "supporting"])) == "full"
    # A numeric request that couldn't be fully met still reports as requested.
    assert effective_claim_scope("3", _claims(["headline"])) == "3"


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


def test_claim_scope_invalidates_all_stages():
    # resource_estimate is derived from the plan, so a scope change must drop it
    # too — otherwise `estimate --scope` reuses the prior scope's estimate.
    assert FINGERPRINT_INVALIDATES["claim_scope"] == (
        "analyze", "plan", "resource_estimate", "replicate", "assess_fixes", "verify",
    )


def test_claim_scope_in_config_fingerprint(tmp_path):
    config = Config(repo_path=tmp_path, output_dir=tmp_path / "out", claim_scope="2")
    fp = ReplicationRunner(config)._config_fingerprint()
    assert fp["claim_scope"] == "2"


def test_detect_config_changes_missing_claim_scope_matches_full(tmp_path):
    """A recorded config predating claim_scope implicitly ran full-scope."""
    state = PipelineState(tmp_path)
    state.record_config({"provider": "claude", "mode": "full", "claims_path": None})
    changes = state.detect_config_changes(
        {"provider": "claude", "mode": "full", "claims_path": None, "claim_scope": "full"}
    )
    assert "claim_scope" not in changes


def test_detect_config_changes_missing_claim_scope_vs_main_is_a_change(tmp_path):
    state = PipelineState(tmp_path)
    state.record_config({"provider": "claude", "mode": "full", "claims_path": None})
    changes = state.detect_config_changes(
        {"provider": "claude", "mode": "full", "claims_path": None, "claim_scope": "main"}
    )
    assert "claim_scope" in changes


def test_detect_config_changes_main_vs_numeric_is_a_change(tmp_path):
    state = PipelineState(tmp_path)
    state.record_config(
        {"provider": "claude", "mode": "full", "claims_path": None, "claim_scope": "main"}
    )
    changes = state.detect_config_changes(
        {"provider": "claude", "mode": "full", "claims_path": None, "claim_scope": "2"}
    )
    assert "claim_scope" in changes


def test_detect_config_changes_main_vs_main_is_unchanged(tmp_path):
    state = PipelineState(tmp_path)
    state.record_config(
        {"provider": "claude", "mode": "full", "claims_path": None, "claim_scope": "main"}
    )
    changes = state.detect_config_changes(
        {"provider": "claude", "mode": "full", "claims_path": None, "claim_scope": "main"}
    )
    assert "claim_scope" not in changes


def test_reconcile_legacy_dir_full_scope_no_invalidation(tmp_path):
    """A run dir whose recorded config predates claim_scope reconciles clean under --scope full."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# repo", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    cfg = Config(repo_path=repo, output_dir=out, mode="repo-only", claim_scope="full")

    state = PipelineState(out)
    state.record_inputs(cfg.repo_path, cfg.paper_path, data_path=cfg.data_path)
    # Pre-feature recorded config: no claim_scope key at all.
    state.record_config({"provider": cfg.provider, "mode": cfg.mode, "claims_path": None})
    state.start_stage("analyze")
    state.complete_stage("analyze", success=True)

    ReplicationRunner(cfg)._reconcile_with_prior_run(state)

    assert state.is_stage_completed("analyze")


def test_reconcile_legacy_dir_main_scope_invalidates(tmp_path):
    """The same legacy dir resumed with --scope main is a real scope change."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# repo", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    cfg = Config(repo_path=repo, output_dir=out, mode="repo-only", claim_scope="main")

    state = PipelineState(out)
    state.record_inputs(cfg.repo_path, cfg.paper_path, data_path=cfg.data_path)
    state.record_config({"provider": cfg.provider, "mode": cfg.mode, "claims_path": None})
    state.start_stage("analyze")
    state.complete_stage("analyze", success=True)

    ReplicationRunner(cfg)._reconcile_with_prior_run(state)

    assert not state.is_stage_completed("analyze")


def test_scope_change_invalidates_resource_estimate(tmp_path):
    """`estimate --scope` on the same dir must re-estimate, not reuse the prior
    scope's estimate: the estimate is derived from the scope-shaped plan."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("# repo", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    cfg = Config(repo_path=repo, output_dir=out, mode="repo-only", claim_scope="main")

    state = PipelineState(out)
    state.record_inputs(cfg.repo_path, cfg.paper_path, data_path=cfg.data_path)
    # Prior run was full scope, with the estimate already computed and cached.
    state.record_config({"provider": cfg.provider, "mode": cfg.mode, "claims_path": None, "claim_scope": "full"})
    for stage in ("analyze", "plan", "resource_estimate"):
        state.start_stage(stage)
        state.complete_stage(stage, success=True)

    ReplicationRunner(cfg)._reconcile_with_prior_run(state)

    assert not state.is_stage_completed("resource_estimate")
    assert not state.is_stage_completed("plan")
