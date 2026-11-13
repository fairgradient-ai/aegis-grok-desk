"""Gate fixture: the shape of a GET-only market-data client (never executed)."""
from __future__ import annotations

import socket
import ssl
import urllib.error
import urllib.request
from typing import Dict


class RefuseRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401 - urllib hook
        raise urllib.error.HTTPError(newurl, code, "redirects are refused", headers, fp)


def fetch(url: str, headers: Dict[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=headers, method="GET")
    context = ssl.create_default_context()
    opener = urllib.request.build_opener(RefuseRedirects(), urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=timeout) as response:
            return response.read()
    except socket.timeout:
        return b""


def fetch_plain(url: str, timeout: float) -> bytes:
    with urllib.request.urlopen(url, None, timeout) as response:
        return response.read()
