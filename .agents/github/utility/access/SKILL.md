---
name: access
description: Securely onboard, renew and check GitHub credentials for package-only GHCR pulls or organization-scoped employer Git/gh access. Use when private container pulls need authentication, a package token expires, or employer GitHub access needs setup or diagnosis.
kind: utility
domain: github
profile: core
applicability: GitHub container package access or explicitly selected employer GitHub access
requires: python, secure credential backend; Docker credential helper for packages; git and Git Credential Manager for employer access
provenance: house
---

# GitHub access

Use the shipped Python 3.12+ `scripts/github_access.py` for onboarding, renewal and checks. Select
`packages` for private GHCR image pulls; select the employer commands below for a bound Git/gh/MCP
checkout. Derive the expected login and immutable image from the authorized task or project declaration.
Package access does not select an employer profile or require repository permissions.

## Container package access

```text
python -B <skill-directory>/scripts/github_access.py packages check --login <github-login> --image ghcr.io/<owner>/<image>@sha256:<digest>
python -B <skill-directory>/scripts/github_access.py packages onboard --login <github-login> --image ghcr.io/<owner>/<image>@sha256:<digest>
```

Run `packages check` first. For a missing or expired credential, run `packages onboard`, which also
renews an existing credential. The script opens the GitHub classic token form with only `read:packages`
selected and a nonce-protected loopback page. The human chooses the expiry, creates the token and pastes
it into that page. Use its structured `local_url` result if the browser does not open. Keep that process
alive until submission and verification finish. Never reconstruct this flow with an ad hoc receiver or
request tokens in chat, arguments, environment variables, clipboard automation or files.

The script verifies the expected GitHub identity, exactly `read:packages` scope and authenticated access
to the supplied manifest, including its digest, before saving. A rejected candidate leaves the saved
credential unchanged. Saving requires exact secure-helper readback; a failed save restores the previous
GHCR entry, or removes the new entry when none existed. A rollback failure reports uncertain credential
state and never claims readiness. It uses the existing Docker configuration (`DOCKER_CONFIG` or `~/.docker`) and its
configured OS-backed credential helper: Desktop/wincred on Windows, Desktop/Secret Service/pass on Linux,
or Desktop/osxkeychain on macOS. An absent or unavailable secure helper fails closed; configure that
backend, then rerun. Another account's existing GHCR entry is a conflict. Other registries, employer
credentials and account-wide gh authentication are untouched. Never fall back to Docker's base64 `auth`
storage or request `repo`/`workflow` scopes for this flow.

Successful JSON output establishes identity, scope, secure storage and that immutable manifest's access.
It does not prove every image or layer download; rerun the original authorized pull/build afterward.
An access denial can require the package owner's grant or SSO authorization, not a broader token.

## Employer GitHub access

Use the shipped Python 3.12+ entry point with the employer-owned profile. The profile declares the
organization, expected GitHub login, verification repository, repository allowlist and permissions.
Keep employer data in its owning private plugin. Explicit selection and current task authority are
required; finding an employer's name on disk does not select it.

```text
python -B <skill-directory>/scripts/github_access.py check --profile <employer-profile.json>
python -B <skill-directory>/scripts/github_access.py setup --profile <employer-profile.json> --root <checkout>
python -B <skill-directory>/scripts/github_access.py activate --binding <checkout>/.agents/github-access.json --host codex
python -B <skill-directory>/scripts/github_access.py activate --binding <checkout>/.agents/github-access.json --host claude --host-executable <absolute-native-claude>
python -B <skill-directory>/scripts/github_access.py onboard --binding <checkout>/.agents/github-access.json
python -B <skill-directory>/scripts/github_access.py onboard --binding <checkout>/.agents/github-access.json --token-kind classic
python -B <skill-directory>/scripts/github_access.py rotate --binding <checkout>/.agents/github-access.json
python -B <skill-directory>/scripts/github_access.py check --binding <checkout>/.agents/github-access.json
python -B <skill-directory>/scripts/github_access.py mcp --binding <checkout>/.agents/github-access.json
python -B <skill-directory>/scripts/github_access.py gh --binding <checkout>/.agents/github-access.json -- pr list
python -B <skill-directory>/scripts/github_access.py git --binding <checkout>/.agents/github-access.json -- fetch origin
python -B <skill-directory>/scripts/github_access.py native-git --binding <checkout>/.agents/github-access.json -- status
```

The employer profile selects `token_kind`: `fine-grained` by default or `classic` when the user explicitly
authorizes that broader credential. `onboard` and `rotate` accept `--token-kind` to select either form.
Never switch credential types automatically after a refusal. Classic tokens can reach other repositories
the account can access; organization checks in this utility constrain routing, not the token itself.

Fine-grained onboarding opens GitHub's form and a temporary local submission page. The human selects
the displayed resource owner and repository access, obtains any required organization approval, then
pastes into the local page. Repository selection cannot be prefilled. A profile with `repositories: ["*"]`
permits all repositories in its one organization; otherwise select only the listed repositories.

Classic onboarding requests `repo`, plus `workflow` when the profile declares workflow writes. The human
creates the token, selects its expiry, then uses Configure SSO to authorize the employer organization
before submitting locally. An organization can disallow classic tokens; a denied classic token does not
justify changing organization policy or adding administration scopes. No fine-grained administrator
approval is requested by the classic flow. Never request a token in chat, arguments, an environment dump,
clipboard automation or a file. Preserve the active version when candidate verification fails.

The command uses GCM with Windows Credential Manager, or an explicitly configured Secret Service/GPG
backend on Linux. An unavailable secure backend fails closed. It never configures a host-wide gh login
or writes a plaintext credential. Public state holds only a credential version identifier.

Network Git uses an isolated HOME. If effective repository configuration changes under that isolation,
including HOME-dependent `includeIf` conditions, the command fails closed. Use absolute or
HOME-independent include conditions for employer Git configuration.

Setup records the exact Git root, installed Git executable, employer declaration and immutable runtime in a public binding.
The installed `git` launcher supports local work in the selected worktree and restricts network commands to
allowlisted HTTPS repositories. Each Git/gh invocation rechecks that selection and the employer origin before
reading the credential. Leaving the selected checkout or changing its origin disables the binding. Git helpers only answer allowlisted
HTTPS repository requests; Git's store/erase callbacks cannot replace or delete the active version.

The `mcp` command is a stdio gateway for `get_file_contents`, `list_branches` and
`pull_request_read`. It serves initialization and tool discovery without reading the credential, then
validates each tool's owner and repository against the binding before starting the pinned read-only
GitHub MCP server. `setup` installs the verified upstream archive and binary under the selected state
root; `check --binding` verifies that cache. It never exposes upstream resources, prompts, completion
or unrestricted tool methods.

The installed `gh` executable is a scoped launcher. Its supported commands use a closed argument grammar;
unknown commands, alternate hosts, cross-repository targets, browser/editor launches and arbitrary GraphQL
are rejected. REST `api` supports repository reads. Use the owning repository's delivery procedures for
writes and merges. Unsupported operations require a reviewed capability change, never a token export or
an account-wide login. The token exists only in the authorized child environment and secure vault.

Run setup, activate each selected native host, then onboard from the exact selected checkout. These
steps are part of the authorized access task. Resolve the actual native Claude executable through
Agent's existing host resolver and pass its absolute path; shell functions are not executables.
Activation merges public environment into the checkout's untracked settings file, refusing a tracked
one, and registers the MCP command. Claude's
native CLI owns its local MCP registration; an unrelated `employer_github` registration is a conflict.
Custom `CLAUDE_CONFIG_DIR` is currently unsupported and fails before mutation.

Preserve Codex project trust and existing shell policy; a policy that removes required public variables
must be reconciled by its owner. Start a fresh host through Agent's existing launch path. For Claude,
pass the activation result's `required_host_arguments` (`--settings` and the absolute project settings
path), including on POSIX linked worktrees. Do not write another checkout's settings or add a launcher.
Use `activate --host <host> --check --binding <binding>` to check configuration without registration
writes. This verifies saved configuration, not a running host.

Bound onboarding, rotation and check perform actual Git, gh and MCP repository reads; rotation restores
the previous active version if any read fails. After publication/install, verify those reads in each
fresh selected native host and verify that leaving the bound checkout denies employer access. Human
token creation/submission, organization approval and repository review approval remain human actions.
Do not claim full readiness until installed routing and fresh-host scope checks pass.

`check` verifies identity, repository access and declared read capabilities. A successful read does not
prove write permission or the token's resource-owner selection; the human must confirm the token form,
and actual authorized operations establish their own permission evidence. A 401 proves invalid
authentication, not its precise expiry cause. SSO headers, policy/approval ambiguity, rate limiting and
network failure receive distinct actions. Never relay raw HTTP, GCM or child-process diagnostics that
could contain a credential. Tool routing and native installation must also pass before claiming full
Git/gh/MCP readiness.
