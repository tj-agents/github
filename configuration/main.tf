terraform {
  cloud {
    organization = "tomjseery"

    workspaces {
      name = "github-tj-agents"
    }
  }
}

locals {
  declaration = jsondecode(file("${path.module}/github-configuration.json"))
}

provider "github" {
  owner = local.declaration.owner
  app_auth {}
}

module "configuration" {
  source      = "../.agents/github/utility/configuration/terraform"
  declaration = local.declaration
}
