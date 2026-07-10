"""Tests for model-spec parsing and per-bucket engine resolution."""

import pytest

from veritas.core.config import BUCKETS, Config, parse_model_spec


def _mk_config(tmp_path, **kwargs):
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    return Config(repo_path=repo, output_dir=tmp_path / "out", **kwargs)


# -- parse_model_spec -----------------------------------------------------

def test_parse_bare_model():
    assert parse_model_spec("claude-opus-4-8") == (None, "claude-opus-4-8")

def test_parse_provider_prefixed():
    assert parse_model_spec("openrouter:moonshotai/kimi-k2.6") == (
        "openrouter", "moonshotai/kimi-k2.6")

def test_parse_variant_suffix_is_bare_model():
    # ':free'/':online' suffixes: head contains '/', so not a provider prefix
    assert parse_model_spec("moonshotai/kimi-k2.6:free") == (
        None, "moonshotai/kimi-k2.6:free")

def test_parse_fusion_spec():
    assert parse_model_spec("openrouter:openrouter/fusion") == (
        "openrouter", "openrouter/fusion")

def test_parse_unknown_simple_prefix_raises():
    with pytest.raises(ValueError, match="unknown provider"):
        parse_model_spec("openruter:gpt-5.5")

def test_parse_empty_model_after_prefix_raises():
    with pytest.raises(ValueError, match="requires a model"):
        parse_model_spec("openrouter:")

def test_parse_empty_spec_raises():
    with pytest.raises(ValueError):
        parse_model_spec("   ")


# -- engine resolution ----------------------------------------------------

def test_default_engines_follow_global_provider(tmp_path):
    config = _mk_config(tmp_path)
    for bucket in BUCKETS:
        assert config.engine_for(bucket) == ("claude", None)

def test_global_model_applies_to_all_buckets(tmp_path):
    config = _mk_config(tmp_path, model="claude-opus-4-8")
    assert config.engine_for("verify") == ("claude", "claude-opus-4-8")
    assert config.engine_for("replicate") == ("claude", "claude-opus-4-8")

def test_bucket_spec_overrides_global(tmp_path):
    config = _mk_config(
        tmp_path, model="claude-opus-4-8",
        verify_model="openrouter:openai/gpt-5.5",
    )
    assert config.engine_for("verify") == ("openrouter", "openai/gpt-5.5")
    assert config.engine_for("analyze") == ("claude", "claude-opus-4-8")

def test_bare_bucket_spec_keeps_global_provider(tmp_path):
    config = _mk_config(tmp_path, verify_model="claude-sonnet-5")
    assert config.engine_for("verify") == ("claude", "claude-sonnet-5")

def test_env_var_fills_bucket_model(tmp_path, monkeypatch):
    monkeypatch.setenv("VERITAS_VERIFY_MODEL", "openrouter:openai/gpt-5.5")
    config = _mk_config(tmp_path)
    assert config.engine_for("verify") == ("openrouter", "openai/gpt-5.5")

def test_flag_beats_env_var(tmp_path, monkeypatch):
    monkeypatch.setenv("VERITAS_VERIFY_MODEL", "openrouter:openai/gpt-5.5")
    config = _mk_config(tmp_path, verify_model="claude-sonnet-5")
    assert config.engine_for("verify") == ("claude", "claude-sonnet-5")

def test_global_env_model(tmp_path, monkeypatch):
    monkeypatch.setenv("VERITAS_MODEL", "claude-opus-4-8")
    config = _mk_config(tmp_path)
    assert config.engine_for("assess") == ("claude", "claude-opus-4-8")

def test_unknown_bucket_raises(tmp_path):
    config = _mk_config(tmp_path)
    with pytest.raises(ValueError, match="Unknown bucket"):
        config.engine_for("report")

def test_resolved_engines_strings(tmp_path):
    config = _mk_config(tmp_path, verify_model="openrouter:openai/gpt-5.5")
    engines = config.resolved_engines()
    assert engines["verify"] == "openrouter:openai/gpt-5.5"
    assert engines["analyze"] == "claude"

def test_resolved_providers_union(tmp_path):
    config = _mk_config(tmp_path, verify_model="openrouter:openai/gpt-5.5")
    assert config.resolved_providers() == {"claude", "openrouter"}


# -- validation -----------------------------------------------------------

def test_prefixed_global_model_raises(tmp_path):
    with pytest.raises(ValueError, match="bare model"):
        _mk_config(tmp_path, model="openrouter:openai/gpt-5.5")

def test_openrouter_provider_without_model_parses(tmp_path):
    # Provisioning requirements (openrouter needs an explicit model) are
    # checked per run against the active buckets, not at Config construction
    # — a knob for a bucket that will not run must not block a run.
    config = _mk_config(tmp_path, provider="openrouter")
    assert config.engine_for("verify") == ("openrouter", None)

def test_openrouter_provider_with_model_ok(tmp_path):
    config = _mk_config(
        tmp_path, provider="openrouter", model="moonshotai/kimi-k2.6")
    assert config.engine_for("replicate") == ("openrouter", "moonshotai/kimi-k2.6")

def test_bad_env_model_fails_fast(tmp_path, monkeypatch):
    # Unlike numeric tunables, a malformed engine must not silently fall back.
    monkeypatch.setenv("VERITAS_VERIFY_MODEL", "openruter:x")
    with pytest.raises(ValueError, match="unknown provider"):
        _mk_config(tmp_path)


# -- web-locked models -------------------------------------------------------

from veritas.core.config import is_web_locked_slug


def test_web_locked_fusion():
    assert is_web_locked_slug("openrouter/fusion") is True

def test_web_locked_bare_fusion_shorthand():
    # `--replicate-model openrouter:fusion` resolves the model as bare
    # "fusion"; the lock must catch the shorthand, not only the full slug.
    assert is_web_locked_slug("fusion") is True

def test_web_locked_online_suffix():
    assert is_web_locked_slug("moonshotai/kimi-k2.6:online") is True

def test_web_locked_normal_model():
    assert is_web_locked_slug("claude-opus-4-8") is False

def test_web_locked_none():
    assert is_web_locked_slug(None) is False


def test_parse_provider_prefix_is_case_insensitive():
    assert parse_model_spec("Claude:claude-opus-4-8") == ("claude", "claude-opus-4-8")
    with pytest.raises(ValueError, match="unknown provider"):
        parse_model_spec("OpenRuter:x")


def test_citation_artifact_paths_cover_all_config_citation_outputs(tmp_path):
    # The --restart discard list must track every citation artifact Config
    # owns, or a stale prior-paper file survives for the resume gates and
    # the extraction prompt to pick up.
    from veritas.core.config import citation_artifact_paths

    cfg = _mk_config(tmp_path)
    expected = {
        cfg.citation_check_path, cfg.citation_check_meta_path,
        cfg.citation_check_transcript_path, cfg.citation_audit_path,
        cfg.citation_audit_transcript_path, cfg.references_path,
        cfg.resolver_verdicts_path, cfg.resolver_script_path,
    }
    assert set(citation_artifact_paths(cfg.output_dir)) == expected


# -- provider-native model env vars -------------------------------------------

def test_native_model_env_fills_claude_engine(tmp_path, monkeypatch):
    # ANTHROPIC_MODEL is honored as the lowest precedence level, so the
    # model it selects surfaces in provenance and the resume fingerprint
    # instead of silently repointing the CLI.
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4-8")
    config = _mk_config(tmp_path)
    assert config.engine_for("verify") == ("claude", "claude-opus-4-8")
    assert config.resolved_engines()["verify"] == "claude:claude-opus-4-8"


def test_global_model_beats_native_model_env(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-5")
    config = _mk_config(tmp_path, model="claude-opus-4-8")
    assert config.engine_for("verify") == ("claude", "claude-opus-4-8")


def test_native_model_env_scoped_to_its_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-opus-4-8")
    config = _mk_config(tmp_path, provider="codex")
    assert config.engine_for("verify") == ("codex", None)


def test_native_model_env_applies_per_resolved_provider(tmp_path, monkeypatch):
    # A bucket pinned to another provider resolves that provider's own
    # native var, not the global provider's.
    monkeypatch.setenv("OPENAI_MODEL", "gpt-5.5")
    config = _mk_config(tmp_path)
    assert config.engine_for("verify") == ("claude", None)
