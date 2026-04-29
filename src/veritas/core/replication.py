"""Replication plan, execution evidence, and parsers for both."""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List, Dict, Any


@dataclass
class ReplicationStep:
    """A single step in a replication plan."""
    id: int
    description: str
    command_hint: str
    expected_outcome: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "description": self.description,
            "command_hint": self.command_hint,
            "expected_outcome": self.expected_outcome,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReplicationStep":
        return cls(
            id=data["id"],
            description=data["description"],
            command_hint=data.get("command_hint", ""),
            expected_outcome=data.get("expected_outcome", ""),
        )


@dataclass
class ReplicationPlan:
    """A plan for replicating a paper's results."""
    environment: Dict[str, Any]
    steps: List[ReplicationStep]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "environment": self.environment,
            "steps": [s.to_dict() for s in self.steps],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ReplicationPlan":
        return cls(
            environment=data.get("environment", {}),
            steps=[ReplicationStep.from_dict(s) for s in data.get("steps", [])],
        )

    @classmethod
    def from_json(cls, raw: str) -> "ReplicationPlan":
        return cls.from_dict(json.loads(raw))


@dataclass
class AppliedFix:
    """A fix applied by the replication agent during execution."""
    file_path: str
    description: str
    original_error: str
    diff_snippet: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "file_path": self.file_path,
            "description": self.description,
            "original_error": self.original_error,
            "diff_snippet": self.diff_snippet,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AppliedFix":
        return cls(
            file_path=data.get("file_path", ""),
            description=data.get("description", ""),
            original_error=data.get("original_error", ""),
            diff_snippet=data.get("diff_snippet", ""),
        )


@dataclass
class StepOutcome:
    """The outcome of executing a single replication step."""
    step_id: int
    description: str
    command_executed: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    output_files: List[str] = field(default_factory=list)
    duration_seconds: float = 0.0
    fixes_applied: List[AppliedFix] = field(default_factory=list)
    code_modified: bool = False
    notes: str = ""

    @property
    def succeeded(self) -> bool:
        return self.exit_code == 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "description": self.description,
            "command_executed": self.command_executed,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "output_files": self.output_files,
            "duration_seconds": self.duration_seconds,
            "fixes_applied": [f.to_dict() for f in self.fixes_applied],
            "code_modified": self.code_modified,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "StepOutcome":
        return cls(
            step_id=data["step_id"],
            description=data["description"],
            command_executed=data["command_executed"],
            exit_code=data["exit_code"],
            stdout=data.get("stdout", ""),
            stderr=data.get("stderr", ""),
            output_files=data.get("output_files", []),
            duration_seconds=data.get("duration_seconds", 0.0),
            fixes_applied=[AppliedFix.from_dict(f) for f in data.get("fixes_applied", [])],
            code_modified=data.get("code_modified", False),
            notes=data.get("notes", ""),
        )


@dataclass
class ExecutionEvidence:
    """Evidence collected from a replication attempt."""
    environment: Dict[str, Any]
    step_outcomes: List[StepOutcome]

    @property
    def steps_attempted(self) -> int:
        return len(self.step_outcomes)

    @property
    def steps_succeeded(self) -> int:
        return sum(1 for s in self.step_outcomes if s.succeeded)

    @property
    def steps_failed(self) -> int:
        return sum(1 for s in self.step_outcomes if not s.succeeded)

    @property
    def total_duration_seconds(self) -> float:
        return sum(s.duration_seconds for s in self.step_outcomes)

    @property
    def all_output_files(self) -> List[str]:
        files = []
        for s in self.step_outcomes:
            files.extend(s.output_files)
        return files

    @property
    def all_fixes_applied(self) -> List[AppliedFix]:
        fixes = []
        for s in self.step_outcomes:
            fixes.extend(s.fixes_applied)
        return fixes

    def to_dict(self) -> Dict[str, Any]:
        return {
            "environment": self.environment,
            "step_outcomes": [s.to_dict() for s in self.step_outcomes],
            "steps_attempted": self.steps_attempted,
            "steps_succeeded": self.steps_succeeded,
            "steps_failed": self.steps_failed,
            "total_duration_seconds": self.total_duration_seconds,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecutionEvidence":
        return cls(
            environment=data.get("environment", {}),
            step_outcomes=[StepOutcome.from_dict(s) for s in data.get("step_outcomes", [])],
        )


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
