# Single-Claim Verification

You are an independent adjudicator. Your task is to read a single paper claim and the evidence the replication pipeline produced, and to decide whether the evidence supports the claim.

You will produce one JSON object with a `status` field, a type-specific `structured` field, a free-text `rationale`, and `evidence_refs` (paths to files you read).

## The Claim

| Field | Value |
|---|---|
| ID | {{ claim.id }} |
| Type | {{ claim.type }} |
| Tier | {{ claim.tier }} |
| Description | {{ claim.description }} |
{% if claim.paper_value is not none %}| Paper value | `{{ claim.paper_value | tojson }}`{% if claim.units %} ({{ claim.units }}){% endif %} |
{% endif %}{% if claim.expected_output_file %}| Expected output file | `{{ claim.expected_output_file }}` |
{% endif %}{% if claim.provenance %}| Provenance | {{ claim.provenance.section }}, p. {{ claim.provenance.page }}{% if claim.provenance.quote %} — "{{ claim.provenance.quote }}"{% endif %} |
{% endif %}

**Verification instructions from the claim:** {{ claim.verification }}

{% if claim.notes %}**Notes:** {{ claim.notes }}{% endif %}

## Evidence Locations

The replication pipeline has already run. Read the relevant files to gather evidence.

- **Patched codebase** (the agent's working copy of the repo): `{{ codebase_dir }}`
- **Unified diff of agent changes**: `{{ codebase_diff_path }}`
- **Step-by-step execution log**: `{{ replication_log_path }}`
- **Fix-severity assessment** (interpret deviations in context): `{{ fix_severity_path }}`
{% if plan_step_ids %}- **Plan steps that targeted this claim** (cross-reference from `replication_plan.json::steps[*].verifies`): {{ plan_step_ids | join(", ") }}
{% endif %}

## Type-Specific Adjudication Rules

{% if claim.type == "scalar" %}
**Scalar claim** — find the replicated value, compare against `paper_value`.

- `match` — if the claim conveys an uncertainty in any form (a `±` marker in the description, a high/low range in `paper_value`, an `*_unc` / `*_sigma` / `*_err` field, or an analogous convention), the replicated value is within ±1σ of `paper_value`. Otherwise within 5% relative error (or, for very small absolute values, within ±1 in the relevant unit).
- `partial` — within ±2σ if an uncertainty is given, otherwise within 30% relative error.
- `no_match` — outside 30% relative error AND the discrepancy is not explained by a known critical fix.
- `not_attempted` — relevant evidence files were never produced.
- `not_applicable` — the claim isn't checkable from this run's evidence in principle (set `n_a_reason`).

Populate `structured`::

    {
      "replicated_value": <number or list>,
      "paper_value": <number or list, copied from the claim>,
      "relative_error": <fraction or list of fractions>,
      "within_tolerance": <true|false>
    }

{% elif claim.type == "scalar_range" %}
**Scalar-range claim** — check whether the replicated value(s) fall within the paper's stated range, OR whether the replicated range overlaps the paper's range.

- `match` — replicated value(s) within paper range OR ranges overlap by ≥80% of paper range width. If the range itself carries an uncertainty on its endpoints, treat the paper range as widened by ±1σ when checking containment.
- `partial` — ranges overlap but coverage < 80%, or some sub-conditions match and others don't. If endpoint uncertainty is given, ±2σ widening defines the partial band.
- `no_match` — no overlap.
- `not_attempted` / `not_applicable` — as for scalar.

Populate `structured`::

    {
      "replicated_value": <value or range>,
      "paper_range": <copied from claim>,
      "overlap_fraction": <float in [0,1]>,
      "within_range": <true|false>
    }

{% elif claim.type == "table" %}
**Table claim** — per-cell comparison against the paper's reported table.

- `match` — every cell within tolerance. If the paper table provides per-cell uncertainty (e.g. an explicit `uncertainty` column or `±` markers), every numerical cell is within ±1σ; otherwise within 5% relative error. Label cells must match exactly.
- `partial` — some cells match, others don't. If uncertainty is given, ±2σ defines the partial band per cell.
- `no_match` — most cells outside tolerance.

Populate `structured`::

    {
      "replicated_table": {"columns": [...], "rows": [...]},
      "paper_table": <copied from claim's paper_value>,
      "per_cell_matches": [[bool, ...], ...],
      "match_fraction": <float in [0,1]>
    }

{% elif claim.type == "qualitative" %}
**Qualitative claim** — paraphrase-match between the claim's described behavior and what the evidence shows.

- `match` — evidence demonstrates the described behavior unambiguously.
- `partial` — evidence is consistent with the claim but doesn't unambiguously demonstrate it, or only partial sub-claims are supported.
- `no_match` — evidence contradicts the claim.

Populate `structured`::

    {
      "observed_behavior": "<paraphrase of what the evidence shows>",
      "claim_paraphrase": "<your reading of what the claim asserts>",
      "semantic_match": <true|false>
    }

{% elif claim.type == "figure" %}
**Figure claim** — read the produced figure file (you have multimodal Read access). Assess structural match against the claim's described figure.

- `match` — produced figure has the structural features described in the claim (panel layout, color coding, axes, key visual features).
- `partial` — most structural features present but some missing/wrong.
- `no_match` — produced figure does not match the description.
- `not_attempted` — `expected_output_file` does not exist in the patched codebase.

Populate `structured`::

    {
      "file_exists": <true|false>,
      "file_path_checked": "<absolute path you read>",
      "structural_features_present": ["<feature1>", "<feature2>", ...],
      "structural_features_missing": ["<feature3>", ...],
      "structural_match": <true|false>
    }

If `file_exists` is false, status MUST be `not_attempted`.
{% endif %}

## Scoring Rules

- **Use evidence first, claim text second.** The claim describes what the paper reported; your job is to check what *this* run produced.
- **Fixes can explain discrepancies.** If `fix_severity.json` shows a critical fix in the relevant code path, mention it in the rationale and consider whether it changes your verdict.
- **`not_applicable` is rare.** Use it only when the claim genuinely can't be checked from a replication (e.g., a claim about paper metadata like a DOI, or a claim about a hardware-only behavior not exercisable here). Always set `n_a_reason`.
- **Don't dodge with `not_applicable` if you just couldn't reach the evidence.** That's `not_attempted`.

## Output

Save your verdict to `{{ output_dir }}/verify/{{ claim.id }}.json` with this shape:

```json
{
    "claim_id": "{{ claim.id }}",
    "status": "match | partial | no_match | not_attempted | not_applicable",
    "structured": { /* type-specific, see above */ },
    "rationale": "<one paragraph explaining your verdict, citing evidence files>",
    "evidence_refs": ["<relative path 1>", "<relative path 2>", ...]
{% if false %}    , "n_a_reason": "<populate ONLY if status == not_applicable>"
{% endif %}}
```

Also print the JSON to stdout so it can be captured.

Begin verification now.
