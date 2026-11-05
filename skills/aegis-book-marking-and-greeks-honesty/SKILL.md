---
name: aegis-book-marking-and-greeks-honesty
description: How the read-only Aegis desk marks the book and when it refuses to, for the Aegis Exit Sentinel. The mid and liquidation marks and the mark_source gate, which side each rule reads, time to expiry, spot and days to expiry, implied volatility and its failure statuses, European Greeks in desk units with the dividend-yield assumption, unrealized, realized and day P/L from the desk's own snapshot, percent captured, max profit and loss and breakevens, and what UNAVAILABLE protects the owner from.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: book
---

# Book marking and Greeks honesty

`scripts/book.py` marks every row of `positions.csv` from the desk's quotes and writes the numbers every rule reads.
The Exit Sentinel speaks about the book in Stage 1. It never marks a position itself, never fills a missing mark and
never adds two of the book's numbers. When the book says unavailable, the Sentinel says unavailable.

## Two marks and the gate

- `mark_mid` is `(bid + ask) / 2`.
- `mark_liq` is the bid for a long leg and the ask for a short leg: what closing the leg would face.
- `mark_source` is `mid` only when the quote's grade is A to D and both sides are fresh. Otherwise it is
  `unavailable`.

Every P/L figure and every loss reading is gated on `mark_source`, never on the grade alone. A grade E quote (a zero
bid, or a spread above 25 %) keeps its mid for display only: its leg has no P/L, and its group's totals are
unavailable. The last trade price and model prices never feed a P/L figure or a rule.

## Which side each rule reads

The book also keeps each leg's fresh sides, `fresh_bid` and `fresh_ask`, for any grade A to E. The rules use them in
one way only:

- A take-profit rule on the liquidation basis reads the fresh conservative side, a short leg at its fresh ask and a
  long leg at its fresh bid. A wide spread can only delay a take-profit, never cause one. On a leg whose `mark_source` is not
  `mid` the alert notes `quote grade <G>: the conservative side stands`.
- A fresh ask of 0 is no offer, not a price of zero: the read is unavailable, `quote grade <G>: no offer (ask 0)`.
- Every other reading of a mark (a loss test, a P/L figure, a rule on the mid basis) needs `mark_source` `mid`.
  Without it the rule is unavailable: `quote grade <G>: a loss test needs a two-sided fresh quote`.

On the synthetic expiry_day set (2026-11-20 19:50 UTC) the WMT call is quoted 0.12/0.16, a spread of 28.57 %: grade
E. The book keeps the display mid 0.14, gives no liquidation mark and no P/L, and keeps both fresh sides. The
take-profit rule reads the ask 0.16 against the 3.42 credit, and the book check's alert reads:

```text
EXIT SIGNAL AG-20261020-01 | WMT covered call | your rule TP80 (rules 1.0.0, sha cc09859c) fired: credit captured 95.32 % (your threshold: at or above 80 %); quote grade E: the conservative side stands | already past this threshold at the desk's first observation | quote grade E, Tradier sandbox, 15-min delayed, as of 19:31 UTC | your plan: close | not an instruction to trade; you act in your brokerage account.
```

In the same run the portfolio's day-P/L rule is unavailable for that leg:

```text
unavailable: your rule DAILY: day P/L unavailable: row 4 WMT261120C00105000: no usable mark (quote grade E, fresh)
```

## Time, spot and days to expiry

- Time to expiry (`tau_years`) runs from the option quote's own stamp (the older of its bid and ask stamps) to the
  exercise cutoff: 16:00 ET on the last trading day for a PM-settled option, 09:30 ET on the settlement morning for an
  AM-settled one. It is counted in seconds over a 365-day year, with no floor.
- With under one hour left, `iv_status` is `expiring`: no IV and no Greeks.
- Spot is the underlying's usable mid stamped within 60 seconds of the option quote. A spot with no usable mark is
  `spot_unavailable`; one stamped further away is `spot_unaligned`. The last trade is never a spot.
- Days to expiry are counted to the last trading day on America/New_York dates, two ways: calendar days and trading
  days. `book.py` prints them as calendar/trading, for example `DTE 17/13` for the WMT call on 2026-11-03.

## Implied volatility and when it is refused

The desk solves IV itself from the leg's mid: the sandbox serves no IV and no Greeks. The rate `r` is
`desk.toml` `[rates] risk_free_rate`. The dividend yield `q` is the symbol's `dividend_yield` in
`research/calendar.toml`, else 0, with the leg warning `q = 0: no dividend yield in calendar.toml`. The dividend
intake of `desk_init.py` writes no `dividend_yield`, so on a new desk that warning is expected until the owner adds
the yields by hand.

Each leg's `iv_status` says what happened:

| `iv_status` | Meaning |
| --- | --- |
| `ok` | solved; IV and Greeks are set |
| `unavailable` | no usable mark to solve from (`mark_source` is not `mid`) |
| `spot_unavailable`, `spot_unaligned` | no usable spot, or one stamped more than 60 s from the option |
| `expiring` | under one hour to the cutoff |
| `expired`, `closed` | past its last trading day, or a closed row |
| `not_option` | a stock or ETF leg |
| `below_intrinsic`, `above_bound` | the mid is outside the no-arbitrage bounds |
| `no_root` | no volatility reproduces the mid |

Every status but `ok` leaves IV and Greeks empty, and a rule that needs them is unavailable. From a synthetic book
check on the stale set: `unavailable: your rule DELTA_WATCH: row 6 NVDA261120C00150000: no Greeks (iv_status
unavailable)`.

## Greeks: European, in desk units

- The model is the European closed form. A deep in-the-money American option quoted at intrinsic can come back
  `below_intrinsic` under it; that is a known limit, not a data error. An early-exercise model for American options
  is planned for Stage 2 (not in this release).
- Units: delta per share, theta per calendar day, vega per volatility point, rho per 1 % of rate.
- `position_greeks` scale a leg's Greeks by its open signed quantity times the multiplier.
- A group's `net_greeks` adds one delta per share of stock to its option legs, and is empty when any open option leg
  has no Greeks.
- The portfolio's `net_delta` is set only when every open group has Greeks: a partial total is worse than none.
- `beta_weighted_delta` is always `{"value": null, "r2_flag": "unavailable"}` in Stage 1. Beta-weighted delta,
  exposures and volatility context are planned for Stage 2 (not in this release).

## P/L: unrealized, realized and day

- Unrealized P/L is measured on the open part of each leg, on both bases: `unrealized_pl_mid` and
  `unrealized_pl_liq`.
- Realized P/L comes from the closed rows: today, month to date and year to date, on America/New_York dates.
- Day P/L compares each open leg's mid with its mid in the desk's own snapshot of the previous trading day,
  `data/book/<date>.json`, written by the 16:35 eod routine. It never uses a vendor's previous close. A leg opened
  today counts from its entry price; a previous mark that was not `mid` is no basis.
- With no previous snapshot there is no day P/L. A synthetic `book.py` run on the desk's first day warns:

```text
warning: day P/L unavailable: no snapshot for 2026-11-02 (the desk's first day?)
```

- Portfolio totals add only the groups whose data quality is `ok`, and each group left out is named. A group with an
  unmarked leg is never summed as zero, and a group's day P/L is never partly summed.

The 17:10 EOD Desk Brief carries the totals. The figures of its P/L line on a synthetic fixture day (the
journey-pinned value):

```text
realized today $0.00, month to date $0.00, year to date $425.00 | unrealized $4,034.00 (liquidation), $4,097.00 (mid) | day $400.00
```

## Structure metrics: percent captured, max profit and loss, breakevens

Each group carries:

- `pct_captured_liq` and `pct_captured_mid`: for a credit structure, the share of the credit already captured; for
  any other structure, the gain as a share of the group's max profit;
- `max_profit`, `max_loss` and `breakevens`, from the payoff of the open legs (they need no quote, so they stay set
  when the marks are unavailable);
- `unbounded_up` and `unbounded_down` where the payoff has no cap.

A synthetic `book.py` group line (regular set, 2026-11-03 19:50 UTC):

```text
AG-20261020-01 covered_call WMT | Brokerage (margin) | net cost 19016.00 | P/L mid 1145.00 liq 1124.00 | max profit 1984.00 max loss 19016.00 BE 95.08 | ok
```

`book.py` prose and `reports/book.md` are diagnostics, never a desk message: they are not linted. When the owner
asks how the book stands, the Sentinel or the Desk Lead quotes the latest EOD Desk Brief lines. Run interactively:

```text
python3 scripts/book.py --desk-root /workspace/aegis-desk
```

Without `--snapshot` or `--report`, `book.py` writes no book record (only the quote cache); only the eod routine
passes those flags.

## What UNAVAILABLE protects the owner from

A stale quote read as fresh, a wide quote read as a fair price, a missing dividend read as zero, a model price read as
a market price, a partial total read as the whole: each would put a number in front of the owner that no market
offered. The book refuses all of them, says which input is missing, and the rule that needed it says UNAVAILABLE,
never HOLD.
