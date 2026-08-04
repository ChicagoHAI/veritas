"""Parsing for replication plans and gathering of execution evidence."""

import json
import re
from pathlib import Path
from typing import Optional

from veritas.core.models.replication import (
    ReplicationPlan,
    ExecutionEvidence,
    StepOutcome,
)


# Filenames written by the replication agent inside the container and read
# back here. Both names are also referenced in
# ``templates/replication/session_instructions.md`` (the agent contract).
REPLICATION_LOG_FILE = "replication_log.json"
EVIDENCE_SUMMARY_FILE = "evidence_summary.json"


_VALID_JSON_ESCAPES = frozenset('"\\/bfnrtu')


def _fix_json_escapes(text: str) -> str:
    r"""Fix invalid JSON escape sequences commonly produced by LLMs.

    JSON only allows: \" \\ \/ \b \f \n \r \t \uXXXX
    LLMs often write \' (from Python) which becomes a bare apostrophe,
    or \s, \d, \( etc. (from embedded regex) which get double-escaped
    to preserve the intended literal backslash.
    """
    out = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "\\" and i + 1 < len(text):
            nxt = text[i + 1]
            if nxt in _VALID_JSON_ESCAPES:
                out.append(ch)
                out.append(nxt)
                i += 2
            elif nxt == "'":
                # \' is invalid JSON; drop the backslash
                out.append("'")
                i += 2
            else:
                # \s, \d, \(, etc. — double the backslash
                out.append("\\\\")
                out.append(nxt)
                i += 2
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _extract_json(text: str) -> str:
    """Extract JSON from LLM output that may contain surrounding text.

    Tries each extraction strategy with raw text first, then with
    escape-fixed text. Strategies:
    1. Raw text as JSON
    2. JSON inside markdown code fences
    3. Outermost { ... } braces (handles explanation text around JSON)
    """
    for candidate_text in [text.strip(), _fix_json_escapes(text.strip())]:
        # 1. Raw JSON
        try:
            json.loads(candidate_text)
            return candidate_text
        except json.JSONDecodeError:
            pass

        # 2. Markdown code block
        match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", candidate_text, re.DOTALL)
        if match:
            candidate = match.group(1).strip()
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

        # 3. Find outermost { ... } braces
        first = candidate_text.find("{")
        last = candidate_text.rfind("}")
        if first != -1 and last > first:
            candidate = candidate_text[first:last + 1]
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                pass

    raise ValueError("Could not parse JSON from response")


def parse_replication_plan_response(response: str) -> ReplicationPlan:
    """Parse a replication plan from LLM response text.

    Handles raw JSON, markdown code blocks, and JSON embedded in
    surrounding explanation text.
    """
    raw = _extract_json(response)
    data = json.loads(raw)
    return ReplicationPlan.from_dict(data)


def gather_evidence(replication_dir: Path) -> Optional[ExecutionEvidence]:
    """Gather execution evidence from a replication output directory.

    Expects:
      - replication_dir/replication_log.json (required)
      - replication_dir/evidence_summary.json (optional, for environment info)
    """
    if not replication_dir.exists():
        return None

    log_path = replication_dir / REPLICATION_LOG_FILE
    if not log_path.exists():
        return None

    try:
        log_data = json.loads(log_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return None

    # Read optional summary for environment info
    summary_path = replication_dir / EVIDENCE_SUMMARY_FILE
    environment = {}
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            environment = summary.get("environment", {})
        except (json.JSONDecodeError, ValueError):
            pass  # proceed with empty environment

    step_outcomes = [StepOutcome.from_dict(s) for s in log_data.get("step_outcomes", [])]

    return ExecutionEvidence(
        environment=environment,
        step_outcomes=step_outcomes,
        # Not written by the agent — stamped into its log by
        # record_early_termination() after the heartbeat loop cuts a run off.
        terminated_early=bool(log_data.get("terminated_early", False)),
        termination_reason=log_data.get("termination_reason", "") or "",
    )


def record_early_termination(replication_dir: Path, reason: str) -> bool:
    """Stamp the heartbeat loop's early-termination marker into
    ``replication_log.json`` so it outlives the run that set it.

    ``runner.py::_replicate`` learns that a run was cut off at its time budget,
    but the agent's own log has no idea — the agent was killed. Keeping the
    marker only on the in-memory evidence object would lose it the moment a
    later run re-reads that log from disk (a resumed pipeline with
    ``--max-iters > 1`` recomputes the execution facts from it), silently
    re-labelling a budget-truncated run as one that finished on its own and
    handing the manager a lazy-agent verdict for what was really a clock cutoff.

    Merges into whatever the agent wrote rather than rewriting the file, and is
    called only after the agent's final write. Returns True when the marker was
    persisted. Never raises: a missing or unparseable log means there is no
    evidence to annotate, which ``compute_execution_facts`` already reports as
    its own, louder signal.
    """
    log_path = replication_dir / REPLICATION_LOG_FILE
    try:
        log_data = json.loads(log_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return False
    if not isinstance(log_data, dict):
        return False

    log_data["terminated_early"] = True
    log_data["termination_reason"] = reason
    try:
        log_path.write_text(json.dumps(log_data, indent=2), encoding="utf-8")
    except OSError:
        return False
    return True
