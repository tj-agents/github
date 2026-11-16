import ctypes
import io
import signal
import subprocess
import sys
import sysconfig
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

from gate_platform import MARKER, PrivateStateError

TOKEN_QUERY = 8
TOKEN_USER = 1
SDDL_REVISION = 1
DACL_SECURITY_INFORMATION = 4
PROTECTED_DACL_SECURITY_INFORMATION = 0x80000000
UNAVAILABLE = "Windows permissions are unavailable on this platform"


def default_bin_directory(environment: Mapping[str, str], home: Path) -> Path:
    local = environment.get("LOCALAPPDATA")
    if not local:
        raise PrivateStateError("LOCALAPPDATA is required to find tj's bin directory")
    return Path(local) / "Programs" / "tj"


def launcher_name() -> str:
    return "gh.exe"


def launcher_stub() -> bytes:
    """pip's console-script launcher, which runs the Python named by the shebang."""
    import ensurepip  # Debian-family Pythons can omit it; only Windows needs it.

    name = "t64-arm.exe" if sysconfig.get_platform() == "win-arm64" else "t64.exe"
    wheels = sorted((Path(ensurepip.__file__).parent / "_bundled").glob("pip-*.whl"))
    if not wheels:
        raise PrivateStateError("this Python has no bundled pip launcher for gh.exe")
    with zipfile.ZipFile(wheels[-1]) as wheel:
        return wheel.read(f"pip/_vendor/distlib/{name}")


def launcher_bytes(python: Path, script: Path) -> bytes:
    main = (
        f"# {MARKER}\n"
        "import runpy, sys\n"
        f"sys.path.insert(0, {str(script.parent)!r})\n"
        f"sys.argv = [{str(script)!r}, 'gh', *sys.argv[1:]]\n"
        f"runpy.run_path({str(script)!r}, run_name='__main__')\n"
    )
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as payload:
        payload.writestr(zipfile.ZipInfo("__main__.py", (1980, 1, 1, 0, 0, 0)), main)
    return launcher_stub() + f'#!"{python}" -B\n'.encode() + archive.getvalue()


def write_launcher(path: Path, content: bytes) -> None:
    path.write_bytes(content)


def approve_launcher_name() -> str:
    return "tj-approve.cmd"


def approve_launcher_bytes(python: Path, script: Path) -> bytes:
    lines = ("@echo off", f"rem {MARKER}", f'"{python}" -B "{script}" %*')
    return "".join(f"{line}\r\n" for line in lines).encode()


if sys.platform == "win32":
    from ctypes import wintypes

    def user_sid() -> str:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi.OpenProcessToken.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.HANDLE),
        )
        advapi.OpenProcessToken.restype = wintypes.BOOL
        advapi.GetTokenInformation.argtypes = (
            wintypes.HANDLE,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        advapi.GetTokenInformation.restype = wintypes.BOOL
        advapi.ConvertSidToStringSidW.argtypes = (
            ctypes.c_void_p,
            ctypes.POINTER(wintypes.LPWSTR),
        )
        advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel.LocalFree.restype = ctypes.c_void_p
        token = wintypes.HANDLE()
        if not advapi.OpenProcessToken(
            kernel.GetCurrentProcess(), TOKEN_QUERY, ctypes.byref(token)
        ):
            raise PrivateStateError("could not identify the Windows user")
        try:
            size = wintypes.DWORD()
            advapi.GetTokenInformation(token, TOKEN_USER, None, 0, ctypes.byref(size))
            buffer = ctypes.create_string_buffer(size.value)
            if not size.value or not advapi.GetTokenInformation(
                token, TOKEN_USER, buffer, size.value, ctypes.byref(size)
            ):
                raise PrivateStateError("could not identify the Windows user")
            sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p)).contents
            text = wintypes.LPWSTR()
            if not advapi.ConvertSidToStringSidW(sid, ctypes.byref(text)):
                raise PrivateStateError("could not identify the Windows user")
            try:
                return str(text.value)
            finally:
                kernel.LocalFree(text)
        finally:
            kernel.CloseHandle(token)

    def set_dacl(path: Path, sddl: str) -> None:
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
        convert.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        )
        convert.restype = wintypes.BOOL
        advapi.SetFileSecurityW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_void_p,
        )
        advapi.SetFileSecurityW.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel.LocalFree.restype = ctypes.c_void_p
        security = ctypes.c_void_p()
        if not convert(sddl, SDDL_REVISION, ctypes.byref(security), None):
            raise PrivateStateError(f"could not protect {path}")
        try:
            information = (
                DACL_SECURITY_INFORMATION | PROTECTED_DACL_SECURITY_INFORMATION
            )
            if not advapi.SetFileSecurityW(str(path), information, security):
                raise PrivateStateError(f"could not protect {path}")
        finally:
            kernel.LocalFree(security)

    def dacl_text(path: Path | None, sddl: str | None) -> str:
        """Windows' own rendering of a file's DACL, or of an SDDL string."""
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        advapi.GetFileSecurityW.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.c_void_p,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        )
        advapi.GetFileSecurityW.restype = wintypes.BOOL
        to_descriptor = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
        to_descriptor.argtypes = (
            wintypes.LPCWSTR,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.c_void_p),
            ctypes.POINTER(wintypes.DWORD),
        )
        to_descriptor.restype = wintypes.BOOL
        to_text = advapi.ConvertSecurityDescriptorToStringSecurityDescriptorW
        to_text.argtypes = (
            ctypes.c_void_p,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(wintypes.LPWSTR),
            ctypes.POINTER(wintypes.DWORD),
        )
        to_text.restype = wintypes.BOOL
        kernel.LocalFree.argtypes = (ctypes.c_void_p,)
        kernel.LocalFree.restype = ctypes.c_void_p
        rendered = wintypes.LPWSTR()
        if path is not None:
            needed = wintypes.DWORD()
            advapi.GetFileSecurityW(
                str(path), DACL_SECURITY_INFORMATION, None, 0, ctypes.byref(needed)
            )
            buffer = ctypes.create_string_buffer(needed.value)
            if not needed.value or not advapi.GetFileSecurityW(
                str(path),
                DACL_SECURITY_INFORMATION,
                buffer,
                needed.value,
                ctypes.byref(needed),
            ):
                raise PrivateStateError(f"could not inspect the permissions of {path}")
            converted = to_text(
                buffer,
                SDDL_REVISION,
                DACL_SECURITY_INFORMATION,
                ctypes.byref(rendered),
                None,
            )
        else:
            descriptor = ctypes.c_void_p()
            if not to_descriptor(sddl, SDDL_REVISION, ctypes.byref(descriptor), None):
                raise PrivateStateError("could not build the private permissions")
            try:
                converted = to_text(
                    descriptor,
                    SDDL_REVISION,
                    DACL_SECURITY_INFORMATION,
                    ctypes.byref(rendered),
                    None,
                )
            finally:
                kernel.LocalFree(descriptor)
        if not converted:
            raise PrivateStateError("could not render Windows permissions")
        try:
            return str(rendered.value)
        finally:
            kernel.LocalFree(rendered)

else:

    def user_sid() -> str:
        raise PrivateStateError(UNAVAILABLE)

    def set_dacl(path: Path, sddl: str) -> None:
        raise PrivateStateError(UNAVAILABLE)

    def dacl_text(path: Path | None, sddl: str | None) -> str:
        raise PrivateStateError(UNAVAILABLE)


def private_sddl(*, directory: bool) -> str:
    inherit = "OICI" if directory else ""
    return f"D:P(A;{inherit};FA;;;SY)(A;{inherit};FA;;;{user_sid()})"


def protect(path: Path, *, directory: bool) -> None:
    """Replace the DACL with full control for this user and SYSTEM only."""
    set_dacl(path, private_sddl(directory=directory))


def verify_private(path: Path, *, directory: bool) -> None:
    actual = dacl_text(path, None)
    expected = dacl_text(None, private_sddl(directory=directory))
    flags = actual.split("(", 1)[0].removeprefix("D:")
    if "P" not in flags or sorted(actual.split("(")[1:]) != sorted(
        expected.split("(")[1:]
    ):
        raise PrivateStateError(f"{path} is readable by other users; run onboarding")


def execute(
    executable: Path,
    arguments: Sequence[str],
    environment: Mapping[str, str] | None,
) -> int:
    """Run the real program and return its status; Ctrl+C reaches it, not us."""
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    try:
        return subprocess.run(
            [str(executable), *arguments],
            env=None if environment is None else dict(environment),
            check=False,
        ).returncode
    finally:
        signal.signal(signal.SIGINT, previous)
