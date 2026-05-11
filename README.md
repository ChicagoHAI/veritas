# Veritas

**A Replication Agent for Evaluating Scientific Reproducibility**

Veritas is an AI-powered tool that evaluates whether scientific papers can be reproduced. Given a paper and repository, it runs a four-phase pipeline — **Analyze**, **Replicate**, **Assess Fixes**, **Evaluate** — producing replication reports with execution evidence, fix severity ratings, and PASS/FAIL checklists.

The replication agent actively fixes issues it encounters (deprecated APIs, missing dependencies, configuration problems) rather than stopping at the first error. Every fix is tracked and rated for severity, so the final report honestly reflects both the results and what it took to get them.

## How It Works

```
Input (Paper PDF + Repository)
        |
  1. ANALYZE
  |  - Generate a checklist of verification items tailored to the paper
  |  - Generate a scoped replication plan (steps, commands, expected outcomes)
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
  4. EVALUATE
  |  - Score checklist items against evidence, with fix severity as context
  |  - Fixes are context, not automatic penalties
        |
  5. REPORT
     - Aggregate into markdown + PDF with a Fixes Applied section
```

Rather than using a fixed set of evaluation criteria, Veritas generates a checklist dynamically for each paper. An LLM reads the paper and repository, then produces targeted verification items based on the specific claims, methods, datasets, and statistical analyses present in that work. For more details on the AutoChecklist methodology, see [ChicagoHAI/AutoChecklist](https://github.com/ChicagoHAI/AutoChecklist).

## Features

- **Active fix-and-continue replication**: The agent patches deprecated APIs, installs missing tools, and adjusts configurations — then reports what it changed
- **Fix severity assessment**: A separate LLM pass rates each fix (minor/major/critical) so results are honest about what it took to reproduce
- **Dynamic checklists**: LLM-generated verification items tailored to each paper
- **Resumable pipeline**: Completed phases are checkpointed; a re-run after a crash, OOM, or Ctrl+C picks up where it left off instead of restarting from scratch
- **Streaming JSONL transcripts**: Every provider invocation streams a structured event log to disk for post-hoc inspection and debugging
- **Docker-based replication**: Code runs inside a CUDA-enabled container; the original repo stays read-only
- **Multi-provider support**: Works with Claude Code, Codex CLI, and Gemini CLI
- **Scoped replication**: `--mode main` (default) targets key claims; `--mode full` coming soon
- **Five evaluation dimensions**: Code quality, consistency, generalization, replication, instruction following
- **Cross-platform**: Windows, macOS, and Linux (GPU acceleration on Linux with NVIDIA)

## Installation

You need Docker. Nothing else — no Python, no pandoc, no LaTeX, no provider CLI on the host.

```bash
git clone https://github.com/ChicagoHAI/veritas.git
cd veritas
./veritas login claude      # one-time OAuth sign-in
./veritas evaluate --paper your_paper.pdf --repo your_repo/
```

On first run, `./veritas` pulls `ghcr.io/chicagohai/veritas:latest` (~3GB) from GitHub Container Registry. Subsequent runs are instant.

Linux/macOS users: after cloning, the shell scripts are already marked executable in the git index. If you somehow ended up with non-executable scripts, run `chmod +x veritas docker/run.sh scripts/test_docker.sh`.

Apple Silicon users: the wrapper automatically passes `--platform linux/amd64` because `nvidia/cuda` base images have no arm64 build. Rosetta emulation handles it.

## Commands

```bash
./veritas evaluate --paper p.pdf --repo ./myrepo    # full pipeline
./veritas evaluate --repo ./myrepo --mode main      # key claims only (default)
./veritas evaluate --repo ./myrepo --provider codex  # use a different provider
./veritas evaluate --repo ./myrepo --restart        # discard prior state and start fresh
./veritas extract-plan paper.pdf                     # plan only
./veritas report ./evaluation                        # regenerate report
./veritas shell                                      # interactive container
./veritas login claude                               # provider OAuth
./veritas status                                     # dashboard
./veritas update                                     # pull latest image
./veritas build                                      # build image locally
./veritas help
```

Run `./veritas evaluate --help` for the full option list.

## Resuming Runs

A full evaluation can run for an hour or more, and Docker crashes, OOM kills, network hiccups, and Ctrl+C all happen. After each phase completes, Veritas writes its status to `<output>/.veritas/pipeline_state.json`. Re-invoking `evaluate` against the same `--output` directory auto-detects that state and skips phases that already completed — analyze, replicate, assess-fixes, and evaluate are tracked atomically, and the evaluate phase additionally records per-category sub-completion so a half-finished scoring pass resumes where it left off.

Resume is automatic and prints a banner so the skip behavior isn't a surprise. Pass `--restart` to discard the state file and run everything from scratch.

## Evaluation Dimensions

Veritas evaluates across five dimensions. The specific checklist items within each dimension are generated dynamically based on the paper's content.

| Dimension | What it assesses |
|-----------|-----------------|
| **Code Quality** | Does the code run? Is it correct, non-redundant, and relevant? |
| **Consistency** | Do results match conclusions? Does implementation match the plan? |
| **Generalization** | Do findings hold across different models, data, or methods? |
| **Replication** | Can the work be reproduced from documentation? Are results deterministic? |
| **Instruction Following** | Does the implementation serve the stated objectives? |

## Output Structure

After evaluation, the output directory is organized by pipeline phase:

```
evaluation/
├── analyze/
│   ├── checklist.json                       # Generated verification checklist
│   ├── replication_plan.json                # Scoped replication plan
│   ├── checklist_transcript.jsonl           # Streamed agent transcript (checklist gen)
│   └── replication_plan_transcript.jsonl    # Streamed agent transcript (plan gen)
├── replication/
│   ├── codebase/                            # Patched repo (writable copy with agent's fixes)
│   ├── codebase.diff                        # Unified diff of all changes the agent made
│   ├── replication_log.json                 # Step-by-step execution log with fix records
│   ├── evidence_summary.json                # Environment and execution evidence
│   └── replication_transcript.jsonl         # Streamed agent transcript (replicate phase)
├── evaluate/
│   ├── fix_severity.json                    # Fix severity ratings (minor/major/critical)
│   ├── fix_severity_transcript.jsonl
│   ├── code_evaluation.json                 # Code quality scores
│   ├── code_transcript.jsonl
│   ├── consistency_evaluation.json
│   ├── consistency_transcript.jsonl
│   ├── generalization_evaluation.json
│   ├── generalization_transcript.jsonl
│   ├── replication_evaluation.json
│   ├── replication_transcript.jsonl
│   ├── instruction_following_evaluation.json
│   └── instruction_following_transcript.jsonl
├── report/
│   ├── replication_report.md                # Final markdown report
│   └── replication_report.pdf               # Final PDF report
├── prompts/                                 # Debug: rendered prompts sent to the LLM
└── .veritas/
    └── pipeline_state.json                  # Resume checkpoint (per-phase status)
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

API keys and environment variables can be provided via `~/.veritas/.env`:

```bash
mkdir -p ~/.veritas
echo "ANTHROPIC_API_KEY=sk-..." > ~/.veritas/.env
```

## Configuration

### Using with Different Providers

```bash
./veritas evaluate --repo ./project --paper paper.pdf --provider claude   # default
./veritas evaluate --repo ./project --paper paper.pdf --provider codex
./veritas evaluate --repo ./project --paper paper.pdf --provider gemini
```

### Running Specific Evaluations

```bash
# Only code and consistency
./veritas evaluate --repo ./project -e code,consistency

# Only replication
./veritas evaluate --repo ./project -e replication
```

## Acknowledgments

- Built upon research from [NeuriCo](https://github.com/ChicagoHAI/NeuriCo)
- Evaluation criteria adapted from [eval_agent](https://github.com/ChicagoHAI/eval_agent)
