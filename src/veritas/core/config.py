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


# Output directory structure — each phase writes into its own subdir.
ANALYZE_SUBDIR = "analyze"
REPLICATION_SUBDIR = "replication"
EVALUATE_SUBDIR = "evaluate"
REPORT_SUBDIR = "report"
PROMPTS_SUBDIR = "prompts"

OUTPUT_SUBDIRS = (
    ANALYZE_SUBDIR,
    REPLICATION_SUBDIR,
    EVALUATE_SUBDIR,
    REPORT_SUBDIR,
    PROMPTS_SUBDIR,
)


# Well-known output filenames produced by the pipeline.
CHECKLIST_FILE = "checklist.json"
REPLICATION_PLAN_FILE = "replication_plan.json"
EXTRACTED_PLAN_FILE = "extracted_plan.md"
FIX_SEVERITY_FILE = "fix_severity.json"
REPORT_MD_FILE = "replication_report.md"
REPORT_PDF_FILE = "replication_report.pdf"

# Per-category evaluation files: ``<category>_evaluation.json`` under
# ``<output>/evaluate/``. The suffix is also referenced in
# ``templates/evaluation/scoring.txt``.
EVALUATION_FILE_SUFFIX = "_evaluation.json"

# Per-phase JSONL transcripts of the agent's streaming output. Each provider
# invocation writes its event stream to one of these files; on a parse-repair
# re-invocation, events are appended to the same file so the failed attempt
# and the repair attempt land in one place.
CHECKLIST_TRANSCRIPT_FILE = "checklist_transcript.jsonl"
REPLICATION_PLAN_TRANSCRIPT_FILE = "replication_plan_transcript.jsonl"
REPLICATION_TRANSCRIPT_FILE = "transcript.jsonl"
FIX_SEVERITY_TRANSCRIPT_FILE = "fix_severity_transcript.jsonl"
EVALUATION_TRANSCRIPT_FILE_SUFFIX = "_transcript.jsonl"


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

    # -- Output subdirectories ----------------------------------------------

    @property
    def analyze_dir(self) -> Path:
        return self.output_dir / ANALYZE_SUBDIR

    @property
    def replication_dir(self) -> Path:
        return self.output_dir / REPLICATION_SUBDIR

    @property
    def evaluate_dir(self) -> Path:
        return self.output_dir / EVALUATE_SUBDIR

    @property
    def report_dir(self) -> Path:
        return self.output_dir / REPORT_SUBDIR

    @property
    def prompts_dir(self) -> Path:
        return self.output_dir / PROMPTS_SUBDIR

    # -- Well-known output files --------------------------------------------

    @property
    def checklist_path(self) -> Path:
        return self.analyze_dir / CHECKLIST_FILE

    @property
    def replication_plan_path(self) -> Path:
        return self.analyze_dir / REPLICATION_PLAN_FILE

    @property
    def extracted_plan_path(self) -> Path:
        return self.analyze_dir / EXTRACTED_PLAN_FILE

    @property
    def fix_severity_path(self) -> Path:
        return self.evaluate_dir / FIX_SEVERITY_FILE

    @property
    def report_md_path(self) -> Path:
        return self.report_dir / REPORT_MD_FILE

    @property
    def report_pdf_path(self) -> Path:
        return self.report_dir / REPORT_PDF_FILE

    def evaluation_path(self, category: str) -> Path:
        """Path to the per-category evaluation JSON, e.g. ``code_evaluation.json``."""
        return self.evaluate_dir / f"{category}{EVALUATION_FILE_SUFFIX}"

    # -- Transcript files (JSONL streamed from provider invocations) -------

    @property
    def checklist_transcript_path(self) -> Path:
        return self.analyze_dir / CHECKLIST_TRANSCRIPT_FILE

    @property
    def replication_plan_transcript_path(self) -> Path:
        return self.analyze_dir / REPLICATION_PLAN_TRANSCRIPT_FILE

    @property
    def replication_transcript_path(self) -> Path:
        return self.replication_dir / REPLICATION_TRANSCRIPT_FILE

    @property
    def fix_severity_transcript_path(self) -> Path:
        return self.evaluate_dir / FIX_SEVERITY_TRANSCRIPT_FILE

    def evaluation_transcript_path(self, category: str) -> Path:
        """Path to the per-category evaluation transcript, e.g. ``code_transcript.jsonl``."""
        return self.evaluate_dir / f"{category}{EVALUATION_TRANSCRIPT_FILE_SUFFIX}"
