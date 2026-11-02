import json
import re
import sys
from pathlib import Path
from typing import Any

TEMPLATES = Path(__file__).resolve().parent / "templates"
KINDS = ("feature", "story", "task", "bug", "mistake", "debt")


class ItemError(ValueError):
    pass


def schema_for(kind: object) -> dict[str, Any]:
    if not isinstance(kind, str) or kind not in KINDS:
        raise ItemError("kind must be one of: " + ", ".join(KINDS))
    schema: dict[str, Any] = json.loads(
        (TEMPLATES / f"{kind}.schema.json").read_text(encoding="utf-8")
    )
    return schema


def check_value(value: object, spec: dict[str, Any], path: str) -> None:
    expected = spec["type"]
    if expected == "string":
        if not isinstance(value, str) or not value.strip():
            raise ItemError(f"{path} must be a nonempty string")
        if len(value) > spec.get("maxLength", 65536):
            raise ItemError(f"{path} is too long")
        if not re.search(spec.get("pattern", r"\S"), value):
            raise ItemError(f"{path} does not match its contract")
        if "enum" in spec and value not in spec["enum"]:
            raise ItemError(f"{path} has an unsupported value")
        if "const" in spec and value != spec["const"]:
            raise ItemError(f"{path} must equal {spec['const']}")
    elif expected == "array":
        if not isinstance(value, list):
            raise ItemError(f"{path} must be an array")
        for index, item in enumerate(value):
            check_value(item, spec["items"], f"{path}[{index}]")
    elif expected == "object":
        if not isinstance(value, dict) or not value:
            raise ItemError(f"{path} must be a nonempty object")
    else:
        raise ItemError(f"unsupported contract type: {expected}")


def validate(item: object) -> dict[str, Any]:
    if not isinstance(item, dict):
        raise ItemError("input must be a JSON object")
    spec = schema_for(item.get("kind"))
    if set(item) - spec["properties"].keys():
        raise ItemError("input contains unknown fields")
    missing = [name for name in spec["required"] if name not in item]
    if missing:
        raise ItemError("missing fields: " + ", ".join(missing))
    for name, value in item.items():
        check_value(value, spec["properties"][name], name)
    if "\n" in item["title"] or "\r" in item["title"]:
        raise ItemError("title must be one line")
    return item


def skeleton(kind: str) -> dict[str, str]:
    spec = schema_for(kind)
    return {name: kind if name == "kind" else "" for name in spec["required"]}


def render(item: dict[str, Any]) -> str:
    validate(item)
    spec = schema_for(item["kind"])
    lines = [item["summary"].strip()]
    for name, rule in spec["properties"].items():
        if "title" in rule and name in item:
            lines.extend(["", "## " + rule["title"], "", item[name].strip()])
    metadata = [f"Kind: {item['kind']}"]
    for name in ("priority", "owner"):
        if name in item:
            metadata.append(f"{name.capitalize()}: {item[name]}")
    lines.extend(["", "## Tracking", "", *metadata])
    for name, heading in (("links", "Links"), ("legacy_ids", "Legacy identities")):
        if item.get(name):
            lines.extend(
                ["", "## " + heading, "", *["- " + value for value in item[name]]]
            )
    if "handoff_disposition" in item:
        lines.extend(
            [
                "",
                "```agent-handoff-disposition",
                json.dumps(
                    item["handoff_disposition"],
                    ensure_ascii=False,
                    sort_keys=True,
                    indent=2,
                ),
                "```",
            ]
        )
    return "\n".join(lines) + "\n"


def read_input(path: str) -> dict[str, Any]:
    try:
        raw = (
            sys.stdin.read()
            if path == "-"
            else Path(path).read_text(encoding="utf-8-sig")
        )
        return validate(json.loads(raw))
    except (OSError, json.JSONDecodeError) as error:
        raise ItemError("cannot read a valid work-item JSON input") from error
