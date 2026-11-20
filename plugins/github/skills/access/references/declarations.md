# Credential declarations

The broker takes the first declaration it finds, in this order:

1. The `credentials` section of the committed `.agents/repository.json` at the current commit.
2. That section at the fetched remote default, `origin/HEAD`.
3. The `credentials.json` of each tj-agents plugin the checkout's `.agents/plugins.json` selects, or the
   main checkout's when the linked worktree has none. It is read from the installed copy Claude records
   for the main checkout (local, then project scope), else the user-scope install.

A committed section, including an explicit empty list, is authoritative for repositories Tommy owns.
Employer repositories commit nothing TJ Agents-specific: their local selection, kept in
`.git/info/exclude`, names the employer plugin, and that plugin ships the profile. A plugin profile uses
this same format with only `schema_version` and `credentials`. The file's other sections, such as
`access`, do not affect the broker. A declaration is public configuration; it contains no secret.
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
| `npm` | `https://npm.pkg.github.com` | Add `"scope":"@example"` and `"probe":"https://npm.pkg.github.com/@example/sample"`. npm accepts one credential per registry host; its temporary user config contains variable references, while the value exists only in the child environment. The npm and pnpm launchers serve it. |
| `container` | `https://ghcr.io/example` | Use `https://ghcr.io/v2/example/image/manifests/v1` or the intended immutable digest. Docker must select an OS-backed credential helper. Another saved account is a conflict. The launcher verifies, synchronizes and reads back that helper before running Docker. |
| `azure-devops` | `https://dev.azure.com/example` | Use `azure-devops-pat`, the expected authenticated identity and the intended `vso.*` scopes. The human selects an organization-scoped PAT. The Azure CLI launcher fixes the organization and supplies its required child-only PAT variable. |

Azure DevOps PATs may also serve NuGet endpoints on `pkgs.dev.azure.com` inside the declared
organization, with explicit resource probes. Existing native Microsoft Entra sign-in remains with its
own client; this broker does not exchange it for a PAT or widen its permissions.

Keep package sources and mappings in the repository's normal client configuration. A NuGet
`packageSourceCredentials` entry overrides the provider, and a project `.npmrc` token overrides the
broker's user configuration. When those files must stay unchanged, declare the environment placeholder
they read as the endpoint's optional `variable`, such as `"variable":"PACKAGES_TOKEN"` for
`%PACKAGES_TOKEN%` or `${PACKAGES_TOKEN}`. The dotnet, npm and pnpm launchers then set it in
that client's child environment only, after the same checks; it never reaches the caller's environment.
Otherwise remove the broker-managed source's credential entry. Preserve unrelated feeds and providers.

For source development, invoke `python -B <plugin-root>/.agents/github/runtime/gate/tj_credentials.py`
with the same command arguments. Installed clients use the setup-created launchers; they do not depend on
a producer checkout. A declaration on the default branch becomes available to older worktrees after fetch.

## Private source reads in GitHub Actions

Commit the credential in the destination repository's `.agents/repository.json`. Add the optional
`actions_secret` destination to a fine-grained GitHub credential that declares only Contents read
and one Git endpoint per source repository under the same owner.

```json
{
  "name": "private-source-read",
  "provider": "github-fine-grained",
  "owner": "Example",
  "login": "example-user",
  "expires_in": 30,
  "scopes": [],
  "permissions": {"contents": "read"},
  "endpoints": [{"client": "git", "url": "https://github.com/Example/private-source"}],
  "actions_secret": {"repository": "Example/catalog", "name": "PRIVATE_SOURCE_READ"}
}
```

The agent starts `tj-credentials onboard --name private-source-read` from the destination checkout.
The broker checks the committed declaration and HTTPS origin, then opens the issuer and guarded
local submission pages. In the issuer form the human chooses
only the listed source repositories and Contents read. The local form asks the human to confirm the
grant and explicitly authorize the displayed operation, account, destination and secret. After that
submission, the broker rechecks the committed destination and origin, verifies the owner's native
GitHub CLI identity and repository access, and
verifies the candidate's identity and reads a commit from each source. GitHub's token API does not
attest that no additional repositories or permissions were granted.

The broker keeps a dedicated OS-vault enrollment for that destination and secret name. Its endpoints
serve as probes only. It sends the verified value to `gh secret set` on stdin and checks the secret's
metadata. If the upload fails after enrollment, the agent starts `tj-credentials publish --name
private-source-read`. The same guarded local form asks the human to authorize rechecking and publishing
the saved credential. It binds consent to that enrollment, so a concurrent rotation requires fresh
consent. `rotate` uses the same guarded flow for a replacement. The owner's existing GitHub CLI
sign-in must match the declared login and permit managing the destination's Actions secrets.
Agent markers remain on the broker and its child processes. Credential overrides are refused.

A successful local check verifies the saved credential. The hosted value is write-only. The
publication receipt confirms the destination accepted the secret and the original workflow's
successful private fetch establishes that a runner can use it. Upgrade the credential broker before
committing a declaration that uses a newly supported field.

### Human consent and process lifetime

Run the broker directly with the host's process tool. Use a fresh result path in an existing local
directory when a public process receipt is needed.

```text
tj-credentials onboard --name private-source-read --result-file <new-result-path>
tj-credentials publish --name private-source-read --result-file <new-result-path>
```

Keep the process alive while awaiting the human's browser submission. The human performs issuer
sign-in, grant selection and local submission. Only the broker handles credential values and the
private form URL. Its output and receipts contain public status. Agents must preserve identity markers
and leave publication consent to the human in the browser. Opening the form authorizes no owner
credential access or upload. An abandoned, rejected or expired form authorizes no publication.

Read the result file as JSON. A running receipt contains the PID and start time. Match the
process command line to its unique result path and operation before treating it as live. Reopening
an existing result path preserves the original attempt. A missing receipt indicates no verified start.
Only `state=complete`, `exit_code=0`, `status=published` and the expected destination confirm
publication. After a failed or interrupted attempt, diagnose it, run the named check again, and
start a fresh attempt for the action it reports.
