# Each declared repository's merge policy: the merge section of its own .agents/repository.json or, when
# that file has no merge section, the merge section of the owner default in the <owner>/.github
# repository. A repository's merge section replaces the default's entirely; its other sections do not
# affect it. GitHub reporting a file missing (404) means absent; any other read failure fails the plan.
locals {
  configuration_file     = ".agents/repository.json"
  configuration_keys     = ["schema_version", "access", "merge", "credentials"]
  merge_policy_ruleset   = "merge-policy-review"
  merge_policy_fields    = ["approval", "merge", "default_branch_push", "required_checks", "review_check"]
  owner_configuration_at = ".github/${local.configuration_file}"

  # A document that is not JSON decodes to the string "invalid", which no check accepts.
  owner_configuration = (
    data.github_repository_file.owner_configuration.content == null ? null
    : try(jsondecode(data.github_repository_file.owner_configuration.content), "invalid")
  )

  repository_configurations = {
    for repository, file in data.github_repository_file.configuration :
    repository => try(jsondecode(file.content), "invalid") if file.content != null
  }

  # Each configuration is an object with schema_version 1 and only the known sections.
  configuration_validity = {
    for name, document in local.repository_configurations : name => alltrue([
      try(document.schema_version == 1, false),
      try(length(setsubtract(keys(document), local.configuration_keys)) == 0, false),
    ])
  }
  owner_configuration_valid = alltrue([
    try(local.owner_configuration.schema_version == 1, false),
    try(length(setsubtract(keys(local.owner_configuration), local.configuration_keys)) == 0, false),
  ])

  invalid_configurations = sort(concat(
    [for name, valid in local.configuration_validity : name if !valid],
    local.owner_configuration != null && !local.owner_configuration_valid ? [local.owner_configuration_at] : [],
  ))

  merge_policies = {
    for repository in keys(data.github_repository_file.configuration) :
    repository => try(local.repository_configurations[repository].merge, local.owner_configuration.merge)
    if can(local.repository_configurations[repository].merge) || can(local.owner_configuration.merge)
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
    }
    if !contains(local.invalid_configurations, repository)
    && !contains(local.invalid_merge_policies, repository)
    && try(policy.approval != "none", false)
  }
}

data "github_repository_file" "owner_configuration" {
  repository = ".github"
  file       = local.configuration_file
}

data "github_repository_file" "configuration" {
  for_each = try(var.declaration.repositories, {})

  repository = each.key
  file       = local.configuration_file
}
