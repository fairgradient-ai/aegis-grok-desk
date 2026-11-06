---
name: aegis-vertical-spread-management
description: How the Aegis desk measures and watches vertical spreads, for the Aegis Exit Sentinel. The four vertical structures and their net credit or net debit basis, the cost to close from the fresh sides of both legs, the take-profit and loss metrics the rules engine allows for each kind, the example rules and their thresholds, the between-the-strikes rule into expiration, the alerts on the short leg, and spread levels on the 0.01 grid. It describes the owner's adopted rules; it never chooses a rule or a threshold.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: structures
---

# Vertical spreads

This skill says how the desk reads a vertical spread and which rules of the example rulebook reach it. Every threshold
is the owner's choice, adopted rule by rule. The skill never chooses a rule, a threshold or a plan.

The fixture book (`tests/fixtures/desk`) holds no vertical, so this skill quotes no tool output for one. The line
shapes below are the fixed texts of `scripts/aegis_desk/levels.py` and `scripts/aegis_desk/expiry.py`. Never present
an example of your own as tool output.

## The basis: net credit or net debit

- A vertical is two option legs of the same right and the same expiry, with equal unsigned quantity and opposite
  signs. Any other pair of option legs is `custom`.
- The four structures are `vertical_credit_call`, `vertical_credit_put`, `vertical_debit_call` and
  `vertical_debit_put`.
- The group's net cost is the sum of its open legs' cost bases: positive for a net debit paid, negative for a net
  credit received. A vertical is a credit vertical when its net cost is below zero.
- Its take-profit and loss rules read the net credit or the net debit of the whole spread, never one leg's.

## The cost to close from the fresh sides

- On the liquidation basis, a take-profit rule reads the short leg at its fresh ask and the long leg at its fresh
  bid. Per share, the cost to close a credit vertical is the short leg's ask minus the long leg's bid.
- A take-profit may read a quote of any grade A to E: a wide quote can only delay it. A zero fresh ask on the short leg
  is no offer, and the rule is unavailable.
- A loss rule needs a two-sided fresh quote on every leg. Without one it is unavailable, and the group is
  UNAVAILABLE unless an EXIT_SIGNAL rule fired.
- The engine computes every percentage. A Bot never adds or subtracts the two legs itself.

## Take-profit and loss metrics per kind

The adoption lint holds each held structure to its own take-profit metric and at most one loss rule.

| Kind | Take-profit metric | Loss metric |
| --- | --- | --- |
| credit verticals | `pct_captured` of the net credit | one loss rule, for example `loss_multiple_of_credit` or `pct_of_max_loss` |
| debit verticals | `pct_of_max_profit` or `pct_of_debit` | one loss rule, for example `pct_of_max_loss` |

- `pct_captured` is (net credit minus cost to close) divided by the net credit, times 100.
- `pct_of_max_profit` is the unrealized P/L over the group's max profit; `pct_of_max_loss` is the unrealized loss over
  the group's max loss. Both are percentages of the group, computed by the engine.
- A credit metric never covers a debit vertical: the lint refuses it (`take_profit_metric`). The short leg's credit
  of a debit vertical covers nothing.
- A second loss rule on one structure is refused (`duplicate_loss`). A `pct_of_max_loss` threshold above 100 is
  refused (`max_loss_over_100`): the loss cannot exceed the max loss.

## The example rules and their thresholds

From the fixture's `rules/CHANGELOG.md` (each adopted `example-accepted`):

| Rule | Condition | What fires | Reaches |
| --- | --- | --- | --- |
| TP50 | `pct_captured >= 50 percent and dte > 21 trading days` | an EXIT_SIGNAL, plan close | credit verticals |
| TP80 | `pct_captured >= 80 percent` | an EXIT_SIGNAL, plan close | credit verticals |
| LOSSX | `loss_multiple_of_credit >= 2 x` | an EXIT_SIGNAL, plan close | credit verticals |
| DTE21 | `dte <= 21 trading days` | a WATCH, plan roll_review | credit verticals |
| DELTA_WATCH | `abs_delta >= 0.30 delta` | a WATCH, plan hold_review | the short leg of a credit vertical |
| DELTA_SIGNAL | `abs_delta >= 0.50 delta` | an EXIT_SIGNAL, plan roll_review | the short leg of a credit vertical |
| PIN3 | `pct_from_strike <= 3 percent and dte <= 3 trading days` | a WATCH, plan hold_review | the short leg of every vertical |
| VTP | `pct_of_max_profit >= 60 percent` | an EXIT_SIGNAL, plan close | debit verticals |
| VLOSS | `pct_of_max_loss >= 50 percent` | an EXIT_SIGNAL, plan close | debit verticals |
| LDTE30 | `dte <= 30 calendar days` | a WATCH, plan hold_review | debit verticals |
| VBETWEEN | `between_strikes is_true and dte <= 2 trading days` | an EXIT_SIGNAL, plan close | every vertical |
| EXDIV | `days_to_ex_dividend <= 5 days and extrinsic_minus_dividend < 0 usd` | an EXIT_SIGNAL, plan roll_review | the short call of a call vertical |
| EARN2 | `days_to_earnings <= 2 days and days_to_earnings >= 0 days` | a WATCH, plan hold_review | every vertical |

The 60 % of VTP is the example's documented default. Any other value is the owner's own, entered as
`owner-entered`.

## Never into expiration between the strikes

- VBETWEEN fires when the underlying is strictly between the two strikes in the last 2 trading days. In that zone
  one leg is in the money and the other is not, so the outcome at expiration is uncertain.
- On the level sheet its band runs from one cent above the lower strike to one cent below the upper strike.
- Aegis Position Control's expiry docket carries the same check on a vertical's legs, with the fixed words
  `never into expiration with the stock between the strikes`, in the shape
  `the underlying <price> is between the strikes <low> and <high>: never into expiration with the stock between the strikes`.
  The assignment watch fires (exit 10) for a vertical between its strikes on a leg that is due.

## The short leg: delta, pin and dividend alerts

- In a leg rule that names a vertical, the subject is the vertical's open short leg: the leg that carries assignment
  and delta risk. A leg's result is its group's.
- DELTA_WATCH and DELTA_SIGNAL read the short leg's absolute delta from the desk's own Greeks (class G: they need an
  implied volatility and wait for their confirmations).
- PIN3 reads the short leg of credit and debit verticals alike. In the example rulebook it carries no
  `suppress_if_tag`, so no row tag silences it.
- EXDIV reaches the short call of a call vertical, under the same cum-dividend bound as any short call: the call's
  last trading day must be on or after the last cum-dividend day, the trading day before the ex-date. An unknown
  dividend is never zero.
- AUTOEX reaches the long leg on its last trading day, when that leg is in the money.
- These alerts describe the owner's rules firing. What to do about an early assignment is the owner's decision, in
  the brokerage account.

## Levels for spreads

- A vertical's take-profit and loss levels are net spread prices on the 0.01 grid.
- A level for closing a credit vertical rounds down; a level for closing a debit vertical rounds up. So a
  take-profit level lies inside the rule's firing region and a loss alert fires no later than the rule.
- A spread line reads `fires at a spread price of` the level, with the arithmetic in brackets.
- A take-profit spread level the market has not reached says
  `resting-eligible: yes (below the current net cost to close ...)` for a credit vertical, or
  `resting-eligible: yes (above the current net value ...)` for a debit vertical. Every other spread price carries
  `alert level, not an order: a limit at this price executes immediately`.
- A level outside what the spread can reach is not printed as a price. The line says
  `cannot fire on this position: the spread price would have to fall to` the value, below zero, or
  `cannot fire on this position: the spread price would have to reach` the value, beyond the strike width.

## What the desk does not decide

- Whether to keep a vertical, end it or adjust it is the owner's decision. The plan words in a message are the owner's
  own plan, never the desk's instruction.
- Verticals stay outside the structuring scope in this release. On the owner's request the Aegis Structuring Desk
  prints a CLOSE ticket for a vertical, its short leg first; it proposes no spread and prices no replacement. A
  vertical's other choices are planned for Stage 2 (not in this release).
- No alert reads the approval level. Only the structuring tools check `desk.toml`'s self-attested `approval_level`,
  on their own answers; a vertical is level 3 by the desk's convention.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.
