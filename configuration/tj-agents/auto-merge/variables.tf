terraform {
  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.13"
    }
  }
}

variable "owner" { type = string }
variable "repositories" { type = set(string) }
