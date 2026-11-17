import pytest
from gate_owner import gh_target, remote_owner


@pytest.mark.parametrize(
    ("arguments", "owners"),
    [
        (("pr", "merge", "-R", "sample-org/core"), ("sample-org",)),
        (("pr", "view", "--repo=Sample-Org/auth"), ("Sample-Org",)),
        (("pr", "list", "-Ralex/winwrap"), ("alex",)),
        (("api", "repos/sample-org/core/pulls/1/merge", "-X", "PUT"), ("sample-org",)),
        (("api", "https://api.github.com/orgs/sample-org/rulesets"), ("sample-org",)),
        (("pr", "view", "-R", "https://github.com/sample-org/b2b"), ("sample-org",)),
        (("pr", "view", "-R", "github.com/sample-org/b2b"), ("sample-org",)),
        (
            ("pr", "review", "--approve", "https://github.com/sample-org/core/pull/5"),
            ("sample-org",),
        ),
        (
            ("pr", "view", "--json", "title", "https://github.com/other/cris/pull/5"),
            ("other",),
        ),
        (("repo", "delete", "alex/notes", "--yes"), ("alex",)),
        (("repo", "list", "sample-org", "--limit", "10"), ("sample-org",)),
        (("repo", "ls", "--json", "name", "sample-org"), ("sample-org",)),
        (("repo", "list", "-L", "5", "-l", "python", "sample-org"), ("sample-org",)),
        (("repo", "list", "--limit", "10"), ()),
        (("secret", "list", "--org", "sample-org"), ("sample-org",)),
        (("variable", "list", "-o", "sample-org"), ("sample-org",)),
        (("ruleset", "list", "--org=sample-org"), ("sample-org",)),
        (("search", "repos", "--owner", "alex,sample-org"), ("alex", "sample-org")),
        (("project", "list", "--owner", "sample-org"), ("sample-org",)),
        (("api", "graphql"), ()),
        (
            ("issue", "create", "-t", "x", "-b", "See https://github.com/other/cris"),
            (),
        ),
        (
            ("api", "repos/sample-org/x/issues", "-f", "body=https://github.com/o/r"),
            ("sample-org",),
        ),
        (
            ("repo", "clone", "-u", "upstream", "sample-org/personal", "destination"),
            ("sample-org",),
        ),
    ],
)
def test_gh_target_names_the_accounts_a_command_addresses(
    arguments: tuple[str, ...], owners: tuple[str, ...]
) -> None:
    assert gh_target(arguments).owners == owners


@pytest.mark.parametrize(
    ("arguments", "hostname"),
    [
        (("api", "user", "--hostname", "enterprise.example"), "enterprise.example"),
        (("api", "user", "--hostname=github.com"), "github.com"),
        (("issue", "create", "--title", "--hostname", "--body", "x"), None),
    ],
)
def test_gh_target_reads_the_host_only_from_a_hostname_option(
    arguments: tuple[str, ...], hostname: str | None
) -> None:
    assert gh_target(arguments).hostname == hostname


@pytest.mark.parametrize(
    ("url", "owner"),
    [
        ("https://x-access-token@github.com/sample-org/core.git", "sample-org"),
        ("git@github.com:other/cris.git", "other"),
        ("https://dev.azure.com/example/_git/service", None),
        ("https://notgithub.com/sample-org/core", None),
    ],
)
def test_remote_owner_comes_only_from_github_remotes(
    url: str, owner: str | None
) -> None:
    assert remote_owner(url) == owner
