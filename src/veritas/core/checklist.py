"""Data models for personalized checklist generation."""

import json
import re
from dataclasses import dataclass, field
from typing import List, Dict, Any


@dataclass
class ChecklistItem:
    """A single YES/NO evaluation question."""

    question: str
    category: str
    weight: float = 100.0


@dataclass
class Checklist:
    """A collection of checklist items organized by category."""

    items: List[ChecklistItem] = field(default_factory=list)

    def get_items_by_category(self, category: str) -> List[ChecklistItem]:
        """Get all items for a specific category."""
        return [item for item in self.items if item.category == category]

    @property
    def categories(self) -> List[str]:
        """Get list of unique categories."""
        seen = []
        for item in self.items:
            if item.category not in seen:
                seen.append(item.category)
        return seen

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary."""
        return {
            "items": [
                {
                    "question": item.question,
                    "category": item.category,
                    "weight": item.weight,
                }
                for item in self.items
            ]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Checklist":
        """Deserialize from dictionary."""
        items = [
            ChecklistItem(
                question=item["question"],
                category=item["category"],
                weight=item.get("weight", 100.0),
            )
            for item in data["items"]
        ]
        return cls(items=items)


def _extract_json(text: str) -> str:
    """Extract JSON from text, handling markdown code blocks."""
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text.strip()


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
