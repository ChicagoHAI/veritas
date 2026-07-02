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
