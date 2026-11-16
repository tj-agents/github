import sys
from pathlib import Path

import pytest

sys.path.insert(
    0, str(Path(__file__).parents[1] / "github/utility/collaboration/scripts")
)
sys.path.insert(0, str(Path(__file__).parents[1] / "github/utility/access/scripts"))
sys.path.insert(
    0, str(Path(__file__).parents[1] / "github/utility/configuration/scripts")
)
sys.path.insert(0, str(Path(__file__).parents[1] / "github/runtime/gate"))
sys.path.insert(0, str(Path(__file__).parent))

from gate_app import Apps
from gate_sandbox import (
    CURRENT_PLATFORM,
    INHERITED,
    FakeGitHub,
    Sandbox,
    fake_sign,
)


@pytest.fixture
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Sandbox:
    result = Sandbox(tmp_path.resolve())
    for directory in (result.home, result.root / "local", result.neutral):
        directory.mkdir()
    for name in INHERITED:
        monkeypatch.delenv(name, raising=False)
    for index in range(64):
        monkeypatch.delenv(f"GIT_CONFIG_KEY_{index}", raising=False)
        monkeypatch.delenv(f"GIT_CONFIG_VALUE_{index}", raising=False)
    for name, value in result.environment().items():
        monkeypatch.setenv(name, value)
    monkeypatch.chdir(result.neutral)
    return result


@pytest.fixture
def github() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def apps(sandbox: Sandbox, github: FakeGitHub) -> Apps:
    return Apps(sandbox.gate_home, CURRENT_PLATFORM, github, fake_sign)
