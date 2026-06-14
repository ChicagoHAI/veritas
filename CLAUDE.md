# Veritas

Veritas is a replication agent that evaluates whether scientific papers can be reproduced. It runs a 6-phase pipeline: analyze the inputs to extract structured claims, optionally generate code from the paper (paper-only mode), plan the replication, replicate the methodology inside Docker (actively fixing issues), assess fix severity, then verify each claim against the produced evidence and emit a tier-weighted Replication Score.

## Project Status

The basic claim-verification pipeline is in place: paper claims extraction in analyze, per-claim adjudication in verify, tier-weighted scoring, and a single-PR-output report. External lab feedback during a subsequent demo cycle may surface gaps; check open GitHub issues before changing pipeline shape or verifier behavior.

### Direction the lab wants veritas to go

- **Replication is the primary, highlightable output.** The Replication Score and per-claim verdicts are the publishable result; replication evidence and fix-severity context are the supporting narrative.
- **The replicate agent should try hard to reproduce results.** Environment errors, API deprecations, and missing compilers should be fixed so replication can continue. Only give up after genuine effort. Every applied fix is logged and rated for severity in a separate pass.
- **Paper claims drive verification.** Each paper's specific reproducible claims are extracted into `paper_claims.json` (5 shape-typed categories: `scalar | scalar_range | table | qualitative | figure`; 2 tiers: `headline | supporting`). The verifier adjudicates each claim independently with a fresh-context LLM call.
- **Replication Score is a tier-weighted fraction**: `score = Σ(tier_weight × verdict_value) / Σ(tier_weight)` with tier weights `3 / 2` for headline / supporting, verdict values `match=1.0, partial=0.5, no_match=0.0, not_attempted=0.0`, and `not_applicable` excluded from both sums.
- **The replicate agent never sees `paper_claims.json`.** The replication plan references claim IDs in a `verifies` field but doesn't embed paper-reported result values. Plan steps' `expected_outcome` is shape-prescriptive (file path, JSON field names, figure layout), not value-prescriptive. This is veritas's structural defense against ground-truth leakage to the replication agent.
- **The final codebase used during replication is preserved as output**: `replication/codebase/` holds the patched copy and `replication/codebase.diff` shows the unified diff vs. the original repo.
- **Veritas is being modularized** — components (execution environment, LLM provider, scoring formula, output format) are progressively being split into swappable modules.

## Commands

```bash
# Install
git clone https://github.com/ChicagoHAI/veritas.git && cd veritas

# Full pipeline (paper + repo)
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

# Per-phase timeouts (default: no timeout)
./veritas replicate --repo ./my-project --analyze-timeout 600 --verify-timeout 300

# Regenerate the report from existing outputs
./veritas report ./replicate-dir

# Interactive shell inside the replication container
./veritas shell

# Build the image locally (usually not needed — first run pulls from GHCR)
./veritas build

# Smoke test the built image
./scripts/test_docker.sh
```

## Architecture

6-phase pipeline orchestrated by `ReplicationRunner` in `src/veritas/core/runner.py`:

1. **Analyze** (`_generate_paper_claims`) — extracts `paper_claims.json`. Source depends on input mode: paper PDF (`full` / `paper-only`), repo README (`repo-only`), or a user-supplied `--claims` JSON (universal override, validated and copied through). Yielding 0 claims raises `_InsufficientSpec` and triggers a dedicated bail report instead of propagating as an error.
2. **Codegen** (`_generate_code`, paper-only mode only) — has the agent write the paper's methodology from scratch into `replication/codebase/`. Sentinel-based resume at `<output>/.veritas/codegen_complete`. Anti-leakage: `paper_claims.json` is intentionally out of this phase's scope.
3. **Plan** (`_generate_replication_plan`) — generates the claim-aware `replication_plan.json` from the effective codebase (the user's repo, or the generated one in paper-only mode). Uses `Config.effective_repo_path`. Plan steps carry a `verifies: List[str]` field referencing claim IDs; a post-plan cross-check (`_validate_plan_claim_refs`) warns on unknown IDs.
4. **Replicate** (`_replicate`) — runs the plan inside a writable copy of the codebase via an AI agent that actively fixes issues; collects execution evidence and fix records. The agent never sees `paper_claims.json`.
5. **Assess Fixes** (`_assess_fixes`) — rates severity of each fix applied during replication (minor/major/critical) via a separate LLM pass. Output: `assess/fix_severity.json`.
6. **Verify** (`_verify_with_resume`) — one provider invocation per claim. Each verifier reads the relevant evidence files and produces a structured verdict at `verify/<claim_id>.json` (status `match | partial | no_match | not_attempted | not_applicable`, type-specific `structured` field, free-text `rationale`, `evidence_refs`). Per-claim resume primitive: file-exists check. Final aggregation writes `verify/verdicts.json` and `verify/replication_score.json`.

Output is organized into per-phase subdirectories: `analyze/`, `replication/` (with `codebase/` and `codebase.diff`), `assess/`, `verify/`, `report/`, and `prompts/`.

### Input modes

Veritas resolves the input mode at startup (auto-detected by default from which of `--paper` / `--repo` were supplied):

- **`full`** — paper PDF + repo. Claims come from the paper; replication runs against the repo.
- **`paper-only`** — paper PDF only. The codegen phase writes the methodology from the paper into a fresh codebase, then the rest of the pipeline runs against that generated codebase.
- **`repo-only`** — repo only. Claims are extracted from the repo's README; codegen is skipped.

`--mode` is the input-mode selector. `--claims path/to/claims.json` is a universal override that skips automatic extraction. `--data path/to/data-dir` mounts a host directory read-only at `/workspace/data/`; the path is surfaced to codegen / plan / replicate prompts via `has_data` so the agent uses these files instead of procuring from the network.

### Key modules (`src/veritas/core/`)

- `runner.py` — orchestrator; provider invocation via `_invoke_provider` (single method using `subprocess.Popen`, stdin for the prompt, line-streamed JSONL transcript to disk, `threading.Timer` watchdog for wall-clock timeouts); JSON repair re-prompt logic; per-provider command/flag tables (`CLI_COMMANDS`, `TRANSCRIPT_FLAGS`, `PERMISSION_FLAGS`, `PROMPT_STDIN_ARGS`).
- `config.py` — `Config` dataclass with output-path properties; `VALID_PROVIDERS`, output-structure constants (`*_SUBDIR`, `*_FILE`), per-phase timeout fields (`analyze_timeout`, `replicate_timeout`, `verify_timeout`).
- `paper_claims.py` — `parse_paper_claims_response()` reading the analyze-phase LLM output.
- `verify.py` — `compute_replication_score()`: pure-function tier-weighted aggregation over a list of `ClaimVerdict`s, returning a `ReplicationScore` with per-tier breakdown, missing-verdict list, and edge-case flags.
- `replication.py` — `parse_replication_plan_response()`, `gather_evidence()`, and `_extract_json` / `_fix_json_escapes` JSON-repair logic.
- `pipeline_state.py` — `PipelineState` class; persists per-phase status to `<output>/.veritas/pipeline_state.json` with a `schema_version` field. Loading a state file with `schema_version < 3` raises a clear error directing the user to `--restart`.
- `models/` — dataclass-only sub-package: `replication.py` (`ReplicationPlan`, `ReplicationStep` with `verifies: List[str]`, `ExecutionEvidence`, `StepOutcome`, `AppliedFix`), `fix_severity.py` (`FixSeverityRating`, `FixSeverityAssessment`), `paper_claims.py` (`PaperClaim`, `PaperClaims`, `ClaimVerdict`, `ReplicationScore`, `Provenance`, `TIER_WEIGHTS`, `VERDICT_VALUES`).
- `report_generator.py` — markdown + PDF report generation (Replication Score headline, tier breakdown, per-claim verdict table, flags, replication evidence, fixes-applied section, environment summary).

### Utilities (`src/veritas/utils/`)

- `security.py` — API key redaction via regex patterns; recursive log sanitization across the output tree.

### Templates (`templates/`)

- `analyze/paper_claims_extraction.md` — analyze phase, first LLM call: extracts structured paper claims.
- `replication/plan_generation.md` — analyze phase, second LLM call: produces a claim-aware plan with `verifies` per step and shape-prescriptive `expected_outcome`.
- `replication/session_instructions.md` — replicate phase: active fix-and-continue instructions for the agent (consumes only the plan; never `paper_claims.json`).
- `assess/fix_severity.md` — assess phase: rates each applied fix as minor/major/critical.
- `verify/single_claim.md` — verify phase: per-claim adjudication template with type-specific guidance (Jinja2 branches per claim type).

All templates are Jinja2, rendered by `src/veritas/templates/prompt_generator.py`.

### Docker

Multi-stage CUDA 12.5.1 build (`docker/Dockerfile`). The image bakes in the veritas Python package (`uv sync --frozen`), Claude/Codex/Gemini CLIs, and pandoc + LaTeX for PDF report generation. Runs as non-root `veritas` user (UID/GID configurable at build time). The `./veritas` bash wrapper (forwarding to `docker/run.sh`) handles host-side concerns: GPU auto-detection, macOS Keychain extraction for Claude credentials, `--platform linux/amd64` on Apple Silicon, path rewriting for `--paper`/`--repo`/`--data`/`--output`, and image pull-from-GHCR with local-build fallback. `docker/entrypoint.sh` sets `umask 000` so container-created files are manageable from the host regardless of UID mismatch.

## Gotchas

- **The replication agent actively fixes issues.** The agent works on a writable copy of the repo at `/workspace/output/replication/codebase/`. It may patch deprecated APIs, install missing tools, and fix configuration issues. Every fix is tracked in `StepOutcome.fixes_applied` and rated for severity by a separate post-replicate LLM pass. The original repo at `/workspace/repo` remains read-only.
- **The user's repo is bind-mounted read-only** at `/workspace/repo` by the wrapper. The entrypoint copies it to `/workspace/output/replication/codebase/` for the agent to modify. An EXIT trap generates a unified diff at `/workspace/output/replication/codebase.diff`.
- **`--data` is mounted read-only at `/workspace/data/`.** Surfaced to codegen / plan / replicate prompts via `has_data`. Agent writes (downloaded auxiliary files) land in `codebase/data/` instead — the two directories don't collide. `data_path` participates in the input fingerprint as a resolved-path string; changing `--data` between runs invalidates downstream phases.
- **The replication agent never sees `paper_claims.json`.** The session prompt is rendered with `replication_plan` only; `expected_outcome` is shape-prescriptive (file paths, field names) rather than value-prescriptive. This is the structural defense against leaking paper-reported result values to the replicator.
- **Verify phase is per-claim with file-exists resume.** A failed verifier call leaves `verify/<claim_id>.json` absent; the next run re-attempts that claim only. State tracks `completed_claims` (not `completed_categories`).
- **Pipeline state `schema_version` is 3.** Old state files (pre-refactor or `< 3`) raise a clear error directing the user to `--restart`. The bump tracks the analyze/plan split, the new codegen phase for paper-only mode, the `insufficient_spec` terminal status, and the top-level `mode` field; silent reuse would mix incompatible artifacts.
- **Provider CLI resolution is cross-platform** — `_resolve_cli()` in `runner.py` handles Windows `.cmd` shims via `shutil.which()`. Don't hardcode paths.
- **Windows Git Bash requires `winpty` for interactive subcommands** — mintty uses Windows pipes instead of Unix ptys, so `docker run -it` fails with "the input device is not a TTY". The top-level `./veritas` wrapper auto-re-execs under `winpty` when detected; if `winpty` is missing, `get_tty_flag` falls back to `-i`-only (scripted use works; interactive sessions like `./veritas shell` and `./veritas login` are degraded). Modern Git for Windows ships with `winpty` by default. Linux and macOS are unaffected.
- **JSON responses from LLMs are unreliable.** `core/replication.py` has multi-strategy extraction (raw → markdown blocks → brace matching) plus escape repair in `_extract_json` / `_fix_json_escapes`. Both `paper_claims.py` and the verifier consumer in `runner.py` route through `_extract_json()`.
- **GPU is auto-detected and Linux-only** (requires NVIDIA Container Toolkit).
- **Two runtimes: docker (`./veritas`) and host (`./veritas-host`).** Docker is the default; the wrapper manages image lifecycle (pull from GHCR on first run, build locally if pull fails). Host mode is for environments without docker (HPC clusters); the user provides claude/codex/gemini CLI, python, and uv on PATH, and `veritas-host` does the workspace pre-staging that `docker/entrypoint.sh` does in docker mode (`templates/skills/` → `<output>/veritas-skills/`, `--repo` → `<output>/replication/codebase/`, EXIT-trap codebase.diff). Both runtimes share the Python pipeline; the two `/workspace/`-derived paths in templates (skills catalog, agent venv) are parameterized via `VERITAS_SKILLS_DIR` and `VERITAS_VENV_DIR` env vars with docker-mode defaults.
- **Image contains the whole runtime.** Changes to `src/`, `templates/`, `pyproject.toml`, or `uv.lock` require a rebuild (`./veritas build`) or an update from GHCR (`./veritas update`). The CI workflow rebuilds automatically on main-branch pushes.
- **GPU two-step auto-detect.** `docker/run.sh::get_gpu_flags` checks both that the NVIDIA Container Toolkit is installed (`docker info | grep nvidia`) AND that a GPU is actually reachable (`docker run --gpus all ... nvidia-smi`). The second probe catches WSL and emulated environments where the toolkit is present but no GPU adapter is accessible. If the veritas image isn't built yet, the probe is skipped.
- **Replication API keys live in `$PROJECT_ROOT/.env`** (chmod 600, gitignored). Passed into the container via `--env-file` on `cmd_replicate` / `cmd_shell` only. The wrapper publishes the var-name list as `VERITAS_ENV_FILE_KEYS`; `runner.py::_invoke_provider` strips those vars from the subprocess env by default, and only the `_replicate` call site opts in via `expose_api_keys=True`. So analyze/plan/codegen/assess/verify agents never see the keys, but the paper code run during replicate does. `./veritas setup` and `./veritas config` subcommands manage the file.

## Testing

The Python test suite was removed during the flat-Docker refactor (issue #27). Current coverage consists of:

- **`scripts/test_docker.sh`** — asserts the built image has functional claude/codex/gemini/pandoc/pdflatex/python/veritas and that the entrypoint banner prints. Run locally with `./scripts/test_docker.sh <image-tag>`; CI runs it automatically against the pushed image in `.github/workflows/docker-publish.yml`.

Python unit tests for `defaults`/`settings`/`paths`/`providers`/`evidence`/`verify` will be re-introduced once the current redesign stabilizes.

## Related Work

- **NeuriCo** (formerly idea-explorer, `C:/MyFolders/Research/AI Replication/idea-explorer`) — upstream project veritas adapted architecture and Docker setup from. Multiple improvements in NeuriCo are being ported back; see issues labeled `upstream: neurico`.
- **PaperBench** (OpenAI, ICML 2025) — uses a hierarchical author-co-developed rubric with three judge-artifact types (code_development / execution / result_match) and binary leaf grading. Veritas's shape-typed claim enum is a different axis (claim content shape vs. judge artifact).
- **ReplicationBench** (Ye et al., 2025, arXiv:2510.24591) — primary numerical-comparison benchmark veritas aims to be comparable with. Tests AI agents replicating astrophysics papers from scratch.
- **Scaling Reproducibility** (Xu & Yang, 2026) — paper+repo political-science replication; single-scalar (2SLS coefficient) numerical match. Veritas's flexible/agentic approach contrasts with that benchmark's deterministic workflow.

## Working with Claude

- **Commit during implementation; never push.** Run `git add` and `git commit` at each semantic boundary so the git log reflects the change's structure. NEVER run `git push`, `git push --force`, or any remote-modifying command — the user pushes manually after reviewing the branch.
