# Resource Estimation

You are analyzing a scientific paper and its replication plan to estimate
the computational resources needed to replicate it.

## Paper
{% if has_paper %}
Read the paper at: {{ paper_path }}
Look for any mentions of compute used: GPU hours, training time, API costs,
number of experiments, dataset sizes.
{% endif %}

## Replication Plan
{{ replication_plan }}

## Your Task

Produce a structured resource estimate. Pay attention to:
- How many steps are in the plan and whether any involve loops or repetition
  (e.g. "run on 5 datasets × 3 seeds = 15 runs")
- Whether any steps call an external LLM API and on how many samples
- Whether the paper itself reported compute cost or wall time

Write your estimate to `{{ output_dir }}/analyze/resource_estimate.json`:

```json
{
    "reported_compute": "4 A100 GPUs for 48 hours",
    "reported_cost_usd": null,
    "total_steps": 5,
    "estimated_experiment_runs": 15,
    "estimated_llm_calls": null,
    "compute_class": "heavy",
    "breakdown_notes": "Plan runs training across 5 datasets with 3 seeds each."
}