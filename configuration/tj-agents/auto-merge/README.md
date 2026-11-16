# Repository auto-merge

The `tj-agents` configuration enables auto-merge on every eligible active repository. On GitHub Free,
private repositories are ineligible and retain `allow_auto_merge = false`; they can merge directly once
their required checks and any configured review requirements pass. Public repositories remain enabled.
If the organization moves to a plan that supports private auto-merge, the next apply enables it there too.

The configuration fails if the organization plan is unknown, the repository inventory changes, or the
applied setting does not match eligibility. Auto-merge does not bypass a repository's merge rules.

GitHub documents [auto-merge availability by plan](https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/configuring-pull-request-merges/managing-auto-merge-for-pull-requests-in-your-repository).
