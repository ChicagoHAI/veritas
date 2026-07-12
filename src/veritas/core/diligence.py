"""Objective execution facts over replicate evidence.

These are *pure* functions: given the replicate evidence (``ExecutionEvidence``,
parsed from ``replication_log.json``) and the replication plan, they compute
cheap, structured, **objective facts** about what the replication run actually
did. They do NOT judge diligence — that is the manager's (an LLM's) job, reading
this evidence plus the full trajectory.

Design intent (Haokun, 2026-06): deterministic code asserts only OBJECTIVE
FACTS. Semantic questions — "is this a placeholder?", "was a step
skipped/downsized?", "did the agent give up early?" — are judgment calls about
intent and meaning. Keyword/regex matching is the wrong tool for those: it
produces false positives (a clean run mentioning the word "placeholder" in a
comment, a legitimately fast step that says "quick check"). Those calls belong
to the manager, which reads the real evidence. This module was previously a
``DiligenceSignals`` verdict with keyword pattern banks; that machinery is gone.

What counts as an objective fact here:
  * planned step count vs. executed step count, and which planned steps produced
    no record (set difference over step IDs — a fact);
  * per-step exit codes (nonzero == a hard failure — a fact);
  * per-step declared output files present or absent (the step's own
    ``output_files`` list — a fact about what it recorded producing);
  * stuck/looping == byte-identical consecutive commands (string equality, not
    keywords — a fact);
  * granular tool-call repeats parsed from the replicate transcript: one planned
    step spans many tool calls, so the step-level command comparison cannot see
    intra-step retry/polling loops. Byte-identical consecutive runs and
    anywhere-counts over the transcript's tool_use events are still string
    equality — facts. Only the claude stream-json transcript schema is parsed;
    other providers' transcripts yield zero tool calls and the fields stay
    neutral;
  * counts: total / succeeded / failed steps, fixes applied, durations.

Everything here is a pure function and the module never raises on malformed or
missing input (it degrades to empty/zero facts). The manager consumes these
facts as evidence; it owns every semantic verdict.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .models.replication import ExecutionEvidence, ReplicationPlan, StepOutcome

# Anywhere-counts below this floor are omitted from ``repeated_tool_calls``
# (running the same call twice is unremarkable; the dict would otherwise drown
# in incidental pairs on long transcripts).
_TOOL_CALL_REPEAT_FLOOR = 3
# At most this many entries are kept (highest counts first, then key order).
_TOOL_CALL_REPEAT_TOP = 10
# Keys longer than this are truncated with a content-hash suffix so the facts
# file stays readable while distinct long commands remain distinguishable.
_TOOL_CALL_KEY_MAX = 160


@dataclass
class ExecutionFacts:
    """Objective, deterministic facts about one replicate run.

    Pure facts only — no diligence verdict. Every field is something a careful
    reader could confirm directly from ``replication_log.json`` + the plan. The
    manager (LLM) reads these as evidence and makes the accept/revise judgment.
    """

    # --- step coverage (set arithmetic over step IDs) ----------------------
    planned_steps: int = 0
    executed_steps: int = 0
    # Planned step IDs that produced no execution record at all.
    missing_step_ids: List[int] = field(default_factory=list)

    # --- exit codes (nonzero == failure) -----------------------------------
    succeeded_steps: int = 0
    failed_steps: int = 0
    failed_step_ids: List[int] = field(default_factory=list)
    # (step_id -> exit_code) for every executed step, so the manager can see the
    # raw codes without re-parsing the log.
    exit_codes: Dict[int, int] = field(default_factory=dict)
    last_step_failed: bool = False

    # --- declared output files (presence/absence is a fact) ----------------
    steps_with_output_files: List[int] = field(default_factory=list)
    steps_without_output_files: List[int] = field(default_factory=list)
    total_output_files: int = 0

    # --- stuck / looping (byte-identical commands) -------------------------
    # Normalized command string -> number of executed steps that ran it. Only
    # commands that appear more than once are kept.
    repeated_commands: Dict[str, int] = field(default_factory=dict)
    max_command_repeat: int = 1

    # --- granular tool-call repeats (from the replicate transcript) --------
    # Parsed from the transcript's tool_use events, so intra-step retry and
    # polling loops are visible. All zero/empty when no transcript was
    # available or its schema yielded no tool calls.
    transcript_tool_calls: int = 0
    # Longest run of byte-identical consecutive tool calls, and that call
    # (truncated); the call is only recorded for an actual repeat (run >= 2).
    max_consecutive_tool_repeat: int = 0
    max_consecutive_tool_call: str = ""
    # Normalized tool call -> anywhere-count (>= _TOOL_CALL_REPEAT_FLOOR,
    # top _TOOL_CALL_REPEAT_TOP entries).
    repeated_tool_calls: Dict[str, int] = field(default_factory=dict)

    # --- effort accounting -------------------------------------------------
    total_fixes_applied: int = 0
    total_duration_seconds: float = 0.0

    # --- liveness ----------------------------------------------------------
    # No evidence was collected at all (empty / missing log). A bare fact the
    # manager will obviously weigh, but still just a fact, not a verdict.
    no_evidence: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "planned_steps": self.planned_steps,
            "executed_steps": self.executed_steps,
            "missing_step_ids": list(self.missing_step_ids),
            "succeeded_steps": self.succeeded_steps,
            "failed_steps": self.failed_steps,
            "failed_step_ids": list(self.failed_step_ids),
            "exit_codes": {str(k): v for k, v in self.exit_codes.items()},
            "last_step_failed": self.last_step_failed,
            "steps_with_output_files": list(self.steps_with_output_files),
            "steps_without_output_files": list(self.steps_without_output_files),
            "total_output_files": self.total_output_files,
            "repeated_commands": dict(self.repeated_commands),
            "max_command_repeat": self.max_command_repeat,
            "transcript_tool_calls": self.transcript_tool_calls,
            "max_consecutive_tool_repeat": self.max_consecutive_tool_repeat,
            "max_consecutive_tool_call": self.max_consecutive_tool_call,
            "repeated_tool_calls": dict(self.repeated_tool_calls),
            "total_fixes_applied": self.total_fixes_applied,
            "total_duration_seconds": self.total_duration_seconds,
            "no_evidence": self.no_evidence,
        }

    def summary_line(self) -> str:
        """One-line human/log summary of the facts (no verdict)."""
        if self.no_evidence:
            line = "execution facts: no replication evidence collected"
            if self.transcript_tool_calls:
                line += (
                    f" (transcript: tool_calls={self.transcript_tool_calls}, "
                    f"max_consec_tool_repeat={self.max_consecutive_tool_repeat})"
                )
            return line
        parts = [f"steps={self.executed_steps}/{self.planned_steps}"]
        if self.missing_step_ids:
            parts.append(f"missing={self.missing_step_ids}")
        parts.append(f"succeeded={self.succeeded_steps} failed={self.failed_steps}")
        if self.failed_step_ids:
            parts.append(f"failed_ids={self.failed_step_ids}")
        parts.append(f"output_files={self.total_output_files}")
        if self.steps_without_output_files:
            parts.append(f"no_output_steps={self.steps_without_output_files}")
        if self.max_command_repeat > 1:
            parts.append(f"max_cmd_repeat={self.max_command_repeat}")
        if self.transcript_tool_calls:
            parts.append(f"tool_calls={self.transcript_tool_calls}")
        if self.max_consecutive_tool_repeat >= 2:
            parts.append(f"max_consec_tool_repeat={self.max_consecutive_tool_repeat}")
        parts.append(f"fixes={self.total_fixes_applied}")
        return "; ".join(parts)


# --- command normalization (byte-equality, whitespace-collapsed) -----------


def _normalize_command(cmd: str) -> str:
    """Collapse internal whitespace so a trivially-reformatted rerun of the same
    command counts as identical. This is string equality, not a keyword match:
    the only thing it asserts is "the same command text ran again"."""
    return " ".join((cmd or "").split())


# --- granular tool-call extraction (replicate transcript) -------------------


def _normalize_tool_call(name: str, tool_input: Any) -> str:
    """One tool call as a canonical string: tool name + its input serialized
    with sorted keys, whitespace-collapsed. Two calls map to the same key only
    when the same tool ran with the same input — string equality, no keywords."""
    try:
        canon = json.dumps(tool_input, sort_keys=True, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        canon = str(tool_input)
    return " ".join(f"{name} {canon}".split())


def _truncate_key(key: str) -> str:
    """Bound a call key for the facts file; a content-hash suffix keeps
    distinct long commands distinguishable after truncation."""
    if len(key) <= _TOOL_CALL_KEY_MAX:
        return key
    digest = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:8]
    return f"{key[:_TOOL_CALL_KEY_MAX]}…{digest}"


def _transcript_tool_call_keys(transcript_path: Union[str, Path, None]) -> List[str]:
    """Normalized tool-call keys from a claude stream-json transcript, in
    temporal order. Degrades to an empty list on a missing/unreadable file,
    malformed lines, or an unrecognized (non-claude) transcript schema."""
    if not transcript_path:
        return []
    keys: List[str] = []
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                message = obj.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        keys.append(
                            _normalize_tool_call(str(block.get("name", "")), block.get("input"))
                        )
    except OSError:
        return []
    return keys


def _apply_tool_call_facts(
    facts: ExecutionFacts, transcript_path: Union[str, Path, None]
) -> None:
    keys = _transcript_tool_call_keys(transcript_path)
    if not keys:
        return
    facts.transcript_tool_calls = len(keys)

    best_len = 1
    best_key = ""
    run_len = 1
    for prev, curr in zip(keys, keys[1:]):
        run_len = run_len + 1 if curr == prev else 1
        if run_len > best_len:
            best_len, best_key = run_len, curr
    facts.max_consecutive_tool_repeat = best_len
    facts.max_consecutive_tool_call = _truncate_key(best_key) if best_len >= 2 else ""

    counts: Dict[str, int] = {}
    for key in keys:
        counts[key] = counts.get(key, 0) + 1
    top = sorted(
        ((k, n) for k, n in counts.items() if n >= _TOOL_CALL_REPEAT_FLOOR),
        key=lambda kv: (-kv[1], kv[0]),
    )[:_TOOL_CALL_REPEAT_TOP]
    facts.repeated_tool_calls = {_truncate_key(k): n for k, n in top}


def _step_id(step: StepOutcome) -> Optional[int]:
    sid = getattr(step, "step_id", None)
    if isinstance(sid, bool):  # bool is an int subclass; reject it
        return None
    if isinstance(sid, int):
        return sid
    try:
        return int(sid)
    except (TypeError, ValueError):
        return None


def _exit_code(step: StepOutcome) -> Optional[int]:
    code = getattr(step, "exit_code", None)
    if isinstance(code, bool):
        return None
    if isinstance(code, int):
        return code
    try:
        return int(code)
    except (TypeError, ValueError):
        return None


def compute_execution_facts(
    evidence: Optional[ExecutionEvidence],
    plan: Optional[ReplicationPlan] = None,
    transcript_path: Union[str, Path, None] = None,
) -> ExecutionFacts:
    """Compute objective execution facts for one replicate run.

    Pure function. ``evidence`` is the parsed ``replication_log.json``; ``plan``
    the replication plan (used only for the planned-vs-executed step set
    comparison); ``transcript_path`` the replicate transcript JSONL, parsed for
    granular tool-call repeats. Any may be ``None``; the facts degrade to
    empty/zero. Never raises on malformed input — bad records are simply
    skipped. The transcript facts are computed even when the step evidence is
    absent (a hard-terminated run leaves a transcript but no log).

    Returns an :class:`ExecutionFacts`. It makes no diligence judgment; the
    manager owns that.
    """
    facts = ExecutionFacts()
    _apply_tool_call_facts(facts, transcript_path)

    steps = list(getattr(evidence, "step_outcomes", None) or []) if evidence is not None else []

    if not steps:
        facts.no_evidence = True
        # Still surface the planned count if a plan is available, so the manager
        # sees that 0 of N planned steps ran.
        planned_ids = _planned_ids(plan)
        facts.planned_steps = len(planned_ids)
        facts.missing_step_ids = sorted(planned_ids)
        return facts

    executed_ids: List[int] = []
    for step in steps:
        sid = _step_id(step)
        if sid is not None:
            executed_ids.append(sid)

    facts.executed_steps = len(steps)

    # --- step coverage: which planned steps produced no record -------------
    planned_ids = _planned_ids(plan)
    if planned_ids:
        facts.planned_steps = len(planned_ids)
        executed_set = set(executed_ids)
        facts.missing_step_ids = sorted(pid for pid in planned_ids if pid not in executed_set)
    else:
        # No plan to compare against: planned == executed (best effort).
        facts.planned_steps = facts.executed_steps

    # --- exit codes, output files, commands, effort ------------------------
    command_counts: Dict[str, int] = {}
    last_failed = False
    for step in steps:
        sid = _step_id(step)
        code = _exit_code(step)

        if code is not None and sid is not None:
            facts.exit_codes[sid] = code
        succeeded = (code == 0) if code is not None else True
        if succeeded:
            facts.succeeded_steps += 1
            last_failed = False
        else:
            facts.failed_steps += 1
            last_failed = True
            if sid is not None:
                facts.failed_step_ids.append(sid)

        output_files = list(getattr(step, "output_files", None) or [])
        facts.total_output_files += len(output_files)
        if sid is not None:
            if output_files:
                facts.steps_with_output_files.append(sid)
            else:
                facts.steps_without_output_files.append(sid)

        norm = _normalize_command(getattr(step, "command_executed", "") or "")
        if norm:
            command_counts[norm] = command_counts.get(norm, 0) + 1

        facts.total_fixes_applied += len(getattr(step, "fixes_applied", None) or [])
        dur = getattr(step, "duration_seconds", 0.0) or 0.0
        try:
            facts.total_duration_seconds += float(dur)
        except (TypeError, ValueError):
            pass

    facts.failed_step_ids.sort()
    facts.steps_with_output_files.sort()
    facts.steps_without_output_files.sort()
    facts.last_step_failed = last_failed

    facts.repeated_commands = {c: n for c, n in command_counts.items() if n >= 2}
    facts.max_command_repeat = max(command_counts.values(), default=1)

    return facts


def _planned_ids(plan: Optional[ReplicationPlan]) -> List[int]:
    """Extract the planned step IDs as a list of ints (skips malformed ones)."""
    if plan is None:
        return []
    out: List[int] = []
    for step in getattr(plan, "steps", None) or []:
        sid = getattr(step, "id", None)
        if isinstance(sid, bool):
            continue
        if isinstance(sid, int):
            out.append(sid)
            continue
        try:
            out.append(int(sid))
        except (TypeError, ValueError):
            continue
    return out
