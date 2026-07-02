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
