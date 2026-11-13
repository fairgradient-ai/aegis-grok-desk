---
name: aegis-threshold-levels
description: How to read and relay the Aegis threshold-level sheet and the exit-plan card, for the Aegis Exit Sentinel. Each adopted rule as the option, spread or underlying price, band or date at which its own reading turns, with the arithmetic shown; the tick classes and the rounding direction for closing a short leg or a long leg; resting-eligible lines versus alert levels; model estimates; the lines that say a rule cannot fire; when the sheet is written and posted; plan cards on request; and no order shape in this release.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: exits
---

# Threshold levels

## What the sheet is for

The desk checks the book at fixed slots, on data delayed 15 minutes. The owner's brokerage can watch a price in real
time. The threshold-level sheet joins the two: it prints, for each adopted rule of each open group, the price, band or
date at which that rule's own reading turns. The owner can then set alerts in the brokerage account.

- A level is where the owner's rule turns. It is never an order and never a suggestion.
- Every status on the sheet comes from the rules engine's own evaluation, with the rule state the book check
  recorded. The sheet never re-decides one.
- Relay the sheet as printed. Never restate a level in other words, never round it again, never add one of your own.

```text
python3 /workspace/aegis-grok-desk/scripts/trigger_levels.py --desk-root /workspace/aegis-desk
```

The examples are synthetic: tool runs on copies of `tests/fixtures/desk` at the instants named beside them.

## The head of the sheet

From the regular quotes on 2026-11-03 at 19:50 UTC:

```text
THRESHOLD LEVELS 2026-11-03 | rules 1.0.0, sha cc09859c | book as of 19:31 UTC
## AG-20261020-01 | WMT covered call | option ticks 0.05 below 3.00, 0.10 at or above (standard class: desk.toml [levels] names no penny class for WMT)
```

The first line names the date, the adopted rulebook and its sha, and the time of the newest quote in the book. Each
group then gets a header with its trade id, its structure and its tick grid, and one line per rule in rulebook order.
A section `## underlyings and portfolio` follows the groups. The fixed footer closes the sheet.

## Reading a line

Each line starts with the rule id, then the level, then the arithmetic in brackets, then a status. The WMT covered
call from the same run:

```text
TP50 cannot fire on this position: time to expiry 13 trading days (your threshold: above 21 trading days), and the days only fall
TP80 fires at an option price of 0.65 (rounded down from 0.684 = credit 3.42 x (1 - 80 %)); resting-eligible: yes (below the current ask 3.50)
DTE21 fires on 2026-10-22 (21 trading days before the last trading day 2026-11-20); threshold already crossed (rule fired)
PIN3 fires while WMT is between 101.94 and 108.25 (within 3 % of the strike 105) from 2026-11-17 (3 trading days before the last trading day 2026-11-20)
EXDIV not applicable: the last trading day 2026-11-20 precedes the last cum-dividend day 2026-11-23 of the 2026-11-24 ex-date
AUTOEX cannot fire on this position: it reads long option legs only
USTOP fires at WMT 92.00 or below (your stop in positions.csv; 11.73 % below the current 104.23)
EARN2 fires from 2026-11-13 to 2026-11-17 (within 2 trading days of the earnings date 2026-11-17 in research/calendar.toml)
suppressed by your row tags: DELTA_WATCH, DELTA_SIGNAL, CAP
```

The statuses a line can carry:

| Status | What it says |
| --- | --- |
| `resting-eligible: yes (...)` | a take-profit price the market has not reached: below the current ask when closing a short leg, above the current bid when closing a long leg |
| `resting-eligible: unknown (...)` | a take-profit price with no fresh side to compare it with |
| `alert level, not an order: a limit at this price executes immediately` | every other option or spread price, such as a loss level |
| `threshold already crossed (rule fired)` | the rule fired (or is in its cooldown) |
| `threshold already crossed (pending confirmation: observation N of M)` | the reading is past the threshold and the rule waits for a confirmation |
| `cannot fire on this position: ...` | the rule's own reading can never reach its threshold on this group |
| `not applicable: ...` | the rule has nothing to read here, for a reason of record |
| `no level: ...` | no level can be computed, for the reason named |

A rule the desk cannot read now, whose level can still be computed, adds `the desk cannot read this rule now:` and the
reason. A group whose data is unusable says so first. From the expiry-day quotes on 2026-11-20 at 19:50 UTC:

```text
AG-20261020-01 data unavailable: row 4 WMT261120C00105000: no usable mark (quote grade E, fresh)
```

## Resting-eligible versus alert level

- A take-profit level is marked `resting-eligible: yes` only when the market has not reached it yet: the level is
  below the current ask for a short leg (or the current net cost to close of a credit vertical), or above the current
  bid for a long leg (or the current net value of a debit vertical).
- Every other option or spread price line carries the alert-level words. A loss level lies on the far side of the
  market: a limit at that price executes immediately. That is why the desk marks it as a level to watch, with the
  fixed words and nothing else.
- Underlying, band and date lines carry no such words. An underlying line not yet crossed shows its distance from
  the current price instead.

A loss line, from a fixture copy whose SPY rows were rewritten as one short put (the shares row removed and the put
row's quantity, cost basis and note made those of a short put; 2026-11-03, 19:50 UTC):

```text
LOSSX fires at an option price of 33.90 (= credit 11.30 x (1 + 2)); alert level, not an order: a limit at this price executes immediately
```

## Ticks and the rounding direction

| Tick class | Below 3.00 | At or above 3.00 |
| --- | --- | --- |
| standard (the default) | 0.05 | 0.10 |
| penny | 0.01 | 0.05 |
| penny at every price | 0.01 | 0.01 |

- The grid is chosen by the unrounded value. Spread prices use a 0.01 grid. Stock prices use 0.01 (0.0001 below
  1.00).
- A leg's class is its OCC root's. `desk.toml [levels]` lists the penny roots in `penny_classes` and
  `penny_all_classes`, which `desk_init.py` writes from the answers `levels.penny_classes` and
  `levels.penny_all_classes`. A root named in neither is standard. Standard is the coarsest grid, and its prices are
  valid on every grid. A group uses the coarsest class of its option legs.
- A level for closing a short leg or a credit vertical rounds down. A level for closing a long leg or a debit vertical
  rounds up. So a take-profit level always lies inside the rule's firing region, and a loss alert fires no later than
  the rule.
- Model levels of the underlying round toward the current price.
- When rounding changed the value, the unrounded value is printed: `0.65 (rounded down from 0.684 = ...)`. Otherwise
  the line prints `(= ...)`.
- A single option's take-profit level that would round down to 0.00 is no price, because an ask of 0 is no offer.
  The line then says `cannot fire on this position: the option price would have to fall to` the exact value, below
  the class's lowest tick.

The same WMT lines from a copy whose `desk.toml [levels]` adds `penny_classes = ["WMT"]` (and, for the warning at
the end of this skill, `order_shape_enabled = true`):

```text
## AG-20261020-01 | WMT covered call | option ticks 0.01 below 3.00, 0.05 at or above (penny class)
TP80 fires at an option price of 0.68 (rounded down from 0.684 = credit 3.42 x (1 - 80 %)); resting-eligible: yes (below the current ask 3.50)
```

## Underlying, band and date levels; model estimates

- Exact prices come from the rule's own reading on the stock grid: USTOP, FLOOR, CAP and AUTOEX on long legs.
- Bands come from distance rules, such as PIN3's distance from the strike, and from VBETWEEN's strikes.
- Dates come from day counts: DTE21, LAPSE14, LDTE30, EARN2 and the hedge's 30-day rule ROLL30.
- Delta rules have no closed form. The desk finds the underlying price where the rule's absolute delta crosses its
  threshold, with the leg's implied volatility and the date held fixed, and labels it a model estimate. It moves when
  the implied volatility or the time changes, so it is refreshed with the sheet.

From the regular quotes on 2026-11-03 at 19:50 UTC:

```text
DELTA_SIGNAL fires near NVDA 149.33 (rounded down from 149.3308; model estimate at IV 27.80 %, date fixed; 0.27 % above the current 148.925, 0.05 sigma)
CAP fires at NVDA 150.01 or above (one cent above the strike 150; 0.73 % above the current 148.925)
your rule ROLL30 fires on 2026-11-18 (30 calendar days before the last trading day 2026-12-18)
MOVE5_DOWN, MOVE5_UP, DAILY: no level: no previous end-of-day snapshot (none for 2026-11-02)
CONC has no single price level (it reads largest concentration by underlying, now 26.32 %; your threshold: at or above 25 %); threshold already crossed (rule fired)
```

The line of ROLL30 starts with `your rule` because the first letters of that id would read to the alert lint as an
imperative sentence start.

## Plan cards on request

An exit-plan card is one group's section of the sheet plus the dates that matter. No routine writes one.

```text
python3 /workspace/aegis-grok-desk/scripts/trigger_levels.py --plan AG-20261020-01 --desk-root /workspace/aegis-desk
```

- It prints the sheet, then the card, and writes `plans/<group id>.md` beside `levels/<date>.md` and
  `levels/<date>.json`.
- Post the card as printed, from its `PLAN CARD` line to the footer after it.
- When Aegis Position Control reports a new group CLEAN, it hands the group to the Exit Sentinel. The Sentinel runs
  `trigger_levels.py --plan <group id>` once and posts the card.

The card's first and last lines before the footer, from the same run:

```text
PLAN CARD AG-20261020-01 | WMT covered call | rules 1.0.0, sha cc09859c
dates that matter: last trading day 2026-11-20; 21 trading days before it 2026-10-22; earnings 2026-11-17 (confirmed, research/calendar.toml); ex-dividend 2026-11-24 (confirmed, $0.2350 per share); your broker's exercise cutoff 16:30 ET on 2026-11-20
```

## When the sheet is written and posted

- 10:05 ET: the book check refreshes `levels/<date>.md` and `levels/<date>.json` and posts nothing about them. A
  failed refresh leaves an incident file, adds no INCIDENT line, and the book check's own message still posts.
- 16:35 ET: the eod routine snapshots the book and posts the sheet built from the snapshot
  (`trigger_levels --from-snapshot`). It posts only the sheet; the book table goes to `reports/book.md`. Its head on
  2026-11-03 reads `THRESHOLD LEVELS 2026-11-03 | rules 1.0.0, sha cc09859c | book as of 20:59 UTC`.
- 07:30 ET: the Morning Desk Sheet counts last night's levels per group, for example:

```text
- **Last night's levels**
  - AG-20261020-01 1 resting-eligible of 3 price levels
  - AG-20261022-02 1 resting-eligible of 5 price levels
  - AG-20261028-03 0 resting-eligible of 3 price levels
```

- On request, the Sentinel may post the full text of `trigger_levels.py`, the sheet or a card. The tool lints its own
  text before it prints.

The exit codes: 0 when written and every group is usable; 20 when a group is unavailable or quarantined, when the
snapshot is missing, when the market data is refused or when the desk lock was busy; 1 on an error such as
`unknown_group` or `lint_failed`, with nothing written; 2 on usage. The sheet never exits 10: it is information, not a
signal.

## No order shape in this release

- The sheet has no side, no duration and no order kind. It names levels, never orders.
- `desk.toml [levels] order_shape_enabled` is written `false` by `desk_init.py`. This release has no order-shape
  template: when the key is true, the sheet still prints alert levels only, and the tool warns:

```text
warning: desk.toml [levels] order_shape_enabled is true, but this release has no order-shape template: the sheet prints alert levels only
```

- Describe rounding as closing a short leg or closing a long leg, never with trade-side words.
- When the owner asks what to do at a level, the answer is the boundary:

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.
