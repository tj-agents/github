mock_provider "github" {
  override_during = plan
  mock_data "github_repository" {
    defaults = { allow_auto_merge = true, private = false }
  }
  mock_data "github_organization" {
    defaults = { plan = "free" }
  }
}

variables {
  owner        = "sample"
  repositories = ["agent", "cli"]
}

run "all_repositories_enable_auto_merge" {
  command = plan

  assert {
    condition     = alltrue([for repo in github_repository.auto_merge : repo.allow_auto_merge])
    error_message = "Every declared repository must enable auto-merge."
  }
}

run "free_plan_private_repository_is_ineligible" {
  command = apply

  override_resource {
    target = github_repository.auto_merge
  }

  override_data {
    target = data.github_repository.eligibility["agent"]
    values = { private = true }
  }

  override_data {
    target = data.github_repository.auto_merge_verified["agent"]
    values = { private = true, allow_auto_merge = false }
  }

  assert {
    condition     = !github_repository.auto_merge["agent"].allow_auto_merge && github_repository.auto_merge["cli"].allow_auto_merge
    error_message = "GitHub Free private repositories are ineligible; public repositories remain enabled."
  }
}

run "paid_plan_private_repository_is_eligible" {
  command = plan

  override_data {
    target = data.github_organization.owner
    values = { plan = "team" }
  }

  override_data {
    target = data.github_repository.eligibility["agent"]
    values = { private = true }
  }

  assert {
    condition     = github_repository.auto_merge["agent"].allow_auto_merge
    error_message = "A private repository on a paid plan must enable auto-merge."
  }
}

run "unknown_plan_fails_closed" {
  command = plan

  override_data {
    target = data.github_organization.owner
    values = { plan = "" }
  }

  expect_failures = [terraform_data.auto_merge_inventory]
}

run "disabled_repository_fails_verification" {
  command = apply

  override_resource {
    target = github_repository.auto_merge
  }

  override_data {
    target = data.github_repository.auto_merge_verified["agent"]
    values = { allow_auto_merge = false }
  }

  expect_failures = [data.github_repository.auto_merge_verified["agent"]]
}
