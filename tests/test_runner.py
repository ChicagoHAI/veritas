"""Tests for runner module."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch
from veritas.core.runner import ReplicationRunner, EvaluationResult, RunResult
from veritas.core.config import Config
from veritas.core.checklist import Checklist, ChecklistItem


class TestEvaluationResult:
    def test_create_with_items(self):
        result = EvaluationResult(
            name="code",
            success=True,
            items=[
                {"question": "Q1?", "answer": "YES", "rationale": "Works"},
                {"question": "Q2?", "answer": "NO", "rationale": "Fails"},
            ],
            pass_rate=0.5,
        )
        assert len(result.items) == 2
        assert result.pass_rate == 0.5

    def test_create_with_error(self):
        result = EvaluationResult(name="code", success=False, error="Failed")
        assert result.error == "Failed"
        assert result.items is None


class TestGenerateChecklist:
    def test_generate_checklist_with_paper(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()
        paper = tmp_path / "paper.pdf"
        paper.write_text("dummy pdf content")

        config = Config(repo_path=repo, paper_path=paper, output_dir=output)
        runner = ReplicationRunner(config)

        mock_response = json.dumps({
            "categories": {
                "code": [{"question": "Does the code run?"}],
                "consistency": [{"question": "Do results match?"}],
            }
        })

        with patch.object(runner, '_invoke_provider', return_value=mock_response):
            with patch('veritas.core.runner.read_pdf', return_value="Paper about testing"):
                checklist = runner._generate_checklist()

        assert checklist is not None
        assert len(checklist.get_items_by_category("code")) == 1
        assert (output / "checklist.json").exists()

    def test_generate_checklist_without_paper(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        config = Config(repo_path=repo, output_dir=output)
        runner = ReplicationRunner(config)

        mock_response = json.dumps({
            "categories": {
                "code": [{"question": "Does the code run?"}],
            }
        })

        with patch.object(runner, '_invoke_provider', return_value=mock_response):
            checklist = runner._generate_checklist()

        assert checklist is not None
        assert len(checklist.get_items_by_category("code")) == 1

    def test_checklist_saved_to_file(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        config = Config(repo_path=repo, output_dir=output)
        runner = ReplicationRunner(config)

        mock_response = json.dumps({
            "categories": {"code": [{"question": "Q1?"}]}
        })

        with patch.object(runner, '_invoke_provider', return_value=mock_response):
            runner._generate_checklist()

        saved = json.loads((output / "checklist.json").read_text())
        assert "items" in saved
        assert saved["items"][0]["question"] == "Q1?"

    def test_checklist_generation_failure_raises(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        config = Config(repo_path=repo, output_dir=output)
        runner = ReplicationRunner(config)

        with patch.object(runner, '_invoke_provider', return_value=None):
            with pytest.raises(RuntimeError, match="Checklist generation failed"):
                runner._generate_checklist()
