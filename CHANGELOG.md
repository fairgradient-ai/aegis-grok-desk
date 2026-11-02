# Changelog

## Unreleased

- Repository gates: `scripts/check.sh` (structure, Python 3.9 floor, version pins, test-count
  floor, command journeys), `check_manifests.py`, `check_no_write_paths.py` (AST no-write gate and
  prose scanner), `check_grok_template.py`, `cli_journeys.py`; CI on Python 3.9, 3.10, 3.12 and
  3.13 plus the QuantLib oracle workflow; negative fixture trees for every gate.
- Engine library `scripts/aegis_desk/`: Black-Scholes-Merton pricing, implied volatility, desk-unit
  Greeks and units ported from the Aegis quant engine with their golden values; NYSE market clock
  with embedded 2026-2027 calendar and DST rules; OCC symbol parser; strict vendored TOML reader;
  atomic state and file locking; the shared command-line contract and exit-code table.
- Repository scaffold: manifests, licences, placeholder role and skill, documentation skeletons.
