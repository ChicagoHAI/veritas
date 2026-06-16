# Veritas review modes — how the three modes work

Veritas reviews a scientific paper at three increasing levels of engagement. All
three produce the **same two outputs** — an **in-line review** (comments anchored
to the paper) and a **referee report** — but they differ in *how much veritas
engages with the work*, and therefore in what the comments and report can say.

## The two axes

A "mode" is really two independent settings:

| Axis | Flag | Values | Meaning |
|---|---|---|---|
| **Inputs** | `--paper`, `--repo`, `--data` | paper alone, or paper + code/data | what veritas is given |
| **Depth** | `--depth` | `read` \| `run` | whether veritas *executes* anything |

The three demo modes are points in that space:

| # | Name | Inputs | Depth | What veritas does |
|---|------|--------|-------|-------------------|
| **1** | Paper only | `--paper` | `read` | Reads the paper only. No code. |
| **2** | Paper + code/data (read) | `--paper --repo [--data]` | `read` | Reads the paper **and** the code/data, but runs nothing. |
| **3** | Full run (execution) | `--paper --repo [--data]` | `run` | Actually **executes** the code and checks each claim against produced output. |

```
            engagement increases  ───────────────────────────────▶
  Mode 1                    Mode 2                       Mode 3
  read the paper            read paper + code            run the code
  "is this plausible        "does the code that          "do the results
   and well specified?"      ships actually support       actually reproduce
                             the claims?"                  when executed?"
```

---

## Mode 1 — Paper only (`--depth read`, no repo)

```bash
./veritas --paper paper.pdf --mode paper-only --depth read --inline
```

**What happens.** Veritas extracts the paper's checkable claims, then a
reading-based reviewer judges, *from the paper alone*, how reproducible each
claim is — is the method specified well enough to re-implement, are
hyperparameters/data/procedures stated, are there red flags. Nothing is
executed, so there is no "score" — the headline is a qualitative
**Reproducibility Assessment** (overall risk + axes: specification, code
coverage [unknown here], data availability).

**Pipeline:** `analyze` (extract claims) → `static review` (read-only
per-claim assessment) → `report` + `inline`.

**In-line review contains:** OpenAIReview-engine review comments (technical /
logical issues) **+** veritas per-claim *reproducibility* comments (supported /
partial / unsupported, with the reasoning), anchored where each appears.

**Use it for:** a fast, cheap first-pass review when you only have the PDF.

---

## Mode 2 — Paper + code/data, read-only (`--depth read` with a repo)

```bash
./veritas --paper paper.pdf --repo ./code --depth read --inline
```

**What happens.** Same as Mode 1, but the reviewer also **reads the provided
code and data statically** (without running them). For each claim it traces
*which file/function would produce it*, whether that code is present and
complete, and whether the shipped data covers the claim. The Reproducibility
Assessment now reports real **code coverage** and **data availability** (no
longer "unknown"), and comments point at specific files.

**Pipeline:** `analyze` → `static review` (now code-aware) → `report` +
`inline`.

**In-line review contains:** OpenAIReview review comments **+** veritas
*reproducibility* comments that cite code locations and call out gaps (missing
scripts, absent datasets, unseeded randomness).

**Use it for:** "the authors shared code — is it actually enough to reproduce
the paper, without me running it?" This is the artifact-evaluation pass.

> In the demo, Mode 2 on the Cooperation paper differs from Mode 1 precisely
> because it read the repo: it credits `main_model_Python/demo.py` for
> reproducing the headline numbers and flags the missing empirical networks and
> unseeded Monte-Carlo — turning several "partial (from the paper)" judgments
> into code-grounded ones.

---

## Mode 3 — Full run (`--depth run`, the execution-grounded review)

```bash
./veritas --paper paper.pdf --repo ./code --depth run --inline --evaluate
```

**What happens.** Veritas actually **runs the methodology** inside a sandbox: it
plans the replication, executes the code (fixing environment/API issues so it
can proceed), collects the evidence the run produced, and then — per claim — an
independent verifier compares the produced value to the paper's value. A
deterministic grader decides `match / partial / no_match` for numeric claims,
and the results aggregate into a single tier-weighted **Replication Score**. A
separate evaluation manager then writes the referee narrative.

**Pipeline:** `analyze` → `plan` → `replicate` (execute + fix) → `assess fixes`
→ `verify` (per claim) → `evaluate` (manager) → `report` + `inline`.

**In-line review contains:** OpenAIReview review comments **+** veritas
*replication* comments — each claim's verdict (reproduced / partially / did not
reproduce) with the **replicated value vs the paper value** surfaced inline.

**Use it for:** the strongest evidence — did the results actually come out when
the code was run? This is veritas's headline capability.

---

## The two outputs (same for every mode)

1. **In-line review** (`inline/inline_review.html`, and the richer
   `inline/oar_review.html`): the paper on the left, color-coded comments on the
   right, each anchored to the exact paragraph it's about. Comments come from
   **two sources, kept visually distinct**:
   - **veritas** (green chips): reproducibility assessments (modes 1–2) or
     replication verdicts (mode 3) for each extracted claim.
   - **OpenAIReview engine** (blue chips): technical / logical / reproducibility
     review comments from the vendored OpenAIReview progressive reviewer.

2. **Referee report** (`report/replication_report.html` + `.pdf` + `.md`): the
   headline verdict (Reproducibility Assessment for read modes, Replication
   Score for the full run), the structured verdict sections, strengths /
   obstacles / recommendation, and the per-claim table.

## How it's built (architecture)

```
paper [+ code/data]
      │
      ├─ claim extraction  (veritas)
      ├─ read review (modes 1–2)  /  execute + verify (mode 3)   → veritas comments + verdict
      └─ OpenAIReview progressive reviewer (vendored)            → review comments
                                   │
                          one ReviewBundle  (canonical: paragraphs, comments,
                          verdict sections, score/assessment)
                                   │
              ┌────────────────────┴─────────────────────┐
        OpenAIReview viewer JSON              sai-web demo JSON
        (standalone inline.html)             (the web demo)
```

The single `ReviewBundle` is veritas's canonical output; thin exporters render
it to the standalone viewer and to the web demo, so the same engine powers a
static demo today and a live web demo later.

## Cost / engagement, at a glance

| Mode | Executes code? | Relative cost | Headline output |
|---|---|---|---|
| 1 Paper only | no | low | Reproducibility Assessment (risk) |
| 2 Paper + code (read) | no | low–medium | Reproducibility Assessment (code-aware) |
| 3 Full run | **yes** | higher (depends on the paper) | Replication Score + per-claim verdicts |

The review-comment LLM (OpenAIReview engine) is selected with
`VERITAS_REVIEW_MODEL` (default `openai/gpt-4o`) and `REVIEW_PROVIDER`, reading
API keys from the shared `.env`. PDF parsing itself needs no key.
