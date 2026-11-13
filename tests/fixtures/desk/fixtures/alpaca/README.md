# Offline Alpaca paper fixtures for the fixture desk root

These directories are the recorded-response store an offline run of the market-data client
(`scripts/aegis_desk/alpaca_client.py`, `OfflineTransport`) serves from:
`fixtures/alpaca/<set>/<file>.json` under the fixture desk root `tests/fixtures/desk`. Tools copy
the whole desk root to a temporary directory before anything writes (`cli_journeys.py` does this for
the journeys), so nothing here is ever modified by a run.

## Provenance

Every value in these files is **synthetic**. The prices, sizes, open interest, stamps, bars and
expirations are the synthetic world the desk's fixtures have always carried, unchanged; only the
response shapes are Alpaca's. The shapes follow Alpaca's public API reference as read at authoring
(the market-data API on `data.alpaca.markets` for quotes, snapshots and bars; the paper trading API
on `paper-api.alpaca.markets` for the option contract listing, the clock and the calendar): the
field names and their order, RFC 3339 stamps (one `t` per quote, which the client writes as both the
bid and the ask stamp), strike, multiplier, size, open interest and close price as text in the
contract listing, the calendar as a bare JSON list of trading days, `next_page_token` on the
paginated endpoints. Option quotes are those of the free **indicative** feed (15-minute delayed and
not actual exchange quotes: the desk labels them approximate); stock quotes and bars are those of
the **iex** feed (the only stock feed a paper-only account is entitled to), so every stock quote's
exchange is IEX (`V`). The exchange and condition codes of the option quotes and trades (`Z`, `A`,
`I`) are illustrative. No snapshot records vendor Greeks or implied volatility (the desk computes its
own and ignores the vendor's in any case).

The error bodies are modelled, not captured: `errors/unauthorized.txt` is the 401 body
`{"message": "unauthorized."}`, `errors/too_many_requests.txt` a 429 body
`{"message": "too many requests."}` (the client classifies by status, never by this text), and
`errors/subscription.txt` the refusal Alpaca documents for data the plan does not cover
(`{"code": 42210000, "message": "subscription does not permit querying recent SIP data"}`: the
client matches the message text, because the published code numbers disagree). No live capture has
been made: the live paper smoke replaces or confirms these shapes, and `docs/PROVENANCE.md` records it.

No credential appears anywhere in this tree. Offline runs load no key pair; the test suite scans
every file here for a key-shaped or token-shaped string and for the two credential header names.

## File naming: the OfflineTransport key

`alpaca_client.fixture_file_name(path, params)` maps an allow-listed request to one file:

| Request (host, path) | File | Key |
| --- | --- | --- |
| `data.alpaca.markets` `/v2/stocks/quotes/latest` | `stock_quotes__<SYMBOLS>.json` | the requested equities, `upper`-cased, `sorted` (Python's default string order), comma-joined |
| `data.alpaca.markets` `/v1beta1/options/quotes/latest` | `option_quotes__<SYMBOLS>.json` | the requested OCC symbols (compact form), `upper`-cased, `sorted`, comma-joined |
| `data.alpaca.markets` `/v1beta1/options/snapshots/<UNDERLYING>` | `snapshots__<UNDERLYING>__<YYYY-MM-DD>.json` | underlying and `expiration_date` (`snapshots__<UNDERLYING>.json` without one) |
| `paper-api.alpaca.markets` `/v2/options/contracts`, one expiration | `contracts__<UNDERLYING>__<YYYY-MM-DD>.json` | `underlying_symbols` and `expiration_date` (a chain's listing; also the strikes answer) |
| `paper-api.alpaca.markets` `/v2/options/contracts`, a window | `contracts__<UNDERLYING>__<GTE>__<LTE>.json` | `underlying_symbols`, `expiration_date_gte` and `expiration_date_lte` (the expirations listing) |
| `data.alpaca.markets` `/v2/stocks/bars` | `bars__<SYMBOL>__<START>__<END>.json` | symbol and the `start`/`end` dates; daily timeframe |
| `paper-api.alpaca.markets` `/v2/clock` | `clock.json` | none |
| `paper-api.alpaca.markets` `/v2/calendar` | `calendar__<YYYY-MM-01>__<YYYY-MM-last>.json` | the month's first and last dates |
| error bodies | `errors/unauthorized.txt`, `errors/too_many_requests.txt`, `errors/subscription.txt` | served by tests that need a 401, a 429 or a subscription refusal |

A `page_token` adds `__page-<token>` before `.json` (the token matching `[A-Za-z0-9_-]{1,64}`); no
answer recorded here is paginated, so every body carries `"next_page_token": null`. The feed, the
contract `status` and `type`, the `limit`, the `timeframe` and the `adjustment` are not part of the
key. A request whose key has no file fails with status `network` and the message
`no offline fixture for <path>?<key>`.

The client sends the equities of a quote batch and its options to their own endpoints, so the
six-symbol batch of the fixture book reads `stock_quotes__NVDA,SPY,WMT.json` and
`option_quotes__NVDA261120C00150000,SPY261218P00620000,WMT261120C00105000.json`, and
`SPY WMT WMT261120C00105000` reads `stock_quotes__SPY,WMT.json` and
`option_quotes__WMT261120C00105000.json`. Quote files present in every set: `stock_quotes__` `SPY`,
`WMT`, `NVDA`, `SPY,WMT`, `NVDA,SPY,WMT`; `option_quotes__` each option alone
(`WMT261120C00105000`, `NVDA261120C00150000`, `SPY261218P00620000`) and the three together. The
`regular` set adds `stock_quotes__SPX.json` (an index: the data source cannot quote it, so the body
is `{"quotes": {}, "currency": "USD"}`) and `stock_quotes__SPX,SPY.json` (SPY alone in the map).
Alpaca echoes no list of unknown symbols: a symbol it does not quote is absent from `quotes`, and
the client reports it as not returned.

A chain costs two requests: the contract listing (`contracts__<U>__<DATE>.json`) and the snapshots
(`snapshots__<U>__<DATE>.json`), which hold the same contracts. The expirations answer is the
listing of active calls from `start` through `start + 400` days, where `start` is the ET date of the
set's `--now` (the date a tool passes to `option_expirations`); it holds one call per expiration
here (the listed strike nearest the set's spot: WMT 105 or 107.5, NVDA 150 or 155, SPY 650), because the
client reads only `expiration_date`. A contract that is also in a chain listing of the set is the
same object, open interest included; the others carry null open interest and close price.

| Set | Expirations listing window (`<GTE>__<LTE>`) |
| --- | --- |
| `regular`, `stale`, `after_close`, `pre_open` | `2026-11-03__2027-12-08` |
| `holiday` | `2026-11-26__2027-12-31` |
| `t1` | `2026-11-19__2027-12-24` |
| `expiry_day` | `2026-11-20__2027-12-25` |
| `post_expiry` | `2026-11-21__2027-12-26` |

## The sets and their instants

2026-11-01 ended daylight time, so every instant below is Eastern Standard Time, ET = UTC-5.
Within a set every quote and trade carries the same stamp `t` (one as-of for every leg); `--now`
is the instant the tools and journeys pass, and it is also the clock's `timestamp` (the vendor's
clock is read at the fetch). The clock reports only `is_open` (regular hours) and the next regular
open and close: there is no pre- or post-market state.

| Set | Quotes of | Quote stamp (ET) | Quote stamp `t` | `--now` (UTC) | `--now` (ET) | `is_open` | Exercises |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `regular` | 2026-11-03 | 2026-11-03 14:31:12 | 2026-11-03T19:31:12Z | 2026-11-03T19:50:00Z | 14:50:00 | true | regular hours; quotes 18 min 48 s old, fresh under the 25-minute bound |
| `stale` | 2026-11-03 | 2026-11-03 13:50:00 | 2026-11-03T18:50:00Z | 2026-11-03T19:50:00Z | 14:50:00 | true | regular hours; quotes 60 min old: stale, UNAVAILABLE for rules |
| `after_close` | 2026-11-03 | 2026-11-03 15:59:40 | 2026-11-03T20:59:40Z | 2026-11-03T21:25:00Z | 16:25:00 | false | after the close (16:00 ET = 2026-11-03T21:00:00Z): "as of close 16:00 ET"; the stamp is 20 s before the close |
| `pre_open` | 2026-11-02 | 2026-11-02 15:59:55 | 2026-11-02T20:59:55Z | 2026-11-03T13:00:00Z | 08:00:00 | false | before the open; judged against the prior close 2026-11-02 16:00 ET = 2026-11-02T21:00:00Z (5 s after the stamp) |
| `holiday` | 2026-11-25 | 2026-11-25 15:59:50 | 2026-11-25T20:59:50Z | 2026-11-26T15:00:00Z | 10:00:00 | false | Thanksgiving Day: "NYSE closed"; no quote fetch is expected (the files exist so a fetch that does happen is graded, not crashed); the next close is the 13:00 ET early close of 2026-11-27 |
| `t1` | 2026-11-19 | 2026-11-19 14:31:12 | 2026-11-19T19:31:12Z | 2026-11-19T19:50:00Z | 14:50:00 (Thursday) | true | T-1 of the 2026-11-20 expiration (DTE 1 trading day): the T-1 preview, the assignment watch (the WMT ex-dividend 2026-11-24 falls after the call's last trading day, so no dividend tier is listed; the NVDA pin fires) and the pin tiers; quotes 18 min 48 s old |
| `expiry_day` | 2026-11-20 | 2026-11-20 14:31:12 | 2026-11-20T19:31:12Z | 2026-11-20T19:50:00Z | 14:50:00 (Friday) | true | expiration day of the two November contracts (DTE 0, last trading day 2026-11-20): the Final Call; act-by is the broker cutoff 16:30 ET = 21:30 UTC; quotes 18 min 48 s old |
| `post_expiry` | 2026-11-20 (closing quotes) | 2026-11-20 15:59:50 | 2026-11-20T20:59:50Z | 2026-11-21T14:30:00Z | 09:30:00 (Saturday) | false | the Saturday post-expiry routine: weekend, "NYSE closed", no quote fetch; the quotes are Friday's closing quotes, 10 s before the 16:00 ET close (2026-11-20T21:00:00Z); the bars files carry the expiration-day bars the reconciliation reads |

### Every epoch value, computed with Python

The files hold RFC 3339 text; the client converts a quote stamp to epoch milliseconds
(`int(datetime(..., tzinfo=timezone.utc).timestamp() * 1000)`) and the clock to epoch seconds. The
ET readings are `aegis_desk.market_clock.to_et` of the same instants.

| Value | UTC instant | ET reading | Where |
| --- | --- | --- | --- |
| 1793734272000 | 2026-11-03T19:31:12Z | 2026-11-03 14:31:12 EST | `regular`: every quote and trade `t` |
| 1793731800000 | 2026-11-03T18:50:00Z | 2026-11-03 13:50:00 EST | `stale`: every `t` |
| 1793739580000 | 2026-11-03T20:59:40Z | 2026-11-03 15:59:40 EST | `after_close`: every `t` |
| 1793653195000 | 2026-11-02T20:59:55Z | 2026-11-02 15:59:55 EST | `pre_open`: every `t` |
| 1795640390000 | 2026-11-25T20:59:50Z | 2026-11-25 15:59:50 EST | `holiday`: every `t` |
| 1795116672000 | 2026-11-19T19:31:12Z | 2026-11-19 14:31:12 EST | `t1`: every `t` |
| 1795203072000 | 2026-11-20T19:31:12Z | 2026-11-20 14:31:12 EST | `expiry_day`: every `t` |
| 1795208390000 | 2026-11-20T20:59:50Z | 2026-11-20 15:59:50 EST | `post_expiry`: every `t` |
| 1793735400 | 2026-11-03T19:50:00Z | 2026-11-03 14:50:00 EST | `regular` and `stale`: `clock.json` timestamp (= `--now`) |
| 1793741100 | 2026-11-03T21:25:00Z | 2026-11-03 16:25:00 EST | `after_close`: `clock.json` timestamp (= `--now`) |
| 1793710800 | 2026-11-03T13:00:00Z | 2026-11-03 08:00:00 EST | `pre_open`: `clock.json` timestamp (= `--now`) |
| 1795705200 | 2026-11-26T15:00:00Z | 2026-11-26 10:00:00 EST | `holiday`: `clock.json` timestamp (= `--now`) |
| 1795117800 | 2026-11-19T19:50:00Z | 2026-11-19 14:50:00 EST | `t1`: `clock.json` timestamp (= `--now`) |
| 1795204200 | 2026-11-20T19:50:00Z | 2026-11-20 14:50:00 EST | `expiry_day`: `clock.json` timestamp (= `--now`) |
| 1795271400 | 2026-11-21T14:30:00Z | 2026-11-21 09:30:00 EST | `post_expiry`: `clock.json` timestamp (= `--now`) |
| 1793739600 | 2026-11-03T21:00:00Z | 2026-11-03 16:00:00 EST | the 2026-11-03 close the `after_close` set is graded against (not in a file) |
| 1793653200 | 2026-11-02T21:00:00Z | 2026-11-02 16:00:00 EST | the prior close the `pre_open` set is graded against (not in a file) |
| 1795208400 | 2026-11-20T21:00:00Z | 2026-11-20 16:00:00 EST | the 2026-11-20 close: the exercise cutoff of the November contracts, 10 s after the `post_expiry` stamp, the prior close the `post_expiry` set is graded against (not in a file) |

The clock writes ET with its offset: `regular` reads
`{"timestamp": "2026-11-03T14:50:00-05:00", "is_open": true, "next_open": "2026-11-04T09:30:00-05:00", "next_close": "2026-11-03T16:00:00-05:00"}`.
Daily bars are stamped 00:00 ET (`2026-10-27T04:00:00Z` in daylight time, `2026-11-02T05:00:00Z`
after it).

## Symbols and prices

Underlyings SPY (ETF), WMT and NVDA (stocks); options `WMT261120C00105000` (a covered-call short
leg), `NVDA261120C00150000` and `SPY261218P00620000` (a protective put). 2026-11-20 and 2026-12-18
are the standard monthly expirations (third Fridays). Prices of one trading day are identical in
every set of that day; only the stamps and the option volumes differ (`stale` < `regular` <
`after_close` daily volume, the day's activity accumulating).

Where each value lives: bid / ask (and the sizes) are every quote's `bp` / `ap` (`bs` / `as`); an
option's last is its snapshot's `latestTrade` price and its daily bar's close, its open / high / low
and volume are the snapshot's `dailyBar`, and its prevclose is the contract listing's `close_price`
(dated the prior trading day, like `open_interest`). A stock's last, prevclose, open / high / low and
volume are the generator's book: no stock snapshot endpoint is allow-listed, so no file carries them
except through the bars below (the chains are priced from them).

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
| 2026-11-19 (`t1`) | SPY | 652.10 / 652.14 | 652.12 | 650.80 | 651.00 / 652.60 / 650.50 |
| | WMT | 106.40 / 106.46 | 106.43 | 105.90 | 106.00 / 106.70 / 105.80 |
| | NVDA | 149.20 / 149.25 | 149.22 | 150.10 | 149.90 / 150.40 / 148.80 |
| | WMT261120C00105000 | 1.52 / 1.60 | 1.56 | 1.25 | 1.30 / 1.70 / 1.20 |
| | NVDA261120C00150000 | 1.00 / 1.10 | 1.05 | 1.60 | 1.45 / 1.55 / 0.95 |
| | SPY261218P00620000 | 3.80 / 4.00 | 3.90 | 4.35 | 4.25 / 4.40 / 3.75 |
| 2026-11-20 at 14:31 ET (`expiry_day`) | SPY | 650.00 / 650.04 | 650.02 | 652.12 | 651.80 / 652.40 / 649.60 |
| | WMT | 104.90 / 104.96 | 104.93 | 106.43 | 105.10 / 105.60 / 104.70 |
| | NVDA | 153.30 / 153.36 | 153.33 | 149.22 | 150.20 / 153.80 / 149.90 |
| | WMT261120C00105000 | 0.12 / 0.16 | 0.14 | 1.56 | 0.55 / 0.65 / 0.10 |
| | NVDA261120C00150000 | 3.30 / 3.42 | 3.36 | 1.05 | 0.95 / 3.60 / 0.55 |
| | SPY261218P00620000 | 4.10 / 4.30 | 4.20 | 3.90 | 3.95 / 4.35 / 3.85 |
| 2026-11-20 closing quotes (`post_expiry`) | SPY | 650.00 / 650.04 | 650.02 | 652.12 | 651.80 / 652.40 / 649.60 |
| | WMT | 104.38 / 104.42 | 104.40 | 106.43 | 105.10 / 105.60 / 104.20 |
| | NVDA | 153.38 / 153.42 | 153.40 | 149.22 | 150.20 / 153.80 / 149.90 |
| | WMT261120C00105000 | 0.01 / 0.05 | 0.03 | 1.56 | 0.55 / 0.65 / 0.01 |
| | NVDA261120C00150000 | 3.30 / 3.45 | 3.38 | 1.05 | 0.95 / 3.60 / 0.55 |
| | SPY261218P00620000 | 4.10 / 4.30 | 4.20 | 3.90 | 3.95 / 4.35 / 3.85 |

The days link up: the 2026-11-03 prevclose values are the 2026-11-02 closes of the `pre_open`
book, which are the last bars of `bars__<SYMBOL>__2026-10-27__2026-11-02.json` (five daily bars,
byte-identical in every set; the 2026-11-02 bar's open/high/low/close/volume equal the `pre_open`
book's). In the expiry week the `expiry_day` and `post_expiry` prevclose values are the `t1` lasts
(the option prevclose values of `t1` itself, 1.25 / 1.60 / 4.35, are the fixture's own 2026-11-18
closes: the addendum fixes bid/ask/last only). The `post_expiry` closing book of WMT and NVDA
equals the 2026-11-20 bars of `bars__WMT__2026-11-20__2026-11-20.json` (o 105.10 h 105.60 l 104.20
c 104.40 v 9100000: the 105 call expired 0.60 out of the money, outside the 0.25 pin band) and
`bars__NVDA__2026-11-20__2026-11-20.json` (o 150.20 h 153.80 l 149.90 c 153.40 v 41000000: the 150
call finished 3.40 in the money); the intraday `expiry_day` option bars share the day's open and
never exceed the day's range or volume. Monday 2026-11-23's closing quotes live in the
reconciliation root `tests/fixtures/desk_recon` (set `recon`, SPY prevclose 650.02 = the
`post_expiry` last).

In the `holiday` set the two November-20 options have expired: they are absent from the
`option_quotes__` maps (`{"quotes": {}}` alone; the December put alone in the three-option file),
their chain listings and snapshots are empty (`{"option_contracts": [], ...}`,
`{"snapshots": {}, ...}`), and the December put is still alive. In `t1` and `expiry_day` the
November contracts are still quoted and listed: the expirations listings of those sets start at
2026-11-20 (a contract stays listed through its last session), and the `expiry_day` / `post_expiry`
option quotes are on contracts that expire on the quote day. The `post_expiry` listing starts at
Saturday's date, 2026-11-21, so the expired November contracts are no longer in it (SPY's first
listed date is 2026-11-23, WMT's and NVDA's 2026-11-27); the 2026-11-20 chains of that set are still
served (Friday's closing quotes) so a chain request on it is graded, not crashed.

### Bars: the `{"bars": {"<SYMBOL>": [...]}, "next_page_token": null}` shape

Alpaca answers a list of bars for one bar or several. Every bar is `t, o, h, l, c, v, n, vw`: `t`
at 00:00 ET of the bar's date; `n` (the trade count) is `max(1, v // 100)` and `vw` the typical price
`(h + l + c) / 3` rounded half-even to 0.0001, both stated synthetic values the desk does not read
(an option snapshot's `dailyBar` uses `max(1, v // 10)`). The five-bar `2026-10-27..2026-11-02`
files are identical in every set. The expiration bars PR 2b's reconciliation reads:

| File (sets) | Bars (date o / h / l / c / v) | Reads as |
| --- | --- | --- |
| `bars__WMT__2026-11-20__2026-11-20.json` (`holiday`) | 2026-11-20 104.60 / 105.30 / 104.20 / 104.86 / 18700000 | the PR 2a single-day values, unchanged: close 0.14 inside the 0.25 pin band |
| `bars__NVDA__2026-11-20__2026-11-20.json` (`holiday`) | 2026-11-20 151.20 / 153.90 / 150.80 / 153.40 / 260000000 | the PR 2a single-day values, unchanged: the 150 call 3.40 in the money |
| `bars__WMT__2026-11-20__2026-11-23.json` (`holiday`, and `recon` in the reconciliation root) | 2026-11-20 104.60 / 105.20 / 104.30 / 104.86 / 9100000; 2026-11-23 105.30 / 105.90 / 104.95 / 105.62 / 8400000 | PIN_UNCERTAIN: 104.86 is 0.14 inside the band and Monday opened at 105.30, across the strike (the after-hours-crossing proxy) |
| `bars__NVDA__2026-11-20__2026-11-23.json` (`holiday`, `recon`) | 2026-11-20 150.20 / 153.80 / 149.90 / 153.40 / 41000000; 2026-11-23 152.90 / 154.40 / 152.10 / 154.10 / 38000000 | PROBABLE_ASSIGNED: 3.40 in the money and Monday opened on the same side |
| `bars__WMT__2026-11-20__2026-11-20.json` (`post_expiry`) | 2026-11-20 105.10 / 105.60 / 104.20 / 104.40 / 9100000 | EXPIRED_OTM: the 105 call expired 0.60 out of the money |
| `bars__NVDA__2026-11-20__2026-11-20.json` (`post_expiry`) | 2026-11-20 150.20 / 153.80 / 149.90 / 153.40 / 41000000 | PROBABLE_ASSIGNED: the 150 call finished 3.40 in the money |

The single-day `holiday` files keep their PR 2a open/high/low/volume (the addendum keeps them
unchanged) while the two-bar files carry the addendum's values; only the closes are shared
(104.86 and 153.40). The `post_expiry` WMT world closes at 104.40, not 104.86: the Saturday
routine sees a clean EXPIRED_OTM, the Thanksgiving-week sets a pin.

## Chains: Black-Scholes-Merton consistent with the anchor quote

Each anchor chain holds five strikes with a call and a put each (WMT 100 to 110 for 2026-11-20,
NVDA 140 to 160 for 2026-11-20, SPY 600 to 640 for 2026-12-18): ten contracts in
`contracts__<U>__<DATE>.json` and the same ten in `snapshots__<U>__<DATE>.json`; the structuring
chains of the `regular` and `expiry_day` sets are described in the next section. The anchor
contract's snapshot `latestQuote` is its standalone `option_quotes__` object, unchanged; its
neighbours are priced with `aegis_desk.bsm` at the implied volatility `aegis_desk.iv.implied_vol`
solves from the anchor mid, with `r` = 0.0412 (the fixture `desk.toml` rate), `q` = 0, spot = the
underlying's mid and tau = seconds from the quote stamp to 16:00 ET on the expiration date over a
365-day year. Model mids are rounded to the 0.05 tick (floor 0.05), the spread is 0.10 (0.20 above
5.00) centred on the mid, last = mid, prevclose (`close_price`) is the same model at the prior close
spot one day earlier, the daily bar's open at the day's opening spot. Because tau differs between
the three 2026-11-03 sets, a neighbour can drift by one tick with theta between `stale`, `regular`
and `after_close`; the anchors never change.

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
| `t1` | WMT 2026-11-20 | 106.43 | 1.56 | 91728 / 0.002909 | 0.2510 |
| `t1` | NVDA 2026-11-20 | 149.225 | 1.05 | 91728 / 0.002909 | 0.4337 |
| `t1` | SPY 2026-12-18 | 652.12 | 3.90 | 2510928 / 0.079621 | 0.2137 |
| `expiry_day` | WMT 2026-11-20 | 104.93 | 0.14 | 5328 / 0.000169 | 0.3168 |
| `expiry_day` | NVDA 2026-11-20 | 153.33 | 3.36 | 5328 / 0.000169 | 0.9547 |
| `expiry_day` | SPY 2026-12-18 | 650.02 | 4.20 | 2424528 / 0.076881 | 0.2154 |
| `post_expiry` | WMT 2026-11-20 | 104.40 | 0.03 | 10 / 0.000000 | none: fallback chain (below) |
| `post_expiry` | NVDA 2026-11-20 | 153.40 | 3.375 | 10 / 0.000000 | none: fallback chain (the mid is even below the 3.40 intrinsic) |
| `post_expiry` | SPY 2026-12-18 | 650.02 | 4.20 | 2419210 / 0.076713 | 0.2156 |

Fallback chains: when the anchor is under one hour from its exercise cutoff (the `post_expiry`
set, 10 s before the close) no implied volatility is solved, mirroring the book's `expiring`
rule (under one hour tau gives no IV and no Greeks), and every neighbour is written at
`max(intrinsic, 0.01) + 0.05` per share at the closing spot (bid = mid - 0.05 floored at 0.00,
ask = 2 x mid - bid, last = mid, prevclose and open by the same rule at the prior close and the
open), with one no-arbitrage cap: a contract further out of the money than the anchor is never
worth more than the anchor, so the WMT 107.5 and 110 calls are 0.00 / 0.06 (mid 0.03, the anchor's
mid) rather than 0.06, and the chains stay monotone in strike. A bid of 0.00 is Alpaca's "no bid"
and the client keeps it as 0.

## Structuring chains (PR 3b): no anchor contract, a stated surface

The Structuring Desk's roll analyses, proposals and tickets read chains of later standard monthly
expirations. Seven chains hold them (a `contracts__` listing and a `snapshots__` file each), five in
the `regular` set and two in the `expiry_day` set:

| Set | Files | Strikes (a call and a put each) |
| --- | --- | --- |
| `regular` | `snapshots__WMT__2026-12-18.json`, `snapshots__WMT__2027-01-15.json` | 95, 97.5, 100, 102.5, 105, 107.5, 110, 112.5, 115, 120 (20 contracts) |
| `regular` | `snapshots__NVDA__2026-12-18.json`, `snapshots__NVDA__2027-01-15.json` | 130, 135, 140, 145, 150, 155, 160, 165, 170, 180 (20 contracts) |
| `regular` | `snapshots__SPY__2027-03-19.json` | 580, 590, 600, 610, 620, 625, 630, 640, 650, 660, 680, 700 (24 contracts) |
| `expiry_day` | `snapshots__WMT__2027-01-15.json` | the WMT strikes above (20 contracts) |
| `expiry_day` | `snapshots__NVDA__2027-01-15.json` | the NVDA strikes above (20 contracts) |

Each `snapshots__` file has its `contracts__` twin of the same date. They hold no anchor contract.
Every contract is priced with `aegis_desk.bsm` on the stated implied-volatility surface
`sigma(K) = max(0.05, sigma0 - skew x ln(K / S))`, skewed in log-moneyness and flat in maturity,
with skews 0.25 (WMT), 0.25 (NVDA) and 0.50 (SPY), `q` = 0, `r` = 0.0412, spot `S` = the
underlying's mid in the same set and tau = seconds from the set's quote stamp to 16:00 ET on the
expiration date over a 365-day year (2027-03-19 16:00 ET = 20:00Z, daylight time; the January and
December dates are standard time, 21:00Z). `sigma0` is the implied volatility of the underlying's
anchor contract in the `regular` set (the sigma its anchor chain is priced at). The `expiry_day`
chains keep that `regular` sigma0: on the expiration day the November anchors have 1 h 29 min to
their cutoff and almost no time value (the NVDA 150 call's mid is 0.03 above intrinsic inside a
0.12 spread), so their own solved sigmas (0.3168 for WMT, 0.9547 for NVDA) identify no level for a
56-day surface. The spot, the stamp and tau are the `expiry_day` set's own. The rounding and spread
rules are those of the anchor chains: model mids to the 0.05 tick (floor 0.05), a spread of 0.10
(0.20 at or above 5.00) centred on the mid with the bid floored at 0.00, last = mid, prevclose
(`close_price`) the same surface at the prior close spot one day earlier, the daily bar's open at
the day's opening spot.

| Set | Chain | Spot (mid) | tau (seconds / years) | sigma0 |
| --- | --- | --- | --- | --- |
| `regular` | WMT 2026-12-18 | 104.23 | 3893328 / 0.123457 | 0.4108 |
| `regular` | WMT 2027-01-15 | 104.23 | 6312528 / 0.200169 | 0.4108 |
| `regular` | NVDA 2026-12-18 | 148.925 | 3893328 / 0.123457 | 0.2780 |
| `regular` | NVDA 2027-01-15 | 148.925 | 6312528 / 0.200169 | 0.2780 |
| `regular` | SPY 2027-03-19 | 648.12 | 11752128 / 0.372658 | 0.2501 |
| `expiry_day` | WMT 2027-01-15 | 104.93 | 4843728 / 0.153594 | 0.4108 |
| `expiry_day` | NVDA 2027-01-15 | 153.33 | 4843728 / 0.153594 | 0.2780 |

Every date is listed in that set's expirations listing and is a standard monthly (the settlement
day of the month's third Friday). SPY has no 2027-01-15 chain in any set: `opening_bell.py`'s
tests pin that request as a failure, and the request finds no file. On any other set, or for any
other date, a structuring chain request finds no file and fails like any other missing fixture.
Put-call parity holds to the tick rounding (`|C - P - (S - K e^(-r tau))|` at most 0.05), and a
January contract is worth at least its December twin except a deep in-the-money European put.

## Calendar and clock

* `calendar__2026-11-01__2026-11-30.json`: a bare list of the month's trading days only (weekends
  and Thanksgiving 2026-11-26 are absent; the client fills them in as closed), each
  `date, open, close, session_open, session_close, settlement_date`: `open` 09:30, `close` 16:00
  (13:00 on 2026-11-27, the early close), `session_open` `0400`, `session_close` `2000` (`1700` on
  the early close), `settlement_date` the next trading day. Identical in every set.
* `clock.json`: `timestamp` (= `--now`, ET with its offset), `is_open`, `next_open` (the next 09:30
  ET open) and `next_close` (the next regular close); the client reads `state` `open` or `closed`
  and the next change from it.

## Regenerating

`_generate.py` beside this file rebuilds every set from the price books above and prints each
set's stamps and expirations window: `python3 tests/fixtures/desk/fixtures/alpaca/_generate.py`
from the repository root (it imports `aegis_desk` from `scripts/`). It writes byte-identical files on
CPython 3.9 and 3.13. It is data tooling for this fixture tree, not a desk tool, and it is excluded
from the repository gates like the rest of `tests/fixtures/`. `tests/engine/test_desk_fixtures.py`
checks the generated tree against the facts in this README (and replays every request the tools make
through the client's `OfflineTransport` on a temporary copy). The reconciliation root's generator
(`tests/fixtures/desk_recon/fixtures/alpaca/_generate.py`) imports this one by path, so both roots
share one set of shapes.
