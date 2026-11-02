# Changelog

## Unreleased

- Data plane: the GET-only Tradier market-data client (`scripts/aegis_desk/tradier_client.py`:
  pinned hosts, allow-listed paths, literal `GET` and no body on every request, no redirects, no
  proxy, TLS 1.2 or later, one receipt per request, credential redaction, refusal of a `200` that
  echoes the credential, loopback fake-server tests asserting the method on every request) with
  an offline transport over the recorded fixture desk `tests/fixtures/desk/` (five sets: regular,
  stale, after the close, before the open, holiday); the quote cache with the desk-wide
  60-per-minute ledger, per-run caps and a run-wide stop after a quota or credential error;
  quote quality grades with session-aware freshness; heartbeat records; shared vendor types,
  desk paths and the strict `desk.toml` reader; the tools `quote_cache.py`, `quote_qc.py`,
  `heartbeat.py` and `market_clock.py`; `--now` on the shared command-line contract; nine tool
  journeys. The no-write gate also rejects subclasses of `urllib.request.Request`, assignment of
  a request's `_data` or target fields, `set_proxy`, and reflection into third-party HTTP clients.
- Repository gates: `scripts/check.sh` (structure, Python 3.9 floor, version pins, test-count
  floor, command journeys), `check_manifests.py`, `check_no_write_paths.py` (AST no-write gate and
  prose scanner), `check_grok_template.py`, `cli_journeys.py`; CI on Python 3.9, 3.10, 3.12 and
  3.13 plus the QuantLib oracle workflow; negative fixture trees for every gate.
- Engine library `scripts/aegis_desk/`: Black-Scholes-Merton pricing, implied volatility, desk-unit
  Greeks and units ported from the Aegis quant engine with their golden values; NYSE market clock
  with embedded 2026-2027 calendar and DST rules; OCC symbol parser; strict vendored TOML reader;
  atomic state and file locking; the shared command-line contract and exit-code table.
- Repository scaffold: manifests, licences, placeholder role and skill, documentation skeletons.
