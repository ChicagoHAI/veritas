"""Contracts for tool-using agents that produce artifacts in a workspace."""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Mapping, Optional, Protocol


@dataclass(frozen=True)
class SessionRequest:
    session_id: str
    operation: Literal["start", "resume"]

    def __post_init__(self) -> None:
        if not self.session_id or self.operation not in ("start", "resume"):
            raise ValueError("A session requires an ID and a start or resume operation")


@dataclass(frozen=True)
class AgentRequest:
    prompt: str
    working_dir: Optional[Path]
    transcript_path: Path
    timeout: Optional[float]
    append: bool = False
    # An explicit environment replaces inheritance, including for SDK adapters.
    env: Optional[Mapping[str, str]] = field(default=None, repr=False)
    session: Optional[SessionRequest] = None


@dataclass(frozen=True)
class AgentResult:
    success: bool
    timed_out: bool = False
    exit_code: Optional[int] = None
    error: Optional[str] = None


@dataclass(frozen=True)
class AgentCapabilities:
    supports_resume: bool = False


class AgentBackend(Protocol):
    @property
    def capabilities(self) -> AgentCapabilities: ...

    def invoke(self, request: AgentRequest) -> AgentResult: ...
