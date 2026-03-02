"""Tests for checklist generation data models and parsing."""

import json
import pytest
from veritas.core.checklist import ChecklistItem, Checklist, parse_checklist_response


class TestChecklistItem:
    def test_create_item(self):
        item = ChecklistItem(question="Does the code run?", category="code")
        assert item.question == "Does the code run?"
        assert item.category == "code"
        assert item.weight == 100.0

    def test_create_item_with_weight(self):
        item = ChecklistItem(question="Q?", category="code", weight=50.0)
        assert item.weight == 50.0


class TestChecklist:
    def test_create_checklist(self):
        items = [
            ChecklistItem(question="Q1?", category="code"),
            ChecklistItem(question="Q2?", category="code"),
            ChecklistItem(question="Q3?", category="consistency"),
        ]
        cl = Checklist(items=items)
        assert len(cl.items) == 3

    def test_get_items_by_category(self):
        items = [
            ChecklistItem(question="Q1?", category="code"),
            ChecklistItem(question="Q2?", category="consistency"),
            ChecklistItem(question="Q3?", category="code"),
        ]
        cl = Checklist(items=items)
        code_items = cl.get_items_by_category("code")
        assert len(code_items) == 2
        assert all(i.category == "code" for i in code_items)

    def test_categories(self):
        items = [
            ChecklistItem(question="Q1?", category="code"),
            ChecklistItem(question="Q2?", category="consistency"),
        ]
        cl = Checklist(items=items)
        assert set(cl.categories) == {"code", "consistency"}

    def test_to_dict(self):
        items = [ChecklistItem(question="Q1?", category="code")]
        cl = Checklist(items=items)
        d = cl.to_dict()
        assert "items" in d
        assert d["items"][0]["question"] == "Q1?"

    def test_from_dict(self):
        d = {
            "items": [
                {"question": "Q1?", "category": "code", "weight": 100.0},
                {"question": "Q2?", "category": "consistency", "weight": 100.0},
            ]
        }
        cl = Checklist.from_dict(d)
        assert len(cl.items) == 2
        assert cl.items[0].question == "Q1?"


class TestParseChecklistResponse:
    def test_parse_valid_json(self):
        response = json.dumps({
            "categories": {
                "code": [
                    {"question": "Does the code run without errors?"},
                    {"question": "Is the optimizer correct?"},
                ],
                "consistency": [
                    {"question": "Do results match claims?"},
                ],
            }
        })
        cl = parse_checklist_response(response)
        assert len(cl.items) == 3
        assert len(cl.get_items_by_category("code")) == 2
        assert len(cl.get_items_by_category("consistency")) == 1

    def test_parse_json_in_markdown_code_block(self):
        response = '```json\n{"categories": {"code": [{"question": "Q1?"}]}}\n```'
        cl = parse_checklist_response(response)
        assert len(cl.items) == 1

    def test_parse_empty_categories(self):
        response = json.dumps({"categories": {}})
        cl = parse_checklist_response(response)
        assert len(cl.items) == 0

    def test_parse_invalid_json_raises(self):
        with pytest.raises(ValueError, match="parse"):
            parse_checklist_response("this is not json at all")

    def test_parse_with_weights(self):
        response = json.dumps({
            "categories": {
                "code": [{"question": "Q1?", "weight": 80.0}],
            }
        })
        cl = parse_checklist_response(response)
        assert cl.items[0].weight == 80.0
