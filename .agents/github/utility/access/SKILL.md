---
name: access
description: Enroll, rotate and diagnose repository-declared GitHub and Azure DevOps credentials, including private source reads in GitHub Actions, Git, gh, MCP and package feeds. Use for missing, expiring or refused credentials and credential adoption.
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

A repository Tommy owns selects `github@tj-agents` and commits its nonsecret declaration as the
`credentials` section of `.agents/repository.json`. An employer repository commits nothing: its uncommitted
local `.agents/plugins.json` selects the employer plugin, whose installed payload ships the same
declaration as `credentials.json`. Use [the declaration contract](references/declarations.md) for the
provider, account, scopes, sources and precedence. Linked worktrees share the declaration and account
enrollment; no per-worktree authentication setup is required. An explicit empty committed credential list
opts that branch out. Uncommitted `.agents/repository.json` declarations are ignored.

Machine setup installs `tj-credentials`, the native NuGet provider and the Git/dotnet/npm/pnpm/Docker/Azure
CLI launchers. Declared agent Git requests include their actual repository path; human Git settings and
credential-helper behavior stay intact. Git and gh use the access gate. The plugin supplies the scoped, read-only GitHub MCP
connection. Keep tj's bin directory before the real tools on PATH; setup refuses to replace foreign commands.
For an invalid committed declaration, Git `status`, `diff`, `add` and `commit` remain available for repair.
Agent Git network commands report the declaration error until that repair is committed.

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
Service/GPG store. GPG/pass needs an initialized store and `GPG_TTY` set from the current terminal.
An unavailable secure backend fails closed. NuGet receives its credential through the
plugin protocol; Docker uses its configured OS helper. npm, pnpm and Azure CLI receive only their own
credential in the required child environment, as does a declared placeholder `variable` that the
repository's own NuGet or npm configuration reads. See the declaration contract for their configuration constraints.

After enrollment, rerun the original operation. Source tests or a received token alone do not establish
installed readiness. Verify the selected native consumers on Windows/Linux, including an inherited linked
worktree; report any unavailable entry point with its resolver and observable acceptance condition.

When an Actions job cannot fetch a private source, use the workflow's secret name to find its
`actions_secret` declaration, then run `tj-credentials check --name <credential-name>` from the
destination repository. If absent, add the nonsecret declaration using the
[declaration contract](references/declarations.md#private-source-reads-in-github-actions).
When a failed workflow needs a credential, start the displayed onboarding or publication command
yourself from the destination checkout and keep it running. It opens the existing guarded browser
form for the human's grant selection and publication consent. The
[browser flow](references/declarations.md#human-consent-and-process-lifetime) binds consent to the
operation, account, source grants, destination repository and secret. The broker accesses the owner's
GitHub CLI sign-in only after that consent. Keep agent identity markers intact. After the publication
receipt, rerun the failed workflow and verify its private fetch.
