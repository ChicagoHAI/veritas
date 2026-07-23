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
        |         With --max-iters > 1, a manager reviews the attempt and can
        |         send it back for another pass with new instructions.
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
`not_attempted=0.0`) with tier weights `headline=3, supporting=2`.
`not_applicable` claims are excluded.

The score is computed by code, not by a model. For numeric and table claims the
verify phase splits in two: the LLM **comparator** extracts the value the run
produced, and a deterministic **grader** decides the verdict from that value
against the paper value and a declared tolerance. Each verdict records how it was
graded. Qualitative and figure claims, which have no number to compute on, keep
the comparator's judgment.

## Commands

```bash
./veritas --paper p.pdf --repo ./myrepo     # full pipeline (the default)
./veritas full --paper p.pdf --repo ./myrepo  # same thing, named
./veritas replicate --repo ./myrepo         # replication only, for benchmarking
./veritas replicate --paper p.pdf --repo ./myrepo --check-citations  # opt-in citation check
./veritas check-citations ./replicate-dir                            # standalone: run on a finished run
./veritas replicate --paper p.pdf --check-citations --check-citations-faithfulness all  # widen faithfulness scope
./veritas evaluate ./myrepo/replicate       # add the manager + report to an existing run
./veritas report ./myrepo/replicate         # re-render the report (no LLM)
./veritas estimate --paper p.pdf --repo ./myrepo  # resource/cost estimate, runs nothing
./veritas shell                             # interactive container
./veritas setup                             # one-shot prereqs + image + login + .env
./veritas config | login                    # manage .env keys | provider CLI auth
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

- **`estimate`** reads the paper and repo and reports what a run would need
  (GPU, external LLM APIs, rough compute class) without running the pipeline.
  `replicate --dry-run` does the same as part of a normal invocation.

Run `./veritas replicate --help` for the full option list.

### Retrying a weak replication (`--max-iters`)

By default Veritas makes one replication attempt. With `--max-iters N` (N > 1),
a manager reviews each attempt before verification and decides whether to accept
it or send it back with new instructions, up to a hard cap of N. The manager sees
objective execution facts (which planned steps ran, exit codes, which declared
outputs exist, repeated commands) — never the paper's claimed values. When it
needs something the run lacked, such as a missing dataset or an underspecified
method, it can dispatch narrow research sub-agents whose findings are scrubbed of
known result values before they reach the retry. Each attempt is archived, and
the full decision trail is written to `.veritas/workflow.md`.

### Input modes

`--mode` (auto-detected by default):
- `full` — paper + repo. Claims from the paper; replication runs the repo.
- `paper-only` — paper only. Veritas writes the code from the paper, then runs it.
- `repo-only` — repo only. Claims come from the README.

Other inputs: `--claims path.json` supplies hand-authored claims and skips
extraction. `--data dir/` mounts a read-only data directory at `/workspace/data/`
so the agent uses local files instead of fetching from the network.

### Citation check (opt-in)

`--check-citations` runs an advisory reference check after verification: it
extracts the paper's reference list and confirms each cited work exists and is
described correctly (authors, venue, year, identifiers) against free scholarly
databases (Crossref, OpenAlex, Semantic Scholar, DBLP, arXiv). It flags
fabricated references and metadata errors such as a published paper cited as an
arXiv preprint. It is advisory and does not change the Replication Score, and it
requires `--paper`. The method is adapted from the
[refchecker](https://github.com/markrussinovich/refchecker) project (MIT).
The check also verifies, for the paper's main claims, whether the cited source supports what the paper attributes to it. Verdicts are `supported`, `partially_supported`, `contradicted`, or `not_mentioned`, each grounded in a verbatim quote. An independent audit pass re-checks flagged verdicts and can only soften a flag it cannot confirm (it never escalates), so the report shows the final reconciled verdicts. Use `--check-citations-faithfulness all` to check every claim-bearing citation instead of only the main ones. The check runs inline during `replicate --check-citations`, or standalone on a finished run via `check-citations <replicate-dir>` (which recovers the paper path from the run's saved config).

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
./veritas-host evaluate ./run-1              # same pipeline subcommands
```

The host wrapper stages the same workspace the container would and runs the same
pipeline. It resolves the Python that has the deps (an installed `veritas`, the
repo's `.venv`, or `uv run`). It supports the pipeline subcommands (`full`,
`replicate`, `estimate`, `evaluate`, `report`, `check-citations`) but not the
image-lifecycle ones (`shell`, `setup`, `build`, `update`, `status`), which are
docker-only concerns.

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

## Output structure

```
replicate/
├── analyze/        paper_claims.json, replication_plan.json, resource_estimate.json (+ transcripts)
├── replication/    codebase/ (patched copy), codebase.diff, replication_log.json, evidence_summary.json
├── assess/         fix_severity.json
├── verify/         <claim_id>.json (per claim, with the grading rule), verdicts.json, replication_score.json
├── evaluation/     contextual_evaluation.json  (the manager's notes; product runs only)
├── report/         replication_report.{html,pdf,md}
├── prompts/        rendered prompts (debug)
├── resource_usage.json   wall time, tokens, disk, approximate cost
└── .veritas/       pipeline_state.json (resume checkpoint)
```

With `--max-iters > 1`, each superseded attempt is archived alongside as
`replication.attempt-N/`, and the manager's decision trail is written to
`.veritas/workflow.md` (plus `workflow.jsonl`).

## Resuming runs

After each phase, Veritas writes its state to `<output>/.veritas/`. Re-invoking
against the same `--output` directory skips completed phases. Verify resumes per
claim. Pass `--restart` to start fresh.

## Development

```bash
pip install -e ".[dev]"
pytest
```

The suite covers the deterministic layers — the score computation, the grader,
the bibliographic resolver, the manager loop, and the execution-facts pass.

## Acknowledgments

- Built upon research from [NeuriCo](https://github.com/ChicagoHAI/NeuriCo).
