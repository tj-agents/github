# Credential declarations

The broker reads the `credentials` section of `.agents/repository.json` at the current commit, falling
back to the fetched remote default only when that commit has no such section; the file's other sections,
such as `access`, do not affect it. This declaration is public configuration; it contains no secret.
The same provider, owner and login share one enrollment across repositories and worktrees.

```json
{
  "schema_version": 1,
  "credentials": [{
    "name": "work-packages",
    "provider": "github-classic",
    "owner": "Example",
    "login": "example-user",
    "scopes": ["read:packages"],
    "expires_in": 30,
    "endpoints": [{
      "client": "nuget",
      "url": "https://nuget.pkg.github.com/Example/index.json",
      "probe": "https://nuget.pkg.github.com/Example/download/sample/index.json"
    }]
  }]
}
```

`name` selects the displayed onboarding action. `provider` is `github-classic`, `github-fine-grained` or
`azure-devops-pat`. `owner` and `login` select the expected account and organization, not a machine path.
`expires_in` is a maximum lifetime of 1–90 days; the human confirms the actual expiry locally. GitHub's
reported expiry takes precedence when earlier. Saved metadata contains only a random vault version and
expiry. Revocation still requires a live check; no expiry date proves a token remains valid.

`scopes` lists the issuer's required scopes. GitHub Packages requires a classic PAT with `read:packages`;
fine-grained PATs cannot serve those routes. Private repository work normally needs `repo`; workflow writes
also need `workflow`. Optional `permissions` declares repository permissions such as
`{"contents":"write","pull_requests":"write","issues":"write","actions":"read","workflows":"write"}`.
Fine-grained tokens use this permission map and an empty scope list. The issuer form still requires human
repository selection and any organization approval. A classic token can reach other resources the account
can access. Git, gh and MCP route only to declared targets. Native package authentication is registry-host
scoped: `owner` binds enrollment and resource probes, npm `scope` selects a registry, and Docker's helper
serves that registry. Implicit dependencies and base images keep the native client's authentication
behavior. Resource authorization remains with the issuer and registry; an owner declaration does not
make a classic PAT organization-scoped.

Each endpoint declares `client`, a plain HTTPS `url`, and, when needed, an authenticated `probe`. Registry
probes must name an actual resource inside the declared owner, not a public registry landing page.

| Client | URL | Probe and requirements |
| --- | --- | --- |
| `git`, `gh`, `mcp` | `https://github.com/Example/private-project` | A repository read checks access. An organization-wide URL uses a declared repository probe. Git needs 2.46+; gh uses a closed command grammar. MCP exposes only the three inherited read tools through the pinned upstream server. |
| `nuget` | `https://nuget.pkg.github.com/Example/index.json` | A private package versions URL, such as the example above. Native provider discovery requires NuGet 6.13+ (.NET SDK 9.0.200+). |
| `npm` | `https://npm.pkg.github.com` | Add `"scope":"@example"` and `"probe":"https://npm.pkg.github.com/@example/sample"`. npm accepts one credential per registry host; its temporary user config contains variable references, while the value exists only in the child environment. |
| `container` | `https://ghcr.io/example` | Use `https://ghcr.io/v2/example/image/manifests/v1` or the intended immutable digest. Docker must select an OS-backed credential helper. Another saved account is a conflict. The launcher verifies, synchronizes and reads back that helper before running Docker. |
| `azure-devops` | `https://dev.azure.com/example` | Use `azure-devops-pat`, the expected authenticated identity and the intended `vso.*` scopes. The human selects an organization-scoped PAT. The Azure CLI launcher fixes the organization and supplies its required child-only PAT variable. |

Azure DevOps PATs may also serve NuGet endpoints on `pkgs.dev.azure.com` inside the declared
organization, with explicit resource probes. Existing native Microsoft Entra sign-in remains with its
own client; this broker does not exchange it for a PAT or widen its permissions.

Keep package sources and mappings in the repository's normal client configuration. Remove a NuGet
`packageSourceCredentials` entry for a broker-managed source so its password or environment placeholder
does not override the provider. Remove the retired long-lived password variable through the environment's
owning setup command. The broker never exports that variable. Preserve unrelated feeds and providers.

For source development, invoke `python -B <plugin-root>/.agents/github/runtime/gate/tj_credentials.py`
with the same command arguments. Installed clients use the setup-created launchers; they do not depend on
a producer checkout. A declaration on the default branch becomes available to older worktrees after fetch.
