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
- Use real numbers you find, and quote the source URL in your output.
- If you cannot find reliable pricing, omit the cost fields or set them to null.

## Output

Write your estimate to {{ output_dir }}/analyze/resource_estimate.json.

The file must contain at minimum:
- compute_class: "light" (under 5 min on CPU), "medium" (significant CPU/RAM or hours), "heavy" (GPU required or multi-hour run)
- breakdown_notes: a plain-English explanation of your estimate, quoting the paper or plan where possible
- estimated_veritas_overhead: the fixed veritas pipeline overhead breakdown (see below — copy as-is)

Beyond those, include any fields that are useful and available for this paper. Examples:

```json
{
    "compute_class": "heavy",
    "breakdown_notes": "Paper states 48h on 4 A100s. Current A100 rate ~$2/hr on RunPod (https://runpod.io/pricing), giving ~$384 estimated.",
    "needs_gpu": true,
    "paper_reported_compute": "4 A100 GPUs for 48 hours",
    "paper_reported_wall_time": "48 hours on 4 A100s",
    "paper_reported_cost_usd": null,
    "estimated_cost_usd": 384.0,
    "estimated_cost_source": "https://runpod.io/pricing",
    "total_steps": 5,
    "estimated_experiment_runs": 15,
    "estimated_llm_calls": null,
    "parallelizable": false,
    "estimated_total_run_time": "~1-2 hours (15 min experiment + 30-60 min veritas overhead)",
    "estimated_veritas_overhead": {
        "analyze": "1-3 min",
        "replicate": "30-60+ min (varies by repo complexity and number of agent fixes)",
        "verify": "1-5 min per claim"
    }
}
```

Only include `paper_reported_wall_time` if the paper explicitly states a duration — omit it entirely if not mentioned.
Always include `estimated_total_run_time` as a single human-readable rough range combining the experiment runtime and veritas overhead. Label it clearly as approximate.

Add or omit other fields as the paper warrants — the schema is a suggestion, not a contract.
Set numeric fields to null when unknown. Write the file now.
{% if mode == "paper-only" and pre_codegen %}
## Note: paper-only mode

No repository was provided — the code will be generated from the paper by a separate codegen phase that runs after this estimate. Codegen is a roughly constant cost regardless of paper content (~$1–2 in LLM API calls). Include it as a predicted line item in your output, for example:

```json
"predicted_codegen_cost_usd": 1.5
```

Set this to 0 if a repo was supplied (codegen doesn't run in that case).
{% endif %}
