"""Main runner for the veritas replication pipeline."""

import json
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Tuple

from veritas.core.config import BUCKETS, Config, OUTPUT_SUBDIRS, is_web_locked_slug
from veritas.core.models.resource_estimate import ResourceEstimate
from veritas.core.pipeline_state import (
    PipelineState,
    STATUS_INSUFFICIENT_SPEC,
    state_file_path,
)
from veritas.core.models.replication import ReplicationPlan, ExecutionEvidence
from veritas.core.models.fix_severity import FixSeverityAssessment
from veritas.core.models.paper_claims import PaperClaims, PaperClaim, ClaimVerdict, ReplicationScore
from veritas.core.paper_claims import parse_paper_claims_response
from veritas.core.verify import compute_replication_score
from veritas.core.replication import (
    parse_replication_plan_response,
    gather_evidence,
    _extract_json,
)
from veritas.core.diligence import compute_execution_facts, ExecutionFacts
from veritas.core.manager import (
    ManagerGuidance,
    ManagerVerdict,
    WorkflowLog,
    archive_attempt,
    build_handoff,
    parse_manager_verdict,
    should_stop,
)
from veritas.core.research import (
    KIND_TEMPLATES,
    RedactionResult,
    ResearchConfig,
    ResearchFinding,
    format_findings_for_guidance,
    known_value_strings,
    parse_research_requests,
    redact_known_values,
    split_requests,
)
from veritas.core.report_generator import ReportGenerator
from veritas.templates.prompt_generator import PromptGenerator
from veritas.utils.security import sanitize_logs_directory, sanitize_text


class _InsufficientSpec(Exception):
    """Signal raised when claim extraction returns zero verifiable claims.

    Caught by run() to trigger a clean bail with a dedicated report rather than
    propagating as a runtime error. source_path identifies what was read (the
    paper PDF or a README), mode reports which input mode was active.
    """

    def __init__(self, source_path: Path, mode: str):
        super().__init__(
            f"Analyze produced 0 claims (source: {source_path}, mode: {mode})"
        )
        self.source_path = source_path
        self.mode = mode


# Provider invocation tables. Each provider has a CLI command (cli name plus
# any required positional subcommand or print flag), a transcript-output flag
# set (so the JSONL stream lands on stdout for capture), and a permission
# flag set (so non-interactive runs don't block on confirmation prompts).
CLI_COMMANDS: Dict[str, Tuple[str, ...]] = {
    "claude": ("claude", "-p"),
    "codex":  ("codex", "exec"),
    "gemini": ("gemini",),
    # opencode `run` is the non-interactive mode.
    "openrouter": ("opencode", "run"),
}

TRANSCRIPT_FLAGS: Dict[str, Tuple[str, ...]] = {
    "claude": ("--verbose", "--output-format", "stream-json"),
    "codex":  ("--json",),
    "gemini": ("--output-format", "stream-json"),
    "openrouter": ("--format", "json"),
}

PERMISSION_FLAGS: Dict[str, Tuple[str, ...]] = {
    "claude": ("--dangerously-skip-permissions",),
    # codex: --full-auto is deprecated and keeps the network-blocking
    # sandbox, which would break replicate-phase pip installs and data
    # downloads. Full bypass matches the trust already granted to claude;
    # the container is the isolation boundary. --skip-git-repo-check:
    # phase working dirs are not git repos and codex refuses to start
    # there without it.
    "codex":  ("--dangerously-bypass-approvals-and-sandbox",
               "--skip-git-repo-check"),
    "gemini": ("--yolo", "--skip-trust"),
    # opencode: --auto approves tool use not explicitly denied — headless runs
    # must never block on a permission prompt. Same trust as the other
    # providers' bypass flags; the container is the isolation boundary.
    "openrouter": ("--auto",),
}

# Trailing positional args appended after all flags. codex exec only reads
# the prompt from stdin when given the `-` sentinel; claude (-p) and gemini
# read piped stdin natively.
PROMPT_STDIN_ARGS: Dict[str, Tuple[str, ...]] = {
    "claude": (),
    "codex":  ("-",),
    "gemini": (),
    "openrouter": (),
}

# Flag each provider CLI uses to pin a model. Appended only when the
# resolved engine names a model; otherwise the CLI's own default applies.
# opencode addresses models as `openrouter/<author>/<slug>`, so the model
# value gets that prefix in build_provider_command.
MODEL_FLAGS: Dict[str, Tuple[str, ...]] = {
    "claude": ("--model",),
    "codex":  ("-m",),
    "gemini": ("-m",),
    "openrouter": ("-m",),
}

# Auth vars each provider CLI reads. Each invocation's environment exempts
# only the invoked provider's own vars from .env key-stripping, so an API key
# placed in .env reaches its provider in every phase while other providers'
# subprocesses never see it. Consequence when claude runs a phase: an
# ANTHROPIC_API_KEY present in .env reaches that claude subprocess; billing is
# expected to follow the key.
PROVIDER_AUTH_VARS: Dict[str, Tuple[str, ...]] = {
    "claude": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL"),
    "codex": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "openrouter": ("OPENROUTER_API_KEY",),
}


def build_provider_command(
    cli: str, provider: str, model: Optional[str]
) -> List[str]:
    """Assemble the provider argv: base command, transcript and permission
    flags, model flag (when a model is resolved), then prompt-stdin args."""
    cmd: List[str] = [
        cli,
        *CLI_COMMANDS[provider][1:],
        *TRANSCRIPT_FLAGS[provider],
        *PERMISSION_FLAGS[provider],
    ]
    if model is not None:
        value = f"openrouter/{model}" if provider == "openrouter" else model
        cmd.extend([*MODEL_FLAGS[provider], value])
    cmd.extend(PROMPT_STDIN_ARGS[provider])
    return cmd


def build_config_fingerprint(config: Config) -> Dict[str, Any]:
    """Config fields that affect output content.

    Behavior-only flags (timeouts, ``generate_pdf``, ``verbose``) are
    excluded so changing them between runs doesn't trigger needless re-runs.

    The resolved engine of every bucket is always included; comparison
    against state files recorded before engine tracking is handled by
    ``_is_spurious_engine_change``.
    """
    fingerprint: Dict[str, Any] = {
        'provider': config.provider,
        'mode': config.mode,
        'claims_path': str(config.claims_path) if config.claims_path else None,
    }
    for bucket, engine in config.resolved_engines().items():
        fingerprint[f'engine_{bucket}'] = engine
    return fingerprint


def _coerce_manager_target(target: str) -> str:
    """Map a manager re-run target onto a phase the retry loop can run.

    The loop body re-runs plan and replicate only; regenerating code is not
    reachable from inside the loop, so a codegen target downgrades to plan
    (the closest phase whose re-run the loop supports) and the codegen stage
    and its sentinel stay intact."""
    return "plan" if target == "codegen" else target


def _read_engine_meta(meta_path: Path) -> Optional[Dict[str, Any]]:
    """Sidecar describing the settings that produced an evaluate-bucket
    output. None when absent or unreadable (outputs from before this
    tracking, or a corrupt sidecar)."""
    if not meta_path.exists():
        return None
    try:
        data = json.loads(meta_path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _write_engine_meta(meta_path: Path, meta: Dict[str, Any]) -> None:
    """Best-effort sidecar write; the phases it serves are advisory and must
    never fail over bookkeeping. A failed write is loud because an output
    without a sidecar is treated as pre-tracking and never re-run on a
    settings change."""
    try:
        meta_path.write_text(json.dumps(meta, indent=2), encoding='utf-8')
    except OSError as e:
        print(f"  Warning: could not record settings sidecar {meta_path} ({e})")


def _is_spurious_engine_change(
    field: str, recorded: Dict[str, Any], current: Dict[str, Any]
) -> bool:
    """True when an engine field only appears changed because the recorded
    run predates engine tracking.

    A recorded config with no ``engine_*`` keys describes a run whose every
    bucket resolved to the recorded global provider with no model pin, so
    the comparison baseline for each engine is that provider string rather
    than "missing". Without this, the first run that sets any model knob
    against an older output dir would invalidate every stage instead of
    only the stages whose engine actually changed.
    """
    if not field.startswith('engine_'):
        return False
    if any(key.startswith('engine_') for key in recorded):
        return False
    return current.get(field) == (recorded.get('provider') or '').lower()


# Per-million-token pricing for known models (input_price, output_price).
# Unknown models produce None for estimated_cost_usd_approximate; the
# resource-estimation prompt instructs the LLM to search for pricing in that
# case and record it narratively in breakdown_notes.
KNOWN_MODEL_PRICING: Dict[str, Tuple[float, float]] = {
    "claude-sonnet-4-6": (3.00,  15.00),
    "claude-opus-4-8":   (15.00, 75.00),
    "claude-haiku-4-5":  (0.80,   4.00),
    "gpt-4o":            (2.50,  10.00),
    "gpt-4o-mini":       (0.15,   0.60),
    "gemini-2.0-flash":  (0.10,   0.40),
}

# Per-field stage invalidation rules. When an input or config field changes
# between runs against the same output dir, the listed stages are dropped from
# pipeline state so they re-run. Input and provider/mode fields invalidate the
# full downstream pipeline; engine_* fields are scoped to the stages their
# bucket feeds (a verify-engine change re-runs verify alone).
FINGERPRINT_INVALIDATES: Dict[str, Tuple[str, ...]] = {
    # Inputs
    'repo_path':     ('analyze', 'plan', 'resource_estimate', 'replicate', 'assess_fixes', 'verify'),
    'paper_path':    ('analyze', 'codegen', 'plan', 'resource_estimate', 'replicate', 'assess_fixes', 'verify'),
    'paper_sha256':  ('analyze', 'codegen', 'plan', 'resource_estimate', 'replicate', 'assess_fixes', 'verify'),
    'data_path':     ('analyze', 'codegen', 'plan', 'resource_estimate', 'replicate', 'assess_fixes', 'verify'),
    # Config. The provider row invalidates nothing itself: a provider change
    # always shows up in the per-bucket engine fields below, which scope the
    # invalidation to the stages each bucket feeds.
    'provider':      (),
    'mode':          ('analyze', 'codegen', 'plan', 'resource_estimate', 'replicate', 'assess_fixes', 'verify'),
    'claims_path':   ('analyze', 'plan', 'resource_estimate', 'replicate', 'assess_fixes', 'verify'),
    # Per-bucket engines (resolved provider:model). Scoped: a verify-engine
    # change re-adjudicates an existing run without re-replicating.
    # engine_analyze skips codegen (codegen consumes the paper, never the
    # claims) and covers resource_estimate (its LLM pass consumes the plan).
    # engine_evaluate maps to no state-tracked stage: the
    # contextual-evaluation and citation outputs track their producing
    # settings in sidecar files and re-run on a settings change themselves;
    # manager/research engine changes apply to future loop iterations only.
    'engine_analyze':   ('analyze', 'plan', 'resource_estimate', 'replicate', 'assess_fixes', 'verify'),
    'engine_codegen':   ('codegen', 'plan', 'resource_estimate', 'replicate', 'assess_fixes', 'verify'),
    'engine_replicate': ('replicate', 'assess_fixes', 'verify'),
    'engine_assess':    ('assess_fixes',),
    'engine_verify':    ('verify',),
    'engine_evaluate':  (),
}


@dataclass
class RunResult:
    """Result of the full replication run."""
    success: bool
    verdicts: Optional[List[ClaimVerdict]] = None
    score: Optional[ReplicationScore] = None
    report_path: Optional[Path] = None
    pdf_path: Optional[Path] = None
    error: Optional[str] = None


class ReplicationRunner:
    """Orchestrates the replication pipeline."""

    def __init__(self, config: Config):
        self.config = config
        self.prompt_generator = PromptGenerator()
        self.report_generator = ReportGenerator()
        # Last-computed objective execution facts from the most recent
        # _replicate call; consumed by the manager retry loop (None until
        # replicate runs). These are facts, not a diligence verdict — the
        # manager does the judging.
        self._last_facts: Optional[ExecutionFacts] = None

    def run(self, dry_run: bool = False) -> RunResult:
        """Run the full pipeline: analyze -> replicate -> assess fixes -> verify -> report.

        Resumable: completed phases recorded in ``<output>/.veritas/pipeline_state.json``
        are skipped on re-invocation. Pass ``--restart`` at the CLI level to discard state.
        """
        try:
            self._check_provider_auth(buckets=self._active_buckets(dry_run))
            self._setup_output_dir()
            state = PipelineState(self.config.output_dir)

            if state.state.get('inputs') is None:
                state.record_inputs(self.config.repo_path, self.config.paper_path, data_path=self.config.data_path)
                state.record_config(build_config_fingerprint(self.config))
            else:
                self._reconcile_with_prior_run(state)

            for leak_bucket in self._leak_buckets():
                _, leak_model = self.config.engine_for(leak_bucket)
                if is_web_locked_slug(leak_model):
                    print(
                        f"WARNING: the {leak_bucket} bucket is configured with "
                        f"'{leak_model}', which has always-on web access. Its "
                        f"output can reach the replication agent, so fetched "
                        f"paper values can leak into replication, defeating "
                        f"the anti-leakage design. Proceeding anyway."
                    )

            # analyze (claims extraction only)
            if state.is_stage_completed('analyze'):
                print("[OK] analyze: skipped (already completed)")
                claims = self._load_paper_claims()
            else:
                state.start_stage('analyze')
                try:
                    claims = self._generate_paper_claims()
                    state.complete_stage('analyze', success=True)
                except _InsufficientSpec as e:
                    state.complete_stage(
                        'analyze',
                        success=False,
                        status_override=STATUS_INSUFFICIENT_SPEC,
                    )
                    self._run_insufficient_spec_bail(e.source_path)
                    return RunResult(success=True, score=None, report_path=self.config.report_md_path)
                except Exception:
                    state.complete_stage('analyze', success=False)
                    raise

            # codegen (paper-only mode only)
            if self.config.mode == "paper-only" and not dry_run:
                if state.is_stage_completed('codegen'):
                    print("[OK] codegen: skipped (already completed)")
                else:
                    self._clear_stale_codegen_sentinel(state)
                    state.start_stage('codegen')
                    try:
                        self._generate_code()
                        state.complete_stage('codegen', success=True)
                        # Invalidate any plan and resource estimate built before
                        # code existed (e.g. from a prior --dry-run on the same
                        # output dir) so they regenerate against the generated
                        # codebase.
                        state.invalidate_stages(['plan', 'resource_estimate'])
                    except Exception:
                        state.complete_stage('codegen', success=False)
                        raise

            # plan (now its own phase)
            if state.is_stage_completed('plan'):
                print("[OK] plan: skipped (already completed)")
                replication_plan = self._load_replication_plan()
            else:
                state.start_stage('plan')
                try:
                    replication_plan = self._generate_replication_plan(claims)
                    if replication_plan is not None:
                        self._validate_plan_claim_refs(replication_plan, claims)
                    state.complete_stage('plan', success=True)
                except Exception:
                    state.complete_stage('plan', success=False)
                    raise

            # resource estimation (non-fatal: a failed estimate never aborts replication)
            resource_estimate = None
            if state.is_stage_completed('resource_estimate'):
                print("[OK] resource_estimate: skipped (already completed)")
            else:
                state.start_stage('resource_estimate')
                try:
                    resource_estimate = self._estimate_resources(replication_plan, state)
                    state.complete_stage('resource_estimate', success=True)
                except Exception as e:
                    print(f"Warning: resource estimation failed (continuing): {e}")
                    state.complete_stage('resource_estimate', success=False)

            if dry_run:
                # Always read the full JSON from disk — it contains everything the LLM
                # wrote (estimated_cost_usd, paper_reported_compute, etc.), not just the
                # 5 minimal dataclass fields that to_dict() would return.
                if self.config.resource_estimate_path.exists():
                    try:
                        resource_estimate_data = json.loads(
                            self.config.resource_estimate_path.read_text(encoding="utf-8")
                        )
                    except (json.JSONDecodeError, OSError):
                        resource_estimate_data = resource_estimate.to_dict() if resource_estimate is not None else {}
                elif resource_estimate is not None:
                    resource_estimate_data = resource_estimate.to_dict()
                else:
                    resource_estimate_data = {}
                compute_class = resource_estimate_data.get("compute_class", "light")
                cost_tier = {"light": "< $1", "medium": "$1–$10", "heavy": "$10–$100+"}.get(compute_class, "unknown")
                reported = resource_estimate_data.get("paper_reported_compute") or resource_estimate_data.get("reported_compute")
                print("\nResource Estimate:")
                print(json.dumps(resource_estimate_data, indent=2))
                if reported:
                    print(f"\nPaper-reported compute: {reported}")
                print(f"Estimated cost tier (rough size class): {cost_tier}")
                print("Run without --dry-run to start replication.")
                return RunResult(success=True)

            # replicate (+ manager retry loop when max_iters > 1)
            evidence, replication_plan = self._replicate_with_manager_loop(
                state, claims, replication_plan
            )

            # assess_fixes
            if state.is_stage_completed('assess_fixes'):
                print("[OK] assess_fixes: skipped (already completed)")
                fix_assessment = self._load_fix_assessment()
            else:
                state.start_stage('assess_fixes')
                try:
                    fix_assessment = self._assess_fixes(evidence)
                    state.complete_stage('assess_fixes', success=True)
                except Exception:
                    state.complete_stage('assess_fixes', success=False)
                    raise

            # verify
            already_done = state.get_stage_outputs('verify').get('completed_claims', [])
            missing_claims = [c.id for c in claims.claims if c.id not in already_done]

            if state.is_stage_completed('verify') and not missing_claims:
                print("[OK] verify: skipped (already completed)")
                verdicts = self._load_verify_artifacts(claims)
            else:
                self._clear_stale_verify_artifacts(state)
                if state.get_stage_status('verify') != 'in_progress':
                    state.start_stage('verify')
                    if already_done:
                        state.update_stage_outputs('verify', {'completed_claims': already_done})
                try:
                    verdicts = self._verify_with_resume(
                        claims, replication_plan,
                        state, already_done=already_done,
                    )
                    state.complete_stage('verify', success=True)
                except Exception:
                    state.complete_stage('verify', success=False)
                    raise

            score = self._score_after_verify(claims, verdicts)

            # Optional, opt-in contextual-evaluation phase (external checker).
            # Advisory only: does not feed the Replication Score. Idempotent via
            # a file-exists check so it doesn't re-run on resume.
            if self.config.run_evaluation:
                try:
                    self._evaluate()
                except Exception as e:
                    print(f"  Warning: contextual evaluation failed (continuing): {e}")

            # Optional, opt-in citation-check submodule (reference verification).
            # Advisory only: does not feed the Replication Score. Idempotent.
            # The guard makes the advisory contract structural: no failure in
            # the citation phase can flip the run itself to failed.
            if self.config.run_citation_check:
                try:
                    self._check_citations()
                except Exception as e:
                    print(f"  Warning: citation check failed ({e}); continuing without it")

            try:
                self._collect_resource_usage(state)
            except Exception as e:
                print(f"  Warning: Could not collect resource usage: {e}")

            report_path, pdf_path = self._report(
                claims, verdicts, score, evidence, fix_assessment,
            )
            state.mark_completed()

            return RunResult(
                success=True,
                verdicts=verdicts,
                score=score,
                report_path=report_path,
                pdf_path=pdf_path,
            )

        except Exception as e:
            return RunResult(success=False, error=str(e))

        finally:
            try:
                sanitize_logs_directory(self.config.output_dir)
            except Exception:
                pass

    def _setup_output_dir(self):
        """Create the output directory structure."""
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        for subdir in OUTPUT_SUBDIRS:
            (self.config.output_dir / subdir).mkdir(exist_ok=True)

    # -- Phase 1: Analyze --------------------------------------------------

    def _run_insufficient_spec_bail(self, source_path: Path) -> None:
        """Write the bail report when analyze produces zero claims; downstream phases are skipped."""
        print(
            f"\n[INSUFFICIENT_SPEC] Analyze produced 0 claims from {source_path}. "
            f"Writing bail report and exiting."
        )
        report_md = self.prompt_generator.generate_insufficient_spec_report(
            mode=self.config.mode,
            source_path=source_path,
            has_paper=self.config.has_paper,
        )
        self.config.report_md_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.report_md_path.write_text(report_md, encoding='utf-8')
        print(f"  Report written to {self.config.report_md_path}")

    def _generate_paper_claims(self) -> PaperClaims:
        """Generate or load paper claims.

        Sources, in priority order:
          1. ``--claims`` user file (validate + copy to ``analyze/paper_claims.json``)
          2. ``--paper`` PDF (extract via LLM)
          3. ``<repo>/README`` (mode 3 — extract via LLM, treat README as spec)

        Raises ``_InsufficientSpec`` when extraction yields 0 claims.
        """
        if self.config.has_user_claims:
            return self._load_user_claims(self.config.claims_path)

        if self.config.has_paper:
            return self._extract_claims_from_paper()

        if self.config.has_repo:
            return self._extract_claims_from_readme()

        raise RuntimeError(
            "No claim source available: provide --paper, --repo, or --claims"
        )

    def _load_user_claims(self, path: Path) -> PaperClaims:
        """Validate a user-supplied claims JSON file and copy it into the output tree."""
        print(f"Loading user-supplied claims from {path}...")
        raw = path.read_text(encoding='utf-8')
        claims = PaperClaims.from_dict(json.loads(raw))
        if len(claims.claims) == 0:
            raise _InsufficientSpec(path, self.config.mode)

        self.config.paper_claims_path.write_text(
            json.dumps(claims.to_dict(), indent=2), encoding='utf-8'
        )

        n_h = len(claims.by_tier("headline"))
        n_s = len(claims.by_tier("supporting"))
        print(
            f"  Loaded {len(claims.claims)} claims "
            f"({n_h} headline, {n_s} supporting)"
        )
        return claims

    def _extract_claims_from_paper(self) -> PaperClaims:
        """Extract claims via LLM from the paper PDF."""
        print("Extracting paper claims...")
        return self._run_claim_extraction(
            readme_path=None,
            source_for_bail=self.config.paper_path,
        )

    def _extract_claims_from_readme(self) -> PaperClaims:
        """Extract claims via LLM from the repo's README (repo-only mode)."""
        readme_path = self._find_readme()
        if readme_path is None:
            raise _InsufficientSpec(
                self.config.repo_path / "README.md", self.config.mode
            )
        print(f"Extracting claims from README at {readme_path}...")
        return self._run_claim_extraction(
            readme_path=readme_path,
            source_for_bail=readme_path,
        )

    def _find_readme(self) -> Optional[Path]:
        """Locate a README in the repo root (case variants tried in order)."""
        if self.config.repo_path is None:
            return None
        for name in ("README.md", "README.rst", "readme.md", "Readme.md"):
            candidate = self.config.repo_path / name
            if candidate.exists():
                return candidate
        return None

    def _run_claim_extraction(
        self,
        readme_path: Optional[Path],
        source_for_bail: Path,
    ) -> PaperClaims:
        """Common LLM-driven extraction path. Raises ``_InsufficientSpec`` on 0 claims."""
        prompt = self.prompt_generator.generate_paper_claims_prompt(
            repo_path=self.config.repo_path,
            output_dir=self.config.output_dir,
            paper_path=self.config.paper_path if self.config.has_paper else None,
            readme_path=readme_path,
        )

        prompt_path = self.config.prompts_dir / "paper_claims_prompt.txt"
        prompt_path.write_text(prompt, encoding='utf-8')

        output_json_path = self.config.paper_claims_path
        log_path = self.config.paper_claims_transcript_path

        working_dir = self.config.repo_path or self.config.output_dir
        success = self._invoke_provider(
            prompt=prompt,
            working_dir=working_dir,
            log_path=log_path,
            timeout=self.config.analyze_timeout,
            bucket="analyze",
        )

        if not success:
            raise RuntimeError(
                f"Paper claims extraction failed: provider invocation did not succeed "
                f"(transcript: {log_path})"
            )

        if not output_json_path.exists():
            raise RuntimeError(
                f"Paper claims extraction failed: agent did not write {output_json_path}"
            )

        response_text = output_json_path.read_text(encoding='utf-8').strip()
        if not response_text:
            raise RuntimeError(
                f"Paper claims extraction failed: {output_json_path} is empty"
            )

        try:
            claims = parse_paper_claims_response(response_text)
        except ValueError as e:
            print(f"  Warning: Could not parse paper claims: {e}")
            print("  Retrying with repair prompt...")
            claims = self._repair_json_response(
                original_prompt=prompt,
                broken_output=response_text,
                output_path=output_json_path,
                log_path=log_path,
                parser=parse_paper_claims_response,
                timeout=self.config.analyze_timeout,
                working_dir=working_dir,
                bucket="analyze",
            )

        if claims is None:
            raise RuntimeError(
                "Paper claims extraction failed: could not parse response even after repair"
            )

        if len(claims.claims) == 0:
            raise _InsufficientSpec(source_for_bail, self.config.mode)

        output_json_path.write_text(
            json.dumps(claims.to_dict(), indent=2), encoding='utf-8'
        )

        n_h = len(claims.by_tier("headline"))
        n_s = len(claims.by_tier("supporting"))
        print(
            f"  Extracted {len(claims.claims)} claims "
            f"({n_h} headline, {n_s} supporting)"
        )
        return claims

    # -- Phase 1.5: Codegen (paper-only mode) ------------------------------

    def _generate_code(self) -> None:
        """Paper-only mode: have the agent write the paper's methodology into
        <replication>/codebase/. Resume primitive: sentinel file at
        <output>/.veritas/codegen_complete. Partial codebases from killed sessions
        are wiped before retry. Anti-leakage: paper_claims.json is intentionally
        not in this method's scope."""

        sentinel = self.config.codegen_complete_sentinel_path
        if sentinel.exists():
            print("[OK] codegen: skipped (sentinel exists from prior completed run)")
            return

        codebase_dir = self.config.replication_dir / "codebase"

        # Wipe any partial codebase from a killed prior session
        if codebase_dir.exists() and any(codebase_dir.iterdir()):
            # Defensive: refuse to rmtree anything not strictly under the output tree
            resolved = codebase_dir.resolve()
            output_root = self.config.output_dir.resolve()
            if output_root not in resolved.parents:
                raise RuntimeError(
                    f"Refusing to wipe codebase_dir {resolved}: "
                    f"not under output tree {output_root}"
                )
            print(f"  Wiping partial codebase at {codebase_dir} before retry")
            shutil.rmtree(codebase_dir)
        codebase_dir.mkdir(parents=True, exist_ok=True)

        print("Generating code from paper...")
        prompt = self.prompt_generator.generate_codegen_prompt(
            paper_path=self.config.paper_path,
            output_dir=self.config.output_dir,
            data_path=self.config.data_path,
        )

        prompt_path = self.config.prompts_dir / "codegen_prompt.txt"
        prompt_path.write_text(prompt, encoding='utf-8')

        log_path = self.config.codegen_transcript_path

        success = self._invoke_provider(
            prompt=prompt,
            working_dir=codebase_dir,
            log_path=log_path,
            timeout=self.config.codegen_timeout,
            bucket="codegen",
        )

        if not success:
            raise RuntimeError(
                f"Codegen failed: provider invocation did not succeed "
                f"(transcript: {log_path})"
            )

        # Sanity check: the codebase should be non-empty
        contents = list(codebase_dir.iterdir())
        if not contents:
            raise RuntimeError(
                f"Codegen failed: agent did not write any files to {codebase_dir}"
            )

        n_files = sum(1 for _ in codebase_dir.rglob('*') if _.is_file())
        print(f"  Codegen wrote {n_files} file(s) to {codebase_dir}")

        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.touch()

        # Snapshot the pristine generated tree so a later replicate re-run in
        # paper-only mode can start from unpatched code (repo-backed modes
        # re-stage from the repo instead).
        snapshot = self.config.veritas_state_dir / "codegen_snapshot"
        if snapshot.exists():
            shutil.rmtree(snapshot)
        shutil.copytree(codebase_dir, snapshot, symlinks=True)

    def _generate_replication_plan(
        self,
        claims: PaperClaims,
        manager_guidance: Optional["ManagerGuidance"] = None,
    ) -> Optional[ReplicationPlan]:
        """Generate a replication plan whose steps reference claim IDs.

        ``manager_guidance`` is set only on a manager-directed re-run that
        targets the plan phase; it is threaded into the prompt so the
        regenerated plan addresses the prior deficiency (never a blank repeat).
        """
        print("Generating replication plan...")

        effective_repo_path = self.config.effective_repo_path

        prompt = self.prompt_generator.generate_replication_plan_prompt(
            repo_path=effective_repo_path,
            output_dir=self.config.output_dir,
            claims=claims,
            paper_path=self.config.paper_path if self.config.has_paper else None,
            mode=self.config.mode,
            data_path=self.config.data_path,
            manager_guidance=manager_guidance,
        )

        prompt_path = self.config.prompts_dir / "replication_plan_prompt.txt"
        prompt_path.write_text(prompt, encoding='utf-8')

        output_path = self.config.replication_plan_path
        log_path = self.config.replication_plan_transcript_path

        success = self._invoke_provider(
            prompt=prompt,
            working_dir=effective_repo_path,
            log_path=log_path,
            timeout=self.config.analyze_timeout,
            bucket="analyze",
        )

        if not success:
            print(f"  Warning: Provider invocation did not succeed (transcript: {log_path}), skipping replication phase")
            return None

        if not output_path.exists():
            print(f"  Warning: Agent did not write {output_path}, skipping replication phase")
            return None

        response_text = output_path.read_text(encoding='utf-8').strip()
        if not response_text:
            print(f"  Warning: {output_path} is empty, skipping replication phase")
            return None

        try:
            plan = parse_replication_plan_response(response_text)
        except ValueError as e:
            print(f"  Warning: Could not parse replication plan: {e}")
            print("  Retrying with repair prompt...")
            plan = self._repair_json_response(
                original_prompt=prompt,
                broken_output=response_text,
                output_path=output_path,
                log_path=log_path,
                parser=parse_replication_plan_response,
                timeout=self.config.analyze_timeout,
                working_dir=effective_repo_path,
                bucket="analyze",
            )

        if plan is None:
            return None

        output_path.write_text(
            json.dumps(plan.to_dict(), indent=2), encoding='utf-8'
        )
        print(f"  Generated replication plan with {len(plan.steps)} steps")
        return plan

    def _validate_plan_claim_refs(
        self,
        plan: ReplicationPlan,
        claims: PaperClaims,
    ) -> None:
        """Warn if plan steps reference claim IDs that don't exist in the claims set.

        Non-fatal — a misreferenced ID just means the verifier won't get the
        step-id hint for that claim. Surfaced so the user knows the analyze
        phase's two outputs disagreed.
        """
        valid_ids = claims.claim_ids()
        unknown_refs: Dict[int, List[str]] = {}
        for step in plan.steps:
            bad = [cid for cid in step.verifies if cid not in valid_ids]
            if bad:
                unknown_refs[step.id] = bad

        if unknown_refs:
            print(
                "  Warning: replication plan references claim IDs that don't exist:"
            )
            for step_id, bad in unknown_refs.items():
                print(f"    Step {step_id}: {', '.join(bad)}")

    def _repair_json_response(
        self,
        original_prompt: str,
        broken_output: str,
        output_path: Path,
        log_path: Path,
        parser,
        timeout: Optional[int],
        working_dir: Path,
        bucket: str,
    ):
        """Re-prompt the provider to fix invalid JSON output.

        Appends the broken output to the original prompt and asks the
        provider to return valid JSON only. The transcript of this
        re-invocation is appended to ``log_path`` so the original failed
        attempt and the repair attempt share one file.
        """
        repair_prompt = (
            original_prompt
            + "\n\n---\n\n"
            + "Your last output was not valid JSON. Here is what you returned:\n\n"
            + broken_output[:2000]
            + f"\n\nRewrite {output_path} so it contains ONLY valid JSON, "
            + "with no explanation or markdown formatting."
            + "\n\nCommon JSON mistakes to avoid:"
            + "\n- Double quotes inside strings MUST be escaped: use \\\" not \""
            + "\n- Backslash-single-quote (\\') is not valid JSON — just use '"
            + "\n- Regex patterns in strings need double-escaped backslashes: use \\\\s not \\s"
            + "\n- If a command_hint contains Python code with double quotes, escape them"
        )

        success = self._invoke_provider(
            prompt=repair_prompt,
            working_dir=working_dir,
            log_path=log_path,
            timeout=timeout,
            bucket=bucket,
            append=True,
        )

        if not success:
            print("  Warning: Repair invocation did not succeed")
            return None

        if not output_path.exists():
            print(f"  Warning: Repair did not produce {output_path}")
            return None

        response_text = output_path.read_text(encoding='utf-8').strip()
        if not response_text:
            print(f"  Warning: Repair produced empty {output_path}")
            return None

        try:
            return parser(response_text)
        except ValueError as e:
            print(f"  Warning: Repair also failed: {e}")
            return None

    # -- Phase 2: Replicate ------------------------------------------------

    def _replicate(
        self,
        replication_plan: Optional[ReplicationPlan],
        manager_guidance: Optional["ManagerGuidance"] = None,
    ) -> Optional[ExecutionEvidence]:
        """Phase 2: Execute replication via the configured provider.

        Runs as an in-process subprocess. When the whole veritas CLI is
        running inside the veritas Docker image (the production path),
        the provider sees `/workspace/repo` as the read-only repo and
        `/workspace/output` as the writable scratch space. When run
        outside Docker (dev-time), paths come from `self.config` as-is.

        ``manager_guidance`` is set only on a manager-directed re-run; it is
        threaded into the session prompt's guidance block so the re-run is
        genuinely different (deficiency + new instructions + already-tried).
        """
        if replication_plan is None:
            print("No replication plan available, skipping replication phase")
            return None

        if manager_guidance is not None:
            print(
                f"Running replication phase (manager-directed re-run, "
                f"iteration {manager_guidance.iteration})..."
            )
        else:
            print("Running replication phase...")

        session_instructions = self.prompt_generator.generate_replication_session_prompt(
            replication_plan,
            output_dir=self.config.output_dir,
            paper_path=self.config.paper_path,
            repo_path=self.config.repo_path,
            mode=self.config.mode,
            data_path=self.config.data_path,
            manager_guidance=manager_guidance,
        )

        log_path = self.config.replication_transcript_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

        prompt_path = self.config.prompts_dir / "replication_session_prompt.txt"
        prompt_path.write_text(session_instructions, encoding='utf-8')

        success = self._invoke_provider(
            prompt=session_instructions,
            working_dir=self.config.effective_repo_path,
            log_path=log_path,
            timeout=self.config.replicate_timeout,
            bucket="replicate",
            expose_api_keys=True,
        )

        if not success:
            print(f"  Warning: Provider invocation did not succeed (transcript: {log_path})")

        evidence = gather_evidence(self.config.replication_dir)

        if evidence:
            print(f"  Replication completed: {evidence.steps_succeeded}/{evidence.steps_attempted} steps succeeded")
        else:
            print("  Warning: No evidence collected from replication")

        # Compute objective execution facts over the replicate evidence and
        # persist them. These are facts (step counts, exit codes, declared
        # outputs, repeated commands) — NOT a diligence verdict; the manager
        # judges diligence from these facts + the trajectory. The facts are
        # stashed on the runner so the manager loop (when enabled) can consume
        # them without recomputing. With ``max_iters == 1`` the loop never runs
        # and this stays log-only.
        self._last_facts = self._compute_and_write_execution_facts(
            evidence, replication_plan
        )

        return evidence

    # -- Phase 2 loop: replicate + manager-controlled retries --------------

    def _replicate_with_manager_loop(
        self,
        state: PipelineState,
        claims: PaperClaims,
        replication_plan: Optional[ReplicationPlan],
    ) -> Tuple[Optional[ExecutionEvidence], Optional[ReplicationPlan]]:
        """Run replicate, then (when ``max_iters > 1``) the manager retry loop.

        The loop sits AFTER replicate and BEFORE verify. Each iteration: compute
        objective execution facts (already done inside ``_replicate``), run the
        manager review — which ALWAYS does the diligence judging (there is no
        deterministic short-circuit-accept) — log to the workflow artifact, and
        — on a genuine-deficiency ``revise`` within budget — archive the prior
        attempt, invalidate the target phase + downstream, and re-run with the
        manager's directive injected. Hard cap + no-progress terminator (over
        the objective facts) + graceful hand-off enforced here in python.

        With ``max_iters <= 1`` (the default for ``replicate`` / the benchmark)
        the manager never runs: behavior is identical to a single pass.
        Resume-safe: a completed-and-accepted replicate is skipped; an
        in-progress loop re-enters at the correct iteration via the workflow log.
        """
        max_iters = max(1, int(self.config.max_iters))
        workflow = WorkflowLog(self.config.veritas_state_dir)

        # --- First pass (or resume of a completed replicate) ---------------
        if state.is_stage_completed('replicate'):
            print("[OK] replicate: skipped (already completed)")
            evidence = gather_evidence(self.config.replication_dir)
            # Recompute facts for the loop (cheap, pure) when the loop is on
            # and we don't already have them from this process.
            if max_iters > 1 and self._last_facts is None:
                self._last_facts = self._compute_and_write_execution_facts(
                    evidence, replication_plan
                )
        else:
            self._refresh_codebase_if_stale(state)
            state.start_stage('replicate')
            try:
                evidence = self._replicate(replication_plan)
                state.complete_stage('replicate', success=True)
            except Exception:
                state.complete_stage('replicate', success=False)
                raise

        # Loop off: single-pass, no manager gate, no workflow log entries.
        if max_iters <= 1:
            return evidence, replication_plan

        # --- Resume guard: a prior process already converged ----------------
        # If replicate was skipped (already completed) AND the workflow log shows
        # the loop already reached a terminal state (an accept verdict or a
        # hand-off), do not re-run the manager — the trajectory is settled.
        prior_records = workflow.records()
        if state.is_stage_completed('replicate') and prior_records:
            last_review = next(
                (r for r in reversed(prior_records) if r.get("phase") == "manager_review"),
                None,
            )
            already_accepted = (
                last_review is not None
                and (last_review.get("manager_verdict") or {}).get("decision") == "accept"
            )
            has_handoff = any(r.get("phase") == "handoff" for r in prior_records)
            if already_accepted or has_handoff:
                print("[OK] manager loop: skipped (already converged on a prior run)")
                return evidence, replication_plan

        # --- Determine where we are in the loop (resume-aware) -------------
        prior_runs = [r for r in prior_records if r.get("phase") == "replicate"]
        iteration = len(prior_runs) if prior_runs else 1
        if iteration < 1:
            iteration = 1
        # If the workflow log has no replicate entry yet, this first pass is
        # iteration 1; record it.
        if not prior_runs:
            workflow.append(self._workflow_replicate_record(iteration, self._last_facts, None))

        prev_facts: Optional[ExecutionFacts] = None
        prev_directive: Optional[str] = None
        last_verdict: Optional[ManagerVerdict] = None

        # --- Bounded review→decide→(accept|revise) loop --------------------
        while True:
            facts = self._last_facts
            retries_remaining = max(0, max_iters - iteration)

            prior_guidance = (
                ManagerGuidance.from_verdict(last_verdict, iteration=iteration)
                if last_verdict is not None and last_verdict.decision == "revise"
                else None
            )
            verdict = self._manager_review(
                facts,
                iteration=iteration,
                retries_remaining=retries_remaining,
                prior_guidance=prior_guidance,
            )

            workflow.append(
                self._workflow_review_record(iteration, facts, verdict)
            )

            stop = should_stop(
                verdict=verdict,
                iteration=iteration,
                max_iters=max_iters,
                prev_signals=prev_facts,
                curr_signals=facts,
                prev_directive=prev_directive,
            )

            if stop.stop:
                if verdict.accepted:
                    print(f"  Manager ACCEPTED replication at iteration {iteration}.")
                else:
                    # Graceful terminal: cap or no-progress without acceptance.
                    handoff = build_handoff(
                        iteration=iteration,
                        verdict=verdict,
                        signals=facts,
                        stop_reason=stop.reason,
                    )
                    workflow.write_handoff(handoff)
                    print(
                        f"  Manager did NOT accept (stop reason: {stop.reason}); "
                        f"wrote unresolved hand-off to {workflow.md_path}"
                    )
                return evidence, replication_plan

            # --- Re-run: archive, invalidate, inject guidance, replicate ---
            requested_target = verdict.target_phase or "replicate"
            target = _coerce_manager_target(requested_target)
            if target != requested_target:
                print(
                    f"  Manager targeted '{requested_target}'; the retry loop "
                    f"re-runs plan+replicate only — using '{target}'."
                )
            guidance = ManagerGuidance.from_verdict(verdict, iteration=iteration + 1)

            # Phase 3: if the manager requested methodology/resource research,
            # dispatch the matching sub-agent(s), redact each finding, and fold
            # the provenance-tagged, post-redaction methodology into the re-run
            # guidance. Bounded + opt-in; runs only inside the loop. Never raises
            # into the pipeline — research is best-effort augmentation.
            guidance.research_findings = self._run_research(
                verdict, claims, iteration=iteration, workflow=workflow
            )

            archived = archive_attempt(self.config.replication_dir, iteration)
            if archived is not None:
                print(f"  Archived attempt {iteration} -> {archived}")

            # Invalidate the target phase + downstream so they re-run.
            self._invalidate_for_rerun(state, target)

            prev_facts = facts
            prev_directive = verdict.directive
            last_verdict = verdict
            iteration += 1

            print(
                f"  Manager REVISE: re-running '{target}' "
                f"(iteration {iteration}/{max_iters}) with new directive."
            )

            # Re-run the plan first if the manager targeted it, then replicate.
            if target == "plan":
                state.start_stage('plan')
                try:
                    replication_plan = self._generate_replication_plan(
                        claims, manager_guidance=guidance
                    )
                    if replication_plan is not None:
                        self._validate_plan_claim_refs(replication_plan, claims)
                    state.complete_stage('plan', success=True)
                except Exception:
                    state.complete_stage('plan', success=False)
                    raise

            # Replicate again. The guidance is always surfaced to the replicate
            # agent (even on a plan-targeted re-run the deficiency is relevant to
            # how it executes), so the re-run is never a blank repeat.
            state.start_stage('replicate')
            try:
                evidence = self._replicate(replication_plan, manager_guidance=guidance)
                state.complete_stage('replicate', success=True)
            except Exception:
                state.complete_stage('replicate', success=False)
                raise

            workflow.append(
                self._workflow_replicate_record(
                    iteration, self._last_facts, archived, guidance=guidance
                )
            )

    def _invalidate_for_rerun(self, state: PipelineState, target_phase: str) -> None:
        """Invalidate the manager's target phase + all downstream phases.

        Uses the existing per-field invalidation map as the canonical phase
        ordering so a re-run cleanly discards stale downstream state (assess /
        verify) and they recompute against the new attempt.
        """
        order = ['analyze', 'plan', 'resource_estimate',
                 'replicate', 'assess_fixes', 'verify']
        if target_phase not in order:
            target_phase = 'replicate'
        idx = order.index(target_phase)
        to_invalidate = order[idx:]
        state.invalidate_stages(to_invalidate)

    def _refresh_codebase_if_stale(self, state: PipelineState) -> None:
        """Re-stage ``replication/codebase`` from its pristine source when a
        prior replicate attempt was discarded (no stage record, but attempt
        artifacts show one ran).

        The source is the repo in repo-backed modes and the codegen
        snapshot in paper-only mode. Without this, a replicate re-run
        (e.g. after an engine change) would start from the previous
        attempt's patched tree. Fresh runs skip — their staged tree is
        already pristine, and re-copying it would only burn time. An
        in_progress record is a partial attempt and is left in place. Not
        reached inside the manager loop, which deliberately continues on
        the patched tree.
        """
        if state.get_stage_status('replicate') is not None:
            return
        prior_attempt = (
            self.config.replication_transcript_path.exists()
            or (self.config.replication_dir / "codebase.diff").exists()
        )
        if not prior_attempt:
            return
        if self.config.mode == "paper-only":
            source = self.config.veritas_state_dir / "codegen_snapshot"
            if not source.exists():
                return
        elif self.config.has_repo:
            source = self.config.repo_path
        else:
            return
        codebase_dir = self.config.replication_dir / "codebase"
        if not codebase_dir.exists() or not any(codebase_dir.iterdir()):
            return
        resolved = codebase_dir.resolve()
        output_root = self.config.output_dir.resolve()
        if output_root not in resolved.parents:
            raise RuntimeError(
                f"refusing to reset {resolved}: not under the output tree {output_root}"
            )
        print("  Re-staging replication/codebase from its pristine source for a fresh attempt")
        shutil.rmtree(codebase_dir)
        shutil.copytree(source, codebase_dir, symlinks=True)

    def _clear_stale_verify_artifacts(self, state: PipelineState) -> None:
        """Remove leftover per-claim verdict files when verify has no stage
        record (fresh dir, --restart, or invalidation).

        Verify resumes per claim on verdict-file existence, so files from a
        discarded attempt would otherwise be silently reused and attributed
        to the current engine. Runs at verify entry — never at reconcile
        time — so a dry run or an aborted resume cannot destroy a completed
        run's verdicts and score. A present record (in_progress) means a
        legitimate partial resume: files are kept.
        """
        if state.get_stage_status('verify') is not None:
            return
        if not self.config.verify_dir.exists():
            return
        for verdict_file in self.config.verify_dir.glob('*.json'):
            verdict_file.unlink(missing_ok=True)

    def _clear_stale_codegen_sentinel(self, state: PipelineState) -> None:
        """Remove the codegen sentinel when codegen has no stage record
        (fresh dir, --restart, or invalidation).

        The sentinel is codegen's resume primitive; one left over from a
        discarded attempt would make ``_generate_code`` skip regeneration.
        A present record (in_progress) means a crash between the sentinel
        write and the stage completion: the sentinel stays authoritative.
        """
        if state.get_stage_status('codegen') is None:
            self.config.codegen_complete_sentinel_path.unlink(missing_ok=True)

    def _workflow_replicate_record(
        self,
        iteration: int,
        facts: Optional[ExecutionFacts],
        archived_attempt_path: Optional[Path],
        guidance: Optional[ManagerGuidance] = None,
    ) -> Dict[str, Any]:
        rec: Dict[str, Any] = {
            "iteration": iteration,
            "phase": "replicate",
            "status": "completed",
            "transcript_path": str(self.config.replication_transcript_path),
            "signals": self._facts_record(facts),
            "manager_verdict": None,
            "directive": guidance.directive if guidance is not None else None,
            "archived_attempt_path": str(archived_attempt_path) if archived_attempt_path else None,
        }
        return rec

    def _workflow_review_record(
        self,
        iteration: int,
        facts: Optional[ExecutionFacts],
        verdict: ManagerVerdict,
    ) -> Dict[str, Any]:
        return {
            "iteration": iteration,
            "phase": "manager_review",
            "status": verdict.decision,
            "transcript_path": str(self.config.manager_review_transcript_path),
            "signals": self._facts_record(facts),
            "manager_verdict": verdict.to_dict(),
            "directive": verdict.directive or None,
            "archived_attempt_path": None,
        }

    @staticmethod
    def _facts_record(facts: Optional[ExecutionFacts]) -> Optional[Dict[str, Any]]:
        """Workflow-log payload for one run's objective execution facts.

        The JSON key in the workflow log stays ``"signals"`` for backward
        compatibility with the markdown renderer and existing logs, but the
        content is the objective execution facts (no diligence verdict).
        """
        if facts is None:
            return None
        d = facts.to_dict()
        d["summary_line"] = facts.summary_line()
        return d

    def _compute_and_write_execution_facts(
        self,
        evidence: Optional[ExecutionEvidence],
        replication_plan: Optional[ReplicationPlan],
    ) -> Optional[ExecutionFacts]:
        """Compute objective execution facts and write them to disk.

        Pure-compute + log; never raises into the pipeline. Writes
        ``replication/diligence_signals.json`` and prints a one-line summary,
        and returns the computed :class:`ExecutionFacts` (or ``None`` on
        failure) so the manager loop can consume them as evidence. With
        ``max_iters == 1`` the return value is simply ignored, preserving the
        prior log-only behavior. These are facts — NOT a diligence verdict; the
        manager judges diligence.
        """
        try:
            facts = compute_execution_facts(evidence, plan=replication_plan)

            out_path = self.config.diligence_signals_path
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(facts.to_dict(), indent=2),
                encoding="utf-8",
            )
            print(f"  Execution facts: {facts.summary_line()}")
            print(f"  Execution facts written to {out_path}")
            return facts
        except Exception as exc:  # never let facts computation break the run
            print(f"  Warning: execution-facts computation failed ({exc}); continuing")
            return None

    # -- Phase 2.5: Manager review (post-replicate control gate) -----------

    def _manager_review(
        self,
        facts: Optional[ExecutionFacts],
        *,
        iteration: int,
        retries_remaining: int,
        prior_guidance: Optional["ManagerGuidance"],
    ) -> ManagerVerdict:
        """Independent post-replicate control gate — the manager ALWAYS judges.

        There is NO deterministic short-circuit-accept. The manager (an
        independent LLM pass: fresh context, API keys stripped — it must NOT run
        paper code) ALWAYS runs and makes the accept/revise decision, reading the
        trajectory + evidence + the objective execution facts. Diligence
        questions (skipped/downsized/premature-stop/placeholder) are the
        manager's to assess from the real evidence, not pre-decided by
        keyword-matching code. This is distinct from the post-verify
        contextual-evaluation report author; it never alters the deterministic
        Replication Score.

        ``facts`` are the objective execution facts, passed to the prompt builder
        only as a place to surface evidence; they do not gate the call.

        Always returns a :class:`ManagerVerdict`; on any failure it falls back to
        ACCEPT (fail-open is the safe default for a control gate over an already-
        completed replication — we never block the score on a flaky judge call).
        """
        # The manager always runs — no clean-signals auto-accept path.
        print(
            f"  Manager: running independent review "
            f"(iteration {iteration}, {retries_remaining} retries remaining)..."
        )
        output_path = self.config.manager_review_path
        # Clear any stale verdict from a prior iteration so we read this run's.
        try:
            if output_path.exists():
                output_path.unlink()
        except OSError:
            pass

        prompt = self.prompt_generator.generate_manager_review_prompt(
            output_dir=self.config.output_dir,
            retries_remaining=retries_remaining,
            iteration=iteration,
            manager_guidance=prior_guidance,
        )
        prompt_path = self.config.prompts_dir / "manager_review_prompt.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")

        # Default env (API keys stripped) + working dir = output tree: the
        # manager reads artifacts but cannot run the paper's code. Mirrors the
        # contextual-evaluation checker's isolation.
        success = self._invoke_provider(
            prompt=prompt,
            working_dir=self.config.output_dir,
            log_path=self.config.manager_review_transcript_path,
            timeout=self.config.evaluate_timeout,
            bucket="evaluate",
        )
        if not success:
            print("  Warning: manager review pass did not succeed; defaulting to ACCEPT")
            return self._fallback_accept_verdict("manager review invocation failed")
        if not output_path.exists():
            print("  Warning: manager wrote no verdict; defaulting to ACCEPT")
            return self._fallback_accept_verdict("manager produced no verdict file")

        try:
            raw = json.loads(_extract_json(output_path.read_text(encoding="utf-8")))
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  Warning: manager verdict is not valid JSON ({e}); defaulting to ACCEPT")
            return self._fallback_accept_verdict(f"unparseable manager verdict: {e}")

        verdict = parse_manager_verdict(raw, source="llm")
        print(
            f"  Manager verdict: {verdict.decision.upper()} "
            f"(genuine={verdict.deficiency_is_genuine}, "
            f"target={verdict.target_phase}, confidence={verdict.confidence})"
        )
        if verdict.reason:
            print(f"    reason: {verdict.reason}")
        if verdict.decision == 'revise' and verdict.directive:
            print(f"    directive: {verdict.directive}")
        return verdict

    @staticmethod
    def _fallback_accept_verdict(reason: str) -> ManagerVerdict:
        return ManagerVerdict(
            decision="accept",
            diligence_sufficient=True,
            reason=f"manager fallback accept ({reason})",
            confidence=0.0,
            source="fallback",
        )

    # -- Phase 3: Manager research sub-agents (behind anti-leakage barriers) -

    def _run_research(
        self,
        verdict: ManagerVerdict,
        claims: Optional[PaperClaims],
        *,
        iteration: int,
        workflow: WorkflowLog,
    ) -> str:
        """Dispatch the manager's honored research requests; return guidance text.

        Phase 3. Reads ``verdict.research_requests``, applies the THREE structural
        anti-leakage barriers, and returns a provenance-tagged, post-redaction
        guidance block to fold into the re-run (or "" if nothing usable).

          a. **Intent allow-list** (``split_requests``): only ``resource`` /
             ``literature`` kinds are honored; answer-seeking requests are
             rejected and recorded as rejected.
          b. **Redaction before injection**: each finding goes through the LLM
             redactor (semantic, no keyword matching) and then a deterministic
             exact-string scrub of known ``paper_value`` strings, before it can
             reach the replicate agent.
          c. **Provenance-tagged injection** (``format_findings_for_guidance``):
             every injected item carries its source URL; the post-verify cheating
             monitor watches the re-run trace.

        Bounded by ``VERITAS_RESEARCH_MAX_CALLS`` (per iteration) and never raises
        into the pipeline. All requests, findings, redaction results, and what
        got injected are logged to the workflow trajectory.
        """
        research_cfg = ResearchConfig.from_env()
        requests = parse_research_requests(verdict.research_requests)
        if not requests:
            return ""

        honored, rejected = split_requests(requests)
        cap = research_cfg.max_calls_per_iteration
        # Apply the per-iteration cap on honored requests (bound the fan-out).
        capped = honored[:cap] if cap >= 0 else honored
        dropped_for_cap = honored[len(capped):]

        # Known reported-value strings for the deterministic belt-and-suspenders
        # scrub. The runner (python) holds these; the searcher/redactor agents
        # never receive them — this is an objective exact-match fact, not a hint.
        known_values = known_value_strings(
            [c.paper_value for c in claims.claims] if claims is not None else []
        )

        if not capped:
            workflow.append(self._workflow_research_record(
                iteration, honored=[], rejected=rejected,
                dropped_for_cap=dropped_for_cap, findings=[],
            ))
            return ""

        print(
            f"  Manager research: {len(capped)} honored request(s) "
            f"(rejected {len(rejected)}, cap {cap})..."
        )

        findings: List[ResearchFinding] = []
        # Disambiguate multiple requests of the same kind within one iteration.
        kind_seen: Dict[str, int] = {}
        for req in capped:
            idx = kind_seen.get(req.kind, 0)
            kind_seen[req.kind] = idx + 1
            finding = self._dispatch_research_agent(req, index=idx)
            if finding is not None and finding.finding.strip() and not finding.error:
                finding = self._redact_finding(finding, known_values, index=idx)
            findings.append(finding if finding is not None else ResearchFinding(
                kind=req.kind, need=req.need, finding="", error="dispatch failed"
            ))

        guidance_text = format_findings_for_guidance(findings)

        workflow.append(self._workflow_research_record(
            iteration, honored=capped, rejected=rejected,
            dropped_for_cap=dropped_for_cap, findings=findings,
            injected=guidance_text,
        ))
        return guidance_text

    def _dispatch_research_agent(
        self, request, *, index: int
    ) -> Optional[ResearchFinding]:
        """Invoke one finder sub-agent (resource/literature) and parse its result.

        A SEPARATE provider invocation from the manager and the replicate agent,
        with web-search/fetch access (the one place tools are warranted). It runs
        with API keys stripped (it must not run paper code) and working dir at the
        output tree. Returns a :class:`ResearchFinding` (un-redacted at this
        point) or ``None`` on hard failure.
        """
        template_name = KIND_TEMPLATES.get(request.kind)
        if template_name is None:
            return None

        out_path = self.config.research_finding_path(request.kind, index)
        transcript = self.config.research_transcript_path(request.kind, index)
        try:
            if out_path.exists():
                out_path.unlink()
        except OSError:
            pass

        prompt = self.prompt_generator.generate_research_prompt(
            template_name=template_name,
            output_dir=self.config.output_dir,
            out_path=out_path,
            need=request.need,
            rationale=request.rationale,
        )
        prompt_path = self.config.prompts_dir / f"research_{request.kind}_{index}_prompt.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")

        success = self._invoke_provider(
            prompt=prompt,
            working_dir=self.config.output_dir,
            log_path=transcript,
            timeout=self.config.evaluate_timeout,
            bucket="evaluate",
        )
        if not success or not out_path.exists():
            print(f"    research [{request.kind}]: no finding produced")
            return ResearchFinding(
                kind=request.kind, need=request.need, finding="",
                error="sub-agent produced no finding",
            )
        try:
            raw = json.loads(_extract_json(out_path.read_text(encoding="utf-8")))
        except (ValueError, json.JSONDecodeError) as e:
            return ResearchFinding(
                kind=request.kind, need=request.need, finding="",
                error=f"unparseable finding: {e}",
            )

        if not bool(raw.get("found", False)):
            return ResearchFinding(
                kind=request.kind, need=request.need, finding="",
                error="resource/method not found by sub-agent",
            )
        sources = raw.get("sources") or []
        if not isinstance(sources, list):
            sources = [str(sources)]
        sources = [str(s).strip() for s in sources if str(s).strip()]
        return ResearchFinding(
            kind=request.kind,
            need=request.need,
            finding=str(raw.get("finding", "") or "").strip(),
            sources=sources,
        )

    def _redact_finding(
        self, finding: ResearchFinding, known_values: List[str], *, index: int
    ) -> ResearchFinding:
        """Two-layer redaction of a finding (anti-leakage barrier b).

        Primary layer: an LLM/agent redactor reads the finding and removes
        reported result/metric values by JUDGMENT (no keyword matching),
        preserving methodology/resources + provenance. Belt-and-suspenders layer:
        a deterministic exact-string scrub of *known* ``paper_value`` strings on
        top of the LLM's output. The redactor agent runs with keys stripped and
        does NOT receive the known values (the deterministic scrub is the runner's
        objective check). On LLM-redactor failure we fall CLOSED to the
        deterministic scrub of the original finding (never inject un-redacted
        text past a failed LLM pass).
        """
        kind = finding.kind
        out_path = self.config.research_redaction_path(kind, index)
        transcript = self.config.research_redaction_transcript_path(kind, index)
        try:
            if out_path.exists():
                out_path.unlink()
        except OSError:
            pass

        prompt = self.prompt_generator.generate_research_redactor_prompt(
            output_dir=self.config.output_dir,
            out_path=out_path,
            kind=kind,
            need=finding.need,
            finding=finding.finding,
            sources=finding.sources,
        )
        prompt_path = self.config.prompts_dir / f"research_{kind}_{index}_redactor_prompt.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")

        llm_text = finding.finding
        llm_removed = False
        success = self._invoke_provider(
            prompt=prompt,
            working_dir=self.config.output_dir,
            log_path=transcript,
            timeout=self.config.evaluate_timeout,
            bucket="evaluate",
        )
        if success and out_path.exists():
            try:
                raw = json.loads(_extract_json(out_path.read_text(encoding="utf-8")))
                llm_text = str(raw.get("redacted_finding", finding.finding) or "").strip()
                llm_removed = bool(raw.get("removed_anything", False))
            except (ValueError, json.JSONDecodeError):
                # Fall closed: LLM output unparseable -> redact the ORIGINAL text
                # deterministically rather than trusting the raw finding.
                llm_text = finding.finding
                llm_removed = False
        else:
            print(f"    redactor [{kind}]: LLM pass failed; falling back to exact scrub only")

        # Belt-and-suspenders deterministic scrub of KNOWN paper values on top.
        det = redact_known_values(llm_text, known_values)
        finding.finding = det.redacted_text
        finding.redaction = RedactionResult(
            redacted_text=det.redacted_text,
            llm_removed=llm_removed,
            exact_hits=det.exact_hits,
        )
        if det.exact_hits:
            print(
                f"    redactor [{kind}]: deterministic scrub removed "
                f"{len(det.exact_hits)} known paper value(s)"
            )
        return finding

    def _workflow_research_record(
        self,
        iteration: int,
        *,
        honored,
        rejected,
        dropped_for_cap,
        findings,
        injected: str = "",
    ) -> Dict[str, Any]:
        """Workflow-log record for one iteration's research dispatch (§6 logging)."""
        return {
            "iteration": iteration,
            "phase": "research",
            "status": "completed" if findings else "none",
            "research": {
                "honored": [r.to_dict() for r in honored],
                "rejected": [r.to_dict() for r in rejected],
                "dropped_for_cap": [r.to_dict() for r in dropped_for_cap],
                "findings": [f.to_dict() for f in findings],
                "injected_guidance": injected,
            },
            "manager_verdict": None,
            "directive": None,
            "archived_attempt_path": None,
        }

    # -- Fix Assessment ----------------------------------------------------

    def _assess_fixes(self, evidence: Optional[ExecutionEvidence]) -> FixSeverityAssessment:
        """Assess severity of fixes applied during replication.

        Runs a separate LLM pass over the fix records. Skips the LLM call
        entirely when no fixes were applied.
        """
        if evidence is None:
            return FixSeverityAssessment.empty()

        all_fixes = evidence.all_fixes_applied
        if not all_fixes:
            print("No fixes applied during replication, skipping severity assessment")
            return FixSeverityAssessment.empty()

        print(f"Assessing severity of {len(all_fixes)} fix(es)...")

        prompt = self.prompt_generator.generate_fix_severity_prompt(
            fixes=[f.to_dict() for f in all_fixes],
            output_dir=self.config.output_dir,
        )

        prompt_path = self.config.prompts_dir / "fix_severity_prompt.txt"
        prompt_path.write_text(prompt, encoding='utf-8')

        output_path = self.config.fix_severity_path
        log_path = self.config.fix_severity_transcript_path

        success = self._invoke_provider(
            prompt=prompt,
            working_dir=self.config.output_dir,
            log_path=log_path,
            timeout=None,
            bucket="assess",
        )

        if not success:
            print(f"  Warning: Provider invocation did not succeed (transcript: {log_path})")
            return FixSeverityAssessment.empty()

        if not output_path.exists():
            print(f"  Warning: Agent did not write {output_path}")
            return FixSeverityAssessment.empty()

        response_text = output_path.read_text(encoding='utf-8').strip()
        if not response_text:
            print(f"  Warning: {output_path} is empty")
            return FixSeverityAssessment.empty()

        try:
            raw = _extract_json(response_text)
            data = json.loads(raw)
            assessment = FixSeverityAssessment.from_dict(data)
            output_path.write_text(
                json.dumps(assessment.to_dict(), indent=2), encoding='utf-8'
            )
            print(f"  Fix assessment: {assessment.minor_count} minor, {assessment.major_count} major, {assessment.critical_count} critical")
            return assessment
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  Warning: Could not parse fix severity assessment: {e}")
            return FixSeverityAssessment.empty()

    def _evaluate(self) -> None:
        """Post-verify contextual-evaluation phase (external checker).

        Runs a single independent LLM pass over the replication artifacts,
        verdicts, and paper, producing an advisory cheating-monitor +
        contextual-evaluation JSON at ``evaluation/contextual_evaluation.json``.

        This phase is advisory: its output does NOT alter the Replication Score.
        It runs only when ``config.run_evaluation`` is set, and is idempotent —
        an existing output produced with the same evaluate engine is skipped
        (resume-safe); an engine change re-runs it. Outputs without a sidecar
        predate engine tracking and are kept as-is.
        """
        output_path = self.config.evaluation_path
        meta_path = self.config.evaluation_meta_path
        current_meta = {"engine": self.config.resolved_engines()["evaluate"]}
        if output_path.exists() and output_path.read_text(encoding='utf-8').strip():
            stored_meta = _read_engine_meta(meta_path)
            if stored_meta == current_meta:
                print("[OK] evaluation: skipped (already produced with this engine)")
                return
            if stored_meta is None:
                print(
                    f"[OK] evaluation: skipped (already produced; predates "
                    f"engine tracking — delete {output_path} to re-run)"
                )
                return
            print(
                f"  Re-running evaluation: evaluate engine changed "
                f"({stored_meta.get('engine')} -> {current_meta['engine']})"
            )

        print("Running contextual-evaluation phase (external checker)...")
        self.config.evaluation_dir.mkdir(parents=True, exist_ok=True)

        prompt = self.prompt_generator.generate_evaluation_prompt(
            output_dir=self.config.output_dir,
            mode=self.config.mode,
            has_paper=self.config.paper_path is not None,
            paper_path=self.config.paper_path,
        )
        prompt_path = self.config.prompts_dir / "evaluation_prompt.txt"
        prompt_path.write_text(prompt, encoding='utf-8')

        # Default env (API keys stripped) — the checker must not run paper code.
        success = self._invoke_provider(
            prompt=prompt,
            working_dir=self.config.output_dir,
            log_path=self.config.evaluation_transcript_path,
            timeout=self.config.evaluate_timeout,
            bucket="evaluate",
        )
        if not success:
            print(f"  Warning: evaluation phase did not succeed (transcript: {self.config.evaluation_transcript_path})")
            return
        if not output_path.exists():
            print(f"  Warning: evaluation agent did not write {output_path}")
            return
        _write_engine_meta(meta_path, current_meta)
        # Validate it parses; leave the agent's file in place regardless.
        try:
            data = json.loads(_extract_json(output_path.read_text(encoding='utf-8')))
            risk = (data.get("cheating_monitor") or {}).get("risk", "unknown")
            print(f"  Contextual evaluation written; cheating-risk: {risk}")
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  Warning: evaluation output is not valid JSON ({e}); left as-is for audit")

    def _stage_resolver_script(self) -> Path:
        """Copy the standalone deterministic resolver into the agent workspace.

        ``core/citations.py`` imports only the stdlib, so the copied file runs as
        a self-contained script the citation-check agent invokes. Copying (rather
        than relying on veritas being importable from the agent's shell) keeps it
        runtime-agnostic across docker and host modes. The source is located via
        ``importlib`` so a ``.py`` path is used even on compiled installs.
        """
        import importlib.util

        spec = importlib.util.find_spec("veritas.core.citations")
        if spec is None or not spec.origin:
            raise RuntimeError("cannot locate the veritas.core.citations source file")
        dest = self.config.resolver_script_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(spec.origin, dest)
        return dest

    def _check_citations(self) -> None:
        """Opt-in citation-check submodule (post-verify; under evaluate).

        A single web-enabled provider invocation extracts the paper's reference
        list, runs the staged deterministic resolver (authoritative for
        existence/metadata), and escalates only unresolved references. Advisory:
        the output at ``evaluation/citation_check.json`` does NOT alter the
        Replication Score. Idempotent for the same settings; a change of the
        faithfulness scope or the evaluate engine re-runs it (and drops the
        stale audit). Outputs without a sidecar predate settings tracking and
        are kept as-is. Never raises into the pipeline.

        A self-contained method that mirrors the research sub-agent dispatch.
        """
        output_path = self.config.citation_check_path
        meta_path = self.config.citation_check_meta_path
        current_meta = {
            "engine": self.config.resolved_engines()["evaluate"],
            "faithfulness_scope": self.config.faithfulness_scope,
        }
        existing = self._load_json_object(output_path)
        if existing is not None:
            if self._citation_check_settings_match(current_meta):
                print("[OK] citation-check: skipped (already produced with these settings)")
                # A missing or previously failed audit still gets its pass
                # (idempotent: no-op when the audit exists or nothing needs
                # auditing).
                self._audit_citations(existing)
                return
            print(
                f"  citation-check: settings changed (now scope "
                f"'{self.config.faithfulness_scope}', engine "
                f"'{current_meta['engine']}'); re-running"
            )
            # Discard the stale trio before re-running: provider success is
            # only the subprocess exit code, so a re-run that exits cleanly
            # without writing must not leave the old output to be stamped with
            # the new settings. The audit re-checks the check's findings, so
            # it is stale too.
            output_path.unlink(missing_ok=True)
            meta_path.unlink(missing_ok=True)
            self.config.citation_audit_path.unlink(missing_ok=True)

        if not self.config.has_paper:  # defensive; config validation already enforces this
            print("  citation-check: no paper available; skipping")
            return

        print("Running citation-check submodule (reference verification)...")
        try:
            self.config.evaluation_dir.mkdir(parents=True, exist_ok=True)
            script_path = self._stage_resolver_script()
            prompt = self.prompt_generator.generate_citation_check_prompt(
                output_dir=self.config.output_dir,
                paper_path=self.config.paper_path,
                resolver_script_path=script_path,
                faithfulness_scope=self.config.faithfulness_scope,
            )
            prompt_path = self.config.prompts_dir / "citation_check_prompt.txt"
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text(prompt, encoding="utf-8")
            success = self._invoke_provider(
                prompt=prompt,
                working_dir=self.config.output_dir,
                log_path=self.config.citation_check_transcript_path,
                timeout=self.config.citation_timeout,
                bucket="evaluate",
            )
        except Exception as e:
            print(f"  Warning: citation-check could not run ({e}); skipped")
            return
        if not success:
            print(f"  Warning: citation-check did not succeed (transcript: {self.config.citation_check_transcript_path})")
            return
        if not output_path.exists():
            print(f"  Warning: citation-check agent did not write {output_path}")
            return
        # Any audit on disk now refers to a superseded check output (covers
        # re-runs that entered through the output-absent path, where the
        # settings gate never removed an orphaned audit). Removed before the
        # meta stamp so an interruption between the two re-audits on resume
        # instead of pairing the old audit with the new output.
        self.config.citation_audit_path.unlink(missing_ok=True)
        data = self._load_json_object(output_path)
        if data is None:
            # Not stamped with a meta sidecar: the resume gate treats unusable
            # output as not-produced, so the next invocation re-runs the check
            # instead of silently going dead on it.
            print(
                f"  Warning: citation-check output at {output_path} is not a "
                "valid JSON object; the check will re-run on the next invocation"
            )
            return
        _write_engine_meta(meta_path, current_meta)
        s = data.get("summary")
        s = s if isinstance(s, dict) else {}
        f = s.get("faithfulness")
        f = f if isinstance(f, dict) else {}
        print(
            f"  Citation check written; {s.get('total', '?')} refs "
            f"({s.get('likely_fabricated', 0)} likely fabricated, "
            f"{s.get('metadata_mismatch', 0)} metadata-mismatch, "
            f"{s.get('inconclusive', 0)} inconclusive, "
            f"{s.get('unresolved', 0)} unresolved); "
            f"faithfulness checked {f.get('checked', 0)} "
            f"({f.get('contradicted', 0)} contradicted, "
            f"{f.get('partially_supported', 0)} partial)"
        )

        # Independent audit pass over the flagged verdicts (advisory; never raises).
        self._audit_citations(data)

    @staticmethod
    def _load_json_object(output_path: Path) -> Optional[dict]:
        """Parse an agent-written JSON artifact. None when the file is absent,
        empty, unreadable, or not a JSON object — all treated as not-produced,
        so the producing pass re-runs instead of resuming on unusable output."""
        try:
            raw = output_path.read_text(encoding="utf-8")
        except (OSError, ValueError):
            return None
        if not raw.strip():
            return None
        try:
            data = json.loads(_extract_json(raw))
        except (ValueError, json.JSONDecodeError):
            return None
        return data if isinstance(data, dict) else None

    def _citation_check_settings_match(self, current_meta: Dict[str, Any]) -> bool:
        """True if the existing citation-check output was produced with the
        current settings (faithfulness scope and evaluate engine).

        A missing sidecar (an output from before settings tracking) counts as
        matching, so legacy outputs are kept as-is; a sidecar that exists but
        cannot be read or parsed is damaged tracking data, so the check
        re-runs rather than trusting output of unknown settings. Lenient per
        field: a sidecar that predates a field counts as matching it."""
        meta_path = self.config.citation_check_meta_path
        if not meta_path.exists():
            return True
        meta = _read_engine_meta(meta_path)
        if meta is None:
            return False
        return all(
            meta.get(key) is None or meta.get(key) == value
            for key, value in current_meta.items()
        )

    @staticmethod
    def _has_auditable_findings(check_data: dict) -> bool:
        """True if the citation-check output has any flagged integrity item or a
        contradicted/partially_supported faithfulness verdict worth re-checking."""
        if (check_data.get("flagged") or []):
            return True
        for f in check_data.get("faithfulness") or []:
            if isinstance(f, dict) and f.get("verdict") in ("contradicted", "partially_supported"):
                return True
        return False

    def _audit_citations(self, check_data: Optional[dict] = None) -> None:
        """Independent re-check of the verify pass's flagged verdicts.

        Takes the parsed check output when the caller already has it (fresh
        runs), otherwise reads ``evaluation/citation_check.json``; if it has
        any non-trivial finding, runs a separate provider invocation (fresh
        context, API keys stripped) that writes disagreements to
        ``evaluation/citation_audit.json``. Advisory; idempotent; never raises.
        """
        check_path = self.config.citation_check_path
        audit_path = self.config.citation_audit_path
        # Same parse-validated gate as the check: garbage from a clean-exit
        # audit agent must not permanently satisfy the resume check.
        if self._load_json_object(audit_path) is not None:
            print("[OK] citation-audit: skipped (already produced)")
            return
        if check_data is None:
            check_data = self._load_json_object(check_path)
        if check_data is None:
            return
        if not self.config.has_paper:
            return
        if not self._has_auditable_findings(check_data):
            return
        print("Running citation-audit (independent re-check of flagged verdicts)...")
        try:
            prompt = self.prompt_generator.generate_citation_audit_prompt(
                output_dir=self.config.output_dir,
                paper_path=self.config.paper_path,
            )
            prompt_path = self.config.prompts_dir / "citation_audit_prompt.txt"
            prompt_path.parent.mkdir(parents=True, exist_ok=True)
            prompt_path.write_text(prompt, encoding="utf-8")
            success = self._invoke_provider(
                prompt=prompt,
                working_dir=self.config.output_dir,
                log_path=self.config.citation_audit_transcript_path,
                timeout=self.config.citation_timeout,
                bucket="evaluate",
            )
        except Exception as e:
            print(f"  Warning: citation-audit could not run ({e}); skipped")
            return
        if not success:
            print(f"  Warning: citation-audit did not succeed (transcript: {self.config.citation_audit_transcript_path})")
            return
        if not audit_path.exists():
            print(f"  Warning: citation-audit agent did not write {audit_path}")
            return
        data = self._load_json_object(audit_path)
        if data is None:
            # The resume gate treats this as not-produced; say so rather
            # than reporting a written audit.
            print("  Warning: citation-audit output is not a valid JSON object; the audit will re-run on the next invocation")
        else:
            print(f"  Citation audit written; {len(data.get('items') or [])} item(s) re-checked")

    # -- Phase 4: Verify ---------------------------------------------------

    def _run_single_verify(
        self,
        claim: PaperClaim,
        replication_plan: Optional[ReplicationPlan],
    ) -> Optional[ClaimVerdict]:
        """Verify one claim against replication evidence.

        Returns the parsed verdict on success, ``None`` on failure (which
        leaves ``verify/{claim_id}.json`` absent so the next run re-attempts).
        """
        plan_step_ids: List[int] = []
        if replication_plan is not None:
            plan_step_ids = [
                s.id for s in replication_plan.steps if claim.id in s.verifies
            ]

        codebase_dir = self.config.replication_dir / "codebase"
        codebase_diff = self.config.replication_dir / "codebase.diff"
        replication_log = self.config.replication_dir / "replication_log.json"
        fix_severity_file = self.config.fix_severity_path

        prompt = self.prompt_generator.generate_verify_prompt(
            claim=claim,
            codebase_dir=codebase_dir,
            codebase_diff_path=codebase_diff,
            replication_log_path=replication_log,
            fix_severity_path=fix_severity_file,
            plan_step_ids=plan_step_ids,
            output_dir=self.config.output_dir,
        )

        prompt_path = self.config.prompts_dir / f"verify_{claim.id}_prompt.txt"
        prompt_path.write_text(prompt, encoding='utf-8')

        output_json_path = self.config.verify_path(claim.id)
        log_path = self.config.verify_transcript_path(claim.id)

        success = self._invoke_provider(
            prompt=prompt,
            working_dir=self.config.effective_repo_path,
            log_path=log_path,
            timeout=self.config.verify_timeout,
            bucket="verify",
        )

        if not success:
            print(f"  Warning: verifier invocation failed for {claim.id} (transcript: {log_path})")
            return None

        if not output_json_path.exists():
            print(f"  Warning: verifier did not write {output_json_path}")
            return None

        response_text = output_json_path.read_text(encoding='utf-8').strip()
        if not response_text:
            print(f"  Warning: {output_json_path} is empty")
            return None

        try:
            raw = _extract_json(response_text)
            data = json.loads(raw)
            verdict = ClaimVerdict.from_dict(data)
        except (ValueError, json.JSONDecodeError, KeyError) as e:
            print(f"  Warning: could not parse verdict for {claim.id}: {e}")
            return None

        # Verifier split: the LLM above is the *comparator* (it extracts the
        # replicated value). For deterministically-gradable claim types, re-derive
        # the status from that value with the LLM-free grader, so the entity that
        # produced the value does not also grade it (independence + auditability).
        verdict = self._apply_deterministic_grade(claim, verdict)

        output_json_path.write_text(
            json.dumps(verdict.to_dict(), indent=2), encoding='utf-8'
        )
        return verdict

    def _apply_deterministic_grade(
        self, claim: PaperClaim, verdict: ClaimVerdict
    ) -> ClaimVerdict:
        """Re-grade a numeric/table claim deterministically from the comparator's
        extracted value; passthrough for qualitative/figure and non-gradable
        shapes. Records grading provenance in ``structured['grading']`` and sets
        ``graded_by``."""
        from veritas.core.grading import grade_claim, GradingTolerances, DETERMINISTIC_TYPES

        # not_applicable is a structural call the comparator owns; never override.
        if claim.type not in DETERMINISTIC_TYPES or verdict.status == "not_applicable":
            verdict.graded_by = verdict.graded_by or "llm"
            return verdict

        tol = GradingTolerances()
        status, why, graded_by = grade_claim(claim.type, verdict.structured, verdict.status, tol)

        verdict.structured = dict(verdict.structured or {})
        verdict.structured["grading"] = {
            "deterministic_status": status if graded_by == "deterministic" else None,
            "comparator_proposed_status": verdict.status,
            "rule": why,
            "graded_by": graded_by,
            "tolerances": tol.to_dict(),
        }
        if graded_by == "deterministic" and status != verdict.status:
            print(f"    {claim.id}: comparator said {verdict.status}, grader says {status} ({why})")
        comparator_rationale = verdict.rationale
        verdict.status = status
        verdict.graded_by = graded_by
        if graded_by == "deterministic":
            verdict.rationale = f"[deterministic grade] {why}. Comparator notes: {comparator_rationale}"
        return verdict

    def _load_verdict(self, claim_id: str) -> Optional[ClaimVerdict]:
        """Read a single per-claim verdict JSON. Returns None if missing or unparseable."""
        output_path = self.config.verify_path(claim_id)
        if not output_path.exists():
            return None
        try:
            with open(output_path, encoding='utf-8') as f:
                data = json.load(f)
            return ClaimVerdict.from_dict(data)
        except (json.JSONDecodeError, ValueError, KeyError):
            return None

    def _load_verify_artifacts(self, claims: PaperClaims) -> List[ClaimVerdict]:
        """Load all available per-claim verdicts from disk."""
        verdicts: List[ClaimVerdict] = []
        for c in claims.claims:
            v = self._load_verdict(c.id)
            if v is not None:
                verdicts.append(v)
        return verdicts

    def _verify_with_resume(
        self,
        claims: PaperClaims,
        replication_plan: Optional[ReplicationPlan],
        state: PipelineState,
        already_done: List[str],
    ) -> List[ClaimVerdict]:
        """Run verification per-claim with per-claim resume.

        Resume primitive: ``verify/<claim_id>.json`` exists => skip. The
        ``state.outputs.completed_claims`` list is updated as each verdict
        lands on disk so the resume banner can summarize progress accurately.
        """
        results: List[ClaimVerdict] = []
        completed = list(already_done)

        for claim in claims.claims:
            output_path = self.config.verify_path(claim.id)
            if output_path.exists():
                v = self._load_verdict(claim.id)
                if v is not None:
                    print(f"  Skipping {claim.id} (already verified)")
                    results.append(v)
                    if claim.id not in completed:
                        completed.append(claim.id)
                        state.update_stage_outputs('verify', {'completed_claims': completed})
                    continue
                print(f"  {claim.id} verdict file unparseable; re-attempting")
                output_path.unlink()

            print(f"Verifying {claim.id} ({claim.tier}/{claim.type})...")
            verdict = self._run_single_verify(claim, replication_plan)

            if verdict is not None:
                results.append(verdict)
                completed.append(claim.id)
                state.update_stage_outputs('verify', {'completed_claims': completed})
                print(f"  {claim.id}: {verdict.status}")
            else:
                print(f"  {claim.id}: verifier failed (no verdict written; retry on next run)")

        return results

    def _score_after_verify(
        self,
        claims: PaperClaims,
        verdicts: List[ClaimVerdict],
    ) -> ReplicationScore:
        """Compute the Replication Score and persist the aggregate verdict files."""
        score = compute_replication_score(claims, verdicts)

        self.config.verdicts_path.parent.mkdir(parents=True, exist_ok=True)
        self.config.verdicts_path.write_text(
            json.dumps([v.to_dict() for v in verdicts], indent=2),
            encoding='utf-8',
        )
        self.config.replication_score_path.write_text(
            json.dumps(score.to_dict(), indent=2),
            encoding='utf-8',
        )

        if score.score is not None:
            print(f"Replication Score: {score.score * 100:.1f}%")
        else:
            print("Replication Score: not computable")
        for flag in score.flags:
            print(f"  Flag: {flag}")

        return score

    def _collect_resource_usage(self, state: PipelineState) -> None:
        """Combine time per phase, token counts, and disk usage into resource_usage.json."""
        from veritas.core.models.resource_usage import ResourceUsage, PhaseUsage
        from veritas.core.config import RESOURCE_USAGE_FILE
        from veritas.utils.transcripts import sum_tokens_from_transcript

        phase_transcripts = {
            "analyze":           self.config.paper_claims_transcript_path,
            "codegen":           self.config.codegen_transcript_path,
            "plan":              self.config.replication_plan_transcript_path,
            "resource_estimate": self.config.resource_estimate_transcript_path,
            "replicate":         self.config.replication_transcript_path,
            "assess_fixes":      self.config.fix_severity_transcript_path,
            "evaluation":        self.config.evaluation_transcript_path,
            # Advisory citation passes (opt-in). They have no PipelineState
            # stage, so wall_seconds stays None; only their tokens are summed.
            "citation_check":    self.config.citation_check_transcript_path,
            "citation_audit":    self.config.citation_audit_transcript_path,
        }
        # verify transcripts: one per claim
        verify_transcripts = list(self.config.verify_dir.glob("*_transcript.jsonl"))

        usage = ResourceUsage()

        for phase, transcript_path in phase_transcripts.items():
            stage = state.state.get("stages", {}).get(phase, {})
            started = stage.get("started_at")
            completed = stage.get("completed_at")
            wall = None
            if started and completed:
                wall = (datetime.fromisoformat(completed) - datetime.fromisoformat(started)).total_seconds()
            inp, out = sum_tokens_from_transcript(transcript_path)
            usage.phases[phase] = PhaseUsage(wall_seconds=wall, input_tokens=inp, output_tokens=out)

        # verify: sum across all claim transcripts
        verify_stage = state.state.get("stages", {}).get("verify", {})
        v_started = verify_stage.get("started_at")
        v_completed = verify_stage.get("completed_at")
        v_wall = None
        if v_started and v_completed:
            v_wall = (datetime.fromisoformat(v_completed) - datetime.fromisoformat(v_started)).total_seconds()
        v_inp, v_out = 0, 0
        for t in verify_transcripts:
            i, o = sum_tokens_from_transcript(t)
            v_inp += i
            v_out += o
        usage.phases["verify"] = PhaseUsage(wall_seconds=v_wall, input_tokens=v_inp, output_tokens=v_out)

        # Prior loop iterations live in archived attempt trees; the current
        # iteration's files in replication/ are counted by the phase loop.
        archived_attempts = sorted(
            self.config.output_dir.glob("replication.attempt-*"))
        for attempt_dir in archived_attempts:
            i, o = sum_tokens_from_transcript(
                attempt_dir / "replication_transcript.jsonl")
            usage.phases["replicate"].input_tokens += i
            usage.phases["replicate"].output_tokens += o

        # manager review + research sub-agents (present only when the retry
        # loop ran); no wall time — their stages are not state-tracked.
        loop_transcripts = [self.config.manager_review_transcript_path]
        loop_transcripts += sorted(
            self.config.replication_dir.glob("research_*_transcript.jsonl"))
        for attempt_dir in archived_attempts:
            loop_transcripts.append(attempt_dir / "manager_review_transcript.jsonl")
            loop_transcripts += sorted(
                attempt_dir.glob("research_*_transcript.jsonl"))
        m_inp, m_out = 0, 0
        for t in loop_transcripts:
            i, o = sum_tokens_from_transcript(t)
            m_inp += i
            m_out += o
        if m_inp or m_out:
            usage.phases["manager_loop"] = PhaseUsage(
                wall_seconds=None, input_tokens=m_inp, output_tokens=m_out)

        # totals
        all_phases = list(usage.phases.values())
        walls = [p.wall_seconds for p in all_phases if p.wall_seconds is not None]
        usage.total_wall_seconds = sum(walls) if walls else None
        usage.total_input_tokens = sum(p.input_tokens for p in all_phases)
        usage.total_output_tokens = sum(p.output_tokens for p in all_phases)

        # disk footprint (-sb is GNU-only; -sk works on both macOS and Linux)
        try:
            if sys.platform == "darwin":
                result = subprocess.run(
                    ["du", "-sk", str(self.config.output_dir)],
                    capture_output=True, text=True
                )
                usage.disk_bytes = int(result.stdout.split()[0]) * 1024
            else:
                result = subprocess.run(
                    ["du", "-sb", str(self.config.output_dir)],
                    capture_output=True, text=True
                )
                usage.disk_bytes = int(result.stdout.split()[0])
        except Exception:
            usage.disk_bytes = None

        # cost estimate: each phase is priced by its bucket's resolved model,
        # falling back to the provider's model env var when the engine names
        # no explicit model. Known models use KNOWN_MODEL_PRICING; if any
        # phase that consumed tokens has no known price, the total is None so
        # the field is omitted rather than silently wrong.
        provider_model_env = {
            "claude": "ANTHROPIC_MODEL",
            "codex": "OPENAI_MODEL",
            "gemini": "GEMINI_MODEL",
        }
        phase_buckets = {
            "analyze": "analyze", "codegen": "codegen", "plan": "analyze",
            "resource_estimate": "analyze", "replicate": "replicate",
            "assess_fixes": "assess", "verify": "verify",
            "evaluation": "evaluate", "citation_check": "evaluate",
            "citation_audit": "evaluate", "manager_loop": "evaluate",
        }

        def _price_for(model_name):
            # Exact match first; else the longest known id the model name
            # starts with, so dated ids (e.g. claude-haiku-4-5-20251001)
            # still price. Longest-first avoids gpt-4o matching gpt-4o-mini.
            if not model_name:
                return None
            pricing = KNOWN_MODEL_PRICING.get(model_name)
            if pricing is None:
                for key in sorted(KNOWN_MODEL_PRICING, key=len, reverse=True):
                    if model_name.startswith(key):
                        return KNOWN_MODEL_PRICING[key]
            return pricing

        estimated_cost_usd = 0.0
        priced_any = False
        unpriced_phases = []
        for phase, p in usage.phases.items():
            if not p.input_tokens and not p.output_tokens:
                continue
            phase_provider, phase_model = self.config.engine_for(phase_buckets[phase])
            if phase_model is None:
                env_var = provider_model_env.get(phase_provider)
                phase_model = os.environ.get(env_var) if env_var else None
            pricing = _price_for(phase_model)
            if pricing is None:
                unpriced_phases.append(phase)
                continue
            priced_any = True
            estimated_cost_usd += (
                p.input_tokens / 1_000_000 * pricing[0]
                + p.output_tokens / 1_000_000 * pricing[1]
            )
        if unpriced_phases or not priced_any:
            # A partial sum would read as the run's cost; report None and
            # name the phases that could not be priced.
            estimated_cost_usd = None
        else:
            estimated_cost_usd = round(estimated_cost_usd, 4)

        # write
        out_dict = {
            "phases": {
                name: {"wall_seconds": p.wall_seconds, "input_tokens": p.input_tokens, "output_tokens": p.output_tokens}
                for name, p in usage.phases.items()
            },
            "totals": {
                "wall_seconds": usage.total_wall_seconds,
                "input_tokens": usage.total_input_tokens,
                "output_tokens": usage.total_output_tokens,
                "disk_bytes": usage.disk_bytes,
                "estimated_cost_usd_approximate": estimated_cost_usd,
                **({"unpriced_phases": unpriced_phases} if unpriced_phases else {}),
            }
        }
        with open(self.config.output_dir / RESOURCE_USAGE_FILE, "w") as f:
            json.dump(out_dict, f, indent=2)

    def _estimate_resources(
        self,
        replication_plan: Optional[ReplicationPlan],
        state: Optional["PipelineState"] = None,
    ) -> ResourceEstimate:
        """Combine static code analysis with an LLM pass to produce resource_estimate.json.

        The LLM output is written to disk as free-form JSON — the schema is a suggestion,
        not a contract. We only parse the fields the code needs programmatically; everything
        else stays in the raw JSON for downstream LLM consumers.
        """
        print("Estimating replication resources...")

        # Static analysis: only run if there is a repo to scan (skipped in paper-only
        # mode before codegen has produced one).
        static: Dict[str, Any] = {}
        repo_path = self.config.effective_repo_path
        if repo_path and repo_path.exists():
            from veritas.utils.static_analysis import analyze_repo
            static = analyze_repo(repo_path)

        plan_text = json.dumps(
            replication_plan.to_dict() if replication_plan else {}, indent=2
        )

        prompt = self.prompt_generator.generate_resource_estimation_prompt(
            replication_plan=plan_text,
            output_dir=self.config.output_dir,
            paper_path=self.config.paper_path,
            mode=self.config.mode,
            pre_codegen=state is not None and not state.is_stage_completed('codegen'),
        )

        prompt_path = self.config.prompts_dir / "resource_estimation_prompt.txt"
        prompt_path.write_text(prompt, encoding="utf-8")

        log_path = self.config.resource_estimate_transcript_path
        output_path = self.config.resource_estimate_path

        success = self._invoke_provider(
            prompt=prompt,
            working_dir=self.config.output_dir,
            log_path=log_path,
            timeout=None,
            bucket="analyze",
        )

        if not success or not output_path.exists():
            print("  Warning: Resource estimation did not produce output")
            return ResourceEstimate(**static)

        try:
            raw = _extract_json(output_path.read_text(encoding="utf-8"))
            data = json.loads(raw)
            # Static analysis fields always win (they are deterministic).
            data.update(static)
            # Write the merged free-form JSON back to disk as-is.
            output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            result = ResourceEstimate.from_dict(data)
            print(f"  Compute class: {result.compute_class}, GPU: {result.needs_gpu}")
            return result
        except (ValueError, json.JSONDecodeError) as e:
            print(f"  Warning: Could not parse resource estimate: {e}")
            return ResourceEstimate(**static)

    # -- Report ------------------------------------------------------------

    def _report(
        self,
        claims: PaperClaims,
        verdicts: List[ClaimVerdict],
        score: ReplicationScore,
        evidence=None,
        fix_assessment=None,
    ):
        """Generate the final report."""
        return self.report_generator.generate_from_results(
            claims=claims,
            verdicts=verdicts,
            score=score,
            config=self.config,
            output_dir=self.config.output_dir,
            generate_pdf=self.config.generate_pdf,
            evidence=evidence,
            fix_assessment=fix_assessment,
        )

    def check_citations_existing(self) -> RunResult:
        """Run the citation check on an already-completed run, then refresh the report.

        Assumes ``config.output_dir`` is a finished run directory and
        ``config.paper_path`` was recovered by the caller. Runs the self-contained
        citation check (verify + independent audit) and regenerates the report so
        it includes the citation sections. Advisory; never touches the Replication
        Score.
        """
        try:
            self._check_provider_auth(buckets=("evaluate",))
            self.config.evaluation_dir.mkdir(parents=True, exist_ok=True)
            self._check_citations()
            self._collect_usage_if_tracked()
            if self._load_json_object(self.config.citation_check_path) is None:
                # The check soft-fails internally; surface that as a command
                # failure (exit 1) instead of a green message, and leave the
                # existing report untouched.
                return RunResult(
                    success=False,
                    error=(
                        "the citation check did not produce a usable "
                        f"{self.config.citation_check_path.name} (see transcript: "
                        f"{self.config.citation_check_transcript_path})"
                    ),
                )
            report_path, pdf_path = self.report_generator.generate(
                replicate_dir=self.config.output_dir,
                generate_pdf=self.config.generate_pdf,
                generate_md=True,
            )
            return RunResult(success=True, report_path=report_path, pdf_path=pdf_path)
        except Exception as e:
            return RunResult(success=False, error=str(e))
        finally:
            # Same API-key redaction pass run() applies; this entry point writes
            # prompts and transcripts without going through run().
            try:
                sanitize_logs_directory(self.config.output_dir)
            except Exception:
                pass

    def evaluate_existing(self) -> RunResult:
        """Run the contextual evaluation on an already-completed run, then
        refresh the report.

        Assumes ``config.output_dir`` is a finished run directory. Runs only
        the evaluation pass and regenerates the report; never reconciles
        pipeline state or re-runs pipeline stages.
        """
        try:
            self._check_provider_auth(buckets=("evaluate",))
            self.config.evaluation_dir.mkdir(parents=True, exist_ok=True)
            self.config.prompts_dir.mkdir(parents=True, exist_ok=True)
            self._evaluate()
            self._collect_usage_if_tracked()
            report_path, pdf_path = self.report_generator.generate(
                replicate_dir=self.config.output_dir,
                generate_pdf=self.config.generate_pdf,
                generate_md=True,
            )
            return RunResult(success=True, report_path=report_path, pdf_path=pdf_path)
        except Exception as e:
            return RunResult(success=False, error=str(e))
        finally:
            # Same API-key redaction pass run() applies; this entry point writes
            # prompts and transcripts without going through run().
            try:
                sanitize_logs_directory(self.config.output_dir)
            except Exception:
                pass

    # -- Resume helpers ----------------------------------------------------

    def _announce_resume(self, state: PipelineState) -> None:
        """Print the resume banner. Called when state file already had stages."""
        created = state.state.get('created_at', 'unknown time')
        print(f"WARNING: Found existing pipeline state from {created}. Resuming.")
        print("   Pass --restart to start fresh.")

    def _reconcile_with_prior_run(self, state: PipelineState) -> None:
        """Detect input/config changes against the recorded run and invalidate
        affected stages so they re-run with the new values.

        Without this, re-running on the same output dir with different flags
        (e.g. a different ``--provider``) silently reuses stage outputs from
        the prior run, producing a report that doesn't match the requested
        configuration.
        """
        self._announce_resume(state)

        input_changes = state.detect_input_changes(
            self.config.repo_path, self.config.paper_path, data_path=self.config.data_path,
        )

        current_config = build_config_fingerprint(self.config)
        recorded_config = state.state.get('config') or {}
        config_changes = [
            field for field in state.detect_config_changes(current_config)
            if not _is_spurious_engine_change(field, recorded_config, current_config)
        ]

        # A legacy baseline (recorded before engine tracking) is upgraded to
        # the explicit engine fields whenever it is seen, so future resumes
        # compare directly instead of via the provider baseline.
        recorded_lacks_engines = not any(
            key.startswith('engine_') for key in recorded_config
        )

        all_changes = input_changes + config_changes
        if not all_changes:
            if recorded_lacks_engines:
                state.record_config(current_config)
            return

        affected = set()
        for field in all_changes:
            affected.update(FINGERPRINT_INVALIDATES.get(field, ()))
        affected_sorted = sorted(affected)

        print(f"WARNING: detected changes since prior run: {all_changes}")
        print(f"  Invalidating stages: {affected_sorted}")
        state.invalidate_stages(affected_sorted)

        if input_changes:
            state.record_inputs(
                self.config.repo_path,
                self.config.paper_path,
                data_path=self.config.data_path,
            )
        if config_changes or recorded_lacks_engines:
            state.record_config(current_config)

    def _load_paper_claims(self) -> PaperClaims:
        """Load paper_claims.json from disk (used when analyze phase is skipped via resume)."""
        path = self.config.paper_claims_path
        if not path.exists():
            raise RuntimeError(f"paper_claims.json missing at {path}")
        return PaperClaims.from_dict(json.loads(path.read_text(encoding='utf-8')))

    def _load_replication_plan(self) -> Optional[ReplicationPlan]:
        """Load replication_plan.json from disk (used when plan phase is skipped via resume)."""
        path = self.config.replication_plan_path
        if not path.exists():
            return None
        return parse_replication_plan_response(path.read_text(encoding='utf-8'))

    def _load_fix_assessment(self) -> FixSeverityAssessment:
        """Load fix severity assessment from disk (or empty if no fixes were applied)."""
        if not self.config.fix_severity_path.exists():
            return FixSeverityAssessment.empty()
        with open(self.config.fix_severity_path, encoding='utf-8') as f:
            return FixSeverityAssessment.from_dict(json.load(f))

    # -- Provider Invocation -----------------------------------------------

    @staticmethod
    def _env_file_keys() -> set[str]:
        """Names of vars sourced from the host .env file via --env-file.

        The wrapper publishes the comma-separated list as
        VERITAS_ENV_FILE_KEYS so the Python layer can scope visibility:
        the replicate phase inherits these keys; other phases get a
        subprocess env with them stripped out.
        """
        raw = os.environ.get("VERITAS_ENV_FILE_KEYS", "")
        if not raw:
            return set()
        return {k.strip() for k in raw.split(",") if k.strip()}

    @staticmethod
    def _stripped_env(exempt: frozenset = frozenset()) -> Dict[str, str]:
        """os.environ minus the keys defined in VERITAS_ENV_FILE_KEYS and
        minus every provider auth var not named in ``exempt``.

        ``exempt`` names keys excluded from stripping — the invoked
        provider's own auth vars (see PROVIDER_AUTH_VARS). Stripping the
        other providers' auth vars unconditionally (not only when they came
        from .env) keeps the scoping intact in containers that never set
        VERITAS_ENV_FILE_KEYS (evaluate, check-citations, estimate) and for
        host-shell-exported keys.
        """
        all_auth_vars = {
            var for vars_ in PROVIDER_AUTH_VARS.values() for var in vars_
        }
        keys_to_strip = (
            ReplicationRunner._env_file_keys() | all_auth_vars
        ) - set(exempt)
        if not keys_to_strip:
            return os.environ.copy()
        return {k: v for k, v in os.environ.items() if k not in keys_to_strip}

    @staticmethod
    def _auth_exemptions(provider: str) -> frozenset:
        """Auth vars of the provider this invocation runs on.

        Scoped per invocation: each subprocess sees only its own provider's
        auth vars, so a key configured for one bucket never reaches another
        bucket's provider."""
        return frozenset(PROVIDER_AUTH_VARS.get(provider, ()))

    @staticmethod
    def _subprocess_env(provider: str, expose_api_keys: bool) -> Dict[str, str]:
        """Environment for a provider subprocess.

        Every invocation keeps only its own provider's auth vars; other
        providers' keys are stripped even at the replicate call site, so a
        host-shell key configured for a different bucket never reaches the
        paper code. ``expose_api_keys=True`` additionally keeps the vars
        named in ``VERITAS_ENV_FILE_KEYS`` — the .env file is the sanctioned
        key channel for the paper code replicate runs."""
        exempt = ReplicationRunner._auth_exemptions(provider)
        if expose_api_keys:
            exempt = exempt | ReplicationRunner._env_file_keys()
        return ReplicationRunner._stripped_env(exempt)

    def _collect_usage_if_tracked(self) -> None:
        """Best-effort resource_usage refresh for standalone entry points,
        so their transcripts count toward the run's totals. Skipped when the
        directory has no pipeline state (nothing established the baseline)."""
        state_file = state_file_path(self.config.output_dir)
        if not state_file.exists():
            return
        try:
            self._collect_resource_usage(PipelineState(self.config.output_dir))
        except Exception as e:
            print(f"  Warning: Could not collect resource usage: {e}")

    def _leak_buckets(self) -> List[str]:
        """Buckets whose output reaches the replication agent: the plan
        (analyze bucket) always, codegen when it runs (paper-only mode),
        and the manager directive (evaluate bucket) when the retry loop
        is on. A web-locked engine on any of these defeats the
        anti-leakage design."""
        buckets = ["analyze"]
        if self.config.mode == "paper-only":
            buckets.append("codegen")
        buckets.append("replicate")
        if self.config.max_iters > 1:
            buckets.append("evaluate")
        return buckets

    def _active_buckets(self, dry_run: bool = False) -> set:
        """Buckets whose engines this run will actually invoke."""
        buckets = {"analyze"}
        if not dry_run:
            if self.config.mode == "paper-only":
                buckets.add("codegen")
            buckets.update(("replicate", "assess", "verify"))
            if (self.config.run_evaluation or self.config.run_citation_check
                    or self.config.max_iters > 1):
                buckets.add("evaluate")
        return buckets

    def _check_provider_auth(self, buckets=None) -> None:
        """Fail fast when a configured provider is not provisioned to run.

        The wrapper preflight only sees the global --provider; per-bucket
        engines are validated here so a missing key or model surfaces
        before any stage runs instead of mid-pipeline. openrouter is
        API-key-only and has no usable default model; the other providers
        may authenticate via mounted login state, so only openrouter is
        checked.

        ``buckets`` restricts the check to the named buckets (callers pass
        the buckets their entry point will actually run, so a knob
        configured for an inactive bucket cannot block the run). ``None``
        checks every bucket.
        """
        for bucket in (BUCKETS if buckets is None else buckets):
            provider, model = self.config.engine_for(bucket)
            if provider != "openrouter":
                continue
            if model is None:
                raise RuntimeError(
                    f"Provider openrouter requires an explicit model for the "
                    f"'{bucket}' bucket (pass --model or --{bucket}-model)"
                )
            if not os.environ.get("OPENROUTER_API_KEY", "").strip():
                raise RuntimeError(
                    "Provider openrouter requires OPENROUTER_API_KEY. "
                    "Export it in your shell or add it to .env."
                )

    def _invoke_provider(
        self,
        prompt: str,
        working_dir: Path,
        log_path: Path,
        timeout: Optional[int],
        bucket: str,
        append: bool = False,
        expose_api_keys: bool = False,
    ) -> bool:
        """Run the configured provider as a subprocess; stream its JSONL
        transcript to ``log_path``; return True on success.

        The agent is expected to write its actual results (paper-claims JSON,
        replication-plan JSON, per-claim verdict JSON, etc.) to known disk paths during the run.
        ``log_path`` only captures the conversation transcript — it is
        never the source of the agent's answer.

        ``bucket`` names the engine group this call belongs to (see
        ``config.BUCKETS``); the provider and model are resolved per bucket
        via ``Config.engine_for``, so different pipeline steps can run on
        different engines.

        Wall-clock timeout enforcement uses a daemon ``threading.Timer``
        that calls ``process.kill()`` after ``timeout`` seconds. A plain
        ``process.wait(timeout=...)`` after a streaming loop does not
        enforce a wall-clock limit, since the loop blocks until the
        subprocess closes stdout (which happens when it exits anyway).

        With ``append=True`` the transcript file is opened in append mode,
        which is used by the repair-re-invocation path so the original
        failed attempt and the repair attempt land in one transcript file.

        ``expose_api_keys=True`` lets the subprocess inherit the
        replication API keys (the vars listed in
        ``VERITAS_ENV_FILE_KEYS``). Only the replicate phase should set
        this — paper code it runs needs the keys. All other phases keep
        the default ``False``: their environment strips those keys,
        except the invoked provider's own auth vars (see
        ``PROVIDER_AUTH_VARS``), which the subprocess needs to reach
        its provider.
        """
        provider, model = self.config.engine_for(bucket)
        if provider not in CLI_COMMANDS:
            raise ValueError(f"Unknown provider: {provider}")

        try:
            cli = self._resolve_cli(CLI_COMMANDS[provider][0])
        except FileNotFoundError as e:
            print(f"  {e}")
            return False

        cmd = build_provider_command(cli, provider, model)

        log_path.parent.mkdir(parents=True, exist_ok=True)
        open_mode = "a" if append else "w"

        # Strip replication API keys (sourced from .env via --env-file) and
        # other providers' auth vars, keeping only the invoked provider's own
        # (PROVIDER_AUTH_VARS). _replicate opts in via expose_api_keys=True,
        # which keeps the .env keys since the paper code it runs needs them —
        # but other providers' host-shell keys stay stripped even there.
        env = self._subprocess_env(provider, expose_api_keys)

        try:
            process = subprocess.Popen(
                cmd,
                cwd=working_dir,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                bufsize=1,
                env=env,
            )
        except FileNotFoundError as e:
            print(f"  {e}")
            return False
        except Exception as e:
            print(f"  Error invoking {provider}: {e}")
            return False

        process.stdin.write(prompt)
        process.stdin.close()

        timed_out = False
        watchdog: Optional[threading.Timer] = None
        if timeout is not None and timeout > 0:
            def _kill_on_timeout() -> None:
                nonlocal timed_out
                timed_out = True
                try:
                    process.kill()
                except Exception:
                    pass

            watchdog = threading.Timer(timeout, _kill_on_timeout)
            watchdog.daemon = True
            watchdog.start()

        try:
            with open(log_path, open_mode, encoding="utf-8") as log_f:
                for line in iter(process.stdout.readline, ""):
                    line = sanitize_text(line)
                    print(line, end="")
                    log_f.write(line)
            return_code = process.wait()
        finally:
            if watchdog is not None:
                watchdog.cancel()
                watchdog.join()

        if return_code == 0:
            return True
        if timed_out:
            print(f"  Timeout after {timeout}s")
            return False
        return False

    @staticmethod
    def _resolve_cli(name: str) -> str:
        """Resolve a CLI tool name to its full path.

        On Windows, npm installs .cmd shims that subprocess can't find
        without shell=True. This resolves the full path instead.
        """
        resolved = shutil.which(name)
        if resolved is None:
            raise FileNotFoundError(f"{name} CLI not found on PATH")
        return resolved
