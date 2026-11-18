# Shared source-owned work items and board progress

Current checkpoint: 2026-11-03. See Story12 for the durable work record.

Tracker: https://github.com/tj-agents/github/issues/11

Authorization: Tommy, 2026-11-02 — initially investigate and recommend; subsequently approved implementation
with "right, shall we sort this out then?" after confirming shared personal-project item/board management
and source-targeted tj-agents defect routing from any observation context. Implement, test, commit and
prepare reviewable delivery under the source repositories' gates. Their merge restrictions and employer
evidence/installation boundaries still apply. Existing owners keep their work; do not take them over.

## The problem

- The Personal plugin bundles two different things:
  1. personal standards and rules for personal projects, which must stay out of work projects;
  2. the recording path for defects in Tommy's own repos (`github:create-work-item` / `work-item`), which
     tj-agents defects need.
  never select or inject Personal there"). Point 2 disappears with point 1, so a tj-agents defect found while
  `~/.claude/plans/tj-agents/workspace-tier-gate-and-self-repair-handoff.md`, Defects 1–4) and none were recorded
  by default.
- "Load Personal everywhere" is wrong: it would apply personal scope to work projects.
- Tommy's requirement (2026-11-02, verbatim intent): when he calls out a tj-agents failure in ANY repo, anywhere
  (personal, work, or no repo), the agent runs the appropriate routine by default — because the failure is
  relative to tj-agents, not to the repo he's in. The routine is one of: create a tj-agents work item; find the
  existing one and add to / refine it; or hand off the repair. Doing it must never make the current repo
  personal.
- Tommy's batching requirement (2026-11-02): tj-agents has too many defects to hand off one session per bug.
  After recording, the routine picks one route: (1) a live repair owner already works the same area (same
  source repo or behavior cluster, e.g. handoff/messaging, tier scope) → append the defect to that owner's
  goal queue; (2) small, mechanical and independent → a background agent from the current session, with its
  own isolated checkout; (3) totally different area with no live owner → a separate handoff. One owner may carry several defects,
  but still ships one coherent PR per repository and behavior — never one PR mixing unrelated fixes.
- Tommy's plans requirement (2026-11-02): for personal projects the GitHub issue is the only durable plan and
  work record. A `GOAL.md` is allowed as working scratch while a branch is in flight, but is deleted before the
  PR is marked ready, so no `plans/` directory ever lands on main and committed plan folders go away. Update
  the owning standards (`engineering:plans`, `workflow:plan`, `workflow:run`, the docs scratch rule) and migrate
  existing `plans/` content into issues. Employer projects keep their own tracker rules.

## Initial investigation context

- Core `problem-records.md` (commit b4de4d5 on main) already selects the tracker by the defect's owning source
  repo, excluding employer-only facts — but selection lives in that repo's `.agents/plugins.json` /
  `.problem-records.json`, and the operation it names is a Personal skill that isn't loaded in work sessions.
- `tj-agents/agent` doesn't select `personal@tj-agents`; Core and Template do. Work Items plugin retired
  (Marketplace PR19, 2026-10-21).
- Personal draft PR51 (`Fix/IncidentalProblemRouting`) owns default incidental recording but limits it to
  authorized personal work.
- Template PR69 and PR70 were closed unmerged on 2026-11-02; the House rename was abandoned. Template self-repair remains the source owner.

## Questions to answer

1. Where should "record a defect against a tj-agents repo" live so it is available in every session, work or
   personal, without loading any personal standards into a work project? (e.g. split the recorder out of
   Personal into a scope-neutral plugin; tier gate admits only the recorder for tj-agents targets; recording
   owned by Agent/Core; something better.)
3. What makes recording plus handoff the default, so Tommy never has to ask (self-repair, PR51 scope, hooks)?
4. Which repos and PRs change, in what order, and how is each verified on an installed host?

## Current delivery checkpoint — 2026-11-03

Existing owner: execution:a177e7d5-3411-449d-8ad9-b2b08e681815; native owner codex:01a19c8e-3c8a-7da2-b668-66476e3349a6. Story12 remains the native child of Feature11. Preserve the existing issues, owners, request journals and independent work.

Personal Bug64 / PR65 now contains the completed source guided-onboarding correction at `db2280285b6d79873b5957237e246fba0886135d`. It is OPEN and ready for review, with current GitHub reviewDecision APPROVED and exact-head Windows/Linux/host CI41633986843 green. The same setup --onboard invocation keeps a valid key, guides App permissions then missing/updated installations, retries transient failures and finishes checkout-scoped host setup after full readiness. Ordinary setup/check remain browser-free. Merged PR62 checkout scope is preserved.

Three new causal regressions failed before; afterward103 tests and110 subtests passed,3 Windows platform skips. Generation121current, frozen exported source Template conformance and skill examples pass. Configured Ruff/mypy pass but exclude the changed helper/tests. Native incremental review62b1..db2280285b6d79873b5957237e246fba0886135d and independent workflow/security union with trunk46aa779 completed. Parent synthesis, exact current source/security markers, host coverage and descriptor-bound preflight passed. Two native credential-routing findings reproduce unchanged on the actual merged base; their existing source record60 has verified recurrence evidence at https://github.com/tj-agents/personal/issues/60#issuecomment-6603754776. No independent repair was folded into PR65.

The full unmodified source setup CLI rendered its actual missing-App-Projects page using the real enrolled App in pre-existing headlessChrome with an isolated new profile. Parent viewed it: key upload stays hidden and the exact missing permission/next GitHub action are visible. The real screenshot is attached inline to PR65, hosted at https://raw.githubusercontent.com/tj-agents/personal/6d892f14a8c60fdadb4d12973925a33ba43ec871/pr-65/db2280285b6d79873b5957237e246fba0886135d/missing-projects.png. The waiting process was cancelled after capture. No grant was changed or accepted and successful host setup was not observed. Post-grant states have real HTTP and JavaScript coverage; actual successful browser/installed behavior remains open.

## One concrete setup action

Tommy requested the full setup and offered to do its required account steps. Run the reviewed source invocation in Bash:

```bash
python -B "C:/Users/TommySeery/source/repos/tj-agents/personal.worktrees/Fix-ProjectsReadiness/.agents/personal/utility/bot-identity/scripts/bot_identity.py" setup --onboard --root "C:/Users/TommySeery/source/repos/tj-agents/personal"
```

The local page reuses the current enrollment. Follow its GitHub link, set Organization Projects to Read and write on the existing tomjseery-bot App, save it, and accept the new permission for the tj-agents installation. Keep the setup page and terminal open; it rechecks and continues automatically. No replacement key is needed for the current valid enrollment. These are actual GitHub account changes, not a PR approval request.

The owner was already authorized to perform the scoped grant, but the authenticated Computer Use/browser transport remains unavailable; a clean headless browser proved the local page and cannot substitute for Tommy's authenticated account action. No human-token fallback or employer installation is authorized. After actual setup succeeds, the existing execution must verify live organization Projects and separately qualify user-owned Projects; reconcile existing issue/board placements, honor source merge policy and publish producer/catalog before dependent retirement, then exercise fresh supported Claude/Codex installed entry points. This is still IN PROGRESS; source readiness is not rollout completion.

## Approval and source gate evidence

Tommy's successful Bash command produced eight verified approvals on the originally delivered heads (Temp/shared-human-approvals-20261103.json). PR65 subsequently changed from62b1 todb2280285b6d79873b5957237e246fba0886135d; its current GitHub reviewDecision remains APPROVED after ready-for-review. This does not claim Tommy submitted a new review at the new head. The other seven approvals are retained; do not request them again. PR65 is not merged, and no auto-merge or unattended continuation is armed. Source delivery binding currently resolves no recorded merge authorization and auto_merge=false; repository policy and any stop remain effective.

## Current deliveries

- github: https://github.com/tj-agents/github/pull/13 — `cac5d8647df696db3485180acc42b3c03d5024ae`; all configured checks green; OPEN, draft.
- personal: https://github.com/tj-agents/personal/pull/61 — `989413b5eb4e194bd29c80396855ba2a74a952c5`; all configured checks green; OPEN, draft.
- core: https://github.com/tj-agents/core/pull/545 — `1a221f25905560ad5875d506e16d4bd166ce6a01`; all configured checks green; OPEN, draft.
- agent: https://github.com/tj-agents/agent/pull/57 — `28f106bca1ab1afc12123b86808a571c02c278be`; all configured checks green; OPEN, draft.
- template: https://github.com/tj-agents/template/pull/71 — `5f8399ccd861b4112fce0336b67754c3ef9d361f`; all configured checks green; OPEN, draft.
- marketplace: https://github.com/tj-agents/marketplace/pull/46 — `9666fe2e1ef52bc0dc881b7d6d7d89307b721429`; all configured checks green; OPEN, draft.
- docs: https://github.com/tj-agents/docs/pull/31 — `4232c77c4f6b242476eb7549bab5cee0d82c70ff`; no CI configured; OPEN, draft.
- personal readiness: https://github.com/tj-agents/personal/pull/65 — `db2280285b6d79873b5957237e246fba0886135d`; source reviewed and configured CI green; OPEN, ready, current GitHub reviewDecision APPROVED.

## Verified progress

- GitHub full136 tests, generation93, Ruff/mypy17, Windows/Linux/host CI41491490671 pass. Neutral source-primary selection, managed identities, uncertain request receipts, selected authentication, concurrent first-use boards, casing, progress and closure regressions are covered. The final three-file supported Template refresh has a recovered completed native review: session01a19d69-5b5e-7321-98eb-ca60212a749b, exactc2d8f84→cac5d86, no findings;136tests/generation and hook/snapshot smoke tests pass. Genuine temporary native inventory separately resolves the selected standards.
- Core's source routing, exact unambiguous personal@tj-agents policy identity, lazy ordinary lookup and root CI regression collection are repaired. Final one-line pre-existing roster-count correction189fb68→1a221f25 has completed native review, no findings, discovery54/Claude54/Codex54. Full Windows/Linux/required CI41491491067 pass.
- Personal source policy and retirement slice, Agent default/host selection with upstream adoption/handoff fixes, Marketplace catalogs/dependency graph, and Docs references have completed source review. Personal/Agent/Marketplace configured CI is green. Docs has no configured CI.
- Template's own caller now uses the existing read-only automatic job token rather than optional supplied read_token; its exact corpus sources are all public. New Windows/Linux/host/system-resource CI41491496493 is green, proving the old checkout failure cleared. The final two-line correction passed native incremental review. A fresh independent security session reviewed the complete seven-path trunk range3d77fdbb..5f8399cc; its exact terminal outcome is preserved in the canonical review work order. Native source review and supported CI are complete; the three publication dependency findings remain open.
- All seven descriptor-bound preflights pass at their current exact heads, with unchanged bases and no unpublished code. Current canonical review receipts are recorded. GitHub retains its live board-acceptance finding; Template retains its three publication findings.

## Remaining acceptance and continuation

Actual App/installation grant acceptance and successful setup; organization and user-owned board access/placement; source merge and coordinated producer/catalog/consumer publication; retired-name cutover; supported fresh installed host routing, source ownership, recurrence, uncertain requests and progress transitions. GitHub13 retains its board acceptance finding; Template71 retains its three publication dependency findings. Existing code/source/CI evidence does not close those gates.

Sources remain <repository>.worktrees/Feature-SharedWorkItems except Agent uses Review-SharedWorkItems on Feature/SharedWorkItemsDelivery; Personal65 uses personal.worktrees/Fix-ProjectsReadiness. Preserve original Agent checkout conflicts and independent PersonalPR51/GitHubPR10. Do not install into employer repositories. Parent remains sole canonical work-order writer. Story12 is durable; GOAL.md is local working scratch. Resume with the same execution and current source bindings after the concrete setup action, without duplicate issues, owners or approval prompts.

## Findings

Investigation completed 2026-11-02 before implementation authorization. The findings below preserve
the assessment and pinned source evidence; current execution state is recorded above.

### Verified state

Several primary checkouts are stale. Evidence below uses GitHub's exact main revisions and live PR reads,
plus source inspection, rather than assuming the local checkout or the incoming handoff is current.

| Source | Verified main revision | Relevant evidence |
| --- | --- | --- |
| Core | `ded8692c81aa32ceae48fddd1a5e9c4900376714` | `.agents/workflow/resources/problem-records.md`, `.agents/hooks/problem_records.py`, `.agents/hooks/installed_skills.py` |
| Agent | `ae8d8823a2dcf6c8d1fe0272c773592b7fee7a34` | `.agents/agent/utility/plugins/scripts/plugin_selection.py`, `.agents/agent/runtime/required_plugins.py`, primary `.agents/plugins.json` |
| Personal | `b0470997bb86a0dc52a6cdb4108390d1e3aa2f5f` | `.agents/personal/utility/create/work-item/SKILL.md`, `utility/work-item/github_tracker.py`, `workflow/address/work-item/SKILL.md` |
| Template | `89ef8327674b770f38776a873aed79ea2a04e16e` | `.agents/template/utility/self-repair/SKILL.md`, `.agents/tiers/template.json` |
| Marketplace | `c6df0d81dc95dfb2f6455c3c577c779e7560b1b6` | `plugins.json`, `README.md`; local `catalog.json` is superseded |
| Docs | `be821e46d6646a55ad3418c5a1e87f866d9485a7` | `PLUGINS.md`, `WORKFLOW_AGENT_STORE.md` |
| GitHub | `e934b497afaaa8c1081eb4503cf2416b760ef7f9` | `README.md`, `.agents/github/utility/collaboration/SKILL.md`, harness manifests |

1. **Owning-source routing already exists.** Core's problem-record contract selects the affected source
   repository, reads its primary checkout's provider declaration, preserves employer boundaries, and
   supplies an installed-operation resolver when the skill is absent from the starting session's list.
   Core PR531 merged on 2026-10-31 (`b9dc69caf1f4171438ec8a315c794e29fa317657`). The missing behavior is
   broader than merely choosing the correct `--root` or finding an unlisted skill.
2. **Provider mechanics and personal scope are coupled.** Personal's `github_tracker.py:143–187` requires
   Personal adoption before examining an explicit provider declaration. Its public read/create/address
   family also assumes Personal. Core's resolver can locate those operations; it cannot make them neutral
   or authorize loading Personal in an employer session. Current Personal main additionally supports
   recurrence notes and reconcile-only retries, which must survive any extraction.
3. **GitHub already has a neutral source owner.** Its README defines GitHub mechanics for both personal
   and work projects, leaving policy and authorization to callers. It currently provides collaboration
   utilities, not the work-item provider. The shared Marketplace main roster does not yet list `github`.
   Its existence in a producer checkout therefore does not establish shared installed availability.
4. **Work Items is retired.** Marketplace PR19 merged on 2026-10-21 at
   `063a604c86051388cc3889eefe8734a22f19010d`. Surviving local source is historical, not an available provider.
5. **The self-repair conflict is still in published source.** Template main requires both originating task
   and source repository to be personal, and says to hand off only on request. Core's general incidental
   rule separately says a callout/record/standing repair authority does not itself dispatch work. Both
   must acknowledge the requested specific default; changing Personal's creator alone cannot fix this.
6. **Agent adoption has an existing owner.** Agent PR55 is open at
   `e0f1123cefebb1bb19608fe35b863a677be0a7aa`, with successful Windows/Linux/host checks. Main still lacks
   Personal adoption. The later origin handoff records Tommy's explicit adoption authorization; this
   investigation neither repeats that decision nor takes over PR55. Adoption alone leaves the work-session
   recording problem unsolved.
7. **Template PR70 is no longer an open CI dependency.** PR69 and PR70 were closed unmerged on
   2026-11-02 at 12:35:09 and 12:35:26 UTC. PR69's closing comment rejects the House-plugin migration and
   retains `template:self-repair`. PR68 remains open at `fdd43770603ad465c9936532d29544de937c99a0`, with
   successful checks, addressing host-feedback precedence. Preserve that owner's work; do not resurrect
   House or silently reopen the closed stack.
8. **Personal PR51 remains relevant but insufficient.** It is open/draft at
   `d4c4fff1c25c67f92d6339f5d0a388e5b19928d0`, limits default incidental recording to authorized personal
   work, and reports failing OS conformance checks. Its Core531 dependency has landed. Its owner must
   reconcile current Personal main's later recurrence behavior and any chosen extraction.

Evidence was obtained through read-only `gh pr list/view`, `gh api` commit/content reads, and scoped source
reads. PR descriptions are attributed reports of prior validation, not fresh test executions here.

### Recommended design: neutral GitHub provider, Workflow routing, source-owned self-repair

Clarification from Tommy, 2026-11-02: this must serve personal projects generally, including tech debt,
code issues, new features and other work items. A tj-agents defect observed from a non-personal project
enters that same personal-project routine. It is not a separate defect-only recording system.

**Personal routine** means the personal project's work-management workflow: classify the work, reconcile
an existing item or create it in the owning repository's GitHub tracker and boards, verify persistence,
preserve its identity and repair owner, and route the authorized planning, implementation or handoff.
The target project's established scope selects this policy; the observation's current directory does
not change ownership. Record-only requests stay record-only. A demonstrated tj-agents tooling defect
has the separately stated standing record-plus-repair-handoff default.

Tommy's further clarification, 2026-11-02: the imported capability also automates ongoing board updates
from what is actually happening. It reconciles the item, recurrence, repair links and board state with
observed progress, blockers and verified completion, under that project's policy. Creating an item once
does not satisfy this behavior. Personal projects can use this automation for their own defects and
authorized work; tj-agents uses it for tj-agents defects even when demonstrated in another repository.
The affected source determines the item and board destination independently of the observation location.

Import this bounded work-management capability into the observing session. Its selected target supplies
the recording/update policy and destination; this does not adopt Personal for the current repository or
import unrelated personal-project standards. For a tooling defect, only the tooling item follows that
personal routine while the originating project retains its own work policy. Repair owners supply verified
progress and completion evidence; observation or issue creation alone cannot mark a repair complete.

Represent this as one personal-project workflow using one shared GitHub provider:

| Observation or request | Owning scope and result |
| --- | --- |
| Feature, task, code issue, mistake or debt in an adopted personal project | Apply that project's Personal recording policy; use the shared GitHub provider for its tracker and boards; follow through within the request's authority. |
| tj-agents tooling defect found during any other task, including employer work | Apply the tooling source's personal-project routine to the separate tooling work item; record and reuse/hand off its repair under the standing authorization. Preserve the originating task. |
| Issue belonging to a non-personal product or employer tooling | Apply its own project/provider policy; no inferred personal scope or personal tracker destination. |

Personal owns the defaults and permissions for personal work. GitHub owns the common record operations.
Workflow resolves the target and selects its policy before invoking those operations. A neutral GitHub
installation alone grants no personal recording policy or implementation authority. The source-only
handoff enters the personal tooling project and loads its policy there; it does not inject that project's
Personal standards into the originating employer session. Self-repair is one trigger and follow-through
path within this arrangement; normal feature/debt/task creation uses the same provider and records.

Use the existing `tj-agents/github` producer for the extracted GitHub work-item provider. Move the complete
record adapter family and shared implementation from Personal, rather than copying a special tj-agents
creator: proposed names are `github:work-item`, `github:create-work-item` and `github:address-work-item`.
The address adapter preserves record identity and delegates to Core's existing Workflow/Engineering
lifecycles, as the current Personal adapter does; it does not acquire a second execution store or delivery
lifecycle. Update GitHub's current utilities-only authoring boundary explicitly for that adapter.

Preserve schemas, native issue-kind mapping, board behavior, request journals, reconciliation, read-back,
optimistic updates and closure evidence. Preserve existing GitHub identities and uncertain request IDs;
renaming the runtime must not change a request's deduplication identity or discard its journal. In
particular, do not resubmit the uncertain `workspace-remote-tiers-20261102` request under a new namespace.

Personal retains adoption and personal-project rules, including permission to record incidental personal
work. It selects the neutral provider; provider installation never selects Personal. Employer providers
and their recording decisions remain independent. Core resolves the target primary checkout's explicit
provider declaration first; for already adopted Personal repositories, its existing default becomes the
neutral GitHub provider. The GitHub helper validates that target selection and verified origin without
requiring the observing task to be personal. Missing or foreign declarations still block writes.

Publish `github` through Marketplace and make it part of Agent's ordinary neutral project selection, so a
work session can discover it without Personal. Declare the provider's real process prerequisites in the
catalog. Add GitHub to selection, not as a mandatory dependency of Agent or Core: the address adapter uses
Workflow/Engineering, whose existing dependency graph already reaches Agent. Verify the resulting graph
is acyclic. Existing projects need an explicit coordinated selection/refresh rollout; adding a default
does not update them. An `always` tier alone proves neither installation nor loading.

Put the **cross-scope default** in Core Workflow's existing problem-record contract, with a short pointer
from its already available Session policy. It applies to a demonstrated defect in an explicitly authorized
personal tj-agents tooling source, using Tommy's standing instruction recorded in this handoff. Source
scope and authorization control eligibility; the observer's employer scope does not invalidate that
source authority or become personal. Neither the `tj-agents` owner name alone nor an ordinary employer
product bug qualifies. `tj-agents/company` and employer tooling remain excluded.

The default sequence is:

1. Answer the direct question and preserve the original task's owner and next action. Establish the
   affected tj-agents source and safe source evidence; distinguish an agent overlooking an adequate rule
   from a defect requiring a source change.
2. Reconcile an existing record, repair owner and uncertain submission. Record a new defect or append
   material recurrence evidence through the neutral selected provider, then verify persistence.
3. Reuse the verified existing repair owner; otherwise invoke the existing bounded `workflow:handoff`
   into the source repository. Carry only the safe source payload described below. A blocked/uncertain
   tracker operation is not permission to submit again or start a second writer; preserve its request
   identity. A generic authorized handoff can delegate reconciliation without depending on issue creation.
4. In the source session, load `template:self-repair` through that repository's actual selection and
   perform the authorized repair lifecycle. The originating work session never needs to select Template
   or Personal merely to route this problem. A repair already constituting the active task stays there.

Make this a specific exception to Core's generic no-automatic-dispatch rule. Keep Template as the sole
owner of the source-repair procedure; remove its originating-personal prerequisite and conflicting
handoff wording for this explicitly authorized case. Preserve source gates, other owners, merge policy,
and the distinction between publication and consumer installation. This proposal grants no installation
authority in employer repositories.

### Employer evidence boundary

Both the tracker record and the handoff cross the boundary. Build their content from tj-agents source:
repository-relative source path, revision, expected/actual tooling behavior, and a synthetic reproduction
that uses dummy repositories or data. Do not forward the originating transcript, employer plan pointer,
absolute workspace paths, logs, product identifiers, code, business data, attachments or credentials.
Do not include a link to a private employer artifact as a substitute for copying it.

The neutral helper validates the selected target and record shape. The routing agent prepares and checks
the complete record and successor prompt, including titles and free text, for source-only evidence before
either write or launch. Deterministic path/provenance checks and sentinel fixtures support this boundary;
schema validation or a text blacklist cannot prove arbitrary prose contains no employer facts. When a
safe source-only account cannot be established, retain the sensitive evidence in the original authorized
task and hand off only a safe source investigation, or report the precise missing decision. Do not claim
a demonstrated cause or publish employer facts to satisfy an automatic-recording default.

### Delivery order and existing PRs

The approved delivery order follows. Publish changes as one coordinated provider cutover.

| Order | Source owners and change | Gate before dependent activation |
| --- | --- | --- |
| 1 | Reconcile Personal PR51, Agent PR55, Template PR68, the existing Core workspace-repair owner, and GitHub's open tier PR10. Inventory active callers and retain their owners. | Agree the extraction cutover, target authorization and preserved request journals; do not take over existing deliveries. |
| 2 | GitHub + Personal: extract the complete provider and its tests, retain Personal policy, retire superseded Personal read/create/address names and runtime imports. | Neutral provider selection, recurrence/retry/closure invariants and Windows/Linux checks pass. Stage this with callers as one coordinated cutover; no compatibility aliases. |
| 3 | Marketplace: register GitHub and actual dependencies in `plugins.json`, regenerate both catalogs. Agent: update its published snapshot, ordinary selection, target declarations and native projections. | Producer and catalogs publish before new selections activate; dependency graph and fresh native discovery pass. Include the actual employer-session selection in the authorized rollout without adding Personal. |
| 4 | Core: migrate resolver/default and active callers, add the narrow default record-plus-handoff rule and Session pointer. Template: reconcile `template:self-repair` with it and PR68. | Target routing, source authorization, safe payload and single-owner cases pass. Reuse relevant PR70 wording only through its source owner; the closed House stack is not a delivery prerequisite. |
| 5 | Reconcile Personal PR51 into the final Personal-policy boundary; migrate every active declaration, caller, generated registration and installed entry using the retired names. Agent PR55 keeps its adoption owner. | No active retired names or dual provider implementations remain; existing issue identities, histories and uncertain requests still reconcile. Coordinate publication/activation as one cutover despite separate source PRs. |
| 6 | Docs, Marketplace and GitHub ownership text: update Docs `PLUGINS.md` and `WORKFLOW_AGENT_STORE.md`, Marketplace README's provider table, GitHub README's retired WorkItems reference. Verify installed behavior on both hosts. | Docs describe the delivered boundaries; actual user entry points pass the acceptance matrix below. |

Do not land a temporarily broken provider removal while callers still depend on it. Prepare producer,
consumer and retirement changes together, publish the new producer before activating callers, and close
the retired registrations in the same rollout. Source readiness alone is not completed cutover evidence.

### Required verification

Run focused resolver/provider/ownership tests, generation and graph checks against the integrated source;
use exact-head CI for the supported Windows/Linux checks. Then refresh each actual supported installed
Claude/Codex entry point under the approved rollout and exercise these cases:

| Scenario | Observable acceptance |
| --- | --- |
| Personal task reports a Core defect | Correct Core record and one source repair owner; original task remains active. |
| Employer task reports the same Core/Template/Agent defect | Neutral provider resolves with Personal absent from selection and loaded policy; correct target record, safe source handoff, no employer tracker write. |
| Repeated callout, existing PR, live owner, or uncertain create | Reuse canonical identity/owner/request; no duplicate issue, POST or successor. |
| The owning work progresses, blocks, resumes or completes | Automatically reconcile its existing item and board from verified owner evidence; personal-project work updates that project's board, and tj-agents repairs update their source board regardless of where the defect surfaced. |
| Ordinary employer defect or `tj-agents/company` source | Existing employer lifecycle and consent apply; no automatic personal routing or scope change. |
| Agent skipped an adequate instruction | Correct the task and classify the observation honestly; no invented source repair. |
| Provider absent, offline, invalid, or declined; launcher uncertain | Preserve facts and exact retry/launch identity; continue unaffected original work; no alternate tracker, install, or duplicate writer. |
| Employer sentinel strings in source transcript/attachments/paths | They appear in neither emitted record nor successor prompt; independently constructed source fixture remains actionable. |
| Fresh installed lookup of all retired public names | No active retired names/aliases; current provider reads existing records and journals successfully. |

Use deterministic fixtures for mechanics. Installed proof requires the actual entry point and observed
record/handoff behavior, not just a version command, generated files or an assumed model interpretation.
Any controlled remote acceptance fixture and its cleanup need inclusion in the eventual approved scope.
No installed-host tests or new remote fixtures were run during this read-only investigation.

```agent-host-coverage
{
  "schema_version": 1,
  "shared_source": "core/.agents/workflow/resources/problem-records.md",
  "hosts": [
    {
      "host": "claude",
      "behavior": "resolve a neutral source-selected provider and route a safe tooling repair while preserving the employer task",
      "source": "core/.agents/workflow/policy/session/SKILL.md",
      "mapping": "generated Claude Workflow entry and hook delivery plus native GitHub provider discovery",
      "verification": {
        "level": "planned",
        "result": "pending",
        "evidence": "the installed acceptance matrix in this investigation; no installed execution claimed"
      }
    },
    {
      "host": "codex",
      "behavior": "resolve a neutral source-selected provider and route a safe tooling repair while preserving the employer task",
      "source": "core/.agents/workflow/policy/session/SKILL.md",
      "mapping": "generated Codex Workflow entry and hook delivery plus native GitHub provider discovery",
      "verification": {
        "level": "planned",
        "result": "pending",
        "evidence": "the installed acceptance matrix in this investigation; no installed execution claimed"
      }
    }
  ]
}
```

### Alternatives assessed

Assessment complete for this recommendation; reuse it unless new evidence changes the decision.

| Alternative | Why not recommend it |
| --- | --- |
| Keep the current resolver and load the Personal creator on demand | Locates the right target but still invokes a personal-only operation from work; does not fix scope, dispatch authority or installed availability. |
| Load Personal everywhere or allow selected Personal utilities through its tier | Couples global utility availability to a forbidden personal profile and makes admission depend on skill exceptions. |
| Put the GitHub backend in Agent or Core | Mixes tracker contents/operations with execution storage or common process; an existing neutral GitHub owner already supplies that capability boundary. |
| Restore Work Items or create another recorder/House plugin | Restores a retired identity or adds a package without a demonstrated need; the prior House migration was explicitly abandoned. |
| Add a separate tj-agents-only recorder beside Personal's implementation | Duplicates schemas, retry state and mutation paths, with two implementations to maintain. |
| Use raw `gh issue create` or fork another tracker | GitHub CLI already supplies transport, but neither action supplies this repository's provider selection, managed-record integrity, stable request reconciliation and repair-owner rules. Reuse the existing adapter and authenticated CLI instead. |
| Add a hook that infers bugs from conversation text and posts/launches them | Adds an unproven inference/enforcement mechanism. Existing Session guidance, resolver and handoff are the first implementation; verify their installed behavior before proposing more machinery. |

External primary reference checked: GitHub CLI's `gh issue create` manual
(`https://cli.github.com/manual/gh_issue_create`). It confirms explicit repository/body-file issue creation;
this assessment does not treat that transport as a replacement for the existing record protocol.

### Decision and next action

Recommend one personal-project work-management routine for all supported item kinds, using the existing
GitHub producer as its shared provider. Core Workflow resolves target scope and cross-scope routing;
Template owns tooling self-repair. Personal retains the personal-project defaults and permissions.
Tommy approved this design on 2026-11-02. Follow the execution checkpoint and Next Steps above;
preserve all source delivery and consumer installation boundaries.

## Completion checkpoint

```completion
{
  "outcome": "Original seven source drafts plus Personal65 guided-onboarding source are reviewed, pushed and configured CI green. PR65 is ready with current GitHub reviewDecision APPROVED. Actual grant/setup success, source merge/publication, board and installed acceptance remain incomplete.",
  "acceptance": [
    {
      "id": "source-candidates",
      "criterion": "All seven source candidates are committed and linked from the durable Story12 under Feature11",
      "evidence": [
        {
          "source": "https://github.com/tj-agents/github/issues/12",
          "result": "Story12 exists with native parent11; all seven draft PRs linked; exact refreshed heads verified by fetch and PR readback."
        }
      ],
      "owner": "Codex execution:a177e7d5-3411-449d-8ad9-b2b08e681815",
      "next_action": "Preserve the same execution, issues and request receipts."
    },
    {
      "id": "review-ci",
      "criterion": "Every final source head has completed native/source review and required exact-head CI",
      "evidence": [
        {
          "source": "https://github.com/tj-agents/github/issues/12",
          "result": "All seven exact-head source reviews and configured CI completed; full Template security range3d77fdbb..5f8399cc completed. Canonical receipts and descriptor-bound preflights pass. External acceptance/publication findings remain open."
        },
        {
          "source": "C:\\Users\\TommySeery\\AppData\\Local\\Temp\\projects-onboarding-preflight-20261103.json",
          "result": "Personal65 guided onboarding exactdb2280285b6d79873b5957237e246fba0886135d native/security synthesis, current source/security proof, frozen source checks and Windows/Linux/host CI41633986843 pass; actual missing-grant browser state captured."
        }
      ],
      "owner": "Codex execution:a177e7d5-3411-449d-8ad9-b2b08e681815",
      "next_action": "Preserve reviewed exact heads; finish external acceptance findings without re-running completed source reviews."
    },
    {
      "id": "publication",
      "criterion": "Human-approved coordinated producer/catalog and dependent-source publication completes without an active old provider",
      "evidence": [],
      "owner": "Existing Codex execution; exact-head human reviews observed",
      "next_action": "Preserve the eight observed approvals, resolve live App/board acceptance and source merge gates, then honor producer/catalog/consumer publication order."
    },
    {
      "id": "boards",
      "criterion": "Selected authenticated provider verifies issue and repository/owner board state for organization and user-owned personal projects",
      "evidence": [],
      "owner": "Existing Codex execution, already authorized by Tommy for the App permission change",
      "next_action": "Use the reviewed one-command wizard to save and accept the actual App/installation Projects grant, then verify live organization and user-owned board operations without human-token fallback."
    },
    {
      "id": "installed-routing",
      "criterion": "Actual supported Claude/Codex entry points pass the source-routing, single-owner, scope, recurrence and board-progress acceptance matrix",
      "evidence": [],
      "owner": "Codex execution:a177e7d5-3411-449d-8ad9-b2b08e681815",
      "next_action": "After publication and authorized personal tooling refresh, exercise the existing acceptance matrix with safe dummy observers. Do not install into employer repositories or claim source checks establish installed behavior."
    },
    {
      "id": "human-reviews",
      "criterion": "Tommy required PR approvals are observed and current GitHub review state is reconciled after any source repair.",
      "evidence": [
        {
          "source": "C:/Users/TommySeery/AppData/Local/Temp/shared-human-approvals-20261103.json",
          "result": "All eight authoritative PR reads contain tomjseery APPROVED at each recorded delivered head after Tommy's successful user_shell_command."
        },
        {
          "source": "C:\\Users\\TommySeery\\AppData\\Local\\Temp\\projects-onboarding-ready-pr-20261103.json",
          "result": "PR65 current headdb2280285b6d79873b5957237e246fba0886135d remains reviewDecision APPROVED after the same-PR repair and ready-for-review. The earlier human review stays attached to62b1; no new review at the current head is claimed."
        }
      ],
      "owner": "Existing Codex execution",
      "next_action": "Preserve observed approvals; do not repeat the approval request. Honor actual source merge policy and remaining operational gates."
    }
  ],
  "deliveries": [
    {
      "repository": "tj-agents/github",
      "pr": 13,
      "head": "cac5d8647df696db3485180acc42b3c03d5024ae"
    },
    {
      "repository": "tj-agents/personal",
      "pr": 61,
      "head": "989413b5eb4e194bd29c80396855ba2a74a952c5"
    },
    {
      "repository": "tj-agents/core",
      "pr": 545,
      "head": "1a221f25905560ad5875d506e16d4bd166ce6a01"
    },
    {
      "repository": "tj-agents/agent",
      "pr": 57,
      "head": "28f106bca1ab1afc12123b86808a571c02c278be"
    },
    {
      "repository": "tj-agents/template",
      "pr": 71,
      "head": "5f8399ccd861b4112fce0336b67754c3ef9d361f"
    },
    {
      "repository": "tj-agents/marketplace",
      "pr": 46,
      "head": "9666fe2e1ef52bc0dc881b7d6d7d89307b721429"
    },
    {
      "repository": "tj-agents/docs",
      "pr": 31,
      "head": "4232c77c4f6b242476eb7549bab5cee0d82c70ff"
    },
    {
      "repository": "tj-agents/personal",
      "pr": 65,
      "head": "db2280285b6d79873b5957237e246fba0886135d"
    }
  ],
  "open_tasks": [
    {
      "repository": "tj-agents/github",
      "pr": 13,
      "head": "cac5d8647df696db3485180acc42b3c03d5024ae",
      "text": "Resolve remaining board acceptance and coordinated publication gates",
      "owner": "Existing Codex execution; exact-head human review already observed",
      "next_action": "Complete the concrete guided setup action in Story12, verify actual App/install readiness and Projects boards, honor source merge policy/publication order, then qualify supported installed entry points."
    },
    {
      "repository": "tj-agents/personal",
      "pr": 61,
      "head": "989413b5eb4e194bd29c80396855ba2a74a952c5",
      "text": "Resolve remaining board acceptance and coordinated publication gates",
      "owner": "Existing Codex execution; exact-head human review already observed",
      "next_action": "Complete the concrete guided setup action in Story12, verify actual App/install readiness and Projects boards, honor source merge policy/publication order, then qualify supported installed entry points."
    },
    {
      "repository": "tj-agents/core",
      "pr": 545,
      "head": "1a221f25905560ad5875d506e16d4bd166ce6a01",
      "text": "Resolve remaining board acceptance and coordinated publication gates",
      "owner": "Existing Codex execution; exact-head human review already observed",
      "next_action": "Complete the concrete guided setup action in Story12, verify actual App/install readiness and Projects boards, honor source merge policy/publication order, then qualify supported installed entry points."
    },
    {
      "repository": "tj-agents/agent",
      "pr": 57,
      "head": "28f106bca1ab1afc12123b86808a571c02c278be",
      "text": "Resolve remaining board acceptance and coordinated publication gates",
      "owner": "Existing Codex execution; exact-head human review already observed",
      "next_action": "Complete the concrete guided setup action in Story12, verify actual App/install readiness and Projects boards, honor source merge policy/publication order, then qualify supported installed entry points."
    },
    {
      "repository": "tj-agents/template",
      "pr": 71,
      "head": "5f8399ccd861b4112fce0336b67754c3ef9d361f",
      "text": "Resolve remaining board acceptance and coordinated publication gates",
      "owner": "Existing Codex execution; exact-head human review already observed",
      "next_action": "Complete the concrete guided setup action in Story12, verify actual App/install readiness and Projects boards, honor source merge policy/publication order, then qualify supported installed entry points."
    },
    {
      "repository": "tj-agents/marketplace",
      "pr": 46,
      "head": "9666fe2e1ef52bc0dc881b7d6d7d89307b721429",
      "text": "Resolve remaining board acceptance and coordinated publication gates",
      "owner": "Existing Codex execution; exact-head human review already observed",
      "next_action": "Complete the concrete guided setup action in Story12, verify actual App/install readiness and Projects boards, honor source merge policy/publication order, then qualify supported installed entry points."
    },
    {
      "repository": "tj-agents/docs",
      "pr": 31,
      "head": "4232c77c4f6b242476eb7549bab5cee0d82c70ff",
      "text": "Resolve remaining board acceptance and coordinated publication gates",
      "owner": "Existing Codex execution; exact-head human review already observed",
      "next_action": "Complete the concrete guided setup action in Story12, verify actual App/install readiness and Projects boards, honor source merge policy/publication order, then qualify supported installed entry points."
    },
    {
      "repository": "tj-agents/personal",
      "pr": 65,
      "head": "db2280285b6d79873b5957237e246fba0886135d",
      "text": "Verify actual grant acceptance/successful setup and publish/qualify the reviewed guided-onboarding source.",
      "owner": "Existing Codex execution; Tommy performs the authenticated GitHub account steps in the local wizard.",
      "next_action": "Complete the concrete guided setup action in Story12, verify actual App/install readiness and Projects boards, honor source merge policy/publication order, then qualify supported installed entry points."
    }
  ]
}
```
