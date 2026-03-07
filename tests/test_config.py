"""Tests for configuration module."""

import pytest
from pathlib import Path
from veritas.core.config import Config, ALL_EVALUATIONS, VALID_PROVIDERS


class TestConfig:
    """Tests for Config class."""

    def test_default_evaluations(self, tmp_path):
        """Test that all evaluations are included by default."""
        repo = tmp_path / "repo"
        repo.mkdir()

        config = Config(repo_path=repo)

        assert config.evaluations == ALL_EVALUATIONS
        assert "code" in config.evaluations
        assert "consistency" in config.evaluations
        assert "generalization" in config.evaluations
        assert "replication" in config.evaluations
        assert "instruction_following" in config.evaluations

    def test_custom_evaluations(self, tmp_path):
        """Test custom evaluation selection."""
        repo = tmp_path / "repo"
        repo.mkdir()

        config = Config(
            repo_path=repo,
            evaluations=["code", "consistency"]
        )

        assert config.evaluations == ["code", "consistency"]

    def test_invalid_evaluation(self, tmp_path):
        """Test that invalid evaluation types raise error."""
        repo = tmp_path / "repo"
        repo.mkdir()

        with pytest.raises(ValueError, match="Unknown evaluation type"):
            Config(repo_path=repo, evaluations=["invalid"])

    def test_output_dir_default(self, tmp_path):
        """Test default output directory."""
        repo = tmp_path / "repo"
        repo.mkdir()

        config = Config(repo_path=repo)

        assert config.output_dir == repo / "evaluation"

    def test_output_dir_custom(self, tmp_path):
        """Test custom output directory."""
        repo = tmp_path / "repo"
        repo.mkdir()
        output = tmp_path / "output"

        config = Config(repo_path=repo, output_dir=output)

        assert config.output_dir == output

    def test_has_paper(self, tmp_path):
        """Test paper detection."""
        repo = tmp_path / "repo"
        repo.mkdir()
        paper = tmp_path / "paper.pdf"
        paper.write_text("fake pdf")

        config_no_paper = Config(repo_path=repo)
        assert not config_no_paper.has_paper

        config_with_paper = Config(repo_path=repo, paper_path=paper)
        assert config_with_paper.has_paper

    def test_has_plan(self, tmp_path):
        """Test plan detection."""
        repo = tmp_path / "repo"
        repo.mkdir()
        plan = tmp_path / "plan.md"
        plan.write_text("# Plan")

        config_no_plan = Config(repo_path=repo)
        assert not config_no_plan.has_plan

        config_with_plan = Config(repo_path=repo, plan_path=plan)
        assert config_with_plan.has_plan

    def test_invalid_provider(self, tmp_path):
        """Test that invalid provider raises error."""
        repo = tmp_path / "repo"
        repo.mkdir()

        with pytest.raises(ValueError, match="Unknown provider"):
            Config(repo_path=repo, provider="invalid_provider")

    def test_valid_providers(self, tmp_path):
        """Test that all valid providers are accepted."""
        repo = tmp_path / "repo"
        repo.mkdir()

        for provider in VALID_PROVIDERS:
            config = Config(repo_path=repo, provider=provider)
            assert config.provider == provider


class TestDockerConfig:
    def test_default_docker_settings(self, tmp_path):
        config = Config(repo_path=tmp_path)
        assert config.use_docker is True
        assert config.docker_image == "veritas-replicator:latest"
        assert config.replication_timeout == 3600
        assert config.gpu is True

    def test_custom_docker_settings(self, tmp_path):
        config = Config(
            repo_path=tmp_path,
            use_docker=False,
            docker_image="custom:v1",
            replication_timeout=7200,
            gpu=False,
        )
        assert config.use_docker is False
        assert config.docker_image == "custom:v1"
        assert config.replication_timeout == 7200
        assert config.gpu is False
