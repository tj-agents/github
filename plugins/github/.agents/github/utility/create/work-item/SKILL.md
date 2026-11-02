---
name: create-work-item
description: Classify and record a GitHub work item or append recurrence evidence to its existing identity without starting implementation. Use when capturing a bug, mistake, debt, feature, story or task through the selected GitHub provider.
kind: utility
domain: github
profile: core
applicability: repositories selecting the GitHub work-item provider
requires: engineering, python
provenance: house
---

# Create a GitHub work item

This is the recording intent: classify and create one record, or append material evidence to its
existing identity. The affected source repository supplies the recording policy and provider selection.
Resolve both from its primary checkout before writing; the observation's checkout does not select the
destination. Never adopt, configure or replace a provider while recording.

Classify supplied facts once: faulty behavior is `bug`; an observed agent or process error is
`mistake`; a deliberate deferral is `debt`; changed product behavior is `feature`; concrete work with a
completion condition is `task`; a user-centered outcome is `story`. Read an identified existing item
before classifying it. Reconcile repeated observations with their canonical item and keep its identity
and repair owner.

Use the shipped template, validation and preview commands to prepare facts, then create with one stable
request ID:

```text
python -B <skill-directory>/scripts/create_work_item.py template --kind story
python -B <skill-directory>/scripts/create_work_item.py validate --input facts.json
python -B <skill-directory>/scripts/create_work_item.py preview --input facts.json
python -B <skill-directory>/scripts/create_work_item.py create --root <source-root> --input facts.json --request-id UNIQUE-STABLE-ID
```

Creation places the issue on the repository and owner boards. Verify the persisted issue, board entries
and initial state before reporting success. Report `boards.error` as an incomplete board operation and
reconcile the existing issue with the same request ID; do not create another issue to retry placement.

For an existing item, append material new recurrence facts, the known cause and current repair state,
then read the comments back through `github:work-item` to verify persistence:

```text
python -B <skill-directory>/scripts/create_work_item.py note --root <source-root> --item owner/repository#42 --request-id UNIQUE-STABLE-ID --text-file recurrence.txt
```

Preserve managed body markers and request receipts. Reuse the same request ID and facts after an
uncertain response; reconcile with the same command and `--reconcile-only` before another submission.
Recording does not start implementation. `github:address-work-item` reconciles an authorized addressing
request with its existing owner and maintains execution progress.
