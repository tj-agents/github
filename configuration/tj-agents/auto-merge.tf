data "github_repositories" "active" {
  query = "org:tj-agents archived:false"
}

locals {
  auto_merge_repositories = toset(data.github_repositories.active.names)
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
