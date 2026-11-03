from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from access_core import AccessError
from github_claude_registration import RegistrationState, registration_state


def expected() -> dict[str, object]:
    return {
        "type": "stdio",
        "command": "C:/Python/python.exe",
        "args": ["-B", "C:/runtime/github_access.py", "mcp", "--binding", "C:/binding"],
        "env": {"GITHUB_ACCESS_BINDING": "C:/binding"},
    }


def registration(**extra: object) -> dict[str, object]:
    value = expected()
    value.update(extra)
    return value


def document(root: Path, entry: object | None = None) -> dict[str, object]:
    project: dict[str, object] = {}
    if entry is not None:
        project["mcpServers"] = {"employer_github": entry}
    return {"projects": {str(root.resolve()).replace("\\", "/"): project}}


def write_profile(path: Path, value: object) -> None:
    path.write_text(json.dumps(value), encoding="utf-8")


def test_registration_states(tmp_path: Path) -> None:
    profile = tmp_path / "claude.json"
    root = tmp_path / "worktree"
    root.mkdir()
    assert registration_state(profile, root, expected()) is RegistrationState.ABSENT
    write_profile(profile, document(root))
    assert registration_state(profile, root, expected()) is RegistrationState.ABSENT
    write_profile(profile, document(root, registration()))
    assert registration_state(profile, root, expected()) is RegistrationState.CURRENT
    previous = registration(command="C:/Python/old-python.exe")
    write_profile(profile, document(root, previous))
    assert (
        registration_state(profile, root, expected(), previous)
        is RegistrationState.PREVIOUS_OWNED
    )
    write_profile(profile, document(root, registration(command="C:/other.exe")))
    assert registration_state(profile, root, expected()) is RegistrationState.CONFLICT


def test_unknown_credential_values_are_skipped_without_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "claude.json"
    root = tmp_path / "worktree"
    root.mkdir()
    secret = "credential-" + "x" * 200_000
    value = document(root, registration(headers={"Authorization": secret}))
    value["credentials"] = {"token": secret}
    write_profile(profile, value)
    decoded: list[str] = []
    loads = json.loads

    def capture(value: str | bytes, *args: Any, **kwargs: Any) -> Any:
        result = loads(value, *args, **kwargs)
        if isinstance(result, str):
            decoded.append(result)
        return result

    monkeypatch.setattr("github_claude_registration.json.loads", capture)
    assert registration_state(profile, root, expected()) is RegistrationState.CONFLICT
    assert secret not in decoded


def test_unknown_environment_token_is_skipped_without_decoding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    profile = tmp_path / "claude.json"
    root = tmp_path / "worktree"
    root.mkdir()
    secret = "token-" + "y" * 200_000
    value = registration(
        env={"GITHUB_ACCESS_BINDING": "C:/binding", "GH_TOKEN": secret}
    )
    write_profile(profile, document(root, value))
    decoded: list[str] = []
    loads = json.loads

    def capture(value: str | bytes, *args: Any, **kwargs: Any) -> Any:
        result = loads(value, *args, **kwargs)
        if isinstance(result, str):
            decoded.append(result)
        return result

    monkeypatch.setattr("github_claude_registration.json.loads", capture)
    assert registration_state(profile, root, expected()) is RegistrationState.CONFLICT
    assert secret not in decoded


@pytest.mark.parametrize("indent", [None, 2])
def test_unrelated_objects_accept_whitespace_between_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, indent: int | None
) -> None:
    profile = tmp_path / "claude.json"
    root = tmp_path / "worktree"
    root.mkdir()
    value = document(root, registration())
    value["unrelated"] = {"first": "ignored-one", "second": {"a": 1, "b": 2}}
    profile.write_text(json.dumps(value, indent=indent), encoding="utf-8")
    loads = json.loads

    def reject_unrelated(value: str | bytes, *args: Any, **kwargs: Any) -> Any:
        result = loads(value, *args, **kwargs)
        assert result != "ignored-one"
        return result

    monkeypatch.setattr("github_claude_registration.json.loads", reject_unrelated)
    assert registration_state(profile, root, expected()) is RegistrationState.CURRENT


def test_normalized_project_aliases_are_rejected(tmp_path: Path) -> None:
    profile = tmp_path / "claude.json"
    root = tmp_path / "worktree"
    root.mkdir()
    first = json.dumps(str(root.resolve()))
    second = json.dumps(str(root.resolve() / "."))
    profile.write_text(
        '{"projects":{' + first + ":{}," + second + ":{}}}", encoding="utf-8"
    )
    with pytest.raises(AccessError, match="claude-registration-invalid"):
        registration_state(profile, root, expected())


def test_duplicate_relevant_fields_are_rejected(tmp_path: Path) -> None:
    profile = tmp_path / "claude.json"
    root = tmp_path / "worktree"
    root.mkdir()
    project = json.dumps(str(root.resolve()))
    profile.write_text(
        '{"projects":{' + project + ':{"mcpServers":{},"mcpServers":{}}}}',
        encoding="utf-8",
    )
    with pytest.raises(AccessError, match="claude-registration-invalid"):
        registration_state(profile, root, expected())


@pytest.mark.parametrize(
    "payload",
    [
        b'{"projects":',
        b'{"unknown":' + b"[" * 130 + b"0" + b"]" * 130 + b"}",
        b" " * (16 * 1024 * 1024 + 1),
    ],
    ids=["malformed", "too-deep", "oversized"],
)
def test_malformed_depth_and_size_fail_closed(tmp_path: Path, payload: bytes) -> None:
    profile = tmp_path / "claude.json"
    root = tmp_path / "worktree"
    root.mkdir()
    profile.write_bytes(payload)
    with pytest.raises(AccessError, match="claude-registration-invalid"):
        registration_state(profile, root, expected())


def test_utf8_escaped_project_path_matches(tmp_path: Path) -> None:
    profile = tmp_path / "claude.json"
    root = tmp_path / "worktree"
    root.mkdir()
    project = str(root.resolve()).replace("\\", "/")
    escaped = "".join(f"\\u{ord(character):04x}" for character in project)
    entry = json.dumps(registration())
    payload = '{"projects":{"' + escaped + '":{"mcpServers":{"employer_github":'
    profile.write_text(
        payload + entry + "}}}}",
        encoding="utf-8",
    )
    assert registration_state(profile, root, expected()) is RegistrationState.CURRENT


def test_symlinked_profile_path_fails_closed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    target.mkdir()
    root = tmp_path / "worktree"
    root.mkdir()
    write_profile(target / "claude.json", document(root, registration()))
    profile = tmp_path / "linked" / "claude.json"
    try:
        profile.parent.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks are unavailable")
    with pytest.raises(AccessError, match="claude-registration-invalid"):
        registration_state(profile, root, expected())
