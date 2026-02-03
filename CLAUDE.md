The goal of this project is to build a replication agent for replicating scientific findings.

## Project Status: Basic Implementation Complete

### What's Built

1. **CLI Interface** (`src/veritas/cli/main.py`)
   - `veritas evaluate` - Main evaluation command
   - `veritas extract-plan` - Extract plan from paper PDF
   - `veritas report` - Generate report from results

2. **Core Modules** (`src/veritas/core/`)
   - `config.py` - Configuration with 5 evaluation types
   - `runner.py` - Orchestrates evaluation pipeline
   - `plan_extractor.py` - Extracts plans from PDFs
   - `report_generator.py` - Generates markdown + PDF reports

3. **Evaluation Templates** (`templates/evaluation/`)
   - `code_evaluation.txt` - C1-C4 (runnability, correctness, redundancy, relevance)
   - `consistency_evaluation.txt` - CS1-CS5 (results-conclusion, plan-implementation, etc.)
   - `generalization_evaluation.txt` - GT1-GT3 (model, data, method generalization)
   - `replication_evaluation.txt` - RP1-RP3 (reconstructability, environment, determinism)
   - `instruction_evaluation.txt` - TS1-TS4 (goal alignment, plan adherence)

4. **Tests** (`tests/`)
   - 19 tests passing for config, prompt generator, and report generator

### Usage

```bash
# Evaluate a repo
uv run veritas evaluate --repo ./my-project --paper paper.pdf

# Extract plan from paper
uv run veritas extract-plan paper.pdf

# Generate report from existing evaluations
uv run veritas report ./evaluation-dir
```

## Related Projects

1. /data/chenhao/idea-explorer - Multi-agent research automation (architecture adapted)
2. /data/chenhao/eval_agent - Evaluation criteria and prompts (criteria adapted)

## Next Steps

- Add integration tests with real repositories
- Test with different AI providers (Claude, Codex, Gemini)
- Add more sophisticated PDF extraction with AI assistance
- Add batch evaluation support
