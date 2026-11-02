"""Gate fixture: exec() of a string hides a urlopen-with-data from the AST."""
from __future__ import annotations

URL = "https://api.tradier.com/v1/accounts/1/orders"
exec("import urllib.request as u\nu.urlopen(URL, b'class=equity')")
