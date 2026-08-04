"""Fixed veritas pipeline overhead: the shape of the deterministic estimate.

These values used to be a block the estimation prompt asked the model to "copy
as-is"; they are constants, so they are computed here instead. The invariant that
matters is that replicate is never given a fixed budget — that phase runs the
paper's own methodology and has no ceiling.
"""

from veritas.core.runner import build_veritas_overhead


def test_replicate_is_never_assigned_an_overhead_budget():
    overhead = build_veritas_overhead(claim_count=5, mode="full")
    assert "replicate" not in overhead
    assert "total_excluding_replicate" in overhead


def test_codegen_appears_only_in_paper_only_mode():
    assert "codegen" in build_veritas_overhead(claim_count=5, mode="paper-only")
    for mode in ("full", "repo-only"):
        assert "codegen" not in build_veritas_overhead(claim_count=5, mode=mode)


def test_verify_scales_with_claim_count():
    few = build_veritas_overhead(claim_count=2, mode="full")
    many = build_veritas_overhead(claim_count=20, mode="full")
    assert few["verify"] == "2-10 min (1-5 min per claim x 2 claims)"
    assert many["verify"] == "20-100 min (1-5 min per claim x 20 claims)"


def test_unknown_claim_count_reports_the_rate_and_flags_the_total():
    overhead = build_veritas_overhead(claim_count=0, mode="full")
    assert overhead["verify"] == "1-5 min per claim"
    assert "claim count unknown" in overhead["total_excluding_replicate"]


def test_ranges_switch_to_hours_only_once_the_floor_clears_an_hour():
    # 24 claims -> verify 24-120 min: straddles an hour, so it stays in minutes
    # rather than rendering as the less readable "0.4-2 hours".
    assert build_veritas_overhead(24, "full")["verify"].startswith("24-120 min")
    # 60 claims -> 60-300 min, entirely above the hour mark.
    assert build_veritas_overhead(60, "full")["verify"].startswith("1-5 hours")
