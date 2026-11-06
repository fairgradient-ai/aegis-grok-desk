---
name: aegis-protective-put-and-collar-management
description: How the Aegis desk watches hedges, for the Aegis Exit Sentinel and the Aegis Structuring Desk. How a protective put and a collar are grouped and measured as the rules engine reads them, the continuity rules at 30 and 14 calendar days, monetize on the put's absolute delta, the floor and cover rules, automatic exercise on the last trading day, the collar's short call with its take-profit, cap and ex-dividend exposure, and what the desk never decides. It describes the owner's adopted rules; it never chooses a rule or a threshold.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: structures
---

# Protective puts and collars

This skill says how the desk reads a hedge and which rules of the example rulebook reach it. Every threshold is the
owner's choice, adopted rule by rule. The skill never chooses a rule, a threshold or a plan.

The examples are synthetic: tool runs on copies of `tests/fixtures/desk` at the instants named beside them. In that
fixture book, AG-20261028-03 is a protective put: 100 SPY shares and one long SPY 620 put expiring 2026-12-18. The
fixture book holds no collar, so no collar line is shown here.

## How the desk groups and measures a hedge

- A protective put is long shares plus one or more long put legs. It needs no take-profit rule: a hedge is held for
  protection, so the adoption lint exempts it. It may carry at most one loss rule.
- A collar is long shares, one long put leg and one short call leg, with the shares covering the call. It is a
  covered structure: its take-profit metric is `pct_captured` of the short call's credit (a rule applying to
  `short_call_leg`) or `pct_of_max_profit` of the group. A loss rule may only read the group, with `unrealized_pl`
  or `pct_of_max_loss`, and never the call leg.
- The put of either structure is `long_put_leg` in a rule's `applies_to`. The collar's call is `short_call_leg`.

## The example rules that reach a hedge

From the fixture's `rules/CHANGELOG.md` (each adopted `example-accepted`):

| Rule | Condition | What fires | Reaches |
| --- | --- | --- | --- |
| rule LAPSE14 | `dte <= 14 calendar days` | an EXIT_SIGNAL, plan roll_review | the long put of either structure |
| rule ROLL30 | `dte <= 30 calendar days` | a WATCH, plan roll_review | the long put of either structure |
| rule MONETIZE | `abs_delta >= 0.80 delta` | an EXIT_SIGNAL, plan hold_review | the long put of either structure |
| rule FLOOR | `stock_below_long_put is_true` | a WATCH, plan hold_review | protective put, collar |
| rule COVER | `stock_above_short_call is_true` | a WATCH, plan roll_review | collar |
| rule AUTOEX | `auto_exercise_itm is_true` | an EXIT_SIGNAL, plan hold_review | every long option leg on its last trading day |

The collar's call is also reached by TP50, TP80, DTE21, DELTA_WATCH, DELTA_SIGNAL and PIN3 (through
`short_call_leg`) and by EXDIV (through `any_short_call_leg`). EARN2 reaches the collar as a group, never a
protective put. CAP reaches covered calls only; COVER is the collar's own. USTOP reaches any group with a stop in
`positions.csv`; the fixture's SPY group has none.

The SPY protective put on the sheet, from the regular quotes on 2026-11-03 at 19:50 UTC:

```text
## AG-20261028-03 | SPY protective put | option ticks 0.05 below 3.00, 0.10 at or above (standard class: desk.toml [levels] names no penny class for SPY)
LAPSE14 fires on 2026-12-04 (14 calendar days before the last trading day 2026-12-18)
your rule ROLL30 fires on 2026-11-18 (30 calendar days before the last trading day 2026-12-18)
MONETIZE fires near SPY 572.79 (rounded up from 572.7826; model estimate at IV 24.68 %, date fixed; 11.62 % below the current 648.12, 1.34 sigma)
FLOOR fires at SPY 619.99 or below (one cent below the strike 620; 4.34 % below the current 648.12)
AUTOEX fires at SPY 619.99 or below (one cent below the strike 620; 4.34 % below the current 648.12) on the last trading day 2026-12-18
USTOP not applicable: no underlying_stop on group AG-20261028-03
```

## Continuity: the 30-day review and the 14-day lapse

- The rule ROLL30 posts a WATCH when the put has 30 calendar days or fewer to its last trading day. Its plan is the
  owner's review of a roll.
- The rule LAPSE14 posts an EXIT SIGNAL at 14 calendar days or fewer, so the hedge does not lapse unseen.
- Both are date rules (class E). They fire at once, with no confirmation, and need no quote.

From the expiry-day quotes (the 15:35 slot of 2026-11-20, run at 19:50 UTC):

```text
WATCH AG-20261028-03 | SPY protective put | your rule ROLL30 (rules 1.0.0, sha cc09859c) fired: time to expiry 28 calendar days (your threshold: at or below 30 calendar days) | already past this threshold at the desk's first observation | quote grade B, Tradier sandbox, 15-min delayed, as of 19:31 UTC | your plan: review a roll | not an instruction to trade; you act in your brokerage account.
```

## Monetize, floor and cover

- MONETIZE reads the absolute value of the put's delta. A put's delta is negative, so the threshold 0.80 is met by a
  delta of -0.80 or below: the hedge is deep in the money. It is class G: it needs the desk's own Greeks, waits for
  its confirmations, and its level on the sheet is a model estimate with the implied volatility and the date fixed.
- FLOOR fires when the stock is at or below the put's strike less one cent. Its level is exact.
- COVER fires when the stock is at or above the collar's call strike plus one cent.

Without a fresh quote these rules cannot be read, and the group is UNAVAILABLE, never HOLD. From the stale quotes on
2026-11-03 at 19:50 UTC:

```text
unavailable: your rule MONETIZE: row 8 SPY261218P00620000: no Greeks (iv_status unavailable)
unavailable: your rule FLOOR: underlying SPY: no usable mark (quote grade F)
```

## Automatic exercise on the last trading day

- Under OCC's exercise by exception, a long option at least 0.01 in the money on its last trading day is exercised
  automatically. The owner decides before the broker's cutoff.
- AUTOEX fires on a long option leg that is in the money on its last trading day. The level line names the price
  and the day, as in the SPY line above.
- The owner's broker cutoff is `broker_exercise_cutoff_et` in `desk.toml`. The Final Call of Aegis Position Control
  prints the act-by time; the expiry and assignment skill has the protocol.
- On a group without a long option, the sheet says so:
  `AUTOEX cannot fire on this position: it reads long option legs only`.

## The collar's call

- The take-profit rules TP50 and TP80 read the collar's call through `short_call_leg`, on the conservative side of
  the quote (its fresh ask).
- The time, delta and pin rules (DTE21, DELTA_WATCH, DELTA_SIGNAL and PIN3) read it as they read a covered call's
  call. COVER watches it in the money.
- EXDIV reaches every open short call, the collar's included. A short call gets a dividend tier only when two
  conditions hold. The ex-date falls within 5 trading days after today. The call's last trading day is on or after
  the last cum-dividend day, the trading day before the ex-date. A call that expires before that day lists no
  dividend tier, and its expiry risk is the pin tier's. An unknown dividend is never zero: the rule is unavailable.
- In the example rulebook (each rule's `suppress_if_tag`), `#assign-ok` silences EXDIV, DELTA_WATCH, DELTA_SIGNAL
  and CAP. COVER, FLOOR and PIN3 carry no `suppress_if_tag` there, so no tag silences them.

## What the desk does not decide

- Whether to keep a hedge, extend it or end it is the owner's decision, in the brokerage account. The plan words in a
  message are the owner's own plan, never the desk's instruction.
- No alert measures a protection budget or the time decay against it. The structuring objective
  `stress_cost_per_day` ranks new puts by their ask per day per 100 of loss covered at the owner's stress drop, only
  in a proposal the owner asks for.
- Only when the owner asks, the Aegis Structuring Desk prices a hedge's choices: a CLOSE, MONETIZE_PUT or
  UNWIND_STOCK ticket, a ROLL ticket for a protective put's long put the owner names, and a protective put or a
  collar on shares held alone (`aegis-ticket-and-unwind-order`, `aegis-overlay-proposals`).
- A collar's legs are replaced as a unit, and a roll analysis of a hedge, and stress tests of a hedge through an
  event, are planned for Stage 2 (not in this release).
- The example rules read absolute delta; none of them reads a signed delta.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.
