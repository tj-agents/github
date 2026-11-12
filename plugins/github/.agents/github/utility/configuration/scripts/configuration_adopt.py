import json
import re
import subprocess
from pathlib import Path
from typing import Any

import configuration_app
import configuration_declaration
import configuration_terraform

RESOURCE = "module.configuration.github_repository_ruleset.this"
MERGE_QUEUE = (
    "check_response_timeout_minutes",
    "grouping_strategy",
    "max_entries_to_build",
    "max_entries_to_merge",
    "merge_method",
    "min_entries_to_merge",
    "min_entries_to_merge_wait_minutes",
)
FLAGS = (
    "creation",
    "update",
    "deletion",
    "non_fast_forward",
    "required_linear_history",
    "required_signatures",
)


def _gh(path: str) -> Any:
    result = subprocess.run(
        ["gh", "api", "--paginate", path], capture_output=True, text=True, check=True
    )
    return json.loads(result.stdout)


def live_rulesets(owner: str) -> dict[str, int]:
    found: dict[str, int] = {}
    for repository in _gh(f"users/{owner}/repos?per_page=100&type=owner"):
        if repository["archived"] or repository["private"]:
            continue
        name = repository["name"]
        for ruleset in _gh(f"repos/{owner}/{name}/rulesets?per_page=100"):
            if ruleset["source_type"] == "Repository":
                found[f"{name}/{ruleset['name']}"] = int(ruleset["id"])
    return found


class AdoptError(Exception):
    pass


def native(state: dict[str, Any]) -> dict[str, Any]:
    rules_state = state["rules"][0]
    carried = {*FLAGS, "pull_request", "required_status_checks", "merge_queue"}
    lost = sorted(
        key
        for key, value in rules_state.items()
        if key not in carried and value not in (None, False, [], 0, "")
    )
    if lost:
        raise AdoptError(
            f"Ruleset uses rules this module cannot carry: {', '.join(lost)}"
        )
    rules: list[dict[str, Any]] = [
        {"type": flag} for flag in FLAGS if rules_state.get(flag)
    ]
    for review in rules_state.get("pull_request") or []:
        rules.append(
            {
                "type": "pull_request",
                "parameters": {
                    key: review[key]
                    for key in (
                        "required_approving_review_count",
                        "dismiss_stale_reviews_on_push",
                        "require_code_owner_review",
                        "require_last_push_approval",
                        "required_review_thread_resolution",
                    )
                },
            }
        )
    for queue in rules_state.get("merge_queue") or []:
        rules.append(
            {
                "type": "merge_queue",
                "parameters": {key: queue[key] for key in MERGE_QUEUE},
            }
        )
    for checks in rules_state.get("required_status_checks") or []:
        rules.append(
            {
                "type": "required_status_checks",
                "parameters": {
                    "strict_required_status_checks_policy": checks[
                        "strict_required_status_checks_policy"
                    ],
                    "required_status_checks": [
                        {"context": check["context"]}
                        | (
                            {"integration_id": check["integration_id"]}
                            if check.get("integration_id")
                            else {}
                        )
                        for check in checks["required_check"]
                    ],
                },
            }
        )
    ref_name = state["conditions"][0]["ref_name"][0]
    return {
        "target": state["target"],
        "enforcement": state["enforcement"],
        "conditions": {
            "ref_name": {"include": ref_name["include"], "exclude": ref_name["exclude"]}
        },
        "bypass_actors": [
            {
                key: actor[key]
                for key in ("actor_id", "actor_type", "bypass_mode")
                if actor.get(key) is not None
            }
            for actor in state.get("bypass_actors") or []
        ],
        "rules": rules,
    }


def _write_imports(root: Path, owner_rulesets: dict[str, int]) -> None:
    blocks = [
        f'import {{\n  to = {RESOURCE}["{key}"]\n'
        f'  id = "{key.split("/")[0]}:{ruleset_id}"\n}}\n'
        for key, ruleset_id in sorted(owner_rulesets.items())
    ]
    (root / "imports.tf").write_text("\n".join(blocks), encoding="utf-8")


def generated(document: dict[str, Any], key: str) -> bool:
    approvals = document.get("approvals", {})
    repository, name = key.split("/", 1)
    return bool(
        approvals.get("required", False)
        and name == configuration_declaration.APPROVAL_RULESET
        and repository not in approvals.get("except", [])
    )


def adopt(declaration_path: Path) -> list[str]:
    root = declaration_path.resolve().parent
    document = json.loads(declaration_path.read_text(encoding="utf-8"))
    rulesets = live_rulesets(document["owner"])
    declared = [key for key in rulesets if not generated(document, key)]
    repositories = document.setdefault("repositories", {})
    stub = {
        "target": "branch",
        "enforcement": "active",
        "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"], "exclude": []}},
        "rules": [],
    }
    for key in declared:
        repository, name = key.split("/", 1)
        repositories.setdefault(repository, {}).setdefault("rulesets", {}).setdefault(
            name, stub
        )
    declaration_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    _write_imports(root, rulesets)
    run_id = configuration_terraform.plan(root)
    if run_id is None:
        raise configuration_terraform.TerraformError("terraform plan reported no run")
    plan = configuration_app.plan_json(run_id)
    for change in plan["resource_changes"]:
        found = re.fullmatch(rf'{re.escape(RESOURCE)}\["(.+)"\]', change["address"])
        if (
            found
            and change["change"].get("importing")
            and not generated(document, found.group(1))
        ):
            repository, name = found.group(1).split("/", 1)
            repositories[repository]["rulesets"][name] = native(
                change["change"]["before"]
            )
    declaration_path.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return sorted(rulesets)
