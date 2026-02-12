"""Tests for runner module."""

import json
import pytest
from pathlib import Path
from veritas.core.runner import ReplicationRunner
from veritas.core.config import Config


class TestExtractJsonResult:
    def test_extracts_json_from_markdown_block(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        config = Config(repo_path=repo)
        runner = ReplicationRunner(config)

        output = '```json\n{"Checklist": {"C1": "PASS"}, "Rationale": {"C1": "OK"}}\n```'
        output_path = tmp_path / "result.json"

        assert runner._extract_json_result(output, output_path) is True
        assert output_path.exists()
        with open(output_path, encoding='utf-8') as f:
            data = json.load(f)
        assert data["Checklist"]["C1"] == "PASS"

    def test_returns_false_for_no_json(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        config = Config(repo_path=repo)
        runner = ReplicationRunner(config)

        output_path = tmp_path / "result.json"
        assert runner._extract_json_result("No JSON here", output_path) is False

    def test_writes_utf8(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        config = Config(repo_path=repo)
        runner = ReplicationRunner(config)

        output = '{"Checklist": {"C1": "PASS"}, "Rationale": {"C1": "All good \u2714"}}'
        output_path = tmp_path / "result.json"

        assert runner._extract_json_result(output, output_path) is True
        content = output_path.read_text(encoding='utf-8')
        assert "\u2714" in content
