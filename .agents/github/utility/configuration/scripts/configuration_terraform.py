import re
import shutil
import subprocess
import sys
from pathlib import Path


class TerraformError(Exception):
    pass


def owner_root(declaration: Path) -> Path | None:
    root = declaration.resolve().parent
    return root if (root / "main.tf").is_file() else None


def _run(root: Path, *arguments: str) -> str:
    executable = shutil.which("terraform")
    if executable is None:
        raise TerraformError("terraform is not on PATH")
    process = subprocess.Popen(
        [executable, f"-chdir={root}", *arguments, "-input=false"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert process.stdout is not None
    output = []
    for line in process.stdout:
        sys.stdout.write(line)
        output.append(line)
    if process.wait() != 0:
        raise TerraformError(f"terraform {arguments[0]} failed in {root}")
    return "".join(output)


def plan(root: Path) -> str | None:
    _run(root, "init")
    found = re.search(r"/runs/(run-[A-Za-z0-9]+)", _run(root, "plan"))
    return found.group(1) if found else None
