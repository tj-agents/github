mock_provider "github" {
  override_during = plan
  mock_data "github_repositories" {
    defaults = {
      names = ["agent", "cli"]
    }
  }
  mock_data "github_repository" {
    defaults = { allow_auto_merge = true }
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

run "missing_repository_fails_inventory" {
  command = plan

  override_data {
    target = data.github_repositories.active
    values = { names = ["agent"] }
  }

  expect_failures = [terraform_data.auto_merge_inventory]
}
