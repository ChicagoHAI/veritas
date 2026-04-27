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
class FixSeverityRating:
    """Severity assessment for a single applied fix."""
    fix_description: str
    severity: str
    rationale: str
    reproducibility_impact: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fix_description": self.fix_description,
            "severity": self.severity,
            "rationale": self.rationale,
            "reproducibility_impact": self.reproducibility_impact,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FixSeverityRating":
        return cls(
            fix_description=data.get("fix_description", ""),
            severity=data.get("severity", "unknown"),
            rationale=data.get("rationale", ""),
            reproducibility_impact=data.get("reproducibility_impact", ""),
        )


@dataclass
class FixSeverityAssessment:
    """Overall assessment of all fixes applied during replication."""
    fixes: List[FixSeverityRating] = field(default_factory=list)
    summary: str = ""
    total_fixes: int = 0
    minor_count: int = 0
    major_count: int = 0
    critical_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "fixes": [f.to_dict() for f in self.fixes],
            "summary": self.summary,
            "total_fixes": self.total_fixes,
            "minor_count": self.minor_count,
            "major_count": self.major_count,
            "critical_count": self.critical_count,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FixSeverityAssessment":
        return cls(
            fixes=[FixSeverityRating.from_dict(f) for f in data.get("fixes", [])],
            summary=data.get("summary", ""),
            total_fixes=data.get("total_fixes", 0),
            minor_count=data.get("minor_count", 0),
            major_count=data.get("major_count", 0),
            critical_count=data.get("critical_count", 0),
        )

    @classmethod
    def empty(cls) -> "FixSeverityAssessment":
        return cls()


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
