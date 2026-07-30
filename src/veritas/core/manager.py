"""Manager-controlled retry loop (Phase 2 of the iterative-manager design).

Authoritative spec: ``notes/2026-06-03-iterative-manager-design.md`` §4 (loop +
verdict), §5.3 (workflow log), §5.4 (termination + hand-off), §5.5 (archival).

This module is the *control* layer that sits AFTER ``replicate`` and BEFORE
``verify``. It is built to mirror the convergent pattern of the four agentic
systems studied (CrewAI ``LLMGuardrail`` accept/resend, Magentic-One Progress
Ledger, LangGraph hard+soft bounds, Reflexion diagnosis-injected-as-guidance):

  * a **hard, deterministic cap** the python enforces regardless of the LLM,
  * an **independent critic** (the manager — fresh context, API keys stripped,
    must not run paper code) that ALWAYS does the diligence judging and emits a
    **structured verdict**. There is no deterministic short-circuit-accept:
    deterministic code computes objective execution *facts* (see
    ``diligence.py``); the manager reads those facts + the trajectory and owns
    every semantic verdict (skipped/downsized/premature-stop/placeholder),
  * a **no-progress terminator** independent of the iteration count, comparing
    objective execution facts between attempts,
  * a **graceful terminal hand-off** when the cap is hit without acceptance.

Everything here that does not need an LLM is a pure function so it can be
unit-tested in isolation: :class:`ManagerVerdict` parsing, the termination
predicate (:func:`should_stop`), archival (:func:`archive_attempt`), and the
workflow-log writer (:class:`WorkflowLog`). The LLM pass itself is injected by
the runner (``review_fn``) so this module never imports the provider machinery.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from veritas.core.diligence import ExecutionFacts

# --- Verdict vocabulary -----------------------------------------------------

DECISION_ACCEPT = "accept"
DECISION_REVISE = "revise"
VALID_DECISIONS = {DECISION_ACCEPT, DECISION_REVISE}

# Phases the manager is allowed to send a re-run back to. ``replicate`` is the
# common case; ``plan`` when the plan itself was the problem. ``codegen`` is
# accepted for robustness but the loop downgrades it to ``plan`` — code
# regeneration is not reachable from inside the loop.
VALID_TARGET_PHASES = {"replicate", "plan", "codegen"}

# Genuineness buckets (§4.3). The manager must classify *why* the work fell
# short so the calibration (accept divergence, only revise deficiency) is
# auditable.
GENUINENESS_DEFICIENT = "deficient"
GENUINENESS_DIVERGENT = "diligent-but-divergent"
GENUINENESS_IRREDUCIBLE = "irreducible"
VALID_GENUINENESS = {
    GENUINENESS_DEFICIENT,
    GENUINENESS_DIVERGENT,
    GENUINENESS_IRREDUCIBLE,
}


@dataclass
class ManagerVerdict:
    """Structured verdict from the manager review pass (§4.3).

    Mirrors Magentic-One's Progress Ledger (strict, machine-readable) plus the
    diligence-calibration fields the design adds. ``source`` records whether the
    verdict came from the deterministic short-circuit or the LLM, so the
    workflow log is honest about which path decided.
    """

    decision: str = DECISION_ACCEPT
    diligence_sufficient: bool = True
    deficiency_is_genuine: str = GENUINENESS_DIVERGENT
    target_phase: Optional[str] = None
    reason: str = ""
    directive: str = ""
    already_tried: str = ""
    confidence: float = 0.0
    research_requests: List[Dict[str, str]] = field(default_factory=list)
    source: str = "llm"  # "deterministic" | "llm" | "fallback"

    @property
    def accepted(self) -> bool:
        return self.decision == DECISION_ACCEPT

    def to_dict(self) -> Dict[str, Any]:
        return {
            "decision": self.decision,
            "diligence_sufficient": self.diligence_sufficient,
            "deficiency_is_genuine": self.deficiency_is_genuine,
            "target_phase": self.target_phase,
            "reason": self.reason,
            "directive": self.directive,
            "already_tried": self.already_tried,
            "confidence": self.confidence,
            "research_requests": list(self.research_requests),
            "source": self.source,
        }


def parse_manager_verdict(raw: Dict[str, Any], *, source: str = "llm") -> ManagerVerdict:
    """Parse + normalize a manager verdict dict into a :class:`ManagerVerdict`.

    Defensive: unknown enum values are coerced to safe defaults that **bias to
    ACCEPT** (the design's "bias to ACCEPT past iteration 1" / never block on a
    malformed verdict). A ``revise`` decision missing a usable directive is
    downgraded to ``accept`` so we never re-run with empty guidance (a blank
    re-run is just a repeat — explicitly forbidden by §4.4).
    """
    v = ManagerVerdict(source=source)

    decision = str(raw.get("decision", DECISION_ACCEPT)).strip().lower()
    v.decision = decision if decision in VALID_DECISIONS else DECISION_ACCEPT

    v.diligence_sufficient = bool(raw.get("diligence_sufficient", True))

    genuine = str(raw.get("deficiency_is_genuine", GENUINENESS_DIVERGENT)).strip().lower()
    # Tolerate the design doc's longer phrasings by substring match.
    if genuine not in VALID_GENUINENESS:
        if "deficien" in genuine:
            genuine = GENUINENESS_DEFICIENT
        elif "irreducible" in genuine or "tolerance" in genuine:
            genuine = GENUINENESS_IRREDUCIBLE
        else:
            genuine = GENUINENESS_DIVERGENT
    v.deficiency_is_genuine = genuine

    target = raw.get("target_phase")
    if isinstance(target, str):
        target = target.strip().lower()
        v.target_phase = target if target in VALID_TARGET_PHASES else None
    else:
        v.target_phase = None

    v.reason = str(raw.get("reason", "") or "").strip()
    v.directive = str(raw.get("directive", "") or "").strip()
    v.already_tried = str(raw.get("already_tried", "") or "").strip()

    try:
        v.confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        v.confidence = 0.0

    # Research requests (Phase 3): kept as raw dicts here; the honoring decision
    # (the intent allow-list) lives in ``research.honor_request`` so there is one
    # auditable gate. We only retain dict-shaped entries; everything semantic
    # (which kinds are allowed, redaction) is enforced downstream.
    rr = raw.get("research_requests") or []
    if isinstance(rr, list):
        v.research_requests = [r for r in rr if isinstance(r, dict)]

    # Calibration guard: a revise with no actionable directive becomes an accept
    # (never re-run blind).
    if v.decision == DECISION_REVISE:
        if not v.directive:
            v.decision = DECISION_ACCEPT
            v.reason = (v.reason + " [downgraded: revise emitted without a directive]").strip()
        elif v.target_phase is None:
            # Default the target to replicate (the design's primary re-run target).
            v.target_phase = "replicate"

    return v


# --- No-progress terminator (independent of the count) ----------------------
#
# This compares OBJECTIVE EXECUTION FACTS between attempts. It is NOT a diligence
# judgment (the manager owns that) — it only answers the mechanical question
# "did the re-run mechanically make headway, or is it spinning?", so the loop can
# stop a stalled trajectory without waiting for the cap.


def _facts_fingerprint(facts: Optional[ExecutionFacts]) -> Dict[str, Any]:
    """A small, comparable summary of a run's facts for progress detection."""
    if facts is None:
        return {}
    return {
        "no_evidence": facts.no_evidence,
        "missing_steps": sorted(facts.missing_step_ids),
        "failed_steps": facts.failed_steps,
        "steps_without_output_files": sorted(facts.steps_without_output_files),
        "executed_steps": facts.executed_steps,
    }


def facts_improved(
    prev: Optional[ExecutionFacts],
    curr: Optional[ExecutionFacts],
) -> bool:
    """Did the *current* run's execution facts improve over the previous run's?

    Conservative and mechanical: improvement means strictly fewer missing
    planned steps, strictly fewer failed steps, strictly fewer steps without any
    declared output file, or going from no-evidence to having evidence.
    Equal-or-worse counts as "no progress". Used (with a repeated directive) by
    :func:`should_stop` as the no-progress terminator.

    This is set/count arithmetic over objective facts — it makes no claim about
    whether the run was diligent. That remains the manager's call.
    """
    if prev is None or curr is None:
        return True  # no baseline to compare — don't declare stuck
    p = _facts_fingerprint(prev)
    c = _facts_fingerprint(curr)
    if p["no_evidence"] and not c["no_evidence"]:
        return True
    if len(c["missing_steps"]) < len(p["missing_steps"]):
        return True
    if c["failed_steps"] < p["failed_steps"]:
        return True
    if len(c["steps_without_output_files"]) < len(p["steps_without_output_files"]):
        return True
    return False


# Back-compat alias: the loop historically called this ``signals_improved``.
signals_improved = facts_improved


def _normalize_directive(directive: str) -> str:
    return " ".join((directive or "").lower().split())


@dataclass
class StopDecision:
    stop: bool
    reason: str  # "accepted" | "cap" | "no-progress" | "continue"


def should_stop(
    *,
    verdict: ManagerVerdict,
    iteration: int,
    max_iters: int,
    prev_signals: Optional[ExecutionFacts],
    curr_signals: Optional[ExecutionFacts],
    prev_directive: Optional[str],
) -> StopDecision:
    """Termination predicate (hard cap + acceptance + no-progress).

    ``iteration`` is 1-based (the iteration that just produced ``curr_signals``
    and ``verdict``). The hard cap is enforced here AND by the runner; this is
    the single source of truth for *why* we stop so the workflow log is honest.
    ``prev_signals`` / ``curr_signals`` are :class:`ExecutionFacts` (objective
    facts), compared mechanically for progress — not a diligence judgment.

    Order of checks:
      1. ACCEPT  -> stop "accepted".
      2. Hard cap reached (``iteration >= max_iters``) -> stop "cap".
      3. No-progress: facts did not improve AND the new directive repeats the
         previous one -> stop "no-progress" (Magentic-One stall analogue).
      4. Otherwise continue.
    """
    if verdict.accepted:
        return StopDecision(stop=True, reason="accepted")

    if iteration >= max_iters:
        return StopDecision(stop=True, reason="cap")

    improved = facts_improved(prev_signals, curr_signals)
    directive_repeats = (
        prev_directive is not None
        and _normalize_directive(prev_directive) == _normalize_directive(verdict.directive)
        and bool(_normalize_directive(verdict.directive))
    )
    if not improved and directive_repeats:
        return StopDecision(stop=True, reason="no-progress")

    return StopDecision(stop=False, reason="continue")


# --- Archival (never silently overwrite) ------------------------------------


def archive_attempt(replication_dir: Path, attempt: int) -> Optional[Path]:
    """Archive the current ``replication/`` tree to ``replication.attempt-N/``.

    Returns the archive path (or ``None`` if there was nothing to archive).
    Copies rather than moves so an interrupted re-run still leaves the prior
    attempt readable in place; the caller invalidates pipeline state separately.
    A pre-existing archive for the same N is replaced (idempotent on resume).
    """
    replication_dir = Path(replication_dir)
    if not replication_dir.exists():
        return None
    archive = replication_dir.parent / f"{replication_dir.name}.attempt-{attempt}"
    if archive.exists():
        shutil.rmtree(archive)
    shutil.copytree(replication_dir, archive, symlinks=True)
    return archive


# --- Workflow / decision log (first-class artifact, §5.3) -------------------


class WorkflowLog:
    """Append-only JSONL workflow log plus a human-readable markdown summary.

    One JSONL record per iteration / phase run (§5.3):
    ``{iteration, phase, status, transcript_path, signals, manager_verdict,
    directive, archived_attempt_path, ...}``. The markdown summary is
    regenerated from the full record set on every append so it always reflects
    the complete trajectory. This is the artifact Haokun wants for evaluating
    the run, so we keep it readable and consistent.
    """

    def __init__(self, veritas_dir: Path):
        self.veritas_dir = Path(veritas_dir)
        self.veritas_dir.mkdir(parents=True, exist_ok=True)
        self.jsonl_path = self.veritas_dir / "workflow.jsonl"
        self.md_path = self.veritas_dir / "workflow.md"

    def records(self) -> List[Dict[str, Any]]:
        if not self.jsonl_path.exists():
            return []
        out: List[Dict[str, Any]] = []
        for line in self.jsonl_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def append(self, record: Dict[str, Any]) -> None:
        rec = dict(record)
        rec.setdefault("ts", datetime.now().isoformat())
        with open(self.jsonl_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")
        self._rewrite_summary()

    def write_handoff(self, handoff: Dict[str, Any]) -> None:
        """Record the structured graceful-terminal hand-off (§5.4).

        Written as its own record (``phase="handoff"``) and also surfaced in the
        markdown summary so a human can pick the run up later.
        """
        rec = {"iteration": handoff.get("iteration"), "phase": "handoff", "status": "unresolved", "handoff": handoff}
        self.append(rec)

    def latest_handoff(self) -> Optional[Dict[str, Any]]:
        for rec in reversed(self.records()):
            if rec.get("phase") == "handoff":
                return rec.get("handoff")
        return None

    # -- markdown rendering --------------------------------------------------

    def _rewrite_summary(self) -> None:
        recs = self.records()
        lines = ["# Veritas workflow trajectory", ""]
        lines.append(
            "Iterative-manager loop trajectory: one entry per phase run and per "
            "manager review. The headline Replication Score is unaffected by the "
            "iteration count; this log records what each re-run changed."
        )
        lines.append("")
        for rec in recs:
            it = rec.get("iteration")
            phase = rec.get("phase", "?")
            status = rec.get("status", "")
            header = f"## Iteration {it} — {phase}"
            if status:
                header += f" ({status})"
            lines.append(header)
            lines.append("")
            sig = rec.get("signals")
            if isinstance(sig, dict) and sig.get("summary_line"):
                lines.append(f"- **Diligence signals:** {sig['summary_line']}")
            verdict = rec.get("manager_verdict")
            if isinstance(verdict, dict):
                lines.append(
                    f"- **Manager decision:** `{verdict.get('decision')}` "
                    f"(source: {verdict.get('source')}, "
                    f"genuine: {verdict.get('deficiency_is_genuine')}, "
                    f"confidence: {verdict.get('confidence')})"
                )
                if verdict.get("reason"):
                    lines.append(f"- **Reason:** {verdict['reason']}")
                if verdict.get("directive"):
                    lines.append(f"- **Directive (new instructions):** {verdict['directive']}")
                if verdict.get("already_tried"):
                    lines.append(f"- **Already tried (don't repeat):** {verdict['already_tried']}")
            research = rec.get("research")
            if isinstance(research, dict):
                honored = research.get("honored") or []
                rejected = research.get("rejected") or []
                lines.append(
                    f"- **Research:** {len(honored)} honored, "
                    f"{len(rejected)} rejected (intent gate)"
                )
                for f in research.get("findings") or []:
                    if f.get("error"):
                        lines.append(
                            f"  - [{f.get('kind')}] {f.get('need')}: "
                            f"_no usable finding ({f.get('error')})_"
                        )
                        continue
                    red = f.get("redaction") or {}
                    hits = red.get("exact_hits") or []
                    flags = []
                    if red.get("llm_removed"):
                        flags.append("LLM redacted")
                    if hits:
                        flags.append(f"{len(hits)} known-value scrub(s)")
                    flag_str = f" ({'; '.join(flags)})" if flags else ""
                    srcs = ", ".join(f.get("sources") or []) or "(no source)"
                    lines.append(
                        f"  - [{f.get('kind')}] {f.get('need')} -> source: {srcs}{flag_str}"
                    )
                for r in rejected:
                    lines.append(
                        f"  - REJECTED (intent gate): kind=`{r.get('kind')}` need: {r.get('need')}"
                    )
            if rec.get("directive") and not isinstance(verdict, dict):
                lines.append(f"- **Directive:** {rec['directive']}")
            if rec.get("archived_attempt_path"):
                lines.append(f"- **Archived prior attempt:** `{rec['archived_attempt_path']}`")
            if rec.get("transcript_path"):
                lines.append(f"- **Transcript:** `{rec['transcript_path']}`")
            handoff = rec.get("handoff")
            if isinstance(handoff, dict):
                lines.append(f"- **UNRESOLVED HAND-OFF:** {handoff.get('where_it_falls_short', '')}")
                if handoff.get("why_rerun_needed"):
                    lines.append(f"  - Why a re-run is still needed: {handoff['why_rerun_needed']}")
                if handoff.get("what_to_try_next"):
                    lines.append(f"  - What to try next: {handoff['what_to_try_next']}")
            lines.append("")
        self.md_path.write_text("\n".join(lines), encoding="utf-8")


def build_handoff(
    *,
    iteration: int,
    verdict: ManagerVerdict,
    signals: Optional[ExecutionFacts],
    stop_reason: str,
) -> Dict[str, Any]:
    """Construct the structured ``unresolved_handoff`` for the graceful terminal.

    Produced when the loop ends WITHOUT acceptance (cap or no-progress). States
    where the work still falls short, why a re-run is still warranted, and what
    to try next — drawing on the manager's own last verdict plus the objective
    execution facts so it is grounded, not invented.
    """
    where = verdict.reason or "the manager did not accept the replication"
    if signals is not None:
        fact_notes: List[str] = []
        if signals.no_evidence:
            fact_notes.append("no replication evidence collected")
        if signals.missing_step_ids:
            fact_notes.append(f"planned steps with no record: {signals.missing_step_ids}")
        if signals.failed_step_ids:
            fact_notes.append(f"steps with nonzero exit code: {signals.failed_step_ids}")
        if signals.max_consecutive_tool_repeat >= 2:
            fact_notes.append(
                f"the same tool call ran {signals.max_consecutive_tool_repeat}x back-to-back"
            )
        if fact_notes:
            where += " | execution facts: " + "; ".join(fact_notes)
    return {
        "iteration": iteration,
        "resolved": False,
        "stop_reason": stop_reason,
        "where_it_falls_short": where,
        "why_rerun_needed": (
            "Reached the retry cap without an accept verdict."
            if stop_reason == "cap"
            else "The re-run did not improve the execution facts and the "
            "manager's directive was not making progress."
        ),
        "what_to_try_next": verdict.directive or "Manual review of the replication trajectory is recommended.",
        "last_genuineness": verdict.deficiency_is_genuine,
    }


# --- Manager guidance bundle (threaded into the re-run templates) -----------


@dataclass
class ManagerGuidance:
    """The guidance injected into a re-run's prompt (§4.4 / §5.2).

    Rendered by ``{% if manager_guidance %}`` blocks in the re-runnable phase
    templates. Carries the deficiency, the specific NEW instructions, and what
    was already tried — so the re-run is genuinely different, never a blank
    repeat. Deliberately answer-free (anti-leakage): only methodology/process
    guidance, never reported values.
    """

    iteration: int
    deficiency: str
    directive: str
    already_tried: str = ""
    # Phase 3: provenance-tagged, post-redaction methodology/resource findings
    # from the manager's research sub-agents. Rendered as its own block in the
    # re-run templates. Always answer-free (it has been through the two-layer
    # redactor); empty when no research ran for this iteration.
    research_findings: str = ""

    @classmethod
    def from_verdict(cls, verdict: ManagerVerdict, *, iteration: int) -> "ManagerGuidance":
        return cls(
            iteration=iteration,
            deficiency=verdict.reason,
            directive=verdict.directive,
            already_tried=verdict.already_tried,
        )
