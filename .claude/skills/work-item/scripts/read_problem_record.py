import argparse
import io
import json
import sys
from pathlib import Path
from typing import cast

SCRIPTS = Path(__file__).resolve().parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from github_tracker import (  # noqa: E402
    GitHub,
    Tracker,
    digest,
    issue_number,
    metadata,
    selection,
)
from tj_github_work_items.records import ItemError  # noqa: E402


def read(root: str, reference: str) -> dict[str, str]:
    tracker = Tracker(selection(root), GitHub(timeout=10))
    number = issue_number(reference, tracker.repo)
    if reference != f"https://github.com/{tracker.repo}/issues/{number}":
        raise ItemError("problem records require the canonical GitHub issue URL")
    issue = tracker.get(number)
    info, text = metadata(issue["body"])
    if digest(text) != info["render_hash"]:
        raise ItemError(
            "problem record was manually edited; reconcile its managed body"
        )
    if digest(info["kind"] + "\n" + text) != info.get("kind_hash"):
        raise ItemError(
            "problem record kind differs or is unbound; update its managed body"
        )
    compact = tracker.compact(issue)
    if compact["kind"] not in {"bug", "mistake", "debt"} or compact["state"] != "open":
        raise ItemError("reader supports only open problem records")
    return {
        "record": compact["url"],
        "kind": compact["kind"],
        "state": compact["state"],
        "body": issue["body"],
    }


def main() -> int:
    cast(io.TextIOWrapper, sys.stdout).reconfigure(encoding="utf-8")
    cast(io.TextIOWrapper, sys.stderr).reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(
        description="Read a canonical GitHub problem record"
    )
    parser.add_argument("--root", required=True)
    parser.add_argument("--record", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(read(args.root, args.record), ensure_ascii=False))
        return 0
    except (ItemError, OSError, UnicodeError) as error:
        print(json.dumps({"error": str(error)}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
