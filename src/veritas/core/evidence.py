"""Evidence parsing for replication results."""

import json
import re
from pathlib import Path
from typing import Optional

from veritas.core.models import ReplicationPlan, ExecutionEvidence, StepOutcome


def parse_replication_plan_response(response: str) -> ReplicationPlan:
    """Parse a replication plan from LLM response text.

    Handles both raw JSON and JSON embedded in markdown code blocks.
    """
    text = response.strip()

    # Try raw JSON first
    try:
        data = json.loads(text)
        return ReplicationPlan.from_dict(data)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    match = re.search(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            return ReplicationPlan.from_dict(data)
        except json.JSONDecodeError:
            pass

    raise ValueError(f"Could not parse replication plan from response")


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
