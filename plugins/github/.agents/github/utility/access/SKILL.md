---
name: access
description: Enroll, rotate and diagnose repository-declared GitHub and Azure DevOps credentials, including Git, gh, MCP and private package feeds. Use when a declared credential is missing, expiring or refused, or when adopting credential tooling.
kind: utility
domain: github
profile: core
applicability: explicitly declared repository credentials
requires: Python 3.12+, Git Credential Manager with an OS-backed store; native client requirements follow the declared endpoints
provenance: house
---

# Declared credentials

Owns adopting, enrolling and checking the shared credential broker. GitHub App selection remains with the
[access gate](https://github.com/tj-agents/github#github-access-gate); a selected App retains priority for agents' Git and gh.

```text
tj install
tj-credentials check
tj-credentials onboard --name work-packages
tj-credentials rotate --name work-packages
```

A repository selects `github@tj-agents` and commits its nonsecret `.agents/credentials.json` declaration.
Use [the declaration contract](references/declarations.md) for the provider, account, scopes and actual
resources each client needs. The broker reads the current commit, then the fetched `origin/HEAD` when a
branch has no declaration. Linked worktrees share account enrollment; no checkout setup or host activation
is required. An explicit empty credential list opts that branch out. Uncommitted declarations are ignored.

Machine setup installs `tj-credentials`, the native NuGet provider and the Git/dotnet/npm/Docker/Azure CLI
launchers. Declared agent Git requests include their actual repository path; human Git settings and
credential-helper behavior stay intact. Git and gh use the access gate. The plugin supplies the scoped, read-only GitHub MCP
connection. Keep tj's bin directory before the real tools on PATH; setup refuses to replace foreign commands.

Run `check` before requesting human action. It checks identity, declared scopes and resource access,
and reports invalid authentication, wrong accounts, SSO, permissions, network failures and expiry separately.
Client launchers perform these checks before using the credential and warn seven days before expiry.
A check proves the declared reads; it does not prove write permissions or every package/image download.
Actual authorized writes and builds establish that evidence.

For a missing, invalid or expiring credential, run the one displayed `onboard --name` command. It opens the
issuer's creation page and a guarded local submission page. The human selects the declared type, account,
scopes and expiry, completes any organization approval or SSO, then submits locally. Keep the process alive
until verification and secure storage complete. A rejected candidate preserves the working enrollment.
Never request the value in chat, arguments, clipboard automation, an environment dump or a file. Do not
change token types, widen scopes or change organization policy after a refusal.

Tokens are stored through GCM in Windows Credential Manager or an explicitly configured Linux Secret
Service/GPG store. An unavailable secure backend fails closed. NuGet receives its credential through the
plugin protocol; Docker uses its configured OS helper. npm and Azure CLI receive only their own credential
in the required child environment. See the declaration contract for their configuration constraints.

After enrollment, rerun the original operation. Source tests or a received token alone do not establish
installed readiness. Verify the selected native consumers on Windows/Linux, including an inherited linked
worktree; report any unavailable entry point with its resolver and observable acceptance condition.
