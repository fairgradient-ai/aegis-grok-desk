"""Gate fixture: a tool shells out to curl."""
from __future__ import annotations

import subprocess


def fetch(url: str) -> str:
    return subprocess.run(["curl", "-s", url], capture_output=True, text=True, check=False).stdout
