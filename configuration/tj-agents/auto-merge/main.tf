data "github_repositories" "active" {
  query = "org:${var.owner} archived:false"
}

resource "terraform_data" "auto_merge_inventory" {
  input = var.repositories

  lifecycle {
    precondition {
      condition     = toset(data.github_repositories.active.names) == var.repositories
      error_message = "The active repository inventory changed; reconcile auto_merge_repositories before applying."
    }
  }
}

resource "github_repository" "auto_merge" {
  for_each = var.repositories

  name             = each.key
  allow_auto_merge = true

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
      condition     = self.allow_auto_merge
      error_message = "Auto-merge must be enabled for every declared repository."
    }
  }
}

data "github_repository" "auto_merge_verified" {
  for_each = var.repositories

  full_name  = "${var.owner}/${each.key}"
  depends_on = [github_repository.auto_merge]

  lifecycle {
    postcondition {
      condition     = self.allow_auto_merge
      error_message = "Auto-merge is disabled on ${self.full_name}."
    }
  }
}
