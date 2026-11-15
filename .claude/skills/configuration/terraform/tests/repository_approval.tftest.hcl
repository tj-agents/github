mock_provider "github" {}
mock_provider "infisical" {}

variables {
  declaration = {
    owner     = "owner"
    repositories = {
      gated = {
        requires_approval = true
        rulesets = {
          "merge-policy" = {
            target        = "branch"
            enforcement   = "active"
            conditions    = { ref_name = { include = ["~DEFAULT_BRANCH"], exclude = [] } }
            bypass_actors = [{ actor_id = 5, actor_type = "RepositoryRole", bypass_mode = "pull_request" }]
            rules         = [{ type = "deletion" }]
          }
        }
      }
      ungated = {
        requires_approval = false
        rulesets = {
          "merge-policy" = {
            target        = "branch"
            enforcement   = "active"
            conditions    = { ref_name = { include = ["~DEFAULT_BRANCH"], exclude = [] } }
            bypass_actors = [{ actor_id = 5, actor_type = "RepositoryRole", bypass_mode = "pull_request" }]
            rules         = [{ type = "deletion" }]
          }
        }
      }
    }
  }
}

run "approval_is_opt_in_and_has_no_bypass" {
  command = plan

  assert {
    condition     = github_repository_ruleset.this["gated/merge-policy-review"].rules[0].pull_request[0].required_approving_review_count == 1
    error_message = "A covered repository must require one approval."
  }

  assert {
    condition     = length(github_repository_ruleset.this["gated/merge-policy-review"].bypass_actors) == 0
    error_message = "No actor may bypass an explicitly enabled approval."
  }

  assert {
    condition     = !contains(keys(github_repository_ruleset.this), "ungated/merge-policy-review")
    error_message = "An excepted repository must not be gated."
  }
}

run "without_approval_pull_requests_merge_on_green" {
  command = plan

  variables {
    declaration = {
      owner     = "owner"
      repositories = {
        gated = {
          rulesets = {
            "merge-policy" = {
              target        = "branch"
              enforcement   = "active"
              conditions    = { ref_name = { include = ["~DEFAULT_BRANCH"], exclude = [] } }
              bypass_actors = [{ actor_id = 5, actor_type = "RepositoryRole", bypass_mode = "pull_request" }]
              rules         = [{ type = "deletion" }]
            }
          }
        }
      }
    }
  }

  assert {
    condition     = keys(github_repository_ruleset.this) == ["gated/merge-policy"]
    error_message = "Without an approval opt-in only the declared merge policy remains."
  }
}
