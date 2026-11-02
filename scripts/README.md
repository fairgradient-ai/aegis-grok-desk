# scripts/

Standard-library Python only, written to the Python 3.9 syntax floor. Tools print their own
output and nothing else; the repository gates stop at the first failing step. Run everything from
the repository root.

## Repository gates

| File | Purpose |
| --- | --- |
| `check.sh` | The whole gate: structure (agents, skills, links, emoji, script mentions, version pins), the Python 3.9 floor, the three sub-gates below, the unit-test suite with its count floor, the command journeys and, once the generator exists, the `docs/TOOLS.md` drift check. Takes an optional repository root (`bash scripts/check.sh [root]`) so the fixture trees under `tests/fixtures/gates/` are gated with the real scripts. |
| `check_manifests.py` | One version across `plugin.json` and every mirrored manifest, tag-pinned marketplace refs, hooks declared once per runtime, no `skills`, `agents` or `commands` keys in the manifests Claude Code and Grok Build read, the desk-rule paragraph mirrored in `AGENTS.md` and `GEMINI.md`, the routine manifest's schema line. |
| `check_no_write_paths.py` | The AST no-write gate: network modules only in the market-data client and GET-only there (every request is a direct `urllib.request.Request(...)` with the literal `GET` and no body; no subclass of `Request`, no change of a built request's verb, body or target, no reflection into third-party HTTP clients); write-capable and dynamic-execution standard-library modules nowhere; `subprocess` only in the listed gates and `tests/gates/`; a prose scanner over skills, agents, docs, rules, template, routines and the root instruction files, with `no_write_allowlist.txt` for documented counter-examples. |
| `check_grok_template.py` | The Grok Bot template contract in `template/grok-bot.json`: release pin, skill hashes, publication status, and a byte comparison against the release tag once the tag exists. |
| `cli_journeys.py` | Runs the published commands as subprocesses in a scrubbed environment and screens their output for order-shaped text. The gate journeys run against any tree; the tool journeys run only against this checkout, each on a temporary copy of the fixture desk `tests/fixtures/desk/` with `--offline --now <instant>`, so nothing under `tests/` is ever written. |
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
| `contract.py` | The shared command-line contract: `--json`, `--schema`, `--timeout`, `--desk-root`, `--offline`, `--now` (one UTC spelling, `YYYY-MM-DDTHH:MM:SSZ`, parsed identically on every interpreter), the exit-code table (0 quiet, 1 error, 2 usage, 10 fired, 20 unavailable) and the JSON envelope. |
| `version.py` | The release version read from `plugin.json` (`0.0.0` when it is missing or unreadable). |
| `market_types.py` | Frozen vendor types (`Quote`, `Bar`, `SessionWindow`, `CalendarDay`, `ClockInfo`, `RequestReceipt`) built from the recorded Tradier shapes; a present-but-garbled field raises `MarketTypesError` instead of becoming a quiet `None`. |
| `desk_paths.py` | The desk root's layout (`desk.toml`, `positions.csv`, `state/`, `data/cache/`, `data/book/`, `journal/`, `levels/`, ...) and `ensure_runtime_dirs`, the only function that creates desk directories. |
| `desk_config.py` | Strict reader for the owner-authored `desk.toml` (sections `desk`, `owner`, `market_data`, `rates`, `levels`; `machine_role` rejected anywhere) with the file's sha256 and the freshness bound `feed_delay_min + quote_ttl_min + grace_min`. |
| `tradier_client.py` | The only network module: GET-only Tradier market-data client over an explicit `urllib` opener (pinned sandbox or production host, allow-listed paths, literal `GET`, no body, no redirects, no proxy, TLS 1.2 or later), token from the environment variable or a mode-0600 file, one receipt per request, credential redaction in every message and refusal of a `200` that echoes the credential; `OfflineTransport` serves the recorded fixture sets. |
| `quote_cache.py` | Quote and chain cache under `data/cache/` with the desk-wide 60-per-minute request ledger, per-run request caps, a run-wide stop after a quota or credential error, and the production-host refusal; `open_market_data` is the entry point the tools share. |
| `quote_qc.py` | Quote quality: session-aware freshness per side (`regular`, after the close, before the open, closed days), spread grades A-F on the printed two-decimal spread, liquidation and mid marks, `usable_for_rules`. |
| `heartbeat.py` | Heartbeat records per routine under `state/heartbeat/` (schema 1: run instant, exit code, status, lateness against the scheduled ET slot, message hash) and the append-only history. |

## Desk tools

Every tool takes `--json`, `--schema`, `--timeout`, `--desk-root`, `--offline` (fixture sets under the
desk root's `fixtures/tradier/`) and `--now`, and exits by the one table: 0 quiet or ok, 1 error,
2 usage, 10 fired, 20 unavailable. None of them writes outside the desk root, and none of them
reaches a broker: the only network path is the GET-only market-data client.

| Tool | Purpose |
| --- | --- |
| `market_clock.py` | The NYSE session at an instant (`--at`), the next trading day, the previous close and, with `--expiry`, the settlement day, last trading day, calendar and trading days to expiry and the exchange exercise cutoff. Reads and writes nothing. |
| `quote_cache.py` | Serves quotes (and one chain with `--chain SYMBOL --expiration YYYY-MM-DD`) from the cache or the vendor, within the 60-per-minute budget; exit 20 when a symbol is missing, the host is production or no token is configured. |
| `quote_qc.py` | Grades the served quotes (freshness, spread, marks) and says whether each is usable for rules; exit 20 when any symbol is stale, missing or the market is closed with nothing cached. |
| `heartbeat.py` | Writes one routine's heartbeat (`--routine`, `--exit-code` or `--status`, `--scheduled-et`, `--message-file`) or lists them with `--list`. |

The remaining desk tools (book of record, rules engine, levels, routine entry point, doctor)
arrive in the later pull requests listed in the gates-per-phase table in `CONTRIBUTING.md`.
