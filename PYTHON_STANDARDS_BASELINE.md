# Python standards baseline

Existing violations at `ff58564c9100a777548a420eb1003c6a28687a7a` are baselined by exact authored file and rule. New files receive all enabled checks. Remove an exception when repairing its source. The tool configuration owns enforcement; this document records the rollout evidence and rationale.

## Ruff

Reason: preserve existing implementations during the standards rollout. Each row records the existing count and a representative required repair. No wildcard path or whole-rule ignores were added.

| File | Rule | Existing count | Required repair |
| --- | --- | ---: | --- |
| `.agents/github/utility/collaboration/scripts/github_collaboration.py` | `ANN401` | 5 | Dynamically typed expressions (typing.Any) are disallowed in `request` |
| `.agents/github/utility/configuration/scripts/configuration_adopt.py` | `ANN401` | 1 | Dynamically typed expressions (typing.Any) are disallowed in `_gh` |
| `.agents/github/utility/configuration/scripts/configuration_app.py` | `ANN401` | 3 | Dynamically typed expressions (typing.Any) are disallowed in `_json` |
| `.agents/github/utility/configuration/scripts/configuration_declaration.py` | `ANN401` | 8 | Dynamically typed expressions (typing.Any) are disallowed in `value` |
| `.agents/github/utility/configuration/scripts/github_configuration.py` | `TRY300` | 1 | Consider moving this statement to an `else` block |
| `.agents/sync_generated.py` | `ANN401` | 3 | Dynamically typed expressions (typing.Any) are disallowed in `load` |
| `.agents/tests/conftest.py` | `TID251` | 3 | `sys.path` is banned: Install the declared dependency; do not modify the import search path. |
| `.agents/tests/test_github_collaboration.py` | `ANN401` | 6 | Dynamically typed expressions (typing.Any) are disallowed in `request` |
| `.agents/tests/test_github_collaboration.py` | `PT011` | 4 | `pytest.raises(ValueError)` is too broad, set the `match` parameter or use a more specific exception |
| `.agents/tests/test_github_configuration.py` | `ANN401` | 2 | Dynamically typed expressions (typing.Any) are disallowed in `**overrides` |

## Custom checker

Reason: preserve exact pre-existing custom-checker findings during the standards rollout. Each entry maps to one authored path and rule; repair the stated design issue and remove its exception. No generated or vendored path is included.

| File | Rule | Existing count | Required repair |
| --- | --- | ---: | --- |
| .agents/github/utility/collaboration/scripts/github_collaboration.py | PY001 | 4 | Rewrite the nested comprehension as named loops or one generator. |
| .agents/github/utility/configuration/scripts/configuration_adopt.py | PY001 | 1 | Rewrite the nested comprehension as named loops or one generator. |
| .agents/github/utility/configuration/scripts/configuration_app.py | PY016 | 1 | Inject process state or confine the read to an entry-point main. |
| .agents/github/utility/configuration/scripts/configuration_browser.py | PY016 | 1 | Inject process state or confine the read to an entry-point main. |
| .agents/github/utility/configuration/scripts/configuration_declaration.py | PY001 | 1 | Rewrite the nested comprehension as named loops or one generator. |
| .agents/github/utility/configuration/scripts/github_configuration.py | PY016 | 1 | Inject process state or confine the read to an entry-point main. |
