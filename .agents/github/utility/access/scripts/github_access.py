import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import access_core
import github_binding
import github_platform
import github_tools


def state_root() -> Path:
    return Path(
        os.environ.get("AGENT_STATE_DIRECTORY", str(Path.home() / ".agents-state"))
    )


def git_read(root: Path, arguments: Sequence[str]) -> str:
    environment = access_core.private_environment(os.environ)
    environment.update(
        GIT_CONFIG_GLOBAL=os.devnull,
        GIT_CONFIG_SYSTEM=os.devnull,
        GIT_CONFIG_NOSYSTEM="1",
        GIT_CONFIG_COUNT="0",
        GIT_TERMINAL_PROMPT="0",
    )
    try:
        result = subprocess.run(
            ["git", *arguments],
            cwd=root,
            env=environment,
            text=True,
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise access_core.AccessError(
            "git-scope-invalid: Git inspection failed"
        ) from error
    if result.returncode:
        raise access_core.AccessError(
            "git-scope-invalid: inspect the selected repository"
        )
    return result.stdout.strip()


def remotes(root: Path) -> dict[str, str]:
    result = {}
    for name in git_read(root, ["remote"]).splitlines():
        urls = git_read(root, ["remote", "get-url", "--all", name]).splitlines()
        push_urls = git_read(
            root, ["remote", "get-url", "--push", "--all", name]
        ).splitlines()
        if len(urls) != 1 or push_urls != urls:
            raise access_core.AccessError(
                "git-scope-invalid: each remote needs one matching fetch/push URL"
            )
        result[name] = urls[0]
    return result


def helper_command(binding: github_binding.Binding) -> str:
    return "!" + shlex.join(
        [
            sys.executable,
            "-B",
            str(Path(__file__).resolve()),
            "credential",
            "--binding",
            str(binding.path),
        ]
    )


def run_git(
    binding: github_binding.Binding,
    profile: access_core.Profile,
    arguments: Sequence[str],
) -> int:
    executable = shutil.which("git")
    if executable is None:
        raise access_core.AccessError("git-unavailable: install Git")
    result = github_tools.run_git(
        profile,
        Path(executable).resolve(),
        arguments,
        remotes=remotes(Path.cwd()),
        helper_command=helper_command(binding),
        cwd=Path.cwd(),
    )
    sys.stdout.write(result.stdout)
    sys.stderr.write(result.stderr)
    return result.returncode


def verify_tools(
    binding: github_binding.Binding,
    profile: access_core.Profile,
    state: access_core.State,
    vault: access_core.Vault,
    platform: github_platform.PlatformProtocol,
) -> None:
    profile = binding.validate(Path.cwd(), platform)
    repository = f"{profile.organization}/{profile.verify_repository}"
    result = github_tools.run_gh(
        profile,
        state,
        vault,
        platform.real_gh(),
        ["api", f"repos/{repository}", "--jq", ".full_name"],
        platform=platform,
        cwd=binding.root,
        default_repository=repository,
    )
    if result.returncode or result.stdout.strip().casefold() != repository.casefold():
        raise access_core.AccessError(
            "gh-check-failed: employer repository read failed"
        )
    profile = binding.validate(Path.cwd(), platform)
    if run_git(
        binding, profile, ["ls-remote", f"https://github.com/{repository}.git", "HEAD"]
    ):
        raise access_core.AccessError(
            "git-check-failed: employer repository read failed"
        )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Secure organization-scoped GitHub access"
    )
    operations = result.add_subparsers(dest="operation", required=True)
    for operation in ("onboard", "rotate", "check"):
        command = operations.add_parser(operation)
        command.add_argument("--profile", type=Path)
        command.add_argument("--binding", type=Path)
    setup = operations.add_parser("setup")
    setup.add_argument("--profile", type=Path)
    setup.add_argument("--root", type=Path, default=Path.cwd())
    setup.add_argument("--check", action="store_true")
    for operation in ("gh", "git", "credential"):
        command = operations.add_parser(operation)
        command.add_argument("--binding", type=Path)
        command.add_argument("arguments", nargs=argparse.REMAINDER)
    return result


def selected_binding(
    path: Path | None, platform: github_platform.PlatformProtocol
) -> github_binding.Binding:
    if path is None:
        inherited = os.environ.get("GITHUB_ACCESS_BINDING")
        path = (
            Path(inherited) if inherited else Path.cwd() / ".agents/github-access.json"
        )
    binding = github_binding.Binding.load(path, platform)
    binding.validate(Path.cwd(), platform)
    return binding


def dispatch(
    arguments: argparse.Namespace, platform: github_platform.PlatformProtocol
) -> int:
    binding: github_binding.Binding | None
    if arguments.operation == "setup":
        if arguments.check:
            binding = github_binding.Binding.load(
                arguments.root / ".agents/github-access.json", platform
            )
            binding.check(arguments.root, Path(__file__).resolve().parent, platform)
        else:
            if arguments.profile is None:
                raise access_core.AccessError(
                    "profile-required: select the employer declaration"
                )
            binding = github_binding.bind(
                arguments.profile,
                arguments.root,
                state_root(),
                Path(__file__).resolve().parent,
                platform,
            )
        print(
            json.dumps(
                {
                    "status": "scope-bound",
                    "binding": str(binding.path),
                    "native_hosts": "not-yet-configured",
                }
            )
        )
        return 0
    if arguments.operation in {"gh", "git", "credential"}:
        binding = selected_binding(arguments.binding, platform)
        profile = binding.validate(Path.cwd(), platform)
        state = access_core.State(binding.state_root, profile)
        vault = access_core.GCMVault(profile, os.environ)
        command = arguments.arguments
        if command[:1] == ["--"]:
            command = command[1:]
        if arguments.operation == "credential":
            if len(command) != 1:
                raise access_core.AccessError(
                    "credential-invalid: requires one Git operation"
                )
            response = github_tools.git_credential_response(
                profile, state, vault, command[0], sys.stdin.read(4097)
            )
            sys.stdout.write(response)
            return 0
        if arguments.operation == "git":
            return run_git(binding, profile, command)
        result = github_tools.run_gh(
            profile,
            state,
            vault,
            platform.real_gh(),
            command,
            platform=platform,
            cwd=Path.cwd(),
            default_repository=git_read(Path.cwd(), ["remote", "get-url", "origin"]),
        )
        sys.stdout.write(result.stdout)
        sys.stderr.write(result.stderr)
        return result.returncode
    binding = (
        selected_binding(arguments.binding, platform)
        if arguments.binding is not None
        else None
    )
    if arguments.profile is not None and binding is not None:
        raise access_core.AccessError("scope-ambiguous: use one profile or binding")
    if binding is not None:
        profile = binding.validate(Path.cwd(), platform)
    elif arguments.profile is not None:
        profile = access_core.Profile.load(arguments.profile)
    else:
        raise access_core.AccessError(
            "profile-required: select the employer declaration"
        )
    state = access_core.State(
        binding.state_root if binding is not None else state_root(), profile
    )
    vault = access_core.GCMVault(profile, os.environ)
    if arguments.operation == "check":
        version = state.active()
        if version is None:
            raise access_core.AccessError("credential-missing: run employer onboarding")
        evidence = access_core.verify(
            profile, access_core.GitHubAPI(vault.read(version))
        )
        if binding is not None:
            verify_tools(binding, profile, state, vault, platform)
    else:
        vault.check()
        token = access_core.receive_token(profile)
        if binding is not None:
            profile = binding.validate(Path.cwd(), platform)
        evidence = access_core.rotate(
            profile,
            state,
            vault,
            token,
            after_select=(
                lambda: verify_tools(binding, profile, state, vault, platform)
            )
            if binding is not None
            else None,
        )
    print(
        json.dumps(
            {
                "status": "credential-verified",
                "organization": profile.organization,
                "evidence": evidence,
                "tool_readiness": "git-gh-verified-mcp-pending"
                if binding is not None
                else "not-yet-verified",
            }
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    if sys.version_info < (3, 12):
        print("Python 3.12 or newer is required", file=sys.stderr)
        return 2
    arguments = parser().parse_args(argv)
    try:
        return dispatch(arguments, github_platform.current_platform())
    except (access_core.AccessError, OSError, subprocess.SubprocessError) as error:
        message = (
            str(error)
            if isinstance(error, access_core.AccessError)
            else ("local-operation-failed: check installed tools and public state")
        )
        print(message, file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
