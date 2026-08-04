"""Fixed veritas pipeline overhead: the shape of the deterministic estimate.

These values used to be a block the estimation prompt asked the model to "copy
as-is"; they are constants, so they are computed here instead. The invariant that
matters is that replicate is never given a fixed budget — that phase runs the
paper's own methodology and has no ceiling.
"""

import json
from unittest.mock import patch

from veritas.core.config import Config
from veritas.core.runner import ReplicationRunner, build_veritas_overhead


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


def _runner(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    config = Config(repo_path=repo, output_dir=tmp_path / "out")
    runner = ReplicationRunner(config)
    config.resource_estimate_path.parent.mkdir(parents=True, exist_ok=True)
    return runner, config


def test_failed_llm_pass_still_persists_the_deterministic_half(tmp_path):
    """The overhead doesn't depend on the model, so a failed pass shouldn't lose it."""
    runner, config = _runner(tmp_path)
    runner._write_overhead_only_estimate({"needs_gpu": True}, claim_count=4)

    data = json.loads(config.resource_estimate_path.read_text())
    assert data["needs_gpu"] is True
    assert data["estimated_veritas_overhead"]["verify"].startswith("4-20 min")
    # And it must not read like a complete estimate.
    assert data["estimate_status"].startswith("partial:")


def test_static_analysis_extras_do_not_crash_the_failure_path(tmp_path):
    """Regression: analyze_repo returns key_dependencies / requires_data_download,
    which are not ResourceEstimate fields. Splatting them raised TypeError, so the
    estimate blew up on every failed pass against a repo that imports anything."""
    runner, config = _runner(tmp_path)
    (config.repo_path / "train.py").write_text("import torch\nimport requests\n")
    config.prompts_dir.mkdir(parents=True, exist_ok=True)
    config.resource_estimate_path.write_text("not json at all {{{")

    with patch.object(ReplicationRunner, "_invoke_provider", return_value=True):
        result = runner._estimate_resources(None, None, claim_count=3)

    assert result.needs_gpu is True  # static analysis survives
    data = json.loads(config.resource_estimate_path.read_text())
    assert data["key_dependencies"] == ["requests", "torch"]
    # The model's unparseable output is preserved, not silently overwritten.
    salvaged = config.resource_estimate_path.with_suffix(".unparsed.txt")
    assert salvaged.read_text() == "not json at all {{{"


def test_partial_estimate_omits_the_paper_derived_fields(tmp_path):
    runner, config = _runner(tmp_path)
    runner._write_overhead_only_estimate({}, claim_count=1)

    data = json.loads(config.resource_estimate_path.read_text())
    for field in ("estimated_cost_usd", "estimated_replication_run_time", "breakdown_notes"):
        assert field not in data, f"{field} cannot be known without the LLM pass"
