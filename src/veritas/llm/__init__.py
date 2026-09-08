"""Independent factory and execution contracts for Veritas's LLM agents."""

from veritas.llm.factory import SUPPORTED_PROVIDERS, create_agent_backend
from veritas.llm.runtime import (
    AgentBackend,
    AgentCapabilities,
    AgentRequest,
    AgentResult,
    SessionRequest,
)

__all__ = [
    "SUPPORTED_PROVIDERS",
    "AgentBackend",
    "AgentCapabilities",
    "AgentRequest",
    "AgentResult",
    "SessionRequest",
    "create_agent_backend",
]
