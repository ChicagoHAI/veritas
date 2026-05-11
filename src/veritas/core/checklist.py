"""Parsing for personalized checklist responses."""

import json

from veritas.core.models.checklist import Checklist, ChecklistItem
from veritas.core.replication import _extract_json


def parse_checklist_response(response: str) -> Checklist:
    """Parse an LLM response into a Checklist.

    Expected format:
    {
        "categories": {
            "code": [{"question": "...", "weight": 100.0}, ...],
            "consistency": [{"question": "..."}, ...],
            ...
        }
    }
    """
    try:
        raw = _extract_json(response)
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as e:
        raise ValueError(f"Could not parse checklist response as JSON: {e}")

    categories = data.get("categories", {})
    items = []
    for category, questions in categories.items():
        for q in questions:
            items.append(
                ChecklistItem(
                    question=q["question"],
                    category=category,
                    weight=q.get("weight", 100.0),
                )
            )

    return Checklist(items=items)
