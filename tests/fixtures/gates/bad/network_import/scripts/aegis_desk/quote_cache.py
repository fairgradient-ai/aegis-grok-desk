"""Gate fixture: a second module imports urllib.request."""
from __future__ import annotations

import urllib.request


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url) as response:
        return response.read()
