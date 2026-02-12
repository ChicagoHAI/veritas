"""Tests for JSON utilities."""

import pytest
from veritas.utils.json_utils import extract_json_from_text


class TestExtractJsonFromText:
    def test_extracts_from_markdown_code_block(self):
        text = 'Here is the result:\n```json\n{"Checklist": {"C1": "PASS"}}\n```\nDone.'
        result = extract_json_from_text(text)
        assert result is not None
        assert result["Checklist"]["C1"] == "PASS"

    def test_extracts_from_bare_json(self):
        text = 'Some preamble.\n{"Checklist": {"C1": "FAIL"}, "Rationale": {"C1": "Broken"}}\nSome postamble.'
        result = extract_json_from_text(text)
        assert result is not None
        assert result["Checklist"]["C1"] == "FAIL"

    def test_handles_nested_braces(self):
        text = '{"Checklist": {"C1": "PASS"}, "Rationale": {"C1": "It works {well}"}}'
        result = extract_json_from_text(text)
        assert result is not None
        assert result["Checklist"]["C1"] == "PASS"

    def test_returns_none_for_no_json(self):
        assert extract_json_from_text("No JSON here at all") is None

    def test_returns_none_for_invalid_json(self):
        assert extract_json_from_text("{not valid json}") is None

    def test_prefers_checklist_json_over_other_objects(self):
        text = '{"irrelevant": true}\n{"Checklist": {"C1": "PASS"}, "Rationale": {}}'
        result = extract_json_from_text(text)
        assert result is not None
        assert "Checklist" in result
        assert "irrelevant" not in result

    def test_greedy_regex_bug_fixed(self):
        """The old greedy regex would match from first { to last } across unrelated JSON."""
        text = (
            'Some text {"other": "data"} more text\n'
            '```json\n{"Checklist": {"C1": "PASS"}, "Rationale": {"C1": "Good"}}\n```\n'
            'Even more text {"another": "object"}'
        )
        result = extract_json_from_text(text)
        assert result is not None
        assert "Checklist" in result
        assert "other" not in result
        assert "another" not in result
