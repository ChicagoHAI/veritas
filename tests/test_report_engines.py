"""Report provenance: engines recorded in pipeline state reach the report."""

import json

from veritas.core.report_generator import read_engines_from_state


def test_reads_engines_from_state(tmp_path):
    state_dir = tmp_path / ".veritas"
    state_dir.mkdir()
    (state_dir / "pipeline_state.json").write_text(json.dumps({
        "config": {
            "provider": "claude",
            "engine_verify": "openrouter:openai/gpt-5.5",
            "engine_replicate": "claude:claude-opus-4-8",
        }
    }), encoding="utf-8")
    engines = read_engines_from_state(tmp_path)
    assert engines == {
        "replicate": "claude:claude-opus-4-8",
        "verify": "openrouter:openai/gpt-5.5",
    }


def test_missing_state_returns_empty(tmp_path):
    assert read_engines_from_state(tmp_path) == {}
    assert read_engines_from_state(None) == {}


def test_corrupt_state_returns_empty(tmp_path):
    state_dir = tmp_path / ".veritas"
    state_dir.mkdir()
    (state_dir / "pipeline_state.json").write_text("{not json",
                                                   encoding="utf-8")
    assert read_engines_from_state(tmp_path) == {}


def test_state_without_engine_keys_returns_empty(tmp_path):
    state_dir = tmp_path / ".veritas"
    state_dir.mkdir()
    (state_dir / "pipeline_state.json").write_text(json.dumps({
        "config": {"provider": "claude", "mode": "repo-only"}
    }), encoding="utf-8")
    assert read_engines_from_state(tmp_path) == {}


def test_engine_provenance_reaches_markdown_and_html(tmp_path):
    # The Models header must land in every rendered format; the PDF is
    # generated from the HTML, so covering HTML covers it too.
    from veritas.core.report_generator import ReportGenerator

    state_dir = tmp_path / ".veritas"
    state_dir.mkdir()
    (state_dir / "pipeline_state.json").write_text(json.dumps({
        "config": {
            "provider": "claude",
            "engine_verify": "openrouter:openai/gpt-5.5",
        }
    }), encoding="utf-8")

    md_path, _ = ReportGenerator().generate(tmp_path, generate_pdf=False)
    assert "verify: openrouter:openai/gpt-5.5" in md_path.read_text(encoding="utf-8")
    html = (tmp_path / "report" / "replication_report.html").read_text(encoding="utf-8")
    assert "verify: openrouter:openai/gpt-5.5" in html
