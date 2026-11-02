"""Gate fixture: a Request subclass whose class body predefines the verb."""
from __future__ import annotations

import urllib.request


class _PostRequest(urllib.request.Request):
    method = "POST"


def build(url: str) -> urllib.request.Request:
    return _PostRequest(url, headers={"Accept": "application/json"})
