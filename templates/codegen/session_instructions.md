# Code Generation Session

You are implementing a scientific paper's methodology from scratch in
an empty codebase. By the end of this session, the directory at
`/workspace/output/replication/codebase/` must contain a runnable
implementation of the paper's methodology.

## Inputs

- Paper PDF at: `{{ paper_path }}`
- Output directory: `{{ output_dir }}`
- Your working directory: `/workspace/output/replication/codebase/` (starts empty)

{% if has_data %}- Pre-positioned data at: `/workspace/data/` (read-only). These files
  are the user-supplied inputs for this paper.

{% endif %}You do not have access to the original repository, if one exists.
Implement everything from the paper.

## Workflow

Follow this four-step structure. Take time on each step; do not rush.

### 1. Explore

Read the paper carefully. Prioritize:

- **Methodology / Methods** sections — the procedures you will implement.
- **Setup / Experimental setup / Data** sections — hyperparameters,
  dataset specs, initial conditions.
- **Model architecture / Algorithms** — what to build.

You may also skim Results and Discussion sections for context, but
do not memorize numerical results for hardcoding (see Self-Review).

### 2. Plan

Outline the file structure of your codebase before writing any code:

- What modules do you need?
- What is their dependency order?
- Where will entry points live?
- What dependencies (Python packages) are needed?

Track dependencies in `pyproject.toml` or `requirements.txt` (your
choice; pick one and be consistent).

### 2.5. Capture the plan to disk

Before writing code, write `codegen_plan.json` at the codebase root with
your decisions so they are inspectable and machine-readable. Schema:

```json
{
  "files": [
    {"path": "src/model.py", "responsibility": "..."},
    {"path": "src/dataset.py", "responsibility": "..."}
  ],
  "dependency_order": ["src/dataset.py", "src/model.py", "..."],
  "entry_points": ["main.py"],
  "shared_state": "What modules pass between them, e.g. 'Dataset returns (X, y) tuples consumed by trainer'.",
  "ambiguities": [
    {
      "question": "Paper says 'we use a small batch size' without naming a value.",
      "assumption": "Defaulted to batch_size=32, configurable via config.yaml."
    }
  ]
}
```

The `ambiguities` field is the place to flag every point where the paper
underspecifies methodology and you had to make a judgment call. List the
question and the assumption you took. Downstream phases use this to
distinguish "paper-underspecified" from "agent-misimplemented" outcomes.

### 3. Implement

Write the code, module-by-module. Guidelines:

- Prefer small, focused files. One clear responsibility per file.
- Use the paper's own variable names where natural.
- **Extract every paper-stated hyperparameter into `config.yaml` at the
  codebase root.** Use one section per logical group: `training:` (learning
  rate, batch size, epochs, optimizer settings, seeds), `model:` (layer
  sizes, activation choice, dropout), `data:` (dataset name, split sizes,
  preprocessing knobs), and any methodology-specific group (`sampling:`,
  `mcmc:`, etc.). Reference values from `config.yaml`; do not hardcode
  hyperparameters in `.py` files. A code reader should be able to audit
  every paper-stated input by reading one file.
- Set up dataset paths and other inputs as configuration the methodology
  calls for; don't hardcode anything that needs to be computed.
- You may install packages, create directories, and structure the
  repo as you see fit.
- Do not run the methodology end-to-end. That is a later phase.
  Your job is to produce the codebase; verifying it imports cleanly
  is part of Self-Review, but a full training/inference run is out
  of scope.

### 4. Self-Review

Before declaring done, complete every item in this audit:

#### a. Re-read methodology, then audit faithfulness

Open the paper again. For each algorithm / procedure described:

- Find the corresponding code.
- Confirm it implements what the paper says, not what you assumed.
- Fix any divergence.

#### b. Inputs-vs-outputs audit (anti-leakage)

The paper distinguishes two kinds of numbers: **inputs** (configuration
prescribed by the methodology — hyperparameters, batch size, dataset
size, model architecture, version pins, simulation initial conditions)
and **outputs** (results the paper reports — accuracy, posterior
estimates, table values).

- **Inputs** are allowed as constants in your code.
- **Outputs** must be **computed by your code**, not hardcoded.

Test for each numerical constant: *would this number change if the
experiment were re-run? If yes, it is an output and must be computed.
If no, it is an input and may be hardcoded.*

Examples:

| Paper says | Type | Hardcode? |
|---|---|---|
| "learning rate 2e-5, batch size 32, 3 epochs" | Input | Yes |
| "trained on 161 systems from Albrecht+22" | Input | Yes (dataset spec) |
| "model achieved 92.3% accuracy" | Output | No — compute it |
| "posterior mean w_1 = 0.719 ± 0.085" | Output | No — compute it |
| "max_treedepth=13" | Input | Yes |
| "rhat values were all <1.01 after sampling" | Output | No — compute it |

Scan every numerical constant in your code and apply the test. If a
constant matches a paper-reported result, replace it with code that
computes the value from the methodology.

#### c. Import sanity

For each Python module you wrote, run:

```bash
python -c "import <module_name>"
```

Fix any `ImportError`, `SyntaxError`, or `ModuleNotFoundError`. The
codebase must be importable end-to-end before you exit.

#### d. Dependency completeness

Re-open `pyproject.toml` / `requirements.txt`. Are all imports listed?
Do versions pin to what the paper used (when stated)?

#### e. Config audit

Open `config.yaml`. For each paper-stated hyperparameter, confirm:

- It lives in `config.yaml`, not as a literal in a `.py` file.
- Its value matches the paper. (If the paper specifies a range or "we
  tried X, Y, Z", pick the value used for the paper's headline result
  and record the alternatives in `codegen_plan.json["ambiguities"]`.)
- Code that needs the value reads it from `config.yaml`, not from a
  default function argument or a module-level constant.

If you find a paper-stated hyperparameter not in `config.yaml`, move it.

#### f. Ambiguity audit

Open `codegen_plan.json`. For each entry in `ambiguities`, confirm the
chosen assumption is reflected in the code (typically a `config.yaml`
value) and that the assumption was a reasonable best-guess given the
paper. If, during implementation, you encountered an underspecification
you didn't record, add it now. Future phases rely on this list.

## Hard constraints

- Write into `/workspace/output/replication/codebase/`, nowhere else.
- Dependencies tracked in `pyproject.toml` or `requirements.txt`.
- `codegen_plan.json` and `config.yaml` both live at the codebase root.
- Do not commit (no `git commit`) — the host-side EXIT trap captures
  the diff against an empty initial state.
- Do not run the methodology end-to-end; that is the next phase.

## When you are done

Print "CODEGEN COMPLETE" on its own line and exit.
