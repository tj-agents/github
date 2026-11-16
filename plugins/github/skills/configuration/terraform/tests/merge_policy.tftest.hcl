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
    }
  }
}

override_data {
  target = data.github_repository_file.owner_merge_policy
  values = { content = null }
}

override_data {
  target = data.github_repository_file.merge_policy["human"]
  values = { content = "{\"approval\": \"human\", \"merge\": \"human\", \"default_branch_push\": \"none\", \"required_checks\": [\"ci-required\"]}" }
}

override_data {
  target = data.github_repository_file.merge_policy["automated"]
  values = { content = "{\"approval\": \"automated\", \"merge\": \"agents\", \"default_branch_push\": \"none\", \"review_check\": \"agent-review\"}" }
}

override_data {
  target = data.github_repository_file.merge_policy["none"]
  values = { content = "{\"approval\": \"none\", \"merge\": \"agents\", \"default_branch_push\": \"none\"}" }
}

override_data {
  target = data.github_repository_file.merge_policy["deploy"]
  values = { content = "{\"approval\": \"human\", \"merge\": \"agents\", \"default_branch_push\": \"ci\"}" }
}

override_data {
  target = data.github_repository_file.merge_policy["defaulted"]
  values = { content = null }
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
    error_message = "Without a repository file or an owner default nothing is generated."
  }
}

run "ci_default_branch_push_lets_deploy_keys_bypass" {
  command = plan

  assert {
    condition     = one(github_repository_ruleset.this["deploy/merge-policy-review"].bypass_actors).actor_type == "DeployKey"
    error_message = "default_branch_push ci must let deploy keys bypass the generated ruleset."
  }
}

run "owner_default_applies_only_without_a_repository_file" {
  command = plan

  override_data {
    target = data.github_repository_file.owner_merge_policy
    values = { content = "{\"approval\": \"human\", \"merge\": \"agents\", \"default_branch_push\": \"none\"}" }
  }

  assert {
    condition     = github_repository_ruleset.this["defaulted/merge-policy-review"].rules[0].pull_request[0].required_approving_review_count == 1
    error_message = "A repository without a file must take the owner default."
  }

  assert {
    condition     = !contains(keys(github_repository_ruleset.this), "none/merge-policy-review")
    error_message = "A repository file must replace the owner default entirely."
  }
}

run "the_replaced_boolean_fields_are_rejected" {
  command = plan

  override_data {
    target = data.github_repository_file.merge_policy["none"]
    values = { content = "{\"requires_approval\": true}" }
  }

  expect_failures = [output.merge_policies]
}

run "review_check_requires_approval_automated" {
  command = plan

  override_data {
    target = data.github_repository_file.merge_policy["none"]
    values = { content = "{\"approval\": \"none\", \"merge\": \"agents\", \"default_branch_push\": \"none\", \"review_check\": \"agent-review\"}" }
  }

  expect_failures = [output.merge_policies]
}

run "an_unknown_kind_is_rejected" {
  command = plan

  override_data {
    target = data.github_repository_file.merge_policy["none"]
    values = { content = "{\"approval\": \"none\", \"merge\": \"bots\", \"default_branch_push\": \"none\"}" }
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
