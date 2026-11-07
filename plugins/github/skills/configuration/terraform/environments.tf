locals {
  environments = merge([
    for repository, settings in try(var.declaration.repositories, {}) : {
      for name, environment in try(settings.environments, {}) : "${repository}/${name}" => {
        repository = repository
        name       = name
        branches   = try(environment.deployment_branches, [])
        secrets    = try(environment.secrets, {})
      }
    }
  ]...)

  deployment_branches = merge([
    for key, environment in local.environments : {
      for branch in environment.branches : "${key}/${branch}" => {
        environment = key
        branch      = branch
      }
    }
  ]...)

  environment_secrets = merge([
    for key, environment in local.environments : {
      for name, source in environment.secrets : "${key}/${name}" => {
        environment = key
        name        = name
        source      = source
      }
    }
  ]...)
}

resource "github_repository_environment" "this" {
  for_each = local.environments

  repository  = each.value.repository
  environment = each.value.name

  dynamic "deployment_branch_policy" {
    for_each = length(each.value.branches) > 0 ? [true] : []
    content {
      protected_branches     = false
      custom_branch_policies = true
    }
  }
}

resource "github_repository_environment_deployment_policy" "this" {
  for_each = local.deployment_branches

  repository     = github_repository_environment.this[each.value.environment].repository
  environment    = github_repository_environment.this[each.value.environment].environment
  branch_pattern = each.value.branch
}

resource "github_actions_environment_secret" "this" {
  for_each = local.environment_secrets

  repository  = github_repository_environment.this[each.value.environment].repository
  environment = github_repository_environment.this[each.value.environment].environment
  secret_name = each.value.name
  value       = var.secret_values[each.value.source]

  lifecycle {
    precondition {
      condition     = contains(keys(var.secret_values), each.value.source)
      error_message = "${each.key} reads secret source ${each.value.source}, which the workspace does not supply."
    }
  }
}
