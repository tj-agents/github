import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def payload(tmp_path: Path) -> Path:
    target = tmp_path / "github"
    shutil.copytree(ROOT / "plugins/github", target)
    return target


@pytest.mark.parametrize("kind", ["feature", "story", "task", "bug", "mistake", "debt"])
def test_published_creator_runs_without_the_producer_checkout(
    payload: Path, kind: str
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(payload / "skills/create-work-item/scripts/create_work_item.py"),
            "template",
            "--kind",
            kind,
        ],
        cwd=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["kind"] == kind


def test_published_inspection_has_no_mutation_intent(payload: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(payload / "skills/work-item/scripts/work_item.py"),
            "note",
            "--help",
        ],
        cwd=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 2
    assert "invalid choice: 'note'" in result.stderr


def test_published_problem_record_reader_runs_without_producer_checkout(
    payload: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(payload / "skills/work-item/scripts/read_problem_record.py"),
            "--help",
        ],
        cwd=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "--root" in result.stdout
    assert "--record" in result.stdout


def test_published_address_ships_progress_operation(payload: Path) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(payload / "skills/address-work-item/scripts/address_work_item.py"),
            "progress",
            "--help",
        ],
        cwd=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 0, result.stderr
    for argument in ("--root", "--item", "--status", "--text-file", "--request-id"):
        assert argument in result.stdout
