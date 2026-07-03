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


def test_invalidation_clears_verify_resume_artifacts(tmp_path):
    # Verify resumes per claim on verdict-file existence, so invalidating the
    # stage must also remove the verdict files or the old engine's verdicts
    # would be silently reused under the new engine's name.
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
    (config.verify_dir / "C1_transcript.jsonl").write_text("x", encoding="utf-8")

    ReplicationRunner(config)._reconcile_with_prior_run(state)

    assert not (config.verify_dir / "C1.json").exists()
    assert not (config.verify_dir / "verdicts.json").exists()
    # transcripts are history, not resume state — kept
    assert (config.verify_dir / "C1_transcript.jsonl").exists()


def test_invalidation_clears_codegen_sentinel(tmp_path):
    # Codegen resumes on its sentinel file; a codegen-engine change must
    # remove it so the codebase is regenerated by the new engine.
    from veritas.core.pipeline_state import PipelineState

    repo = tmp_path / "repo"; repo.mkdir(exist_ok=True)
    out = tmp_path / "out"
    config = Config(repo_path=repo, output_dir=out,
                    codegen_model="claude-sonnet-5")
    state = PipelineState(out)
    state.record_inputs(repo, None)
    state.record_config({"provider": "claude", "mode": "repo-only",
                         "claims_path": None})
    for stage in ("analyze", "plan", "codegen", "replicate", "assess_fixes", "verify"):
        state.start_stage(stage)
        state.complete_stage(stage, success=True)
    sentinel = config.codegen_complete_sentinel_path
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text("done", encoding="utf-8")

    ReplicationRunner(config)._reconcile_with_prior_run(state)

    assert not sentinel.exists()
    assert not state.is_stage_completed("codegen")
    assert state.is_stage_completed("analyze")


def test_spurious_filter_handles_capitalized_legacy_provider(tmp_path):
    recorded = {"provider": "Claude", "mode": "repo-only", "claims_path": None}
    current = build_config_fingerprint(_cfg(tmp_path))
    assert _is_spurious_engine_change("engine_analyze", recorded, current) is True


def test_manager_rerun_covers_codegen_and_resource_estimate(tmp_path):
    # A manager-requested codegen re-run must invalidate codegen itself (not
    # silently downgrade to replicate) plus everything downstream, including
    # the plan-dependent resource estimate and the codegen sentinel.
    from veritas.core.pipeline_state import PipelineState

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

    ReplicationRunner(config)._invalidate_for_rerun(state, "codegen")

    for stage in ("codegen", "plan", "resource_estimate", "replicate",
                  "assess_fixes", "verify"):
        assert not state.is_stage_completed(stage), stage
    assert not sentinel.exists()
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
