"""Gate fixture: the routine entry point shells out to curl (no desk tool may import subprocess)."""
from __future__ import annotations

import subprocess


def fetch() -> str:
    return subprocess.run(["curl", "-s", "https://x"], check=False).stdout
