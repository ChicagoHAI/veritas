from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class ResourceEstimate:
    # From static analysis (always populated programmatically)
    needs_gpu: bool = False
    external_llm: Optional[str] = None
    parallelizable: bool = False

    # From LLM pass (only fields the code reads; everything else stays in raw JSON on disk)
    compute_class: str = "light"  # light | medium | heavy
    breakdown_notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "needs_gpu": self.needs_gpu,
            "external_llm": self.external_llm,
            "parallelizable": self.parallelizable,
            "compute_class": self.compute_class,
            "breakdown_notes": self.breakdown_notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResourceEstimate":
        return cls(
            needs_gpu=data.get("needs_gpu", False),
            external_llm=data.get("external_llm"),
            parallelizable=data.get("parallelizable", False),
            compute_class=data.get("compute_class", "light"),
            breakdown_notes=data.get("breakdown_notes", ""),
        )

    @classmethod
    def empty(cls) -> "ResourceEstimate":
        return cls()
