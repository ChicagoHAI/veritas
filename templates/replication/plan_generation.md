# Replication Plan Generation

You are generating a step-by-step replication plan for testing the reproducibility of a code repository.
{% if has_paper %}
You have been given the paper that this repository is meant to replicate.

## Paper

You MUST read the PDF directly from this local path:
{{ paper_path }}

{% else %}
No paper was provided. Generate the plan based on the repository contents alone.
{% endif %}

## Repository Path

{{ repo_path }}

## Checklist Summary

The following checklist items will be evaluated after replication. Your plan should produce evidence relevant to these questions:

{% for item in checklist_items %}
- {{ item.question }}
{% endfor %}

## Your Task

Explore the repository and generate a replication plan — a sequence of concrete steps that an agent should execute to test this code. The plan should cover:

1. **Environment setup** — what to install, any system requirements
2. **Running the code** — training scripts, experiments, evaluations
3. **Collecting outputs** — what files/metrics to check

For each step, provide:
- A clear description of what to do
- A command hint (the likely command to run)
- The expected outcome if the code works correctly

Focus on steps that produce **verifiable evidence** — commands that generate output files, print metrics, or produce error messages.

## Rules

- Order steps logically: setup first, then execution, then verification
- Include 3-10 steps (enough to cover the key claims, not exhaustive)
- The agent executing this plan will work inside a Docker container with the repo mounted at `/workspace/repo`
- The agent will NOT modify the repository's source code
- If you find multiple entry points or experiments, prioritize the main/primary one

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
            "expected_outcome": "What should happen if it works"
        }
    ]
}
```

Also print the JSON to stdout so it can be captured.

Begin your analysis now.
