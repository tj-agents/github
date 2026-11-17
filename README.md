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

For repository-declared Git/gh/MCP, package registry and Azure DevOps credentials, use
[GitHub access](.agents/github/utility/access/SKILL.md). It supplies browser onboarding, OS-vault storage,
rotation, expiry checks and native client routing from a committed declaration shared by linked worktrees.

This package owns GitHub interaction mechanics. Core owns engineering review and delivery;
work-items owns record semantics; github-actions owns CI workflow standards and generators.
Selecting this utility does not automatically adopt personal policy, change repository settings,
or grant posting authority. It does not replace the complete gh CLI.

## GitHub access gate

The access gate decides, per command, whose GitHub credentials an agent uses. It is machine
tooling, not a skill: `tj install` runs its setup for any repository that selects
`github@tj-agents`, and nothing in a session has to load or invoke it.

Access is declared in `.agents/github-access.json`, in one of two forms:

```json
{"schema_version": 1, "method": "human"}
{"schema_version": 1, "method": "app", "app": "<github-app-slug>"}
```

For an agent, the first of these that exists decides:

1. **The repository's own file**, committed at `HEAD`. An untracked or uncommitted file is
   ignored, and each worktree reads its own commit. Commit `{"method": "human"}` to opt one
   repository out of its owner's default.
2. **The owner's default**: the same file on the default branch of the `<owner>/.github`
   repository. The owner is the account of the repository's `origin` remote on github.com; a
   repository without one skips this step.
3. Otherwise, `human`.

```text
tj-agents/.github   .agents/github-access.json   {"schema_version": 1, "method": "app", "app": "sample-bot"}
tj-agents/notes     (no file)                     agents act as sample-bot
tj-agents/sandbox   {"method": "human", ...}      agents use the human's login
```

The gate reads an owner's default only when an App this machine has enrolled is installed on
that owner, using that App's installation token. Any other owner, such as an employer's, is
never looked up and keeps its normal access, so a machine that has not enrolled an App also
treats owner defaults as `human`. It never uses the human's credentials. A 404 means the owner
has no default. The result is cached in the gate's state
directory for 10 minutes; edits take effect after that. If the default cannot be read, agents
in that owner's repositories stop with the reason until it can.

The fields must match exactly. Any other method or field makes a declaration invalid. An invalid
repository file or owner default stops agents there, and a repository file naming an App this
machine has not enrolled stops them with the onboarding command. Humans are never affected and
never cause an owner lookup.

A process is an agent when its environment carries a host's session marker: `CLAUDECODE` or
`CLAUDE_CODE_SESSION_ID` from Claude Code, or `CODEX_THREAD_ID` from Codex. Every other process
is a human. A human always reaches the real `gh` and their own Git credential helpers unchanged.

| Who and where | `gh` | Git over `https://github.com` |
| --- | --- | --- |
| Human, anywhere | Real `gh`, environment unchanged | The human's own helpers |
| Agent, access resolves to `human` | The declared credential when selected for gh, otherwise the human's login | The declared Git credential when selected, otherwise the human's own helpers |
| Agent, access resolves to `app` | Real `gh` with `GH_TOKEN` set to the App's installation token | The App's installation token |

When access resolves to `app`, the target account comes from the command's `--repo`, URL, API path or
`--owner`/`--org`, then `GH_REPO`, then the `origin` remote. The App covers exactly the accounts
it is installed on, read from GitHub. An agent's command is refused when it targets an account
the App does not cover, names two accounts, targets another host, or uses `gh auth` for anything
but `gh auth status`. When Git cannot be answered as the App, the helper tells Git to quit. An
agent whose access resolves to `app` never falls back to the human's credentials.

### Machine setup

`tj install` runs these from the installed plugin; run them yourself to repair or diagnose:

```text
python -B <plugin>/.agents/github/runtime/gate/github_gate.py setup
python -B <plugin>/.agents/github/runtime/gate/github_gate.py setup --check
```

Setup copies the runtime to `~/.agents-state/github-gate/runtime/` (`AGENT_STATE_DIRECTORY`
moves it). It installs `gh`, `tj-approve` and the credential broker's native client launchers in
tj's bin directory: `%LOCALAPPDATA%\Programs\tj` on Windows, otherwise `~/.local/bin`, or
`TJ_BIN_DIR`. See [GitHub access](.agents/github/utility/access/SKILL.md) for the credential
commands and client requirements. Keep that directory before the real tools on `PATH`; the shims
skip their own launchers when finding the native client. Setup refuses to replace foreign commands.
It then appends a marked block to the end of your global Git configuration:

```text
# >>> github@tj-agents access gate (written by setup; do not edit) >>>
[credential "https://github.com"]
    useHttpPath = true
	helper =
	helper = "!<python> -B ~/.agents-state/github-gate/runtime/github_gate.py credential"
	helper = "<each github.com helper your configuration already had, in order>"
# <<< github@tj-agents access gate <<<
```

The block resets the github.com helper list, runs the gate first, then re-adds the helpers that
already applied to github.com. Setup is idempotent, prints what it changed, and writes nothing to
Claude or Codex settings. `setup --check` changes nothing. It reports a stale runtime, shim or
block, a `gh` on `PATH` that is not the shim, and configuration added after the block. It also
reports changes to your github.com helpers and an App the current repository's committed file
names that this machine has not enrolled. It does not read owner defaults, so it works offline;
agents meet an unenrolled owner-default App with the onboarding command instead.

### Onboarding an App

The App's private key is the only secret. Enrol it once per machine, as the human:

```text
python -B ~/.agents-state/github-gate/runtime/github_gate.py onboard app --app <slug> --key <private-key.pem>
```

In a repository whose committed file names an App, `tj install --onboard` asks for the key
path instead.
Onboarding reads the App's public id from GitHub, verifies that the key signs as that App,
stores it readable only by you, and lists the accounts the App is installed on. It never prints
the key or a token. To cover another account, install the App on it in GitHub; no local change
is needed. Create a new App in GitHub's settings, then enrol its key.

### Approving the App's pull requests

The App opens pull requests; you approve them as yourself with `tj approve`, which `tj` runs
through the `tj-approve` launcher setup installs.

The gate hands each pull request over itself. After an agent's `gh pr create` succeeds in a
repository whose access resolves to `app`, it asks GitHub, as the App, for the pull request's
review decision. Drafts, `--web` and `--dry-run` are excluded. The same check runs after
`gh pr ready`, except with `--undo`. When the decision is `REVIEW_REQUIRED`, meaning the
repository's merge policy needs your approval, the gate:

1. finds the agent's session in agent-store: the `TJ_RUN_LAUNCH_ID` launch, else
   `claude:<CLAUDE_CODE_SESSION_ID>` or `codex:<CODEX_THREAD_ID>`;
2. links the pull request to that session's one work item with
   `tj item --profile github link <ref> <url>`;
3. puts `! tj approve` on your clipboard, verified by reading it back (tj-cli's clipboard,
   shipped with the gate: Windows' clipboard API, `wl-copy`/`xclip`/`xsel` on Linux, `pbcopy`
   on macOS);
4. prints one line naming the pull request, the work item, and that `! tj approve` is on the
   clipboard.

```text
github-gate: https://github.com/sample-org/core/pull/5 needs your review: linked it to <work item ref>, and `! tj approve` is on your clipboard; paste it into this session to approve
```

Any failure is printed with its reason and what to do instead, for example when there is no
registered session, several or no linked work items, a failed link or no clipboard. A handover
never changes the gh command's exit status. When the review decision is anything else, nothing
happens.

`tj approve` finds the session's work items in agent-store, including those of sessions and
launches it started, and asks `tj item` for their pull requests. The session comes from the
environment the command runs in; `--session <host>:<id>` names another one.

It keeps an open pull request only when its author is the App that the pull request's
repository declares, read from GitHub (the repository's file on the base branch, then the
owner's `.github` default). You must also not have approved its current head, unless a review
from you is requested. It lists them, then approves each and arms squash auto-merge pinned to
the approved head, printing each pull request it approved and its auto-merge result. It asks
nothing, so it works from a session's `!` shell. It exits non-zero if any approval or auto-merge
fails.

It always runs the real `gh`, never the gate's shim, with your own login. It removes
`CLAUDECODE`, `CLAUDE_CODE_SESSION_ID`, `CODEX_THREAD_ID`, `GH_TOKEN`, `GITHUB_TOKEN`,
`GH_CONFIG_DIR` and every `GIT_CONFIG*` variable from the environment of each command it runs.
Your reviewer identity is whatever `gh api user` reports.

Agents are denied `tj approve` and `tj-approve` by their hosts. The plugin's harness manifest
declares the rules (`claude_deny` and `codex_forbidden_prefix_rules`), and Agent writes them into
each selecting repository's committed Claude and Codex settings. They are the only guard: they
are host permission rules, not an OS boundary around your account.

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
- [Declared credentials](.agents/github/utility/access/SKILL.md) use `.agents/credentials.json`.
  The App declaration remains the source of agent Git/gh App selection; package clients use their
  explicitly declared credential type.

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
