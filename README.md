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
./veritas evaluate ./myrepo/replicate       # add the manager + report to an existing run
./veritas report ./myrepo/replicate         # re-render the report (no LLM)
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

### Per-step models and the OpenRouter provider

Every pipeline bucket (`analyze`, `codegen`, `replicate`, `assess`, `verify`,
`evaluate`) can run on its own engine, written as `[provider:]model`:

```bash
# pin one model globally
./veritas replicate --repo ./my-project --provider claude --model claude-opus-4-8

# vary the replicator, pin the judge (comparable scores across sweeps)
./veritas replicate --repo ./my-project \
    --replicate-model claude-opus-4-8 \
    --verify-model openrouter:openai/gpt-5.5

# run the whole pipeline on an OpenRouter model (needs OPENROUTER_API_KEY)
./veritas replicate --repo ./my-project \
    --provider openrouter --model moonshotai/kimi-k2.6
```

The same settings work file-based via `VERITAS_MODEL` /
`VERITAS_<BUCKET>_MODEL` in `.env` (flags win). Changing only
`--verify-model` on an existing output directory re-runs verification
alone — cheap re-adjudication under a different judge.

**Auth.** Provider API keys are read from your host shell and forwarded
into the container (a key set only in `.env` is forwarded too; the host environment wins when both are set): `OPENROUTER_API_KEY`, `ANTHROPIC_API_KEY`,
`ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_BASE_URL`, `OPENAI_API_KEY`,
`GEMINI_API_KEY`, `GOOGLE_API_KEY`. For claude/codex/gemini an API key is
accepted as an alternative to `./veritas login <provider>`; OpenRouter is
API-key-only (there is no login flow). Note: when an
`ANTHROPIC_API_KEY` is visible to claude, billing is expected to follow
the key rather than your subscription (verify on your console).

**OpenRouter routes.** `--provider openrouter` runs opencode, which
reaches any OpenRouter slug. Two pass-through alternatives need no veritas
support: codex reads `[model_providers.openrouter]` from your own
`~/.codex/config.toml` (copied into the container), and Claude Code
honors `ANTHROPIC_BASE_URL`/`ANTHROPIC_AUTH_TOKEN` pointed at OpenRouter's
Anthropic-compatible endpoint (Anthropic-family models, best-effort).

**Fusion.** OpenRouter's Fusion is reachable as a normal slug:
`--verify-model openrouter:openrouter/fusion`. Fusion's panel and judge
have always-on web search, so veritas prints a leakage warning if you
configure it (or any `:online` variant) for `replicate`/`codegen`.
As of 2026-07-02 the `openrouter/fusion` slug is confirmed to exist on OpenRouter, but driving veritas's file-reading tool loop through Fusion has not been verified end to end.

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
├── analyze/        paper_claims.json, replication_plan.json (+ transcripts)
├── replication/    codebase/ (patched copy), codebase.diff, replication_log.json, evidence_summary.json
├── assess/         fix_severity.json
├── verify/         <claim_id>.json (per claim, with the grading rule), verdicts.json, replication_score.json
├── evaluation/     contextual_evaluation.json  (the manager's notes; product runs only)
├── report/         replication_report.{html,pdf,md}
├── prompts/        rendered prompts (debug)
└── .veritas/       pipeline_state.json (resume checkpoint)
```

## Resuming runs

After each phase, Veritas writes its state to `<output>/.veritas/`. Re-invoking
against the same `--output` directory skips completed phases. Verify resumes per
claim. Pass `--restart` to start fresh.

## Acknowledgments

- Built upon research from [NeuriCo](https://github.com/ChicagoHAI/NeuriCo).
