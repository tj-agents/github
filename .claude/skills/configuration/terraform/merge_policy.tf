# Each declared repository's merge policy: its own .github/merge-policy.json or, when it has none, the
# owner default in the <owner>/.github repository. A repository file replaces the default entirely. GitHub
# reporting a file missing (404) means absent; any other read failure fails the plan.
locals {
  merge_policy_file    = ".github/merge-policy.json"
  merge_policy_ruleset = "merge-policy-review"
  merge_policy_fields  = ["approval", "merge", "default_branch_push", "required_checks", "review_check"]

  owner_merge_policy = data.github_repository_file.owner_merge_policy.content

  merge_policies = {
    for repository, file in data.github_repository_file.merge_policy :
    repository => try(jsondecode(file.content != null ? file.content : local.owner_merge_policy), null)
    if file.content != null || local.owner_merge_policy != null
  }

  invalid_merge_policies = sort([
    for repository, policy in local.merge_policies : repository if !alltrue([
      try(length(setsubtract(keys(policy), local.merge_policy_fields)) == 0, false),
      try(contains(["human", "automated", "none"], policy.approval), false),
      try(contains(["agents", "human"], policy.merge), false),
      try(contains(["none", "ci"], policy.default_branch_push), false),
      try(!can(policy.required_checks) || alltrue([for check in tolist(policy.required_checks) : length(trimspace(check)) > 0]), false),
      try(policy.approval == "automated" ? length(trimspace(policy.review_check)) > 0 : !can(policy.review_check), false),
    ])
  ])

  # approval human requires one approval and automated its review check; none generates no ruleset.
  # default_branch_push ci lets deploy keys bypass it, as they bypass the declared merge-policy ruleset.
  # merge is an agent permission in the repository's harness overlay, not a ruleset rule.
  merge_policy_rulesets = {
    for repository, policy in local.merge_policies : "${repository}/${local.merge_policy_ruleset}" => {
      repository = repository
      name       = local.merge_policy_ruleset
      types      = policy.approval == "human" ? ["pull_request"] : ["required_status_checks"]
      ruleset = {
        target        = "branch"
        enforcement   = "active"
        conditions    = { ref_name = { include = ["~DEFAULT_BRANCH"], exclude = [] } }
        bypass_actors = policy.default_branch_push == "ci" ? [{ actor_type = "DeployKey", bypass_mode = "always" }] : []
        rules = concat(
          [for approval in [policy.approval] : {
            type       = "pull_request"
            parameters = { required_approving_review_count = 1 }
          } if approval == "human"],
          [for approval in [policy.approval] : {
            type       = "required_status_checks"
            parameters = { required_status_checks = [{ context = policy.review_check }] }
          } if approval == "automated"],
        )
      }
    } if !contains(local.invalid_merge_policies, repository) && try(policy.approval != "none", false)
  }
}

data "github_repository_file" "owner_merge_policy" {
  repository = ".github"
  file       = local.merge_policy_file
}

data "github_repository_file" "merge_policy" {
  for_each = try(var.declaration.repositories, {})

  repository = each.key
  file       = local.merge_policy_file
}
