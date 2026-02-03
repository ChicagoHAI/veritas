"""Tests for report generator module."""

import json
import pytest
from pathlib import Path
from veritas.core.report_generator import ReportGenerator


class TestReportGenerator:
    """Tests for ReportGenerator class."""

    def test_collect_results(self, tmp_path):
        """Test collecting results from JSON files."""
        # Create evaluation directory with JSON files
        eval_dir = tmp_path / "evaluation"
        eval_dir.mkdir()

        code_result = {
            "Checklist": {"C1": "PASS", "C2": "PASS", "C3": "FAIL", "C4": "PASS"},
            "Rationale": {"C1": "All code runs", "C2": "Logic correct", "C3": "Some duplication", "C4": "All relevant"},
            "Metrics": {"runnable_pct": 100, "total_blocks": 10}
        }
        (eval_dir / "code_evaluation.json").write_text(json.dumps(code_result))

        consistency_result = {
            "Checklist": {"CS1": "PASS", "CS2": "FAIL", "CS3": "PASS", "CS4": "PASS", "CS5": "NA"},
            "Rationale": {"CS1": "Results match", "CS2": "Plan differs", "CS3": "Effects significant", "CS4": "Justified", "CS5": "Not applicable"}
        }
        (eval_dir / "consistency_evaluation.json").write_text(json.dumps(consistency_result))

        generator = ReportGenerator()
        results = generator._collect_results(eval_dir)

        assert "code" in results
        assert results["code"]["success"] is True
        assert results["code"]["checklist"]["C1"] == "PASS"
        assert results["code"]["checklist"]["C3"] == "FAIL"

        assert "consistency" in results
        assert results["consistency"]["checklist"]["CS2"] == "FAIL"

    def test_generate_markdown_report(self, tmp_path):
        """Test markdown report generation."""
        results = {
            "code": {
                "success": True,
                "checklist": {"C1": "PASS", "C2": "PASS", "C3": "PASS", "C4": "PASS"},
                "rationale": {"C1": "All runs", "C2": "Correct", "C3": "No duplication", "C4": "Relevant"},
                "metrics": {"runnable_pct": 100}
            },
            "consistency": {
                "success": True,
                "checklist": {"CS1": "PASS", "CS2": "PASS", "CS3": "FAIL", "CS4": "PASS", "CS5": "PASS"},
                "rationale": {}
            }
        }

        generator = ReportGenerator()
        report = generator._generate_markdown_report(results)

        assert "# Replication Report" in report
        assert "Executive Summary" in report
        assert "Code Quality" in report
        assert "Consistency" in report
        assert "PASS" in report

    def test_score_calculation(self, tmp_path):
        """Test that score is calculated correctly."""
        results = {
            "code": {
                "success": True,
                "checklist": {"C1": "PASS", "C2": "PASS", "C3": "FAIL", "C4": "PASS"},
                "rationale": {},
                "metrics": {}
            }
        }

        generator = ReportGenerator()
        report = generator._generate_markdown_report(results)

        # 3/4 = 75%
        assert "75.0%" in report

    def test_na_not_counted(self, tmp_path):
        """Test that NA items are not counted in score."""
        results = {
            "generalization": {
                "success": True,
                "checklist": {"GT1": "PASS", "GT2": "PASS", "GT3": "NA"},
                "rationale": {},
                "metrics": {}
            }
        }

        generator = ReportGenerator()
        report = generator._generate_markdown_report(results)

        # 2/2 = 100% (GT3 NA not counted)
        assert "100.0%" in report

    def test_generate_recommendations(self, tmp_path):
        """Test recommendation generation for failed checks."""
        results = {
            "code": {
                "success": True,
                "checklist": {"C1": "FAIL", "C2": "PASS", "C3": "PASS", "C4": "PASS"},
                "rationale": {},
                "metrics": {}
            },
            "replication": {
                "success": True,
                "checklist": {"RP1": "PASS", "RP2": "FAIL", "RP3": "FAIL"},
                "rationale": {},
                "metrics": {}
            }
        }

        generator = ReportGenerator()
        recommendations = generator._generate_recommendations(results)

        assert "code execution errors" in recommendations.lower()
        assert "environment" in recommendations.lower() or "random seeds" in recommendations.lower()
