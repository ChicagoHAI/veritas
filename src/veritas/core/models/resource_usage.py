"""dataclasses that hold the resource usage data (time, tokens, disk) for each phase and in total"""
from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class PhaseUsage:
    wall_seconds: Optional[float] = None
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class ResourceUsage:
    phases: Dict[str, PhaseUsage] = field(default_factory=dict)
    total_wall_seconds: Optional[float] = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    disk_bytes: Optional[int] = None