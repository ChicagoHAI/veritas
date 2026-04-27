"""Configuration for Veritas."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List


# All valid AI providers
VALID_PROVIDERS = ["claude", "codex", "gemini"]

# Valid replication scope modes
VALID_MODES = ["main", "full"]

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
    mode: str = "main"

    # Per-phase timeouts (seconds); None disables the timeout for that phase.
    # Defaults are None — killing a hung run discards partial progress, which
    # is worse than letting it finish. Re-enable once there's a checkpoint /
    # resume mechanism to recover the work.
    analyze_timeout: Optional[int] = None
    replicate_timeout: Optional[int] = None
    evaluate_timeout: Optional[int] = None

    # Runtime settings
    verbose: bool = False

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

        # Validate mode
        if self.mode not in VALID_MODES:
            raise ValueError(f"Unknown mode: {self.mode}. Valid options: {VALID_MODES}")
        if self.mode == "full":
            raise NotImplementedError("--mode full is not yet implemented. Use --mode main (default).")

    @property
    def has_paper(self) -> bool:
        return self.paper_path is not None and self.paper_path.exists()

    @property
    def has_plan(self) -> bool:
        return self.plan_path is not None and self.plan_path.exists()
