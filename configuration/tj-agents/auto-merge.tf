locals {
  auto_merge_repositories = toset([
    ".github",
    "agent",
    "agent-store",
    "aspire",
    "azure",
    "bash",
    "cli",
    "core",
    "cpp",
    "docs",
    "dotnet",
    "github",
    "github-actions",
    "handy",
    "infonetica",
    "learning",
    "marketplace",
    "nvim",
    "personal",
    "python",
    "react",
    "renovate-config",
    "rust",
    "template",
    "work",
    "work-items",
  ])
}

module "auto_merge" {
  source       = "./auto-merge"
  owner        = "tj-agents"
  repositories = local.auto_merge_repositories
}

import {
  for_each = local.auto_merge_repositories
  to       = module.auto_merge.github_repository.auto_merge[each.key]
  id       = each.key
}
