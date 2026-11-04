from __future__ import annotations

import html
import urllib.parse
from typing import TYPE_CHECKING

from github_tokens import TokenKind

if TYPE_CHECKING:
    from access_core import Profile

kind = TokenKind.FINE_GRAINED


def token_url(profile: Profile) -> str:
    return (
        "https://github.com/settings/personal-access-tokens/new?"
        + urllib.parse.urlencode(
            {
                "name": f"Employer {profile.organization} {profile.login}",
                "description": "Organization-scoped developer access",
                "target_name": profile.organization,
                "expires_in": str(profile.expires_in),
                **profile.permissions,
            }
        )
    )


def instructions(profile: Profile) -> tuple[str, str]:
    owner, login = html.escape(profile.organization), html.escape(profile.login)
    selection = (
        "All repositories"
        if profile.repositories == ("*",)
        else "Only select repositories, then select each listed repository"
    )
    return (
        f"Use GitHub account {login}. Select resource owner {owner} and {selection}. "
        "Repository selection is not prefilled. "
        "Complete any required organization approval before submitting.",
        f"I confirmed resource owner {owner} and repository access in GitHub.",
    )
