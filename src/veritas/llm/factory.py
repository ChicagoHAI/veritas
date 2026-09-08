"""Construct agent backends without inspecting credentials or starting a process."""

from veritas.llm.cli import CLI_SPECS, CliAgentBackend
from veritas.llm.runtime import AgentBackend

SUPPORTED_PROVIDERS = tuple(CLI_SPECS)


def create_agent_backend(provider: str) -> AgentBackend:
    try:
        spec = CLI_SPECS[provider.lower()]
    except KeyError:
        raise ValueError(f"Unknown provider: {provider}") from None
    return CliAgentBackend(spec)
