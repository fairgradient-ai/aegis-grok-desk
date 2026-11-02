# Offline Tradier fixtures for the fixture desk root

These directories are the recorded-response store an offline run of the market-data client
serves from: `fixtures/tradier/<set>/<endpoint>__<key>.json` under the fixture desk root
`tests/fixtures/desk`. Tools copy the whole desk root to a temporary directory before anything
writes (`cli_journeys.py` does this for the journeys), so nothing here is ever modified by a run.

## Provenance

Every value in these files is **synthetic**. The quote and chain JSON shapes follow the recorded
Tradier sandbox responses kept in the Aegis repository (FairGradient,
`services/api/tests/integrations/tradier/fixtures.py`): the `quotes` -> `quote` object-or-list,
`unmatched_symbols` -> `symbol` as a string or a list, `options` -> `option` list, the field
names and their order, option-only fields, `greeks` null on the sandbox (the sandbox publishes
no Greeks or implied volatility and no index quotes). The `expirations`, `strikes`, `history`,
`clock` and `calendar` shapes follow Tradier's public API documentation as known at authoring.
The error bodies are the recorded rate-limit fault (`quota_violation.txt`, an HTTP 400 whose
text contains "Quota Violation") and the sandbox's answer to a bad credential
(`invalid_token.txt`, HTTP 401). No live capture has been made yet: the live sandbox smoke in
PR 4 (plan section 14) replaces or confirms these values, and `docs/PROVENANCE.md` records it.

No credential appears anywhere in this tree. Offline runs load no token; the test suite scans
every file here for a token-shaped string.

## File naming: the OfflineTransport key

The transport maps a request to one file by endpoint and a deterministic key:

| Endpoint | File | Key |
| --- | --- | --- |
| `/v1/markets/quotes` | `quotes__<SYMBOLS>.json` | the requested symbols, `upper`-cased, `sorted` (Python's default string order), comma-joined: `",".join(sorted(s.upper() for s in symbols))`. The `greeks` flag is not part of the key. |
| `/v1/markets/options/chains` | `chains__<SYMBOL>__<YYYY-MM-DD>.json` | underlying and expiration; the `greeks` flag is not part of the key |
| `/v1/markets/options/expirations` | `expirations__<SYMBOL>.json` | underlying; `includeAllRoots` is not part of the key |
| `/v1/markets/options/strikes` | `strikes__<SYMBOL>__<YYYY-MM-DD>.json` | underlying and expiration |
| `/v1/markets/history` | `history__<SYMBOL>__<START>__<END>.json` | symbol and the `start`/`end` dates; daily interval only |
| `/v1/markets/clock` | `clock.json` | none |
| `/v1/markets/calendar` | `calendar__<YYYY>-<MM>.json` | year and zero-padded month |
| error bodies | `errors/quota_violation.txt`, `errors/invalid_token.txt` | served by tests that need a 400 or 401 body |

Examples: `SPY WMT WMT261120C00105000` -> `quotes__SPY,WMT,WMT261120C00105000.json`; the six
symbols of the fixture book -> `quotes__NVDA,NVDA261120C00150000,SPY,SPY261218P00620000,WMT,WMT261120C00105000.json`.
A request whose key has no file is `TradierError("network")` with the message
`no offline fixture for <path>?<key>`. Request a symbol twice and no file matches.

Quote subsets present in every set: `SPY`, `WMT`, `NVDA`, `SPY,WMT`, `NVDA,SPY,WMT`, each option
alone (`WMT261120C00105000`, `NVDA261120C00150000`, `SPY261218P00620000`), each underlying with
its option (`WMT,WMT261120C00105000`, `NVDA,NVDA261120C00150000`, `SPY,SPY261218P00620000`),
`SPY,WMT,WMT261120C00105000` and the six-symbol file. The `regular` set adds `SPX` (an index:
the sandbox cannot quote it, so the body is `{"quotes": {"unmatched_symbols": {"symbol": "SPX"}}}`)
and `SPX,SPY` (one match as an object plus one unmatched symbol).

## The sets and their instants

2026-11-01 ended daylight time, so every instant below is Eastern Standard Time, ET = UTC-5.
Within a set every quote carries the same stamp on `trade_date`, `bid_date` and `ask_date`
(one as-of for every leg); `--now` is the instant the tools and journeys pass, and it is also
the clock's `timestamp` (the vendor's clock is read at the fetch).

| Set | Quotes of | Quote stamp (ET) | Quote stamp (UTC) | `--now` (UTC) | `--now` (ET) | `clock.json` state | Exercises |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `regular` | 2026-11-03 | 2026-11-03 14:31:12 | 2026-11-03T19:31:12Z | 2026-11-03T19:50:00Z | 14:50:00 | `open` | regular hours; quotes 18 min 48 s old, fresh under the 25-minute bound |
| `stale` | 2026-11-03 | 2026-11-03 13:50:00 | 2026-11-03T18:50:00Z | 2026-11-03T19:50:00Z | 14:50:00 | `open` | regular hours; quotes 60 min old: stale, UNAVAILABLE for rules |
| `after_close` | 2026-11-03 | 2026-11-03 15:59:40 | 2026-11-03T20:59:40Z | 2026-11-03T21:25:00Z | 16:25:00 | `postmarket` | after the close (16:00 ET = 2026-11-03T21:00:00Z): "as of close 16:00 ET"; the stamp is 20 s before the close |
| `pre_open` | 2026-11-02 | 2026-11-02 15:59:55 | 2026-11-02T20:59:55Z | 2026-11-03T13:00:00Z | 08:00:00 | `premarket` | before the open; judged against the prior close 2026-11-02 16:00 ET = 2026-11-02T21:00:00Z (5 s after the stamp) |
| `holiday` | 2026-11-25 | 2026-11-25 15:59:50 | 2026-11-25T20:59:50Z | 2026-11-26T15:00:00Z | 10:00:00 | `closed` | Thanksgiving Day: "NYSE closed"; no quote fetch is expected (the files exist so a fetch that does happen is graded, not crashed) |

### Every epoch value, computed with Python

Quote stamps are epoch milliseconds (`int(datetime(..., tzinfo=timezone.utc).timestamp() * 1000)`),
the clock stamp is epoch seconds (`int(datetime(..., tzinfo=timezone.utc).timestamp())`); the ET
readings are `aegis_desk.market_clock.to_et` of the same instants.

| Value | UTC instant | ET reading | Where |
| --- | --- | --- | --- |
| 1793734272000 | 2026-11-03T19:31:12Z | 2026-11-03 14:31:12 EST | `regular`: every `*_date` in quotes and chains |
| 1793731800000 | 2026-11-03T18:50:00Z | 2026-11-03 13:50:00 EST | `stale`: every `*_date` |
| 1793739580000 | 2026-11-03T20:59:40Z | 2026-11-03 15:59:40 EST | `after_close`: every `*_date` |
| 1793653195000 | 2026-11-02T20:59:55Z | 2026-11-02 15:59:55 EST | `pre_open`: every `*_date` |
| 1795640390000 | 2026-11-25T20:59:50Z | 2026-11-25 15:59:50 EST | `holiday`: every `*_date` |
| 1793735400 | 2026-11-03T19:50:00Z | 2026-11-03 14:50:00 EST | `regular` and `stale`: `clock.json` timestamp (= `--now`) |
| 1793741100 | 2026-11-03T21:25:00Z | 2026-11-03 16:25:00 EST | `after_close`: `clock.json` timestamp (= `--now`) |
| 1793710800 | 2026-11-03T13:00:00Z | 2026-11-03 08:00:00 EST | `pre_open`: `clock.json` timestamp (= `--now`) |
| 1795705200 | 2026-11-26T15:00:00Z | 2026-11-26 10:00:00 EST | `holiday`: `clock.json` timestamp (= `--now`) |
| 1793739600 | 2026-11-03T21:00:00Z | 2026-11-03 16:00:00 EST | the 2026-11-03 close the `after_close` set is graded against (not in a file) |
| 1793653200 | 2026-11-02T21:00:00Z | 2026-11-02 16:00:00 EST | the prior close the `pre_open` set is graded against (not in a file) |

## Symbols and prices

Underlyings SPY (ETF), WMT and NVDA (stocks); options `WMT261120C00105000` (a covered-call short
leg), `NVDA261120C00150000` and `SPY261218P00620000` (a protective put). 2026-11-20 and 2026-12-18
are the standard monthly expirations (third Fridays). Prices of one trading day are identical in
every set of that day; only the stamps and the volumes differ (`stale` < `regular` < `after_close`
volume, the day's activity accumulating). `change` is `last - prevclose` exactly and
`change_percentage` is that change over `prevclose` in percent, rounded half-even to two decimals.
`close` is null while the session is open (`regular`, `stale`) and equals `last` after a close
(`after_close`, `pre_open`, `holiday`).

| Day (sets) | Symbol | bid / ask | last | prevclose | open / high / low |
| --- | --- | --- | --- | --- | --- |
| 2026-11-03 (`regular`, `stale`, `after_close`) | SPY | 648.10 / 648.14 | 648.12 | 645.90 | 646.40 / 649.05 / 645.70 |
| | WMT | 104.20 / 104.26 | 104.23 | 103.80 | 103.95 / 104.60 / 103.70 |
| | NVDA | 148.90 / 148.95 | 148.92 | 147.60 | 147.80 / 149.70 / 147.10 |
| | WMT261120C00105000 | 3.35 / 3.50 | 3.40 | 3.15 | 3.20 / 3.55 / 3.10 |
| | NVDA261120C00150000 | 3.10 / 3.30 | 3.20 | 2.85 | 2.90 / 3.40 / 2.80 |
| | SPY261218P00620000 | 9.80 / 10.10 | 9.95 | 10.45 | 10.30 / 10.60 / 9.70 |
| 2026-11-02 closing quotes (`pre_open`) | SPY | 645.88 / 645.92 | 645.90 | 643.15 | 644.20 / 646.75 / 642.90 |
| | WMT | 103.78 / 103.84 | 103.80 | 103.25 | 103.30 / 104.10 / 102.95 |
| | NVDA | 147.58 / 147.63 | 147.60 | 146.10 | 146.50 / 148.20 / 145.80 |
| | WMT261120C00105000 | 3.10 / 3.25 | 3.15 | 3.00 | 3.00 / 3.25 / 2.95 |
| | NVDA261120C00150000 | 2.75 / 2.95 | 2.85 | 2.60 | 2.65 / 2.95 / 2.55 |
| | SPY261218P00620000 | 10.30 / 10.60 | 10.45 | 11.10 | 11.00 / 11.20 / 10.25 |
| 2026-11-25 closing quotes (`holiday`) | SPY | 651.38 / 651.44 | 651.40 | 650.20 | 650.60 / 652.10 / 649.80 |
| | WMT | 105.92 / 105.98 | 105.95 | 105.60 | 105.70 / 106.20 / 105.35 |
| | NVDA | 152.27 / 152.33 | 152.30 | 151.80 | 152.00 / 153.10 / 151.20 |
| | SPY261218P00620000 | 6.00 / 6.20 | 6.10 | 6.45 | 6.40 / 6.55 / 5.95 |

The days link up: the 2026-11-03 `prevclose` values are the 2026-11-02 closes of the `pre_open`
set, which are the last bars of `history__<SYMBOL>__2026-10-27__2026-11-02.json` (five daily
bars, identical in every set; the 2026-11-02 bar's open/high/low/close/volume equal the
`pre_open` quote's). In the `holiday` set the two November-20 options have expired: they are
reported in `unmatched_symbols` (a string for one symbol, a list for two), their chains are
`{"options": null}` and their strike lists `{"strikes": null}`; the December put is still alive.
The `holiday` set also carries the expiration-day bars `history__WMT__2026-11-20__2026-11-20.json`
(close 104.86, within the 0.25 pin band of the 105 strike) and
`history__NVDA__2026-11-20__2026-11-20.json` (close 153.40, the 150 call 3.40 in the money);
a single day comes back as an object under `day`, not a list.

## Chains: Black-Scholes-Merton consistent with the anchor quote

Each `chains__` file holds five strikes with a call and a put each (WMT 100 to 110 for
2026-11-20, NVDA 140 to 160 for 2026-11-20, SPY 600 to 640 for 2026-12-18). The anchor contract
(the option quoted above) is copied into the chain unchanged plus `"greeks": null`; its
neighbours are priced with `aegis_desk.bsm` at the implied volatility `aegis_desk.iv.implied_vol`
solves from the anchor mid, with `r` = 0.0412 (the fixture `desk.toml` rate), `q` = 0, spot = the
underlying's mid and tau = seconds from the quote stamp to 16:00 ET on the expiration date over
a 365-day year. Model mids are rounded to the 0.05 tick (floor 0.05), the spread is 0.10 (0.20
above 5.00) centred on the mid, `last` = mid, `prevclose` is the same model at the prior close
spot one day earlier, `open` at the day's opening spot. Because tau differs between the three
2026-11-03 sets, a neighbour can drift by one tick with theta between `stale`, `regular` and
`after_close`; the anchors never change.

| Set | Chain | Spot (mid) | Anchor mid | tau (seconds / years) | Solved sigma |
| --- | --- | --- | --- | --- | --- |
| `regular` | WMT 2026-11-20 | 104.23 | 3.425 | 1474128 / 0.046744 | 0.4108 |
| `regular` | NVDA 2026-11-20 | 148.925 | 3.20 | 1474128 / 0.046744 | 0.2780 |
| `regular` | SPY 2026-12-18 | 648.12 | 9.95 | 3893328 / 0.123457 | 0.2501 |
| `stale` | WMT / NVDA / SPY | as `regular` | as `regular` | 1476600 and 3895800 | 0.4104 / 0.2777 / 0.2500 |
| `after_close` | WMT / NVDA / SPY | as `regular` | as `regular` | 1468820 and 3888020 | 0.4116 / 0.2785 / 0.2503 |
| `pre_open` | WMT 2026-11-20 | 103.81 | 3.175 | 1555205 / 0.049315 | 0.3943 |
| `pre_open` | NVDA 2026-11-20 | 147.605 | 2.85 | 1555205 / 0.049315 | 0.2889 |
| `pre_open` | SPY 2026-12-18 | 645.90 | 10.45 | 3974405 / 0.126028 | 0.2463 |
| `holiday` | SPY 2026-12-18 | 651.41 | 6.10 | 1987210 / 0.063014 | 0.2831 |

## Expirations, strikes, history, clock, calendar

* `expirations__<SYMBOL>.json`: `{"expirations": {"date": [...]}}`, ascending ISO dates, all NYSE
  trading days after the set's quote day (2027-06-17 is a Thursday because 2027-06-18, the
  observed Juneteenth, is a holiday). The 2026-11-03 and 2026-11-02 lists are identical; the
  `holiday` lists start at 2026-11-27.
* `strikes__<SYMBOL>__<YYYY-MM-DD>.json`: `{"strikes": {"strike": [JSON numbers]}}`, ascending,
  a superset of the chain's strikes.
* `history__...`: `{"history": {"day": [...]}}` with `date, open, high, low, close, volume`.
* `clock.json`: `{"clock": {"date", "description", "state", "timestamp", "next_change", "next_state"}}`
  with `timestamp` in epoch SECONDS (the only endpoint that uses seconds) equal to the set's `--now`;
  `state` is `open`, `postmarket`, `premarket` or `closed`, and `aegis_desk.market_clock.reconcile_vendor_state`
  agrees with the local session at every set's `--now`.
* `calendar__2026-11.json`: `{"calendar": {"month": 11, "year": 2026, "days": {"day": [...]}}}`, one
  entry per calendar day; open days carry `premarket` 07:00-09:24, `open` 09:30-16:00 and
  `postmarket` 16:00-20:00 (13:00 and 13:00-17:00 on 2026-11-27, the early close); closed days
  (weekends and Thanksgiving 2026-11-26) carry only `date`, `status` and `description`.

## Regenerating

`_generate.py` beside this file rebuilds every set from the price books above and prints each
set's stamps: `python3 tests/fixtures/desk/fixtures/tradier/_generate.py` from the repository
root (it imports `aegis_desk` from `scripts/`). It is data tooling for this fixture tree, not a
desk tool, and it is excluded from the repository gates like the rest of `tests/fixtures/`.
`tests/engine/test_desk_fixtures.py` checks the generated tree against the facts in this README.
