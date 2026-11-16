from collections.abc import Mapping
from enum import Enum


class AgentHost(Enum):
    """Hosts whose sessions mark every child process with these variables."""

    CLAUDE = (
        "claude",
        "CLAUDE_CODE_SESSION_ID",
        ("CLAUDECODE", "CLAUDE_CODE_SESSION_ID"),
    )
    CODEX = ("codex", "CODEX_THREAD_ID", ("CODEX_THREAD_ID",))

    def __init__(
        self, handle: str, session_variable: str, markers: tuple[str, ...]
    ) -> None:
        self.handle = handle
        self.session_variable = session_variable
        self.markers = markers

    def session(self, environment: Mapping[str, str]) -> str | None:
        """This host's agent-store session handle, such as `claude:<id>`."""
        native = environment.get(self.session_variable)
        return f"{self.handle}:{native}" if native else None


def agent_host(environment: Mapping[str, str]) -> AgentHost | None:
    for host in AgentHost:
        if any(environment.get(marker) for marker in host.markers):
            return host
    return None
