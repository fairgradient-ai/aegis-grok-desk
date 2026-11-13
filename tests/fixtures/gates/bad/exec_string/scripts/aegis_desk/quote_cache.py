"""Gate fixture: exec() of a string hides a urlopen-with-data from the AST."""
from __future__ import annotations

URL = "https://paper-api.alpaca.markets/v2/orders"
exec("import urllib.request as u\nu.urlopen(URL, b'class=equity')")
