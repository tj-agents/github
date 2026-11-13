import shutil
import subprocess
from pathlib import Path

import pytest

MODULE = Path(__file__).parents[1] / "github/utility/configuration/terraform"
TERRAFORM = shutil.which("terraform")


@pytest.mark.skipif(TERRAFORM is None, reason="terraform is not installed")
def test_configuration_module_terraform_tests_pass(tmp_path: Path) -> None:
    assert TERRAFORM is not None
    module = shutil.copytree(MODULE, tmp_path / "module")
    for arguments in (["init", "-backend=false", "-input=false"], ["test"]):
        result = subprocess.run(
            [TERRAFORM, f"-chdir={module}", *arguments, "-no-color"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=600,
        )
        assert result.returncode == 0, result.stdout + result.stderr
