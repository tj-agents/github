---
name: access
description: Bind an employer checkout to organization-scoped Git/gh access, securely onboard or rotate its fine-grained credential, and check authentication. Use when configuring or diagnosing employer GitHub access.
kind: utility
domain: github
profile: core
applicability: explicitly selected employer GitHub access
requires: python, git, Git Credential Manager, secure credential backend
provenance: house
---

# Employer GitHub access

Use the shipped Python 3.12+ entry point with the employer-owned profile. The profile declares the
organization, expected GitHub login, verification repository, repository allowlist and permissions.
Keep employer data in its owning private plugin. Explicit selection and current task authority are
required; finding an employer's name on disk does not select it.

```text
python -B <skill-directory>/scripts/github_access.py check --profile <employer-profile.json>
python -B <skill-directory>/scripts/github_access.py onboard --profile <employer-profile.json>
python -B <skill-directory>/scripts/github_access.py rotate --profile <employer-profile.json>
python -B <skill-directory>/scripts/github_access.py setup --profile <employer-profile.json> --root <checkout>
python -B <skill-directory>/scripts/github_access.py check --binding <checkout>/.agents/github-access.json
python -B <skill-directory>/scripts/github_access.py gh --binding <checkout>/.agents/github-access.json -- pr list
python -B <skill-directory>/scripts/github_access.py git --binding <checkout>/.agents/github-access.json -- fetch origin
```

Onboarding opens GitHub's fine-grained token form and a temporary local submission page. The human
creates the token with the displayed organization as resource owner, selects only the declared
repositories, obtains required organization approval, then pastes into that local page. Never request
a token in chat, arguments, an environment dump, clipboard automation or a file. Classic tokens and
account-wide OAuth are not fallbacks. Preserve the active version when candidate verification fails.

The command uses GCM with Windows Credential Manager, or an explicitly configured Secret Service/GPG
backend on Linux. An unavailable secure backend fails closed. It never configures a host-wide gh login
or writes a plaintext credential. Public state holds only a credential version identifier.

Network Git uses an isolated HOME. If effective repository configuration changes under that isolation,
including HOME-dependent `includeIf` conditions, the command fails closed. Use absolute or
HOME-independent include conditions for employer Git configuration.

Setup records the exact Git root, employer declaration and immutable runtime in a public binding. Each
Git/gh invocation rechecks that selection and the employer origin before reading the credential. Leaving
the selected checkout or changing its origin disables the binding. Git helpers only answer allowlisted
HTTPS repository requests; Git's store/erase callbacks cannot replace or delete the active version.

The installed `gh` executable is a scoped launcher. Its supported commands use a closed argument grammar;
unknown commands, alternate hosts, cross-repository targets, browser/editor launches and arbitrary GraphQL
are rejected. REST `api` supports repository reads. Use the owning repository's delivery procedures for
writes and merges. Unsupported operations require a reviewed capability change, never a token export or
an account-wide login. The token exists only in the authorized child environment and secure vault.

`setup` installs the runtime and public binding. Native host settings and MCP wiring remain separate
acceptance gates. Never claim this source command has configured a running Claude or Codex session.

`check` verifies identity, repository access and declared read capabilities. A successful read does not
prove write permission or the token's resource-owner selection; the human must confirm the token form,
and actual authorized operations establish their own permission evidence. A 401 proves invalid
authentication, not its precise expiry cause. SSO headers, policy/approval ambiguity, rate limiting and
network failure receive distinct actions. Never relay raw HTTP, GCM or child-process diagnostics that
could contain a credential. Tool routing and native installation must also pass before claiming full
Git/gh/MCP readiness.
