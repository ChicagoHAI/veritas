# Veritas

**A Replication Agent for Evaluating Scientific Reproducibility**

Veritas is an AI-powered tool that evaluates the replicability of scientific research projects. Given a paper and repository, it runs a three-phase pipeline — **Analyze**, **Replicate**, **Evaluate** — producing comprehensive replication reports with PASS/FAIL checklists and execution evidence.

## How It Works

```
Input (Paper PDF + Repository)
        |
  1. ANALYZE
  |  - Generate a checklist of verification items tailored to the paper
  |  - Generate a structured replication plan (steps, commands, expected outcomes)
        |
  2. REPLICATE
  |  - Execute the replication plan inside a Docker container
  |  - An AI agent (Claude/Codex/Gemini) runs the code, collects output
  |  - Gather execution evidence (exit codes, stdout, produced files)
        |
  3. EVALUATE
  |  - Score each checklist item against the code and execution evidence
  |  - Produce PASS/FAIL verdicts with rationale
        |
  4. REPORT
     - Aggregate results into a markdown + PDF report
```

Rather than using a fixed set of evaluation criteria, Veritas generates a checklist dynamically for each paper. An LLM reads the paper and repository, then produces targeted verification items based on the specific claims, methods, datasets, and statistical analyses present in that work. For more details on the AutoChecklist methodology, see [ChicagoHAI/AutoChecklist](https://github.com/ChicagoHAI/AutoChecklist).

## Features

- **Three-phase pipeline**: Analyze, Replicate, Evaluate with execution evidence
- **Dynamic checklists**: LLM-generated verification items tailored to each paper
- **Docker-based replication**: Code runs inside a CUDA-enabled container with the AI agent
- **Multi-provider support**: Works with Claude Code, Codex CLI, and Gemini CLI
- **Five evaluation dimensions**: Code quality, consistency, generalization, replication, instruction following
- **Cross-platform**: Runs on Windows, macOS, and Linux (GPU acceleration on Linux with NVIDIA)

## Installation

```bash
# Clone the repository
git clone https://github.com/ChicagoHAI/veritas.git
cd veritas

# Install with uv (recommended)
uv venv --python 3.12
uv pip install -e .

# Or with pip
pip install -e .
```

### Prerequisites

- Python 3.10+
- Docker (for the replication phase)
- One of the following AI CLI tools:
  - [Claude Code](https://claude.com/claude-code) (recommended)
  - [Codex CLI](https://github.com/openai/codex)
  - [Gemini CLI](https://ai.google.dev/gemini-api)

For PDF report generation:
- `pandoc` and `pdflatex` (optional)

## Quick Start

### 1. Build the Docker image

```bash
veritas build-image
```

### 2. Run an evaluation

```bash
veritas evaluate --paper paper.pdf --repo ./my-project
```

This runs the full pipeline: generates a checklist from the paper, replicates the project inside Docker, scores the results, and writes a report to `./my-project/evaluation/`.

### Evaluate without Docker (code analysis only)

```bash
veritas evaluate --paper paper.pdf --repo ./my-project --no-docker
```

This skips the replication phase and evaluates by reading the code only.

## Command Reference

### `veritas evaluate`

Main evaluation command. Runs the full three-phase pipeline.

```
veritas evaluate [OPTIONS]

Options:
  -p, --paper PATH               Path to the paper PDF file
  -r, --repo PATH                Path to the repository to evaluate (required)
  --plan PATH                    Path to an existing plan file
  -o, --output PATH              Output directory (default: repo/evaluation)
  --provider TEXT                AI provider: claude, codex, gemini [default: claude]
  --pdf / --no-pdf               Generate PDF report [default: pdf]
  -e, --evaluations TEXT         Comma-separated evaluations to run
                                 Options: code,consistency,generalization,
                                 replication,instruction_following
  -t, --timeout INT              Timeout per evaluation in seconds [default: 3600]
  --no-docker                    Skip replication phase, evaluate by reading code only
  --docker-image TEXT            Docker image name [default: veritas-replicator:latest]
  --replication-timeout INT      Timeout for replication phase [default: 3600]
  --gpu / --no-gpu               Enable GPU passthrough for Docker [default: no-gpu]
```

### `veritas build-image`

Build the Docker image for replication.

```
veritas build-image [OPTIONS]

Options:
  --tag TEXT    Image tag [default: veritas-replicator:latest]
```

### `veritas extract-plan`

Extract a structured plan from a paper PDF.

```
veritas extract-plan PAPER [OPTIONS]

Options:
  -o, --output PATH      Output path for the plan file
  --with-evidence        Include evidence quotes from the paper
```

### `veritas report`

Generate a report from existing evaluation results.

```
veritas report EVALUATION_DIR [OPTIONS]

Options:
  -o, --output PATH      Output path for the report
  -f, --format TEXT      Output format: md, pdf, or all [default: all]
```

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

After evaluation, the output directory contains:

```
evaluation/
├── checklist.json               # Generated verification checklist
├── replication_plan.json        # Structured replication plan
├── replication/
│   ├── execution_stdout.log     # Container execution log (sanitized)
│   └── evidence.json            # Collected execution evidence
├── code_evaluation.json         # Code quality scores
├── consistency_evaluation.json  # Consistency scores
├── generalization_evaluation.json
├── replication_evaluation.json
├── instruction_following_evaluation.json
├── replication_report.md        # Final markdown report
└── replication_report.pdf       # Final PDF report
```

## Docker Container

The replication container is a CUDA 12.5.1 multi-stage build with:
- Python 3.12 (via uv)
- Node.js 22
- Claude Code, Codex CLI, and Gemini CLI pre-installed
- libcudnn8 for deep learning workloads

### GPU Support

GPU passthrough requires the [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/install-guide.html) and only works on Linux with NVIDIA GPUs. On all other platforms (Windows, macOS, Linux without NVIDIA), the container runs in CPU-only mode.

To enable GPU:

```bash
veritas evaluate --repo ./project --paper paper.pdf --gpu
```

### Credentials

Veritas mounts your AI CLI credential files (read-only) into the container so the agent can authenticate. It mounts only the minimal auth files needed:

| CLI | File mounted |
|-----|-------------|
| Claude | `~/.claude/.credentials.json` |
| Codex | `~/.codex/auth.json` |
| Gemini | `~/.gemini/oauth_creds.json`, `~/.gemini/google_accounts.json` |

### Environment Variables

API keys and environment variables can be provided via `~/.veritas/.env`:

```bash
mkdir -p ~/.veritas
echo "ANTHROPIC_API_KEY=sk-..." > ~/.veritas/.env
```

## Configuration

### Using with Different Providers

```bash
# Use Claude (default)
veritas evaluate --repo ./project --paper paper.pdf --provider claude

# Use Codex
veritas evaluate --repo ./project --paper paper.pdf --provider codex

# Use Gemini
veritas evaluate --repo ./project --paper paper.pdf --provider gemini
```

### Running Specific Evaluations

```bash
# Only code and consistency
veritas evaluate --repo ./project -e code,consistency

# Only replication
veritas evaluate --repo ./project -e replication
```

## Acknowledgments

- Built upon research from the [idea-explorer](https://github.com/ChicagoHAI/idea-explorer) project
- Evaluation criteria adapted from [eval_agent](https://github.com/ChicagoHAI/eval_agent)
