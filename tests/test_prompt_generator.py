"""Tests for prompt generator module."""

import pytest
from pathlib import Path
from veritas.templates.prompt_generator import PromptGenerator


class TestPromptGenerator:
    """Tests for PromptGenerator class."""

    def test_generate_code_evaluation_prompt(self, tmp_path):
        """Test code evaluation prompt generation."""
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"

        generator = PromptGenerator()
        prompt = generator.generate_evaluation_prompt(
            eval_type="code",
            repo_path=repo,
            output_dir=output
        )

        assert "Code" in prompt or "code" in prompt
        assert str(repo) in prompt
        assert "C1" in prompt
        assert "C2" in prompt
        assert "C3" in prompt
        assert "C4" in prompt
        assert "Runnable" in prompt or "runnable" in prompt

    def test_generate_consistency_evaluation_prompt(self, tmp_path):
        """Test consistency evaluation prompt generation."""
        repo = tmp_path / "repo"
        repo.mkdir()

        generator = PromptGenerator()
        prompt = generator.generate_evaluation_prompt(
            eval_type="consistency",
            repo_path=repo
        )

        assert "Consistency" in prompt or "consistency" in prompt
        assert "CS1" in prompt
        assert "CS5" in prompt

    def test_generate_generalization_evaluation_prompt(self, tmp_path):
        """Test generalization evaluation prompt generation."""
        repo = tmp_path / "repo"
        repo.mkdir()

        generator = PromptGenerator()
        prompt = generator.generate_evaluation_prompt(
            eval_type="generalization",
            repo_path=repo
        )

        assert "Generalization" in prompt or "generalization" in prompt
        assert "GT1" in prompt
        assert "GT2" in prompt
        assert "GT3" in prompt

    def test_generate_replication_evaluation_prompt(self, tmp_path):
        """Test replication evaluation prompt generation."""
        repo = tmp_path / "repo"
        repo.mkdir()

        generator = PromptGenerator()
        prompt = generator.generate_evaluation_prompt(
            eval_type="replication",
            repo_path=repo
        )

        assert "Replication" in prompt or "replication" in prompt
        assert "RP1" in prompt
        assert "RP2" in prompt
        assert "RP3" in prompt

    def test_generate_instruction_evaluation_prompt(self, tmp_path):
        """Test instruction evaluation prompt generation."""
        repo = tmp_path / "repo"
        repo.mkdir()

        generator = PromptGenerator()
        prompt = generator.generate_evaluation_prompt(
            eval_type="instruction",
            repo_path=repo
        )

        assert "Instruction" in prompt or "instruction" in prompt
        assert "TS1" in prompt
        assert "TS4" in prompt

    def test_invalid_evaluation_type(self, tmp_path):
        """Test that invalid evaluation type raises error."""
        repo = tmp_path / "repo"
        repo.mkdir()

        generator = PromptGenerator()

        with pytest.raises(ValueError, match="Unknown evaluation type"):
            generator.generate_evaluation_prompt(
                eval_type="invalid",
                repo_path=repo
            )

    def test_prompt_includes_plan_path(self, tmp_path):
        """Test that plan path is included when provided."""
        repo = tmp_path / "repo"
        repo.mkdir()
        plan = tmp_path / "plan.md"
        plan.write_text("# Research Plan")

        generator = PromptGenerator()
        prompt = generator.generate_evaluation_prompt(
            eval_type="code",
            repo_path=repo,
            plan_path=plan
        )

        assert str(plan) in prompt
