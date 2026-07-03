"""Configuration for Veritas."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, Literal, Optional, Tuple

from veritas.core.config_env import _env_int, _env_opt_int, _env_opt_str, _env_str


# All valid AI providers
VALID_PROVIDERS = ["claude", "codex", "gemini", "openrouter"]

# Input mode literal
InputMode = Literal["full", "paper-only", "repo-only"]

VALID_INPUT_MODES = ["auto", "full", "paper-only", "repo-only"]

# Named groups of LLM call sites. Each bucket resolves its own engine
# (provider + model) via Config.engine_for.
BUCKETS = ("analyze", "codegen", "replicate", "assess", "verify", "evaluate")

# Text before the first ':' counts as a provider prefix only when it is a
# simple token — OpenRouter variant suffixes like 'model:free' stay parseable
# as bare models.
_PROVIDER_PREFIX_RE = re.compile(r"^[a-z][a-z0-9_-]*$")


def parse_model_spec(spec: str) -> Tuple[Optional[str], str]:
    """Parse a ``[provider:]model`` spec into ``(provider, model)``.

    Returns ``(None, model)`` for bare specs. Raises ``ValueError`` on a
    typo'd provider prefix, an empty model after a valid prefix, or an
    empty spec.
    """
    spec = spec.strip()
    if not spec:
        raise ValueError("Empty model spec")
    head, sep, tail = spec.partition(":")
    if not sep:
        return None, spec
    head_lower = head.lower()
    if head_lower in VALID_PROVIDERS:
        if not tail.strip():
            raise ValueError(
                f"Model spec '{spec}': provider prefix requires a model"
            )
        return head_lower, tail.strip()
    if _PROVIDER_PREFIX_RE.match(head_lower):
        raise ValueError(
            f"Model spec '{spec}': unknown provider '{head}'. "
            f"Valid providers: {VALID_PROVIDERS}"
        )
    return None, spec


# Model slugs whose web access cannot be disabled. On the replicate/codegen
# buckets these can fetch the paper's published values, defeating the
# anti-leakage design; veritas warns (never blocks) when one is configured
# there.
_WEB_LOCKED_MODELS = ("openrouter/fusion",)


def is_web_locked_slug(model: Optional[str]) -> bool:
    if not model:
        return False
    return model in _WEB_LOCKED_MODELS or model.endswith(":online")


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

OUTPUT_SUBDIRS = (
    ANALYZE_SUBDIR,
    REPLICATION_SUBDIR,
    ASSESS_SUBDIR,
    VERIFY_SUBDIR,
    EVALUATION_SUBDIR,
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
RESOURCE_USAGE_FILE = "resource_usage.json"
RESOURCE_ESTIMATE_FILE = "resource_estimate.json"
RESOURCE_ESTIMATE_TRANSCRIPT_FILE = "resource_estimate_transcript.jsonl"
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

# Sidecars recording the settings (engine, and scope for citations) that
# produced each evaluate-bucket output. A settings change re-runs the output
# instead of silently reusing it; outputs without a sidecar predate this
# tracking and are kept as-is.
EVALUATION_META_FILE = ".contextual_evaluation_meta.json"
CITATION_CHECK_META_FILE = ".citation_check_meta.json"

# Citation-check submodule filenames (opt-in, advisory; under the evaluation dir).
CITATION_CHECK_FILE = "citation_check.json"
CITATION_CHECK_TRANSCRIPT_FILE = "citation_check_transcript.jsonl"
CITATION_REFERENCES_FILE = "references.json"
CITATION_RESOLVER_VERDICTS_FILE = "resolver_verdicts.json"
CITATION_RESOLVER_SCRIPT_FILE = "resolve_references.py"

CITATION_AUDIT_FILE = "citation_audit.json"
CITATION_AUDIT_TRANSCRIPT_FILE = "citation_audit_transcript.jsonl"

VALID_FAITHFULNESS_SCOPES = ["main", "all"]

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
    claims_path: Optional[Path] = None
    data_path: Optional[Path] = None

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

    # Per-bucket engine selection. Each field takes a ``[provider:]model``
    # spec; ``model`` is the bare global default (its provider is
    # ``provider`` above). None = the provider CLI's own default model.
    model: Optional[str] = None
    analyze_model: Optional[str] = None
    codegen_model: Optional[str] = None
    replicate_model: Optional[str] = None
    assess_model: Optional[str] = None
    verify_model: Optional[str] = None
    evaluate_model: Optional[str] = None

    # Opt-in contextual-evaluation phase (post-verify external checker). Off by
    # default to keep per-run cost predictable; benchmark sweeps enable it.
    run_evaluation: bool = False

    # Opt-in citation-check submodule (post-verify; under the evaluate phase).
    # Verifies the paper's references exist + carry correct metadata. Advisory:
    # does not change the Replication Score. Requires --paper.
    run_citation_check: bool = False

    # Timeout (seconds) for the citation-check phase; None disables it.
    citation_timeout: Optional[int] = None

    # Citation faithfulness scope: "main" (central attributed claims only) or
    # "all" (every claim-bearing citation). Default "main".
    faithfulness_scope: str = field(
        default_factory=lambda: _env_str("VERITAS_CITATION_FAITHFULNESS_SCOPE", "main")
    )

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
        "citation_timeout": "VERITAS_CITATION_TIMEOUT",
    }

    # Env-var names backing each model field, consulted in __post_init__
    # when the CLI flag is absent (the field is left as None).
    _MODEL_ENV_VARS = {
        "model": "VERITAS_MODEL",
        "analyze_model": "VERITAS_ANALYZE_MODEL",
        "codegen_model": "VERITAS_CODEGEN_MODEL",
        "replicate_model": "VERITAS_REPLICATE_MODEL",
        "assess_model": "VERITAS_ASSESS_MODEL",
        "verify_model": "VERITAS_VERIFY_MODEL",
        "evaluate_model": "VERITAS_EVALUATE_MODEL",
    }

    def __post_init__(self):
        # Timeouts: CLI (explicit value) wins; otherwise honor the env var as
        # the default. Code default (None) remains when neither is set.
        for field_name, env_name in self._TIMEOUT_ENV_VARS.items():
            if getattr(self, field_name) is None:
                setattr(self, field_name, _env_opt_int(env_name, None))

        # Models: CLI (explicit value) wins; otherwise honor the env var.
        for field_name, env_name in self._MODEL_ENV_VARS.items():
            if getattr(self, field_name) is None:
                setattr(self, field_name, _env_opt_str(env_name))

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

        # Validate and canonicalize the provider (stored lowercase so
        # fingerprints, engine strings, and wrapper checks all agree).
        self.provider = self.provider.lower()
        if self.provider not in VALID_PROVIDERS:
            raise ValueError(
                f"Unknown provider: {self.provider}. Valid options: {VALID_PROVIDERS}"
            )

        # Resolve input mode (auto-detect from inputs, or validate explicit mode)
        self.mode = self._resolve_mode(self.mode)

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

        # Citation check needs the paper PDF (its reference list). Validate up
        # front so the run fails with a clear message instead of failing deep in
        # the citation phase.
        if self.run_citation_check and not self.has_paper:
            raise ValueError(
                "--check-citations requires --paper (it reads the paper's "
                "reference list); no paper was provided"
            )

        if self.run_citation_check:
            scope = (self.faithfulness_scope or "main").strip().lower()
            if scope not in VALID_FAITHFULNESS_SCOPES:
                raise ValueError(
                    f"faithfulness_scope must be one of "
                    f"{VALID_FAITHFULNESS_SCOPES}; got '{self.faithfulness_scope}'"
                )
            self.faithfulness_scope = scope

        # The global model is bare by definition — its provider is --provider.
        if self.model is not None:
            prefix, _ = parse_model_spec(self.model)
            if prefix is not None:
                raise ValueError(
                    f"--model must be a bare model (set the provider with "
                    f"--provider); got '{self.model}'"
                )

        # Eagerly resolve every bucket so malformed specs (flags or
        # VERITAS_*_MODEL vars) fail at startup rather than mid-run, and the
        # openrouter provider always has an explicit model.
        for bucket in BUCKETS:
            bucket_provider, bucket_model = self.engine_for(bucket)
            if bucket_provider == "openrouter" and bucket_model is None:
                raise ValueError(
                    f"Provider openrouter requires an explicit model for the "
                    f"'{bucket}' bucket (pass --model or --{bucket}-model)"
                )

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
        if self.mode == "paper-only":
            codebase = self.replication_dir / "codebase"
            return codebase if codebase.exists() else None
        return self.repo_path

    # -- Per-bucket engine resolution -----------------------------------------

    def engine_for(self, bucket: str) -> Tuple[str, Optional[str]]:
        """Resolve the ``(provider, model)`` engine for a bucket.

        Precedence: the bucket's own spec (flag or VERITAS_<BUCKET>_MODEL,
        already merged in __post_init__) -> the global ``model`` with the
        global ``provider``. ``model=None`` means the provider CLI's default.
        """
        if bucket not in BUCKETS:
            raise ValueError(f"Unknown bucket: {bucket}. Valid: {BUCKETS}")
        spec = getattr(self, f"{bucket}_model")
        if spec is not None:
            prefix, model = parse_model_spec(spec)
            return (prefix or self.provider.lower(), model)
        return (self.provider.lower(), self.model)

    def resolved_engines(self) -> Dict[str, str]:
        """Canonical ``bucket -> 'provider:model'`` (or ``'provider'``) map."""
        engines = {}
        for bucket in BUCKETS:
            provider, model = self.engine_for(bucket)
            engines[bucket] = f"{provider}:{model}" if model else provider
        return engines

    def resolved_providers(self) -> set:
        """Set of providers any bucket resolves to."""
        return {self.engine_for(bucket)[0] for bucket in BUCKETS}

    @property
    def any_model_knob_set(self) -> bool:
        return any(
            getattr(self, field_name) is not None
            for field_name in self._MODEL_ENV_VARS
        )

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
    def evaluation_meta_path(self) -> Path:
        return self.evaluation_dir / EVALUATION_META_FILE

    # Citation-check submodule artifacts (under evaluation/). references_path and
    # resolver_verdicts_path are produced by the agent; resolver_script_path is
    # where the runner stages the standalone resolver for the agent to run.
    @property
    def citation_check_path(self) -> Path:
        return self.evaluation_dir / CITATION_CHECK_FILE

    @property
    def citation_check_meta_path(self) -> Path:
        """Sidecar recording the settings that produced citation_check.json."""
        return self.evaluation_dir / CITATION_CHECK_META_FILE

    @property
    def citation_check_transcript_path(self) -> Path:
        return self.evaluation_dir / CITATION_CHECK_TRANSCRIPT_FILE

    @property
    def citation_audit_path(self) -> Path:
        return self.evaluation_dir / CITATION_AUDIT_FILE

    @property
    def citation_audit_transcript_path(self) -> Path:
        return self.evaluation_dir / CITATION_AUDIT_TRANSCRIPT_FILE

    @property
    def references_path(self) -> Path:
        return self.evaluation_dir / CITATION_REFERENCES_FILE

    @property
    def resolver_verdicts_path(self) -> Path:
        return self.evaluation_dir / CITATION_RESOLVER_VERDICTS_FILE

    @property
    def resolver_script_path(self) -> Path:
        return self.evaluation_dir / CITATION_RESOLVER_SCRIPT_FILE

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

    @property
    def resource_usage_path(self) -> Path:
        return self.output_dir / RESOURCE_USAGE_FILE

    @property
    def resource_estimate_path(self) -> Path:
        return self.analyze_dir / RESOURCE_ESTIMATE_FILE

    @property
    def resource_estimate_transcript_path(self) -> Path:
        return self.analyze_dir / RESOURCE_ESTIMATE_TRANSCRIPT_FILE

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
