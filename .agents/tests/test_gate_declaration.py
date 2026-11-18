import json

import pytest
from gate_declaration import (
    AppAccess,
    DeclarationError,
    HumanAccess,
    committed,
    declared_app,
    parse,
)
from gate_sandbox import Sandbox
from repository_configuration import ConfigurationError

APP = {"schema_version": 1, "access": {"method": "app", "app": "sample-bot"}}
MERGE = {"approval": "none", "merge": "agents", "default_branch_push": "none"}


@pytest.mark.parametrize(
    ("document", "access"),
    [
        ({"schema_version": 1, "access": {"method": "human"}}, HumanAccess()),
        (APP, AppAccess("sample-bot")),
        ({**APP, "merge": MERGE, "credentials": []}, AppAccess("sample-bot")),
        ({"schema_version": 1}, None),
        ({"schema_version": 1, "merge": MERGE, "credentials": []}, None),
    ],
)
def test_parse_reads_only_the_access_section(
    document: dict[str, object], access: HumanAccess | AppAccess | None
) -> None:
    assert parse(json.dumps(document)) == access


@pytest.mark.parametrize(
    ("access", "reason"),
    [
        ({"method": "token", "profile": "x"}, "human or app"),
        ({"method": "human", "app": "x"}, "exactly"),
        ({"method": "app"}, "exactly"),
        ({"method": "app", "app": "x", "y": 1}, "exactly"),
        ({"schema_version": 1, "method": "human"}, "exactly"),
        ({"method": "app", "app": "Sample-Bot"}, "slug"),
        ({"method": "app", "app": "-bot"}, "slug"),
        ({"method": "app", "app": 7}, "slug"),
        (None, "JSON object"),
        ("app", "JSON object"),
    ],
)
def test_parse_rejects_anything_but_an_exact_access_section(
    access: object, reason: str
) -> None:
    with pytest.raises(DeclarationError, match=reason):
        parse(json.dumps({"schema_version": 1, "access": access}))


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ('{"schema_version": 2, "access": {"method": "human"}}', "schema_version 1"),
        ('{"schema_version": true}', "schema_version 1"),
        ('{"access": {"method": "human"}}', "schema_version 1"),
        ('{"schema_version": 1, "method": "human"}', "unknown keys: method"),
        ('{"schema_version": 1, "schema_version": 1}', "repeats"),
        ("[1]", "JSON object"),
        ("{", "not valid JSON"),
    ],
)
def test_parse_rejects_anything_but_an_exact_configuration(
    text: str, reason: str
) -> None:
    with pytest.raises(ConfigurationError, match=reason):
        parse(text)


def test_committed_is_absent_outside_a_repository_and_before_a_commit(
    sandbox: Sandbox,
) -> None:
    assert committed(sandbox.neutral, sandbox.environment()) is None
    unborn = sandbox.root / "unborn"
    sandbox.git(sandbox.root, "init", "-q", str(unborn))
    assert committed(unborn, sandbox.environment()) is None


def test_committed_ignores_an_untracked_or_uncommitted_configuration(
    sandbox: Sandbox,
) -> None:
    repository = sandbox.repository("plain")
    configuration = repository / ".agents/repository.json"
    configuration.parent.mkdir()
    configuration.write_text(json.dumps(APP), encoding="utf-8")
    assert committed(repository, sandbox.environment()) is None
    sandbox.git(repository, "add", ".agents/repository.json")
    assert committed(repository, sandbox.environment()) is None


def test_committed_is_absent_without_an_access_section(sandbox: Sandbox) -> None:
    repository = sandbox.repository("merge-only", {"schema_version": 1, "merge": MERGE})
    assert committed(repository, sandbox.environment()) is None


def test_committed_reads_head_from_subdirectories_and_each_worktree(
    sandbox: Sandbox,
) -> None:
    repository = sandbox.repository("declared", APP)
    subdirectory = repository / "src" / "deep"
    subdirectory.mkdir(parents=True)
    assert declared_app(subdirectory, sandbox.environment()) == "sample-bot"
    sandbox.git(repository, "branch", "undeclared", "HEAD")
    sandbox.git(repository, "checkout", "-q", "--orphan", "bare-branch")
    sandbox.git(repository, "rm", "-q", "-r", "--cached", ".")
    sandbox.git(repository, "commit", "-q", "--allow-empty", "-m", "no configuration")
    sandbox.git(repository, "checkout", "-q", "-f", "main")
    linked = sandbox.root / "linked"
    sandbox.git(repository, "worktree", "add", "-q", str(linked), "bare-branch")
    assert committed(linked, sandbox.environment()) is None
    declared = sandbox.root / "declared-linked"
    sandbox.git(repository, "worktree", "add", "-q", str(declared), "undeclared")
    (declared / "nested").mkdir()
    assert committed(declared / "nested", sandbox.environment()) == AppAccess(
        "sample-bot"
    )


def test_committed_rejects_an_invalid_committed_access_section(
    sandbox: Sandbox,
) -> None:
    repository = sandbox.repository(
        "invalid",
        {"schema_version": 1, "access": {"method": "token", "profile": "employer"}},
    )
    with pytest.raises(DeclarationError, match="human or app"):
        committed(repository, sandbox.environment())
