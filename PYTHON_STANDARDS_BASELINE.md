# Python standards baseline

Existing violations at `ff58564c9100a777548a420eb1003c6a28687a7a` are baselined by exact authored file and rule. New files receive all enabled checks. Remove an exception when repairing its source. The tool configuration owns enforcement; this document records the rollout evidence and rationale.

## Ruff

Reason: preserve existing implementations during the standards rollout. Each row records the existing count and a representative required repair. No wildcard path or whole-rule ignores were added.

| File | Rule | Existing count | Required repair |
| --- | --- | ---: | --- |
| `.agents/github/utility/access/scripts/access_core.py` | `ANN401` | 7 | Dynamically typed expressions (typing.Any) are disallowed in `fp` |
| `.agents/github/utility/access/scripts/access_core.py` | `PLR6301` | 1 | Method `redirect_request` could be a function, class method, or static method |
| `.agents/github/utility/collaboration/scripts/github_collaboration.py` | `ANN401` | 5 | Dynamically typed expressions (typing.Any) are disallowed in `request` |
| `.agents/github/utility/configuration/scripts/configuration_adopt.py` | `ANN401` | 1 | Dynamically typed expressions (typing.Any) are disallowed in `_gh` |
| `.agents/github/utility/configuration/scripts/configuration_app.py` | `ANN401` | 3 | Dynamically typed expressions (typing.Any) are disallowed in `_json` |
| `.agents/github/utility/configuration/scripts/configuration_declaration.py` | `ANN401` | 8 | Dynamically typed expressions (typing.Any) are disallowed in `value` |
| `.agents/github/utility/configuration/scripts/github_configuration.py` | `TRY300` | 1 | Consider moving this statement to an `else` block |
| `.agents/sync_generated.py` | `ANN401` | 3 | Dynamically typed expressions (typing.Any) are disallowed in `load` |
| `.agents/tests/conftest.py` | `TID251` | 3 | `sys.path` is banned: Install the declared dependency; do not modify the import search path. |
| `.agents/tests/test_access_cli.py` | `PLR6301` | 2 | Method `read` could be a function, class method, or static method |
| `.agents/tests/test_github_access.py` | `ANN401` | 2 | Dynamically typed expressions (typing.Any) are disallowed in `read` |
| `.agents/tests/test_github_access.py` | `PT011` | 1 | `pytest.raises(access_core.AccessError)` is too broad, set the `match` parameter or use a more specific exception |
| `.agents/tests/test_github_claude_registration.py` | `ANN401` | 9 | Dynamically typed expressions (typing.Any) are disallowed in `*args` |
| `.agents/tests/test_github_collaboration.py` | `ANN401` | 6 | Dynamically typed expressions (typing.Any) are disallowed in `request` |
| `.agents/tests/test_github_collaboration.py` | `PT011` | 4 | `pytest.raises(ValueError)` is too broad, set the `match` parameter or use a more specific exception |
| `.agents/tests/test_github_configuration.py` | `ANN401` | 2 | Dynamically typed expressions (typing.Any) are disallowed in `**overrides` |
| `.agents/tests/test_github_hosts.py` | `PT011` | 1 | `pytest.raises(github_hosts.SettingsError)` is too broad, set the `match` parameter or use a more specific exception |
| `.agents/tests/test_github_mcp.py` | `ANN401` | 2 | Dynamically typed expressions (typing.Any) are disallowed in `**kwargs` |
| `.agents/tests/test_github_mcp.py` | `PLR6301` | 16 | Method `real_gh` could be a function, class method, or static method |
| `.agents/tests/test_github_mcp.py` | `PT011` | 1 | `pytest.raises(github_mcp_stdio.MCPError)` is too broad, set the `match` parameter or use a more specific exception |
| `.agents/tests/test_github_tools.py` | `PLR6301` | 2 | Method `write` could be a function, class method, or static method |
| `.agents/tests/test_github_tools.py` | `PT011` | 9 | `pytest.raises(access_core.AccessError)` is too broad, set the `match` parameter or use a more specific exception |

## Custom checker

Reason: preserve exact pre-existing custom-checker findings during the standards rollout. Each entry maps to one authored path and rule; repair the stated design issue and remove its exception. No generated or vendored path is included.

| File | Rule | Existing count | Required repair |
| --- | --- | ---: | --- |
| .agents/github/utility/access/scripts/github_access.py | PY016 | 7 | Inject process state or confine the read to an entry-point main. |
| .agents/github/utility/access/scripts/github_binding.py | PY016 | 3 | Inject process state or confine the read to an entry-point main. |
| .agents/github/utility/access/scripts/github_git_child.py | PY016 | 2 | Inject process state or confine the read to an entry-point main. |
| .agents/github/utility/access/scripts/github_hosts.py | PY001 | 1 | Rewrite the nested comprehension as named loops or one generator. |
| .agents/github/utility/access/scripts/github_mcp_stdio.py | PY016 | 2 | Inject process state or confine the read to an entry-point main. |
| .agents/github/utility/access/scripts/github_native_git.py | PY016 | 8 | Inject process state or confine the read to an entry-point main. |
| .agents/github/utility/access/scripts/github_platform.py | PY013 | 1 | Place enum-associated data on the enum member. |
| .agents/github/utility/access/scripts/github_tools.py | PY016 | 2 | Inject process state or confine the read to an entry-point main. |
| .agents/github/utility/access/scripts/github_windows.py | PY016 | 1 | Inject process state or confine the read to an entry-point main. |
| .agents/github/utility/collaboration/scripts/github_collaboration.py | PY001 | 4 | Rewrite the nested comprehension as named loops or one generator. |
| .agents/github/utility/configuration/scripts/configuration_adopt.py | PY001 | 1 | Rewrite the nested comprehension as named loops or one generator. |
| .agents/github/utility/configuration/scripts/configuration_app.py | PY016 | 1 | Inject process state or confine the read to an entry-point main. |
| .agents/github/utility/configuration/scripts/configuration_browser.py | PY016 | 1 | Inject process state or confine the read to an entry-point main. |
| .agents/github/utility/configuration/scripts/configuration_declaration.py | PY001 | 1 | Rewrite the nested comprehension as named loops or one generator. |
| .agents/github/utility/configuration/scripts/github_configuration.py | PY016 | 1 | Inject process state or confine the read to an entry-point main. |
| .agents/tests/test_access_cli.py | PY016 | 5 | Inject process state or confine the read to an entry-point main. |
| .agents/tests/test_github_binding.py | PY016 | 4 | Inject process state or confine the read to an entry-point main. |
| .agents/tests/test_github_host_activation.py | PY016 | 2 | Inject process state or confine the read to an entry-point main. |
| .agents/tests/test_github_mcp.py | PY016 | 3 | Inject process state or confine the read to an entry-point main. |
| .agents/tests/test_github_tools.py | PY016 | 21 | Inject process state or confine the read to an entry-point main. |
