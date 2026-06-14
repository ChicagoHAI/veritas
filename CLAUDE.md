# Veritas

Veritas is a replication agent that evaluates whether scientific papers can be reproduced. It runs a staged pipeline: analyze the inputs to extract structured claims, optionally generate code from the paper (paper-only mode), plan the replication, replicate the methodology inside Docker (actively fixing issues, optionally under a manager-controlled retry loop), assess fix severity, verify each claim against the produced evidence with a comparator plus a deterministic grader, optionally run a contextual evaluation pass, and emit a tier-weighted Replication Score with a styled report.

## Project Status

The claim-verification pipeline is in place and stable enough to extend: paper-claims extraction in analyze, per-claim adjudication in verify, tier-weighted scoring, and a single report output. On top of that core sit opt-in layers — a manager-controlled replicate retry loop, a contextual evaluation phase, an advisory citation check, and a human-facing HTML/PDF report. The benchmark path (`replicate`, single pass) is deliberately frozen so harness numbers stay comparable; new behavior lives behind `full` / `--evaluate` / `--max-iters` / `--check-citations`. Check open GitHub issues before changing pipeline shape or verifier behavior.

### Design principles

- **Replication is the primary output.** The Replication Score and per-claim verdicts are the publishable result; replication evidence, fix-severity context, and the evaluation narrative are supporting.
- **The replicate agent tries hard to reproduce results.** Environment errors, API deprecations, and missing compilers are fixed so replication can continue, pushing through several genuinely different approaches before concluding a step is unreproducible, and running at the methodology's intended scale. Only give up after genuine effort. Every applied fix is logged and rated for severity in a separate pass.
- **No tuning toward reported values.** The replicate agent keeps paper access for methodology, parameters, and setup, but a Reporting Discipline forbids hard-coding or tuning toward the paper's reported numbers. A faithful divergent result is correct; a copied or tuned one is a failure.
- **Paper claims drive verification.** Each paper's specific reproducible claims are extracted into `paper_claims.json` (5 shape-typed categories: `scalar | scalar_range | table | qualitative | figure`; 2 tiers: `headline | supporting`). The verifier adjudicates each claim independently with a fresh-context LLM call.
- **Verification separates extraction from grading.** A comparator LLM extracts the objective replicated value; a deterministic, LLM-free grader decides `match` / `partial` / `no_match` for scalar / scalar_range / table claims from that value vs `paper_value` plus tolerances. Qualitative and figure claims keep the comparator's judgment. Each verdict records `graded_by` and an auditable grading rule.
- **Replication Score is a tier-weighted fraction**: `score = Σ(tier_weight × verdict_value) / Σ(tier_weight)` with tier weights `3 / 2` for headline / supporting, verdict values `match=1.0, partial=0.5, no_match=0.0, not_attempted=0.0`, and `not_applicable` excluded from both sums. Tier weights and grading tolerances are overridable via `VERITAS_*` env vars.
- **The replicate agent never sees `paper_claims.json`.** The replication plan references claim IDs in a `verifies` field but doesn't embed paper-reported result values. Plan steps' `expected_outcome` is shape-prescriptive (file path, JSON field names, figure layout), not value-prescriptive. This is veritas's structural defense against ground-truth leakage to the replication agent.
- **Repo-first planning.** When a real repo is provided (full / repo-only), the plan runs the repo's own code paths rather than rewriting them, and never reads the repo's cached result artifacts as the answer.
- **Intermediate results are sanity-checked, not assumed.** The codegen and replicate agents validate each intermediate (a selection/cut, normalization, grouping key, or fit) before building on it — an implausible count or an off anchor flags a likely-corrupted upstream step that would otherwise silently cascade into every downstream claim. They compare only against documented *method* anchors (a post-cut sample size, a normalization constant, a fit coefficient stated as part of the procedure), never against a value the paper reports as a *result*. This keeps the same anti-leakage line as the Reporting Discipline.
- **The final codebase used during replication is preserved as output**: `replication/codebase/` holds the patched copy and `replication/codebase.diff` shows the unified diff vs. the original repo.
- **Configuration is externalized.** Hardcoded tunables (tolerances, tier weights, timeouts, iteration cap, research budget) resolve as: CLI flag (where one exists) → `VERITAS_*` env var (in `.env`) → code default. No hardcoded numbers buried in modules.

## Commands

```bash
# Install
git clone https://github.com/ChicagoHAI/veritas.git && cd veritas

# Full pipeline (paper + repo): replicate + evaluate + styled report
./veritas full --paper paper.pdf --repo ./my-project
./veritas --paper paper.pdf --repo ./my-project   # bare flags == full

# Replication only (through verify; no manager, no evaluation) — the benchmark surface
./veritas replicate --paper paper.pdf --repo ./my-project

# Run the evaluation manager + report on an existing replication, no recompute
./veritas evaluate ./replicate-dir

# Opt in to the contextual evaluation phase during a replicate run
./veritas replicate --repo ./my-project --evaluate

# Manager-controlled retry loop (review -> accept/revise), bounded by N iterations
./veritas replicate --repo ./my-project --max-iters 3

# Select provider
./veritas replicate --repo ./my-project --provider codex

# Select input mode explicitly (default: auto-detected from inputs)
./veritas replicate --paper paper.pdf --mode paper-only  # generate code from paper, then run
./veritas replicate --repo ./my-project --mode repo-only # extract claims from README

# Supply a hand-authored claims JSON (skips automatic extraction)
./veritas replicate --repo ./my-project --claims claims.json

# Pre-position a data directory (mounted read-only at /workspace/data/)
./veritas replicate --paper paper.pdf --data ./prepositioned-data

# Per-phase timeouts (default: no timeout)
./veritas replicate --repo ./my-project --analyze-timeout 600 --verify-timeout 300

# Opt-in citation check (references exist + metadata correct, plus faithfulness); requires --paper
./veritas replicate --paper paper.pdf --repo ./my-project --check-citations
# Standalone citation check on a finished run (recovers the paper from saved config)
./veritas check-citations ./replicate-dir

# Regenerate the report from existing outputs
./veritas report ./replicate-dir

# Interactive shell inside the replication container
./veritas shell

# Build the image locally (usually not needed — first run pulls from GHCR)
./veritas build

# Smoke test the built image
./scripts/test_docker.sh
```

`full` and bare flags are wrapper aliases that run `replicate --evaluate`; the Python CLI exposes `replicate`, `evaluate`, `report`, and `check-citations`.

## Architecture

Pipeline orchestrated by `ReplicationRunner.run()` in `src/veritas/core/runner.py`. Phases, in order:

1. **Analyze** (`_generate_paper_claims`) — extracts `paper_claims.json`. Source depends on input mode: paper PDF (`full` / `paper-only`), repo README (`repo-only`), or a user-supplied `--claims` JSON (universal override, validated and copied through). Yielding 0 claims raises `_InsufficientSpec` and triggers a dedicated bail report instead of propagating as an error.
2. **Codegen** (`_generate_code`, paper-only mode only) — has the agent write the paper's methodology from scratch into `replication/codebase/`. Sentinel-based resume at `<output>/.veritas/codegen_complete`. Anti-leakage: `paper_claims.json` is intentionally out of this phase's scope.
3. **Plan** (`_generate_replication_plan`) — generates the claim-aware `replication_plan.json` from the effective codebase (the user's repo, or the generated one in paper-only mode). Uses `Config.effective_repo_path`. Plan steps carry a `verifies: List[str]` field referencing claim IDs; a post-plan cross-check (`_validate_plan_claim_refs`) warns on unknown IDs. When a real repo is present, the plan prefers running the repo's own code paths.
4. **Replicate + manager loop** (`_replicate_with_manager_loop` wrapping `_replicate`) — runs the plan inside a writable copy of the codebase via an AI agent that actively fixes issues; collects execution evidence and fix records. The agent never sees `paper_claims.json`. With `max_iters <= 1` (default for `replicate`) this is a single pass with no manager. With `max_iters > 1` it becomes a bounded review→decide→revise loop (see below).
5. **Assess Fixes** (`_assess_fixes`) — rates severity of each fix applied during replication (minor/major/critical) via a separate LLM pass. Output: `assess/fix_severity.json`.
6. **Verify** (`_verify_with_resume`) — one provider invocation per claim. A comparator (`_run_single_verify`) extracts the objective replicated value; `_apply_deterministic_grade` then routes scalar / scalar_range / table claims to the deterministic grader (`grade_claim`) and leaves qualitative / figure claims with the comparator's status. Each verdict at `verify/<claim_id>.json` carries `status` (`match | partial | no_match | not_attempted | not_applicable`), `graded_by` (`deterministic | llm`), a type-specific `structured` field (with the grading rule for deterministic verdicts), free-text `rationale`, and `evidence_refs`. Per-claim resume primitive: file-exists check. Final aggregation writes `verify/verdicts.json` and `verify/replication_score.json`.
7. **Evaluate** (`_evaluate`, opt-in via `--evaluate` / `run_evaluation`) — a single contextual-evaluation pass after the score. Advisory: never feeds the Replication Score. Combines a cheating monitor (scans the trace + diff for copied/uncomputed values), contextual evaluation (score attribution, whole-paper consistency, mode-aware methodology correspondence or repo divergence), and the human-facing report narrative. Idempotent via file-exists. API keys stripped (the checker never runs paper code). Output: `evaluation/contextual_evaluation.json`.
8. **Report** (`_report`) — renders the report. HTML is the primary artifact (`report.html.j2`), the PDF is rendered from that HTML via WeasyPrint (with a pandoc/LaTeX fallback), and markdown is the machine-readable source. Deterministic score/claim/fix tables are always assembled in code; the evaluation manager's narrative fills in and is omitted gracefully when absent.

### Manager retry loop (`max_iters > 1`)

The loop sits after replicate and before verify. Each iteration computes objective `ExecutionFacts` from the replicate evidence (`_compute_and_write_execution_facts`, written to `replication/diligence_signals.json`), then **always** runs an independent manager review (`_manager_review`) — there is no deterministic short-circuit-accept; the manager always does the diligence judging. The review runs with fresh context and API keys stripped, reads the facts + trajectory + `retries_remaining`, and emits a structured verdict (`decision`, `target_phase`, `reason`, `directive`, `already_tried`, `research_requests`, `confidence`). `should_stop` decides ACCEPT vs continue against a hard cap and a no-progress terminator (facts didn't improve and the directive repeats). On a genuine-deficiency `revise` within budget: optional research sub-agents are dispatched (`_run_research`), the prior attempt is archived (`archive_attempt` → `replication.attempt-N/`, never overwritten), the target phase + downstream are invalidated (`_invalidate_for_rerun`), the manager's directive is injected as `manager_guidance`, and the phase re-runs. At the cap without acceptance, a structured `unresolved_handoff` is written (`build_handoff`). Every iteration and phase run is appended to `.veritas/workflow.jsonl` plus a readable `workflow.md`, surfaced in the report. The Replication Score stays deterministic regardless of iteration count.

### Research sub-agents (Phase 3, inside the loop)

On a `revise` verdict carrying `research_requests`, narrow research sub-agents find missing methodology/resources behind anti-leakage barriers, bounded by `VERITAS_RESEARCH_MAX_CALLS` (default 2; `0` disables). Two kinds: a resource-finder (missing dataset/script/dependency) and a literature-finder (underspecified method or standard reference implementation). Each is a separate provider invocation with web access and API keys stripped, returning `{found, finding, sources, notes}`. Three barriers: an intent allow-list (`honor_request`: kind ∈ {resource, literature}, a structured-field check, not keyword matching); redaction before injection (an LLM redactor that never sees the known values, falling closed to a deterministic exact-string scrub of known `paper_value` strings); and provenance-tagged injection (unsourced findings discarded). Requests, findings, and the exact injected guidance are logged to the workflow artifacts.

### Citation check (opt-in, post-verify advisory submodule)

Opt-in via `--check-citations` (requires `--paper`); a submodule under the evaluate phase that **never changes the Replication Score**. Dispatched by `_check_citations` (mirrors the research sub-agent pattern); the standalone `check-citations <replicate-dir>` subcommand re-runs it post-hoc on a finished run via `check_citations_existing()`, recovering the paper path from the run's saved config. Two parts:

- **Reference integrity** — a single web-enabled subagent extracts the paper's reference list and runs a deterministic, LLM-free resolver (`core/citations.py`, staged into the workspace as a script) that verifies existence + metadata (author/venue/year/identifier) against Crossref / OpenAlex / Semantic Scholar / DBLP / arXiv (keyless; optional free `SEMANTIC_SCHOLAR_API_KEY` only raises rate limits). Each reference is classified `verified | metadata_mismatch | likely_fabricated | inconclusive`; the agent web-search-escalates only unresolved references. Method adapted from refchecker (MIT).
- **Faithfulness** — whether each cited source actually supports what the paper attributes to it: `supported | partially_supported | contradicted | not_mentioned` on a `retrieved | inaccessible` source-status axis, each grounded in a verbatim quote. `--check-citations-faithfulness main` (default) limits this to the paper's central attributed claims; `all` extends to every claim-bearing citation.

An independent audit pass (`citation_audit.json`) re-checks flagged verdicts; a deterministic reconciliation only **softens** a flag toward the audit when the audit is less severe (never escalates). No human-review step. Output: `evaluation/citation_check.json` (+ `citation_audit.json`, intermediate `references.json` / `resolver_verdicts.json` / `resolve_references.py`). The report renders a citation section when present (`_render_citation_check`), degrading gracefully when absent.

After the score (and any evaluate / citation submodule), `_collect_resource_usage` writes top-level `resource_usage.json`: per-phase and total wall time (from `pipeline_state.json` timestamps), input/output token counts (summed from the JSONL transcripts), output-tree disk footprint, and an estimated API cost. It reads existing artifacts only and never alters the run.

Output is organized into per-phase subdirectories: `analyze/`, `replication/` (with `codebase/`, `codebase.diff`, `diligence_signals.json`, and `replication.attempt-N/` archives when the loop re-runs), `assess/`, `verify/`, `evaluation/` (`contextual_evaluation.json` plus, when citation check ran, `citation_check.json` / `citation_audit.json`), `report/` (markdown + HTML + PDF), `prompts/`, top-level `resource_usage.json`, and `.veritas/` (`pipeline_state.json`, `workflow.jsonl`, `workflow.md`, `codegen_complete`).

### Input modes

Veritas resolves the input mode at startup (auto-detected by default from which of `--paper` / `--repo` were supplied):

- **`full`** — paper PDF + repo. Claims come from the paper; replication runs against the repo.
- **`paper-only`** — paper PDF only. The codegen phase writes the methodology from the paper into a fresh codebase, then the rest of the pipeline runs against that generated codebase.
- **`repo-only`** — repo only. Claims are extracted from the repo's README; codegen is skipped.

`--mode` is the input-mode selector. `--claims path/to/claims.json` is a universal override that skips automatic extraction. `--data path/to/data-dir` mounts a host directory read-only at `/workspace/data/`; the path is surfaced to codegen / plan / replicate prompts via `has_data` so the agent uses these files instead of procuring from the network.

### Key modules (`src/veritas/core/`)

- `runner.py` — orchestrator. `run()` sequences the phases with resumable per-stage state. Provider invocation via `_invoke_provider` (single method using `subprocess.Popen`, stdin for the prompt, line-streamed JSONL transcript to disk, `threading.Timer` watchdog for wall-clock timeouts, `expose_api_keys` opt-in); JSON repair re-prompt logic; per-provider command/flag tables (`CLI_COMMANDS`, `TRANSCRIPT_FLAGS`, `PERMISSION_FLAGS`, `PROMPT_STDIN_ARGS`). Manager loop in `_replicate_with_manager_loop` / `_manager_review`; research in `_run_research` / `_dispatch_research_agent` / `_redact_finding`; verify split in `_run_single_verify` / `_apply_deterministic_grade`; contextual evaluation in `_evaluate`; citation check in `_check_citations` (+ `check_citations_existing` for the standalone subcommand); resource accounting in `_collect_resource_usage`. An existing run is extended (manager + report layered on without recompute) by re-invoking the resume-aware `run()` against the same output dir — what the standalone `evaluate` subcommand drives.
- `config.py` — `Config` dataclass with output-path properties; `VALID_PROVIDERS`, `VALID_INPUT_MODES`, `VALID_FAITHFULNESS_SCOPES`, output-structure constants (`*_SUBDIR`, `*_FILE`), per-phase timeout fields (`analyze_timeout`, `codegen_timeout`, `replicate_timeout`, `verify_timeout`, `evaluate_timeout`, `citation_timeout`), feature flags (`run_evaluation`, `run_citation_check`), `faithfulness_scope`, and `max_iters`. Timeout fields fall back to `VERITAS_*_TIMEOUT` when the CLI flag is absent.
- `config_env.py` — `load_dotenv_once()` (minimal no-override `.env` load so `VERITAS_*` works for direct CLI runs) plus typed helpers (`_env_int / _env_float / _env_str / _env_bool / _env_opt_int`) that fall back to the default and log a warning on a bad value. Resolves the `VERITAS_*` override layer.
- `grading.py` — pure deterministic grader. `GradingTolerances` (defaults from `VERITAS_GRADE_*`) + `grade_claim()`: decides `match` / `partial` / `no_match` for scalar / scalar_range / table claims from the comparator's value vs `paper_value` and tolerances. No LLM.
- `diligence.py` — `ExecutionFacts` dataclass + `compute_execution_facts(evidence, plan)`: objective facts over the replicate evidence (step coverage, artifacts emitted, exit codes, repeated commands, downsizing / placeholder hints). It makes **no** diligence judgment; the manager interprets the facts.
- `manager.py` — retry-loop machinery: `ManagerVerdict` + `parse_manager_verdict`, `facts_improved`, `should_stop` / `StopDecision`, `archive_attempt`, `WorkflowLog` (writes `workflow.jsonl` + `workflow.md`), `build_handoff`, and `ManagerGuidance` (the deficiency + directive + already-tried + research findings injected into a re-run).
- `research.py` — manager research support: `ResearchRequest` + `parse_research_requests`, `honor_request` (intent allow-list), `split_requests`, `known_value_strings` / `redact_known_values` / `RedactionResult` (deterministic exact-string scrub), `ResearchFinding` + `format_findings_for_guidance` (provenance-tagged), `ResearchConfig`.
- `citations.py` — deterministic, keyless reference resolver for the opt-in citation check: queries Crossref / OpenAlex / Semantic Scholar / DBLP / arXiv and classifies each reference `verified | metadata_mismatch | likely_fabricated | inconclusive` (existence + author/venue/year/identifier). No LLM. Staged into the workspace as `resolve_references.py` for the citation subagent to run. Adapted from refchecker (MIT).
- `paper_claims.py` — `parse_paper_claims_response()` reading the analyze-phase LLM output.
- `verify.py` — `compute_replication_score()`: pure-function tier-weighted aggregation over a list of `ClaimVerdict`s, returning a `ReplicationScore` with per-tier breakdown, missing-verdict list, and edge-case flags.
- `replication.py` — `parse_replication_plan_response()`, `gather_evidence()`, and `_extract_json` / `_fix_json_escapes` JSON-repair logic.
- `pipeline_state.py` — `PipelineState` class; persists per-phase status to `<output>/.veritas/pipeline_state.json` with a `schema_version` field (`STATE_SCHEMA_VERSION = 3`). Loading a state file with `schema_version < 3` raises a clear error directing the user to `--restart`.
- `report_generator.py` — report rendering. Deterministic context + HTML via `_build_html_context` / `_render_html` (`report.html.j2`); PDF from HTML via `_generate_pdf_from_html` (WeasyPrint, pandoc/LaTeX fallback); evaluation weaving via `_load_evaluation` / `_render_synthesis` / `_render_limitations` (graceful when absent). Also emits the markdown source.
- `models/` — dataclass-only sub-package: `replication.py` (`ReplicationPlan`, `ReplicationStep` with `verifies: List[str]`, `ExecutionEvidence`, `StepOutcome`, `AppliedFix`), `fix_severity.py` (`FixSeverityRating`, `FixSeverityAssessment`), `paper_claims.py` (`PaperClaim`, `PaperClaims`, `ClaimVerdict` with `graded_by`, `ReplicationScore`, `Provenance`, `TIER_WEIGHTS`, `VERDICT_VALUES`; tier weights overridable via `VERITAS_TIER_WEIGHT_*`), `resource_usage.py` (`ResourceUsage`, `PhaseUsage`).

### Utilities (`src/veritas/utils/`)

- `security.py` — API key redaction via regex patterns; recursive log sanitization across the output tree.
- `transcripts.py` — `sum_tokens_from_transcript()`: sums input/output token counts from a provider JSONL transcript (used by resource accounting).

### Templates (`templates/`)

- `analyze/paper_claims_extraction.md` — analyze phase: extracts structured paper claims.
- `codegen/session_instructions.md` — codegen phase (paper-only): writes the methodology into a fresh codebase; includes an intermediate-anchor & selection-sanity audit (validate intermediates against documented *method* anchors only, never result values).
- `replication/plan_generation.md` — plan phase: produces a claim-aware plan with `verifies` per step and shape-prescriptive `expected_outcome`; repo-first guidance; `manager_guidance` injection block.
- `replication/session_instructions.md` — replicate phase: active fix-and-continue + try-harder + anti-tuning Reporting Discipline + sanity-check-intermediates-against-method-anchors; `manager_guidance` injection block. Consumes only the plan; never `paper_claims.json`.
- `assess/fix_severity.md` — assess phase: rates each applied fix as minor/major/critical.
- `verify/single_claim.md` — verify phase: comparator + answer-fidelity guidance with Jinja2 branches per claim type.
- `manager/replication_review.md` — manager review: emits the structured accept/revise verdict.
- `research/resource_finder.md`, `research/literature_finder.md`, `research/redactor.md` — research sub-agents and the LLM redactor.
- `evaluation/contextual_evaluation.md` — evaluation phase: cheating monitor + contextual evaluation + report narrative (the report `report` block).
- `evaluation/citation_check.md`, `evaluation/citation_audit.md` — citation-check subagent (reference integrity + faithfulness) and the independent audit pass.
- `report/report.html.j2` — styled single-file HTML report (verdict card, tier bar, per-claim chips, collapsible details).
- `report/insufficient_spec.md` — the analyze bail report when 0 claims are extracted.
- `skills/` — 16 scientific-computing skill catalogs (astropy, dask, polars, scikit-learn, statsmodels, matplotlib, etc.) staged for the agent at `VERITAS_SKILLS_DIR` (default `/workspace/veritas-skills`) and surfaced into the codegen / replicate prompts.

All templates are Jinja2, rendered by `src/veritas/templates/prompt_generator.py`.

### Docker

Multi-stage CUDA 12.5.1 build (`docker/Dockerfile`). The image bakes in the veritas Python package (`uv sync --frozen`), Claude/Codex/Gemini CLIs, pandoc + LaTeX and WeasyPrint for report generation, an R 4.x toolchain (tidyverse / rmarkdown / papaja and the common CB-Hard packages), and the skills catalog. Runs as non-root `veritas` user (UID/GID configurable at build time). The `./veritas` bash wrapper (forwarding to `docker/run.sh`) handles host-side concerns: GPU auto-detection, macOS Keychain extraction for Claude credentials, `--platform linux/amd64` on Apple Silicon, path rewriting for `--paper`/`--repo`/`--data`/`--output`, command routing (`full` / `evaluate` / bare flags), and image pull-from-GHCR with local-build fallback. `docker/entrypoint.sh` sets `umask 000` so container-created files are manageable from the host regardless of UID mismatch.

## Gotchas

- **The replication agent actively fixes issues.** The agent works on a writable copy of the repo at `/workspace/output/replication/codebase/`. It may patch deprecated APIs, install missing tools, and fix configuration issues. Every fix is tracked in `StepOutcome.fixes_applied` and rated for severity by a separate post-replicate LLM pass. The original repo at `/workspace/repo` remains read-only.
- **The user's repo is bind-mounted read-only** at `/workspace/repo` by the wrapper. The entrypoint copies it to `/workspace/output/replication/codebase/` for the agent to modify. An EXIT trap generates a unified diff at `/workspace/output/replication/codebase.diff`.
- **`--data` is mounted read-only at `/workspace/data/`.** Surfaced to codegen / plan / replicate prompts via `has_data`. Agent writes (downloaded auxiliary files) land in `codebase/data/` instead — the two directories don't collide. `data_path` participates in the input fingerprint as a resolved-path string; changing `--data` between runs invalidates downstream phases.
- **The replication agent never sees `paper_claims.json`.** The session prompt is rendered with `replication_plan` only; `expected_outcome` is shape-prescriptive rather than value-prescriptive, and the Reporting Discipline forbids tuning toward reported numbers. This is the structural defense against leaking paper-reported result values to the replicator.
- **The manager always runs when the loop is on.** With `max_iters > 1` there is no deterministic short-circuit-accept; `_manager_review` runs every iteration over the objective `ExecutionFacts`. With `max_iters <= 1` (the `replicate` / benchmark default) the manager never runs, no workflow log is written, and behavior is identical to a single pass. The retry counter increments only on an actual re-run; a re-run archives the prior attempt to `replication.attempt-N/` rather than overwriting.
- **`ExecutionFacts` are objective, not a verdict.** `diligence.py` only computes facts; the accept/revise judgment is the manager's. The facts file is `replication/diligence_signals.json` (the dataclass is `ExecutionFacts`).
- **Verify is split and partly deterministic.** The comparator LLM proposes a value; the deterministic grader decides scalar / scalar_range / table outcomes from it. `graded_by` distinguishes the two paths. `not_applicable` is never overridden by the grader. This is the auditable replacement for prompt-only grading.
- **The evaluation phase is advisory.** It never moves the Replication Score, runs only when opted in, and is idempotent via a file-exists check. If it didn't run or its output is malformed, the report falls back to the deterministic sections.
- **The citation check is advisory and deterministic-first.** Opt-in via `--check-citations` (requires `--paper`); it never touches the Replication Score. Existence/metadata is decided by scholarly-database records (`core/citations.py`), not LLM judgment, to avoid false positives; the agent only does extraction, web-search escalation, and faithfulness. The independent audit can only soften a flag, never escalate. Lookups are keyless. It is a submodule under the evaluate phase, not a pipeline phase — the report renders it when present and omits it otherwise.
- **Verify phase is per-claim with file-exists resume.** A failed verifier call leaves `verify/<claim_id>.json` absent; the next run re-attempts that claim only. State tracks `completed_claims`.
- **Pipeline state `schema_version` is 3.** Old state files (`< 3`) raise a clear error directing the user to `--restart`; silent reuse would mix incompatible artifacts.
- **Provider CLI resolution is cross-platform** — `_resolve_cli()` in `runner.py` handles Windows `.cmd` shims via `shutil.which()`. Don't hardcode paths.
- **Codex has non-obvious invocation requirements** — its `PERMISSION_FLAGS` use `--dangerously-bypass-approvals-and-sandbox` (the deprecated `--full-auto` keeps a network-blocking sandbox that breaks replicate-phase installs/downloads) plus `--skip-git-repo-check` (phase working dirs aren't git repos), and `PROMPT_STDIN_ARGS` appends the `-` sentinel so `codex exec` actually reads the piped prompt. Claude (`-p`) and gemini read stdin natively. The container is the isolation boundary, matching the trust already granted to claude.
- **Windows Git Bash requires `winpty` for interactive subcommands** — mintty uses Windows pipes instead of Unix ptys, so `docker run -it` fails with "the input device is not a TTY". The top-level `./veritas` wrapper auto-re-execs under `winpty` when detected; if `winpty` is missing, `get_tty_flag` falls back to `-i`-only (scripted use works; interactive sessions like `./veritas shell` and `./veritas login` are degraded). Modern Git for Windows ships with `winpty` by default. Linux and macOS are unaffected.
- **JSON responses from LLMs are unreliable.** `core/replication.py` has multi-strategy extraction (raw → markdown blocks → brace matching) plus escape repair in `_extract_json` / `_fix_json_escapes`. Both `paper_claims.py` and the verifier consumer in `runner.py` route through `_extract_json()`.
- **GPU is auto-detected and Linux-only** (requires NVIDIA Container Toolkit).
- **Two runtimes: docker (`./veritas`) and host (`./veritas-host`).** Docker is the default; the wrapper manages image lifecycle (pull from GHCR on first run, build locally if pull fails). Host mode is for environments without docker (HPC clusters); the user provides claude/codex/gemini CLI, python, and uv on PATH, and `veritas-host` does the workspace pre-staging that `docker/entrypoint.sh` does in docker mode (`templates/skills/` → `<output>/veritas-skills/`, `--repo` → `<output>/replication/codebase/`, EXIT-trap codebase.diff). Both runtimes share the Python pipeline; the two `/workspace/`-derived paths in templates (skills catalog, agent venv) are parameterized via `VERITAS_SKILLS_DIR` and `VERITAS_VENV_DIR` env vars with docker-mode defaults.
- **Image contains the whole runtime.** Changes to `src/`, `templates/`, `pyproject.toml`, or `uv.lock` require a rebuild (`./veritas build`) or an update from GHCR (`./veritas update`). The CI workflow rebuilds automatically on main-branch pushes.
- **GPU two-step auto-detect.** `docker/run.sh::get_gpu_flags` checks both that the NVIDIA Container Toolkit is installed (`docker info | grep nvidia`) AND that a GPU is actually reachable (`docker run --gpus all ... nvidia-smi`). The second probe catches WSL and emulated environments where the toolkit is present but no GPU adapter is accessible. If the veritas image isn't built yet, the probe is skipped.
- **Replication API keys live in `$PROJECT_ROOT/.env`** (chmod 600, gitignored). Passed into the container via `--env-file` on `cmd_replicate` / `cmd_shell` only. The wrapper publishes the var-name list as `VERITAS_ENV_FILE_KEYS`; `runner.py::_invoke_provider` strips those vars from the subprocess env by default, and only the `_replicate` call site opts in via `expose_api_keys=True`. So analyze/plan/codegen/assess/verify/evaluate/manager/research agents never see the keys, but the paper code run during replicate does. `./veritas setup` and `./veritas config` subcommands manage the file. The same `.env` also holds the non-secret `VERITAS_*` tunables (documented in `.env.example`).

## Testing

The Python test suite was re-introduced now that the manager-loop redesign has stabilized. Two layers:

- **Python unit tests (`tests/`)** — cover the deterministic modules: `test_config_env.py` (`VERITAS_*` resolution), `test_diligence.py` (`ExecutionFacts`), `test_grading.py` / grader battery, `test_manager.py` and `test_manager_loop_integration.py` (verdict parsing, termination, archival, workflow log, guidance injection, full-loop integration), and `test_research.py` (intent gate, exact-value scrub, provenance, bounds). Run with `uv run pytest`.
- **`scripts/test_docker.sh`** — asserts the built image has functional claude/codex/gemini/pandoc/pdflatex/python/veritas and that the entrypoint banner prints. CI runs it automatically against the pushed image in `.github/workflows/docker-publish.yml`.

Coverage skews toward the manager-loop modules; the older load-bearing contracts (provider argv threading, evidence JSON-repair, config cascade, paths) are still uncovered. Favor adding unit coverage for new deterministic modules.

## Related Work

- **NeuriCo** (formerly idea-explorer, `C:/MyFolders/Research/AI Replication/idea-explorer`) — upstream project veritas adapted architecture and Docker setup from; improvements are ported back in both directions.
- **PaperBench** (OpenAI, ICML 2025) — uses a hierarchical author-co-developed rubric with three judge-artifact types (code_development / execution / result_match) and binary leaf grading. Veritas's shape-typed claim enum is a different axis (claim content shape vs. judge artifact).
- **ReplicationBench** (Ye et al., 2025, arXiv:2510.24591) — primary numerical-comparison benchmark veritas aims to be comparable with. Tests AI agents replicating astrophysics papers from scratch.
- **CORE-Bench** — the CB-Hard capsule set veritas is measured against; deterministic answer grading the verify split brings in-house.
- **Scaling Reproducibility** (Xu & Yang, 2026) — paper+repo political-science replication; single-scalar (2SLS coefficient) numerical match. Veritas's flexible/agentic approach contrasts with that benchmark's deterministic workflow.
