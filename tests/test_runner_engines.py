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
