from collections.abc import Mapping
from enum import Enum


class AgentHost(Enum):
    """Hosts whose sessions mark every child process with these variables."""

    CLAUDE = ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID")
    CODEX = ("CODEX_THREAD_ID",)

    def __init__(self, *markers: str) -> None:
        self.markers = markers


def agent_host(environment: Mapping[str, str]) -> AgentHost | None:
    for host in AgentHost:
        if any(environment.get(marker) for marker in host.markers):
            return host
    return None
