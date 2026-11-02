import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any, cast

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
RUNTIME = SCRIPTS.parent / "runtime" / "src"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))

from tj_github_work_items.records import (  # noqa: E402
    KINDS,
    ItemError,
    read_input,
    render,
    schema_for,
    skeleton,
    validate,
)

__all__ = ("validate",)


def parser(extra_commands: tuple[str, ...] = ()) -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        description="Deterministic, explicitly selected work-item tools"
    )
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("schema", "template"):
        command = commands.add_parser(name)
        command.add_argument("--kind", choices=KINDS, required=True)
    for name in ("validate", "preview"):
        command = commands.add_parser(name)
        command.add_argument(
            "--input", required=True, help="UTF-8 JSON path, or - for stdin"
        )
    provider = commands.add_parser("provider")
    provider.add_argument("--root", required=True)
    from github_tracker import add_commands

    add_commands(commands, ("create", "read", "list", "state", *extra_commands))
    return result


def run(arguments: argparse.Namespace) -> dict[str, Any]:
    if arguments.command == "provider":
        from github_tracker import selection

        return selection(arguments.root)
    if arguments.command == "schema":
        return schema_for(arguments.kind)
    if arguments.command == "template":
        return skeleton(arguments.kind)
    if arguments.command not in ("validate", "preview"):
        from github_tracker import execute

        return execute(arguments)
    item = read_input(arguments.input)
    if arguments.command == "validate":
        return {"valid": True, "kind": item["kind"]}
    return {"title": item["title"], "body": render(item)}


def main(extra_commands: tuple[str, ...] = ()) -> int:
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding="utf-8")
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding="utf-8")
    try:
        output = run(parser(extra_commands).parse_args())
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ItemError, OSError, UnicodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
