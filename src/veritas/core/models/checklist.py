"""Checklist dataclasses for personalized evaluation questions."""

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
