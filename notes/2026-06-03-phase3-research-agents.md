# Phase 3 — Manager research sub-agents (design + trajectory)

**Date:** 2026-06-03
**Branch:** `manager-research-agents` (stacks on `manager-retry-loop` = #76, which
stacks on #74/#75). Base for the PR is `main`.
**Spec:** `notes/2026-06-03-iterative-manager-design.md` §6 (research as invokable
sub-agents the manager calls); anti-leakage template from
`notes/2026-06-02-agentic-reflection-research.md` §5 (AI-Scientist: propose query
→ deterministic retrieval → use only validated returned items; gpt-researcher
grounding / mandatory per-claim citations).

> NOTE: the two referenced spec notes (`2026-06-03-iterative-manager-design.md`
> and the AI-Scientist study) were **not present on the `manager-retry-loop`
> branch checkout** — only `2026-06-02-agentic-reflection-research.md` and the
> Phase 2 trajectory note were. I built to the reflection note's anti-leakage
> §5 (which is the load-bearing constraint) and the Phase 2 code shape. If §6 of
> the iterative-manager note diverges from what I built, a follow-up tweak may be
> needed — flagged for human review.

## What this adds

The Phase 2 manager loop (`runner._replicate_with_manager_loop`) can now, on a
`revise` verdict that carries `research_requests`, invoke narrow research
sub-agents to find missing methodology/resources, and fold their (redacted)
findings into the re-run guidance. Two finders:

- **resource-finder** (`templates/research/resource_finder.md`) — locate a
  missing dataset/script/dependency: a download script, a URL in the
  paper/README, a mirror, an install recipe, the right package+version.
- **literature-finder** (`templates/research/literature_finder.md`) — locate an
  underspecified methodological detail or a standard reference implementation.

Each is a **separate provider invocation** (own prompt, web search/fetch access,
API keys stripped so it cannot run paper code), returning `{found, finding,
sources, notes}` — methodology/resources + provenance, never reported values.

## The three structural anti-leakage barriers (as implemented)

**(a) Intent allow-list — `research.honor_request`.** A request is honored only
if its structured `kind` is `resource` or `literature`. This is a *small
structured field check* on the request kind (explicitly the allowed deterministic
form), **not** keyword matching on the free-text `need`. An answer-seeking
request ("find the reported accuracy of X") cannot carry a valid
resource/literature kind, so it is rejected and recorded as rejected. The honor
decision lives in exactly one auditable function.

**(b) Answer-value redaction BEFORE injection — two layers, runner-driven.**
  - **Primary: LLM/agent judgment** (`templates/research/redactor.md`, run by
    `runner._redact_finding`). The redactor reads the finding and removes reported
    result/metric values **by judgment**, preserving methodology/resources +
    provenance. **No keyword/regex bank** decides "does this look like an answer"
    — that fragile pattern is banned and is not present anywhere in this PR.
  - **Belt-and-suspenders: exact known-value scrub** (`redact_known_values`). The
    ONLY deterministic redaction: an exact-string replacement of *known*
    `paper_value` strings (flattened from `PaperClaims` by `known_value_strings`).
    This is an objective string-containment fact ("does the finding literally
    contain this known answer"), never a guess at what an answer looks like. It
    runs on top of the LLM redactor's output.
  - **Fall-closed:** if the LLM redactor fails or returns unparseable output, we
    redact the *original* finding with the deterministic scrub rather than inject
    un-redacted text. The searcher and the replicate agent are separate roles with
    this redaction step strictly between them; the redactor agent never receives
    the known paper values (the deterministic scrub is the runner's own check).

**(c) Provenance-tagged injection + cheating monitor.**
`format_findings_for_guidance` renders each injected item **with its source
URL(s)**; the block is threaded into the re-run via `ManagerGuidance.
research_findings` and a `{% if manager_guidance.research_findings %}` block in
`templates/replication/session_instructions.md`. The existing post-verify
contextual-evaluation **cheating monitor** (`run_evaluation`) watches the
replication trace — now including the re-run trace — for copied values; unchanged
and still advisory.

## Bounds + config (Phase 0 pattern, no hardcoded configs)

`ResearchConfig.from_env()` reads `VERITAS_RESEARCH_MAX_CALLS` (default 2; `0`
disables research even when the loop is on). The cap bounds honored requests per
iteration; the overflow is logged as `dropped_for_cap`. Research is **opt-in with
the loop** (`max_iters > 1`) — it never runs on the `replicate`/benchmark path
(`max_iters == 1` returns before the loop body).

## Logging (workflow trajectory)

`runner._workflow_research_record` appends a `phase: "research"` record to
`.veritas/workflow.jsonl` per iteration with: honored requests, rejected requests
(intent gate), `dropped_for_cap`, each finding (post-redaction) with its
`redaction` block (`llm_removed`, `exact_hits`), and the exact `injected_guidance`
text. `WorkflowLog._rewrite_summary` renders all of this in `workflow.md`
(honored/rejected counts, per-finding source + redaction flags, rejected-by-gate
lines).

## Files

- `src/veritas/core/research.py` — NEW. Deterministic pieces: request parsing,
  intent gate, exact known-value scrub, findings + provenance formatting,
  `ResearchConfig`. No provider imports (pure, unit-testable).
- `src/veritas/core/runner.py` — `_run_research`, `_dispatch_research_agent`,
  `_redact_finding`, `_workflow_research_record`; wired into the loop's re-run
  branch (folds findings into `guidance.research_findings`).
- `src/veritas/core/manager.py` — `parse_manager_verdict` retains dict-shaped
  `research_requests` (was stripped in Phase 2); `ManagerGuidance.
  research_findings`; workflow.md research rendering.
- `src/veritas/core/config.py` / `config_env.py` (via existing `_env_int`) —
  research artifact filenames + path helpers.
- `src/veritas/templates/prompt_generator.py` — `generate_research_prompt`,
  `generate_research_redactor_prompt`.
- `templates/research/{resource_finder,literature_finder,redactor}.md`,
  `templates/manager/replication_review.md` (research_requests re-enabled),
  `templates/replication/session_instructions.md` (research-findings block).
- `.env.example` — `VERITAS_RESEARCH_MAX_CALLS` documented.
- `tests/test_research.py` — NEW (deterministic unit + runner structure tests +
  one full-loop integration test). `tests/test_manager.py` — updated the
  Phase-2 strip test to the Phase-3 retain behavior.

## Tests

`97 passed`. Coverage:
- Intent gate honors resource/literature, rejects answer-seeking/unknown kinds.
- Known-value flattening across scalar/range/table shapes; exact scrub removes
  only known values (no keyword guessing — a non-answer number is left alone).
- Provenance formatting includes source URLs, skips empty/errored findings.
- Bounds: default 2 / env override / `0` disables; per-iteration cap dispatches
  only `cap` requests, logs the rest as dropped.
- Runner wiring: answer-seeking request rejected (no dispatch); a finding with an
  embedded fake reported value → redactor (LLM fail → deterministic scrub) →
  value gone, methodology + provenance kept; all logged.
- **Full-loop integration** (`test_loop_research_findings_reach_rerun_guidance`):
  real `_replicate_with_manager_loop` → manager revise + resource request →
  resource-finder finding with `91.4` embedded → redaction → the re-run's
  `manager_guidance.research_findings` has the value scrubbed but keeps the
  source URL + dataset name; workflow log has a `research` record.

A live docker run was **not** performed (no LLM/web access in this environment);
the integration is covered by the stubbed full-loop test above.

## For human review (uncertainties)

1. **Spec drift:** §6 of the iterative-manager note wasn't on the branch (see top
   note). I followed the reflection note's §5 anti-leakage template and §6's
   summary as relayed in the task. Worth a diff against the real §6.
2. **#76 may still be under review** — this stacks on it; a rebase may be needed
   if `_replicate_with_manager_loop` / the verdict schema changes.
3. **Redaction trust model:** the LLM redactor is the semantic layer; the
   deterministic scrub only catches *known* `paper_value`s. A reported value the
   paper-claims extraction did not capture relies on the LLM redactor + the
   cheating monitor — by design (no keyword bank), but the residual risk is the
   LLM redactor's recall. The fall-closed-to-deterministic-scrub behavior bounds
   the worst case to "known values always removed".
4. **Provenance is mandatory:** an unsourced finding is now **discarded** by
   `format_findings_for_guidance` (matching the finder prompts' promise), so only
   auditable, source-tagged methodology can reach the re-run. If a useful finding
   ever lacks a URL it will be dropped — acceptable given the anti-leakage
   posture, but worth knowing.
