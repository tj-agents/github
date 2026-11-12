mock_provider "github" {}
mock_provider "infisical" {}

variables {
  declaration = {
    owner     = "owner"
    approvals = { required = true, except = ["ungated"] }
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
      ungated = {
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

run "approvals_gate_covered_repositories_and_exempt_renovate" {
  command = plan

  assert {
    condition     = github_repository_ruleset.this["gated/merge-policy-review"].rules[0].pull_request[0].required_approving_review_count == 1
    error_message = "A covered repository must require one approval."
  }

  assert {
    condition = anytrue([
      for actor in github_repository_ruleset.this["gated/merge-policy-review"].bypass_actors :
      actor.actor_id == 2740 && actor.actor_type == "Integration" && actor.bypass_mode == "pull_request"
    ])
    error_message = "Renovate must never wait for an approval."
  }

  assert {
    condition = anytrue([
      for actor in github_repository_ruleset.this["gated/merge-policy-review"].bypass_actors :
      actor.actor_id == 5 && actor.actor_type == "RepositoryRole"
    ])
    error_message = "Actors who may bypass the merge policy may also bypass approval."
  }

  assert {
    condition     = !contains(keys(github_repository_ruleset.this), "ungated/merge-policy-review")
    error_message = "An excepted repository must not be gated."
  }
}

run "approvals_off_generates_no_review_rulesets" {
  command = plan

  variables {
    declaration = {
      owner     = "owner"
      approvals = { required = false }
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
    error_message = "With approvals off only the declared merge policy remains."
  }
}
