"""Tests for the shared pipeline-state readers used by post-hoc consumers
(report generation, standalone CLI subcommands) that must tolerate legacy or
damaged run directories."""

from veritas.core.pipeline_state import read_state_dict, state_file_path


def test_state_file_path_is_the_canonical_location(tmp_path):
    assert state_file_path(tmp_path) == tmp_path / ".veritas" / "pipeline_state.json"


def test_read_state_dict_absent_dir_is_empty(tmp_path):
    assert read_state_dict(tmp_path) == {}


def test_read_state_dict_damaged_file_is_empty(tmp_path):
    state_file_path(tmp_path).parent.mkdir(parents=True)
    state_file_path(tmp_path).write_text("{truncated", encoding="utf-8")
    assert read_state_dict(tmp_path) == {}


def test_read_state_dict_non_object_is_empty(tmp_path):
    state_file_path(tmp_path).parent.mkdir(parents=True)
    state_file_path(tmp_path).write_text("[1, 2]", encoding="utf-8")
    assert read_state_dict(tmp_path) == {}


def test_read_state_dict_returns_raw_dict(tmp_path):
    state_file_path(tmp_path).parent.mkdir(parents=True)
    state_file_path(tmp_path).write_text(
        '{"config": {"provider": "codex"}, "inputs": {"paper_path": "/w/p.pdf"}}',
        encoding="utf-8",
    )
    st = read_state_dict(tmp_path)
    assert st["config"]["provider"] == "codex"
    assert st["inputs"]["paper_path"] == "/w/p.pdf"
