import shutil
import subprocess
from pathlib import Path


class TerraformError(Exception):
    pass


def owner_root(declaration: Path) -> Path | None:
    root = declaration.resolve().parent
    return root if (root / "main.tf").is_file() else None


def _run(root: Path, *arguments: str) -> None:
    executable = shutil.which("terraform")
    if executable is None:
        raise TerraformError("terraform is not on PATH")
    result = subprocess.run(
        [executable, f"-chdir={root}", *arguments, "-input=false"], check=False
    )
    if result.returncode != 0:
        raise TerraformError(f"terraform {arguments[0]} failed in {root}")


def plan(root: Path) -> None:
    _run(root, "init")
    _run(root, "plan")
