# GitHub

Deterministic GitHub collaboration and work-item operations for agent workflows, usable by personal and work
projects. Project policy and user authorization remain with the caller.

The first utility, `github:collaboration`, reads compact issue/PR context, posts top-level
comments, submits reviews with inline batches, and replies to review threads. It uses the
existing authenticated `gh` command and Python 3.12+, with no runtime dependencies.

```text
python -B .agents/github/utility/collaboration/scripts/github_collaboration.py --input request.json
```

See the [request examples and behavior](.agents/github/utility/collaboration/SKILL.md).
Use `python3` on Linux. Both hosts receive the same generated skill and shipped script.

This package owns GitHub interaction mechanics and managed work-item records. Core owns Workflow and
Engineering; Personal owns automatic recording and progress policy for explicitly adopted projects;
github-actions owns CI workflow standards and generators.
Selecting these operations does not automatically adopt personal policy, change repository settings,
or grant posting authority. It does not replace the complete gh CLI.

## Work items

`github:work-item` inspects records, `github:create-work-item` records or adds recurrence evidence,
and `github:address-work-item` reconciles authorized execution and factual progress with its existing
owner. Features, stories, tasks, bugs, mistakes and debt share one runtime and request journal.

The affected source repository's primary checkout selects the provider: an explicit
`.problem-records.json` naming `github:work-item`, or its existing Personal adoption default. The
observing repository supplies neither that selection nor recording authority. Workflow resolves the
source policy separately, including its bounded recording resource when observation crosses projects.

Creation places the issue on repository and owner boards. Progress records factual evidence and updates
both boards to an exact existing Status option, then verifies readback. Closure requires acceptance
evidence and a verified closed issue before both boards become Done. Missing options or partial failures
leave the operation incomplete against the same issue; retries preserve its request ID and receipts.
The authenticated `gh` identity needs project access for board operations. No operation installs or
configures credentials.

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
