"""Gate fixture: xmlrpc.client is an HTTP POST client in the standard library."""
from __future__ import annotations

import xmlrpc.client


def submit(url: str, ticket: dict) -> object:
    return xmlrpc.client.ServerProxy(url).submit(ticket)
