---
name: configuration
description: Declare a GitHub owner's settings in one file and converge them with plan and apply, including settings GitHub exposes only in its web UI such as package Manage Actions access. Use when onboarding a repository or fixing GitHub access instead of changing settings by hand.
kind: utility
domain: github
profile: core
applicability: explicitly requested GitHub settings changes
requires: uv, Google Chrome
provenance: house
---

# GitHub configuration

Run the shipped entry point with `uv`, which installs its pinned Playwright. Resolve its path relative to
this installed skill. A codebase's declaration lives in that codebase's own plugin and is passed by path;
this skill holds no owner data.

```text
uv run <skill-directory>/scripts/github_configuration.py login
uv run <skill-directory>/scripts/github_configuration.py plan --declaration <plugin>/github-configuration.json
uv run <skill-directory>/scripts/github_configuration.py apply --declaration <plugin>/github-configuration.json
```

```json
{
  "schema_version": 1,
  "owner": "Concertable",
  "packages": {
    "nuget": {
      "Concertable.Kernel": { "actions_access": { "platform-dotnet": "write" } }
    }
  }
}
```

`plan` prints each declared difference and exits without writing. `apply` makes only those differences,
then reloads and fails unless the page reads back as declared. Repositories the declaration does not name
are reported `unmanaged` and never removed. Roles are `read`, `write` and `admin`; package types are
`container`, `maven`, `npm`, `nuget` and `rubygems`.

API-less settings run headless in installed Chrome through a persistent profile at
`<agent-state>/github-configuration/browser-profile` (`AGENT_STATE_DIRECTORY`, default `~/.agents-state`).
A profile under the system temporary folder can be denied network access by endpoint security, so keep it
there. `login` opens a visible window for the account owner's own sign-in and returns once GitHub shows a
signed-in page. Any sign-in, two-factor or sudo prompt during `plan` or `apply` stops with exit code 2 and
names the command to rerun; the agent never enters credentials.

Run `apply` only when the current conversation authorizes that owner's change. The browser acts as the
signed-in account, so never use it to approve or merge pull requests.
