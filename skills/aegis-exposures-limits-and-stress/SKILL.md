---
name: aegis-exposures-limits-and-stress
description: How to read and relay the book's exposures, the two limit metrics and the stress views, for the Aegis Book & Risk Analyst. The daily beta fit against SPY on split-adjusted closes and its UNAVAILABLE cases, each underlying's net delta and beta, the beta-weighted delta in dollars with its r2 flag, the limits BWD and UDELTA as adopted exit rules with WATCH only, the scenario grid of spot and volatility moves on the book's own model, the event stress through an earnings or ex-dividend date, the stock and options split of a hedge, exclusions, and what is never said - a forecast, a probability or a judgment of an exposure.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: risk
---

# Exposures, limits and stress

Every figure here is a computation on the desk's model of record from the owner's own book. None is a forecast, a
probability, a target or a recommendation. The Bot quotes the lines the tools print and computes nothing.

## The beta fit (`beta_refresh.py`)

- OLS of a symbol's daily simple returns on SPY's, over the 252 closes ending the previous trading day, on
  split-adjusted bars (`adjustment=split` on the desk's one bars path), so a split is no return.
- UNAVAILABLE when fewer than 126 returns pair up, when SPY's returns do not vary, or when any daily return in either
  series lies beyond 40 %: a move like that is an unadjusted event or bad data, not a market move to fit.
- `r2_flag` is `ok` at r2 of 0.30 or more and `low` below it. SPY's own beta is 1.
- The fits are written once a day to `data/beta/<ET date>.json`, the book's only source of betas. Closed bars are
  final: a second run the same day reads the cache and sends nothing.

```text
BETA | NVDA | 2.04 (r2 0.50, ok) over 251 daily returns
BETA | SPY | 1.00 (the benchmark)
BETA | WMT | 0.53 (r2 0.23, low) over 251 daily returns
```

## The book's exposures

- Each underlying carries `net_delta` (its open groups' net delta in shares; zero with no open group; empty when a
  group is unavailable), `beta` and `beta_r2_flag`.
- `beta_weighted_delta` is the sum over the underlyings of net delta x spot x beta, in dollars: the book's
  SPY-equivalent exposure. Its `r2_flag` is `low` when any fit used is low. It is empty without the day's beta file,
  or when an underlying with a net delta lacks a beta, a delta or a mark. An empty figure is never filled in.
- YOUR BOOK prints `Beta-weighted delta $84,335.44 against SPY (r2 low: a weak beta fit for at least one symbol)` when
  the figure exists, and nothing when it does not.

## The limits

- Two metrics of the exit rulebook: `beta_weighted_delta_pct` (portfolio: the beta-weighted delta over
  `declared_equity`, times 100) and `delta_exposure_pct` (per underlying: net delta x spot over `declared_equity`,
  times 100). Either is UNAVAILABLE when a delta, a mark or (for the first) the beta file is missing.
- A limit exists only as an exit rule the owner adopted (`rules/examples/limits-1.0.0.toml` holds `BWD` and `UDELTA`,
  both `owner_must_set`), with WATCH verdicts and `plan = "hold_review"`. `book_check` evaluates it four times a day;
  its message is the Aegis Exit Sentinel's.
- The Book & Risk Analyst shows the exposure; it never says whether it is too high, and never names a threshold the
  owner did not adopt.

## The scenario grid (`scenario.py`)

- The grid is the desk's proposal: spot moves of -10, -5, -2, 0, +2, +5 and +10 % of each underlying's mark, crossed
  with volatility moves of -5, 0 and +5 points (never below 1 %). Every underlying moves by the same percentage in a
  cell; there is no correlation model.
- Every stressed option price comes from the book's own leg pricer (`book.leg_price`, the American lattice), at the
  leg's implied volatility, the book's rate and the symbol's sourced yield.
- A cell is the change from the book's marks. A zero move equals the book exactly.
- Each group prints its change at the two outer spot moves, stock and options apart:

```text
AG-20261028-03 protective_put SPY | spot -10%: stock -6481.20, options +3276.14, net -3205.06 | spot +10%: stock +6481.20, options -878.83, net +5602.37
```

  Quote both sides. Never say a hedge "works", "protects enough" or "fails".

## The event stress

- `--event earnings|ex_dividend --symbol SYM` prices that symbol's groups at the close of the first trading day after
  the date in `research/calendar.toml`. A leg that expires first is valued at its intrinsic value at the moved spot.
- Earnings: the volatility moves stand in for the change of implied volatility across the release.
- Ex-dividend: spot is never cut by the dividend, because the sourced yield already lowers the forward. When a short
  call is in scope, a note says that early assignment is the dividend tier's test (Aegis Position Control's).
- `--upcoming N` (the `pre-event-stress` routine) runs one stress per held symbol with an event within N trading days;
  with none it is quiet.
- An event the calendar cannot date is UNAVAILABLE, never assumed.

## Exclusions

A group the book marks unavailable, a leg without implied volatility, an unknown dividend yield or an underlying
without a mark excludes its group with the reason, and the portfolio total is then empty: a partial total is worse than
none. The tool exits 20.

## Never

- A forecast, a probability, an expected loss, a value at risk or a "worst case".
- A number of the Bot's own, or a cell the tool did not print.
- A judgment of an exposure, a beta or a stress, or a suggestion to hedge, trim or add.
