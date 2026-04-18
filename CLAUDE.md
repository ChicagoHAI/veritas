# Veritas

Veritas is a replication agent that evaluates whether scientific papers can be reproduced. It runs a 3-phase pipeline: analyze the paper/repo, replicate the methodology inside Docker, then evaluate the results.

## Project Status

Basic implementation is complete, but a significant redesign is in flight based on lab feedback from the v1 output review. Before making changes to replication logic, session instructions, evaluation templates, or output format, check open GitHub issues — they describe a fundamental shift in veritas's direction.

### Direction the lab wants veritas to go

- **Replication is the primary, highlightable output** — not evaluation. Evaluation dimensions (code quality, consistency, etc.) are complementary context; the publishable result is whether veritas reproduced the paper's claims.
- **The agent should actively fix issues and try hard to reproduce results**, not passively observe and report failures. Environment errors, API deprecations, and missing compilers should be fixed so replication can continue. Only give up after genuine effort.
- **Veritas should replicate the paper, not just the repo.** Paper-only mode (no repo) and repo-only mode (no paper, use README as methodology source) are planned. Currently only paper+repo works and repo is mandatory.
- **Results must be structured and numerical**, compared against the paper's claims using tolerance-based matching. This makes output naturally comparable with ReplicationBench (Ye et al., 2025) and Scaling Reproducibility (Xu & Yang, 2026) without requiring a special benchmark mode.
- **The final codebase used during replication should always be preserved as output** — whether it's a patched version of the original repo or code veritas wrote from scratch.
- **Veritas should be highly modularized** — components (execution environment, LLM provider, PDF extractor, scoring method, output format) should be swappable via flags or config, following the pattern of the existing `--no-docker` flag.

Open questions still pending lab input:
- Where is the line between "minor fix" and "too broken"?
- What scope of the paper should replication cover (everything / key results / whatever the methods section describes / user-configurable)?
- What specific components need to be swappable for "modularization"?

## Commands

```bash
# Install (editable, requires uv)
uv pip install -e .

# Build the Docker image for replication
uv run veritas build-image

# Full evaluation (paper + repo, Docker-based)
uv run veritas evaluate --paper paper.pdf --repo ./my-project

# Skip Docker replication
uv run veritas evaluate --paper paper.pdf --repo ./my-project --no-docker

# Select provider (default: claude)
uv run veritas evaluate --repo ./my-project --provider codex

# Select specific evaluation dimensions
uv run veritas evaluate --repo ./my-project --evaluations code,consistency

# Extract plan from paper
uv run veritas extract-plan paper.pdf

# Regenerate report from existing results
uv run veritas report ./evaluation-dir

# Interactive shell inside the replication container
uv run veritas shell ./my-project

# Run tests
uv run pytest
uv run pytest tests/test_runner.py
uv run pytest -k "test_checklist"
```

## Architecture

3-phase pipeline orchestrated by `ReplicationRunner` in `src/veritas/core/runner.py`:

1. **Analyze** (`_analyze`) — generates checklist from paper+repo, then a replication plan
2. **Replicate** (`_replicate`) — runs the plan inside a Docker container via an AI agent; collects execution evidence
3. **Evaluate** (`_evaluate`) — scores the checklist against code and evidence, per category

### Key modules (`src/veritas/core/`)

- `runner.py` — orchestrator; provider invocation (`_invoke_claude/codex/gemini`); JSON repair re-prompt logic
- `config.py` — `Config` dataclass; `VALID_PROVIDERS` and `ALL_EVALUATIONS` constants
- `container.py` — Docker command builder; GPU detection; credential mount logic
- `checklist.py` — `ChecklistItem` / `Checklist` data models and parsing
- `models.py` — `ReplicationPlan`, `ReplicationStep`, `ExecutionEvidence`, `StepOutcome`
- `evidence.py` — parses execution evidence; `_extract_json` / `_fix_json_escapes` repair logic
- `plan_extractor.py` — PDF → plan extraction
- `report_generator.py` — markdown + PDF report generation (pandoc-based)

### Utilities (`src/veritas/utils/`)

- `pdf.py` — PDF text extraction (pdfplumber primary, pypdf fallback)
- `security.py` — API key redaction via regex patterns
- `json_utils.py` — JSON I/O helpers

### Templates (`templates/`)

- `checklist_generation.md` — phase 1: generates the evaluation checklist
- `replication/plan_generation.md` — phase 1: generates the replication plan
- `replication/session_instructions.md` — phase 2: instructions for the agent inside Docker
- `evaluation/scoring.txt` — phase 3: scores checklist items per category

All templates are Jinja2, rendered by `src/veritas/templates/prompt_generator.py`.

### Docker

Multi-stage CUDA 12.5.1 build (`docker/Dockerfile`). Runs as non-root `replicator` user; UID/GID configurable at build time. Credentials are mounted read-only at `/tmp/.{claude,codex,gemini}/` and copied to writable `$HOME` by `docker/entrypoint.sh`.

## Gotchas

- **The replication agent is currently told NOT to modify source code.** This is being redesigned — see open issues before touching `templates/replication/session_instructions.md`. The current behavior is the reason v1 output scores are artificially low.
- **Repo is mounted read-only** in the Docker container. This will change as part of the redesign so the agent can apply fixes.
- **Provider CLI resolution is cross-platform** — `_resolve_cli()` in `runner.py` handles Windows `.cmd` shims via `shutil.which()`. Don't hardcode paths.
- **JSON responses from LLMs are unreliable.** `evidence.py` has multi-strategy extraction (raw → markdown blocks → brace matching) and escape repair. If adding new LLM-parsed fields, route through `_extract_json()`. A planned change to structured JSON output format (`--output-format stream-json`) should eventually reduce the need for this.
- **GPU support is Linux-only** and requires NVIDIA Container Toolkit. The `--gpu` flag is a no-op on Windows/macOS.
- **PDF report generation** requires `pandoc` + `pdflatex` on the host. Use `--no-pdf` if those aren't installed.
- **Gemini provider is undertested and likely broken.** The symlink may be stale after `@google/gemini-cli` restructured in v0.36.0. Prefer Claude or Codex until fixed.
- **Docker file ownership on Linux/macOS** may leave output files owned by the container UID. Known issue — see open issues.

## Testing

Tests are mostly unit tests with mocked provider calls in `tests/`. There are no integration tests that hit real LLMs or Docker.

## Related Work

- **NeuriCo** (formerly idea-explorer, `C:/MyFolders/Research/AI Replication/idea-explorer`) — upstream project veritas adapted architecture and Docker setup from. Multiple improvements in NeuriCo are being ported back; see issues labeled `upstream: neurico`.
- **ReplicationBench** (Ye et al., 2025, arXiv:2510.24591) — primary benchmark veritas aims to be comparable with. Tests AI agents replicating astrophysics papers from scratch. Best current model: Claude 4.5 Sonnet at 22% average.
- **Scaling Reproducibility** (Xu & Yang, 2026) — secondary comparison point. Closer to veritas's paper+repo mode; achieves 94.4% on accessible political science replication packages using a deterministic AI-assisted workflow. The comparison with veritas is flexible/agentic vs. rigid/deterministic.

## Working with Claude

- **Do not commit any code.** The user commits manually. When a change is ready, report: (1) a summary of what changed, (2) a suggested commit message. Do not run `git commit`, `git add`, or `git push`.
