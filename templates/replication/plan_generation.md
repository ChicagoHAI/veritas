# Replication Plan Generation

You are generating a step-by-step replication plan for testing whether a paper's code reproduces the paper's reported results.
{% if has_paper %}
## Paper

You MUST read the PDF directly from this local path:
{{ paper_path }}

{% endif %}## Repository Path

{{ repo_path }}

## Paper Claims Summary

The following claims were extracted from the paper and will be verified against your run's output. Each plan step should produce evidence relevant to one or more claims; use the claim IDs (e.g. `C1`, `C2`) in the `verifies` field of each step.

{% for claim in claims.claims %}
- **{{ claim.id }}** ({{ claim.tier }} / {{ claim.type }}): {{ claim.description }}
{% endfor %}

## Your Task

Explore the repository and generate a replication plan — a sequence of concrete steps that an agent should execute to produce evidence for the claims above. The plan should cover:

1. **Environment setup** — what to install, any system requirements
2. **Running the code** — training scripts, experiments, evaluations
3. **Collecting outputs** — what files / metrics each step produces

For each step, provide:
- A clear description of what to do
- A command hint (the likely command to run)
- A **shape-prescriptive** `expected_outcome`: describe the structure of the expected output (file path, JSON field names, figure file location, log message format) — DO NOT include the paper's reported result values.
- A `verifies` list of claim IDs whose verification depends on this step's output. Empty list is allowed for pure-setup steps (e.g. installing dependencies).

### Shape-prescriptive examples

GOOD (shape-prescriptive):
- "Produces `output/metrics.json` with field `accuracy` (float in [0,1])."
- "Writes `figures/HRD.pdf` showing the HR diagram for all three binaries."
- "Logs to stdout in the format `[step] X done, time=Y s`."

BAD (value-prescriptive — DO NOT do this):
- "Accuracy reaches ~92%."
- "Figure shows three peaks at 100, 200, 300 Hz."
- "Loss converges below 0.5."

The replication agent never sees the paper's reported result values. Including them in `expected_outcome` would leak ground truth to the agent and defeat the verification step.

### Setup values from the paper ARE allowed

Setup values that the paper *prescribes* (hyperparameters, dataset sizes, version pins, simulation initial conditions like initial masses or metallicity) ARE allowed in step descriptions and command hints. They tell the agent how to run, not what answer to produce.

GOOD:
- "Run the training with learning rate 2e-5, batch size 32, 3 epochs (paper §3.1)."

This is a setup value, not a result.

{% if mode == "main" %}
## Scope

Focus on the paper's **headline and supporting claims**. Do not attempt to reproduce setup-only assertions, ablation studies, or appendix-only results unless they are essential to a headline claim.
{% endif %}

## Rules

- Order steps logically: setup first, then execution, then verification
- Include 3-10 steps (enough to cover the headline claims, not exhaustive)
- The agent executing this plan will work on a writable copy of the repo at `/workspace/output/replication/codebase/`
- The agent may fix issues in the code to keep replication going (deprecated APIs, missing imports, configuration problems)
- If you find multiple entry points or experiments, prioritize the one that targets the headline claim
- Every result-producing step MUST have at least one claim ID in `verifies`. Setup-only steps may have an empty `verifies` list.
- NEVER include the paper's reported numerical result values in `expected_outcome`.

## Output

Save the plan to `{{ output_dir }}/analyze/replication_plan.json` with this format:

```json
{
    "environment": {
        "language": "python or other",
        "key_dependencies": ["list", "of", "main", "packages"],
        "setup_hints": "Any notes about environment requirements"
    },
    "steps": [
        {
            "id": 1,
            "description": "What this step does",
            "command_hint": "the command to run",
            "expected_outcome": "Shape of expected output (NOT the paper's reported values)",
            "verifies": ["C1", "C2"]
        }
    ]
}
```

Also print the JSON to stdout so it can be captured.

Begin your analysis now.
