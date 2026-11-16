"""Install, verify and refresh the gate's machine pieces.

Setup copies the runtime into the gate's state directory, writes the `gh` shim into
tj's bin directory and registers the Git credential helper in a marked block at the end
of the user's global Git configuration. It writes nothing to Claude or Codex settings.
"""

from __future__ import annotations

import itertools
import os
import re
import shlex
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import assert_never

from gate_app import Apps
from gate_declaration import declared_app
from gate_error import GateError
from gate_gh import gh_on_path, is_gate_launcher
from gate_git import git, machine_environment
from gate_platform import Platform

ENTRY = "github_gate.py"
GITHUB = "https://github.com"
BLOCK_BEGIN = "# >>> github@tj-agents access gate (written by setup; do not edit) >>>"
BLOCK_END = "# <<< github@tj-agents access gate <<<"
BLOCK_NOTE = (
    "# Agents in repositories that commit .agents/github-access.json reach\n"
    "# GitHub through the gate; everyone else falls through to the github.com\n"
    "# helpers below, copied from earlier configuration; setup --check reports\n"
    "# when they change.\n"
)


class InstallError(GateError):
    pass


class Applies(Enum):
    """Whether a `credential.<url>.helper` entry takes part in github.com requests."""

    NEVER = "never"
    ALWAYS = "always"
    SOMETIMES = "sometimes"


@dataclass(frozen=True, slots=True)
class ConfigEntry:
    origin: str
    key: str
    value: str | None


@dataclass(frozen=True, slots=True)
class Machine:
    """Where setup reads and writes on this machine."""

    source: Path
    home: Path
    bin_directory: Path
    python: Path
    platform: Platform
    environment: Mapping[str, str]
    neutral: Path
    git_home: Path

    @property
    def runtime(self) -> Path:
        return self.home / "runtime"

    @property
    def script(self) -> Path:
        return self.runtime / ENTRY

    @property
    def launcher(self) -> Path:
        return self.bin_directory / self.platform.launcher_name()

    @property
    def helper_command(self) -> str:
        python, script = self.python.as_posix(), self.script.as_posix()
        return f"!{shlex.quote(python)} -B {shlex.quote(script)} credential"

    @property
    def global_config(self) -> Path:
        configured = self.environment.get("GIT_CONFIG_GLOBAL")
        if configured is not None:
            if not configured or configured == os.devnull:
                raise InstallError(
                    "GIT_CONFIG_GLOBAL disables global Git configuration, which the "
                    "gate's credential helper needs"
                )
            return Path(configured).expanduser()
        home_config = self.git_home / ".gitconfig"
        xdg_home = self.environment.get("XDG_CONFIG_HOME")
        xdg = (Path(xdg_home) if xdg_home else self.git_home / ".config") / "git/config"
        if not home_config.exists() and xdg.exists():
            return xdg
        return home_config


def runtime_sources(source: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted(source.iterdir())
        if path.is_file() and path.suffix != ".pyc"
    }


def check_runtime(machine: Machine) -> list[str]:
    expected = runtime_sources(machine.source)
    problems = [
        f"{machine.runtime / name} is missing or stale"
        for name, content in expected.items()
        if not (machine.runtime / name).is_file()
        or (machine.runtime / name).read_bytes() != content
    ]
    if machine.runtime.is_dir():
        problems.extend(
            f"{path} is not part of the gate runtime"
            for path in sorted(machine.runtime.iterdir())
            if path.is_file() and path.name not in expected
        )
    return problems


def refresh_runtime(machine: Machine) -> list[str]:
    expected = runtime_sources(machine.source)
    machine.runtime.mkdir(parents=True, exist_ok=True)
    changes: list[str] = []
    for name, content in expected.items():
        target = machine.runtime / name
        if target.is_file() and target.read_bytes() == content:
            continue
        temporary = target.with_name(f".{name}.{os.getpid()}.next")
        try:
            temporary.write_bytes(content)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
        changes.append(f"refreshed {target}")
    for path in sorted(machine.runtime.iterdir()):
        if path.is_file() and path.name not in expected:
            path.unlink()
            changes.append(f"removed {path}")
    return changes


def check_launcher(machine: Machine) -> list[str]:
    launcher = machine.launcher
    expected = machine.platform.launcher_bytes(machine.python, machine.script)
    if not launcher.is_file() or launcher.read_bytes() != expected:
        return [f"{launcher} is missing or stale"]
    first = next(gh_on_path(machine.environment), None)
    if first is None or first.resolve() != launcher.resolve():
        return [
            f"gh on PATH resolves to {first} instead of the gate's {launcher}; put "
            f"{machine.bin_directory} before other gh locations on PATH"
        ]
    return []


def install_launcher(machine: Machine) -> list[str]:
    launcher = machine.launcher
    if launcher.exists() and not is_gate_launcher(launcher):
        raise InstallError(f"{launcher} is not the gate's gh; refusing to replace it")
    expected = machine.platform.launcher_bytes(machine.python, machine.script)
    if launcher.is_file() and launcher.read_bytes() == expected:
        return []
    machine.bin_directory.mkdir(parents=True, exist_ok=True)
    machine.platform.write_launcher(launcher, expected)
    return [f"installed {launcher}"]


def parse_entries(output: bytes, *, with_origin: bool) -> list[ConfigEntry]:
    tokens = output.split(b"\0")
    if tokens[-1:] == [b""]:
        tokens.pop()
    size = 2 if with_origin else 1
    if len(tokens) % size:
        raise InstallError("Git returned unreadable configuration")
    entries: list[ConfigEntry] = []
    for record in itertools.batched(tokens, size):
        key, newline, value = record[-1].decode("utf-8").partition("\n")
        origin = record[0].decode("utf-8") if with_origin else ""
        entries.append(ConfigEntry(origin, key, value if newline else None))
    return entries


def read_config(
    machine: Machine, arguments: Sequence[str], stdin: bytes | None = None
) -> list[ConfigEntry]:
    """Configuration Git reads outside any repository, without command overrides."""
    environment = {
        **machine_environment(machine.environment),
        "GIT_CEILING_DIRECTORIES": str(machine.neutral.parent),
    }
    result = git(["config", *arguments, "--null"], machine.neutral, environment, stdin)
    if result.returncode and (result.stdout or result.stderr):
        raise InstallError("Git could not read its configuration")
    return parse_entries(result.stdout, with_origin="--show-origin" in arguments)


def helper_subsection(key: str) -> str | None:
    lowered = key.lower()
    if lowered == "credential.helper":
        return ""
    if lowered.startswith("credential.") and lowered.endswith(".helper"):
        return key[len("credential.") : -len(".helper")]
    return None


def host_matches(pattern: str, host: str) -> bool:
    expression = "[^.]*".join(re.escape(part) for part in pattern.split("*"))
    return re.fullmatch(expression, host) is not None


def applies(subsection: str) -> Applies:
    """How Git applies `credential.<subsection>.helper` to https://github.com."""
    if not subsection:
        return Applies.ALWAYS
    scheme, separator, rest = subsection.partition("://")
    if not separator:
        scheme, rest = "", subsection
    if scheme and scheme.lower() != "https":
        return Applies.NEVER
    authority, _, path = rest.partition("/")
    user, at, address = authority.rpartition("@")
    host, _, port = address.partition(":")
    if port not in {"", "443"} or not host_matches(host.lower(), "github.com"):
        return Applies.NEVER
    if at or user or path.strip("/"):
        return Applies.SOMETIMES
    return Applies.ALWAYS


def human_helpers(entries: Sequence[ConfigEntry]) -> list[str]:
    """The helpers Git runs for https://github.com, applying resets in order."""
    helpers: list[str] = []
    for entry in entries:
        subsection = helper_subsection(entry.key)
        if subsection is None:
            continue
        match applies(subsection):
            case Applies.NEVER:
                continue
            case Applies.SOMETIMES:
                raise InstallError(
                    f"{entry.origin}: credential.{subsection}.helper applies to "
                    "only some github.com requests, which the gate cannot preserve; "
                    f"scope it to {GITHUB} or remove it"
                )
            case Applies.ALWAYS:
                if entry.value is None:
                    raise InstallError(f"{entry.origin}: {entry.key} has no value")
                if entry.value:
                    helpers.append(entry.value)
                else:
                    helpers.clear()
            case _ as unreachable:
                assert_never(unreachable)
    return helpers


def include_target(entry: ConfigEntry, git_home: Path) -> Path | None:
    if not entry.value:
        return None
    if entry.value.startswith("~/"):
        return git_home / entry.value[2:]
    target = Path(entry.value)
    if target.is_absolute():
        return target
    origin = entry.origin.removeprefix("file:")
    if origin == entry.origin:
        return None
    return Path(origin).parent / target


def conditional_helpers(machine: Machine, entries: Sequence[ConfigEntry]) -> list[str]:
    """Conditional includes that add github.com helpers the gate would reorder."""
    problems: list[str] = []
    for entry in entries:
        lowered = entry.key.lower()
        if not (lowered.startswith("includeif.") and lowered.endswith(".path")):
            continue
        target = include_target(entry, machine.git_home)
        if target is None or not target.is_file():
            continue
        included = read_config(machine, ["--file", str(target), "--includes", "--list"])
        if any(
            (subsection := helper_subsection(item.key)) is not None
            and applies(subsection) is not Applies.NEVER
            for item in included
        ):
            problems.append(
                f"{target} is included conditionally and sets github.com credential "
                "helpers, which the gate cannot preserve"
            )
    return problems


def quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_block(helper_command: str, helpers: Sequence[str]) -> str:
    lines = [
        BLOCK_BEGIN,
        BLOCK_NOTE.rstrip("\n"),
        f'[credential "{GITHUB}"]',
        "\thelper =",
        f"\thelper = {quoted(helper_command)}",
        *(f"\thelper = {quoted(helper)}" for helper in helpers),
        BLOCK_END,
    ]
    return "\n".join(lines) + "\n"


@dataclass(frozen=True, slots=True)
class ConfigText:
    """A global Git configuration file split around the gate's block."""

    before: str
    block: str | None
    after: str

    @classmethod
    def split(cls, content: str, path: Path) -> ConfigText:
        begins, ends = content.count(BLOCK_BEGIN), content.count(BLOCK_END)
        if begins == ends == 0:
            return cls(content, None, "")
        start, stop = content.find(BLOCK_BEGIN), content.find(BLOCK_END)
        if begins != 1 or ends != 1 or stop < start:
            raise InstallError(
                f"{path} has damaged gate markers; remove the gate's lines and rerun "
                "setup"
            )
        stop += len(BLOCK_END)
        after = content[stop:].removeprefix("\r\n").removeprefix("\n")
        block = content[start:stop].replace("\r\n", "\n") + "\n"
        return cls(content[:start], block, after)

    def without_block(self) -> str:
        return self.before + self.after


def read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as error:
        raise InstallError(f"{path} is not UTF-8") from error


def without_block(
    entries: Sequence[ConfigEntry], block: Sequence[ConfigEntry]
) -> list[ConfigEntry]:
    wanted = [(entry.key.lower(), entry.value) for entry in block]
    size = len(wanted)
    for start in range(len(entries) - size, -1, -1):
        window = entries[start : start + size]
        if [(entry.key.lower(), entry.value) for entry in window] == wanted:
            return [*entries[:start], *entries[start + size :]]
    raise InstallError("Git does not read the gate's block; rerun setup")


def expected_block(machine: Machine, current: str | None) -> str:
    entries = read_config(machine, ["--list", "--show-origin"])
    if current is not None:
        block = read_config(machine, ["--file", "-", "--list"], current.encode())
        entries = without_block(entries, block)
    problems = conditional_helpers(machine, entries)
    if problems:
        raise InstallError("; ".join(problems))
    return render_block(machine.helper_command, human_helpers(entries))


def check_git_config(machine: Machine) -> list[str]:
    path = machine.global_config
    text = ConfigText.split(read_text(path), path)
    if text.block is None:
        return [f"{path} does not register the gate's Git credential helper"]
    problems: list[str] = []
    if text.after.strip():
        problems.append(
            f"{path} has configuration after the gate's block, which can bypass it"
        )
    try:
        expected = expected_block(machine, text.block)
    except InstallError as error:
        return [*problems, str(error)]
    if text.block != expected:
        problems.append(
            f"{path}: the gate's block is stale; the github.com helpers it re-adds or "
            "the gate command changed"
        )
    return problems


def install_git_config(machine: Machine) -> list[str]:
    path = machine.global_config
    content = read_text(path)
    text = ConfigText.split(content, path)
    expected = expected_block(machine, text.block)
    rest = text.without_block().rstrip()
    updated = (rest + "\n\n" if rest else "") + expected
    if updated == content:
        return []
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(updated.encode("utf-8"))
    return [f"registered the gate's Git credential helper in {path}"]


def check_repository(
    cwd: Path, environment: Mapping[str, str], apps: Apps
) -> list[str]:
    """For a repository declaring an App, whether this machine has enrolled it."""
    try:
        slug = declared_app(cwd, environment)
    except GateError as error:
        return [str(error)]
    if slug is None:
        return []
    try:
        apps(slug).verify_enrolled()
    except GateError as error:
        return [str(error)]
    return []


def check(machine: Machine, cwd: Path, apps: Apps) -> list[str]:
    return [
        *check_runtime(machine),
        *check_launcher(machine),
        *check_git_config(machine),
        *check_repository(cwd, machine.environment, apps),
    ]


def setup(machine: Machine) -> list[str]:
    return [
        *refresh_runtime(machine),
        *install_launcher(machine),
        *install_git_config(machine),
    ]


def converge(machine: Machine) -> list[str]:
    """Refresh an installed runtime's copies after a plugin update."""
    return refresh_runtime(machine) if machine.script.is_file() else []
