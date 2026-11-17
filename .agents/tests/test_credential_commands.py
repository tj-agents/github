from __future__ import annotations

import json
from dataclasses import replace

import pytest
from credentials import commands
from credentials.commands import Repository, parse_repository, validate_gh_command
from credentials.model import CredentialError
from credentials.repository_scope import RepositoryScope


@pytest.fixture
def profile() -> RepositoryScope:
    return RepositoryScope(
        "employer", ("private-project", "other-project"), {"contents": "read"}
    )


@pytest.mark.parametrize(
    "value",
    [
        "employer/private-project",
        "https://github.com/employer/private-project",
        "https://github.com/employer/private-project.git",
    ],
)
def test_parse_repository_accepts_exact_allowlisted_employer_repository(
    profile: RepositoryScope, value: str
) -> None:
    assert parse_repository(profile, value) == Repository("employer", "private-project")


def test_organization_wide_access_accepts_new_repositories_and_denies_other_owners(
    profile: RepositoryScope,
) -> None:
    selected = replace(profile, repositories=("*",))
    assert parse_repository(selected, "employer/new-project") == Repository(
        "employer", "new-project"
    )
    assert (
        validate_gh_command(
            selected, ("pr", "list", "--repo", "employer/new-project")
        ).repository.name
        == "new-project"
    )
    with pytest.raises(CredentialError, match="repository-denied"):
        parse_repository(selected, "other/new-project")


@pytest.mark.parametrize(
    "value",
    [
        "other/private-project",
        "employer/unlisted",
        "git@github.com:employer/private-project.git",
        "http://github.com/employer/private-project",
        "https://evil.example/employer/private-project",
        "https://github.com/employer/private-project/extra",
        "employer/%70rivate-project",
        "employer/../private-project",
    ],
)
def test_parse_repository_rejects_other_hosts_and_ambiguous_paths(
    profile: RepositoryScope, value: str
) -> None:
    with pytest.raises(CredentialError):
        parse_repository(profile, value)


@pytest.mark.parametrize(
    "arguments",
    [
        ("pr", "list", "--repo", "employer/private-project"),
        ("pr", "view", "42", "--repo=employer/private-project"),
        ("issue", "view", "https://github.com/employer/private-project/issues/42"),
        ("run", "list"),
        ("release", "view", "v1"),
    ],
)
def test_gh_validator_allows_bounded_supported_commands(
    profile: RepositoryScope, arguments: tuple[str, ...]
) -> None:
    invocation = validate_gh_command(
        profile, arguments, default_repository="employer/private-project"
    )
    assert invocation.repository == Repository("employer", "private-project")
    assert invocation.arguments[-2:] == ("--repo", "employer/private-project")


@pytest.mark.parametrize(
    "arguments",
    [
        ("auth", "status"),
        ("extension", "install", "evil"),
        ("api", "https://api.github.com/user"),
        ("api", "/repos/employer/private-project/../issues"),
        ("api", "/repos/employer/private-project/issues", "-H", "Authorization: evil"),
        ("pr", "view", "42", "--hostname", "evil.example"),
        ("pr", "view", "https://github.com/employer/other-project/pull/42"),
        ("pr", "view", "42", "--repo", "other/private-project"),
        (
            "pr",
            "view",
            "42",
            "--repo",
            "employer/private-project",
            "--repo",
            "employer/other-project",
        ),
        ("graphql", "-f", "query={viewer{login}}"),
    ],
)
def test_gh_validator_rejects_broad_or_mixed_scope_before_vault(
    profile: RepositoryScope, arguments: tuple[str, ...]
) -> None:
    with pytest.raises(CredentialError):
        validate_gh_command(
            profile, arguments, default_repository="employer/private-project"
        )


def test_gh_api_is_rest_get_only_inside_selected_repository(
    profile: RepositoryScope,
) -> None:
    invocation = validate_gh_command(
        profile,
        (
            "api",
            "/repos/employer/private-project/issues",
            "-X",
            "GET",
            "-f",
            "state=open",
            "--paginate",
            "--repo",
            "employer/private-project",
        ),
    )
    assert invocation.arguments[:2] == ("api", "/repos/employer/private-project/issues")
    with pytest.raises(CredentialError, match="pending review"):
        validate_gh_command(
            profile,
            (
                "api",
                "/repos/employer/private-project/issues",
                "-X",
                "POST",
                "--repo",
                "employer/private-project",
            ),
        )


def test_pending_review_restricts_endpoint_permission_and_payload(
    profile: RepositoryScope,
) -> None:
    writable = replace(profile, permissions={"pull_requests": "write"})
    args = (
        "api",
        "repos/employer/private-project/pulls/25/reviews",
        "-X",
        "POST",
        "--input",
        "-",
    )
    invocation = validate_gh_command(
        writable, args, default_repository="employer/private-project"
    )
    assert invocation.arguments[-4:] == ("-X", "POST", "--input", "-")
    with pytest.raises(CredentialError, match="pending review"):
        validate_gh_command(
            profile, args, default_repository="employer/private-project"
        )
    with pytest.raises(CredentialError, match="pending review"):
        validate_gh_command(
            writable,
            (
                "api",
                "repos/employer/private-project/issues/1/comments",
                "-X",
                "POST",
                "--input",
                "-",
            ),
            default_repository="employer/private-project",
        )
    comment = {"path": "a.cs", "line": 1, "side": "RIGHT", "body": "Review\ncomment"}
    data = {
        "commit_id": "a" * 40,
        "comments": [comment],
    }
    assert json.loads(commands.validate_pending_review_input(json.dumps(data))) == data
    for bad in [
        {**data, "event": "APPROVE"},
        {**data, "event": "COMMENT"},
        {**data, "commit_id": "main"},
        {**data, "comments": []},
        {**data, "comments": [{**comment, "line": True}]},
    ]:
        with pytest.raises(CredentialError):
            commands.validate_pending_review_input(json.dumps(bad))


def test_api_read_supports_slurped_pagination(profile: RepositoryScope) -> None:
    invocation = validate_gh_command(
        profile,
        (
            "api",
            "repos/employer/private-project/pulls/25/reviews",
            "--paginate",
            "--slurp",
        ),
        default_repository="employer/private-project",
    )
    assert "--slurp" in invocation.arguments


def test_gh_api_normalizes_relative_path_forces_get_and_rejects_scope_bypass(
    profile: RepositoryScope,
) -> None:
    invocation = validate_gh_command(
        profile,
        (
            "api",
            "repos/employer/private-project/issues",
            "-f",
            "state=open",
            "--repo",
            "employer/private-project",
        ),
    )
    assert invocation.arguments == (
        "api",
        "/repos/employer/private-project/issues",
        "-f",
        "state=open",
        "-X",
        "GET",
    )
    permitted = replace(
        profile, repositories=("private-project", "private-project-other")
    )
    for arguments in [
        ("api", "/repos/employer/private-project-other/issues"),
        ("api", "/repos/employer/private-project/issues", "-F", "body=@secret"),
        ("api", "/repos/employer/private-project/issues", "-f", "unknown=value"),
    ]:
        with pytest.raises(CredentialError):
            validate_gh_command(
                permitted,
                (*arguments, "--repo", "employer/private-project"),
            )


@pytest.mark.parametrize(
    "arguments",
    [
        ("repo", "view", "employer/other-project"),
        ("pr", "view", "https://github.com/employer/private-project/issues/4"),
        ("issue", "view", "https://github.com/employer/private-project/pull/4"),
        ("pr", "view", "employer/private-project"),
        (
            "release",
            "view",
            "https://github.com/employer/private-project/releases/tag/v1",
        ),
        ("pr", "list", "--web"),
        ("pr", "create", "--title", "title"),
        ("pr", "comment", "4"),
        ("pr", "review", "4", "--request-changes"),
        ("pr", "merge", "4"),
        (
            "pr",
            "create",
            "--title",
            "title",
            "--body",
            "body",
            "--head",
            "other:branch",
        ),
        ("pr", "create", "--title", "--body"),
        ("issue", "create", "--body", "body"),
        ("pr", "comment", "4", "--delete-last"),
        ("issue", "comment", "4", "--delete-last"),
    ],
)
def test_gh_validator_rejects_editor_browser_and_mixed_owner_paths(
    profile: RepositoryScope, arguments: tuple[str, ...]
) -> None:
    with pytest.raises(CredentialError):
        validate_gh_command(
            profile, arguments, default_repository="employer/private-project"
        )


@pytest.mark.parametrize(
    "arguments",
    [
        ("pr", "ready", "4"),
        ("pr", "close", "4", "--delete-branch"),
        ("pr", "reopen", "4"),
        ("pr", "review", "4", "--approve"),
        ("pr", "merge", "4", "--squash", "--match-head-commit", "a" * 40),
        ("pr", "create", "--fill", "--head", "branch"),
        ("pr", "comment", "4", "--body-file", "C:/work/comment.md"),
    ],
)
def test_gh_validator_allows_bounded_noninteractive_pr_workflows(
    profile: RepositoryScope, arguments: tuple[str, ...]
) -> None:
    validate_gh_command(
        profile, arguments, default_repository="employer/private-project"
    )


@pytest.mark.parametrize(
    "arguments",
    [
        ("pr", "create", "--title", "title", "--body", "body", "--head", "branch"),
        ("pr", "review", "4", "--approve"),
    ],
)
def test_gh_validator_allows_create_and_review_with_default_repository(
    profile: RepositoryScope, arguments: tuple[str, ...]
) -> None:
    invocation = validate_gh_command(
        profile, arguments, default_repository="employer/private-project"
    )
    assert invocation.arguments[-2:] == ("--repo", "employer/private-project")


@pytest.mark.parametrize(
    "arguments",
    [
        (
            "pr",
            "create",
            "--title",
            "title",
            "--body",
            "body",
            "--head",
            "branch",
            "--head=other",
        ),
        (
            "pr",
            "create",
            "--title=title",
            "--body=body",
            "--head=branch",
            "--head",
            "other",
        ),
    ],
)
def test_gh_validator_rejects_repeated_singleton_options(
    profile: RepositoryScope, arguments: tuple[str, ...]
) -> None:
    with pytest.raises(CredentialError, match="repeated gh option"):
        validate_gh_command(
            profile, arguments, default_repository="employer/private-project"
        )


def test_gh_validator_does_not_treat_title_value_as_body(
    profile: RepositoryScope,
) -> None:
    with pytest.raises(CredentialError):
        validate_gh_command(
            profile,
            ("pr", "create", "--title", "--body"),
            default_repository="employer/private-project",
        )


def test_gh_validator_treats_body_value_as_value_not_head(
    profile: RepositoryScope,
) -> None:
    invocation = validate_gh_command(
        profile,
        ("pr", "create", "--title", "title", "--body", "--head", "--head", "branch"),
        default_repository="employer/private-project",
    )
    assert invocation.arguments[-2:] == ("--repo", "employer/private-project")


def test_gh_validator_checks_effective_head_after_option_like_title(
    profile: RepositoryScope,
) -> None:
    with pytest.raises(CredentialError, match="--head must name a branch"):
        validate_gh_command(
            profile,
            (
                "pr",
                "create",
                "--title",
                "--head",
                "--body",
                "body",
                "--head",
                "employer:branch",
            ),
            default_repository="employer/private-project",
        )


def test_gh_validator_emits_repository_view_target_as_positional(
    profile: RepositoryScope,
) -> None:
    invocation = validate_gh_command(
        profile,
        ("repo", "view", "employer/private-project", "--json", "name"),
    )
    assert invocation.arguments == (
        "repo",
        "view",
        "--json",
        "name",
        "employer/private-project",
    )
