# Replication Plan Generation

You are generating a step-by-step replication plan for testing whether a paper's code reproduces the paper's reported results.

**Run mode:** {{ mode }} — {% if mode == "full" %}paper and repository both provided.{% elif mode == "paper-only" %}paper-only mode; the codebase at the repository path was just written from the paper by an earlier phase and may be rough.{% elif mode == "repo-only" %}repo-only mode; no paper available — claims came from the README.{% endif %}

{% if manager_guidance %}
> ## ⚠️ This plan is being regenerated at the review manager's request (iteration {{ manager_guidance.iteration }})
>
> A previous replication attempt was reviewed and judged insufficient, and the
> **plan itself** was identified as the thing to fix. Revise the plan with the
> guidance below — do not just reproduce the previous plan.
>
> **Where the previous attempt fell short:**
> {{ manager_guidance.deficiency }}
>
> **What the revised plan must do differently:**
> {{ manager_guidance.directive }}
{% if manager_guidance.already_tried %}>
> **Already tried — do not re-propose these:**
> {{ manager_guidance.already_tried }}
{% endif %}

{% endif %}
## Available skills

A catalog of scientific-computing skills is staged at
`{{ skills_dir }}/`. Each subdirectory has a `SKILL.md` whose
YAML frontmatter `description:` field summarizes when the skill applies.
You may browse the catalog and reference relevant skills in plan steps if
a skill genuinely matches; many plans will not need any skill, and that
is fine.

{% if has_paper %}
## Paper

You MUST read the PDF directly from this local path:
{{ paper_path }}

{% endif %}{% if has_repo %}## Repository Path

{{ repo_path }}

{% endif %}{% if has_data %}## Pre-positioned Data

`{{ data_path }}/` (read-only) — user-supplied inputs for this paper.

{% endif %}
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
{% if has_repo and mode != "paper-only" %}
### Prefer the provided implementation (repo inventory first)

This repository was provided by the paper's authors (or the user) and contains a
working implementation of the analysis. **Inventory it before planning**: list
the scripts / notebooks / entry points (`README`, `run.sh`/`Makefile`, top-level
scripts, `__main__` modules, notebook cells), and for each claim map its
described computation to the existing code path that produces it.

Plan steps must **run the provided code paths** (execute the script, call the
function, run the notebook) rather than re-deriving the analysis from scratch.
The goal is to reproduce *this repository's* results, not to write a parallel
implementation. Only plan a from-scratch computation when the repo genuinely
lacks a path for that claim, the path depends on infrastructure unavailable in
the workspace, or it provably contradicts the paper's methodology — and say so
explicitly in the step description.

When a step will need to patch the shipped code (e.g. a deprecated API), prefer
**pinning the repo's authoring library versions** (from `requirements.txt`,
`environment.yml`, `setup.py`, or a lockfile) over rewriting the call, since a
"behavior-preserving" rewrite can silently shift numerical results. Note in the
step description that the run used a code patch so it can be flagged downstream.

Do **not** read the repository's *saved result artifacts* (cached notebook
outputs, `results/`, `data/cache/`, pickled outputs) as the answer — those are
not a reproduction. Run the code to regenerate them.
{% endif %}

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

### Plan at the paper's scale — no pre-authorized reductions

Plan every result-producing step at the full scale the methodology prescribes — problem size, resolution, iteration count, dataset, and seed count. Do NOT write reduced-scale fallbacks into the plan — no "if intractable, shrink the problem" clauses, no `--quick`/`--fast`-style shortcut flags, no downsized parameter grids. There is no hidden time budget to plan around: a heavy step is allowed to run for hours. If the plan offers a reduced-scale escape hatch, the executing agent will take it and the run will produce numbers at the wrong scale.

When a step is genuinely expensive, plan for *efficiency at full scale* instead: prefer the repo's compiled/vectorized code paths, use the GPU when one is available and the method supports it, or split the computation into resumable chunks. Whether to reduce scale is the executing agent's runtime decision, made only under a genuine resource limit and recorded explicitly — never a plan provision.
{% if gpu_info %}

**Hardware available for this plan:** a GPU is present in this environment: {{ gpu_info }}. Steps whose method benefits from GPU acceleration should plan to use it, and `setup_hints` should say so, rather than assuming a CPU-only path.
{% endif %}

## Scope

Focus on the paper's **headline and supporting claims**. Do not attempt to reproduce setup-only assertions, ablation studies, or appendix-only results unless they are essential to a headline claim.

## Rules

- Order steps logically: setup first, then execution, then verification
- Include 3-10 steps (enough to cover the headline claims, not exhaustive)
- The agent executing this plan will work on a writable copy of the repo at `{{ codebase_dir }}/`
- The agent may fix issues in the code to keep replication going (deprecated APIs, missing imports, configuration problems)
- If you find multiple entry points or experiments, prioritize the one that targets the headline claim
- Every result-producing step MUST have at least one claim ID in `verifies`. Setup-only steps may have an empty `verifies` list.
- **Validate each `verifies` entry.** For every claim ID you list in a step's `verifies`, re-read that claim's `verification` field. The step's `command_hint` must actually run a workflow that produces the specific evidence the verification field asks the verifier to inspect — not merely touch the same file or codepath. If a step doesn't exercise the claim's specific behavior, either modify `command_hint` to do so, or drop the claim ID from `verifies`. Example of the failure to catch: a claim asks for a comparison between two specific configurations of a procedure (e.g. one parameter fixed vs. that same parameter varied), the step description says only "run the analysis script," and the script hardcodes the fixed-parameter path so it never actually exercises the varied case. The step touches the relevant code area but never runs the second configuration, so the cross-reference is wrong — either modify the step to run both configurations, or drop that claim ID from `verifies`.
- Step outputs (files the commands produce) belong under the working copy at `{{ codebase_dir }}/`. Do not direct outputs into other pipeline directories (e.g. `{{ output_dir }}/analyze/`).
- NEVER include the paper's reported numerical result values in `expected_outcome`.

## Output

Save the plan to `{{ output_dir }}/analyze/replication_plan.json` with this format:

```json
{
    "environment": {
        "language": "the language(s) the implementation actually uses",
        "key_dependencies": ["list", "of", "main", "packages"],
        "setup_hints": "Toolchains to install and hardware to use (e.g. which steps should run on the GPU). Never pre-authorize reduced-scale runs here."
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

The pipeline reads that file; nothing else is captured.

Begin your analysis now.
