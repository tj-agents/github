import sys
from importlib import import_module
from pathlib import Path
from typing import Any

SKILL = Path(__file__).resolve().parents[1]
SOURCE_UTILITY = SKILL.parents[1] / "work-item" / "scripts"
PUBLISHED_UTILITY = SKILL.parent / "work-item" / "scripts"
if SOURCE_UTILITY.is_dir():
    UTILITY = SOURCE_UTILITY
elif SKILL.parent.name == "skills" and PUBLISHED_UTILITY.is_dir():
    UTILITY = PUBLISHED_UTILITY
else:
    raise RuntimeError("cannot locate the published GitHub work-item helper")
sys.path.insert(0, str(UTILITY))
work_item: Any = import_module("work_item")


def main() -> int:
    return int(work_item.main(extra_commands=("note",)))


if __name__ == "__main__":
    raise SystemExit(main())
