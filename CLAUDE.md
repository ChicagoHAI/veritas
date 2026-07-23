# Veritas

Veritas is a replication agent that evaluates whether scientific papers can be reproduced. It analyzes the inputs to extract structured claims, optionally generates code from the paper (paper-only mode), plans the replication, runs the methodology inside Docker (actively fixing issues, under an optional manager-controlled retry loop), assesses fix severity, verifies each claim against the produced evidence, and emits a tier-weighted Replication Score. An optional evaluation pass writes the narrative report on top of that.

## Project Status

The claim-verification pipeline is complete end to end: claims extraction in analyze, per-claim adjudication in verify with a deterministic grader, tier-weighted scoring, a manager-controlled retry loop around replicate, and an HTML/PDF/markdown report. Check open GitHub issues before changing pipeline shape or verifier behavior.

### Design principles

- **Replication is the primary output.** The Replication Score and per-claim verdicts are the publishable result; replication evidence and fix-severity context are the supporting narrative.
- **The replicate agent should try hard to reproduce results.** Environment errors, API deprecations, and missing compilers should be fixed so replication can continue. Only give up after genuine effort. Every applied fix is logged and rated for severity in a separate pass.
- **Paper claims drive verification.** Each paper's specific reproducible claims are extracted into `paper_claims.json` (5 shape-typed categories: `scalar | scalar_range | table | qualitative | figure`; 2 tiers: `headline | supporting`). The verifier adjudicates each claim independently with a fresh-context LLM call.
- **The agent that produces a number never grades it.** For `scalar`, `scalar_range`, and `table` claims the verify phase splits in two: an LLM *comparator* extracts the value the run produced, and a pure, LLM-free *grader* (`core/grading.py`) decides the verdict from that value against the paper value and a declared tolerance. Each verdict records how it was graded in `graded_by`. `qualitative` and `figure` claims have no number to compute on and keep the comparator's judgment.
- **Replication Score is a tier-weighted fraction**: `score = Σ(tier_weight × verdict_value) / Σ(tier_weight)` with tier weights `3 / 2` for headline / supporting, verdict values `match=1.0, partial=0.5, no_match=0.0, not_attempted=0.0`, and `not_applicable` excluded from both sums.
- **No hardcoded configs.** Tunables resolve highest-wins as `CLI flag → VERITAS_* env var → code default`, via `core/config_env.py`. This covers tier weights, grading tolerances, retry caps, and research bounds. See `.env.example`.
- **The replicate agent never sees `paper_claims.json`.** The replication plan references claim IDs in a `verifies` field but doesn't embed paper-reported result values. Plan steps' `expected_outcome` is shape-prescriptive (file path, JSON field names, figure layout), not value-prescriptive. This is veritas's structural defense against ground-truth leakage to the replication agent. Prompt templates must not carry real reported values as worked examples either.
- **The final codebase used during replication is preserved as output**: `replication/codebase/` holds the patched copy and `replication/codebase.diff` shows the unified diff vs. the original repo.
- **Veritas is being modularized** — components (execution environment, LLM provider, scoring formula, output format) are progressively being split into swappable modules.

## Commands

```bash
# Install
git clone https://github.com/ChicagoHAI/veritas.git && cd veritas

# Full pipeline: replicate + evaluate + styled report (the normal way to run it)
./veritas --paper paper.pdf --repo ./my-project
./veritas full --paper paper.pdf --repo ./my-project   # same thing, named

# Replication only — stops after verify, skips the evaluation manager.
# This is the lean mode for benchmarking.
./veritas replicate --paper paper.pdf --repo ./my-project

# Select provider
./veritas replicate --repo ./my-project --provider codex

# Select input mode explicitly (default: auto-detected from inputs)
./veritas replicate --paper paper.pdf --mode paper-only  # generate code from paper, then run
./veritas replicate --repo ./my-project --mode repo-only # extract claims from README

# Supply a hand-authored claims JSON (skips automatic extraction)
./veritas replicate --repo ./my-project --claims claims.json

# Pre-position a data directory (mounted read-only at /workspace/data/)
./veritas replicate --paper paper.pdf --data ./prepositioned-data

# Manager-controlled retry loop (default 1 = single pass, loop off)
./veritas replicate --repo ./my-project --max-iters 3

# Per-phase timeouts (default: no timeout)
./veritas replicate --repo ./my-project --analyze-timeout 600 --verify-timeout 300
# also: --codegen-timeout, --replicate-timeout, --evaluate-timeout, --citation-timeout

# Opt-in citation check (verify the paper's references exist + metadata is correct)
./veritas replicate --paper paper.pdf --repo ./my-project --check-citations

# Resource/cost estimate without running the pipeline
./veritas estimate --paper paper.pdf --repo ./my-project
./veritas replicate --repo ./my-project --dry-run

# Post-hoc passes on an already-completed run directory
./veritas evaluate ./replicate-dir          # manager narrative + report
./veritas report ./replicate-dir            # re-render the report (no LLM)
./veritas check-citations ./replicate-dir   # citation check on a finished run

# Container lifecycle (docker runtime only)
./veritas shell     # interactive shell inside the replication container
./veritas setup     # one-shot prereqs + image + login + .env
./veritas config    # manage the .env replication API keys
./veritas login     # provider CLI auth
./veritas status    # dashboard
./veritas build     # build the image locally (first run pulls from GHCR instead)
./veritas update    # pull the latest image

# Smoke test the built image
./scripts/test_docker.sh
```

`full`, `shell`, `setup`, `config`, `login`, `build`, `update`, and `status` are wrapper-level commands implemented in `docker/run.sh`; the Python CLI itself exposes only `replicate`, `estimate`, `report`, `evaluate`, and `check-citations`. `veritas-host` implements the pipeline subcommands but not the docker-image-lifecycle ones.

## Architecture

Pipeline orchestrated by `ReplicationRunner.run()` in `src/veritas/core/runner.py`:

1. **Analyze** (`_generate_paper_claims`) — extracts `paper_claims.json`. Source depends on input mode: paper PDF (`full` / `paper-only`), repo README (`repo-only`), or a user-supplied `--claims` JSON (universal override, validated and copied through). Yielding 0 claims raises `_InsufficientSpec` and triggers a dedicated bail report instead of propagating as an error.
2. **Codegen** (`_generate_code`, paper-only mode only) — has the agent write the paper's methodology from scratch into `replication/codebase/`. Sentinel-based resume at `<output>/.veritas/codegen_complete`. Anti-leakage: `paper_claims.json` is intentionally out of this phase's scope.
3. **Plan** (`_generate_replication_plan`) — generates the claim-aware `replication_plan.json` from the effective codebase (the user's repo, or the generated one in paper-only mode). Uses `Config.effective_repo_path`. Plan steps carry a `verifies: List[str]` field referencing claim IDs; a post-plan cross-check (`_validate_plan_claim_refs`) warns on unknown IDs.
4. **Resource estimation** (`_estimate_resources`) — combines an AST scan of the repo (`utils/static_analysis.py`) with an LLM pass to write `analyze/resource_estimate.json` (GPU need, external-LLM use, parallelism, compute class). Non-fatal. Backs `--dry-run` and the `estimate` subcommand.
5. **Replicate + manager loop** (`_replicate_with_manager_loop`) — runs the plan inside a writable copy of the codebase via an AI agent that actively fixes issues. Wraps:
   - `_replicate` — the replication agent itself; collects execution evidence and fix records. Never sees `paper_claims.json`.
   - `_compute_and_write_execution_facts` — `core/diligence.py` computes objective facts (planned vs. executed steps, exit codes, declared output files, repeated commands/tool calls) into `replication/diligence_signals.json`. It does not judge diligence.
   - `_manager_review` (only when `--max-iters > 1`) — an independent LLM control gate (`core/manager.py`) that reads the facts and returns accept/revise, bounded by a hard deterministic cap.
   - `_run_research` (only inside a manager-directed revise) — narrow resource-finder / literature-finder sub-agents (`core/research.py`) behind an intent allow-list and two-layer redaction before any finding reaches the re-run guidance.
6. **Assess Fixes** (`_assess_fixes`) — rates severity of each fix applied during replication (minor/major/critical) via a separate LLM pass. Output: `assess/fix_severity.json`.
7. **Verify** (`_verify_with_resume`) — one provider invocation per claim. Each verifier reads the relevant evidence files and produces a structured verdict at `verify/<claim_id>.json` (status `match | partial | no_match | not_attempted | not_applicable`, type-specific `structured` field, free-text `rationale`, `evidence_refs`). Numeric claims then pass through `_apply_deterministic_grade` → `core/grading.py::grade_claim`. Per-claim resume primitive: file-exists check.
8. **Score** (`_score_after_verify`) — aggregates into `verify/verdicts.json` and `verify/replication_score.json`.
9. **Evaluate** (`_evaluate`, opt-in via `--evaluate`; on by default in `full`) — a manager reviews the whole run and writes `evaluation/contextual_evaluation.json`: which claims matter, how well it reproduced, what didn't and why, plus an advisory cheating monitor. Never changes the score.
10. **Resource usage** (`_collect_resource_usage`) — wall time, token counts, disk footprint, and an approximate cost estimate into `resource_usage.json`.
11. **Report** (`_report`) — renders `report/replication_report.{html,pdf,md}`.

- **Citation check** (`_check_citations` / `_audit_citations`, opt-in via `--check-citations`) — a
  post-verify advisory submodule under the evaluate phase. A single web-enabled
  subagent extracts the paper's reference list and runs a deterministic,
  LLM-free resolver (`core/citations.py`, staged into the workspace as a script)
  that verifies existence/metadata against Crossref/OpenAlex/Semantic
  Scholar/DBLP/arXiv (keyless); the agent web-search-escalates unresolved
  references and venue-checks resolver-verified records that lack a venue.
  Output: `evaluation/citation_check.json`. Advisory: never changes
  the Replication Score. Requires `--paper`. Method adapted from
  [refchecker](https://github.com/markrussinovich/refchecker) (MIT).
  The faithfulness sub-pass checks whether each cited source actually
  supports what the paper attributes to it, with verdicts `supported`,
  `partially_supported`, `contradicted`, or `not_mentioned`; the first three are
  each grounded in a verbatim quote from the source. `--check-citations-faithfulness main` (default)
  limits this to the paper's central attributed claims; `all` extends it to every
  claim-bearing citation. A scope change re-runs the check (the producing scope
  is recorded in `evaluation/.citation_check_meta.json`). An independent audit pass writes its own verdicts to
  `evaluation/citation_audit.json`; a deterministic reconciliation softens any flagged
  verdict toward the audit only when the audit is less severe (never escalates).
  The `check-citations <replicate-dir>` subcommand runs the full citation check
  on an already-completed run; it recovers the paper path from the run's saved
  config, with `--paper` as an override (in docker mode the saved path is a
  container path from the original run, so `--paper` is effectively required there).

### Input modes

Veritas resolves the input mode at startup (auto-detected by default from which of `--paper` / `--repo` were supplied):

- **`full`** — paper PDF + repo. Claims come from the paper; replication runs against the repo.
- **`paper-only`** — paper PDF only. The codegen phase writes the methodology from the paper into a fresh codebase, then the rest of the pipeline runs against that generated codebase.
- **`repo-only`** — repo only. Claims are extracted from the repo's README; codegen is skipped.

`--mode` is the input-mode selector. `--claims path/to/claims.json` is a universal override that skips automatic extraction. `--data path/to/data-dir` mounts a host directory read-only at `/workspace/data/`; the path is surfaced to codegen / plan / replicate prompts via `has_data` so the agent uses these files instead of procuring from the network.

### Key modules (`src/veritas/core/`)

- `runner.py` — orchestrator; provider invocation via `_invoke_provider` (single method using `subprocess.Popen`, stdin for the prompt, line-streamed JSONL transcript to disk, `threading.Timer` watchdog for wall-clock timeouts); JSON repair re-prompt logic; per-provider command/flag tables (`CLI_COMMANDS`, `TRANSCRIPT_FLAGS`, `PERMISSION_FLAGS`, `PROMPT_STDIN_ARGS`).
- `config.py` — `Config` dataclass with output-path properties; `VALID_PROVIDERS`, output-structure constants (`*_SUBDIR`, `*_FILE`), per-phase timeout fields.
- `config_env.py` — typed `VERITAS_*` env-var helpers implementing the `CLI flag → env var → code default` resolution; best-effort non-overriding `.env` load for direct (non-wrapper) CLI invocations.
- `paper_claims.py` — `parse_paper_claims_response()` reading the analyze-phase LLM output.
- `grading.py` — `grade_claim()` and `GradingTolerances`: the deterministic, LLM-free grader for `scalar` / `scalar_range` / `table` claims. Pure functions, no I/O, no model. Tolerances are env-overridable via `VERITAS_GRADE_*`.
- `verify.py` — `compute_replication_score()`: pure-function tier-weighted aggregation over a list of `ClaimVerdict`s, returning a `ReplicationScore` with per-tier breakdown, missing-verdict list, and edge-case flags.
- `manager.py` — the manager-controlled retry loop's pure core: `ManagerVerdict` parsing, the `should_stop` termination predicate (hard cap + accept + no-progress), `archive_attempt` (copies `replication/` to `replication.attempt-N/` before a re-run), `WorkflowLog` (append-only JSONL plus a regenerated markdown trajectory), and `ManagerGuidance` (answer-free re-run guidance).
- `diligence.py` — `compute_execution_facts()` / `ExecutionFacts`: objective facts about a replicate run only. Deliberately no keyword or semantic matching; those judgments belong to the manager.
- `research.py` — the manager's research sub-agents: intent allow-list (`honor_request` / `split_requests`), deterministic known-value scrub (`redact_known_values`), and provenance-tagged formatting (`format_findings_for_guidance`).
- `citations.py` — deterministic, stdlib-only bibliographic resolver (Crossref / OpenAlex / Semantic Scholar / DBLP / arXiv). Staged into the agent workspace as a standalone script so the subagent can run it without importing the package.
- `replication.py` — `parse_replication_plan_response()`, `gather_evidence()`, and `_extract_json` / `_fix_json_escapes` JSON-repair logic.
- `pipeline_state.py` — `PipelineState` class; persists per-phase status to `<output>/.veritas/pipeline_state.json` with a `schema_version` field.
- `models/` — dataclass-only sub-package: `replication.py` (`ReplicationPlan`, `ReplicationStep` with `verifies: List[str]`, `ExecutionEvidence`, `StepOutcome`, `AppliedFix`), `fix_severity.py` (`FixSeverityRating`, `FixSeverityAssessment`), `paper_claims.py` (`PaperClaim`, `PaperClaims`, `ClaimVerdict`, `ReplicationScore`, `Provenance`, `TIER_WEIGHTS`, `VERDICT_VALUES`), `resource_estimate.py` (`ResourceEstimate`), `resource_usage.py` (`ResourceUsage`, `PhaseUsage`).
- `report_generator.py` — markdown + HTML + PDF report generation (Replication Score headline, verdict card, tier breakdown, per-claim verdict table, flags, replication evidence, fixes-applied section, environment summary).

### Utilities (`src/veritas/utils/`)

- `security.py` — API key redaction via regex patterns; recursive log sanitization across the output tree.
- `static_analysis.py` — `analyze_repo()`: AST scan detecting GPU / LLM / parallel / network-call usage, feeding resource estimation.
- `transcripts.py` — `sum_tokens_from_transcript()`: token accounting from a JSONL transcript.

### Templates (`templates/`)

All templates are Jinja2, rendered by `src/veritas/templates/prompt_generator.py`.

- `analyze/paper_claims_extraction.md` — extracts structured paper claims.
- `analyze/resource_estimation.md` — resource/cost estimate.
- `codegen/session_instructions.md` — paper-only-mode codegen.
- `replication/plan_generation.md` — claim-aware plan with `verifies` per step and shape-prescriptive `expected_outcome`.
- `replication/session_instructions.md` — active fix-and-continue instructions for the replication agent (consumes only the plan; never `paper_claims.json`).
- `manager/replication_review.md` — the manager's accept/revise review.
- `research/resource_finder.md`, `research/literature_finder.md` — the two research sub-agent prompts.
- `research/redactor.md` — the LLM redaction layer applied to research findings.
- `assess/fix_severity.md` — rates each applied fix as minor/major/critical.
- `verify/single_claim.md` — per-claim adjudication with type-specific guidance (Jinja2 branches per claim type).
- `evaluation/contextual_evaluation.md` — the post-verify manager narrative and cheating monitor.
- `evaluation/citation_check.md`, `evaluation/citation_audit.md` — citation check and independent audit.
- `report/report.html.j2` — the styled, self-contained HTML report (the primary report artifact).
- `report/insufficient_spec.md` — bail report used when analyze yields 0 claims.

`templates/skills/` is a separate, bundled catalog of 16 pre-written agent skill packages: `astropy`, `dask`, `exploratory-data-analysis`, `get-available-resources`, `markdown-mermaid-writing`, `markitdown`, `matplotlib`, `networkx`, `pdf`, `polars`, `scientific-visualization`, `scikit-learn`, `seaborn`, `statistical-analysis`, `statsmodels`, `sympy`. It is copied into the workspace at `veritas-skills/` and exposed to the replication agent via `VERITAS_SKILLS_DIR`, so the agent has ready reference material for common scientific-computing tasks. These are reference documents, not part of the pipeline's control flow.

### Docker

Multi-stage CUDA 12.5.1 build (`docker/Dockerfile`). The image bakes in the veritas Python package (`uv sync --frozen`), Claude/Codex/Gemini CLIs, R (from the CRAN apt repo, since some papers require R >= 4.2), WeasyPrint (the primary HTML→PDF path), and pandoc + LaTeX (the fallback PDF path). Runs as non-root `veritas` user (UID/GID configurable at build time). The `./veritas` bash wrapper (forwarding to `docker/run.sh`) handles host-side concerns: GPU auto-detection, macOS Keychain extraction for Claude credentials, `--platform linux/amd64` on Apple Silicon, path rewriting for `--paper`/`--repo`/`--data`/`--output`, and image pull-from-GHCR with local-build fallback. `docker/entrypoint.sh` sets `umask 000` so container-created files are manageable from the host regardless of UID mismatch.

## Output structure

```
replicate/
├── analyze/        paper_claims.json, replication_plan.json, resource_estimate.json (+ transcripts)
├── replication/    codebase/, codebase.diff, replication_log.json, evidence_summary.json,
│                   diligence_signals.json, manager_review.json, research_*.json
├── replication.attempt-N/   archived prior attempts (manager loop re-runs)
├── assess/         fix_severity.json
├── verify/         <claim_id>.json (per claim, with graded_by), verdicts.json, replication_score.json
├── evaluation/     contextual_evaluation.json, citation_check.json, citation_audit.json
├── report/         replication_report.{html,pdf,md}
├── prompts/        rendered prompts (debug)
├── resource_usage.json
└── .veritas/       pipeline_state.json, workflow.jsonl, workflow.md
```

## Gotchas

- **The replication agent actively fixes issues.** The agent works on a writable copy of the repo at `/workspace/output/replication/codebase/`. It may patch deprecated APIs, install missing tools, and fix configuration issues. Every fix is tracked in `StepOutcome.fixes_applied` and rated for severity by a separate post-replicate LLM pass. The original repo at `/workspace/repo` remains read-only.
- **The user's repo is bind-mounted read-only** at `/workspace/repo` by the wrapper. The entrypoint copies it to `/workspace/output/replication/codebase/` for the agent to modify. An EXIT trap generates a unified diff at `/workspace/output/replication/codebase.diff`.
- **`--data` is mounted read-only at `/workspace/data/`.** Surfaced to codegen / plan / replicate prompts via `has_data`. Agent writes (downloaded auxiliary files) land in `codebase/data/` instead — the two directories don't collide. `data_path` participates in the input fingerprint as a resolved-path string; changing `--data` between runs invalidates downstream phases.
- **The replication agent never sees `paper_claims.json`.** The session prompt is rendered with `replication_plan` only; `expected_outcome` is shape-prescriptive (file paths, field names) rather than value-prescriptive. This is the structural defense against leaking paper-reported result values to the replicator. The same rule applies to prompt templates: worked examples must not carry real reported values.
- **The manager loop is off by default.** `--max-iters` defaults to 1 (single pass), which keeps `replicate` benchmark-comparable. Only `> 1` engages the manager review gate and research sub-agents.
- **Verify phase is per-claim with file-exists resume.** A failed verifier call leaves `verify/<claim_id>.json` absent; the next run re-attempts that claim only. State tracks `completed_claims` (not `completed_categories`).
- **Pipeline state `schema_version` is 3.** Old state files (`< 3`) raise a clear error directing the user to `--restart`; silent reuse would mix incompatible artifacts.
- **Provider CLI resolution is cross-platform** — `_resolve_cli()` in `runner.py` handles Windows `.cmd` shims via `shutil.which()`. Don't hardcode paths.
- **Windows Git Bash requires `winpty` for interactive subcommands** — mintty uses Windows pipes instead of Unix ptys, so `docker run -it` fails with "the input device is not a TTY". The top-level `./veritas` wrapper auto-re-execs under `winpty` when detected; if `winpty` is missing, `get_tty_flag` falls back to `-i`-only (scripted use works; interactive sessions like `./veritas shell` and `./veritas login` are degraded). Modern Git for Windows ships with `winpty` by default. Linux and macOS are unaffected.
- **JSON responses from LLMs are unreliable.** `core/replication.py` has multi-strategy extraction (raw → markdown blocks → brace matching) plus escape repair in `_extract_json` / `_fix_json_escapes`. Both `paper_claims.py` and the verifier consumer in `runner.py` route through `_extract_json()`.
- **GPU is auto-detected and Linux-only** (requires NVIDIA Container Toolkit).
- **Two runtimes: docker (`./veritas`) and host (`./veritas-host`).** Docker is the default; the wrapper manages image lifecycle (pull from GHCR on first run, build locally if pull fails). Host mode is for environments without docker (HPC clusters); the user provides claude/codex/gemini CLI, python, and uv on PATH, and `veritas-host` does the workspace pre-staging that `docker/entrypoint.sh` does in docker mode (`templates/skills/` → `<output>/veritas-skills/`, `--repo` → `<output>/replication/codebase/`, EXIT-trap codebase.diff). Both runtimes share the Python pipeline; the two `/workspace/`-derived paths in templates (skills catalog, agent venv) are parameterized via `VERITAS_SKILLS_DIR` and `VERITAS_VENV_DIR` env vars with docker-mode defaults.
- **Image contains the whole runtime.** Changes to `src/`, `templates/`, `pyproject.toml`, or `uv.lock` require a rebuild (`./veritas build`) or an update from GHCR (`./veritas update`). The CI workflow rebuilds automatically on main-branch pushes.
- **GPU two-step auto-detect.** `docker/run.sh::get_gpu_flags` checks both that the NVIDIA Container Toolkit is installed (`docker info | grep nvidia`) AND that a GPU is actually reachable (`docker run --gpus all ... nvidia-smi`). The second probe catches WSL and emulated environments where the toolkit is present but no GPU adapter is accessible. If the veritas image isn't built yet, the probe is skipped. `get_gpu_info` (docker) / `detect_gpu_info` (host) reuse this same reachability result to capture each device's actual model/VRAM (`nvidia-smi --query-gpu=name,memory.total`) as `VERITAS_GPU_INFO` — a semicolon-joined string, empty when no GPU is reachable — so `prompt_generator.py` can state real GPU presence and capacity as a fact in codegen/plan/replicate prompts, instead of leaving the agent to infer it or telling it only a bare yes/no.
- **Replication API keys live in `$PROJECT_ROOT/.env`** (chmod 600, gitignored). Passed into the container via `--env-file` on `cmd_replicate` / `cmd_shell` only. The wrapper publishes the var-name list as `VERITAS_ENV_FILE_KEYS`; `runner.py::_invoke_provider` strips those vars from the subprocess env by default, and only the `_replicate` call site opts in via `expose_api_keys=True`. So analyze/plan/codegen/assess/verify agents never see the keys, but the paper code run during replicate does. `./veritas setup` and `./veritas config` subcommands manage the file.

## Testing

```bash
pip install -e ".[dev]"   # or: uv sync
pytest                    # pythonpath and testpaths are configured in pyproject.toml
```

`tests/` covers the pure, deterministic layers — the parts where a wrong answer is silent:

| File | Covers |
|---|---|
| `test_citations.py` | `core/citations.py` — the deterministic bibliographic resolver (the largest suite) |
| `test_diligence.py` | `core/diligence.py` — step coverage, exit codes, output files, command/tool-call repeat detection |
| `test_manager.py` | `core/manager.py` — verdict parsing, `should_stop`, `archive_attempt`, `WorkflowLog` |
| `test_research.py` | `core/research.py` — intent allow-list, two-layer redaction, provenance formatting |
| `test_manager_loop_integration.py` | the manager retry loop wired into the runner |
| `test_config_env.py` | `core/config_env.py` typed env-var helpers |
| `test_grading.py` | `core/grading.py` deterministic grader shape handling |
| `test_verify.py` | `verify.py::compute_replication_score` tier-weight contract |

`test_diligence.py` can additionally run against a real `replication_log.json` by setting `VERITAS_REAL_LOG_FIXTURE` to its path; those tests skip when it is unset.

- **`scripts/test_docker.sh`** — asserts the built image has functional claude/codex/gemini/pandoc/pdflatex/python/veritas and that the entrypoint banner prints. Run locally with `./scripts/test_docker.sh <image-tag>`; CI runs it automatically against the pushed image in `.github/workflows/docker-publish.yml`.

## Related Work

- **NeuriCo** ([ChicagoHAI/NeuriCo](https://github.com/ChicagoHAI/NeuriCo)) — upstream project veritas adapted its architecture and Docker setup from.
- **PaperBench** (OpenAI, ICML 2025) — uses a hierarchical author-co-developed rubric with three judge-artifact types (code_development / execution / result_match) and binary leaf grading. Veritas's shape-typed claim enum is a different axis (claim content shape vs. judge artifact).
- **ReplicationBench** (Ye et al., 2025, arXiv:2510.24591) — numerical-comparison benchmark testing AI agents replicating astrophysics papers from scratch.
- **Scaling Reproducibility** (Xu & Yang, 2026) — paper+repo political-science replication; single-scalar (2SLS coefficient) numerical match. Veritas's flexible/agentic approach contrasts with that benchmark's deterministic workflow.

## Working with Claude

- **Commit during implementation; never push.** Run `git add` and `git commit` at each semantic boundary so the git log reflects the change's structure. NEVER run `git push`, `git push --force`, or any remote-modifying command — the user pushes manually after reviewing the branch.
- **Comments and templates document current behavior.** No contributor names, issue/PR numbers, branch names, or planning context in code, comments, or prompt templates.
