# scripts/

Standard-library Python only, written to the Python 3.9 syntax floor. Tools print their own
output and nothing else; the repository gates stop at the first failing step. Run everything from
the repository root.

## Repository gates

| File | Purpose |
| --- | --- |
| `check.sh` | The whole gate: structure (agents, skills, links, emoji, script mentions, version pins), the Python 3.9 floor, the three sub-gates below, the unit-test suite with its count floor, the command journeys and, once the generator exists, the `docs/TOOLS.md` drift check. Takes an optional repository root (`bash scripts/check.sh [root]`) so the fixture trees under `tests/fixtures/gates/` are gated with the real scripts. |
| `check_manifests.py` | One version across `plugin.json` and every mirrored manifest, tag-pinned marketplace refs, hooks declared once per runtime, no `skills`, `agents` or `commands` keys in the manifests Claude Code and Grok Build read, the desk-rule paragraph mirrored in `AGENTS.md` and `GEMINI.md`, the routine manifest's schema line. |
| `check_no_write_paths.py` | The AST no-write gate: network modules only in the market-data client and GET-only there; write-capable and dynamic-execution standard-library modules nowhere; `subprocess` only in the listed gates and `tests/gates/`; a prose scanner over skills, agents, docs, rules, template, routines and the root instruction files, with `no_write_allowlist.txt` for documented counter-examples. |
| `check_grok_template.py` | The Grok Bot template contract in `template/grok-bot.json`: release pin, skill hashes, publication status, and a byte comparison against the release tag once the tag exists. |
| `cli_journeys.py` | Runs the published commands as subprocesses in a scrubbed environment and screens their output for order-shaped text. |
| `no_write_allowlist.txt` | Exact paths or globs whose prose may contain a documented write-looking counter-example; an entry that matches nothing fails as stale. |
| `test_count_floor.txt` | The minimum number of tests the suite must discover. Raised in every pull request that adds tests, never lowered, never left at zero. |

## Library (`aegis_desk/`)

| Module | Purpose |
| --- | --- |
| `bsm.py` | Generalised Black-Scholes-Merton price and natural-unit Greeks on `math.erfc`, ported from the Aegis quant engine with its golden values. |
| `iv.py` | Bracketed Brent implied-volatility solver with arbitrage bounds and explicit statuses (`ok`, `expired`, `below_intrinsic`, `above_bound`, `no_root`). |
| `greeks.py` | Desk-unit Greeks (theta per calendar day, vega per volatility point, rho per rate point), absolute-delta helper and position scaling. |
| `units.py` | The desk's constants: 365-day year, seconds per year, volatility and rate points, contract multiplier. |
| `market_clock.py` | NYSE sessions, the embedded 2026-2027 holiday and early-close table with its source and expiry date, America/New_York daylight-saving rules without `tzdata`, days to the last trading day, exercise cutoffs, vendor clock reconciliation. |
| `occ.py` | OCC option-symbol parsing and rendering (canonical, compact and `O:`-prefixed forms). |
| `_vendor/toml_reader.py` | Strict TOML 1.0 reader (`Decimal` numbers, finite only, no `eval`) used on every interpreter; `toml_compat.py` cross-checks it against `tomllib` where that exists. |
| `state.py` | File lock, atomic writes, fsync'd JSONL appends and quarantine of corrupt state files. |
| `contract.py` | The shared command-line contract: `--json`, `--schema`, `--timeout`, `--desk-root`, `--offline`, the exit-code table (0 quiet, 1 error, 2 usage, 10 fired, 20 unavailable) and the JSON envelope. |

The desk tools themselves (market-data client, book of record, rules engine, levels, routine
entry point, doctor) arrive in the later pull requests listed in the gates-per-phase table in
`CONTRIBUTING.md`.
