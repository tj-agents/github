mock_provider "github" {}
mock_provider "infisical" {}

variables {
  declaration = {
    owner = "owner"
    repositories = {
      human     = {}
      automated = {}
      none      = {}
      deploy    = {}
      defaulted = {}
      access    = {}
    }
  }
}

override_data {
  target = data.github_repository_file.owner_configuration
  values = { content = null }
}

override_data {
  target = data.github_repository_file.configuration["human"]
  values = { content = "{\"schema_version\": 1, \"merge\": {\"approval\": \"human\", \"merge\": \"human\", \"default_branch_push\": \"none\", \"required_checks\": [\"ci-required\"]}}" }
}

override_data {
  target = data.github_repository_file.configuration["automated"]
  values = { content = "{\"schema_version\": 1, \"merge\": {\"approval\": \"automated\", \"merge\": \"agents\", \"default_branch_push\": \"none\", \"review_check\": \"agent-review\"}}" }
}

override_data {
  target = data.github_repository_file.configuration["none"]
  values = { content = "{\"schema_version\": 1, \"merge\": {\"approval\": \"none\", \"merge\": \"agents\", \"default_branch_push\": \"none\"}}" }
}

override_data {
  target = data.github_repository_file.configuration["deploy"]
  values = { content = "{\"schema_version\": 1, \"merge\": {\"approval\": \"human\", \"merge\": \"agents\", \"default_branch_push\": \"ci\"}}" }
}

override_data {
  target = data.github_repository_file.configuration["defaulted"]
  values = { content = null }
}

override_data {
  target = data.github_repository_file.configuration["access"]
  values = { content = "{\"schema_version\": 1, \"access\": {\"method\": \"app\", \"app\": \"sample-bot\"}, \"credentials\": []}" }
}

run "approval_kinds_map_to_the_generated_ruleset" {
  command = plan

  assert {
    condition     = github_repository_ruleset.this["human/merge-policy-review"].rules[0].pull_request[0].required_approving_review_count == 1
    error_message = "approval human must require one approval."
  }

  assert {
    condition     = length(github_repository_ruleset.this["human/merge-policy-review"].bypass_actors) == 0
    error_message = "No actor may bypass a human approval unless deploy keys may push."
  }

  assert {
    condition     = one(github_repository_ruleset.this["automated/merge-policy-review"].rules[0].required_status_checks[0].required_check).context == "agent-review"
    error_message = "approval automated must require its review check."
  }

  assert {
    condition     = length(github_repository_ruleset.this["automated/merge-policy-review"].rules[0].pull_request) == 0
    error_message = "approval automated must not require an approval."
  }

  assert {
    condition     = !contains(keys(github_repository_ruleset.this), "none/merge-policy-review")
    error_message = "approval none generates no ruleset."
  }

  assert {
    condition     = !contains(keys(github_repository_ruleset.this), "defaulted/merge-policy-review")
    error_message = "Without a repository merge section or an owner default nothing is generated."
  }

  assert {
    condition     = !contains(keys(github_repository_ruleset.this), "access/merge-policy-review")
    error_message = "Other sections must not generate a ruleset."
  }
}

run "ci_default_branch_push_lets_deploy_keys_bypass" {
  command = plan

  assert {
    condition     = one(github_repository_ruleset.this["deploy/merge-policy-review"].bypass_actors).actor_type == "DeployKey"
    error_message = "default_branch_push ci must let deploy keys bypass the generated ruleset."
  }
}

run "the_owner_default_applies_where_a_repository_has_no_merge_section" {
  command = plan

  override_data {
    target = data.github_repository_file.owner_configuration
    values = { content = "{\"schema_version\": 1, \"access\": {\"method\": \"app\", \"app\": \"sample-bot\"}, \"merge\": {\"approval\": \"human\", \"merge\": \"agents\", \"default_branch_push\": \"none\"}}" }
  }

  assert {
    condition     = github_repository_ruleset.this["defaulted/merge-policy-review"].rules[0].pull_request[0].required_approving_review_count == 1
    error_message = "A repository without a configuration must take the owner default."
  }

  assert {
    condition     = github_repository_ruleset.this["access/merge-policy-review"].rules[0].pull_request[0].required_approving_review_count == 1
    error_message = "A configuration without a merge section must take the owner default's."
  }

  assert {
    condition     = !contains(keys(github_repository_ruleset.this), "none/merge-policy-review")
    error_message = "A repository merge section must replace the owner default entirely."
  }
}

run "an_owner_default_without_a_merge_section_generates_nothing" {
  command = plan

  override_data {
    target = data.github_repository_file.owner_configuration
    values = { content = "{\"schema_version\": 1, \"access\": {\"method\": \"app\", \"app\": \"sample-bot\"}}" }
  }

  assert {
    condition     = !contains(keys(github_repository_ruleset.this), "defaulted/merge-policy-review")
    error_message = "An owner default without a merge section generates nothing."
  }
}

run "the_retired_merge_policy_fields_are_rejected" {
  command = plan

  override_data {
    target = data.github_repository_file.configuration["none"]
    values = { content = "{\"schema_version\": 1, \"merge\": {\"requires_approval\": true}}" }
  }

  expect_failures = [output.merge_policies]
}

run "review_check_requires_approval_automated" {
  command = plan

  override_data {
    target = data.github_repository_file.configuration["none"]
    values = { content = "{\"schema_version\": 1, \"merge\": {\"approval\": \"none\", \"merge\": \"agents\", \"default_branch_push\": \"none\", \"review_check\": \"agent-review\"}}" }
  }

  expect_failures = [output.merge_policies]
}

run "an_unknown_kind_is_rejected" {
  command = plan

  override_data {
    target = data.github_repository_file.configuration["none"]
    values = { content = "{\"schema_version\": 1, \"merge\": {\"approval\": \"none\", \"merge\": \"bots\", \"default_branch_push\": \"none\"}}" }
  }

  expect_failures = [output.merge_policies]
}

run "a_configuration_without_schema_version_1_is_rejected" {
  command = plan

  override_data {
    target = data.github_repository_file.configuration["none"]
    values = { content = "{\"merge\": {\"approval\": \"none\", \"merge\": \"agents\", \"default_branch_push\": \"none\"}}" }
  }

  expect_failures = [output.merge_policies]
}

run "an_unknown_section_is_rejected" {
  command = plan

  override_data {
    target = data.github_repository_file.configuration["access"]
    values = { content = "{\"schema_version\": 1, \"method\": \"app\", \"app\": \"sample-bot\"}" }
  }

  expect_failures = [output.merge_policies]
}

run "an_invalid_owner_default_is_rejected" {
  command = plan

  override_data {
    target = data.github_repository_file.owner_configuration
    values = { content = "{\"approval\": \"none\", \"merge\": \"agents\", \"default_branch_push\": \"none\"}" }
  }

  expect_failures = [output.merge_policies]
}

run "a_configuration_that_is_not_json_is_rejected" {
  command = plan

  override_data {
    target = data.github_repository_file.configuration["none"]
    values = { content = "{" }
  }

  expect_failures = [output.merge_policies]
}

run "a_declared_ruleset_cannot_take_the_generated_name" {
  command = plan

  variables {
    declaration = {
      owner = "owner"
      repositories = {
        human = {
          rulesets = {
            "merge-policy-review" = {
              target        = "branch"
              enforcement   = "active"
              conditions    = { ref_name = { include = ["~DEFAULT_BRANCH"], exclude = [] } }
              bypass_actors = []
              rules         = [{ type = "deletion" }]
            }
          }
        }
      }
    }
  }

  expect_failures = [github_repository_ruleset.this]
}
