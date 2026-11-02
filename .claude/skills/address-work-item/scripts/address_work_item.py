import argparse
import io
import json
import sys
from importlib import import_module
from pathlib import Path
from typing import Any, cast

SKILL = Path(__file__).resolve().parents[1]
SOURCE_UTILITY = SKILL.parents[2] / "utility" / "work-item" / "scripts"
PUBLISHED_UTILITY = SKILL.parent / "work-item" / "scripts"
if SOURCE_UTILITY.is_dir():
    UTILITY = SOURCE_UTILITY
elif SKILL.parent.name == "skills" and PUBLISHED_UTILITY.is_dir():
    UTILITY = PUBLISHED_UTILITY
else:
    raise RuntimeError("cannot locate the published GitHub work-item helper")
sys.path.insert(0, str(UTILITY))
RUNTIME = UTILITY.parent / "runtime" / "src"
if str(RUNTIME) not in sys.path:
    sys.path.insert(0, str(RUNTIME))
github_tracker: Any = import_module("github_tracker")

from tj_github_work_items.records import ItemError  # noqa: E402


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Address existing GitHub work items")
    commands = result.add_subparsers(dest="command", required=True)
    github_tracker.add_commands(
        commands, ("update", "note", "link", "progress", "close")
    )
    return result


def main() -> int:
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding="utf-8")
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding="utf-8")
    try:
        output = github_tracker.execute(parser().parse_args())
        print(json.dumps(output, ensure_ascii=False, separators=(",", ":")))
        return 0
    except (ItemError, OSError, UnicodeError) as error:
        print(json.dumps({"error": str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
