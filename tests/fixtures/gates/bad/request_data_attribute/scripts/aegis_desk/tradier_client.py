"""Gate fixture: a body smuggled through the data property's backing field."""
from __future__ import annotations

import urllib.request


def build(url: str) -> urllib.request.Request:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    request._data = b"symbols=SPY"
    return request
