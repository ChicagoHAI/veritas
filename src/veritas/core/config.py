"""Configuration for Veritas."""

from dataclasses import dataclass
from pathlib import Path
from typing import Optional


# All valid AI providers
VALID_PROVIDERS = ["claude", "codex", "gemini"]

# Valid claim-extraction scopes
VALID_CLAIM_SCOPES = ["main", "full"]


# Output directory structure — each phase writes into its own subdir.
ANALYZE_SUBDIR = "analyze"
REPLICATION_SUBDIR = "replication"
REPORT_SUBDIR = "report"
PROMPTS_SUBDIR = "prompts"

# New subdirectories introduced by the claim-verification pipeline.
ASSESS_SUBDIR = "assess"
VERIFY_SUBDIR = "verify"

OUTPUT_SUBDIRS = (
    ANALYZE_SUBDIR,
    REPLICATION_SUBDIR,
    ASSESS_SUBDIR,
    VERIFY_SUBDIR,
    REPORT_SUBDIR,
    PROMPTS_SUBDIR,
)


# Well-known output filenames produced by the pipeline.
REPLICATION_PLAN_FILE = "replication_plan.json"
FIX_SEVERITY_FILE = "fix_severity.json"
REPORT_MD_FILE = "replication_report.md"
REPORT_PDF_FILE = "replication_report.pdf"

# Claim-verification pipeline filenames.
PAPER_CLAIMS_FILE = "paper_claims.json"
VERDICTS_FILE = "verdicts.json"
REPLICATION_SCORE_FILE = "replication_score.json"
VERIFY_FILE_SUFFIX = ".json"  # per-claim files: ``verify/<claim_id>.json``

PAPER_CLAIMS_TRANSCRIPT_FILE = "paper_claims_transcript.jsonl"
VERIFY_TRANSCRIPT_FILE_SUFFIX = "_transcript.jsonl"  # ``verify/<claim_id>_transcript.jsonl``

# Per-phase JSONL transcripts of the agent's streaming output. Each provider
# invocation writes its event stream to one of these files; on a parse-repair
# re-invocation, events are appended to the same file so the failed attempt
# and the repair attempt land in one place.
REPLICATION_PLAN_TRANSCRIPT_FILE = "replication_plan_transcript.jsonl"
REPLICATION_TRANSCRIPT_FILE = "replication_transcript.jsonl"
FIX_SEVERITY_TRANSCRIPT_FILE = "fix_severity_transcript.jsonl"


@dataclass
class Config:
    """Configuration for a replication evaluation run."""

    # Input paths
    repo_path: Path
    paper_path: Optional[Path] = None

    # Output settings
    output_dir: Optional[Path] = None
    generate_pdf: bool = True

    # Evaluation settings
    provider: str = "claude"
    claim_scope: str = "main"

    # Per-phase timeouts (seconds); None disables the timeout for that phase.
    # Defaults are None — killing a hung run discards partial progress, which
    # is worse than letting it finish. Re-enable once there's a checkpoint /
    # resume mechanism to recover the work.
    analyze_timeout: Optional[int] = None
    replicate_timeout: Optional[int] = None
    verify_timeout: Optional[int] = None

    # Runtime settings
    verbose: bool = False

    def __post_init__(self):
        # Convert paths to Path objects
        self.repo_path = Path(self.repo_path)
        if self.paper_path:
            self.paper_path = Path(self.paper_path)
        if self.output_dir:
            self.output_dir = Path(self.output_dir)
        else:
            self.output_dir = self.repo_path / "evaluation"

        # Validate provider
        if self.provider.lower() not in VALID_PROVIDERS:
            raise ValueError(f"Unknown provider: {self.provider}. Valid options: {VALID_PROVIDERS}")

        # Validate claim scope
        if self.claim_scope not in VALID_CLAIM_SCOPES:
            raise ValueError(
                f"Unknown claim scope: {self.claim_scope}. Valid options: {VALID_CLAIM_SCOPES}"
            )
        if self.claim_scope == "full":
            raise NotImplementedError(
                "--scope full is not yet implemented. Use --scope main (default)."
            )

    @property
    def has_paper(self) -> bool:
        return self.paper_path is not None and self.paper_path.exists()

    # -- Output subdirectories ----------------------------------------------

    @property
    def analyze_dir(self) -> Path:
        return self.output_dir / ANALYZE_SUBDIR

    @property
    def replication_dir(self) -> Path:
        return self.output_dir / REPLICATION_SUBDIR

    @property
    def assess_dir(self) -> Path:
        return self.output_dir / ASSESS_SUBDIR

    @property
    def verify_dir(self) -> Path:
        return self.output_dir / VERIFY_SUBDIR

    @property
    def report_dir(self) -> Path:
        return self.output_dir / REPORT_SUBDIR

    @property
    def prompts_dir(self) -> Path:
        return self.output_dir / PROMPTS_SUBDIR

    # -- Well-known output files --------------------------------------------

    @property
    def replication_plan_path(self) -> Path:
        return self.analyze_dir / REPLICATION_PLAN_FILE

    @property
    def fix_severity_path(self) -> Path:
        return self.assess_dir / FIX_SEVERITY_FILE

    @property
    def report_md_path(self) -> Path:
        return self.report_dir / REPORT_MD_FILE

    @property
    def report_pdf_path(self) -> Path:
        return self.report_dir / REPORT_PDF_FILE

    @property
    def paper_claims_path(self) -> Path:
        return self.analyze_dir / PAPER_CLAIMS_FILE

    @property
    def verdicts_path(self) -> Path:
        return self.verify_dir / VERDICTS_FILE

    @property
    def replication_score_path(self) -> Path:
        return self.verify_dir / REPLICATION_SCORE_FILE

    def verify_path(self, claim_id: str) -> Path:
        """Path to the per-claim verdict JSON, e.g. ``verify/C1.json``."""
        return self.verify_dir / f"{claim_id}{VERIFY_FILE_SUFFIX}"

    def verify_transcript_path(self, claim_id: str) -> Path:
        """Path to the per-claim verifier transcript, e.g. ``verify/C1_transcript.jsonl``."""
        return self.verify_dir / f"{claim_id}{VERIFY_TRANSCRIPT_FILE_SUFFIX}"

    @property
    def paper_claims_transcript_path(self) -> Path:
        return self.analyze_dir / PAPER_CLAIMS_TRANSCRIPT_FILE

    # -- Transcript files (JSONL streamed from provider invocations) --------

    @property
    def replication_plan_transcript_path(self) -> Path:
        return self.analyze_dir / REPLICATION_PLAN_TRANSCRIPT_FILE

    @property
    def replication_transcript_path(self) -> Path:
        return self.replication_dir / REPLICATION_TRANSCRIPT_FILE

    @property
    def fix_severity_transcript_path(self) -> Path:
        return self.assess_dir / FIX_SEVERITY_TRANSCRIPT_FILE
