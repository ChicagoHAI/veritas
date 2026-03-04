"""Integration test for the personalized checklist pipeline."""

import json
import pytest
from pathlib import Path
from unittest.mock import patch
from veritas.core.config import Config
from veritas.core.runner import ReplicationRunner


class TestChecklistPipelineIntegration:
    def test_full_pipeline_with_paper(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "train.py").write_text("print('hello')")

        paper = tmp_path / "paper.pdf"
        paper.write_text("dummy pdf")

        output = tmp_path / "output"

        config = Config(
            repo_path=repo, paper_path=paper, output_dir=output,
            evaluations=["code", "consistency"], generate_pdf=False,
            use_docker=False,
        )
        runner = ReplicationRunner(config)

        checklist_response = json.dumps({
            "categories": {
                "code": [
                    {"question": "Does train.py run without errors?"},
                    {"question": "Is the output correct?"},
                ],
                "consistency": [
                    {"question": "Do results match the paper's claims?"},
                ],
            }
        })

        replication_plan_response = json.dumps({
            "environment": {"language": "python"},
            "steps": [{"id": 1, "description": "Run train.py", "command_hint": "python train.py", "expected_outcome": "OK"}],
        })

        code_scoring = json.dumps({
            "items": [
                {"question": "Does train.py run without errors?", "answer": "YES", "rationale": "Runs fine"},
                {"question": "Is the output correct?", "answer": "NO", "rationale": "Output is just hello"},
            ],
            "pass_rate": 0.5,
        })

        consistency_scoring = json.dumps({
            "items": [
                {"question": "Do results match the paper's claims?", "answer": "YES", "rationale": "Verified"},
            ],
            "pass_rate": 1.0,
        })

        call_count = 0

        def mock_invoke(prompt, working_dir, output_path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                output_path.write_text(checklist_response)
                return checklist_response
            elif call_count == 2:
                output_path.write_text(replication_plan_response)
                return replication_plan_response
            elif call_count == 3:
                output_path.write_text(code_scoring)
                return code_scoring
            elif call_count == 4:
                output_path.write_text(consistency_scoring)
                return consistency_scoring
            return None

        with patch.object(runner, '_invoke_provider', side_effect=mock_invoke), \
             patch('veritas.core.runner.read_pdf', return_value="Paper about testing"), \
             patch.object(runner.plan_extractor, '_read_pdf', return_value=("Paper about testing", [])):
                result = runner.run()

        assert result.success
        assert result.report_path.exists()

        report = result.report_path.read_text()
        assert "Does train.py run without errors?" in report
        assert (output / "checklist.json").exists()

    def test_full_pipeline_without_paper(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "main.py").write_text("print('test')")

        output = tmp_path / "output"

        config = Config(
            repo_path=repo, output_dir=output,
            evaluations=["code"], generate_pdf=False,
            use_docker=False,
        )
        runner = ReplicationRunner(config)

        checklist_response = json.dumps({
            "categories": {
                "code": [{"question": "Does main.py execute?"}],
            }
        })

        replication_plan_response = json.dumps({
            "environment": {"language": "python"},
            "steps": [{"id": 1, "description": "Run main.py", "command_hint": "python main.py", "expected_outcome": "OK"}],
        })

        code_scoring = json.dumps({
            "items": [
                {"question": "Does main.py execute?", "answer": "YES", "rationale": "OK"},
            ],
            "pass_rate": 1.0,
        })

        call_count = 0

        def mock_invoke(prompt, working_dir, output_path):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                output_path.write_text(checklist_response)
                return checklist_response
            elif call_count == 2:
                output_path.write_text(replication_plan_response)
                return replication_plan_response
            elif call_count == 3:
                output_path.write_text(code_scoring)
                return code_scoring
            return None

        with patch.object(runner, '_invoke_provider', side_effect=mock_invoke):
            result = runner.run()

        assert result.success
        report = result.report_path.read_text()
        assert "Does main.py execute?" in report
        assert "100.0%" in report
