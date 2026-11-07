locals {
  secrets = try(var.declaration.secrets, null)
  syncs   = local.secrets == null ? {} : try(local.secrets.syncs, {})
  sync_repositories = toset(flatten([
    for sync in values(local.syncs) : try(sync.repositories, [])
  ]))
}

resource "infisical_project" "this" {
  count = local.secrets == null ? 0 : 1

  name = local.secrets.project
  slug = local.secrets.project
}

resource "infisical_secret_folder" "this" {
  for_each = {
    for name, sync in local.syncs : name => sync
    if try(sync.path, "/") != "/"
  }

  project_id       = infisical_project.this[0].id
  environment_slug = try(each.value.environment, "prod")
  folder_path      = "/"
  name             = trimprefix(each.value.path, "/")
}

data "github_repository" "sync" {
  for_each = local.sync_repositories

  name = each.key
}

resource "infisical_secret_sync_github" "this" {
  for_each = local.syncs

  name          = each.key
  project_id    = infisical_project.this[0].id
  connection_id = local.secrets.connection
  environment   = try(each.value.environment, "prod")
  secret_path   = try(each.value.path, "/")

  destination_config = {
    scope                  = each.value.scope
    repository_owner       = var.declaration.owner
    repository_name        = try(each.value.repository, null)
    repository_environment = try(each.value.repository_environment, null)
    visibility             = each.value.scope == "organization" ? (length(try(each.value.repositories, [])) > 0 ? "selected" : "all") : null
    selected_repository_ids = each.value.scope == "organization" && length(try(each.value.repositories, [])) > 0 ? [
      for repository in each.value.repositories : tonumber(data.github_repository.sync[repository].repo_id)
    ] : null
  }

  sync_options = {
    initial_sync_behavior   = "overwrite-destination"
    disable_secret_deletion = true
  }

  depends_on = [infisical_secret_folder.this]
}
