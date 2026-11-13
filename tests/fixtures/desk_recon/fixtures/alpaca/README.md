# Offline Alpaca paper fixtures for the reconciliation desk root

`tests/fixtures/desk_recon` is the second fixture desk root: the one `csv_recon.py` and its tests
run against to exercise every settlement branch of plan section 8 in one book, so the main root
(`tests/fixtures/desk`) stays CLEAN. It holds a byte-identical copy of the main root's
`desk.toml`, its own `positions.csv` (twelve rows, account blank = "default"), a
`research/calendar.toml` that adds the SPX settlement value, and one recorded set, `recon`,
under `fixtures/alpaca/recon/`. Tools copy the whole root to a temporary directory before
anything writes; nothing here is ever modified by a run, and the tools never write
`positions.csv`.

Every value is **synthetic**; the Alpaca response shapes, the file-naming rule
(`stock_quotes__<SYMBOLS>.json` and `option_quotes__<SYMBOLS>.json` with the symbols upper-cased and
sorted, `bars__<SYMBOL>__<START>__<END>.json`, `clock.json`, `errors/`) and the provenance notes are
those of the main root's README (`tests/fixtures/desk/fixtures/alpaca/README.md`). No credential
appears anywhere in this tree.

## The scenario and its instants

The reconciliation runs at `--now 2026-11-24T12:00:00Z`: Tuesday 2026-11-24 07:00 ET, pre-open
(the `preopen` routine's slot), the first session after the Monday that followed the 2026-11-20
monthly expiration. Every instant is Eastern Standard Time (ET = UTC-5).

| Value | UTC instant | ET reading | Where |
| --- | --- | --- | --- |
| 1795467590000 | 2026-11-23T20:59:50Z | 2026-11-23 15:59:50 EST | `recon`: every quote `t` (Monday's closing quotes, 10 s before the close) |
| 1795521600 | 2026-11-24T12:00:00Z | 2026-11-24 07:00:00 EST | `recon`: `clock.json` timestamp `2026-11-24T07:00:00-05:00` (= `--now`), `is_open` false |
| 1795467600 | 2026-11-23T21:00:00Z | 2026-11-23 16:00:00 EST | the prior close the quotes are graded against (not in a file) |
| 1795208400 | 2026-11-20T21:00:00Z | 2026-11-20 16:00:00 EST | the 2026-11-20 close: the exercise cutoff of the November contracts (not in a file) |

`aegis_desk.market_clock.session_at(--now)` is `pre` on a trading day, so the quote check may
fetch; the vendor clock agrees: closed, next open 2026-11-24T09:30:00-05:00, next close
2026-11-24T16:00:00-05:00.

## Files of the `recon` set

| File | Content |
| --- | --- |
| `clock.json` | `{"timestamp": "2026-11-24T07:00:00-05:00", "is_open": false, "next_open": "2026-11-24T09:30:00-05:00", "next_close": "2026-11-24T16:00:00-05:00"}` |
| `stock_quotes__SPY.json` | SPY 651.90 / 651.94 (sizes 300 / 200, iex). The book behind it: last 651.92, prevclose 650.02 (Friday 2026-11-20's close), open / high / low 650.40 / 652.30 / 649.90 (not served: no stock snapshot endpoint is allow-listed) |
| `option_quotes__SPY261218P00620000.json` | SPY261218P00620000 4.05 / 4.25 (sizes 75 / 90, indicative). The book behind it: last 4.15, prevclose 4.20, open / high / low 4.20 / 4.30 / 4.00 |
| `bars__WMT__2026-11-20__2026-11-23.json` | 2026-11-20 o 104.60 h 105.20 l 104.30 c 104.86 v 9100000; 2026-11-23 o 105.30 h 105.90 l 104.95 c 105.62 v 8400000 (byte-identical to the main root's `holiday` file) |
| `bars__NVDA__2026-11-20__2026-11-23.json` | 2026-11-20 o 150.20 h 153.80 l 149.90 c 153.40 v 41000000; 2026-11-23 o 152.90 h 154.40 l 152.10 c 154.10 v 38000000 (byte-identical to the `holiday` file) |
| `bars__AMD__2026-11-20__2026-11-23.json` | 2026-11-20 o 228.10 h 231.60 l 227.80 c 231.00 v 52000000; 2026-11-23 o 229.40 h 230.20 l 227.90 c 228.75 v 47000000 |
| `errors/unauthorized.txt`, `errors/too_many_requests.txt`, `errors/subscription.txt` | the 401, 429 and subscription-refusal bodies, as in every main-root set |

The reconciliation's quote check sends one batch for the unexpired legs (the SPY shares and the
December put); the client sends the equity to `/v2/stocks/quotes/latest` and the option to
`/v1beta1/options/quotes/latest`, so the batch reads the two quote files above. Every bars file
holds two bars (the expiration day and the next session, `start = expiry`,
`end = next_trading_day(expiry)` = 2026-11-23). There is no AAPL bars file on purpose.

## The book and the expected outcome per row

`positions.csv` (header identical to the main root's; one comment line; `account` blank):

| trade_id | Rows | Expected at `--now 2026-11-24T12:00:00Z --fixture-set recon` |
| --- | --- | --- |
| AG-20261015-01 | WMT261120P00095000 x -1 (credit 165.00) | EXPIRED_OTM: WMT closed 104.86 on 2026-11-20, above 95 + 0.01; proposed edit: the row closed at 0 on 2026-11-20 |
| AG-20261020-02 | WMT x 200 (19700.00) and WMT261120C00105000 x -2 (credit 684.00) | PIN_UNCERTAIN: 104.86 is 0.14 inside the 0.25 pin band, and Monday opened at 105.30, across the strike (the after-hours-crossing proxy); "confirm with your broker", no edit |
| AG-20261022-03 | NVDA x 200 (28240.00) and NVDA261120C00150000 x -2 (credit 610.00) | PROBABLE_ASSIGNED: 153.40 is 3.40 in the money and Monday opened on the same side (152.90); proposed edits: the option row closed at 0 on 2026-11-20 and the stock row closed, 200 at 150.00 on 2026-11-20 (the option premium stays in option P/L; the stock is booked at the strike) |
| AG-20261023-04 | AMD261120C00230000 x -1 (credit 520.00, `#assign-ok`) | PIN_UNCERTAIN by the crossing rule: closed 231.00 (1.00 in the money, outside the band) but Monday opened at 229.40, below the strike; no edit |
| AG-20261101-05 | SPX261120C06500000 x 1 (debit 2450.00) | CASH_SETTLED at 23.17 = 6523.17 - 6500 (the calendar's `[[settlement.values]]` for SPX on 2026-11-20, the AM settlement day); proposed edit: the row closed at 23.17 on 2026-11-20; never ASSIGNED or EXERCISED |
| AG-20261101-06 | RUT261120P02200000 x -1 (credit 1850.00) | SETTLEMENT_UNKNOWN: RUT is AM-settled and the calendar records no value; an UNAVAILABLE item, no edit |
| AG-20261105-07 | AAPL261120P00230000 x 2 (debit 1240.00) | EXPIRED_UNRECORDED: no bars file for AAPL; an UNAVAILABLE item, the owner records the outcome |
| AG-20261110-08 | SPY x 100 (63240.00) and SPY261218P00620000 x 1 (debit 1130.00) | unexpired: quoted from `stock_quotes__SPY.json` and `option_quotes__SPY261218P00620000.json`, nothing to reconcile |
| AG-20261001-09 | SPY261016P00600000 x -1, closed 2026-10-14 at 0.55 | closed: ignored by the reconciliation (realized +425.00 stays in the book) |

Verdict UNAVAILABLE (exit 20: RUT, AAPL and the two pins are UNAVAILABLE items) with four proposed
edits listed (WMT put; NVDA option and NVDA stock; SPX). Money follows plan section 8: paid is
positive, received is negative; every option row gives `cost_basis_total` and the two rows that
also give `open_price_per_share` agree with it (`quantity x 100 x premium`).

## The calendar

`research/calendar.toml` is the main root's file plus, inside `[settlement]`, the array of
tables `[[settlement.values]]` with `root = "SPX"`, `date = 2026-11-20`, `value = 6523.17`,
`source = "owner-entered (fixture)"`, `checked_utc = 2026-11-23T12:00:00Z`. No value is recorded
for RUT (SETTLEMENT_UNKNOWN is a verdict, never a quiet zero). The `am_settled_roots`, the symbol
entries (WMT, NVDA, SPY, checked 2026-11-01) and the 2026-11-27 early-close closure are identical
to the main root's.

## Regenerating

`_generate.py` beside this file rebuilds the `recon` set: `python3
tests/fixtures/desk_recon/fixtures/alpaca/_generate.py` from the repository root. It imports
the main generator (`tests/fixtures/desk/fixtures/alpaca/_generate.py`) by path and reuses its
quote, bar, clock and error-body helpers, so the shapes of the two roots cannot drift apart. It is
data tooling, excluded from the repository gates like the rest of `tests/fixtures/`;
`tests/engine/test_desk_fixtures.py` (`ReconRootTest`, and `ClientRoundTripTest` through the
client's `OfflineTransport`) checks this root against the facts above.
