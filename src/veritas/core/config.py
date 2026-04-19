"""Configuration for Veritas."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List


# All valid AI providers
VALID_PROVIDERS = ["claude", "codex", "gemini"]

# All available evaluation types
ALL_EVALUATIONS = [
    "code",           # Code quality evaluation
    "consistency",    # Consistency between docs, code, and claims
    "generalization", # Generalization testing
    "replication",    # Replicability assessment
    "instruction_following",    # Instruction following (for AI-generated work)
]


@dataclass
class Config:
    """Configuration for a replication evaluation run."""

    # Input paths
    repo_path: Path
    paper_path: Optional[Path] = None
    plan_path: Optional[Path] = None

    # Output settings
    output_dir: Optional[Path] = None
    generate_pdf: bool = True

    # Evaluation settings
    evaluations: Optional[List[str]] = None
    provider: str = "claude"

    # Per-phase timeouts (seconds).
    analyze_timeout: int = 300
    replicate_timeout: int = 3600
    evaluate_timeout: int = 600

    # Runtime settings
    verbose: bool = False

    # Docker / replication settings
    use_docker: bool = True
    docker_image: str = "veritas-replicator:latest"
    gpu: bool = False

    def __post_init__(self):
        # Convert paths to Path objects
        self.repo_path = Path(self.repo_path)
        if self.paper_path:
            self.paper_path = Path(self.paper_path)
        if self.plan_path:
            self.plan_path = Path(self.plan_path)
        if self.output_dir:
            self.output_dir = Path(self.output_dir)
        else:
            self.output_dir = self.repo_path / "evaluation"

        # Default to all evaluations
        if self.evaluations is None:
            self.evaluations = ALL_EVALUATIONS.copy()

        # Validate evaluations
        for e in self.evaluations:
            if e not in ALL_EVALUATIONS:
                raise ValueError(f"Unknown evaluation type: {e}. Valid options: {ALL_EVALUATIONS}")

        # Validate provider
        if self.provider.lower() not in VALID_PROVIDERS:
            raise ValueError(f"Unknown provider: {self.provider}. Valid options: {VALID_PROVIDERS}")

    @property
    def has_paper(self) -> bool:
        return self.paper_path is not None and self.paper_path.exists()

    @property
    def has_plan(self) -> bool:
        return self.plan_path is not None and self.plan_path.exists()
