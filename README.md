# Veritas

**A Replication Agent for Evaluating Scientific Reproducibility**

Veritas checks whether a scientific paper's results can actually be reproduced.
Give it a paper and/or a code repository. It runs the methodology, fixes what it
takes to make the code run, checks each of the paper's claims against the output
it actually produced, and writes a human-readable report with a single
Replication Score.

Correctness is the floor, not the goal. Veritas separates the work of *running*
the science from the work of *judging* it: a replication agent reproduces the
results, and a separate evaluation manager reviews the whole run and writes the
report. The agent that produces a number never grades it.

## How it works

```
Input (Paper PDF and/or Repository)
        |
  1. ANALYZE     Extract the paper's specific, checkable claims (paper_claims.json).
        |        From the paper, the repo README, or a supplied --claims file.
  2. CODEGEN      (paper-only mode) Write the methodology into a fresh codebase.
        |
  3. PLAN         A claim-aware plan. With a real repo, run the repo's own code
        |         rather than reimplementing it.
  4. REPLICATE    Run the plan on a writable copy of the code. Fix issues to keep
        |         going. Report what was produced, never tune toward paper values.
        |         Optionally retried under a manager loop (--max-iters).
  5. ASSESS       Rate each applied fix (minor / major / critical).
        |
  6. VERIFY       Per claim: an LLM comparator extracts the produced value; a
        |         deterministic, LLM-free grader decides match / partial / no_match.
        |         Aggregate into a tier-weighted Replication Score.
        |
  7. EVALUATE     (product runs) A manager reviews the whole run: which claims
        |         matter, how well it reproduced, what didn't and why, and what
        |         the provided code got wrong. It also flags integrity concerns.
        |
  REPORT          A styled HTML report (+ matching PDF, + markdown) with a
                  gist-first verdict card, the manager's narrative, and the
                  deterministic tables underneath.
```

Veritas extracts each paper's own claims and checks them one at a time against
the evidence the run produced. The **Replication Score** is a tier-weighted
average of verdict values (`match=1.0`, `partial=0.5`, `no_match=0.0`,
`not_attempted=0.0`) with tier weights `headline=3, supporting=2, setup=1`.
`not_applicable` claims are excluded.

The score is computed by code, not by a model. For numeric and table claims the
verify phase splits in two: the LLM **comparator** extracts the value the run
produced, and a deterministic **grader** decides the verdict from that value
against the paper value and a declared tolerance. Each verdict records how it was
graded. Qualitative and figure claims, which have no number to compute on, keep
the comparator's judgment.

### Iterative replication (opt-in)

By default the replicate phase runs once. Pass `--max-iters N` (or set
`VERITAS_MAX_ITERS`) to turn on a manager-controlled retry loop. After each
attempt an independent manager reviews the execution facts (which steps ran, what
they produced, where it got stuck) and the trajectory, then either **accepts** the
run or **sends it back to revise** with specific guidance, up to N attempts. The
manager runs with a fresh context and no API keys, so it cannot run the paper's
code or see reported values — it only reviews and directs.

A faithful-but-divergent result is accepted, not retried; only a real deficiency
(a step that never ran, a missing artifact, a premature stop) triggers a revise.
On a revise the manager can dispatch narrow research sub-agents to find a missing
dataset, script, or underspecified method; their findings are stripped of any
reported values and tagged with their source before feeding the next attempt.
Each attempt is archived under `replication.attempt-N/`, every decision is logged
to `.veritas/workflow.{jsonl,md}` and shown in the report, and the Replication
Score stays deterministic no matter how many attempts ran.

`replicate` with no `--max-iters` (the benchmark path) stays a single pass.

## Commands

```bash
./veritas --paper p.pdf --repo ./myrepo     # full pipeline (the default)
./veritas full --paper p.pdf --repo ./myrepo  # same thing, named
./veritas replicate --repo ./myrepo         # replication only, for benchmarking
./veritas evaluate ./myrepo/replicate       # add the manager + report to an existing run
./veritas report ./myrepo/replicate         # re-render the report (no LLM)
./veritas extract-plan paper.pdf            # plan-only sketch
./veritas shell                             # interactive container
./veritas setup                             # one-shot prereqs + image + login + .env
./veritas status                            # dashboard
./veritas build | update                    # build locally | pull latest image
```

- **`full`** (and the bare `./veritas <inputs>`) runs everything: replicate +
  evaluate + the styled report. This is the normal way to use Veritas.
- **`replicate`** stops after verify and skips the evaluation manager. This is
  the lean mode for benchmarking, where an external harness scores the verdicts.
- **`evaluate <dir>`** runs the manager and renders the report on a directory a
  prior `replicate` produced, without re-running the pipeline. Replicate once,
  evaluate later.

Run `./veritas replicate --help` for the full option list.

### Input modes

`--mode` (auto-detected by default):
- `full` — paper + repo. Claims from the paper; replication runs the repo.
- `paper-only` — paper only. Veritas writes the code from the paper, then runs it.
- `repo-only` — repo only. Claims come from the README.

Other inputs: `--claims path.json` supplies hand-authored claims and skips
extraction. `--data dir/` mounts a read-only data directory at `/workspace/data/`
so the agent uses local files instead of fetching from the network.

## The report

Every run writes three files to `<output>/report/`:
- `replication_report.html` — the primary, human-facing report. Self-contained;
  open it by double-clicking. A verdict card up top (score gauge, a
  reproduced / partially reproduced / not reproduced badge, a one-line bottom
  line, key counts), a claims-at-a-glance bar, the manager's summary and
  analysis, a limitations section, and collapsible details.
- `replication_report.pdf` — the same report as a PDF, for sharing.
- `replication_report.md` — the machine-readable source.

The verdict card and all tables are computed deterministically from the score and
verdicts. The narrative is written by the evaluation manager and is advisory: it
explains the result but never changes the score. If the manager pass did not run
(e.g. a `replicate`-only benchmark run), the report still renders from the
deterministic parts.

## Installation

Two ways to run Veritas. **Docker is the default**; **host mode** is for
environments without docker (HPC clusters, managed compute).

### Docker (default)

```bash
git clone https://github.com/ChicagoHAI/veritas.git
cd veritas
./veritas setup                              # prereqs, image, login, .env
./veritas --paper your_paper.pdf --repo your_repo/
```

The first run pulls `ghcr.io/chicagohai/veritas:latest` from GitHub Container
Registry. The image bakes in Python, the provider CLIs, R + LaTeX, and WeasyPrint
(for the PDF). Apple Silicon: the wrapper passes `--platform linux/amd64`
automatically.

### Host mode (no docker)

```bash
pip install -e .                             # one-time, into a venv
./veritas-host --paper paper.pdf --repo ./code --output ./run-1
./veritas-host evaluate ./run-1              # same subcommands as the docker wrapper
```

The host wrapper stages the same workspace the container would and runs the same
pipeline. It resolves the Python that has the deps (an installed `veritas`, the
repo's `.venv`, or `uv run`).

## Replication API keys (`.env`)

Veritas's own provider (Claude / Codex / Gemini CLI) signs in via OAuth. Papers
that call LLM APIs from inside their own code need raw keys (e.g.
`OPENAI_API_KEY`) in a `.env` file at the repo root:

```bash
cp .env.example .env && chmod 600 .env
$EDITOR .env
```

Keys are passed into the container only on the replicate phase, which runs the
paper's code. Every other phase gets a stripped environment.

## Configuration

Most defaults are overridable per run through `VERITAS_*` variables in the same
`.env`, with no code edits. Resolution, highest wins: a CLI flag (where one
exists) → the `VERITAS_*` env var → the built-in default. `.env.example` lists
them all.

- **Grading tolerances** — `VERITAS_GRADE_MATCH_REL` (0.05), `VERITAS_GRADE_PARTIAL_REL` (0.30), the σ bands, range overlap.
- **Tier weights** — `VERITAS_TIER_WEIGHT_HEADLINE` (3), `_SUPPORTING` (2), `_SETUP` (1).
- **Retry loop** — `VERITAS_MAX_ITERS` (1), `VERITAS_RESEARCH_MAX_CALLS` (2; `0` disables research).
- **Per-phase timeouts** — `VERITAS_ANALYZE_TIMEOUT`, `VERITAS_REPLICATE_TIMEOUT`, `VERITAS_VERIFY_TIMEOUT`, and the rest (unset = no timeout).

## Claim types and tiers

Five shape-typed claim categories; each claim carries a tier that sets its weight.

| Claim Type | Use for | How it's graded |
|-----------|---------|-----------------|
| **scalar** | A single number | Deterministic: rel. error (5% match, 30% partial), or ±1σ/±2σ when an uncertainty is given |
| **scalar_range** | A range or set | Deterministic: containment / overlap |
| **table** | A table of values | Deterministic: per-cell, keyed by the exact question labels |
| **qualitative** | A described behavior | LLM paraphrase-match |
| **figure** | A produced figure | LLM structural match (reads the figure file) |

| Tier | Weight | Use for |
|------|--------|---------|
| **headline** | 3 | The paper's central result (usually 1-3 per paper) |
| **supporting** | 2 | Intermediate measurements, secondary figures |
| **setup** | 1 | Borderline configuration assertions (rare) |

## Output structure

```
replicate/
├── analyze/        paper_claims.json, replication_plan.json (+ transcripts)
├── replication/    codebase/ (patched copy), codebase.diff, replication_log.json,
│                   evidence_summary.json, diligence_signals.json,
│                   replication.attempt-N/ (archived attempts; iterative runs only)
├── assess/         fix_severity.json
├── verify/         <claim_id>.json (per claim, with the grading rule), verdicts.json, replication_score.json
├── evaluation/     contextual_evaluation.json  (the manager's notes; product runs only)
├── report/         replication_report.{html,pdf,md}
├── prompts/        rendered prompts (debug)
└── .veritas/       pipeline_state.json (resume checkpoint),
                    workflow.{jsonl,md} (manager decisions; iterative runs only)
```

## Resuming runs

After each phase, Veritas writes its state to `<output>/.veritas/`. Re-invoking
against the same `--output` directory skips completed phases. Verify resumes per
claim. Pass `--restart` to start fresh.

## Acknowledgments

- Built upon research from [NeuriCo](https://github.com/ChicagoHAI/NeuriCo).
