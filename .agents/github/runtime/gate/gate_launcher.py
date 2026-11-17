import os
from pathlib import Path

from gate_platform import MARKER

PROBE_BYTES = 4096


def is_gate_launcher(path: Path) -> bool:
    try:
        with path.open("rb") as stream:
            head = stream.read(PROBE_BYTES)
            size = stream.seek(0, os.SEEK_END)
            stream.seek(max(0, size - PROBE_BYTES))
            tail = stream.read()
    except OSError:
        return False
    marker = MARKER.encode()
    return marker in head or marker in tail
