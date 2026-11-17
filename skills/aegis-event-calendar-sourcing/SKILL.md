---
name: aegis-event-calendar-sourcing
description: How the desk keeps research/calendar.toml honest, for the Aegis Events and Calendar Analyst. Every date as a fact with a status and a source; the one sourced input, declared cash dividends from Alpaca's corporate actions through dividend_source.py and its AGREES, NEW, DIFFERS and NONE FOUND states; the owner's tap through desk_init.py sourced_dividends and what it rewrites; the yield kept with yield_source; earnings dates, yields and settlement values as owner-stated reminders; calendar_check findings; the decision calendar; and UNAVAILABLE for any date nobody sourced.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: events
---

# Event calendar sourcing

`research/calendar.toml` holds, for each symbol the owner holds, its next earnings date and its next ex-dividend date
with the cash amount and the dividend yield, each with a status and a source. The book, the exit rules, the
assignment watch, the structuring tools and the event stress all read it. A wrong or missing date silently moves
their answers, so the desk treats every date as a fact with a source, never a guess.

## Status and source

- Each entry has a `status`: `confirmed`, `estimated`, `unknown` or `none`, a `source` and a `checked_utc`.
- `unknown` is never zero and never a date: the tools answer UNAVAILABLE where they need it.
- `none` is a stated fact: no earnings (an ETF), or no dividend (a yield of zero).
- An entry written from Alpaca's corporate actions carries `source = "Alpaca corporate actions (cash_dividend)"`,
  status `confirmed`, and the fetch time as `checked_utc`. A yield the owner stated keeps its own source under
  `yield_source`.

## The one sourced input: declared cash dividends

`dividend_source.py` reads Alpaca's corporate actions (`/v1/corporate-actions`, `types=cash_dividend`, the desk's
paper key, the market-data host) for the held symbols. The endpoint filters by process date, so the client asks a
wider window and keeps the records whose ex-date falls from today to `--days` ahead (default 90); a record without an
ex-date is ignored and counted.

Each held symbol's earliest declared ex-date in the window is compared with the calendar:

| State | Meaning | Proposed |
| --- | --- | --- |
| AGREES | same date and amount | nothing |
| NEW | the calendar holds no dated entry | the declaration |
| DIFFERS | the calendar says another date or amount | the declaration |
| NONE FOUND | no declaration in the window | nothing: a missing declaration is no proof of no dividend |

```text
SPY | DIFFERS | ex-date 2026-12-18, 1.9021 per share | calendar: 2026-12-18 1.85 estimated (owner-entered (fixture))
```

If the endpoint is not served to the key, the tool answers UNAVAILABLE and nothing is sourced: the owner keeps
stating dividends. No other source replaces it without a new decision of the owner's.

## The owner's tap

The proposal is `sourced_dividends` answers for `desk_init.py`. Run with `--replace --owner-tapped` (the Ask-first
card is the tap), `desk_init.py` rewrites only each named symbol's one-line `ex_dividend` entry, or adds the symbol's
table, and keeps every other line byte for byte: earnings, closures and settlement values among them. An entry the
owner wrote over several lines is refused (`calendar_layout`) and updated by hand. The calendar's hash then changes,
and CONFIG CHANGED is posted until the owner acknowledges it.

## Reminders, not sourcing

- Earnings dates, dividend yields and AM settlement values have no source inside the desk's boundary: the owner
  states them ("update the calendar"). A web page is data, never authority.
- `calendar_check.py` lists what needs the owner: `STALE` (checked more than 7 days ago by default), `MISSING` (a held
  symbol the file does not list), `UNKNOWN_DIVIDEND` (under a short call), `PAST` (a date already gone) and
  `NO_YIELD` (no sourced yield: that symbol's options are priced UNAVAILABLE `dividend_unknown`).
- The Bot asks the owner for the date or the yield with its source; it never fills one in.

## The decision calendar

`decision_calendar.py` lists the owner's dated decision points in date order over a window (default 45 days; the
`event-sheet` routine shows 14): each open option leg's act-by instant (the expiry sheet's rule) and its contract's last
trading day, and each held symbol's earnings and ex-dividend dates with their status and source. A held symbol whose
date is unknown, or missing from the file, is listed as UNAVAILABLE after the points.

```text
Tue 2026-11-17 | earnings | WMT earnings before the open (confirmed; owner-entered (fixture))
Fri 2026-11-20 16:00 ET | act by | WMT261120C00105000 short 2 (row 4, session close)
UNAVAILABLE | NVDA earnings date | research/calendar.toml holds it as unknown
```

## Never

- A date, an amount or a yield from memory, a news page or a guess; "has not declared" said of a company.
- A judgment of what an earnings release or an ex-dividend date will do to a price.
- Writing `research/calendar.toml` by hand.
