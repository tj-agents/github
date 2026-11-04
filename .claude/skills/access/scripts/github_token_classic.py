from __future__ import annotations

import html
import urllib.parse
from typing import TYPE_CHECKING

from github_tokens import TokenKind

if TYPE_CHECKING:
    from access_core import Profile

kind = TokenKind.CLASSIC


def token_url(profile: Profile) -> str:
    scopes = ["repo"]
    if profile.permissions.get("workflows") == "write":
        scopes.append("workflow")
    return "https://github.com/settings/tokens/new?" + urllib.parse.urlencode(
        {
            "description": f"Employer {profile.organization} {profile.login}",
            "scopes": ",".join(scopes),
        }
    )


def instructions(profile: Profile) -> tuple[str, str]:
    owner, login = html.escape(profile.organization), html.escape(profile.login)
    scopes = (
        "repo and workflow"
        if profile.permissions.get("workflows") == "write"
        else "repo"
    )
    return (
        f"Use GitHub account {login}. Create a classic token with {scopes} scope. "
        f"On its settings page choose Configure SSO and authorize {owner}. "
        "Classic tokens can reach other repositories your account can access; "
        f"this tool routes access only to {owner}.",
        f"I authorized this classic token for {owner} SSO.",
    )
