---
name: address-work-item
description: Reconcile an existing GitHub work item and enter Workflow execution with its mapped Engineering operation. Use when authorized to address, fix or deliver an identified GitHub item.
kind: utility
domain: github
profile: core
applicability: repositories selecting the GitHub work-item provider
requires: engineering, python
provenance: house
---

# Address a GitHub work item

Select authorized execution for an existing record through its Workflow goal and Engineering operation.
Read the item
through `github:work-item`; verify its source primary checkout, selected provider, canonical identity,
managed kind and current facts. Apply the source's policy and user authority. Reject a foreign identity,
another provider, an unmanaged body or evidence outside the source's permitted scope.

Reconcile the existing repair owner before delivery. `workflow:run` reuses that owner; for an authorized
unowned item it acquires the sole whole-goal owner. Reading or recording alone never enters execution.
Map the persisted kind to one bounded Engineering operation: `bug` to `engineering:address-bug`;
`mistake` to `engineering:address-mistake`; `debt` to `engineering:address-debt`; and `feature`, `story`
or `task` to `engineering:feature`. Enter `workflow:run` with the same canonical identity and operation.
Engineering supplies delivery and verification evidence; Workflow retains the goal and its owner.

```text
github:address-work-item owner/repository#42
1. github:work-item confirms the selected source, managed story, open state and current ownership.
2. Enter workflow:run for that identity with engineering:feature, reusing or acquiring its sole owner.
3. Maintain factual issue and board progress under source policy; close after verified acceptance.
```

Use this skill's helper for factual updates. Preserve the persisted kind and managed body markers; read
and reconcile changed content before updating with its current body hash:

```text
python -B <skill-directory>/scripts/address_work_item.py update --root <source-root> --item owner/repository#42 --input facts.json --expected-hash HASH
```

Apply the source's progress policy as the work actually changes state. Keep factual evidence of starting,
blocking, handoff, review and completion in the item. Resolve the exact existing `Status` option name on
both boards before a transition; do not assume `Blocked` or `Review` exists or create new options to
force a mapping. An unavailable option is an actionable board failure, while the factual note retains
the actual work state. Board status does not establish ownership or acceptance.

For boards whose existing option is `In Progress`, the transition is:

```text
python -B <skill-directory>/scripts/address_work_item.py progress --root <source-root> --item owner/repository#42 --status "In Progress" --text-file progress.txt --request-id PROGRESS-STABLE-ID
```

Verify the factual note and both board entries remotely before claiming progress persisted. Retain the
request ID and identical facts after an uncertain response; reconcile with the same command and
`--reconcile-only` before another submission. Report partial board failure against the existing issue.

Close only after the item's acceptance condition is satisfied and verification evidence is recorded:

```text
python -B <skill-directory>/scripts/address_work_item.py close --root <source-root> --item owner/repository#42 --text-file verification.txt --request-id CLOSE-STABLE-ID
```

Closure records the evidence, closes and reads back the issue, then marks both boards `Done` and verifies
them. `Done` requires a closed, accepted item. A board failure leaves board completion unverified;
reconcile closure on the same issue with the same request ID. An intermediate commit, open PR or handoff
alone does not establish acceptance, and no progress failure authorizes duplicate item creation.
