---
name: approve
description: Hand a pull request the GitHub App opened over to the human for approval. Use after opening or readying a pull request in a repository whose GitHub access resolves to app.
kind: utility
domain: github
profile: core
applicability: repositories whose GitHub access resolves to app
requires: tj, agent-store
provenance: house
---

# Approve

After you open or ready a pull request in a repository whose GitHub access resolves to `app`,
link it to this session's work item, then give the human its link and the approval command.
Never run the command yourself: it approves as the human, and it refuses to run without their
terminal.

```text
tj item link <ref> https://github.com/<owner>/<repo>/pull/<number>
Human, in your own terminal: tj approve --session claude:<CLAUDE_CODE_SESSION_ID>
```

In Codex the session is `codex:<CODEX_THREAD_ID>`. Expand the variable to its value in the line
you give the human.
