# Veritas

**A Replication Agent for Evaluating Scientific Reproducibility**

Veritas is an AI-powered tool that evaluates the replicability of scientific research projects. Given a paper and/or repository, it produces comprehensive replication reports assessing code quality, consistency, generalizability, and reproducibility.

## Features

- **Multi-dimensional Evaluation**: Assesses projects across 5 key dimensions:
  - **Code Quality** (C1-C4): Runnability, correctness, redundancy, relevance
  - **Consistency** (CS1-CS5): Documentation-code alignment, statistical rigor
  - **Generalization** (GT1-GT3): Model, data, and method generalization
  - **Replicability** (RP1-RP3): Reconstructability, environment reproducibility, determinism
  - **Instruction Following** (TS1-TS4): Goal alignment and plan adherence

- **Flexible Input**: Accepts papers (PDF), repositories, and/or existing plans
- **Comprehensive Reports**: Generates detailed markdown reports with PDF export
- **Multi-Provider Support**: Works with Claude, Codex, and Gemini CLI tools
- **Binary Checklists**: Clear PASS/FAIL assessments for each criterion

## Installation

```bash
# Clone the repository
git clone https://github.com/your-org/veritas.git
cd veritas

# Install with uv (recommended)
uv pip install -e .

# Or with pip
pip install -e .
```

### Prerequisites

- Python 3.10+
- One of the following AI CLI tools:
  - [Claude Code](https://claude.com/claude-code) (recommended)
  - [Codex CLI](https://github.com/openai/codex)
  - [Gemini CLI](https://ai.google.dev/gemini-api)

For PDF generation:
- `pandoc` and `pdflatex` (optional, for PDF reports)

## Quick Start

### Basic Usage

Evaluate a repository with its paper:

```bash
veritas evaluate --paper paper.pdf --repo ./my-project
```

Evaluate a repository only:

```bash
veritas evaluate --repo ./my-project
```

### Extract Plan from Paper

```bash
veritas extract-plan paper.pdf --output plan.md --with-evidence
```

### Generate Report from Existing Evaluations

```bash
veritas report ./my-project/evaluation --format all
```

## Command Reference

### `veritas evaluate`

Main evaluation command.

```bash
veritas evaluate [OPTIONS]

Options:
  -p, --paper PATH       Path to the paper PDF file
  -r, --repo PATH        Path to the repository to evaluate (required)
  --plan PATH            Path to an existing plan file
  -o, --output PATH      Output directory for the report
  --provider TEXT        AI provider: claude, codex, gemini [default: claude]
  --pdf / --no-pdf       Generate PDF report [default: pdf]
  -e, --evaluations TEXT Comma-separated evaluations to run
                         Options: code,consistency,generalization,replication,instruction
  -t, --timeout INT      Timeout per evaluation in seconds [default: 3600]
```

### `veritas extract-plan`

Extract a structured plan from a paper.

```bash
veritas extract-plan PAPER [OPTIONS]

Options:
  -o, --output PATH      Output path for the plan file
  --with-evidence        Include evidence quotes from the paper
```

### `veritas report`

Generate a report from evaluation results.

```bash
veritas report EVALUATION_DIR [OPTIONS]

Options:
  -o, --output PATH      Output path for the report
  -f, --format TEXT      Output format: md, pdf, or all [default: all]
```

## Evaluation Criteria

### Code Quality (C1-C4)

| ID | Criterion | Description |
|----|-----------|-------------|
| C1 | Runnable | All core analysis code executes without errors |
| C2 | Correct | Implementations match their described behavior |
| C3 | Non-redundant | No duplicate computations |
| C4 | Relevant | All code contributes to project goals |

### Consistency (CS1-CS5)

| ID | Criterion | Description |
|----|-----------|-------------|
| CS1 | Results-Conclusion | Conclusions match actual code outputs |
| CS2 | Plan-Implementation | Implementation reflects planned steps |
| CS3 | Effect Size | Effects are non-trivial |
| CS4 | Justification | Key choices are explained |
| CS5 | Statistical Rigor | Uncertainty measures are reported |

### Generalization (GT1-GT3)

| ID | Criterion | Description |
|----|-----------|-------------|
| GT1 | Model | Findings hold on new models |
| GT2 | Data | Findings hold on new data |
| GT3 | Method | Proposed methods work on similar tasks |

### Replicability (RP1-RP3)

| ID | Criterion | Description |
|----|-----------|-------------|
| RP1 | Reconstructable | Can reimplement from documentation |
| RP2 | Environment | Dependencies are reproducible |
| RP3 | Deterministic | Results are stable across runs |

### Instruction Following (TS1-TS4)

| ID | Criterion | Description |
|----|-----------|-------------|
| TS1 | Goal Alignment | Implementation serves stated objective |
| TS2 | Plan Adherence | All plan steps are implemented |
| TS3 | Hypothesis Coverage | All hypotheses are tested |
| TS4 | Component Matching | Functions do what docs claim |

## Output Structure

After evaluation, the output directory contains:

```
evaluation/
├── code_evaluation.json          # Code quality results
├── consistency_evaluation.json   # Consistency results
├── generalization_evaluation.json # Generalization results
├── replication_evaluation.json   # Replicability results
├── instruction_evaluation.json   # Instruction following results
├── replications/
│   ├── replication_notes.md      # Replication attempt notes
│   └── results_comparison.json   # Results comparison
├── replication_report.md         # Final markdown report
└── replication_report.pdf        # Final PDF report
```

## Example Report

```
# Replication Report

**Overall Replicability Score: 73.3%** (11/15 checks passed)

⚠️ **Moderate Replicability** - Some areas need improvement.

| Evaluation | Status | Passed | Total |
|------------|--------|--------|-------|
| Code Quality | ✅ | 4 | 4 |
| Consistency | ⚠️ | 3 | 5 |
| Generalization | ⚠️ | 2 | 3 |
| Replicability | ⚠️ | 2 | 3 |

## Recommendations

1. Add statistical significance tests
2. Document environment setup with exact versions
3. Set random seeds for reproducibility
```

## Configuration

### Using with Different Providers

```bash
# Use Claude (default)
veritas evaluate --repo ./project --provider claude

# Use Codex
veritas evaluate --repo ./project --provider codex

# Use Gemini
veritas evaluate --repo ./project --provider gemini
```

### Running Specific Evaluations

```bash
# Only code and consistency
veritas evaluate --repo ./project -e code,consistency

# Only replication
veritas evaluate --repo ./project -e replication
```

## Architecture

Veritas is built on concepts from:

- **idea-explorer**: Multi-agent research automation framework
- **eval_agent**: Execution-grounded evaluation criteria for mechanistic interpretability

```
Input (Paper + Repo)
    ↓
Plan Extraction (optional)
    ↓
Evaluation Pipeline
├─ Code Quality Evaluator
├─ Consistency Evaluator
├─ Generalization Evaluator
├─ Replication Evaluator
└─ Instruction Evaluator
    ↓
Report Generator
    ↓
Output (Markdown + PDF Report)
```

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

MIT License - see [LICENSE](LICENSE) for details.

## Acknowledgments

- Built upon research from the [idea-explorer](https://github.com/your-org/idea-explorer) project
- Evaluation criteria adapted from [eval_agent](https://github.com/your-org/eval_agent)
