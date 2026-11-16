# GitHub

Deterministic GitHub collaboration utilities for agent workflows, usable by personal and work
projects. Project policy and user authorization remain with the caller. It's one of TJ Agents'
plugin families — Tommy's agentic engineering standards and tooling for Claude Code and Codex,
with harness parity: both hosts load the same generated skills. A repository selects it by adding
`github@tj-agents` to `.agents/plugins.json` against the `tj-agents/marketplace` marketplace.

The first utility, `github:collaboration`, reads compact issue/PR context, posts top-level
comments, submits reviews with inline batches, and replies to review threads. It uses the
existing authenticated `gh` command and Python 3.12+, with no runtime dependencies.

```text
python -B .agents/github/utility/collaboration/scripts/github_collaboration.py --input request.json
```

See the [request examples and behavior](.agents/github/utility/collaboration/SKILL.md).
Use `python3` on Linux. Both hosts receive the same generated skill and shipped script.

For organization-scoped employer credentials, use [GitHub access](.agents/github/utility/access/SKILL.md).
It supplies browser onboarding, secure OS storage, rotation and diagnostic checks with employer-owned data.

This package owns GitHub interaction mechanics. Core owns engineering review and delivery;
work-items owns record semantics; github-actions owns CI workflow standards and generators.
Selecting this utility does not automatically adopt personal policy, change repository settings,
or grant posting authority. It does not replace the complete gh CLI.

## GitHub access gate

The access gate decides, per command, whose GitHub credentials an agent uses. It is machine
tooling, not a skill: `tj install` runs its setup for any repository that selects
`github@tj-agents`, and nothing in a session has to load or invoke it.

A repository opts in by committing `.agents/github-access.json`. Without the file, access is
`human`. Only the version committed at `HEAD` counts, so an untracked or uncommitted file is
ignored and each worktree reads its own commit.

```json
{"schema_version": 1, "method": "human"}
{"schema_version": 1, "method": "app", "app": "<github-app-slug>"}
```

The fields must match exactly. Any other method or field makes the declaration invalid, and an
invalid committed declaration stops agents in that repository without changing what humans get.

A process is an agent when its environment carries a host's session marker: `CLAUDECODE` or
`CLAUDE_CODE_SESSION_ID` from Claude Code, or `CODEX_THREAD_ID` from Codex. Every other process
is a human. A human always reaches the real `gh` and their own Git credential helpers unchanged.

| Who and where | `gh` | Git over `https://github.com` |
| --- | --- | --- |
| Human, anywhere | Real `gh`, environment unchanged | The human's own helpers |
| Agent, no declaration or `human` | Real `gh`, environment unchanged | The human's own helpers |
| Agent, `app` | Real `gh` with `GH_TOKEN` set to the App's installation token | The App's installation token |

In an `app` repository the target account comes from the command's `--repo`, URL, API path or
`--owner`/`--org`, then `GH_REPO`, then the `origin` remote. The App covers exactly the accounts
it is installed on, read from GitHub. An agent's command is refused when it targets an account
the App does not cover, names two accounts, targets another host, or uses `gh auth` for anything
but `gh auth status`. When Git cannot be answered as the App, the helper tells Git to quit. An
agent in an `app` repository never falls back to the human's credentials.

### Machine setup

`tj install` runs these from the installed plugin; run them yourself to repair or diagnose:

```text
python -B <plugin>/.agents/github/runtime/gate/github_gate.py setup
python -B <plugin>/.agents/github/runtime/gate/github_gate.py setup --check
```

Setup copies the runtime to `~/.agents-state/github-gate/runtime/` (`AGENT_STATE_DIRECTORY`
moves it). It also installs the `gh` shim (`gh.exe` on Windows) in tj's bin directory:
`%LOCALAPPDATA%\Programs\tj` on Windows, otherwise `~/.local/bin`, or `TJ_BIN_DIR`. That
directory must come before the real `gh` on `PATH`; the shim finds the real `gh` by skipping its
own launchers. Setup then appends a marked block to the end of your global Git configuration:

```text
# >>> tj-agents github access gate (written by setup; do not edit) >>>
[credential "https://github.com"]
	helper =
	helper = "!<python> -B ~/.agents-state/github-gate/runtime/github_gate.py credential"
	helper = "<each github.com helper your configuration already had, in order>"
# <<< tj-agents github access gate <<<
```

The block resets the github.com helper list, runs the gate first, then re-adds the helpers that
already applied to github.com. Setup is idempotent, prints what it changed, and writes nothing to
Claude or Codex settings. `setup --check` changes nothing. It reports a stale runtime, shim or
block, a `gh` on `PATH` that is not the shim, and configuration added after the block. It also
reports changes to your github.com helpers and an App the current repository declares that this
machine has not enrolled.

### Onboarding an App

The App's private key is the only secret. Enrol it once per machine, as the human:

```text
python -B ~/.agents-state/github-gate/runtime/github_gate.py onboard app --app <slug> --key <private-key.pem>
```

In a repository that declares an App, `tj install --onboard` asks for the key path instead.
Onboarding reads the App's public id from GitHub, verifies that the key signs as that App,
stores it readable only by you, and lists the accounts the App is installed on. It never prints
the key or a token. To cover another account, install the App on it in GitHub; no local change
is needed. Create a new App in GitHub's settings, then enrol its key.

### Limits

- Agents acting as an App over Git need Git 2.46 or newer. The gate answers with a one-off
  `Authorization` value, so Git's later `store` and `erase` give your helpers no password to
  save. Older Git is told to quit.
- Unless `credential.useHttpPath` is set, Git does not tell helpers which repository it is
  fetching, so the gate uses the `origin` owner. A remote under another account then fails
  rather than using your credentials.
- Git Credential Manager prints `Missing 'password' input argument` when Git asks it to store
  the App's credential. The message is harmless; nothing is stored.
- Setup refuses github.com helpers it cannot reproduce after its own: helpers scoped to a path or
  user on github.com, and conditional includes that set credential helpers.
- Repository-local Git configuration can still reset credential helpers. The gate prevents
  accidents, not a hostile process.
- SSH remotes never ask credential helpers, so an agent would use your SSH key. Give `app`
  repositories an `https://github.com/...` remote.
- The shim and helper run the Python that ran setup. After removing that Python, rerun
  `tj install`.
- The employer [GitHub access](.agents/github/utility/access/SKILL.md) utility is separate. Its
  untracked per-checkout binding uses the same path, and the gate ignores it because it is not
  committed.

## Development

```text
uv sync --locked
uv run ruff format --check
uv run ruff check
uv run mypy
uv run pytest
python -B .agents/sync_generated.py --root . --check
```

The layout and generated host payloads come from Template. Windows and Linux fixture tests
use a fake API without posting to GitHub. Fresh installed-host acceptance remains separate
from source validation.
