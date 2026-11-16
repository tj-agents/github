data "github_repositories" "active" {
  query = "org:${var.owner} archived:false"
}

data "github_organization" "owner" {
  name         = var.owner
  summary_only = true
}

data "github_repository" "eligibility" {
  for_each = var.repositories

  full_name = "${var.owner}/${each.key}"
}

locals {
  auto_merge_required = {
    for name, repo in data.github_repository.eligibility :
    name => !(lower(data.github_organization.owner.plan) == "free" && repo.private)
  }
}

resource "terraform_data" "auto_merge_inventory" {
  input = var.repositories

  lifecycle {
    precondition {
      condition     = toset(data.github_repositories.active.names) == var.repositories
      error_message = "The active repository inventory changed; reconcile auto_merge_repositories before applying."
    }
    precondition {
      condition     = trimspace(data.github_organization.owner.plan) != ""
      error_message = "The organization plan could not be determined; auto-merge eligibility is unknown."
    }
  }
}

resource "github_repository" "auto_merge" {
  for_each = var.repositories

  name             = each.key
  allow_auto_merge = local.auto_merge_required[each.key]
  depends_on       = [terraform_data.auto_merge_inventory]

  lifecycle {
    prevent_destroy = true
    ignore_changes = [
      allow_forking,
      allow_merge_commit,
      allow_rebase_merge,
      allow_squash_merge,
      allow_update_branch,
      archive_on_destroy,
      archived,
      auto_init,
      default_branch,
      delete_branch_on_merge,
      description,
      etag,
      fork,
      gitignore_template,
      has_discussions,
      has_downloads,
      has_issues,
      has_projects,
      has_wiki,
      homepage_url,
      ignore_vulnerability_alerts_during_read,
      is_template,
      license_template,
      merge_commit_message,
      merge_commit_title,
      pages,
      private,
      security_and_analysis,
      source_owner,
      source_repo,
      squash_merge_commit_message,
      squash_merge_commit_title,
      template,
      topics,
      visibility,
      vulnerability_alerts,
      web_commit_signoff_required,
    ]
    postcondition {
      condition     = self.allow_auto_merge == local.auto_merge_required[each.key]
      error_message = "Auto-merge does not match GitHub plan eligibility for ${each.key}."
    }
  }
}

data "github_repository" "auto_merge_verified" {
  for_each = var.repositories

  full_name  = "${var.owner}/${each.key}"
  depends_on = [github_repository.auto_merge]

  lifecycle {
    postcondition {
      condition     = self.allow_auto_merge == local.auto_merge_required[each.key]
      error_message = "Auto-merge does not match GitHub plan eligibility for ${self.full_name}."
    }
  }
}
