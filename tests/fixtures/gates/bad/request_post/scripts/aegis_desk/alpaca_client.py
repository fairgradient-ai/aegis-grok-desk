"""Gate fixture: a Request with a non-GET method."""
from __future__ import annotations

import urllib.request


def build(url: str) -> urllib.request.Request:
    return urllib.request.Request(url, method="POST")
