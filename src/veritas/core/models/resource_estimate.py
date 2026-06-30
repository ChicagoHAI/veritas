from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class ResourceEstimate:
    # From static analysis
    needs_gpu: bool = False
    external_llm: Optional[str] = None
    parallelizable: bool = False
    requires_data_download: bool = False
    key_dependencies: List[str] = field(default_factory=list)

    # From paper (extracted by LLM)
    reported_compute: Optional[str] = None   # e.g. "4 GPUs for 48 hours"
    reported_cost_usd: Optional[float] = None

    # From replication plan (extracted by LLM)
    total_steps: int = 0
    estimated_experiment_runs: Optional[int] = None  # detects loops/repetition
    estimated_llm_calls: Optional[int] = None
    compute_class: str = "light"  # light | medium | heavy
    breakdown_notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "needs_gpu": self.needs_gpu,
            "external_llm": self.external_llm,
            "parallelizable": self.parallelizable,
            "requires_data_download": self.requires_data_download,
            "reported_compute": self.reported_compute,
            "reported_cost_usd": self.reported_cost_usd,
            "total_steps": self.total_steps,
            "estimated_experiment_runs": self.estimated_experiment_runs,
            "estimated_llm_calls": self.estimated_llm_calls,
            "compute_class": self.compute_class,
            "breakdown_notes": self.breakdown_notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ResourceEstimate":
        return cls(
            needs_gpu=data.get("needs_gpu", False),
            external_llm=data.get("external_llm"),
            parallelizable=data.get("parallelizable", False),
            requires_data_download=data.get("requires_data_download", False),
            key_dependencies=data.get("key_dependencies", []),
            reported_compute=data.get("reported_compute"),
            reported_cost_usd=data.get("reported_cost_usd"),
            total_steps=data.get("total_steps", 0),
            estimated_experiment_runs=data.get("estimated_experiment_runs"),
            estimated_llm_calls=data.get("estimated_llm_calls"),
            compute_class=data.get("compute_class", "light"),
            breakdown_notes=data.get("breakdown_notes", ""),
        )

    @classmethod
    def empty(cls) -> "ResourceEstimate":
        return cls()