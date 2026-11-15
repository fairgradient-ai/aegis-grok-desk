---
name: aegis-covered-call-and-cash-secured-put-management
description: How the Aegis desk measures covered calls and cash-secured puts and which example rules reach them, for the Aegis Exit Sentinel and the Aegis Structuring Desk. The take-profit and loss metric of each structure as the rules engine reads it, take-profit on the conservative side of the quote, the time, delta, pin, cap and earnings rules, the ex-dividend test and its cum-dividend bound, the row tags, the assignment words of the expiry docket, and what the desk never decides. It describes the owner's adopted rules; it never chooses a rule or a threshold.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: structures
---

# Covered calls and cash-secured puts

This skill says how the desk reads these two structures and which rules of the example rulebook reach them. Every
threshold is the owner's choice, adopted rule by rule. The skill never chooses a rule, a threshold or a plan.

The examples are synthetic: tool runs on copies of `tests/fixtures/desk` at the instants named beside them. In that
fixture book, AG-20261020-01 (WMT) and AG-20261022-02 (NVDA) are covered calls, and AG-20261001-04 is a cash-secured
put closed early.

## How the desk groups them

- A covered call is long shares and one short call leg, with at least 100 shares per call. Extra shares carry the
  warning `uncovered shares`; too few shares make the group `custom`.
- A cash-secured put is one short put. It carries the warning `cash reserve not verified`: `positions.csv` does not
  hold the cash reserve, so no tool checks it.

## How the desk measures each

The adoption lint holds each held structure to its own take-profit metric and at most one loss rule.

| Structure | Take-profit metric | Loss rule |
| --- | --- | --- |
| `cash_secured_put` | `pct_captured`: the share of the leg's credit captured | at most one; the example's LOSSX reads `loss_multiple_of_credit` |
| `covered_call` | `pct_captured` of the short call's credit (a rule applying to `short_call_leg`), or `pct_of_max_profit` of the group | only on the group, with `unrealized_pl` or `pct_of_max_loss`; never on the call leg |

- `pct_captured` of a group is (credit minus cost to close) divided by the credit, times 100. On a leg it is (entry
  premium minus the side read now) divided by the entry premium, times 100. The engine computes it; a Bot never does.
- A loss rule on `short_call_leg` is refused by the lint (`loss_on_short_call_leg`). The call of a covered call is
  watched by CAP, EXDIV and PIN3 when it goes into the money, not by a loss rule.
- The example rulebook holds no loss rule for a covered call.

## The example rules that reach them

From the fixture's `rules/CHANGELOG.md` (each adopted `example-accepted`):

| Rule | Condition | What fires | Reaches |
| --- | --- | --- | --- |
| TP50 | `pct_captured >= 50 percent and dte > 21 trading days` | an EXIT_SIGNAL, plan close | both (the covered call through its short call) |
| TP80 | `pct_captured >= 80 percent` | an EXIT_SIGNAL, plan close | both |
| LOSSX | `loss_multiple_of_credit >= 2 x` | an EXIT_SIGNAL, plan close | the cash-secured put only |
| DTE21 | `dte <= 21 trading days` | a WATCH, plan roll_review | both |
| DELTA_WATCH | `abs_delta >= 0.30 delta` | a WATCH, plan hold_review | both (the short option) |
| DELTA_SIGNAL | `abs_delta >= 0.50 delta` | an EXIT_SIGNAL, plan roll_review | both (the short option) |
| PIN3 | `pct_from_strike <= 3 percent and dte <= 3 trading days` | a WATCH, plan hold_review | both (the short option) |
| EXDIV | `days_to_ex_dividend <= 5 days and extrinsic_minus_dividend < 0 usd` | an EXIT_SIGNAL, plan roll_review | the covered call's short call |
| CAP | `stock_above_short_call is_true and dte <= 21 trading days` | a WATCH, plan roll_review | the covered call only |
| EARN2 | `days_to_earnings <= 2 days and days_to_earnings >= 0 days` | a WATCH, plan hold_review | both |
| USTOP | `spot_vs_stop_pct <= 0 percent` | an EXIT_SIGNAL, plan hold_review | any group with a stop in `positions.csv` |

LOSSX's own source line in the example rulebook says what it is: "a stop alert at loss 2.0 x credit = cost-to-close
3.0 x credit; stop alerts are risk control, not edge".

## Take-profit reads the conservative side

- On the liquidation basis, a take-profit rule reads a short leg at its fresh ask, from a quote of any grade A to E.
  A wide quote can only delay a take-profit, which is the safe direction. The message then adds
  `quote grade E: the conservative side stands`.
- A zero fresh ask is no offer: the rule is unavailable, never 100 % captured.
- Every other rule that reads a mark needs a two-sided fresh quote. A loss test without one is unavailable.

From the expiry-day quotes (the 15:35 slot of 2026-11-20, run at 19:50 UTC):

```text
EXIT SIGNAL AG-20261020-01 | WMT covered call | your rule TP80 (rules 1.0.0, sha cc09859c) fired: credit captured 95.32 % (your threshold: at or above 80 %); quote grade E: the conservative side stands | already past this threshold at the desk's first observation | quote grade E, Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC | your plan: close | not an instruction to trade; you act in your brokerage account.
```

## Time, delta, pin and cap

- TP50 needs more than 21 trading days left. Once fewer remain, the sheet says so:
  `TP50 cannot fire on this position: time to expiry 13 trading days (your threshold: above 21 trading days), and the days only fall`.
- DTE21 is a date rule (class E): it fires at once, with no confirmation. From the 14:05 slot on 2026-11-03:

```text
WATCH AG-20261020-01 | WMT covered call | your rule DTE21 (rules 1.0.0, sha cc09859c) fired: time to expiry 13 trading days (your threshold: at or below 21 trading days) | already past this threshold at the desk's first observation | quote grade B, Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC, run 45 min late | your plan: review a roll | not an instruction to trade; you act in your brokerage account.
```

- DELTA_WATCH and DELTA_SIGNAL read the absolute delta of the short option, from the desk's own Greeks. They are
  class G: without an implied volatility the reading is unavailable, and they wait for their confirmations. From the
  same run:

```text
pending confirmation: your rule DELTA_WATCH: absolute delta 0.48 (your threshold: at or above 0.30); observation 1 of 2
```

- PIN3 watches the short option within 3 % of its strike in its last 3 trading days. In the example rulebook it
  carries no `suppress_if_tag`, so no row tag silences it.
- CAP watches the stock above the covered call's strike, by at least a cent, in the last 21 trading days.
- EARN2 watches an earnings date within 2 trading days. Its window on the sheet:
  `EARN2 fires from 2026-11-13 to 2026-11-17 (within 2 trading days of the earnings date 2026-11-17 in research/calendar.toml)`.

## The ex-dividend test and its bound

A short call gets a dividend tier only when two conditions hold. The ex-date falls within 5 trading days after today.
The call's last trading day is on or after the last cum-dividend day, the trading day before the ex-date. A call that
expires before that day lists no dividend tier, and its expiry risk is the pin tier's.

- EXDIV compares the call's extrinsic value on the bid side, the fresh bid minus the in-the-money amount, with the
  coming dividend. It fires when the extrinsic value is below the dividend.
- The dividend facts come from `research/calendar.toml`, the same facts the assignment watch reads, so the two tools
  never disagree. An unknown dividend is never zero: the rule is unavailable.
- The fixture's WMT call expires before the cum-dividend day, so the sheet says:

```text
EXDIV not applicable: the last trading day 2026-11-20 precedes the last cum-dividend day 2026-11-23 of the 2026-11-24 ex-date
```

## Row tags: #assign-ok and #earnings-ok

- A row tag silences a rule only when the rule names that tag in its `suppress_if_tag` key.
- `#assign-ok` on a row of the group records that the owner accepts assignment. In the example rulebook it silences
  EXDIV, DELTA_WATCH, DELTA_SIGNAL and CAP. The fixture's WMT call carries it, so its messages and its sheet section
  carry the line `suppressed by your row tags: DELTA_WATCH, DELTA_SIGNAL, CAP`.
- In the example rulebook `#earnings-ok` silences EARN2, and PIN3 carries no `suppress_if_tag`.

## A cash-secured put on the sheet

The fixture book holds no open cash-secured put. An excerpt of that group's sheet section, from a copy whose SPY rows
were rewritten as one short put (the shares row removed and the put row made that of a short put), on 2026-11-03 at
19:50 UTC. The section's TP80, DELTA_WATCH, DELTA_SIGNAL, AUTOEX and USTOP lines are left out here:

```text
## AG-20261028-03 | SPY cash-secured put | option ticks 0.05 below 3.00, 0.10 at or above (standard class: desk.toml [levels] names no penny class for SPY)
TP50 fires at an option price of 5.60 (rounded down from 5.65 = credit 11.30 x (1 - 50 %)); resting-eligible: yes (below the current ask 10.10)
LOSSX fires at an option price of 33.90 (= credit 11.30 x (1 + 2)); alert level, not an order: a limit at this price executes immediately
DTE21 fires on 2026-11-18 (21 trading days before the last trading day 2026-12-18)
PIN3 fires while SPY is between 601.92 and 639.20 (within 3 % of the strike 620) from 2026-12-15 (3 trading days before the last trading day 2026-12-18)
EARN2 not applicable: SPY has no earnings (a confirmed none)
```

## Assignment: the docket's words

Aegis Position Control owns the expiry docket. Its lines name each short leg's implication in one sentence: the
shares called away for a short call, the cash needed for a short put. From the Final Call on 2026-11-20 at 19:50 UTC:

```text
AG-20261020-01 WMT261120C00105000 short 2 | covered_call | pm-settled | last trading day Fri 2026-11-20 (today) | shares called away if assigned: 200 WMT at 105 (covered by the 200 shares held) | act by Fri 2026-11-20 16:00 ET (21:00 UTC) [today]; the session close: a short option is closed only while the market trades
```

That output is diagnostic. Quote it only when the owner asks, unchanged, in a fenced block headed by the exact
command; the expiry and assignment skill has the protocol.

## What the desk does not decide

- Whether to keep a position, end it or adjust it is the owner's decision, in the brokerage account. The plan words
  in a message are the owner's own plan, never the desk's instruction.
- No alert prices a roll or compares choices. Only when the owner asks, the Aegis Structuring Desk prices them:
  the roll analysis of a covered call's short call (`aegis-roll-decision-framework`), a CLOSE, UNWIND_STOCK, LET_ASSIGN
  or ROLL ticket (`aegis-ticket-and-unwind-order`), and a covered call on shares held alone
  (`aegis-overlay-proposals`). The Exit Sentinel never prices them.
- A cash-secured put is outside the structuring scope: the desk proposes none, and a short put's replacement is
  planned for Stage 2 (not in this release). A CLOSE ticket covers it.
- The early-exercise test is the European extrinsic-versus-dividend test above. American pricing is
  planned for Stage 2 (not in this release).
- The desk checks no cash reserve. No alert reads the approval level: only the structuring tools check `desk.toml`'s
  self-attested `approval_level`, on their own answers (a covered call needs level 1 by the desk's convention).

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.
