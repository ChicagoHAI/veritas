# Veritas

**A Replication Agent for Evaluating Scientific Reproducibility**

Veritas is an AI-powered tool that evaluates whether scientific papers can be reproduced. Given a paper and repository, it runs a four-phase pipeline — **Analyze**, **Replicate**, **Assess Fixes**, **Verify** — producing a Replication Report with a single tier-weighted Replication Score plus per-claim verdicts, execution evidence, and fix severity ratings.

The replication agent actively fixes issues it encounters (deprecated APIs, missing dependencies, configuration problems) rather than stopping at the first error. Every fix is tracked and rated for severity, so the final report honestly reflects both the results and what it took to get them.

## How It Works

```
Input (Paper PDF + Repository)
        |
  1. ANALYZE
  |  - Extract structured paper claims (paper_claims.json)
  |  - Generate a claim-aware replication plan (replication_plan.json)
        |
  2. REPLICATE
  |  - Execute the plan inside a Docker container on a writable copy of the repo
  |  - An AI agent (Claude/Codex/Gemini) runs the code, actively fixing issues
  |  - Collect execution evidence + structured fix records
        |
  3. ASSESS FIXES
  |  - Rate each applied fix as minor / major / critical
  |  - Assess what each fix implies about reproducibility quality
        |
  4. VERIFY
  |  - One verifier invocation per claim against produced evidence
  |  - Per-claim structured verdict (match / partial / no_match / not_attempted / not_applicable)
  |  - Tier-weighted Replication Score aggregation
        |
  5. REPORT
     - Headline Replication Score with tier breakdown
     - Per-claim verdict table with rationales
     - Replication evidence, fixes-applied section, environment summary
```

Rather than scoring papers on a fixed rubric, Veritas extracts each paper's specific reproducible claims and adjudicates them one at a time against the evidence the replication actually produced. The Replication Score is a tier-weighted fraction (headline = weight 3, supporting = 2, setup = 1) of `match`-or-`partial` verdicts among adjudicated claims.

## Features

- **Active fix-and-continue replication**: The agent patches deprecated APIs, installs missing tools, and adjusts configurations — then reports what it changed
- **Fix severity assessment**: A separate LLM pass rates each fix (minor/major/critical) so results are honest about what it took to reproduce
- **Per-claim verification**: Each paper claim is adjudicated independently against produced evidence (5 typed shapes: scalar, scalar_range, table, qualitative, figure)
- **Replication Score**: A single tier-weighted number summarising whether the paper's claims were reproduced
- **Resumable pipeline**: Completed phases are checkpointed; the verify phase additionally resumes per-claim (a death after claim 17 doesn't redo 1-16)
- **Streaming JSONL transcripts**: Every provider invocation streams a structured event log to disk for post-hoc inspection and debugging
- **Docker-based replication**: Code runs inside a CUDA-enabled container; the original repo stays read-only
- **Multi-provider support**: Works with Claude Code, Codex CLI, and Gemini CLI
- **Scoped extraction**: `--scope main` (default) extracts headline+supporting claims; `--scope full` coming soon
- **Three input modes**: paper+repo, paper-only (code generated from the paper), or repo-only (claims extracted from the README)
- **Cross-platform**: Windows, macOS, and Linux (GPU acceleration on Linux with NVIDIA)

## Installation

You need Docker. Nothing else — no Python, no pandoc, no LaTeX, no provider CLI on the host.

```bash
git clone https://github.com/ChicagoHAI/veritas.git
cd veritas
./veritas setup             # one-shot: prereqs, image, login, .env
./veritas evaluate --paper your_paper.pdf --repo your_repo/
```

On first run, `./veritas` pulls `ghcr.io/chicagohai/veritas:latest` (~3GB) from GitHub Container Registry. Subsequent runs are instant.

Linux/macOS users: after cloning, the shell scripts are already marked executable in the git index. If you somehow ended up with non-executable scripts, run `chmod +x veritas docker/run.sh scripts/test_docker.sh`.

Apple Silicon users: the wrapper automatically passes `--platform linux/amd64` because `nvidia/cuda` base images have no arm64 build. Rosetta emulation handles it.

## Replication API keys (`.env`)

Veritas's own LLM provider (Claude / Codex / Gemini CLI) signs in via OAuth — no host env vars needed. But papers that call LLM APIs from inside their own code (e.g. hypogenic, PaperBench-style runs) need raw keys like `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` in their environment.

Veritas reads keys from a `.env` file at the repo root and passes them into the replication container via `--env-file`:

```bash
cp .env.example .env
chmod 600 .env
$EDITOR .env          # uncomment and set any keys your paper needs

./veritas evaluate --paper paper.pdf --repo ./my-project
```

Or use the interactive UX:

```bash
./veritas setup       # one-shot: prereqs, image, login, .env
./veritas config      # edit .env via masked-input menu later
```

`./veritas status` reports whether `.env` is present. The keys are scoped to the replicate phase only — every other phase gets a stripped subprocess env.

> **Windows note:** `chmod 600` only toggles NTFS's read-only bit on Git Bash; full POSIX owner-only semantics are not available. If you're on a shared Windows host, set the file's NTFS ACL manually.

## Commands

```bash
./veritas evaluate --paper p.pdf --repo ./myrepo    # full pipeline
./veritas evaluate --repo ./myrepo --scope main     # headline+supporting claims (default)
./veritas evaluate --repo ./myrepo --provider codex  # use a different provider
./veritas evaluate --repo ./myrepo --restart        # discard prior state and start fresh
./veritas extract-plan paper.pdf                     # plan only
./veritas report ./evaluation                        # regenerate report
./veritas shell                                      # interactive container
./veritas setup                                      # one-shot prereqs + image + login + .env
./veritas config                                     # edit .env via masked-input menu
./veritas login claude                               # provider OAuth
./veritas status                                     # dashboard
./veritas update                                     # pull latest image
./veritas build                                      # build image locally
./veritas help
```

Run `./veritas evaluate --help` for the full option list.

### Input modes

Veritas supports three input modes (the `--mode` flag, which auto-detects from the supplied inputs by default):

- `--mode full` — paper PDF + repo provided (default when both are supplied).
- `--mode paper-only` — paper PDF only. Veritas writes code from the paper in a new codegen phase, then runs it.
- `--mode repo-only` — repo only. Claims are extracted from the repo's README.

Universal override: `--claims path/to/claims.json` accepts a hand-authored claims file in the same JSON schema as `<output>/analyze/paper_claims.json`; when supplied, automatic claim extraction is skipped.

Note: `--mode` (input mode) is distinct from `--scope` (claim-extraction scope: `main` or `full`).

## Resuming Runs

A full evaluation can run for an hour or more, and Docker crashes, OOM kills, network hiccups, and Ctrl+C all happen. After each phase completes, Veritas writes its status to `<output>/.veritas/pipeline_state.json`. Re-invoking `evaluate` against the same `--output` directory auto-detects that state and skips phases that already completed — analyze, replicate, assess_fixes, and verify are tracked, and the verify phase additionally records per-claim sub-completion so a half-finished verification pass resumes at the next un-verified claim.

Resume is automatic and prints a banner so the skip behavior isn't a surprise. Pass `--restart` to discard the state file and run everything from scratch.

## Claim Types and Tiers

Veritas extracts claims into five shape-typed categories. Each claim also carries a tier that determines its weight in the Replication Score.

| Claim Type | Use for | Verifier behavior |
|-----------|---------|-------------------|
| **scalar** | A single numerical result | Compare to paper_value within tolerance (5% match, 30% partial) |
| **scalar_range** | A numerical range or set | Check range overlap or set containment |
| **table** | A tabular result with rows × cols | Per-cell comparison |
| **qualitative** | A descriptive observation | Paraphrase-match between description and observed behavior |
| **figure** | A paper figure the code produces | Read the figure file (multimodal); structural match against described features |

| Tier | Weight | Use for |
|------|--------|---------|
| **headline** | 3 | The paper's central reproducible result (typically 1-3 per paper) |
| **supporting** | 2 | Intermediate measurements, secondary figures, qualitative observations |
| **setup** | 1 | Borderline configuration assertions (rare) |

## Output Structure

After evaluation, the output directory is organized by pipeline phase:

```
evaluation/
├── analyze/
│   ├── paper_claims.json                    # Structured paper claims
│   ├── replication_plan.json                # Claim-aware replication plan
│   ├── paper_claims_transcript.jsonl
│   └── replication_plan_transcript.jsonl
├── replication/
│   ├── codebase/                            # Patched repo (writable copy with agent's fixes)
│   ├── codebase.diff                        # Unified diff of all changes the agent made
│   ├── replication_log.json                 # Step-by-step execution log with fix records
│   ├── evidence_summary.json
│   └── replication_transcript.jsonl
├── assess/
│   ├── fix_severity.json                    # Fix severity ratings (minor/major/critical)
│   └── fix_severity_transcript.jsonl
├── verify/
│   ├── C1.json                              # Per-claim structured verdict
│   ├── C1_transcript.jsonl                  # Per-claim verifier transcript
│   ├── C2.json
│   ├── ...
│   ├── verdicts.json                        # Aggregated verdicts
│   └── replication_score.json               # Replication Score + tier breakdown
├── report/
│   ├── replication_report.md
│   └── replication_report.pdf
├── prompts/                                 # Debug: rendered prompts sent to the LLM
└── .veritas/
    └── pipeline_state.json                  # Resume checkpoint (schema_version=2)
```

## Docker Container

The replication container is a CUDA 12.5.1 multi-stage build with:
- Python 3.12 (via uv)
- Node.js 22
- Claude Code, Codex CLI, and Gemini CLI pre-installed
- libcudnn8 for deep learning workloads

### GPU Support

GPU passthrough requires the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) and only works on Linux with NVIDIA GPUs. When detected, `./veritas` passes `--gpus all` to the container automatically. On all other platforms (Windows, macOS, Linux without NVIDIA), the container runs in CPU-only mode.

### Credentials

Veritas mounts your AI CLI credential directories (`~/.claude`, `~/.codex`, `~/.gemini`) into the container so the agent can authenticate. Run `./veritas login <provider>` to set up credentials before your first evaluation.

### Environment Variables

API keys consumed by the paper's own code (e.g. `OPENAI_API_KEY`) are loaded from a `.env` file at the repo root and passed in via `--env-file`. See [Replication API keys](#replication-api-keys-env) above for setup details.

## Configuration

### Using with Different Providers

```bash
./veritas evaluate --repo ./project --paper paper.pdf --provider claude   # default
./veritas evaluate --repo ./project --paper paper.pdf --provider codex
./veritas evaluate --repo ./project --paper paper.pdf --provider gemini
```

## Acknowledgments

- Built upon research from [NeuriCo](https://github.com/ChicagoHAI/NeuriCo)
