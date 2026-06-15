"""Configuration for Veritas."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional

from veritas.core.config_env import _env_int, _env_opt_int


# All valid AI providers
VALID_PROVIDERS = ["claude", "codex", "gemini"]

# Input mode literal
InputMode = Literal["full", "paper-only", "repo-only"]

VALID_INPUT_MODES = ["auto", "full", "paper-only", "repo-only"]

# Engagement depth: how hard veritas engages with the artifacts.
#   "run"  — execute the (provided or generated) code and grade produced values.
#            This is the default and covers the original three input modes.
#   "read" — do NOT execute anything. Read the paper (and, when supplied, the
#            code/data) and produce a reading-based Reproducibility Assessment.
#            Backs the demo's "paper only" and "paper + resources (no run)" modes.
DepthMode = Literal["read", "run"]
VALID_DEPTHS = ["read", "run"]


# Output directory structure — each phase writes into its own subdir.
ANALYZE_SUBDIR = "analyze"
REPLICATION_SUBDIR = "replication"
REPORT_SUBDIR = "report"
PROMPTS_SUBDIR = "prompts"

# New subdirectories introduced by the claim-verification pipeline.
ASSESS_SUBDIR = "assess"
VERIFY_SUBDIR = "verify"
# Optional, opt-in contextual-evaluation phase (post-verify external checker).
# Distinct from the benchmark harness's ``evaluate/`` scoring dir.
EVALUATION_SUBDIR = "evaluation"

# Read-mode (``--depth read``) static-review phase output. Holds the per-claim
# reading-based assessments and the aggregate Reproducibility Assessment.
REVIEW_SUBDIR = "review"

# Inline-comment subsystem output (both depths). Holds the anchored comments
# JSON and the self-contained side-by-side viewer.
INLINE_SUBDIR = "inline"

OUTPUT_SUBDIRS = (
    ANALYZE_SUBDIR,
    REPLICATION_SUBDIR,
    ASSESS_SUBDIR,
    VERIFY_SUBDIR,
    EVALUATION_SUBDIR,
    REVIEW_SUBDIR,
    INLINE_SUBDIR,
    REPORT_SUBDIR,
    PROMPTS_SUBDIR,
)


# Well-known output filenames produced by the pipeline.
REPLICATION_PLAN_FILE = "replication_plan.json"
DILIGENCE_SIGNALS_FILE = "diligence_signals.json"
FIX_SEVERITY_FILE = "fix_severity.json"
REPORT_MD_FILE = "replication_report.md"
REPORT_PDF_FILE = "replication_report.pdf"
REPORT_HTML_FILE = "replication_report.html"

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

# Contextual-evaluation phase filenames.
EVALUATION_FILE = "contextual_evaluation.json"
EVALUATION_TRANSCRIPT_FILE = "contextual_evaluation_transcript.jsonl"

# Read-mode static-review phase filenames. The agent writes per-claim
# assessments and the aggregate; the transcript captures its streamed output.
CLAIM_ASSESSMENTS_FILE = "claim_assessments.json"
REPRODUCIBILITY_ASSESSMENT_FILE = "reproducibility_assessment.json"
REVIEW_TRANSCRIPT_FILE = "static_review_transcript.jsonl"

# Inline-comment subsystem filenames.
INLINE_COMMENTS_FILE = "inline_comments.json"
INLINE_VIEWER_FILE = "inline_review.html"
INLINE_TRANSCRIPT_FILE = "inline_comments_transcript.jsonl"
PAPER_TEXT_FILE = "paper_text.json"  # parsed paper paragraphs (for anchoring)

# Manager-controlled retry loop (Phase 2) filenames. The manager review pass is
# the post-replicate control gate; its structured verdict lands in the
# replication subdir, its transcript alongside, and the workflow/decision log
# in the .veritas state dir.
MANAGER_REVIEW_FILE = "manager_review.json"
MANAGER_REVIEW_TRANSCRIPT_FILE = "manager_review_transcript.jsonl"
WORKFLOW_LOG_FILE = "workflow.jsonl"
WORKFLOW_MD_FILE = "workflow.md"

# Manager research sub-agents (Phase 3) filenames. Each honored research request
# spawns a finder sub-agent (separate provider invocation, web access) whose
# finding + transcript land in the replication subdir, then an LLM-redactor pass
# whose result lands alongside. ``{kind}`` is ``resource`` | ``literature`` and
# ``{i}`` disambiguates multiple requests of the same kind in one iteration.
RESEARCH_FINDING_FILE_TMPL = "research_{kind}.json"
RESEARCH_TRANSCRIPT_FILE_TMPL = "research_{kind}_transcript.jsonl"
RESEARCH_REDACTION_FILE_TMPL = "research_{kind}_redaction.json"
RESEARCH_REDACTION_TRANSCRIPT_FILE_TMPL = "research_{kind}_redaction_transcript.jsonl"


@dataclass
class Config:
    """Configuration for a replication run."""

    # Input paths
    repo_path: Optional[Path] = None
    paper_path: Optional[Path] = None

    # Output settings
    output_dir: Optional[Path] = None
    generate_pdf: bool = True

    # Run settings
    provider: str = "claude"
    mode: str = "auto"
    depth: str = "run"
    claims_path: Optional[Path] = None
    data_path: Optional[Path] = None

    # Output selection. ``inline`` additionally emits the anchored in-line
    # comments + side-by-side viewer; the referee-style report is always
    # produced. Inline requires a paper (its comments anchor into paper text),
    # so it is silently skipped in repo-only runs.
    emit_inline: bool = False

    # Per-phase timeouts (seconds); None disables the timeout for that phase.
    # Defaults are None — killing a hung run discards partial progress, which
    # is worse than letting it finish. Re-enable once there's a checkpoint /
    # resume mechanism to recover the work.
    #
    # Resolution (highest wins): CLI flag -> ``VERITAS_*_TIMEOUT`` env var ->
    # code default (None). The CLI passes an explicit value only when its flag
    # is set; when it passes None, ``__post_init__`` consults the env var.
    analyze_timeout: Optional[int] = None
    codegen_timeout: Optional[int] = None
    replicate_timeout: Optional[int] = None
    verify_timeout: Optional[int] = None
    evaluate_timeout: Optional[int] = None
    review_timeout: Optional[int] = None  # read-mode static-review + inline passes

    # Opt-in contextual-evaluation phase (post-verify external checker). Off by
    # default to keep per-run cost predictable; benchmark sweeps enable it.
    run_evaluation: bool = False

    # Hard cap on manager-driven retry iterations (reserved for the later
    # iterative-manager loop phase; no behavior wired yet). Overridable via
    # ``VERITAS_MAX_ITERS`` (default 3). Benchmark runs set 1 for single-pass.
    max_iters: int = field(
        default_factory=lambda: _env_int("VERITAS_MAX_ITERS", 3)
    )

    # Runtime settings
    verbose: bool = False

    # Env-var names backing each per-phase timeout, consulted in __post_init__
    # when the CLI flag is absent (the field is left as None).
    _TIMEOUT_ENV_VARS = {
        "analyze_timeout": "VERITAS_ANALYZE_TIMEOUT",
        "codegen_timeout": "VERITAS_CODEGEN_TIMEOUT",
        "replicate_timeout": "VERITAS_REPLICATE_TIMEOUT",
        "verify_timeout": "VERITAS_VERIFY_TIMEOUT",
        "evaluate_timeout": "VERITAS_EVALUATE_TIMEOUT",
        "review_timeout": "VERITAS_REVIEW_TIMEOUT",
    }

    def __post_init__(self):
        # Timeouts: CLI (explicit value) wins; otherwise honor the env var as
        # the default. Code default (None) remains when neither is set.
        for field_name, env_name in self._TIMEOUT_ENV_VARS.items():
            if getattr(self, field_name) is None:
                setattr(self, field_name, _env_opt_int(env_name, None))

        # Convert input paths to Path objects (if provided)
        if self.repo_path is not None:
            self.repo_path = Path(self.repo_path)
        if self.paper_path is not None:
            self.paper_path = Path(self.paper_path)
        if self.claims_path is not None:
            self.claims_path = Path(self.claims_path)
        if self.data_path is not None:
            self.data_path = Path(self.data_path)

        # Output dir fallback chain: explicit --output wins; else <repo>/replicate; else <paper-parent>/replicate
        if self.output_dir:
            self.output_dir = Path(self.output_dir)
        elif self.repo_path:
            self.output_dir = self.repo_path / "replicate"
        elif self.paper_path:
            self.output_dir = self.paper_path.parent / "replicate"
        else:
            raise ValueError(
                "Cannot determine output directory: provide --output, --repo, or --paper"
            )

        # Validate provider
        if self.provider.lower() not in VALID_PROVIDERS:
            raise ValueError(
                f"Unknown provider: {self.provider}. Valid options: {VALID_PROVIDERS}"
            )

        # Resolve input mode (auto-detect from inputs, or validate explicit mode)
        self.mode = self._resolve_mode(self.mode)

        # Validate / constrain engagement depth.
        if self.depth not in VALID_DEPTHS:
            raise ValueError(
                f"Unknown depth: {self.depth}. Valid options: {VALID_DEPTHS}"
            )
        if self.depth == "read":
            # Read mode is a paper-centric review: the assessment narrates the
            # paper's claims and (in the demo) anchors in-line comments into the
            # paper text. A repo with no paper has nothing to read against.
            if not self.has_paper:
                raise ValueError(
                    "--depth read requires --paper (read mode reviews the paper; "
                    "repo-only runs must use --depth run)"
                )
            # paper-only and full are the meaningful read-mode shapes; repo-only
            # was already rejected above. No code is generated or executed.
        if self.emit_inline and not self.has_paper:
            print(
                "WARNING: --inline requested but no --paper provided; "
                "in-line comments need paper text to anchor to. Skipping inline."
            )
            self.emit_inline = False

        # Validate --data: must be a directory if provided. Empty directories
        # warn rather than fail so smoke-test runs aren't punished. The
        # "at least one of paper/repo" requirement is enforced by mode
        # resolution above, so we don't recheck it here.
        if self.data_path is not None:
            if not self.data_path.exists():
                raise FileNotFoundError(
                    f"--data path does not exist: {self.data_path}"
                )
            if not self.data_path.is_dir():
                raise ValueError(
                    f"--data must be a directory; got file: {self.data_path}"
                )
            if not any(self.data_path.iterdir()):
                print(f"WARNING: --data directory is empty: {self.data_path}")

    def _resolve_mode(self, requested: str) -> str:
        """Resolve --mode auto into an explicit mode, or validate an explicit mode."""
        if requested not in VALID_INPUT_MODES:
            raise ValueError(
                f"Unknown mode: {requested}. Valid options: {VALID_INPUT_MODES}"
            )

        # Check --claims pairing first: more specific error than the generic "needs paper or repo"
        if self.has_user_claims and not (self.has_paper or self.has_repo):
            raise ValueError(
                "--claims requires at least --paper or --repo as evidence source"
            )

        if requested == "auto":
            return self._infer_mode()

        # Explicit mode — validate against the provided inputs
        if requested == "full":
            if not self.has_paper or not self.has_repo:
                raise ValueError(
                    "--mode full requires both --paper and --repo"
                )
        elif requested == "paper-only":
            if not self.has_paper:
                raise ValueError("--mode paper-only requires --paper")
            if self.has_repo:
                print(
                    f"WARNING: --repo provided but --mode paper-only is set; "
                    f"ignoring repo at {self.repo_path}"
                )
        elif requested == "repo-only":
            if not self.has_repo:
                raise ValueError("--mode repo-only requires --repo")
            if self.has_paper:
                print(
                    f"WARNING: --paper provided but --mode repo-only is set; "
                    f"ignoring paper at {self.paper_path}"
                )

        return requested

    def _infer_mode(self) -> str:
        """Infer mode from which of paper/repo are present."""
        if self.has_paper and self.has_repo:
            return "full"
        if self.has_paper and not self.has_repo:
            return "paper-only"
        if self.has_repo and not self.has_paper:
            return "repo-only"
        raise ValueError(
            "At least one of --paper or --repo is required "
            "(or --claims paired with one of them)"
        )

    @property
    def has_paper(self) -> bool:
        return self.paper_path is not None and self.paper_path.exists()

    @property
    def has_repo(self) -> bool:
        return self.repo_path is not None and self.repo_path.exists()

    @property
    def has_user_claims(self) -> bool:
        return self.claims_path is not None and self.claims_path.exists()

    @property
    def has_data(self) -> bool:
        return self.data_path is not None and self.data_path.exists()

    @property
    def effective_repo_path(self) -> Optional[Path]:
        """Path to the codebase the pipeline operates on. In paper-only mode this
        is the codegen output (replication_dir/codebase). In other modes it is the
        user-supplied repo. Returns None when neither is available (rare; should
        only occur if called before codegen runs in paper-only mode)."""
        # Read mode never generates code: there is no codegen codebase to point
        # at, so the effective codebase is simply the user's repo (or None).
        if self.depth == "read":
            return self.repo_path
        if self.mode == "paper-only":
            codebase = self.replication_dir / "codebase"
            return codebase if codebase.exists() else None
        return self.repo_path

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
    def evaluation_dir(self) -> Path:
        return self.output_dir / EVALUATION_SUBDIR

    @property
    def review_dir(self) -> Path:
        return self.output_dir / REVIEW_SUBDIR

    @property
    def inline_dir(self) -> Path:
        return self.output_dir / INLINE_SUBDIR

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
    def evaluation_path(self) -> Path:
        return self.evaluation_dir / EVALUATION_FILE

    @property
    def evaluation_transcript_path(self) -> Path:
        return self.evaluation_dir / EVALUATION_TRANSCRIPT_FILE

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

    @property
    def diligence_signals_path(self) -> Path:
        return self.replication_dir / DILIGENCE_SIGNALS_FILE

    # -- Read-mode (static review) artifacts --------------------------------

    @property
    def claim_assessments_path(self) -> Path:
        return self.review_dir / CLAIM_ASSESSMENTS_FILE

    @property
    def reproducibility_assessment_path(self) -> Path:
        return self.review_dir / REPRODUCIBILITY_ASSESSMENT_FILE

    @property
    def review_transcript_path(self) -> Path:
        return self.review_dir / REVIEW_TRANSCRIPT_FILE

    # -- Inline-comment artifacts -------------------------------------------

    @property
    def inline_comments_path(self) -> Path:
        return self.inline_dir / INLINE_COMMENTS_FILE

    @property
    def inline_viewer_path(self) -> Path:
        return self.inline_dir / INLINE_VIEWER_FILE

    @property
    def inline_transcript_path(self) -> Path:
        return self.inline_dir / INLINE_TRANSCRIPT_FILE

    @property
    def paper_text_path(self) -> Path:
        return self.inline_dir / PAPER_TEXT_FILE

    # -- Manager retry-loop artifacts ---------------------------------------

    @property
    def veritas_state_dir(self) -> Path:
        return self.output_dir / ".veritas"

    @property
    def manager_review_path(self) -> Path:
        return self.replication_dir / MANAGER_REVIEW_FILE

    @property
    def manager_review_transcript_path(self) -> Path:
        return self.replication_dir / MANAGER_REVIEW_TRANSCRIPT_FILE

    @property
    def workflow_log_path(self) -> Path:
        return self.veritas_state_dir / WORKFLOW_LOG_FILE

    # -- Manager research sub-agents (Phase 3) artifacts --------------------

    def research_finding_path(self, kind: str, index: int = 0) -> Path:
        suffix = f"_{index}" if index else ""
        name = RESEARCH_FINDING_FILE_TMPL.format(kind=kind)
        if suffix:
            name = name.replace(".json", f"{suffix}.json")
        return self.replication_dir / name

    def research_transcript_path(self, kind: str, index: int = 0) -> Path:
        suffix = f"_{index}" if index else ""
        name = RESEARCH_TRANSCRIPT_FILE_TMPL.format(kind=kind)
        if suffix:
            name = name.replace(".jsonl", f"{suffix}.jsonl")
        return self.replication_dir / name

    def research_redaction_path(self, kind: str, index: int = 0) -> Path:
        suffix = f"_{index}" if index else ""
        name = RESEARCH_REDACTION_FILE_TMPL.format(kind=kind)
        if suffix:
            name = name.replace(".json", f"{suffix}.json")
        return self.replication_dir / name

    def research_redaction_transcript_path(self, kind: str, index: int = 0) -> Path:
        suffix = f"_{index}" if index else ""
        name = RESEARCH_REDACTION_TRANSCRIPT_FILE_TMPL.format(kind=kind)
        if suffix:
            name = name.replace(".jsonl", f"{suffix}.jsonl")
        return self.replication_dir / name

    def verify_path(self, claim_id: str) -> Path:
        """Path to the per-claim verdict JSON, e.g. ``verify/C1.json``."""
        return self.verify_dir / f"{claim_id}{VERIFY_FILE_SUFFIX}"

    def verify_transcript_path(self, claim_id: str) -> Path:
        """Path to the per-claim verifier transcript, e.g. ``verify/C1_transcript.jsonl``."""
        return self.verify_dir / f"{claim_id}{VERIFY_TRANSCRIPT_FILE_SUFFIX}"

    @property
    def paper_claims_transcript_path(self) -> Path:
        return self.analyze_dir / PAPER_CLAIMS_TRANSCRIPT_FILE

    @property
    def codegen_complete_sentinel_path(self) -> Path:
        return self.output_dir / ".veritas" / "codegen_complete"

    @property
    def codegen_transcript_path(self) -> Path:
        return self.replication_dir / "codegen_transcript.jsonl"

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
