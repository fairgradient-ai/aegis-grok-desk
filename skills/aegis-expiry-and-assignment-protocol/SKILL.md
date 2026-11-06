---
name: aegis-expiry-and-assignment-protocol
description: The expiry protocol of the read-only Aegis desk, for Aegis Position Control. Exercise by exception and the one-cent rule, the owner's broker cutoff as the act-by time, PM and AM settlement, early-close days, the pin tier, the dividend tier and its last-cum-dividend-day bound, the expiry week from the morning docket through the T-1 preview and the Final Call to the Saturday inference and the Monday reconciliation, the inferred outcomes that always say confirm with your broker, and the exit codes.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: expiry
---

# Expiry and assignment protocol

Expiration is the one event that changes the book without a trade. Aegis Position Control owns it from the first
docket line to the Monday reconciliation. Every time, tier and outcome below is computed by a tool; the Bot quotes it.
The analysis is European-only in Stage 1: an early-exercise model is planned for Stage 2 (not in this release).

## Exercise by exception and the one-cent rule

The desk calls an option in the money by one cent: a call when the underlying is at or above the strike plus 0.01, a
put when it is at or below the strike minus 0.01. This is the OCC's exercise-by-exception threshold. On its last
trading day every in-the-money leg carries this line (the day before, it is dated):

```text
in the money: automatic exercise or assignment expected at the close
```

Before expiry the tools judge it at the underlying's usable mid (grade A to D); after expiry `csv_recon.py` judges it
at the official close.

## Your broker's cutoff is the act-by

The act-by time is the owner's own broker cutoff, `broker_exercise_cutoff_et` in `desk.toml`, on the leg's last trading
day. The desk prints it in ET and UTC, for example `act by Fri 2026-11-20 16:30 ET (21:30 UTC)` (the fixture desk's
cutoff is 16:30 ET).

- On an early-close day (13:00 ET) the act-by is the earlier of the broker cutoff and 13:00 ET, marked
  `early close 13:00 ET: confirm your broker's cutoff`. The desk does not know the broker's own early-close cutoff.
- The OCC's 17:30 ET exercise deadline is never the desk's act-by.
- The Structuring Desk's LET_ASSIGN ticket prints a short option's act-by as the session close of its last trading
  day (16:00 ET, 13:00 ET on an early close), or this cutoff when it is earlier, because a short option can be closed
  only while the session trades (`aegis-ticket-and-unwind-order`). Each Bot quotes its own tool's time.
- `market_clock.py` prints the exchange's instant as a reference. A synthetic run,
  `market_clock.py --at 2026-11-27T17:00:00Z --expiry 2026-11-27`, ends with:

```text
expiry 2026-11-27 (PM-settled): last trading day 2026-11-27, settlement day 2026-11-27; DTE 0 calendar, 0 trading; exercise cutoff 2026-11-27T18:00:00+00:00 (2026-11-27 13:00 ET, exchange; a broker cutoff is earlier); calendar coverage table
```

## Settlement styles

- **PM-settled, physical** (equity and ETF options): the last trading day is the expiration day, unless a closure moves
  it. Assignment and exercise deliver shares.
- **AM-settled, cash** (index roots): a root is AM-settled only when `research/calendar.toml` lists it under
  `[settlement] am_settled_roots`. Its last trading day is the day before the settlement morning, it settles on the
  09:30 ET opening value, and it is never assigned or exercised into shares. A synthetic run,
  `market_clock.py --at 2026-11-19T19:50:00Z --expiry 2026-11-20 --am-settled`:

```text
expiry 2026-11-20 (AM-settled): last trading day 2026-11-19, settlement day 2026-11-20; DTE 0 calendar, 0 trading; exercise cutoff 2026-11-20T14:30:00+00:00 (2026-11-20 09:30 ET, exchange; a broker cutoff is earlier); calendar coverage table
```

Without a calendar file every root is treated as PM-settled. The dividend intake of `desk_init.py` writes no
`[settlement]` table, so an owner who holds index options adds `am_settled_roots`, and each settlement value as a
`[[settlement.values]]` entry, to `research/calendar.toml` by hand. The sandbox quotes no index, so such legs are
never marked. Sourcing settlement values is planned for Stage 2 (not in this release).

A full closure in the calendar (`[[closures]]` with `kind = "full"`) moves the last trading day itself. The scheduled
NYSE early closes are in the desk's own holiday table.

## The pin tier

On a leg's last and next-to-last trading days the desk measures the underlying's distance from the strike:
`|S_mid - K| / S_mid x 100`, printed to two decimals. The tier is decided on the printed value.

| Distance | Tier |
| --- | --- |
| at most 1 % | PIN HIGH |
| at most 3 % | PIN WATCH |
| beyond 3 %, in the money | ITM |
| beyond 3 %, out of the money | PIN LOW |

Without a usable underlying quote the pin tier is UNAVAILABLE.

## The dividend tier and its bound

A short call gets a dividend tier only when two conditions hold:

1. the ex-dividend date falls within 5 trading days after today; and
2. the call's last trading day is on or after the last cum-dividend day, the trading day before the ex-date.

A call that expires before that day lists no dividend tier: it no longer exists when a dividend-capture exercise would
happen, and its expiry risk is the pin tier's. The fixture's own case, from a synthetic `trigger_levels.py` run:

```text
EXDIV not applicable: the last trading day 2026-11-20 precedes the last cum-dividend day 2026-11-23 of the 2026-11-24 ex-date
```

When the tier applies, the extrinsic value on the bid side is `bid - max(0, S_mid - K)`. Below the dividend amount is
HIGH; below twice the amount is WATCH; otherwise LOW. The tag `#assign-ok` turns HIGH into NOTED, with the arithmetic
still printed. A rewritten-calendar example: a fixture copy whose WMT ex-date was moved to 2026-11-20 with an amount
of 2.00, run on the t1 set at 2026-11-19 19:50 UTC (synthetic):

```text
NOTED AG-20261020-01 WMT261120C00105000 short 2: ex-dividend 2026-11-20 in 1 trading day (confirmed), amount 2.00; extrinsic (bid) 0.09 = bid 1.52 - max(0, 106.43 - 105); in the money; HIGH downgraded to NOTED: #assign-ok (owner accepted assignment); act by Fri 2026-11-20 16:30 ET (21:30 UTC) [next trading day]
```

An unknown dividend is UNAVAILABLE, never zero: a symbol missing from the calendar, a status of `unknown`, or an
ex-date already past with the next one not recorded. One line of a T-1 preview, from a synthetic copy whose WMT call
was moved to the 2026-11-27 expiry (an early-close day), run with `--tomorrow` on the regular set at 2026-11-25
20:20 UTC, after the 2026-11-24 ex-date:

```text
  UNAVAILABLE AG-20261020-01 WMT261127C00105000 short 2: dividend unknown: the WMT ex-dividend 2026-11-24 has passed; the next is not in calendar.toml (unknown dividend is never zero); act by Fri 2026-11-27 13:00 ET (18:00 UTC) [next trading day]; early close 13:00 ET: confirm your broker's cutoff
```

In Stage 1 the dividends are the owner's: the bootstrap's intake through `desk_init.py`. Sourcing them is
planned for Stage 2 (not in this release).

## The expiry week

| When (ET) | Routine and tool | What the owner sees |
| --- | --- | --- |
| every weekday, 07:30 | preopen: `expiry_checklist.py --week` | the docket line in the Morning Desk Sheet |
| 15:20, the trading day before a last trading day | final-call: `expiry_checklist.py --tomorrow` | the T-1 PREVIEW |
| 15:20, on a last trading day | final-call: `expiry_checklist.py --final-call` and `assignment_watch.py` | the FINAL CALL and the assignment watch |
| Saturday, 09:30 | post-expiry: `csv_recon.py` | the inferred outcomes and the number of proposed edits |
| Monday, 07:30 | preopen: `csv_recon.py` | the reconciliation line: still EDITS_PENDING until the owner applies the lines |

The final-call routine posts only when one of its steps exited 10 or 20; otherwise it is quiet. Synthetic examples:

The docket, `expiry_checklist.py --week` on the t1 set at 2026-11-19 19:50 UTC (first line):

```text
EXPIRY WEEK 2026-11-16..2026-11-22: 2 contracts due (today Thu 2026-11-19)
```

The T-1 preview at the same instant (first two lines):

```text
T-1 PREVIEW for Fri 2026-11-20: 2 contracts on their last trading day (today Thu 2026-11-19)
AG-20261020-01 WMT261120C00105000 short 2 | covered_call | pm-settled | last trading day Fri 2026-11-20 (1 trading day) | shares called away if assigned: 200 WMT at 105 (covered by the 200 shares held) | act by Fri 2026-11-20 16:30 ET (21:30 UTC) [next trading day]
```

The final-call routine on the expiry_day set at 2026-11-20 19:50 UTC (its first lines, and the assignment watch's
summary):

```text
FINAL CALL Fri 2026-11-20: 2 contracts on their last trading day; act by 16:30 ET (21:30 UTC)
AG-20261020-01 WMT261120C00105000 short 2 | covered_call | pm-settled | last trading day Fri 2026-11-20 (today) | shares called away if assigned: 200 WMT at 105 (covered by the 200 shares held) | act by Fri 2026-11-20 16:30 ET (21:30 UTC) [today]
  PIN HIGH AG-20261020-01 WMT261120C00105000 short 2: 0.07 % from the strike (mid 104.93 vs 105), out of the money; last trading day Fri 2026-11-20; act by Fri 2026-11-20 16:30 ET (21:30 UTC) [today]
assignment watch Fri 2026-11-20 (session open): FIRED (1 fired, 0 noted, 0 unavailable, 3 open option legs)
```

The Saturday routine on the post_expiry set at 2026-11-21 14:30 UTC (its first lines):

```text
EDITS_PENDING (2026-11-21 ET): 2 expired legs, 3 proposed edits, 0 unavailable items, 0 warnings
AG-20261020-01 row 4 WMT261120C00105000 -2 EXPIRED_OTM: WMT closed 104.40 on 2026-11-20: the 105 call expired out of the money (edit proposed)
AG-20261022-02 row 6 NVDA261120C00150000 -2 PROBABLE_ASSIGNED: NVDA closed 153.40 on 2026-11-20: the 150 call finished 3.40 in the money; short: assignment probable (edits proposed)
```

`csv_recon.py --diff` then shows the exact lines (`aegis-positions-csv-book-of-record`).

## Early-close days

There is no 12:20 routine. On an early-close day the final-call routine runs no step and prints `quiet`. Two other
messages carry the early act-by instead:

- the 15:20 T-1 preview on the trading day before, which already prints the earlier of the broker cutoff and 13:00
  ET with the early-close mark;
- the 07:30 Morning Desk Sheet of the day itself, which carries the early-close line.

From a synthetic fixture copy whose WMT call expires on Friday 2026-11-27, the day after Thanksgiving: the T-1
preview on Wednesday 2026-11-25 at 20:20 UTC, then the morning sheet at 2026-11-27 12:35 UTC (one line each):

```text
AG-20261020-01 WMT261127C00105000 short 2 | covered_call | pm-settled | last trading day Fri 2026-11-27 (1 trading day) | shares called away if assigned: 200 WMT at 105 (covered by the 200 shares held) | act by Fri 2026-11-27 13:00 ET (18:00 UTC) [next trading day]; early close 13:00 ET: confirm your broker's cutoff
early close today at 13:00 ET: 1 contract on its last trading day; act by Fri 2026-11-27 13:00 ET (18:00 UTC) [today]; early close 13:00 ET: confirm your broker's cutoff
```

## Inferred outcomes: confirm with your broker

After an expiry, `csv_recon.py` infers each expired leg's outcome from the official close (or the settlement value).
Every outcome is an inference; the owner confirms it against the broker before applying any line.

| Outcome | Meaning | Edit proposed |
| --- | --- | --- |
| `EXPIRED_OTM` | not in the money by the one-cent rule | the row closed at 0 |
| `PROBABLE_ASSIGNED` | a short leg in the money | the option row closed at 0 and the shares booked at the strike |
| `PROBABLE_EXERCISED` | a long leg in the money | the same convention |
| `PIN_UNCERTAIN` | the close within `pin_band` (0.25 in the `desk.toml` `desk_init.py` writes) of the strike, or the next session opened on the other side of the strike | none: "confirm with your broker" |
| `CASH_SETTLED` | an AM-settled root with a recorded settlement value | the row closed at its intrinsic value |
| `SETTLEMENT_UNKNOWN` | an AM-settled root without a recorded value | none: UNAVAILABLE until the owner records it |
| `SETTLEMENT_PENDING` | an AM-settled root before its settlement morning's open | none, and nothing asked yet |
| `EXPIRED_UNRECORDED` | no daily bar for the expiry day | none: the owner records the outcome |

A Bot never states an assignment or an exercise as fact. It says "assignment probable" or "pin uncertain" exactly as
the tool printed, and adds "confirm with your broker".

## What the exit codes mean, and what posts

- `assignment_watch.py`: 10 when an item fired (a HIGH dividend tier, PIN HIGH, ITM on the last trading day, or a
  vertical between its strikes on a due leg); 20 when something is UNAVAILABLE and nothing fired; 0 otherwise.
- `expiry_checklist.py`: 10 when contracts are listed; 20 when the sheet is incomplete (an item UNAVAILABLE or a group
  quarantined); 0 when nothing is due.
- `csv_recon.py`: 0 CLEAN, 10 EDITS_PENDING, 20 UNAVAILABLE, 1 ERRORS.

The routines post what the manifest says (`aegis-routines-and-quiet-rules`). Interactively, Position Control runs
these with `--desk-root /workspace/aegis-desk` and quotes their output unchanged in a fenced block headed by the exact
command:

```text
python3 scripts/expiry_checklist.py --week --desk-root /workspace/aegis-desk
python3 scripts/expiry_checklist.py --tomorrow --desk-root /workspace/aegis-desk
python3 scripts/assignment_watch.py --desk-root /workspace/aegis-desk
python3 scripts/csv_recon.py --diff --desk-root /workspace/aegis-desk
python3 scripts/market_clock.py --expiry YYYY-MM-DD --desk-root /workspace/aegis-desk
```
