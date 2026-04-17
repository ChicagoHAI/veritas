"""Tests for prompt generator module."""

import pytest
from pathlib import Path
from veritas.templates.prompt_generator import PromptGenerator
from veritas.core.checklist import ChecklistItem
from veritas.core.models import ReplicationPlan, ReplicationStep, ExecutionEvidence, StepOutcome


class TestPromptGenerator:
    """Tests for PromptGenerator class."""

    def test_generate_checklist_prompt_with_paper(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        gen = PromptGenerator()
        prompt = gen.generate_checklist_prompt(
            repo_path=repo,
            output_dir=output,
            paper_path=Path("/some/paper.pdf"),
        )
        assert "paper.pdf" in prompt
        assert "read the PDF directly" in prompt
        assert str(repo.absolute()) in prompt
        assert "checklist" in prompt.lower()
        assert "Code Quality" in prompt
        assert "Consistency" in prompt

    def test_generate_checklist_prompt_without_paper(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        gen = PromptGenerator()
        prompt = gen.generate_checklist_prompt(
            repo_path=repo,
            output_dir=output,
        )
        assert str(repo.absolute()) in prompt
        assert "checklist" in prompt.lower()
        assert "No paper was provided" in prompt
        assert "Paper Content" not in prompt

    def test_generate_scoring_prompt(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        gen = PromptGenerator()
        items = [
            ChecklistItem(question="Does train.py run?", category="code"),
            ChecklistItem(question="Is the optimizer Adam?", category="code"),
        ]
        prompt = gen.generate_scoring_prompt(
            category_name="code",
            checklist_items=items,
            repo_path=repo,
            plan_path=None,
            output_dir=output,
        )
        assert "Does train.py run?" in prompt
        assert "Is the optimizer Adam?" in prompt
        assert "code_evaluation.json" in prompt
        assert str(repo.absolute()) in prompt
        assert "Code Quality" in prompt

    def test_generate_scoring_prompt_with_plan(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()
        plan = tmp_path / "plan.md"
        plan.write_text("# Plan")

        gen = PromptGenerator()
        items = [ChecklistItem(question="Q1?", category="consistency")]

        prompt = gen.generate_scoring_prompt(
            category_name="consistency",
            checklist_items=items,
            repo_path=repo,
            plan_path=plan,
            output_dir=output,
        )
        assert str(plan.absolute()) in prompt

    def test_scoring_prompt_category_display_names(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        gen = PromptGenerator()
        for category, display in [
            ("code", "Code Quality"),
            ("consistency", "Consistency"),
            ("generalization", "Generalization"),
            ("replication", "Replicability"),
            ("instruction_following", "Instruction Following"),
        ]:
            items = [ChecklistItem(question="Q?", category=category)]
            prompt = gen.generate_scoring_prompt(
                category_name=category,
                checklist_items=items,
                repo_path=repo,
                plan_path=None,
                output_dir=output,
            )
            assert display in prompt


class TestReplicationPrompts:
    def test_generate_replication_plan_prompt_with_paper(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        pg = PromptGenerator()
        prompt = pg.generate_replication_plan_prompt(
            repo_path=repo,
            output_dir=output,
            paper_path=Path("/some/paper.pdf"),
            checklist_items=[
                ChecklistItem(question="Does training run?", category="code"),
                ChecklistItem(question="Is accuracy 92%?", category="consistency"),
            ],
        )
        assert "paper.pdf" in prompt
        assert "read the PDF directly" in prompt
        assert str(repo.absolute()) in prompt
        assert "Does training run?" in prompt
        assert "replication_plan.json" in prompt

    def test_generate_replication_plan_prompt_without_paper(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        pg = PromptGenerator()
        prompt = pg.generate_replication_plan_prompt(
            repo_path=repo,
            output_dir=output,
            checklist_items=[
                ChecklistItem(question="Does the code run?", category="code"),
            ],
        )
        assert "No paper was provided" in prompt
        assert "Does the code run?" in prompt

    def test_generate_replication_session_prompt(self):
        pg = PromptGenerator()
        plan = ReplicationPlan(
            environment={"language": "python"},
            steps=[
                ReplicationStep(id=1, description="Install deps", command_hint="pip install", expected_outcome="OK"),
                ReplicationStep(id=2, description="Run train", command_hint="python train.py", expected_outcome="Done"),
            ],
        )
        prompt = pg.generate_replication_session_prompt(plan)
        assert "replication agent" in prompt.lower()
        assert "Install deps" in prompt
        assert "Run train" in prompt
        assert "pip install" in prompt
        assert "python train.py" in prompt
        assert "replication_log.json" in prompt


class TestScoringWithEvidence:
    def test_scoring_prompt_with_evidence(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        pg = PromptGenerator()
        evidence = ExecutionEvidence(
            environment={"python_version": "3.12.0", "gpu_available": False},
            step_outcomes=[
                StepOutcome(
                    step_id=1, description="Install deps",
                    command_executed="pip install -r requirements.txt",
                    exit_code=0, stdout="Installed OK", stderr="",
                    output_files=[], duration_seconds=10.0,
                    suggested_fix=None, code_modified=False, notes="",
                ),
                StepOutcome(
                    step_id=2, description="Run eval",
                    command_executed="python eval.py",
                    exit_code=1, stdout="", stderr="FileNotFoundError",
                    output_files=[], duration_seconds=2.0,
                    suggested_fix="Fix path", code_modified=False, notes="",
                ),
            ],
        )
        prompt = pg.generate_scoring_prompt(
            category_name="code",
            checklist_items=[ChecklistItem(question="Does it run?", category="code")],
            repo_path=repo,
            plan_path=None,
            output_dir=output,
            evidence=evidence,
        )
        assert "Execution Evidence" in prompt
        assert "Install deps" in prompt
        assert "FileNotFoundError" in prompt
        assert "Evidence first" in prompt or "evidence" in prompt.lower()

    def test_scoring_prompt_without_evidence(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"
        output.mkdir()

        pg = PromptGenerator()
        prompt = pg.generate_scoring_prompt(
            category_name="code",
            checklist_items=[ChecklistItem(question="Does it run?", category="code")],
            repo_path=repo,
            plan_path=None,
            output_dir=output,
            evidence=None,
        )
        assert "Execution Evidence" not in prompt
        assert "Actually run the code" in prompt
