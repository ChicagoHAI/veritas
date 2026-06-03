"""Deterministic diligence signals over replicate evidence.

Phase 1 groundwork for the iterative-manager loop
(`notes/2026-06-03-iterative-manager-design.md` §4.2). These are *pure*
functions: given the replicate evidence (`ExecutionEvidence`, parsed from
`replication_log.json`), the codebase diff, and the replication plan, they
compute cheap, structured signals about whether the replication run looks
diligent.

The signals mirror the deterministic gates real coding agents run BEFORE any
judge call (OpenHands ``AgentFinishedCritic`` / ``StuckDetector``, cline
``completion_without_submit`` / ``LoopDetectionTracker``, aider test-driven
reflection). They are *compute + log only* in this phase — nothing here reads
or changes control flow. A later manager phase will gate on them.

Design notes:
- Deterministic and conservative: a signal fires only on textual/structural
  evidence we can point at, never on a guess. When something is genuinely
  ambiguous to a deterministic check (e.g. "is this divergence genuine?"), we
  do NOT decide it here — we leave it for the LLM manager and say so.
- ``looks_diligent`` is an overall convenience flag. It is intentionally
  lenient: it flips to ``False`` only on strong negatives (skipped/excepted
  steps, missing artifacts on result steps, premature stop, stuck/looping).
  Soft hints (downsizing, silent-exception keywords) are surfaced as detail
  but do not by themselves flip the overall flag — they are advisory evidence
  for the manager.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .models.replication import ExecutionEvidence, ReplicationPlan, StepOutcome

# --- Keyword / pattern vocabularies (kept here so they're testable) ---------

# Hints that a step was downsized vs. the methodology's intended scale.
# Matched case-insensitively against step notes/stdout/commands.
_DOWNSIZE_PATTERNS = [
    r"\bdownsiz(?:e|ed|ing)\b",
    r"\bdown-?scal(?:e|ed|ing)\b",
    r"\bto[ -]?example\b",
    r"\btoy\b",
    r"\bsubsample[ds]?\b",
    r"\bsub-?set(?:ted|ting)?\b",
    r"\bsmaller (?:grid|model|dataset|sample|subset)\b",
    r"\breduced (?:the )?(?:number of |#? ?)?(?:epochs?|iterations?|steps?|samples?|grid)\b",
    r"\bfewer (?:epochs?|iterations?|samples?|seeds?)\b",
    r"\bonly (?:ran|run|trained|using|a few|one|1|2|3) (?:epochs?|iterations?|samples?|seeds?|of)\b",
    r"\b1 epoch\b",
    r"\bn(?:um)?_?epochs?\s*=\s*1\b",
    r"\b--?max[-_]?samples?\b",
    r"\bquick(?:er)? (?:test|run|check)\b",
    r"\bdemo (?:mode|run|size)\b",
    r"\bfor (?:time|speed|brevity)\b",
    r"\bdue to (?:time|compute|memory|resource)\b",
    r"\bto save (?:time|compute|memory)\b",
]

# Hints that an exception was swallowed or a result was stubbed/placeholdered,
# rather than the step genuinely producing the artifact.
_SILENT_EXCEPTION_PATTERNS = [
    r"\bexcept\b[^\n:]*:\s*pass\b",
    r"\bexcept\b[^\n:]*:\s*continue\b",
    r"\btry:\s*\.\.\.\s*except\b",
    r"\bsilently (?:ignore|skip|pass)\b",
    r"\bswallow(?:ed|ing)? (?:the )?(?:exception|error)\b",
    r"\bplaceholder\b",
    r"\bstub(?:bed|bing)?\b",
    r"\bdummy (?:value|data|output|result)\b",
    r"\bhard-?cod(?:e|ed|ing) (?:a )?(?:value|result|output|number)\b",
    r"\bmock(?:ed)? (?:result|output|value|data)\b",
    r"\bfake (?:result|output|data)\b",
    r"\bTODO\b",
    r"\bFIXME\b",
    r"\bnot implemented\b",
    r"\bNotImplementedError\b",
]

# Hints in step text that a step never actually ran / was skipped.
_SKIP_PATTERNS = [
    r"\bskip(?:ped|ping)?\b",
    r"\bnot (?:attempted|executed|run|reached)\b",
    r"\bdid not (?:run|execute|attempt)\b",
    r"\bcould not (?:run|execute|attempt)\b",
    r"\bunable to (?:run|execute|attempt)\b",
    r"\bgave up\b",
    r"\babandon(?:ed)?\b",
    r"\bmoved on\b",
]

# Words that, appearing in stderr/notes of a *failed* step, indicate the error
# was left unresolved (used by the premature-stop heuristic).
_UNRESOLVED_ERROR_PATTERNS = [
    r"\bError\b",
    r"\bException\b",
    r"\bTraceback\b",
    r"\bfailed\b",
    r"\bfatal\b",
    r"\bcannot\b",
    r"\bno such file\b",
    r"\bnot found\b",
    r"\bunresolved\b",
]


def _compile(patterns: List[str]) -> List[re.Pattern]:
    return [re.compile(p, re.IGNORECASE) for p in patterns]


_DOWNSIZE_RE = _compile(_DOWNSIZE_PATTERNS)
_SILENT_RE = _compile(_SILENT_EXCEPTION_PATTERNS)
_SKIP_RE = _compile(_SKIP_PATTERNS)
_UNRESOLVED_RE = _compile(_UNRESOLVED_ERROR_PATTERNS)


def _matches(text: str, patterns: List[re.Pattern]) -> List[str]:
    """Return the distinct patterns (as source strings) that match ``text``."""
    if not text:
        return []
    hits = []
    for pat in patterns:
        if pat.search(text):
            hits.append(pat.pattern)
    return hits


def _step_blob(step: StepOutcome) -> str:
    """Concatenate the human-authored, free-text fields of a step.

    Deliberately *excludes* stdout/stderr for keyword scans that target the
    agent's own narration (notes), but callers that want the full surface pass
    the wider blob explicitly.
    """
    return "\n".join(
        s for s in (step.description, step.notes, step.command_executed) if s
    )


# --- Per-signal dataclasses -------------------------------------------------


@dataclass
class StepCoverageSignal:
    """Did the plan's steps all get executed (vs. skipped / excepted)?"""

    planned_steps: int = 0
    executed_steps: int = 0
    missing_step_ids: List[int] = field(default_factory=list)
    skipped_step_ids: List[int] = field(default_factory=list)  # ran but narrated as skipped
    all_planned_executed: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "planned_steps": self.planned_steps,
            "executed_steps": self.executed_steps,
            "missing_step_ids": list(self.missing_step_ids),
            "skipped_step_ids": list(self.skipped_step_ids),
            "all_planned_executed": self.all_planned_executed,
        }


@dataclass
class ArtifactSignal:
    """Did each result-producing step actually emit its artifact/metric?"""

    result_steps_total: int = 0
    result_steps_with_artifact: int = 0
    result_steps_missing_artifact_ids: List[int] = field(default_factory=list)
    all_result_steps_emitted: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "result_steps_total": self.result_steps_total,
            "result_steps_with_artifact": self.result_steps_with_artifact,
            "result_steps_missing_artifact_ids": list(self.result_steps_missing_artifact_ids),
            "all_result_steps_emitted": self.all_result_steps_emitted,
        }


@dataclass
class PrematureStopSignal:
    """Did the run stop after failures with thin fixes / unresolved errors?"""

    failed_steps: int = 0
    failed_steps_with_unresolved_errors: int = 0
    total_fixes_applied: int = 0
    last_step_failed: bool = False
    premature_stop_suspected: bool = False
    detail: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "failed_steps": self.failed_steps,
            "failed_steps_with_unresolved_errors": self.failed_steps_with_unresolved_errors,
            "total_fixes_applied": self.total_fixes_applied,
            "last_step_failed": self.last_step_failed,
            "premature_stop_suspected": self.premature_stop_suspected,
            "detail": self.detail,
        }


@dataclass
class StuckSignal:
    """Stuck / looping: repeated identical commands."""

    repeated_commands: Dict[str, int] = field(default_factory=dict)  # command -> count
    max_repeat: int = 1
    stuck_suspected: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "repeated_commands": dict(self.repeated_commands),
            "max_repeat": self.max_repeat,
            "stuck_suspected": self.stuck_suspected,
        }


@dataclass
class DownsizingSignal:
    """Hints the run was downsized vs. the plan's intended scale."""

    downsized_step_ids: List[int] = field(default_factory=list)
    hints: Dict[int, List[str]] = field(default_factory=dict)  # step_id -> matched patterns
    downsizing_suspected: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "downsized_step_ids": list(self.downsized_step_ids),
            "hints": {str(k): v for k, v in self.hints.items()},
            "downsizing_suspected": self.downsizing_suspected,
        }


@dataclass
class PlaceholderSignal:
    """Hints of silent exceptions / placeholders / hard-coded outputs."""

    flagged_step_ids: List[int] = field(default_factory=list)
    hints: Dict[int, List[str]] = field(default_factory=dict)  # step_id -> matched patterns
    diff_hints: List[str] = field(default_factory=list)  # matched patterns in codebase.diff
    placeholder_suspected: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "flagged_step_ids": list(self.flagged_step_ids),
            "hints": {str(k): v for k, v in self.hints.items()},
            "diff_hints": list(self.diff_hints),
            "placeholder_suspected": self.placeholder_suspected,
        }


@dataclass
class DiligenceSignals:
    """Aggregate of all deterministic diligence signals for one replicate run.

    ``looks_diligent`` is a convenience overall flag (see module docstring for
    its leniency policy). ``hard_negative_reasons`` lists the specific strong
    negatives that flipped it to ``False``; ``advisory_flags`` lists soft hints
    that did not flip it but the manager should weigh.
    """

    looks_diligent: bool = True
    hard_negative_reasons: List[str] = field(default_factory=list)
    advisory_flags: List[str] = field(default_factory=list)

    step_coverage: StepCoverageSignal = field(default_factory=StepCoverageSignal)
    artifacts: ArtifactSignal = field(default_factory=ArtifactSignal)
    premature_stop: PrematureStopSignal = field(default_factory=PrematureStopSignal)
    stuck: StuckSignal = field(default_factory=StuckSignal)
    downsizing: DownsizingSignal = field(default_factory=DownsizingSignal)
    placeholders: PlaceholderSignal = field(default_factory=PlaceholderSignal)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "looks_diligent": self.looks_diligent,
            "hard_negative_reasons": list(self.hard_negative_reasons),
            "advisory_flags": list(self.advisory_flags),
            "step_coverage": self.step_coverage.to_dict(),
            "artifacts": self.artifacts.to_dict(),
            "premature_stop": self.premature_stop.to_dict(),
            "stuck": self.stuck.to_dict(),
            "downsizing": self.downsizing.to_dict(),
            "placeholders": self.placeholders.to_dict(),
        }

    def summary_line(self) -> str:
        """One-line human/log summary."""
        verdict = "diligent" if self.looks_diligent else "NOT diligent"
        parts = [f"diligence={verdict}"]
        sc = self.step_coverage
        parts.append(f"steps={sc.executed_steps}/{sc.planned_steps}")
        if sc.missing_step_ids:
            parts.append(f"missing={sc.missing_step_ids}")
        a = self.artifacts
        if a.result_steps_total:
            parts.append(
                f"artifacts={a.result_steps_with_artifact}/{a.result_steps_total}"
            )
        if self.premature_stop.premature_stop_suspected:
            parts.append("premature_stop")
        if self.stuck.stuck_suspected:
            parts.append(f"stuck(x{self.stuck.max_repeat})")
        if self.downsizing.downsizing_suspected:
            parts.append("downsizing?")
        if self.placeholders.placeholder_suspected:
            parts.append("placeholder?")
        return "; ".join(parts)


# --- Result-producing step detection ----------------------------------------

# A step is "result-producing" if it is expected to emit a concrete artifact
# (file) or a metric. We detect this from the step's own description/notes and
# the plan step's expected_outcome rather than guessing from outputs (so that a
# step which *should* have produced an artifact but didn't is still counted in
# the denominator).
_RESULT_INTENT_PATTERNS = _compile([
    r"\b(?:produce|emit|generate|write|save|output|create|plot|compute|report|measure)\b",
    r"\b(?:figure|plot|table|metric|score|accuracy|result|csv|json|png|pdf|\.npy|checkpoint|log)\b",
    r"\bexpected (?:outcome|output|file|artifact)\b",
])


def _is_result_step(step: StepOutcome, expected_outcome: str) -> bool:
    blob = "\n".join(s for s in (step.description, expected_outcome) if s)
    return bool(_matches(blob, _RESULT_INTENT_PATTERNS))


def _step_emitted_artifact(step: StepOutcome) -> bool:
    """Did the step emit a concrete artifact or metric?

    Conservative positive evidence: it lists output files, or its stdout/notes
    plainly report numeric/metric content.
    """
    if step.output_files:
        return True
    # Numeric/metric content in stdout (e.g. "accuracy = 0.83", a results table).
    text = "\n".join(s for s in (step.stdout, step.notes) if s)
    if re.search(r"\b\d+\.\d+\b", text):  # any decimal number reported
        return True
    return False


# --- Individual signal computations -----------------------------------------


def compute_step_coverage(
    evidence: ExecutionEvidence,
    plan: Optional[ReplicationPlan],
) -> StepCoverageSignal:
    sig = StepCoverageSignal()
    executed_ids = {s.step_id for s in evidence.step_outcomes}
    sig.executed_steps = len(evidence.step_outcomes)

    if plan is not None and plan.steps:
        planned_ids = [s.id for s in plan.steps]
        sig.planned_steps = len(planned_ids)
        sig.missing_step_ids = sorted(pid for pid in planned_ids if pid not in executed_ids)
    else:
        # No plan to compare against: planned == executed (best effort).
        sig.planned_steps = sig.executed_steps

    # Steps that ran but narrate themselves as skipped/abandoned.
    for s in evidence.step_outcomes:
        if _matches(_step_blob(s), _SKIP_RE):
            sig.skipped_step_ids.append(s.step_id)
    sig.skipped_step_ids.sort()

    sig.all_planned_executed = not sig.missing_step_ids and not sig.skipped_step_ids
    return sig


def compute_artifacts(
    evidence: ExecutionEvidence,
    plan: Optional[ReplicationPlan],
) -> ArtifactSignal:
    sig = ArtifactSignal()
    expected_by_id: Dict[int, str] = {}
    if plan is not None:
        expected_by_id = {s.id: s.expected_outcome for s in plan.steps}

    for s in evidence.step_outcomes:
        expected = expected_by_id.get(s.step_id, "")
        if not _is_result_step(s, expected):
            continue
        sig.result_steps_total += 1
        if _step_emitted_artifact(s):
            sig.result_steps_with_artifact += 1
        else:
            sig.result_steps_missing_artifact_ids.append(s.step_id)

    sig.result_steps_missing_artifact_ids.sort()
    sig.all_result_steps_emitted = not sig.result_steps_missing_artifact_ids
    return sig


def compute_premature_stop(evidence: ExecutionEvidence) -> PrematureStopSignal:
    sig = PrematureStopSignal()
    sig.total_fixes_applied = len(evidence.all_fixes_applied)
    sig.failed_steps = evidence.steps_failed

    for s in evidence.step_outcomes:
        if s.succeeded:
            continue
        err_text = "\n".join(x for x in (s.stderr, s.notes) if x)
        if _matches(err_text, _UNRESOLVED_RE):
            sig.failed_steps_with_unresolved_errors += 1

    if evidence.step_outcomes:
        sig.last_step_failed = not evidence.step_outcomes[-1].succeeded

    # Premature stop: there are unresolved-error failures AND the agent applied
    # few or no fixes relative to those failures (thin effort), OR the run ended
    # on a failed step with an unresolved error.
    thin_fixes = sig.total_fixes_applied < max(1, sig.failed_steps_with_unresolved_errors)
    if sig.failed_steps_with_unresolved_errors > 0 and thin_fixes:
        sig.premature_stop_suspected = True
        sig.detail = (
            f"{sig.failed_steps_with_unresolved_errors} failed step(s) with unresolved "
            f"errors but only {sig.total_fixes_applied} fix(es) applied"
        )
    elif sig.last_step_failed and sig.failed_steps_with_unresolved_errors > 0:
        sig.premature_stop_suspected = True
        sig.detail = "run ended on a failed step with an unresolved error"

    return sig


def _normalize_command(cmd: str) -> str:
    """Collapse whitespace so trivially-different reruns count as identical."""
    return re.sub(r"\s+", " ", cmd or "").strip()


def compute_stuck(evidence: ExecutionEvidence, repeat_threshold: int = 3) -> StuckSignal:
    """Detect repeated identical commands (cline LoopDetectionTracker analogue).

    ``repeat_threshold`` is the count at/above which we flag stuck. Blank
    commands are ignored.
    """
    sig = StuckSignal()
    counts: Dict[str, int] = {}
    for s in evidence.step_outcomes:
        norm = _normalize_command(s.command_executed)
        if not norm:
            continue
        counts[norm] = counts.get(norm, 0) + 1

    repeated = {c: n for c, n in counts.items() if n >= 2}
    sig.repeated_commands = repeated
    sig.max_repeat = max(counts.values(), default=1)
    sig.stuck_suspected = sig.max_repeat >= repeat_threshold
    return sig


def compute_downsizing(evidence: ExecutionEvidence) -> DownsizingSignal:
    sig = DownsizingSignal()
    for s in evidence.step_outcomes:
        blob = "\n".join(x for x in (s.description, s.notes, s.command_executed, s.stdout) if x)
        hits = _matches(blob, _DOWNSIZE_RE)
        if hits:
            sig.downsized_step_ids.append(s.step_id)
            sig.hints[s.step_id] = hits
    sig.downsized_step_ids.sort()
    sig.downsizing_suspected = bool(sig.downsized_step_ids)
    return sig


def compute_placeholders(
    evidence: ExecutionEvidence,
    codebase_diff: Optional[str] = None,
) -> PlaceholderSignal:
    sig = PlaceholderSignal()
    for s in evidence.step_outcomes:
        blob = "\n".join(x for x in (s.notes, s.stdout, s.command_executed) if x)
        hits = _matches(blob, _SILENT_RE)
        if hits:
            sig.flagged_step_ids.append(s.step_id)
            sig.hints[s.step_id] = hits
        # Fix diff snippets can also reveal swallowed exceptions / placeholders.
        for fix in s.fixes_applied:
            fix_hits = _matches(fix.diff_snippet, _SILENT_RE)
            if fix_hits:
                sig.flagged_step_ids.append(s.step_id)
                sig.hints.setdefault(s.step_id, [])
                sig.hints[s.step_id].extend(
                    h for h in fix_hits if h not in sig.hints[s.step_id]
                )

    if codebase_diff:
        # Only scan added lines (those starting with "+") to avoid flagging
        # placeholders that existed in the upstream repo and weren't introduced
        # by the replication agent.
        added = "\n".join(
            ln for ln in codebase_diff.splitlines()
            if ln.startswith("+") and not ln.startswith("+++")
        )
        sig.diff_hints = _matches(added, _SILENT_RE)

    sig.flagged_step_ids = sorted(set(sig.flagged_step_ids))
    sig.placeholder_suspected = bool(sig.flagged_step_ids) or bool(sig.diff_hints)
    return sig


# --- Top-level aggregation --------------------------------------------------


def compute_diligence_signals(
    evidence: Optional[ExecutionEvidence],
    plan: Optional[ReplicationPlan] = None,
    codebase_diff: Optional[str] = None,
    *,
    stuck_repeat_threshold: int = 3,
) -> DiligenceSignals:
    """Compute all deterministic diligence signals for a replicate run.

    Pure function. ``evidence`` is the parsed ``replication_log.json``; ``plan``
    the replication plan (for planned-step / expected-artifact comparison);
    ``codebase_diff`` the unified diff text of the patched codebase. Any of
    ``plan`` / ``codebase_diff`` may be ``None`` (signals degrade gracefully).

    Returns a :class:`DiligenceSignals`. Never raises on well-typed input;
    callers upstream are responsible for parsing JSON into the dataclasses.
    """
    signals = DiligenceSignals()

    if evidence is None or not evidence.step_outcomes:
        # No evidence at all is itself a strong negative.
        signals.looks_diligent = False
        signals.hard_negative_reasons.append("no replication evidence collected")
        return signals

    signals.step_coverage = compute_step_coverage(evidence, plan)
    signals.artifacts = compute_artifacts(evidence, plan)
    signals.premature_stop = compute_premature_stop(evidence)
    signals.stuck = compute_stuck(evidence, repeat_threshold=stuck_repeat_threshold)
    signals.downsizing = compute_downsizing(evidence)
    signals.placeholders = compute_placeholders(evidence, codebase_diff)

    # --- Overall verdict: hard negatives flip it; soft hints are advisory. ---
    if signals.step_coverage.missing_step_ids:
        signals.hard_negative_reasons.append(
            f"planned steps not executed: {signals.step_coverage.missing_step_ids}"
        )
    if signals.step_coverage.skipped_step_ids:
        signals.hard_negative_reasons.append(
            f"steps narrated as skipped/abandoned: {signals.step_coverage.skipped_step_ids}"
        )
    if signals.artifacts.result_steps_missing_artifact_ids:
        signals.hard_negative_reasons.append(
            "result-producing steps with no emitted artifact: "
            f"{signals.artifacts.result_steps_missing_artifact_ids}"
        )
    if signals.premature_stop.premature_stop_suspected:
        signals.hard_negative_reasons.append(
            f"premature stop: {signals.premature_stop.detail}"
        )
    if signals.stuck.stuck_suspected:
        signals.hard_negative_reasons.append(
            f"stuck/looping: a command repeated {signals.stuck.max_repeat} times"
        )

    if signals.downsizing.downsizing_suspected:
        signals.advisory_flags.append(
            f"possible downsizing in steps {signals.downsizing.downsized_step_ids}"
        )
    if signals.placeholders.placeholder_suspected:
        where = list(signals.placeholders.flagged_step_ids)
        if signals.placeholders.diff_hints:
            where.append("codebase.diff")
        signals.advisory_flags.append(
            f"possible silent-exception/placeholder in {where}"
        )

    signals.looks_diligent = not signals.hard_negative_reasons
    return signals
