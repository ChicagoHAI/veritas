# Paper Claims Extraction

You are extracting the structured, verifiable claims from a scientific paper. Each claim is one fact that the replication pipeline should later check against produced evidence.

## Available skills

A catalog of scientific-computing skills is staged at
`{{ skills_dir }}/`. Each subdirectory has a `SKILL.md` whose
YAML frontmatter `description:` field summarizes when the skill applies.
You may browse the catalog and use a skill if its description genuinely
matches your work; many extractions will not need any skill, and that
is fine.

{% if has_paper %}## Paper

You MUST read the PDF directly from this local path:
{{ paper_path }}
{% else %}## Spec Source

This run is in repo-only mode (no paper provided). Treat the
following README as the spec for what the code aims to do and what
results / outputs / claims it makes:

{{ readme_path }}

Read the README contents and extract any verifiable claims (numerical,
structural, or qualitative). If the README is too thin to support any
verifiable claim, return an empty `claims` array.
{% endif %}

{% if has_repo %}## Repository Path

{{ repo_path }}
{% endif %}

## Your Task

{% if has_paper %}Read the paper.{% else %}Read the README.{% endif %} Identify every claim that:
1. Reports a result, observation, measurement, or behavior of the system under study, AND
2. Could plausibly be checked by inspecting outputs that the paper's code is expected to produce (numbers, ranges, tables, figures, or qualitative behaviors).

DO NOT extract:
- Configuration values, hyperparameters, or method choices the authors *prescribe* for their own run (these are inputs to the replication, not results to verify). They will be encoded in the replication plan separately.
- Background, motivation, or related-work claims.
- Limitations or future-work statements.
- Citations to other papers.

## Claim Schema

Output a JSON object with this top-level shape:

```json
{
    "paper": {
        "title": "<paper title>",
        "arxiv_id": "<arxiv id if visible, else null>",
        "year": <int if visible, else null>,
        "authors": ["<lastname>", ...]
    },
    "extraction_mode": "main",
    "claims": [ /* claim objects, see below */ ]
}
```

Each claim object has these fields:

| Field | Required | Description |
|---|---|---|
| `id` | yes | Short identifier, e.g. `"C1"`, `"C2"`. Sequential. |
| `description` | yes | One sentence: what the claim asserts. Use the paper's own terminology. |
| `type` | yes | One of: `scalar`, `scalar_range`, `table`, `qualitative`, `figure`. |
| `tier` | yes | One of: `headline`, `supporting`. |
| `paper_value` | optional | The value(s) the paper reports. Shape varies by type (see below). Omit for `qualitative` and `figure` claims where no numeric value is stated. |
| `units` | optional | Physical / statistical units of `paper_value`, where meaningful. |
| `expected_output_file` | optional | For `figure` and `table` claims when the paper's code is expected to produce a specific file. Path relative to the repo root. |
| `provenance` | yes | `{"section": "...", "page": <int>, "quote": "..."}` — where in the paper the claim appears. `quote` is the verbatim snippet (≤200 chars). For repo-only sources where "page" doesn't apply, set `page` to 0. |
| `verification` | yes | One paragraph of instructions for the *verifier*: what to read, where, and how to decide if the produced evidence supports the claim. |
| `notes` | optional | Anything else the verifier should know. |

## Type Definitions and `paper_value` Shapes

- **`scalar`** — a single numerical result with units. `paper_value` is a number or a list of numbers (e.g., per-condition values).
  Examples: "accuracy = 92.3%", "TAMS H mass fractions = [0.27, 0.23, 0.21]".

- **`scalar_range`** — a numerical range or set of related ranges. `paper_value` is `[min, max]` or a dict keyed by sub-condition.
  Examples: "reduction is ~42-96%", "ratio minima between 0.56-0.07 / 0.58-0.08 / 0.51-0.04".

- **`table`** — a tabular result with row × column structure. `paper_value` is `{"columns": [...], "rows": [{"label": "...", "values": [...]}]}`. Use this when the paper presents results in a table and individual cells matter.

- **`qualitative`** — a non-numeric observation about the system's behavior. `paper_value` is omitted or a string. The `verification` field tells the verifier what semantic match looks like.
  Examples: "blue loops occur in low-mass binaries", "rotation has smaller effect than accretion history".

- **`figure`** — a paper figure that the code is expected to reproduce. `expected_output_file` points at the produced figure path. `paper_value` is usually omitted; the `verification` field describes the expected structure (panel layout, color coding, axes, key features).

## Tier Definitions

- **`headline`** — the paper's central reproducible result. Usually 1-3 per paper, drawn from the abstract or the marquee figure. The lab cares most about getting these right.
- **`supporting`** — intermediate measurements, secondary figures, qualitative observations that build toward the headline.
When choosing tier, favor `supporting` unless the claim is clearly the paper's central reproducible result. Extract only `headline` and `supporting` claims. Setup-level configuration (e.g., "the model uses 12 layers") belongs in the replication plan, not in claims.

## Verification Field — Concrete Examples

Good `verification` instructions for the verifier:

- For a `scalar` claim: "Read the printed accuracy from the `metrics.json` file produced by `evaluate.py`. The replicated value should be within 5 percentage points of {{ '{{ paper_value }}' }}."
- For a `figure` claim: "Inspect the produced PDF at `expected_output_file`. The figure should show three color-coded trajectories on an HR diagram with iso-radius reference lines."
- For a `qualitative` claim: "From the HR diagram trajectories: check whether accretors of low-mass binaries make a hot-side excursion (T_eff increases then decreases) post-MS."

## Output

Save the JSON to `{{ output_dir }}/analyze/paper_claims.json`. Also print the JSON to stdout so it can be captured.

Begin extraction now.
