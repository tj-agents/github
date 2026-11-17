"""The agent session and work items a command belongs to, read from agent-store."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from gate_agent import AgentHost
from gate_error import GateError
from gate_process import Run, reason

TIMEOUT_SECONDS = 30


class SessionError(GateError):
    pass


@dataclass(frozen=True, slots=True)
class AgentStore:
    executable: str
    environment: Mapping[str, str]
    run: Run

    def query(self, *arguments: str) -> dict[str, Any] | None:
        """agent-store's JSON data, an untyped boundary; None when not found."""
        result = self.run(
            [self.executable, *arguments], self.environment, TIMEOUT_SECONDS
        )
        try:
            document = json.loads(result.stdout)
        except ValueError as error:
            raise SessionError(
                f"agent-store {arguments[0]} failed: {reason(result)}"
            ) from error
        if not isinstance(document, dict):
            raise SessionError(f"agent-store {arguments[0]} returned invalid JSON")
        if document.get("ok"):
            data = document.get("data")
            return data if isinstance(data, dict) else None
        failure = document.get("error") or {}
        if isinstance(failure, dict) and failure.get("code") == "not-found":
            return None
        message = failure.get("message") if isinstance(failure, dict) else None
        raise SessionError(
            f"agent-store {arguments[0]} failed: {message or reason(result)}"
        )


def linked(session: Mapping[str, Any]) -> list[str]:
    links = session.get("links") or []
    return [link["item"]["ref"] for link in links]


def current_session(
    store: AgentStore, environment: Mapping[str, str], handle: str | None
) -> tuple[str | None, list[str]] | None:
    """The originating session and its items: explicit, launch, then host session."""
    if handle is not None:
        data = store.query("show", handle)
        if data is None:
            raise SessionError(f"unknown agent session {handle}")
        session = data["session"]
        return session["session"], linked(session)
    if launch_id := environment.get("TJ_RUN_LAUNCH_ID"):
        data = store.query("show", "--launch-id", launch_id)
        if data is None:
            return None
        launch, session = data["launch"], data.get("session")
        items = [launch["item"]["ref"]] if launch.get("item") else []
        if isinstance(session, dict):
            items += linked(session)
        return launch.get("session"), items
    for host in AgentHost:
        native = host.session(environment)
        if native:
            data = store.query("show", native)
            if data is None:
                return None
            session = data["session"]
            return session["session"], linked(session)
    return None


def descendant_items(store: AgentStore, handle: str) -> list[str]:
    """Items of sessions and launches started, directly or not, from `handle`."""
    launches_data = store.query("launches")
    items_data = store.query("items")
    launches = list(launches_data["launches"]) if launches_data else []
    candidates: list[str] = []
    for item in items_data["items"] if items_data else []:
        candidates.extend(link["session"] for link in item["sessions"])
    for launch in launches:
        candidates.extend(
            launch[key] for key in ("parent", "session") if launch.get(key)
        )
    sessions: dict[str, dict[str, Any]] = {}
    inspected = {handle}
    for candidate in candidates:
        if candidate in inspected:
            continue
        inspected.add(candidate)
        data = store.query("show", candidate)
        if data is None:
            continue
        session = data["session"]
        sessions[candidate] = session
        if session.get("parent"):
            candidates.append(str(session["parent"]))
    found: list[str] = []
    frontier, seen = [handle], {handle}
    while frontier:
        parent = frontier.pop()
        children = [
            child
            for child, session in sessions.items()
            if session.get("parent") == parent
        ]
        for launch in launches:
            if launch.get("parent") != parent:
                continue
            if launch.get("item"):
                found.append(launch["item"]["ref"])
            if launch.get("session"):
                children.append(launch["session"])
        for child in children:
            if child in seen:
                continue
            seen.add(child)
            frontier.append(child)
            if child in sessions:
                found.extend(linked(sessions[child]))
    return found
