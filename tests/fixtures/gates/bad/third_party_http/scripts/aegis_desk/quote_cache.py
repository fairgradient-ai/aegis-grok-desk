"""Gate fixture: a third-party HTTP client."""
from __future__ import annotations

import requests


def fetch(url: str) -> str:
    return requests.get(url, timeout=5).text
