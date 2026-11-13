"""Gate fixture: urlopen given a body."""
from __future__ import annotations

import urllib.request


def fetch(url: str) -> bytes:
    with urllib.request.urlopen(url, data=b"symbol=SPY") as response:
        return response.read()
