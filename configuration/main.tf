terraform {
  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.13"
    }
  }

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

variable "secret_values" {
  type      = map(string)
  sensitive = true
  default   = {}
}

module "configuration" {
  source        = "../.agents/github/utility/configuration/terraform"
  declaration   = local.declaration
  secret_values = var.secret_values
}
