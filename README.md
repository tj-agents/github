# GitHub

Deterministic GitHub collaboration utilities for agent workflows, usable by personal and work
projects. Project policy and user authorization remain with the caller.

The first utility, `github:collaboration`, reads compact issue/PR context, posts top-level
comments, submits reviews with inline batches, and replies to review threads. It uses the
existing authenticated `gh` command and Python 3.12+, with no runtime dependencies.

```text
python -B .agents/github/utility/collaboration/scripts/github_collaboration.py --input request.json
```

See the [request examples and behavior](.agents/github/utility/collaboration/SKILL.md).
Use `python3` on Linux. Both hosts receive the same generated skill and shipped script.

This package owns GitHub interaction mechanics. Core owns engineering review and delivery;
work-items owns record semantics; github-actions owns CI workflow standards and generators.
Selecting this utility does not automatically adopt personal policy, change repository settings,
or grant posting authority. It does not replace the complete gh CLI.

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
