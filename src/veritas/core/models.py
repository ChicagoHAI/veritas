"""Data models for replication plan and execution evidence."""

import json
from dataclasses import dataclass, field
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
    suggested_fix: Optional[str] = None
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
            "suggested_fix": self.suggested_fix,
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
            suggested_fix=data.get("suggested_fix"),
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
