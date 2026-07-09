# Single-Claim Verification

You are an independent adjudicator. Your task is to read a single paper claim and the evidence the replication pipeline produced, and to decide whether the evidence supports the claim.

You will produce one JSON object with a `status` field, a type-specific `structured` field, a free-text `rationale`, and `evidence_refs` (paths to files you read).

## Available skills

A catalog of scientific-computing skills is staged at
`{{ skills_dir }}/`. Each subdirectory has a `SKILL.md` whose
YAML frontmatter `description:` field summarizes when the skill applies.
You may browse the catalog and use a skill if its description genuinely
matches your verification work (for example, when reading evidence files
in a particular format); many verifications will not need any skill, and
that is fine.

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
- **Replication plan** (step descriptions, commands, and which steps target which claims): `{{ output_dir }}/analyze/replication_plan.json`
{% if plan_step_ids %}- **Plan steps that targeted this claim** (from the plan's `steps[*].verifies`): {{ plan_step_ids | join(", ") }}
{% endif %}

## Answer Fidelity (applies to every structured field you write)

The `structured` field you produce is consumed downstream to score this run
against the reference. Two reporting failures lose credit even when the
underlying computation is correct — avoid both:

- **Keys verbatim.** When the claim names specific question keys, labels, or
  entities (in `paper_value`, `verification`, or the description), copy them
  **byte-for-byte** into your structured output. Do not re-type, "fix" spelling,
  pluralize, re-case, or paraphrase a key. Build your output by iterating the
  claim's keys and filling the value for each — never invent keys the claim did
  not ask for. (A value reported under a mutated or invented key cannot be
  matched to the question.)
- **Precision from the question, never from the target.** Report numeric values
  at the precision the claim/units imply, mirroring how the value is naturally
  expressed in the claim — not a raw 15-digit float dump. Do **not** look at the
  reference/`paper_value` to decide how many digits to report or how to round;
  precision is a function of the question and scientific convention only. If in
  doubt, report the value as the produced evidence prints it, trimmed of
  spurious trailing float noise.

## How your verdict is used (read first)

For **scalar / scalar_range / table** claims you are the **comparator**: your job is to extract the replicated value *accurately and objectively* into `structured`. A separate deterministic grader (not an LLM) then decides `match | partial | no_match` from your extracted value against `paper_value` and the run's tolerance policy (defaults shown per type below) — so your numeric `status` is only a proposal and **will be re-derived from your `structured` values**. Get the *values, keys, and uncertainty* right; the pass/fail is computed, not argued. The rules below tell you what that grader will compute, so you can sanity-check your extraction.

For **qualitative / figure** claims there is no number to compute on, so **your
`status` is authoritative** — judge carefully.

In all cases set `value_found` honestly: `true` only if this run actually
produced a value/figure to compare; `false` (→ not_attempted) if it did not.
Never guess a value to fill the slot.

## Type-Specific Adjudication Rules

{% if claim.type == "scalar" %}
**Scalar claim** — find the replicated value, compare against `paper_value`.

- `match` — if the claim conveys an uncertainty in any form (a `±` marker in the description, a high/low range in `paper_value`, an `*_unc` / `*_sigma` / `*_err` field, or an analogous convention), the replicated value is within ±1σ of `paper_value`. Otherwise within 5% relative error (for a paper value that is essentially zero, a small absolute band replaces the relative test).
- `partial` — within ±2σ if an uncertainty is given, otherwise within 30% relative error.
- `no_match` — outside those bands.
- `not_attempted` — relevant evidence files were never produced.
- `not_applicable` — the claim isn't checkable from this run's evidence in principle (set `n_a_reason`).

Populate `structured`: (the grader reads these exact fields)

    {
      "replicated_value": <number, list, or flat dict {key: number} — what THIS run produced; null if none>,
      "paper_value": <number, list, or flat dict — copied verbatim from the claim>,
      "uncertainty": <the 1σ uncertainty as a single number if the claim conveys
                      one (from a ± marker, an *_sigma/*_err field, or a high/low
                      range), else null>,
      "value_found": <true|false>
    }

{% elif claim.type == "scalar_range" %}
**Scalar-range claim** — check whether the replicated value(s) fall within the paper's stated range.

- `match` — every replicated value lies inside `paper_range`.
- `partial` — a value lands outside the range but within the range widened by 30% of its width on each side, or some values fall inside and others outside.
- `no_match` — every value falls outside even the widened range.
- `not_attempted` / `not_applicable` — as for scalar.

Populate `structured`: (the grader reads these exact fields)

    {
      "replicated_value": <number or list of numbers this run produced; null if none>,
      "paper_range": <[low, high], copied from the claim>,
      "value_found": <true|false>
    }

{% elif claim.type == "table" %}
**Table claim** — per-cell comparison against the paper's reported table.

- `match` — every cell within 5% relative error of its paper value. Label keys must match exactly (a missing or mutated key fails that cell).
- `partial` — anything in between: mixed cells, or cells landing in the 5-30% band.
- `no_match` — every cell beyond 30% relative error.

**Cell resolution.** When the claim asks for a specific cell of a table
(e.g. "the similarity between A and B"), resolve it by **explicit row-label
AND column-label lookup**, asserting both labels match what the claim names —
not by row order or position. For an **asymmetric** matrix (where cell [A][B] ≠
[B][A]), the order in the question matters: read the cell the question's phrasing
designates, not its transpose. Report the value under the exact key the claim
uses for that question.

Populate `structured`: (the grader compares per key — prefer flat dicts)

    {
      "replicated_table": {"<exact key1>": <number>, "<exact key2>": <number>, ...},
      "paper_table": {"<exact key1>": <number>, ...},
      "value_found": <true|false>
    }

Build BOTH dicts keyed by the claim's **exact** question keys (copied verbatim —
see Answer Fidelity); the grader matches `replicated_table[key]` against
`paper_table[key]` per key, so a mutated or missing key fails that cell.

**Use the flat `{key: number}` shape whenever the table can be expressed that
way — it almost always can** (per-question answers, per-label rows, a single
cell). Only when the values are genuinely non-scalar or the table truly cannot be
flattened, fall back to `{"columns": [...], "rows": [...]}` and set `status`
yourself (the grader then keeps your judgment). Prefer the flat shape: it routes
the verdict through the deterministic grader, which is the reliable, auditable
path — emitting `{columns, rows}` for a flattenable table needlessly drops back
to a subjective judgment.

{% elif claim.type == "qualitative" %}
**Qualitative claim** — paraphrase-match between the claim's described behavior and what the evidence shows.

- `match` — evidence demonstrates the described behavior unambiguously.
- `partial` — evidence is consistent with the claim but doesn't unambiguously demonstrate it, or only partial sub-claims are supported.
- `no_match` — evidence contradicts the claim.

Populate `structured`:

    {
      "observed_behavior": "<paraphrase of what the evidence shows>",
      "claim_paraphrase": "<your reading of what the claim asserts>",
      "semantic_match": <true|false>
    }

{% elif claim.type == "figure" %}
**Figure claim** — read the produced figure file (you have multimodal Read access). Assess structural match against the claim's described figure.

**Finding the produced figure — look beyond the exact expected filename.** The
`expected_output_file` is a hint, not the only acceptable evidence. Before
concluding the figure wasn't produced, search the patched codebase for the
figure the claim describes, in this order:
1. The exact `expected_output_file`.
2. Any single produced figure with a related name/location (e.g. `figures/`,
   `results/`, `output/`; `.pdf` / `.png` / `.svg` / `.jpg`).
3. **The figure's constituent panels.** A multi-panel figure is often emitted as
   separate panel files (e.g. `fig3_a.png … fig3_f.png`, or per-panel PDFs) even
   when the combined file was never assembled. If the panels that make up the
   claimed figure were produced, that **counts as produced** — assess the
   structural match from the panels collectively.

- `match` — the produced figure (single file or its panels together) has the
  structural features described in the claim (panel layout, color coding, axes,
  key visual features).
- `partial` — most structural features present but some missing/wrong, OR the
  panels were produced but the claimed combined/assembled figure was not.
- `no_match` — the produced figure(s) contradict the description.
- `not_attempted` — **no** relevant figure file or panel was produced at all
  (the code that draws this figure did not run / emitted nothing).

Populate `structured`:

    {
      "file_exists": <true if the exact expected_output_file exists>,
      "evidence_found": <true if ANY relevant figure file OR its panels exist>,
      "file_path_checked": ["<path 1>", "<panel path 2>", ...],
      "structural_features_present": ["<feature1>", "<feature2>", ...],
      "structural_features_missing": ["<feature3>", ...],
      "structural_match": <true|false>
    }

Set status to `not_attempted` **only when `evidence_found` is false** — i.e. no
figure and no panels were produced. If panels exist but the combined figure does
not, prefer `partial` (the content reproduced; only the assembly is missing),
not `not_attempted`.
{% endif %}

## Scoring Rules

- **Use evidence first, claim text second.** The claim describes what the paper reported; your job is to check what *this* run produced.
- **Fixes give context.** If `fix_severity.json` shows a critical fix in the relevant code path, cite it in your rationale. For qualitative / figure claims it may legitimately change your judgment; for numeric claims it cannot change the computed status — the grader sees only your extracted values.
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
    "evidence_refs": ["<relative path 1>", "<relative path 2>", ...],
    "n_a_reason": "<ONLY when status == not_applicable; omit this key otherwise>"
}
```

Begin verification now.
