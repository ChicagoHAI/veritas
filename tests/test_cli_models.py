"""CLI wiring tests: model flags reach Config."""

from types import SimpleNamespace

from typer.testing import CliRunner

import veritas.cli.main as cli_main


def test_replicate_model_flags_reach_config(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    captured = {}

    class FakeRunner:
        def __init__(self, config):
            captured["config"] = config
        def run(self, dry_run=False):
            return SimpleNamespace(success=True, report_path=None,
                                   pdf_path=None, error=None)

    monkeypatch.setattr(cli_main, "ReplicationRunner", FakeRunner)
    result = CliRunner().invoke(cli_main.app, [
        "replicate", "--repo", str(repo),
        "--model", "claude-opus-4-8",
        "--verify-model", "openrouter:openai/gpt-5.5",
        "--assess-model", "claude-sonnet-5",
    ])
    assert result.exit_code == 0, result.output
    config = captured["config"]
    assert config.model == "claude-opus-4-8"
    assert config.verify_model == "openrouter:openai/gpt-5.5"
    assert config.assess_model == "claude-sonnet-5"
    assert config.engine_for("verify") == ("openrouter", "openai/gpt-5.5")


def test_replicate_rejects_bad_spec(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    result = CliRunner().invoke(cli_main.app, [
        "replicate", "--repo", str(repo),
        "--verify-model", "openruter:x",
    ])
    assert result.exit_code == 1
    assert "unknown provider" in result.output


def test_check_citations_model_flags_reach_config(tmp_path, monkeypatch):
    replicate_dir = tmp_path / "run"
    replicate_dir.mkdir()
    paper = tmp_path / "paper.pdf"
    paper.write_bytes(b"%PDF-1.4 stub")
    captured = {}

    class FakeRunner:
        def __init__(self, config):
            captured["config"] = config
        def check_citations_existing(self):
            return SimpleNamespace(success=True, report_path=None,
                                   pdf_path=None, error=None)

    monkeypatch.setattr(cli_main, "ReplicationRunner", FakeRunner)
    result = CliRunner().invoke(cli_main.app, [
        "check-citations", str(replicate_dir),
        "--paper", str(paper),
        "--model", "claude-opus-4-8",
        "--evaluate-model", "openrouter:openai/gpt-5.5",
    ])
    assert result.exit_code == 0, result.output
    config = captured["config"]
    assert config.model == "claude-opus-4-8"
    assert config.evaluate_model == "openrouter:openai/gpt-5.5"
    assert config.engine_for("evaluate") == ("openrouter", "openai/gpt-5.5")
    assert config.run_citation_check is True
