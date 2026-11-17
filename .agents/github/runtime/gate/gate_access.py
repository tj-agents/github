"""Which access applies to an agent: the repository's own declaration, else its owner's.

1. The declaration committed at the repository's HEAD.
2. The owner's default: the same file on the default branch of `<owner>/.github`,
   read from GitHub and cached briefly. The owner is the `origin` remote's account.
3. Otherwise `human`.

Only agents resolve access; humans never reach this module.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from gate_app import Apps, Fetch, GitHubError
from gate_declaration import DECLARATION, Access, HumanAccess, committed, parse
from gate_error import GateError
from gate_owner import origin_owner
from gate_platform import Platform, PrivateStateError
from gate_private import read_private, write_private

OWNER_REPOSITORY = ".github"
CACHE_SECONDS = 600
MAX_DECLARATION_BYTES = 64 * 1024


class OwnerDefaultError(GateError):
    pass


@dataclass(frozen=True, slots=True)
class Fetched:
    """An owner's default declaration as GitHub returned it; None when it has none."""

    text: str | None


@dataclass(frozen=True, slots=True)
class Declarations:
    """Resolve access for agents, reading owner defaults through `fetch`."""

    apps: Apps
    fetch: Fetch
    cache: Path
    platform: Platform

    def resolve(self, cwd: Path, environment: Mapping[str, str], now: float) -> Access:
        declared = committed(cwd, environment)
        if declared is not None:
            return declared
        owner = origin_owner(cwd, environment)
        if owner is None:
            return HumanAccess()
        return self.owner_default(owner, now)

    def owner_default(self, owner: str, now: float) -> Access:
        token = self.reader(owner, now)
        if token is None:
            # No App enrolled here is installed on this owner, so no default could make
            # agents act as an App: never look the owner up, and keep its normal access.
            return HumanAccess()
        fetched = self.cached(owner, now)
        if fetched is None:
            fetched = self.read(owner, token)
            write_private(
                self.cache_path(owner),
                json.dumps({"fetched": now, "text": fetched.text}).encode(),
                self.platform,
            )
        if fetched.text is None:
            return HumanAccess()
        try:
            return parse(fetched.text)
        except GateError as error:
            raise OwnerDefaultError(
                f"{owner}/{OWNER_REPOSITORY}: {error}; agents in {owner} repositories "
                "stop until it is fixed or the repository commits its own declaration"
            ) from error

    def cache_path(self, owner: str) -> Path:
        return self.cache / f"{owner.lower()}.json"

    def cached(self, owner: str, now: float) -> Fetched | None:
        path = self.cache_path(owner)
        if not path.is_file():
            return None
        try:
            value = json.loads(read_private(path, self.platform))
        except (ValueError, PrivateStateError):
            return None
        if not isinstance(value, dict):
            return None
        fetched, text = value.get("fetched"), value.get("text")
        if (
            not isinstance(fetched, int | float)
            or not 0 <= now - fetched < CACHE_SECONDS
        ):
            return None
        if text is not None and not isinstance(text, str):
            return None
        return Fetched(text)

    def reader(self, owner: str, now: float) -> str | None:
        """An enrolled App's installation token for `owner`, if one covers it."""
        for app in self.apps.enrolled():
            covered = app.installations(app.descriptor(), now, refresh=False)
            if owner.lower() in covered:
                return app.installation_token(owner, now)
        return None

    def read(self, owner: str, token: str) -> Fetched:
        path = f"/repos/{owner}/{OWNER_REPOSITORY}/contents/{DECLARATION}"
        try:
            response = self.fetch(path, token, "GET")
        except GitHubError as error:
            if error.status == 404:
                return Fetched(None)
            raise OwnerDefaultError(
                f"cannot read {owner}'s default access from {owner}/{OWNER_REPOSITORY} "
                f"({error}); agents in {owner} repositories stop until it can be read"
            ) from error
        where = f"{owner}/{OWNER_REPOSITORY}/{DECLARATION}"
        return Fetched(decoded(response, where))


def decoded(response: object, where: str) -> str:
    """The text of a file GitHub's contents API returned for `where`."""
    if (
        not isinstance(response, Mapping)
        or response.get("type") != "file"
        or response.get("encoding") != "base64"
        or not isinstance(response.get("content"), str)
    ):
        raise OwnerDefaultError(f"GitHub did not return {where} as a file")
    try:
        content = base64.b64decode(response["content"])
    except (binascii.Error, ValueError) as error:
        raise OwnerDefaultError(f"GitHub returned unreadable {where}") from error
    if len(content) > MAX_DECLARATION_BYTES:
        raise OwnerDefaultError(f"{where} is too large to be a declaration")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError as error:
        raise OwnerDefaultError(f"{where} is not UTF-8") from error
