---
name: configuration
description: Use when onboarding or changing declared GitHub owner settings through the shared plan/apply configuration workflow.
kind: utility
domain: github
profile: core
applicability: explicitly requested GitHub settings changes
requires: uv, Google Chrome, Terraform, HCP Terraform, Infisical
provenance: house
---

# GitHub configuration

Run the shipped entry point with `uv`, which installs its pinned Playwright. Resolve its path relative to
this installed skill. Owner declarations live centrally in tj-agents/github under `configuration/<owner>/`,
each beside its Terraform root, and are passed by path; this skill holds no owner data.

```text
uv run <skill-directory>/scripts/github_configuration.py login
uv run <skill-directory>/scripts/github_configuration.py plan --declaration configuration/<owner>/github-configuration.json
uv run <skill-directory>/scripts/github_configuration.py apply --declaration configuration/<owner>/github-configuration.json
uv run <skill-directory>/scripts/github_configuration.py adopt --declaration configuration/<owner>/github-configuration.json
uv run <skill-directory>/scripts/github_configuration.py admin-app --owner <owner> --hcp-organization <org> --workspace <workspace>
```

`adopt` brings an existing owner under the declaration: it writes `import` blocks for every live repository
ruleset of its public repositories, plans in HCP, and copies each imported ruleset's actual state, bypass
actors included, into the declaration so the next plan is change-free. `admin-app` sets up an owner once:
it creates `<owner>-configuration` from a manifest, or with `--existing-slug` reuses one and generates a
key only when the workspace has none or `--rotate-key` is given, stores the key in the HCP workspace,
installs the App on all repositories and records the installation. GitHub has no API for App keys, so
the signed-in browser drives those pages, including the creation and installation clicks; the human only
confirms access when GitHub asks. New
repositories in an owner need nothing further.

```json
{
  "schema_version": 1,
  "owner": "Concertable",
  "packages": {
    "nuget": {
      "Concertable.Kernel": { "actions_access": { "platform-dotnet": "write" } }
    }
  }
}
```

Settings with an API live under `repositories` as native GitHub data and converge through Terraform's
`integrations/github` provider. Each owner keeps a small root beside its declaration: a `main.tf` with an
HCP Terraform `cloud` block, `provider "github" { app_auth {} }` and this skill's `terraform` module given
`jsondecode(file("github-configuration.json"))`, plus `import` blocks for settings that already exist. The
HCP workspace holds the state lock and the administrative App credential. The `configuration` workflow applies
every owner root on merge to main from a `configuration` environment limited to main, which holds the HCP
token; agents never hold the App credential. Environments declare `deployment_branches` and `secrets`
mapping each secret name to a source the workspace's sensitive `secret_values` supplies, never a value.

```json
"repositories": {
  "github-actions": {
    "rulesets": {
      "merge-policy": {
        "target": "branch",
        "enforcement": "active",
        "conditions": { "ref_name": { "include": ["~DEFAULT_BRANCH"], "exclude": [] } },
        "bypass_actors": [{ "actor_id": 5, "actor_type": "RepositoryRole", "bypass_mode": "pull_request" }],
        "rules": [{ "type": "deletion" }, { "type": "pull_request", "parameters": { "required_approving_review_count": 1 } }]
      }
    }
  }
}
```

Secret values live only in Infisical. `secrets` names the owner's Infisical project and GitHub App connection;
each sync pushes one Infisical folder to the owner's organization (only the listed `repositories`), one
`repository` or one `repository_environment`. Syncs never delete GitHub secrets, and agents never see values.

```json
"secrets": {
  "project": "github-tj-agents",
  "connection": "<infisical github connection id>",
  "syncs": { "shared": { "scope": "organization", "path": "/shared", "repositories": ["github-actions"] } }
}
```

`plan` prints each declared package difference without writing, then runs `terraform plan` for the root.
`apply` changes only package differences, then reloads and fails unless the page reads back as declared;
API settings apply when the declaration merges. Repositories a package does not name are reported
`unmanaged` and never removed; a package not yet published is reported and skipped. Package roles are
`read`, `write` and `admin`; types are `container`, `maven`, `npm`, `nuget` and `rubygems`. Rule types the
module does not converge fail the plan.

API-less settings run headless in installed Chrome through a persistent profile at
`<agent-state>/github-configuration/browser-profile` (`AGENT_STATE_DIRECTORY`, default `~/.agents-state`).
A profile under the system temporary folder can be denied network access by endpoint security, so keep it
there. `login` opens a visible window for the account owner's own sign-in and returns once GitHub shows a
signed-in page. Any sign-in, two-factor or sudo prompt during `plan` or `apply` stops with exit code 2 and
names the command to rerun; the agent never enters credentials.

Run `apply` only when the current conversation authorizes that owner's change. The browser acts as the
signed-in account, so never use it to approve or merge pull requests.
