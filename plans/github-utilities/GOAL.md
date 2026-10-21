# General GitHub collaboration utilities

## Authority and assessment

Tommy authorized creation of tj-agents/github on 2026-10-21 for independently useful GitHub
operations serving personal, work and other callers. Carry forward the completed investigation
from Core's Docs/GitHubUtilityInvestigation checkout; no repeat research or model session is
needed to select the transport. Adopt existing gh mechanics and implement only validation,
request reconciliation and compact output. GitHub Actions remains a separate owner.

Create and validate the package and its repository. Initial scope is issue/PR context,
conversation comments, inline review batches and replies. No automatic adoption in other
repositories, Core review redesign, credential installation, permission changes, live posting,
additional agents or native review sessions is authorized by this plan.

## Delivery boundary

One initial package with one collaboration utility and a packaged stdlib script, tests,
development selections and generated host payloads. No speculative PR administration or
repository-settings writer. Expected substantive code/tests remain around 1,000 lines.
A marketplace catalog entry follows only after the producer is usable.

Measured bootstrap scope: 553 runtime lines, 538 test lines and six fixture-setup lines.
Keep this 1,097-line implementation/test unit atomic: one collaboration script and its safety
fixtures ship together. A transport-only preparatory release would expose an incomplete utility,
and splitting the safety tests would leave write behavior unverified. The remaining initial
files are the required Template scaffolding (including its 1,096-line vendored generator),
generated host/payload copies, declarations, documentation and a 360-line lockfile. This is a
new repository bootstrap, with future unrelated operations kept in separate delivery slices.

## Progress and remaining work

Template scaffold created at C:/Users/TommySeery/source/repos/tj-agents/github; Git initialized
on main with origin tj-agents/github. Plugin-repository, Python, GitHub Actions, Core and
personal development declarations were generated and their adoption check passed. Personal
native instructions and policy CI were generated. uv.lock was resolved.

GitHub App repository creation was rejected by both GraphQL createRepository and the documented
REST POST /orgs/tj-agents/repos with permission errors. Do not switch to Tommy's saved account
without an explicit exception to personal:bot-identity. Local implementation and validation
continue while this remote boundary is unresolved.

Local implementation is complete. Windows validation passed: 46 deterministic fixtures, ruff
format/check, strict mypy (four source files), Template conformance, generation freshness,
plugin-adoption check and personal-policy check. A read-only real gh smoke test fetched Core
PR #335 at fef9cf9656a70158a1f46550e9642c0f8e9ca523 through the source, generated Claude and
generated Codex script paths; all returned the same head and 13 changed files. The Windows
PowerShell generator wrapper passed. No live comment or review was posted.

Linux CI is pending because the remote repository cannot yet be created; local WSL inventory
has only docker-desktop, not a general Linux test environment. Fresh native Claude/Codex
session loading and independent native review remain unverified under the no-additional-session
restriction. Source checks and script-path execution do not establish installed-host acceptance.

Next: resolve remote creation, then push the checked local checkpoint and run producer CI
before adding its marketplace entry. Remote creation resolver: Tommy can create
an empty public tj-agents/github, or explicitly authorize a one-time creation using his saved
gh account. Keep all subsequent eligible agent operations under the App identity. No live GitHub write or installed-host behavior is
claimed by a fixture test. Required native independent review remains pending under the
existing no-additional-session restriction.

## Acceptance

- Exact host/repository/target identity, distinct issue/PR/comment/review operations.
- Exact expected review head and validated inline path/line/side/range.
- Stable request markers reconcile duplicate and uncertain writes without a local database.
- Native gh identity remains unchanged; responses identify the actual resource author.
- Compact output by default, explicit discussion retrieval, meaningful failure results.
- Offline deterministic tests, formatting, lint, typing, packaging and adoption checks pass.
- Publication and any unverified entry point are reported truthfully.

```agent-host-coverage
{"schema_version":1,"shared_source":".agents/github/utility/collaboration/SKILL.md","hosts":[{"host":"claude","behavior":"Invoke packaged deterministic GitHub collaboration utility","source":".agents/github/utility/collaboration/SKILL.md","mapping":".claude/skills/collaboration/SKILL.md and plugins/github","verification":{"level":"source","result":"passed","evidence":"46 Windows fixtures, strict checks and generated packaging passed; source and both generated script paths read Core PR 335 successfully. Fresh installed-host sessions and Linux CI remain pending."}},{"host":"codex","behavior":"Invoke packaged deterministic GitHub collaboration utility","source":".agents/github/utility/collaboration/SKILL.md","mapping":".codex/skills/collaboration/SKILL.md and plugins/github","verification":{"level":"source","result":"passed","evidence":"46 Windows fixtures, strict checks and generated packaging passed; source and both generated script paths read Core PR 335 successfully. Fresh installed-host sessions and Linux CI remain pending."}}]}
```
