"""Tests for evidence parsing module."""

import json
from pathlib import Path
from veritas.core.evidence import parse_replication_plan_response, gather_evidence


class TestParseReplicationPlanResponse:
    def test_parse_valid_json(self):
        response = json.dumps({
            "environment": {"language": "python"},
            "steps": [{"id": 1, "description": "Run", "command_hint": "python run.py", "expected_outcome": "OK"}],
        })
        plan = parse_replication_plan_response(response)
        assert len(plan.steps) == 1
        assert plan.steps[0].description == "Run"

    def test_parse_json_in_markdown_block(self):
        response = """Here is the plan:
```json
{
    "environment": {"language": "python"},
    "steps": [{"id": 1, "description": "Install", "command_hint": "pip install", "expected_outcome": "OK"}]
}
```
"""
        plan = parse_replication_plan_response(response)
        assert len(plan.steps) == 1

    def test_parse_invalid_json_raises(self):
        import pytest
        with pytest.raises(ValueError, match="Could not parse"):
            parse_replication_plan_response("not json at all")


class TestGatherEvidence:
    def test_gather_from_directory(self, tmp_path):
        repl_dir = tmp_path / "replication"
        repl_dir.mkdir()

        log_data = {
            "step_outcomes": [
                {
                    "step_id": 1, "description": "Run", "command_executed": "python run.py",
                    "exit_code": 0, "stdout": "OK", "stderr": "", "output_files": [],
                    "duration_seconds": 5.0, "suggested_fix": None, "code_modified": False, "notes": "",
                }
            ]
        }
        (repl_dir / "replication_log.json").write_text(json.dumps(log_data))

        summary = {"environment": {"python_version": "3.12"}}
        (repl_dir / "evidence_summary.json").write_text(json.dumps(summary))

        evidence = gather_evidence(repl_dir)
        assert evidence is not None
        assert evidence.steps_attempted == 1
        assert evidence.environment["python_version"] == "3.12"

    def test_gather_missing_log_returns_none(self, tmp_path):
        repl_dir = tmp_path / "replication"
        repl_dir.mkdir()
        assert gather_evidence(repl_dir) is None

    def test_gather_missing_directory_returns_none(self, tmp_path):
        assert gather_evidence(tmp_path / "nonexistent") is None

    def test_gather_with_missing_summary_uses_empty_env(self, tmp_path):
        repl_dir = tmp_path / "replication"
        repl_dir.mkdir()

        log_data = {
            "step_outcomes": [
                {
                    "step_id": 1, "description": "Run", "command_executed": "python",
                    "exit_code": 0, "stdout": "", "stderr": "", "output_files": [],
                    "duration_seconds": 1.0, "suggested_fix": None, "code_modified": False, "notes": "",
                }
            ]
        }
        (repl_dir / "replication_log.json").write_text(json.dumps(log_data))

        evidence = gather_evidence(repl_dir)
        assert evidence is not None
        assert evidence.environment == {}
