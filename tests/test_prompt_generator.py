"""Tests for prompt generator module."""

import pytest
from pathlib import Path
from veritas.templates.prompt_generator import PromptGenerator
from veritas.core.checklist import ChecklistItem


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
            paper_text="This paper studies sentiment analysis using BERT.",
        )
        assert "sentiment analysis" in prompt
        assert str(repo.absolute()) in prompt
        assert "checklist" in prompt.lower()
        assert "Code Quality" in prompt
        assert "Consistency" in prompt
        assert "Paper Content" in prompt

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
            ("instruction", "Instruction Following"),
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
