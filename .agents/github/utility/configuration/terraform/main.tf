locals {
  flag_rules = ["creation", "update", "deletion", "non_fast_forward", "required_linear_history", "required_signatures"]
  rule_types = concat(local.flag_rules, ["pull_request", "required_status_checks", "merge_queue"])

  rulesets = merge([
    for repository, settings in try(var.declaration.repositories, {}) : {
      for name, ruleset in try(settings.rulesets, {}) : "${repository}/${name}" => {
        repository = repository
        name       = name
        ruleset    = ruleset
        types      = [for rule in ruleset.rules : rule.type]
      }
    }
  ]...)
}

resource "github_repository_ruleset" "this" {
  for_each = local.rulesets

  repository  = each.value.repository
  name        = each.value.name
  target      = each.value.ruleset.target
  enforcement = each.value.ruleset.enforcement

  conditions {
    ref_name {
      include = each.value.ruleset.conditions.ref_name.include
      exclude = try(each.value.ruleset.conditions.ref_name.exclude, [])
    }
  }

  dynamic "bypass_actors" {
    for_each = try(each.value.ruleset.bypass_actors, [])
    content {
      actor_id    = try(bypass_actors.value.actor_id, null)
      actor_type  = bypass_actors.value.actor_type
      bypass_mode = bypass_actors.value.bypass_mode
    }
  }

  rules {
    creation                = contains(each.value.types, "creation") ? true : null
    update                  = contains(each.value.types, "update") ? true : null
    deletion                = contains(each.value.types, "deletion") ? true : null
    non_fast_forward        = contains(each.value.types, "non_fast_forward") ? true : null
    required_linear_history = contains(each.value.types, "required_linear_history") ? true : null
    required_signatures     = contains(each.value.types, "required_signatures") ? true : null

    dynamic "pull_request" {
      for_each = [for rule in each.value.ruleset.rules : try(rule.parameters, {}) if rule.type == "pull_request"]
      content {
        required_approving_review_count   = try(pull_request.value.required_approving_review_count, 0)
        dismiss_stale_reviews_on_push     = try(pull_request.value.dismiss_stale_reviews_on_push, false)
        require_code_owner_review         = try(pull_request.value.require_code_owner_review, false)
        require_last_push_approval        = try(pull_request.value.require_last_push_approval, false)
        required_review_thread_resolution = try(pull_request.value.required_review_thread_resolution, false)
      }
    }

    dynamic "merge_queue" {
      for_each = [for rule in each.value.ruleset.rules : rule.parameters if rule.type == "merge_queue"]
      content {
        check_response_timeout_minutes    = merge_queue.value.check_response_timeout_minutes
        grouping_strategy                 = merge_queue.value.grouping_strategy
        max_entries_to_build              = merge_queue.value.max_entries_to_build
        max_entries_to_merge              = merge_queue.value.max_entries_to_merge
        merge_method                      = merge_queue.value.merge_method
        min_entries_to_merge              = merge_queue.value.min_entries_to_merge
        min_entries_to_merge_wait_minutes = merge_queue.value.min_entries_to_merge_wait_minutes
      }
    }

    dynamic "required_status_checks" {
      for_each = [for rule in each.value.ruleset.rules : rule.parameters if rule.type == "required_status_checks"]
      content {
        strict_required_status_checks_policy = try(required_status_checks.value.strict_required_status_checks_policy, false)

        dynamic "required_check" {
          for_each = required_status_checks.value.required_status_checks
          content {
            context        = required_check.value.context
            integration_id = try(required_check.value.integration_id, null)
          }
        }
      }
    }
  }

  lifecycle {
    precondition {
      condition     = alltrue([for type in each.value.types : contains(local.rule_types, type)])
      error_message = "${each.key} uses a rule type this module does not converge; supported: ${join(", ", local.rule_types)}."
    }
  }
}
