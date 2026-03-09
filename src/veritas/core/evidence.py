"""Evidence parsing for replication results."""

import json
import re
from pathlib import Path
from typing import Optional

from veritas.core.models import ReplicationPlan, ExecutionEvidence, StepOutcome


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

    raise ValueError("Could not parse replication plan from response")


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

    log_path = replication_dir / "replication_log.json"
    if not log_path.exists():
        return None

    try:
        log_data = json.loads(log_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        return None

    # Read optional summary for environment info
    summary_path = replication_dir / "evidence_summary.json"
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
    )
