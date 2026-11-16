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
link it to this session's work item, then hand the human the command `tj approve` with the
pull request's link, following `cli:clip`'s handover rule. Never run `tj approve` yourself; it
approves as the human.

```text
tj item link <ref> https://github.com/<owner>/<repo>/pull/<number>
Handed to the human: tj approve, with https://github.com/<owner>/<repo>/pull/<number>
```
