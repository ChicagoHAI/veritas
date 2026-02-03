"""Generate prompts for evaluation agents."""

from pathlib import Path
from typing import Optional
from jinja2 import Environment, FileSystemLoader, select_autoescape


class PromptGenerator:
    """Generates prompts for replication evaluation agents."""

    def __init__(self, templates_dir: Optional[Path] = None):
        if templates_dir is None:
            # Default to templates directory in package
            templates_dir = Path(__file__).parent.parent.parent.parent / "templates"

        self.templates_dir = templates_dir
        self.env = Environment(
            loader=FileSystemLoader(templates_dir),
            autoescape=select_autoescape(['html', 'xml']),
            trim_blocks=True,
            lstrip_blocks=True,
        )

    def generate_evaluation_prompt(
        self,
        eval_type: str,
        repo_path: Path,
        plan_path: Optional[Path] = None,
        output_dir: Optional[Path] = None,
    ) -> str:
        """
        Generate a prompt for a specific evaluation type.

        Args:
            eval_type: Type of evaluation (code, consistency, etc.)
            repo_path: Path to the repository being evaluated
            plan_path: Path to the plan file (if available)
            output_dir: Directory for evaluation outputs

        Returns:
            Complete prompt string for the evaluation agent
        """
        template_map = {
            "code": "evaluation/code_evaluation.txt",
            "consistency": "evaluation/consistency_evaluation.txt",
            "generalization": "evaluation/generalization_evaluation.txt",
            "replication": "evaluation/replication_evaluation.txt",
            "instruction": "evaluation/instruction_evaluation.txt",
        }

        if eval_type not in template_map:
            raise ValueError(f"Unknown evaluation type: {eval_type}")

        template_path = template_map[eval_type]

        try:
            template = self.env.get_template(template_path)
        except Exception:
            # If template doesn't exist, use default
            return self._generate_default_prompt(
                eval_type, repo_path, plan_path, output_dir
            )

        # Prepare context
        context = {
            "repo_path": str(repo_path.absolute()),
            "plan_path": str(plan_path.absolute()) if plan_path else None,
            "output_dir": str(output_dir.absolute()) if output_dir else str(repo_path / "evaluation"),
            "has_plan": plan_path is not None and plan_path.exists(),
        }

        return template.render(**context)

    def _generate_default_prompt(
        self,
        eval_type: str,
        repo_path: Path,
        plan_path: Optional[Path],
        output_dir: Optional[Path],
    ) -> str:
        """Generate a default prompt if template is missing."""
        prompts = {
            "code": self._code_evaluation_prompt,
            "consistency": self._consistency_evaluation_prompt,
            "generalization": self._generalization_evaluation_prompt,
            "replication": self._replication_evaluation_prompt,
            "instruction": self._instruction_evaluation_prompt,
        }

        return prompts[eval_type](repo_path, plan_path, output_dir)

    def _code_evaluation_prompt(
        self,
        repo_path: Path,
        plan_path: Optional[Path],
        output_dir: Optional[Path],
    ) -> str:
        """Generate code evaluation prompt."""
        return f"""# Code Quality Evaluation

You are evaluating the code quality of a research project for replication purposes.

## Repository Path
{repo_path}

## Plan File
{plan_path if plan_path else "No plan file provided"}

## Output Directory
{output_dir or repo_path / "evaluation"}

## Your Task

Analyze all code in the repository and evaluate each code block/function on:

1. **Runnable (Y/N)**: Can it execute without errors?
2. **Correct-Implementation (Y/N)**: Does the logic correctly implement what it describes?
3. **Redundant (Y/N)**: Does it duplicate another block's computation?
4. **Irrelevant (Y/N)**: Does it not contribute to the project goal?

## Metrics to Compute

- Runnable%: (runnable blocks) / (total blocks) × 100
- Incorrect%: (blocks with wrong logic) / (total blocks) × 100
- Redundant%: (redundant blocks) / (total blocks) × 100
- Irrelevant%: (irrelevant blocks) / (total blocks) × 100

## Checklist

Provide PASS/FAIL for:
- **C1**: All core analysis code is runnable
- **C2**: All implementations are correct
- **C3**: No redundant code
- **C4**: No irrelevant code

## Output Format

Save your evaluation to `{output_dir}/code_evaluation.json` with this structure:
```json
{{
  "Checklist": {{
    "C1": "PASS or FAIL",
    "C2": "PASS or FAIL",
    "C3": "PASS or FAIL",
    "C4": "PASS or FAIL"
  }},
  "Rationale": {{
    "C1": "Explanation",
    "C2": "Explanation",
    "C3": "Explanation",
    "C4": "Explanation"
  }},
  "Metrics": {{
    "runnable_pct": 0.0,
    "incorrect_pct": 0.0,
    "redundant_pct": 0.0,
    "irrelevant_pct": 0.0,
    "total_blocks": 0
  }}
}}
```

Begin your evaluation now. Read the code files and provide your assessment.
"""

    def _consistency_evaluation_prompt(
        self,
        repo_path: Path,
        plan_path: Optional[Path],
        output_dir: Optional[Path],
    ) -> str:
        """Generate consistency evaluation prompt."""
        return f"""# Consistency Evaluation

You are evaluating the internal consistency of a research project.

## Repository Path
{repo_path}

## Plan File
{plan_path if plan_path else "No plan file provided"}

## Output Directory
{output_dir or repo_path / "evaluation"}

## Your Task

Evaluate the consistency between documentation, code, and claims:

## Checklist

Provide PASS/FAIL for:

- **CS1: Results vs Conclusion** - Do all evaluable conclusions match the code results?
- **CS2: Implementation Follows Plan** - Are plan steps reflected in the implementation?
- **CS3: Effect Size** - Are reported effects non-trivial relative to baseline/variability?
- **CS4: Justification** - Are key design choices and conclusions explicitly justified?
- **CS5: Statistical Significance** - Do key results report uncertainty measures or statistical tests?

## Output Format

Save your evaluation to `{output_dir}/consistency_evaluation.json` with this structure:
```json
{{
  "Checklist": {{
    "CS1": "PASS or FAIL",
    "CS2": "PASS or FAIL",
    "CS3": "PASS or FAIL",
    "CS4": "PASS or FAIL",
    "CS5": "PASS or FAIL"
  }},
  "Rationale": {{
    "CS1": "Explanation",
    "CS2": "Explanation",
    "CS3": "Explanation",
    "CS4": "Explanation",
    "CS5": "Explanation"
  }}
}}
```

Begin your evaluation now.
"""

    def _generalization_evaluation_prompt(
        self,
        repo_path: Path,
        plan_path: Optional[Path],
        output_dir: Optional[Path],
    ) -> str:
        """Generate generalization evaluation prompt."""
        return f"""# Generalization Evaluation

You are evaluating whether the findings generalize beyond the original experimental setting.

## Repository Path
{repo_path}

## Plan File
{plan_path if plan_path else "No plan file provided"}

## Output Directory
{output_dir or repo_path / "evaluation"}

## Your Task

Test whether findings generalize to new settings:

## Checklist

Provide PASS/FAIL/NA for:

- **GT1: Model Generalization** - Is the finding predictable on new models not in the original work?
- **GT2: Data Generalization** - Is the finding predictable on new data instances not in the original dataset?
- **GT3: Method Generalization** - If a new method was proposed, can it apply to similar tasks?

## Rules
- Maximum 3 trial examples allowed per item
- One successful example = PASS
- Models/data must not appear in the original paper

## Output Format

Save your evaluation to `{output_dir}/generalization_evaluation.json` with this structure:
```json
{{
  "Checklist": {{
    "GT1": "PASS or FAIL or NA",
    "GT2": "PASS or FAIL or NA",
    "GT3": "PASS or FAIL or NA"
  }},
  "Rationale": {{
    "GT1": "Explanation with examples tested",
    "GT2": "Explanation with examples tested",
    "GT3": "Explanation with examples tested"
  }}
}}
```

Begin your evaluation now.
"""

    def _replication_evaluation_prompt(
        self,
        repo_path: Path,
        plan_path: Optional[Path],
        output_dir: Optional[Path],
    ) -> str:
        """Generate replication evaluation prompt."""
        return f"""# Replication Evaluation

You are assessing how well the experiment can be replicated from documentation.

## Repository Path
{repo_path}

## Plan File
{plan_path if plan_path else "No plan file provided"}

## Output Directory
{output_dir or repo_path / "evaluation"}

## Your Task

Evaluate the replicability of the project:

## Checklist

Provide PASS/FAIL for:

- **RP1: Implementation Reconstructability** - Can the experiment be reconstructed from plan/code-walk without major guesswork?
- **RP2: Environment Reproducibility** - Can the environment/packages/models be restored without unresolved issues?
- **RP3: Determinism & Stability** - Are replicated results stable across runs?

## Rules
- Do not copy code verbatim - reimplement from plan understanding
- Match reported results within acceptable tolerance
- Log all ambiguities and inconsistencies

## Output Format

Save your evaluation to `{output_dir}/replication_evaluation.json` with this structure:
```json
{{
  "Checklist": {{
    "RP1": "PASS or FAIL",
    "RP2": "PASS or FAIL",
    "RP3": "PASS or FAIL"
  }},
  "Rationale": {{
    "RP1": "Explanation",
    "RP2": "Explanation",
    "RP3": "Explanation"
  }}
}}
```

Also create:
- `{output_dir}/replications/replication_notes.md` - Your reimplementation notes
- `{output_dir}/replications/replication_results.json` - Results comparison

Begin your evaluation now.
"""

    def _instruction_evaluation_prompt(
        self,
        repo_path: Path,
        plan_path: Optional[Path],
        output_dir: Optional[Path],
    ) -> str:
        """Generate instruction following evaluation prompt."""
        return f"""# Instruction Following Evaluation

You are evaluating whether the implementation follows the original research goals.

## Repository Path
{repo_path}

## Plan File
{plan_path if plan_path else "No plan file provided"}

## Output Directory
{output_dir or repo_path / "evaluation"}

## Your Task

Evaluate alignment between goals and implementation:

## Checklist

Provide PASS/FAIL for:

- **TS1: Goal Alignment** - Does the implementation goal align with the original research goal?
- **TS2: Plan Adherence** - Does the methodology cover all required analyses?
- **TS3: Hypothesis Coverage** - Are all hypotheses from the plan tested?
- **TS4: Component Matching** - Do implementation components match their described functions?

## Special Case
If no Plan file exists, mark TS1 and TS2 as FAIL with note "No Plan file found"

## Output Format

Save your evaluation to `{output_dir}/instruction_evaluation.json` with this structure:
```json
{{
  "Checklist": {{
    "TS1": "PASS or FAIL",
    "TS2": "PASS or FAIL",
    "TS3": "PASS or FAIL",
    "TS4": "PASS or FAIL"
  }},
  "Rationale": {{
    "TS1": "Explanation",
    "TS2": "Explanation",
    "TS3": "Explanation",
    "TS4": "Explanation"
  }}
}}
```

Begin your evaluation now.
"""

    def generate_report_prompt(
        self,
        results_dir: Path,
        output_path: Path,
    ) -> str:
        """Generate prompt for creating the final replication report."""
        return f"""# Generate Replication Report

You are generating a comprehensive replication report from evaluation results.

## Results Directory
{results_dir}

## Output Path
{output_path}

## Your Task

1. Read all evaluation JSON files in the results directory
2. Aggregate the checklist results
3. Generate a comprehensive markdown report with:
   - Executive Summary (overall replicability score)
   - Code Quality Assessment
   - Consistency Analysis
   - Generalization Results
   - Replication Assessment
   - Detailed Findings
   - Recommendations

## Report Structure

```markdown
# Replication Report

## Executive Summary
[Overall assessment and score]

## Evaluation Results

### Code Quality
[C1-C4 results with explanations]

### Consistency
[CS1-CS5 results with explanations]

### Generalization
[GT1-GT3 results with explanations]

### Replicability
[RP1-RP3 results with explanations]

## Detailed Findings
[Specific issues and observations]

## Recommendations
[Actionable suggestions for improvement]

## Appendix
[Raw data and additional details]
```

Generate the report now.
"""
