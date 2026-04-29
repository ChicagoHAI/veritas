"""Severity rating for fixes applied during replication."""

from dataclasses import dataclass, field
from typing import List, Dict, Any


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
