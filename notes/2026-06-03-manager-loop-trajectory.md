# Manager-loop live trajectory — Phase 2 smoke test

**Date:** 2026-06-03
**Branch:** `manager-retry-loop` (stacks on `phase2-base` = main + #74 + #75)
**Image:** locally-built `chicagohai/veritas:latest` with the Phase 2 code baked in.

## Test setup

- **Cell:** CB capsule `capsule-3449234` — *"Short-Term Temperature Forecasts Using a
  Convolutional Neural Network"* (ConvLSTM / multichannel-LSTM, TensorFlow 1.15 +
  Keras 2.3 + Python 3.7). Chosen as a harder cell than the 3849634 capsule: the
  pinned TF-1.15 / Py-3.7 stack does not install cleanly on the image's Python 3.12,
  so one-pass replication is plausibly weak.
- **Invocation:** mirrors the RB/CB harness call, with the loop ENABLED:
  ```
  ./veritas replicate --repo <capsule>/repo --claims <capsule>/claims_cb.json \
      --output <out> --provider claude --max-iters 3 \
      --replicate-timeout 2400 --verify-timeout 600 --analyze-timeout 600
  ```
  (`VERITAS_MAX_ITERS=3` also set; either route enables the loop.)
- 1 headline claim (`T0_cbb43c69`): run `visualize_results.ipynb` and convert to HTML,
  reporting `Error ConvLSTM` / `Error LSTM` numbers.

## What the replication agent did (so the manager had something to judge)

The replicate agent ran a genuinely hard, diligent pass: it `uv`-installed CPython
3.7.9, filtered the repo's Windows-only deps, fought and fixed two real resolver
errors (a non-existent `setuptools==49.6.0.post20200925` post-release; a
`tensorflow-estimator==1.14.0` vs. `tensorflow-cpu 1.15.0` conflict needing 1.15.1),
got TF 1.15.0 + Keras 2.3.0 importing, then executed `visualize_results.ipynb` on the
shipped pre-trained ConvLSTM/LSTM pickles and converted it to a 1.2 MB HTML with 19
embedded PNGs. **5/5 plan steps succeeded; 3/3 result steps emitted artifacts.** Real
metrics came out of cell 4: `Error ConvLSTM = 0.0769068`, `Error LSTM = 0.34893808`.

## What the manager actually did

### 1. Deterministic gate fired first (no LLM yet)

`compute_diligence_signals` ran over the evidence and returned **NOT diligent**, so the
run did **not** short-circuit to ACCEPT — it was handed to the LLM manager. The signals:

```
diligence=NOT diligent; steps=5/5; artifacts=3/3; placeholder?
  hard_negative: "steps narrated as skipped/abandoned: [1]"   (substring 'skip' in step 1's
                  description "skip Windows-only packages")
  advisory:      "possible silent-exception/placeholder in [2]" (the word 'placeholder' near
                  the repo's own 0-byte scaler pickle)
```

Both flags are deterministic keyword **false positives** — precisely the ambiguous case the
design routes to the independent critic instead of auto-deciding.

### 2. Independent manager review pass ran (fresh context, keys stripped)

Console: `Manager: signals ambiguous/negative — running independent review (iteration 1,
2 retries remaining)...`. The manager ran in `/workspace/output` with the API keys stripped
(it cannot run paper code), read `replication_log.json`, the diligence signals, and the
replication transcript, then wrote `replication/manager_review.json`.

### 3. Verdict: ACCEPT (diligent-but-divergent, confidence 0.9)

```
Manager verdict: ACCEPT (genuine=diligent-but-divergent, target=None, confidence=0.9)
Manager ACCEPTED replication at iteration 1.
```

The manager's `reason` is grounded and specific (excerpted): it cited the transcript
(lines ~20-90), correctly diagnosed **both** deterministic flags as false positives — (a)
the "skip" hit is the literal "skip Windows-only packages" in step 1's description, while the
transcript shows step 1 was a real ~220 s venv build that fixed two genuine resolver errors;
(b) the "placeholder" hit is the repo's own 0-byte `scaler.pckl` the notebook overwrites at
runtime, not an agent-fabricated stand-in — verified the notebook has **zero error cells**,
22 outputs incl. `image/png`, the real non-zero metrics above, and a 1,214,069-byte HTML with
19 PNGs and the required section headings, and concluded *"a clean, faithful inference
replication with honest reporting and no signs of corner-cutting."*

This is the exact calibration the spec asks for: **accept diligent-but-divergent work; do
not burn a re-run on keyword false positives.**

### 4. Loop termination

`should_stop` returned `accepted` at iteration 1, so the loop stopped immediately (no
archival, no invalidation, no hand-off) and the pipeline continued to assess → verify →
report. The headline Replication Score is computed deterministically and is unaffected by the
loop having run.

### Workflow-log excerpt (`<out>/.veritas/workflow.jsonl`)

```jsonl
{"iteration":1,"phase":"replicate","status":"completed","transcript_path":".../replication_transcript.jsonl",
 "signals":{"looks_diligent":false,"hard_negative_reasons":["steps narrated as skipped/abandoned: [1]"],
            "advisory_flags":["possible silent-exception/placeholder in [2]"],
            "summary_line":"diligence=NOT diligent; steps=5/5; artifacts=3/3; placeholder?"},
 "manager_verdict":null,"directive":null,"archived_attempt_path":null}
{"iteration":1,"phase":"manager_review","status":"accept",
 "manager_verdict":{"decision":"accept","deficiency_is_genuine":"diligent-but-divergent",
                    "target_phase":null,"confidence":0.9,"source":"llm","reason":"All 5 planned steps were
                    actually executed ... false positives ... clean, faithful inference replication ..."}}
```

The readable `workflow.md` renders the same trajectory (iteration, signals summary, decision,
genuineness, reason).

## The re-run path (revise → archive → guidance-injected re-run)

This cell **accepted on iteration 1**, so the live run did not exercise the revise branch.
That branch is demonstrated by the focused integration test
`tests/test_manager_loop_integration.py::test_loop_revise_then_accept_archives_and_injects_guidance`,
which drives the real `_replicate_with_manager_loop` with an injected deficient verdict and
asserts, end to end: the prior attempt is archived to `replication.attempt-1/` (not
overwritten); the directive is threaded into the re-run as `ManagerGuidance` (the second
`_replicate` call receives the guidance, the first does not); downstream `verify` state is
invalidated; and the workflow log records both reviews (`["revise","accept"]`) plus the
archived path. Sibling tests cover the cap hand-off, the no-progress terminator, and the
resume-skip-when-converged guard.

## Assessment against the rubric

- **Did the manager run and judge quality?** Yes — it ran as an independent pass and produced
  a detailed, transcript-grounded judgment, not a rubber stamp.
- **Deterministic signals computed and agreed with reality?** They computed correctly and
  *flagged* the run; the two flags were keyword false positives, which is exactly why the
  design defers ambiguous runs to the LLM rather than auto-revising. The manager resolved them.
- **Sound decision?** Yes — ACCEPT on a genuinely diligent, artifact-producing run is correct;
  a re-run would have wasted ~10 min for no gain.
- **Well-logged?** Yes — `workflow.jsonl` + `workflow.md` + `manager_review.json` are
  consistent with what happened and auditable.
- **Independence / anti-leakage / score determinism?** Manager ran fresh-context with keys
  stripped (couldn't run code); `research_requests` empty (Phase 3 deferred); the score path is
  untouched by the loop.

**One thing to flag for human review:** the deterministic diligence signals are keyword-based
and produced two false positives here (`skip` in a step description; `placeholder` near a repo
artifact). That's by design (conservative → defer to the LLM), and the manager handled it well,
but if these false positives are common across cells, every run will incur an LLM manager call
even when the work is clean — worth tightening the diligence keyword patterns in a later pass so
more clean runs hit the zero-cost deterministic ACCEPT.
