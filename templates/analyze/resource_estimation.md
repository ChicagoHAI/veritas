# Resource Estimation

You are estimating the computational resources needed to replicate a scientific paper.

## Step 1: Extract from the paper (primary source)

{% if has_paper %}
Read the paper at: {{ paper_path }}

Look in the experiments, implementation details, and appendix sections for any of the following:
- Hardware used (e.g. "4 A100 GPUs", "single V100", "CPU only")
- Wall-clock time (e.g. "trained for 48 hours", "inference takes 2s per sample")
- API costs (e.g. "costs $0.002 per query", "uses GPT-4 on 1000 samples")
- Dataset size (e.g. "10GB dataset", "1M training examples")
- Number of runs (e.g. "averaged over 5 seeds", "evaluated on 8 benchmarks")

If any of this information is explicitly stated in the paper, use it directly.
{% else %}
No paper provided — skip to Step 2.
{% endif %}

## Step 2: Infer from the replication plan (fallback)

Only use this if the paper does not explicitly report the information above.

{{ replication_plan }}

Look for:
- How many steps involve training or inference
- Whether steps repeat across datasets, seeds, or configs
- Whether any steps call an external LLM API and on how many samples

## Step 3: Look up current pricing (if compute class is medium or heavy)

Use web search to find current costs for the detected resource type:

- If GPU is required: search for current hourly rates (e.g. "RunPod A100 hourly price 2025" or "Lambda Labs H100 cost per hour")
- If external LLM API calls are detected: search for the provider's current pricing page
- Use real numbers you find, and quote the source URL in `breakdown_notes`.
- If you cannot find reliable pricing, set `reported_cost_usd` to `null`.

## Output

Write your estimate to `{{ output_dir }}/analyze/resource_estimate.json`:

```json
{
    "reported_compute": "4 A100 GPUs for 48 hours",
    "reported_cost_usd": null,
    "total_steps": 5,
    "estimated_experiment_runs": 15,
    "estimated_llm_calls": null,
    "compute_class": "heavy",
    "breakdown_notes": "Paper explicitly states training took 48 hours on 4 A100s."
}
```

`compute_class`: `light` (under 5 min on CPU), `medium` (significant CPU/RAM),
`heavy` (GPU required or multi-hour run).

If the paper explicitly reports compute, set `breakdown_notes` to quote it directly.
Set fields to `null` when unknown. Write the file now.
