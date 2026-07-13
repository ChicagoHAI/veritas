"""Objective execution facts over replicate evidence.

Given the replicate evidence (``ExecutionEvidence``, parsed from
``replication_log.json``), the replication plan, and a read-only parse of the
replicate transcript, these functions compute cheap, structured, **objective
facts** about what the replication run actually did. They do NOT judge
diligence — that is the manager's (an LLM's) job, reading this evidence plus
the full trajectory.

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
    intra-step retry/polling loops. Identical consecutive runs over the
    transcript's tool_use events are still string equality over each call's
    identity fields — facts. Only the claude-style
    stream-json schema (tool_use blocks under ``message.content``) is parsed;
    transcripts in other schemas yield zero tool calls, which is
    indistinguishable from a run that made none — consumers must not read
    zeros as "verified clean";
  * counts: total / succeeded / failed steps, fixes applied, durations.

Everything here is deterministic — the only I/O is the read-only transcript
parse — and the module never raises on malformed or missing input (it degrades
to empty/zero facts). The manager consumes these facts as evidence; it owns
every semantic verdict.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from .models.replication import ExecutionEvidence, ReplicationPlan, StepOutcome

# Keys longer than this are truncated with a content-hash suffix so the facts
# file stays readable while distinct long commands remain distinguishable.
_TOOL_CALL_KEY_MAX = 160


@dataclass
class ExecutionFacts:
    """Objective, deterministic facts about one replicate run.

    Pure facts only — no diligence verdict. Every field is something a careful
    reader could confirm directly from ``replication_log.json``, the plan, or
    the replicate transcript. The manager (LLM) reads these as evidence and
    makes the accept/revise judgment.
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
    # available or its schema yielded no tool calls — zeros mean "nothing
    # measured", not "verified clean".
    transcript_tool_calls: int = 0
    # Longest run of consecutive tool calls identical over their identity
    # fields, and that call (truncated); the call is only recorded for an
    # actual repeat (run >= 2).
    max_consecutive_tool_repeat: int = 0
    max_consecutive_tool_call: str = ""

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
            "total_fixes_applied": self.total_fixes_applied,
            "total_duration_seconds": self.total_duration_seconds,
            "no_evidence": self.no_evidence,
        }

    def summary_line(self) -> str:
        """One-line human/log summary of the facts (no verdict)."""
        if self.no_evidence:
            line = "execution facts: no replication evidence collected"
            if self.transcript_tool_calls:
                extra = f"tool_calls={self.transcript_tool_calls}"
                if self.max_consecutive_tool_repeat >= 2:
                    extra += f", max_consec_tool_repeat={self.max_consecutive_tool_repeat}"
                line += f" (transcript: {extra})"
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

# Tool-input fields that identify the ACTION for repeat detection. claude's
# Bash inputs carry per-call presentation metadata (a free-text `description`,
# a `timeout`) that the model rewrites across retries of the same command;
# including them would hide genuine repeats. Tools not listed use their full
# input — whitespace and all — since e.g. Write/Edit content is semantic.
_TOOL_IDENTITY_FIELDS: Dict[str, frozenset] = {
    "Bash": frozenset({"command"}),
}


def _normalize_tool_call(name: str, tool_input: Any) -> str:
    """One tool call as a canonical string: tool name + its identity fields
    serialized with sorted keys. Two calls map to the same key only when the
    same tool ran the same action — string equality, no keywords."""
    if isinstance(tool_input, dict):
        identity = _TOOL_IDENTITY_FIELDS.get(name)
        if identity:
            tool_input = {k: v for k, v in tool_input.items() if k in identity}
    canon = json.dumps(tool_input, sort_keys=True, ensure_ascii=False, default=str)
    return f"{name} {canon}"


def _truncate_key(key: str) -> str:
    """Bound a call key at collection time so the scan never holds unbounded
    strings; the content-hash suffix keeps distinct long calls distinct (a
    plain prefix cut would merge them into false repeats)."""
    if len(key) <= _TOOL_CALL_KEY_MAX:
        return key
    digest = hashlib.sha1(key.encode("utf-8", "replace")).hexdigest()[:8]
    return f"{key[:_TOOL_CALL_KEY_MAX]}…{digest}"


def _scan_transcript_tool_calls(
    transcript_path: Union[str, Path, None],
) -> "tuple[int, int, str]":
    """Single streaming pass over a claude-style stream-json transcript.

    Returns ``(total, best_run_len, best_run_key)`` computed over truncated
    call keys, so memory stays bounded regardless of transcript size. One file
    can hold several appended provider invocations (a JSON-repair re-prompt
    appends a short second session); the total accumulates across all of them,
    but a ``system``/``init`` line breaks the consecutive-run tracking so runs
    never splice across session boundaries. Duplicate tool_use block ids are
    counted once. Degrades to all-neutral on a missing/unreadable file, on
    malformed lines, or on any unforeseen parse failure — never raises.
    """
    neutral: "tuple[int, int, str]" = (0, 0, "")
    if not transcript_path:
        return neutral
    total = 0
    prev_key = ""
    run_len = 0
    best_len = 0
    best_key = ""
    seen_ids: set = set()
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except (ValueError, RecursionError):
                    # JSONDecodeError is a ValueError, but json.loads can also
                    # raise bare ValueError (>4300-digit ints, Py3.11+) and
                    # RecursionError (deep nesting); all just skip the line.
                    continue
                if not isinstance(obj, dict):
                    continue
                if obj.get("type") == "system" and obj.get("subtype") == "init":
                    prev_key, run_len = "", 0
                    continue
                message = obj.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if not (isinstance(block, dict) and block.get("type") == "tool_use"):
                        continue
                    block_id = block.get("id")
                    if isinstance(block_id, str) and block_id:
                        if block_id in seen_ids:
                            continue
                        seen_ids.add(block_id)
                    key = _truncate_key(
                        _normalize_tool_call(str(block.get("name", "")), block.get("input"))
                    )
                    total += 1
                    run_len = run_len + 1 if key == prev_key else 1
                    prev_key = key
                    if run_len > best_len:
                        best_len, best_key = run_len, key
    except Exception:
        # Facts must never take down the pipeline (nor the step-level facts
        # computed after this): OSError, MemoryError on a single giant line,
        # or anything else unforeseen all degrade to neutral.
        return neutral
    return total, best_len, best_key


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

    ``evidence`` is the parsed ``replication_log.json``; ``plan``
    the replication plan (used only for the planned-vs-executed step set
    comparison); ``transcript_path`` the replicate transcript JSONL, parsed for
    granular tool-call repeats. Any may be ``None``; the facts degrade to
    empty/zero. Deterministic — the only I/O is the read-only transcript parse.
    Never raises on malformed input — bad records are simply skipped. The
    transcript facts are computed even when the step evidence is absent (a
    hard-terminated run leaves a transcript but no log), and a transcript
    failure never affects the step-level facts.

    Returns an :class:`ExecutionFacts`. It makes no diligence judgment; the
    manager owns that.
    """
    facts = ExecutionFacts()
    total, best_len, best_key = _scan_transcript_tool_calls(transcript_path)
    if total:
        facts.transcript_tool_calls = total
        facts.max_consecutive_tool_repeat = best_len
        facts.max_consecutive_tool_call = best_key if best_len >= 2 else ""

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
