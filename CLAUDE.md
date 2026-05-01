# Veritas

Veritas is a replication agent that evaluates whether scientific papers can be reproduced. It runs a 4-phase pipeline: analyze the paper/repo, replicate the methodology inside Docker (actively fixing issues), assess fix severity, then evaluate the results.

## Project Status

Basic implementation is complete, but a significant redesign is in flight based on lab feedback from the v1 output review. Before making changes to replication logic, session instructions, evaluation templates, or output format, check open GitHub issues — they describe a fundamental shift in veritas's direction.

### Direction the lab wants veritas to go

- **Replication is the primary, highlightable output** — not evaluation. Evaluation dimensions (code quality, consistency, etc.) are complementary context; the publishable result is whether veritas reproduced the paper's claims.
- **The agent should actively fix issues and try hard to reproduce results**, not passively observe and report failures. Environment errors, API deprecations, and missing compilers should be fixed so replication can continue. Only give up after genuine effort.
- **Veritas should replicate the paper, not just the repo.** Paper-only mode (no repo) and repo-only mode (no paper, use README as methodology source) are planned. Currently only paper+repo works and repo is mandatory.
- **Results must be structured and numerical**, compared against the paper's claims using tolerance-based matching. This makes output naturally comparable with ReplicationBench (Ye et al., 2025) and Scaling Reproducibility (Xu & Yang, 2026) without requiring a special benchmark mode.
- **The final codebase used during replication should always be preserved as output** — whether it's a patched version of the original repo or code veritas wrote from scratch.
- **Veritas should be highly modularized** — components (execution environment, LLM provider, PDF extractor, scoring method, output format) should be swappable via flags or config.

Open questions still pending lab input:
- What specific components need to be swappable for "modularization"?

## Commands

```bash
# Install
git clone https://github.com/ChicagoHAI/veritas.git && cd veritas

# Full evaluation (paper + repo)
./veritas evaluate --paper paper.pdf --repo ./my-project

# Select provider
./veritas evaluate --repo ./my-project --provider codex

# Select replication scope
./veritas evaluate --repo ./my-project --mode main  # key claims (default)

# Select specific evaluation dimensions
./veritas evaluate --repo ./my-project --evaluations code,consistency

# Extract plan from paper
./veritas extract-plan paper.pdf

# Regenerate report from existing results
./veritas report ./evaluation-dir

# Interactive shell inside the replication container
./veritas shell

# Build the image locally (usually not needed — first run pulls from GHCR)
./veritas build

# Smoke test the built image
./scripts/test_docker.sh
```

## Architecture

4-phase pipeline orchestrated by `ReplicationRunner` in `src/veritas/core/runner.py`:

1. **Analyze** (`_analyze`) — generates checklist from paper+repo, then a scoped replication plan
2. **Replicate** (`_replicate`) — runs the plan via an AI agent that actively fixes issues; collects execution evidence and fix records
3. **Assess Fixes** (`_assess_fixes`) — rates severity of each fix applied during replication (minor/major/critical)
4. **Evaluate** (`_evaluate_with_resume`) — scores the checklist against code and evidence, with fix severity as context; supports per-category resume

Output is organized into phase subdirectories: `analyze/`, `replication/` (includes `codebase/` with the patched repo and `codebase.diff`), `evaluate/`, `report/`, and `prompts/`.

### Key modules (`src/veritas/core/`)

- `runner.py` — orchestrator; provider invocation (`_invoke_claude/codex/gemini`); JSON repair re-prompt logic
- `config.py` — `Config` dataclass with output-path properties; `VALID_PROVIDERS`, `ALL_EVALUATIONS`, and output-structure constants (`*_SUBDIR`, `*_FILE`)
- `checklist.py` — `parse_checklist_response()`
- `replication.py` — `parse_replication_plan_response()`, `gather_evidence()`, and `_extract_json` / `_fix_json_escapes` repair logic
- `pipeline_state.py` — `PipelineState` class; persists per-phase status to `<output>/.veritas/pipeline_state.json` so failed runs resume from the last completed phase
- `models/` — dataclass-only sub-package: `replication.py` (`ReplicationPlan`, `ReplicationStep`, `ExecutionEvidence`, `StepOutcome`, `AppliedFix`), `fix_severity.py` (`FixSeverityRating`, `FixSeverityAssessment`), `checklist.py` (`Checklist`, `ChecklistItem`)
- `plan_extractor.py` — PDF → plan extraction
- `report_generator.py` — markdown + PDF report generation (pandoc-based)

### Utilities (`src/veritas/utils/`)

- `pdf.py` — PDF text extraction (pdfplumber primary, pypdf fallback)
- `security.py` — API key redaction via regex patterns
- `json_utils.py` — JSON I/O helpers

### Templates (`templates/`)

- `checklist_generation.md` — phase 1: generates the evaluation checklist
- `replication/plan_generation.md` — phase 1: generates the scoped replication plan
- `replication/session_instructions.md` — phase 2: active fix-and-continue instructions for the agent
- `evaluation/fix_severity.md` — phase 3: rates each applied fix as minor/major/critical
- `evaluation/scoring.txt` — phase 4: scores checklist items per category (fix-aware)

All templates are Jinja2, rendered by `src/veritas/templates/prompt_generator.py`.

### Docker

Multi-stage CUDA 12.5.1 build (`docker/Dockerfile`). The image bakes in the veritas Python package (`uv sync --frozen`), Claude/Codex/Gemini CLIs, and pandoc + LaTeX for PDF report generation. Runs as non-root `veritas` user (UID/GID configurable at build time). The `./veritas` bash wrapper (forwarding to `docker/run.sh`) handles host-side concerns: GPU auto-detection, macOS Keychain extraction for Claude credentials, `--platform linux/amd64` on Apple Silicon, path rewriting for `--paper`/`--repo`/`--output`, and image pull-from-GHCR with local-build fallback. `docker/entrypoint.sh` sets `umask 000` so container-created files are manageable from the host regardless of UID mismatch.

## Gotchas

- **The replication agent actively fixes issues.** The agent works on a writable copy of the repo at `/workspace/output/replication/codebase/`. It may patch deprecated APIs, install missing tools, and fix configuration issues. Every fix is tracked in `StepOutcome.fixes_applied` and rated for severity by a separate post-replicate LLM pass. The original repo at `/workspace/repo` remains read-only.
- **The user's repo is bind-mounted read-only** at `/workspace/repo` by the wrapper. The entrypoint copies it to `/workspace/output/replication/codebase/` for the agent to modify. An EXIT trap generates a unified diff at `/workspace/output/replication/codebase.diff`.
- **Provider CLI resolution is cross-platform** — `_resolve_cli()` in `runner.py` handles Windows `.cmd` shims via `shutil.which()`. Don't hardcode paths.
- **Windows Git Bash requires `winpty` for interactive subcommands** — mintty uses Windows pipes instead of Unix ptys, so `docker run -it` fails with "the input device is not a TTY". The top-level `./veritas` wrapper auto-re-execs under `winpty` when detected; if `winpty` is missing, `get_tty_flag` falls back to `-i`-only (scripted use works; interactive sessions like `./veritas shell` and `./veritas login` are degraded). Modern Git for Windows ships with `winpty` by default. Linux and macOS are unaffected.
- **JSON responses from LLMs are unreliable.** `evidence.py` has multi-strategy extraction (raw → markdown blocks → brace matching) and escape repair. If adding new LLM-parsed fields, route through `_extract_json()`. A planned change to structured JSON output format (`--output-format stream-json`) should eventually reduce the need for this.
- **GPU is auto-detected and Linux-only** (requires NVIDIA Container Toolkit).
- **Docker is mandatory.** There is no host-side fallback. The `./veritas` wrapper manages the image lifecycle (pull from GHCR on first run, build locally if pull fails).
- **Image contains the whole runtime.** Changes to `src/`, `templates/`, `pyproject.toml`, or `uv.lock` require a rebuild (`./veritas build`) or an update from GHCR (`./veritas update`). The CI workflow rebuilds automatically on main-branch pushes.
- **GPU two-step auto-detect.** `docker/run.sh::get_gpu_flags` checks both that the NVIDIA Container Toolkit is installed (`docker info | grep nvidia`) AND that a GPU is actually reachable (`docker run --gpus all ... nvidia-smi`). The second probe catches WSL and emulated environments where the toolkit is present but no GPU adapter is accessible. If the veritas image isn't built yet, the probe is skipped (we trust the toolkit check alone rather than pulling a 2GB probe image just to decide about a flag).

## Testing

The Python test suite was removed during the flat-Docker refactor (see issue #27). Current coverage consists of:

- **`scripts/test_docker.sh`** — asserts the built image has functional claude/codex/gemini/pandoc/pdflatex/python/veritas and that the entrypoint banner prints. Run locally with `./scripts/test_docker.sh <image-tag>`; CI runs it automatically against the pushed image in `.github/workflows/docker-publish.yml`.

Python unit tests for `defaults`/`settings`/`paths`/`providers`/`evidence` will be re-introduced under issue #27 after the current redesign stabilizes.

## Related Work

- **NeuriCo** (formerly idea-explorer, `C:/MyFolders/Research/AI Replication/idea-explorer`) — upstream project veritas adapted architecture and Docker setup from. Multiple improvements in NeuriCo are being ported back; see issues labeled `upstream: neurico`.
- **ReplicationBench** (Ye et al., 2025, arXiv:2510.24591) — primary benchmark veritas aims to be comparable with. Tests AI agents replicating astrophysics papers from scratch. Best current model: Claude 4.5 Sonnet at 22% average.
- **Scaling Reproducibility** (Xu & Yang, 2026) — secondary comparison point. Closer to veritas's paper+repo mode; achieves 94.4% on accessible political science replication packages using a deterministic AI-assisted workflow. The comparison with veritas is flexible/agentic vs. rigid/deterministic.

## Working with Claude

- **Do not commit any code.** The user commits manually. When a change is ready, report: (1) a summary of what changed, (2) a suggested commit message. Do not run `git commit`, `git add`, or `git push`.
