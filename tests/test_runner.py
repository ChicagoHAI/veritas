"""Tests for runner module."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch
from veritas.core.runner import ReplicationRunner, EvaluationResult, RunResult
from veritas.core.config import Config
from veritas.core.checklist import Checklist, ChecklistItem
from veritas.core.models import ReplicationPlan, ReplicationStep, ExecutionEvidence, StepOutcome


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


class TestAnalyzePhase:
    def test_analyze_produces_checklist_and_plan(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        config = Config(repo_path=repo, output_dir=output)
        runner = ReplicationRunner(config)

        checklist_response = json.dumps({
            "categories": {"code": [{"question": "Does it run?"}]}
        })
        plan_response = json.dumps({
            "environment": {"language": "python"},
            "steps": [{"id": 1, "description": "Install", "command_hint": "pip install", "expected_outcome": "OK"}],
        })

        call_count = 0
        def mock_invoke(prompt, working_dir, output_path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return checklist_response
            elif call_count == 2:
                output_path.write_text(plan_response)
                return plan_response
            return None

        with patch.object(runner, '_invoke_provider', side_effect=mock_invoke):
            checklist, replication_plan = runner._analyze()

        assert checklist is not None
        assert len(checklist.items) == 1
        assert replication_plan is not None
        assert len(replication_plan.steps) == 1


class TestReplicatePhase:
    def test_replicate_skips_when_no_docker(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        config = Config(repo_path=repo, output_dir=output, use_docker=False)
        runner = ReplicationRunner(config)

        plan = ReplicationPlan(environment={}, steps=[])
        evidence = runner._replicate(plan)
        assert evidence is None

    def test_replicate_skips_when_docker_unavailable(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        config = Config(repo_path=repo, output_dir=output, use_docker=True)
        runner = ReplicationRunner(config)

        plan = ReplicationPlan(environment={}, steps=[])

        with patch('veritas.core.runner.is_docker_available', return_value=False):
            evidence = runner._replicate(plan)

        assert evidence is None


class TestEvaluatePhase:
    def test_evaluate_passes_evidence_to_scoring(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        config = Config(repo_path=repo, output_dir=output, evaluations=["code"])
        runner = ReplicationRunner(config)

        from veritas.core.checklist import Checklist, ChecklistItem
        checklist = Checklist(items=[ChecklistItem(question="Does it run?", category="code")])
        evidence = ExecutionEvidence(
            environment={"python_version": "3.12"},
            step_outcomes=[
                StepOutcome(step_id=1, description="Run", command_executed="python run.py",
                            exit_code=0, stdout="OK", stderr="", output_files=[],
                            duration_seconds=5.0, suggested_fix=None, code_modified=False, notes=""),
            ],
        )

        scoring_response = json.dumps({
            "items": [{"question": "Does it run?", "answer": "YES", "rationale": "Step 1 succeeded"}],
            "pass_rate": 1.0,
        })

        def mock_invoke(prompt, working_dir, output_path):
            assert "Execution Evidence" in prompt
            output_path.write_text(scoring_response)
            return scoring_response

        with patch.object(runner, '_invoke_provider', side_effect=mock_invoke):
            results = runner._evaluate(checklist, evidence, plan_path=None)

        assert len(results) == 1
        assert results[0].success
        assert results[0].pass_rate == 1.0
