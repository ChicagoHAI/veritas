"""Tests for provider argv assembly, env stripping, and engine fingerprints."""

import pytest

from veritas.core.runner import build_provider_command


def test_claude_no_model_matches_legacy_argv():
    cmd = build_provider_command("/usr/bin/claude", "claude", None)
    assert cmd == [
        "/usr/bin/claude", "-p",
        "--verbose", "--output-format", "stream-json",
        "--dangerously-skip-permissions",
    ]

def test_claude_with_model():
    cmd = build_provider_command("/usr/bin/claude", "claude", "claude-opus-4-8")
    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "claude-opus-4-8"

def test_codex_with_model_keeps_stdin_sentinel_last():
    cmd = build_provider_command("/usr/bin/codex", "codex", "gpt-5.5-codex")
    assert cmd[:2] == ["/usr/bin/codex", "exec"]
    assert cmd[cmd.index("-m") + 1] == "gpt-5.5-codex"
    assert cmd[-1] == "-"

def test_gemini_with_model():
    cmd = build_provider_command("/usr/bin/gemini", "gemini", "gemini-3-pro")
    assert cmd[cmd.index("-m") + 1] == "gemini-3-pro"

def test_openrouter_prefixes_slug_for_opencode():
    cmd = build_provider_command("/usr/bin/opencode", "openrouter",
                                 "moonshotai/kimi-k2.6")
    assert cmd[:2] == ["/usr/bin/opencode", "run"]
    assert cmd[cmd.index("-m") + 1] == "openrouter/moonshotai/kimi-k2.6"
    assert "--auto" in cmd

def test_unknown_provider_raises():
    with pytest.raises(KeyError):
        build_provider_command("/usr/bin/x", "nonsense", None)


import io
from pathlib import Path

from veritas.core.config import Config
from veritas.core.runner import ReplicationRunner


class _FakeProcess:
    def __init__(self):
        self.stdin = io.StringIO()
        self.stdout = io.StringIO("")  # readline -> "" ends the stream loop
    def wait(self):
        return 0
    def kill(self):
        pass


def _capture_invocation(tmp_path, monkeypatch, config, bucket):
    captured = {}
    def fake_popen(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["env"] = kwargs.get("env")
        return _FakeProcess()
    monkeypatch.setattr("veritas.core.runner.subprocess.Popen", fake_popen)
    monkeypatch.setattr("veritas.core.runner.shutil.which",
                        lambda name: f"/usr/bin/{name}")
    runner = ReplicationRunner(config)
    ok = runner._invoke_provider(
        prompt="hello",
        working_dir=tmp_path,
        log_path=tmp_path / "t.jsonl",
        timeout=None,
        bucket=bucket,
    )
    assert ok is True
    return captured


def test_invoke_uses_bucket_engine(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    config = Config(repo_path=repo, output_dir=tmp_path / "out",
                    verify_model="openrouter:openai/gpt-5.5")
    captured = _capture_invocation(tmp_path, monkeypatch, config, "verify")
    assert captured["cmd"][0] == "/usr/bin/opencode"
    assert captured["cmd"][captured["cmd"].index("-m") + 1] == "openrouter/openai/gpt-5.5"


def test_invoke_default_is_legacy_claude(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir()
    config = Config(repo_path=repo, output_dir=tmp_path / "out")
    captured = _capture_invocation(tmp_path, monkeypatch, config, "analyze")
    assert captured["cmd"] == [
        "/usr/bin/claude", "-p",
        "--verbose", "--output-format", "stream-json",
        "--dangerously-skip-permissions",
    ]


from veritas.core.runner import (
    FINGERPRINT_INVALIDATES,
    _is_spurious_engine_change,
    build_config_fingerprint,
)


def _cfg(tmp_path, **kw):
    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    return Config(repo_path=repo, output_dir=tmp_path / "out", **kw)


def test_fingerprint_always_includes_engines(tmp_path):
    fp = build_config_fingerprint(_cfg(tmp_path))
    assert fp["provider"] == "claude"
    for bucket in ("analyze", "codegen", "replicate", "assess", "verify", "evaluate"):
        assert fp[f"engine_{bucket}"] == "claude"


def test_fingerprint_resolves_knobs(tmp_path):
    fp = build_config_fingerprint(
        _cfg(tmp_path, verify_model="openrouter:openai/gpt-5.5"))
    assert fp["engine_verify"] == "openrouter:openai/gpt-5.5"
    assert fp["engine_analyze"] == "claude"


def test_spurious_engine_change_uses_provider_baseline(tmp_path):
    # A recorded config from before engine tracking compares each engine
    # against the recorded global provider, not against "missing".
    recorded = {"provider": "claude", "mode": "repo-only", "claims_path": None}
    current = build_config_fingerprint(
        _cfg(tmp_path, verify_model="openrouter:openai/gpt-5.5"))
    assert _is_spurious_engine_change("engine_analyze", recorded, current) is True
    assert _is_spurious_engine_change("engine_verify", recorded, current) is False
    # non-engine fields are never filtered
    assert _is_spurious_engine_change("provider", recorded, current) is False


def test_spurious_filter_off_once_engines_recorded(tmp_path):
    # Once a config with engine keys is recorded, comparisons are direct:
    # nothing is treated as spurious, so reverting a knob is detected.
    recorded = {"provider": "claude", "engine_verify": "openrouter:openai/gpt-5.5"}
    current = build_config_fingerprint(_cfg(tmp_path))
    assert current["engine_verify"] == "claude"
    assert _is_spurious_engine_change("engine_verify", recorded, current) is False


def test_first_model_knob_invalidates_only_its_bucket(tmp_path):
    # Regression: a default run records no engine keys; adding --verify-model
    # afterwards must invalidate verify alone, not every stage.
    from veritas.core.pipeline_state import PipelineState

    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    out = tmp_path / "out"
    config = Config(repo_path=repo, output_dir=out,
                    verify_model="openrouter:openai/gpt-5.5")
    state = PipelineState(out)
    state.record_inputs(repo, None)
    state.record_config({"provider": "claude", "mode": "repo-only",
                         "claims_path": None})
    for stage in ("analyze", "plan", "replicate", "assess_fixes", "verify"):
        state.start_stage(stage)
        state.complete_stage(stage, success=True)

    ReplicationRunner(config)._reconcile_with_prior_run(state)

    assert not state.is_stage_completed("verify")
    for stage in ("analyze", "plan", "replicate", "assess_fixes"):
        assert state.is_stage_completed(stage), stage
    # The baseline is upgraded to explicit engine fields.
    assert state.state["config"]["engine_verify"] == "openrouter:openai/gpt-5.5"


def test_legacy_state_resumes_clean_and_upgrades(tmp_path):
    # No knobs set against a pre-engine-tracking state file: nothing is
    # invalidated, and the recorded baseline gains explicit engine fields.
    from veritas.core.pipeline_state import PipelineState

    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    out = tmp_path / "out"
    config = Config(repo_path=repo, output_dir=out)
    state = PipelineState(out)
    state.record_inputs(repo, None)
    state.record_config({"provider": "claude", "mode": "repo-only",
                         "claims_path": None})
    for stage in ("analyze", "plan", "replicate", "assess_fixes", "verify"):
        state.start_stage(stage)
        state.complete_stage(stage, success=True)

    ReplicationRunner(config)._reconcile_with_prior_run(state)

    for stage in ("analyze", "plan", "replicate", "assess_fixes", "verify"):
        assert state.is_stage_completed(stage), stage
    assert state.state["config"]["engine_verify"] == "claude"


def test_invalidation_rows():
    assert FINGERPRINT_INVALIDATES["paper_path"] == (
        "analyze", "codegen", "plan", "resource_estimate", "replicate",
        "assess_fixes", "verify")
    assert FINGERPRINT_INVALIDATES["mode"] == (
        "analyze", "codegen", "plan", "resource_estimate", "replicate",
        "assess_fixes", "verify")
    # provider changes act through the per-bucket engine rows
    assert FINGERPRINT_INVALIDATES["provider"] == ()
    assert FINGERPRINT_INVALIDATES["engine_verify"] == ("verify",)
    assert FINGERPRINT_INVALIDATES["engine_assess"] == ("assess_fixes",)
    assert FINGERPRINT_INVALIDATES["engine_replicate"] == (
        "replicate", "assess_fixes", "verify")
    assert FINGERPRINT_INVALIDATES["engine_analyze"] == (
        "analyze", "plan", "resource_estimate", "replicate", "assess_fixes", "verify")
    assert FINGERPRINT_INVALIDATES["engine_codegen"] == (
        "codegen", "plan", "resource_estimate", "replicate", "assess_fixes", "verify")
    assert FINGERPRINT_INVALIDATES["engine_evaluate"] == ()


def test_stripped_env_exempts_only_invoked_provider_keys(monkeypatch):
    monkeypatch.setenv("VERITAS_ENV_FILE_KEYS",
                       "OPENROUTER_API_KEY,OPENAI_API_KEY,ANTHROPIC_API_KEY,HF_TOKEN")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("HF_TOKEN", "hf-test")

    # An openrouter invocation sees only the openrouter key.
    env = ReplicationRunner._stripped_env(
        ReplicationRunner._auth_exemptions("openrouter"))
    assert env["OPENROUTER_API_KEY"] == "sk-or-test"
    assert "OPENAI_API_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "HF_TOKEN" not in env

    # A claude invocation in the same run sees only claude's vars — the
    # openrouter key configured for another bucket never reaches it.
    env = ReplicationRunner._stripped_env(
        ReplicationRunner._auth_exemptions("claude"))
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-test"
    assert "OPENROUTER_API_KEY" not in env
    assert "HF_TOKEN" not in env


def test_stripped_env_default_unchanged(monkeypatch):
    monkeypatch.setenv("VERITAS_ENV_FILE_KEYS", "HF_TOKEN")
    monkeypatch.setenv("HF_TOKEN", "hf-test")
    env = ReplicationRunner._stripped_env()
    assert "HF_TOKEN" not in env


def test_openrouter_auth_check_raises_without_key(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    config = Config(repo_path=repo, output_dir=tmp_path / "out",
                    verify_model="openrouter:openai/gpt-5.5")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    runner = ReplicationRunner(config)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        runner._check_provider_auth()


def test_openrouter_auth_check_passes_with_key(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    config = Config(repo_path=repo, output_dir=tmp_path / "out",
                    verify_model="openrouter:openai/gpt-5.5")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    ReplicationRunner(config)._check_provider_auth()


def test_auth_check_noop_without_openrouter(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    config = Config(repo_path=repo, output_dir=tmp_path / "out")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    ReplicationRunner(config)._check_provider_auth()


# -- evaluate-bucket settings-aware resume -----------------------------------

def _eval_runner(tmp_path, monkeypatch, **kw):
    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    config = Config(repo_path=repo, output_dir=tmp_path / "out", **kw)
    config.evaluation_dir.mkdir(parents=True, exist_ok=True)
    config.prompts_dir.mkdir(parents=True, exist_ok=True)
    runner = ReplicationRunner(config)
    monkeypatch.setattr(runner.prompt_generator, "generate_evaluation_prompt",
                        lambda **kwargs: "prompt")
    return runner, config


def test_evaluate_skips_when_engine_unchanged(tmp_path, monkeypatch):
    runner, config = _eval_runner(tmp_path, monkeypatch)
    config.evaluation_path.write_text('{"cheating_monitor": {}}', encoding="utf-8")
    config.evaluation_meta_path.write_text('{"engine": "claude"}', encoding="utf-8")
    calls = []
    monkeypatch.setattr(ReplicationRunner, "_invoke_provider",
                        lambda self, *a, **k: calls.append(1) or True)
    runner._evaluate()
    assert calls == []


def test_evaluate_skips_legacy_output_without_meta(tmp_path, monkeypatch):
    runner, config = _eval_runner(tmp_path, monkeypatch)
    config.evaluation_path.write_text('{"cheating_monitor": {}}', encoding="utf-8")
    calls = []
    monkeypatch.setattr(ReplicationRunner, "_invoke_provider",
                        lambda self, *a, **k: calls.append(1) or True)
    runner._evaluate()
    assert calls == []


def test_evaluate_reruns_on_engine_change(tmp_path, monkeypatch):
    runner, config = _eval_runner(
        tmp_path, monkeypatch, evaluate_model="claude-sonnet-5")
    config.evaluation_path.write_text('{"cheating_monitor": {}}', encoding="utf-8")
    config.evaluation_meta_path.write_text('{"engine": "claude"}', encoding="utf-8")
    calls = []

    def fake_invoke(self, *args, **kwargs):
        calls.append(1)
        config.evaluation_path.write_text(
            '{"cheating_monitor": {"risk": "low"}}', encoding="utf-8")
        return True

    monkeypatch.setattr(ReplicationRunner, "_invoke_provider", fake_invoke)
    runner._evaluate()
    assert calls == [1]
    import json as _json
    meta = _json.loads(config.evaluation_meta_path.read_text(encoding="utf-8"))
    assert meta == {"engine": "claude:claude-sonnet-5"}


def test_reconcile_never_deletes_verify_outputs(tmp_path):
    # Reconciliation invalidates state only; the verdict files and score are
    # cleared at verify entry, when the stage actually re-runs. A dry run or
    # aborted resume after an engine change must not destroy a completed
    # run's outputs.
    from veritas.core.pipeline_state import PipelineState

    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    out = tmp_path / "out"
    config = Config(repo_path=repo, output_dir=out,
                    verify_model="openrouter:openai/gpt-5.5")
    state = PipelineState(out)
    state.record_inputs(repo, None)
    state.record_config({"provider": "claude", "mode": "repo-only",
                         "claims_path": None})
    for stage in ("analyze", "plan", "replicate", "assess_fixes", "verify"):
        state.start_stage(stage)
        state.complete_stage(stage, success=True)
    config.verify_dir.mkdir(parents=True, exist_ok=True)
    (config.verify_dir / "C1.json").write_text("{}", encoding="utf-8")
    (config.verify_dir / "verdicts.json").write_text("{}", encoding="utf-8")
    (config.verify_dir / "replication_score.json").write_text("{}", encoding="utf-8")

    ReplicationRunner(config)._reconcile_with_prior_run(state)

    assert not state.is_stage_completed("verify")
    assert (config.verify_dir / "C1.json").exists()
    assert (config.verify_dir / "verdicts.json").exists()
    assert (config.verify_dir / "replication_score.json").exists()


def test_verify_entry_clears_artifacts_only_without_record(tmp_path):
    # No verify stage record (fresh, --restart, or invalidated) -> leftover
    # verdict files belong to a discarded attempt and are cleared. A present
    # in_progress record is a legitimate partial resume: files are kept.
    from veritas.core.pipeline_state import PipelineState

    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    out = tmp_path / "out"
    config = Config(repo_path=repo, output_dir=out)
    state = PipelineState(out)
    config.verify_dir.mkdir(parents=True, exist_ok=True)
    (config.verify_dir / "C1.json").write_text("{}", encoding="utf-8")
    (config.verify_dir / "verdicts.json").write_text("{}", encoding="utf-8")
    (config.verify_dir / "C1_transcript.jsonl").write_text("x", encoding="utf-8")
    runner = ReplicationRunner(config)

    runner._clear_stale_verify_artifacts(state)
    assert not (config.verify_dir / "C1.json").exists()
    assert not (config.verify_dir / "verdicts.json").exists()
    # transcripts are history, not resume state — kept
    assert (config.verify_dir / "C1_transcript.jsonl").exists()

    # in_progress record -> partial resume, nothing cleared
    (config.verify_dir / "C2.json").write_text("{}", encoding="utf-8")
    state.start_stage("verify")
    runner._clear_stale_verify_artifacts(state)
    assert (config.verify_dir / "C2.json").exists()


def test_codegen_sentinel_cleared_only_without_record(tmp_path):
    from veritas.core.pipeline_state import PipelineState

    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    out = tmp_path / "out"
    config = Config(repo_path=repo, output_dir=out)
    state = PipelineState(out)
    sentinel = config.codegen_complete_sentinel_path
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("done", encoding="utf-8")
    runner = ReplicationRunner(config)

    # no record -> discarded attempt, sentinel cleared
    runner._clear_stale_codegen_sentinel(state)
    assert not sentinel.exists()

    # in_progress record (crash between sentinel write and completion) ->
    # the sentinel stays authoritative
    sentinel.write_text("done", encoding="utf-8")
    state.start_stage("codegen")
    runner._clear_stale_codegen_sentinel(state)
    assert sentinel.exists()


def test_spurious_filter_handles_capitalized_legacy_provider(tmp_path):
    recorded = {"provider": "Claude", "mode": "repo-only", "claims_path": None}
    current = build_config_fingerprint(_cfg(tmp_path))
    assert _is_spurious_engine_change("engine_analyze", recorded, current) is True


def test_manager_codegen_target_downgrades_to_plan(tmp_path):
    # The retry loop can only re-run plan and replicate; a codegen target is
    # coerced to plan so the codegen stage and its sentinel stay intact
    # (clearing the sentinel without re-running codegen would prime a later
    # resume to wipe the final patched codebase).
    from veritas.core.pipeline_state import PipelineState
    from veritas.core.runner import _coerce_manager_target

    assert _coerce_manager_target("codegen") == "plan"
    assert _coerce_manager_target("plan") == "plan"
    assert _coerce_manager_target("replicate") == "replicate"

    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    out = tmp_path / "out"
    config = Config(repo_path=repo, output_dir=out)
    state = PipelineState(out)
    for stage in ("analyze", "codegen", "plan", "resource_estimate",
                  "replicate", "assess_fixes", "verify"):
        state.start_stage(stage)
        state.complete_stage(stage, success=True)
    sentinel = config.codegen_complete_sentinel_path
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("done", encoding="utf-8")

    ReplicationRunner(config)._invalidate_for_rerun(
        state, _coerce_manager_target("codegen"))

    for stage in ("plan", "resource_estimate", "replicate",
                  "assess_fixes", "verify"):
        assert not state.is_stage_completed(stage), stage
    assert state.is_stage_completed("codegen")
    assert sentinel.exists()
    assert state.is_stage_completed("analyze")


def test_manager_plan_rerun_invalidates_resource_estimate(tmp_path):
    from veritas.core.pipeline_state import PipelineState

    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    out = tmp_path / "out"
    config = Config(repo_path=repo, output_dir=out)
    state = PipelineState(out)
    for stage in ("analyze", "codegen", "plan", "resource_estimate",
                  "replicate", "assess_fixes", "verify"):
        state.start_stage(stage)
        state.complete_stage(stage, success=True)

    ReplicationRunner(config)._invalidate_for_rerun(state, "plan")

    assert not state.is_stage_completed("plan")
    assert not state.is_stage_completed("resource_estimate")
    assert state.is_stage_completed("codegen")


def test_auth_check_requires_model_for_active_openrouter(tmp_path, monkeypatch):
    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    config = Config(repo_path=repo, output_dir=tmp_path / "out",
                    provider="openrouter")
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")
    runner = ReplicationRunner(config)
    with pytest.raises(RuntimeError, match="explicit model"):
        runner._check_provider_auth()


def test_auth_check_skips_inactive_buckets(tmp_path, monkeypatch):
    # A lingering verify knob must not block a dry run, which never
    # invokes the verify bucket.
    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    config = Config(repo_path=repo, output_dir=tmp_path / "out",
                    verify_model="openrouter:openai/gpt-5.5")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    runner = ReplicationRunner(config)
    active = runner._active_buckets(dry_run=True)
    assert "verify" not in active
    runner._check_provider_auth(buckets=active)  # must not raise
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        runner._check_provider_auth(buckets=runner._active_buckets())


def test_replicate_entry_refreshes_codebase_when_invalidated(tmp_path):
    # A replicate re-run in a repo-backed mode starts from a pristine copy
    # of the repo, not the previous attempt's patched tree. Fresh runs (no
    # prior attempt artifacts) keep the already-staged tree untouched.
    from veritas.core.pipeline_state import PipelineState

    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    (repo / "main.py").write_text("original", encoding="utf-8")
    out = tmp_path / "out"
    config = Config(repo_path=repo, output_dir=out)
    state = PipelineState(out)
    codebase = config.replication_dir / "codebase"
    codebase.mkdir(parents=True, exist_ok=True)
    (codebase / "main.py").write_text("staged-fresh", encoding="utf-8")
    runner = ReplicationRunner(config)

    # fresh run: no prior attempt evidenced -> no re-staging
    runner._refresh_codebase_if_stale(state)
    assert (codebase / "main.py").read_text(encoding="utf-8") == "staged-fresh"

    # a prior attempt ran (transcript exists) and was invalidated -> refresh
    (codebase / "main.py").write_text("patched-by-old-engine", encoding="utf-8")
    config.replication_transcript_path.write_text("x", encoding="utf-8")
    runner._refresh_codebase_if_stale(state)
    assert (codebase / "main.py").read_text(encoding="utf-8") == "original"

    # in_progress record -> partial attempt, left in place
    (codebase / "main.py").write_text("partial-work", encoding="utf-8")
    state.start_stage("replicate")
    runner._refresh_codebase_if_stale(state)
    assert (codebase / "main.py").read_text(encoding="utf-8") == "partial-work"


def test_replicate_entry_restores_codegen_snapshot_in_paper_only(tmp_path):
    # Paper-only mode restores the pristine codegen output from its
    # snapshot instead of copying a (nonexistent) repo.
    from veritas.core.pipeline_state import PipelineState

    paper = tmp_path / "paper.pdf"; paper.write_text("x", encoding="utf-8")
    out = tmp_path / "out"
    config = Config(paper_path=paper, output_dir=out, mode="paper-only")
    state = PipelineState(out)
    codebase = config.replication_dir / "codebase"
    codebase.mkdir(parents=True, exist_ok=True)
    (codebase / "gen.py").write_text("patched", encoding="utf-8")
    snapshot = config.veritas_state_dir / "codegen_snapshot"
    snapshot.mkdir(parents=True, exist_ok=True)
    (snapshot / "gen.py").write_text("pristine-generated", encoding="utf-8")
    config.replication_transcript_path.write_text("x", encoding="utf-8")

    ReplicationRunner(config)._refresh_codebase_if_stale(state)
    assert (codebase / "gen.py").read_text(encoding="utf-8") == "pristine-generated"


# -- leakage-warning bucket selection ----------------------------------------

def test_leak_buckets_exclude_codegen_outside_paper_only(tmp_path):
    # codegen never runs outside paper-only mode; a web-locked global model
    # must not warn about a bucket that stays inert.
    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    config = Config(repo_path=repo, output_dir=tmp_path / "out")
    assert "codegen" not in ReplicationRunner(config)._leak_buckets()


def test_leak_buckets_include_codegen_in_paper_only(tmp_path):
    paper = tmp_path / "paper.pdf"; paper.write_text("x", encoding="utf-8")
    config = Config(paper_path=paper, output_dir=tmp_path / "out")
    assert "codegen" in ReplicationRunner(config)._leak_buckets()


def test_leak_buckets_include_evaluate_only_when_loop_on(tmp_path):
    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    single = Config(repo_path=repo, output_dir=tmp_path / "out", max_iters=1)
    looped = Config(repo_path=repo, output_dir=tmp_path / "out2", max_iters=2)
    assert "evaluate" not in ReplicationRunner(single)._leak_buckets()
    assert "evaluate" in ReplicationRunner(looped)._leak_buckets()
