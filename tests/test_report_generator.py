"""Tests for report generator module."""

import json
import pytest
from pathlib import Path
from veritas.core.report_generator import ReportGenerator


class TestReportGenerator:
    def test_collect_results(self, tmp_path):
        """Test collecting results from new-format JSON files."""
        eval_dir = tmp_path / "evaluation"
        eval_dir.mkdir()

        code_result = {
            "items": [
                {"question": "Does code run?", "answer": "YES", "rationale": "OK"},
                {"question": "Is it correct?", "answer": "NO", "rationale": "Bug found"},
            ],
            "pass_rate": 0.5,
        }
        (eval_dir / "code_evaluation.json").write_text(json.dumps(code_result))

        consistency_result = {
            "items": [
                {"question": "Results match?", "answer": "YES", "rationale": "Verified"},
            ],
            "pass_rate": 1.0,
        }
        (eval_dir / "consistency_evaluation.json").write_text(json.dumps(consistency_result))

        generator = ReportGenerator()
        results = generator._collect_results(eval_dir)

        assert "code" in results
        assert results["code"]["success"] is True
        assert len(results["code"]["items"]) == 2
        assert results["code"]["pass_rate"] == 0.5

    def test_generate_markdown_report(self):
        results = {
            "code": {
                "success": True,
                "items": [
                    {"question": "Q1?", "answer": "YES", "rationale": "OK"},
                    {"question": "Q2?", "answer": "YES", "rationale": "OK"},
                ],
                "pass_rate": 1.0,
            },
            "consistency": {
                "success": True,
                "items": [
                    {"question": "Q3?", "answer": "NO", "rationale": "Bad"},
                ],
                "pass_rate": 0.0,
            },
        }

        generator = ReportGenerator()
        report = generator._generate_markdown_report(results)

        assert "# Replication Report" in report
        assert "Q1?" in report
        assert "Q3?" in report
        assert "Code Quality" in report
        assert "Consistency" in report

    def test_score_calculation(self):
        results = {
            "code": {
                "success": True,
                "items": [
                    {"question": "Q1?", "answer": "YES", "rationale": ""},
                    {"question": "Q2?", "answer": "YES", "rationale": ""},
                    {"question": "Q3?", "answer": "NO", "rationale": ""},
                    {"question": "Q4?", "answer": "YES", "rationale": ""},
                ],
                "pass_rate": 0.75,
            }
        }

        generator = ReportGenerator()
        report = generator._generate_markdown_report(results)
        assert "75.0%" in report

    def test_category_section_pass_rate(self):
        generator = ReportGenerator()
        data = {
            "success": True,
            "items": [
                {"question": "Q1?", "answer": "YES", "rationale": "OK"},
                {"question": "Q2?", "answer": "NO", "rationale": "Bad"},
                {"question": "Q3?", "answer": "YES", "rationale": "OK"},
            ],
            "pass_rate": 0.667,
        }
        section = generator._generate_category_section("Code Quality", data)
        assert "66.7%" in section
        assert "2/3" in section

    def test_failed_category(self):
        generator = ReportGenerator()
        data = {"success": False, "error": "Provider timeout"}
        section = generator._generate_category_section("Code Quality", data)
        assert "ERROR" in section
        assert "Provider timeout" in section

    def test_empty_items_category(self):
        generator = ReportGenerator()
        data = {"success": True, "items": [], "pass_rate": None}
        section = generator._generate_category_section("Generalization", data)
        assert "No checklist items" in section

    def test_generate_recommendations(self):
        results = {
            "code": {
                "success": True,
                "items": [
                    {"question": "Does code run?", "answer": "NO", "rationale": "Crash"},
                    {"question": "Is it correct?", "answer": "NO", "rationale": "Wrong"},
                ],
                "pass_rate": 0.0,
            },
            "replication": {
                "success": True,
                "items": [
                    {"question": "Deps documented?", "answer": "YES", "rationale": "OK"},
                ],
                "pass_rate": 1.0,
            },
        }

        generator = ReportGenerator()
        recs = generator._generate_recommendations(results)
        # Should recommend fixing code issues
        assert "code" in recs.lower() or "fail" in recs.lower()
