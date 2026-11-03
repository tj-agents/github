import io
import os
import platform
import sys
import zipfile
from pathlib import Path

from access_core import AccessError


def real_gh() -> Path:
    candidate = (
        Path(os.environ.get("PROGRAMFILES", "C:/Program Files")) / "GitHub CLI/gh.exe"
    )
    if candidate.is_file():
        return candidate.resolve()
    raise AccessError("gh-unavailable: install the official GitHub CLI")


def launcher_path(launchers: Path) -> Path:
    return launchers / "gh.exe"


def _launcher_stub() -> bytes:
    import ensurepip

    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64", "x64"}:
        name = "t64.exe"
    elif machine in {"arm64", "aarch64"}:
        name = "t64-arm.exe"
    else:
        raise AccessError(
            "binding-invalid: unsupported Windows architecture for gh.exe"
        )
    wheels = sorted((Path(ensurepip.__file__).parent / "_bundled").glob("pip-*.whl"))
    if not wheels:
        raise AccessError("binding-invalid: this Python has no bundled launcher stub")
    with zipfile.ZipFile(wheels[-1]) as wheel:
        try:
            return wheel.read(f"pip/_vendor/distlib/{name}")
        except KeyError as error:
            raise AccessError(
                "binding-invalid: this Python lacks the required launcher stub"
            ) from error


def launcher_contents(script: Path, binding: Path) -> bytes:
    python = Path(sys.executable).resolve()
    if not python.is_file():
        raise AccessError(
            "binding-invalid: the active Python executable is unavailable"
        )
    arguments = [str(script), "gh", "--binding", str(binding)]
    main = "import runpy, sys\n"
    main += f"sys.path.insert(0, {str(script.parent)!r})\n"
    main += f"sys.argv = {arguments!r} + ['--'] + sys.argv[1:]\n"
    main += f"runpy.run_path({str(script)!r}, run_name='__main__')\n"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as payload:
        payload.writestr(zipfile.ZipInfo("__main__.py", (1980, 1, 1, 0, 0, 0)), main)
    return _launcher_stub() + f'#!"{python}" -B\n'.encode() + archive.getvalue()


def write_launcher(path: Path, contents: bytes) -> None:
    path.write_bytes(contents)
