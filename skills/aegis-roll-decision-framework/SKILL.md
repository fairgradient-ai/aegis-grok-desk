---
name: aegis-roll-decision-framework
description: How to read and relay a roll analysis of roll_analysis.py, for the Aegis Structuring Desk. A roll is a close plus an open, judged as a fresh trade; what the analysis covers (the short call of a covered call) and what is planned; the replacement set; the objective, its score and its tie-breaks; the statuses and the FAILS_CRITERION labels (credit_only for a net debit, strike_floor below the stock cost, earnings and ex_dividend inside the new call's life, the bands, unpriced quotes) and how the owner's row tags and criteria accept them; the approval-level check; why no candidate passing is a complete answer and CLOSE or LET_ASSIGN remain choices; the link to the Exit Sentinel's plan words; and why it never runs in a routine. The desk never places anything; the owner decides and acts in the brokerage account.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: structuring
---

# The roll decision framework

A roll analysis prices the replacement candidates for the short call of a covered call, labels each one against
the owner's own criteria in `structuring.toml`, and scores them on the owner's objective. When at least one passes
every criterion, it names the one with the highest score as the best candidate. The Aegis Structuring Desk runs the
tool `roll_analysis.py` only when the owner asks, after the owner's tap on its Ask-first card, and posts the text
verbatim. The desk never places anything: the owner decides, supplies the limit and acts in the brokerage account.

The examples are synthetic: runs on copies of `tests/fixtures/desk` with the fixture criteria written by
`desk_init.py` and acknowledged, on 2026-11-03 at 19:50 UTC with the regular quotes.

```text
python3 /workspace/aegis-grok-desk/scripts/roll_analysis.py --trade AG-20261020-01 --desk-root /workspace/aegis-desk
```

## A roll is a close plus an open

- A roll closes the held short call at its fresh ask and opens a new call at its fresh bid. Those are two trades,
  and the new call is judged as a fresh trade: every candidate shares the same close, so the score reads the new call
  alone.
- The net credit per share is the new call's bid minus the held call's ask. A negative net credit is a net debit.
  The analysis prints it per share, and the best candidate's reasons print it in all, for example `a net credit of
  0.05 (10.00 in all)`.
- The best candidate's `after both legs:` line shows the group's delta, maximum loss, maximum profit and breakeven
  after both legs, beside today's values (`now ...`). The tools compute them; a Bot never does.
- Keeping the call, closing it, or letting it go to expiry are choices too. A CLOSE or a LET_ASSIGN ticket prices
  them (`aegis-ticket-and-unwind-order`).

## What the analysis covers

- The open short call of a `covered_call` group, on a symbol held in `positions.csv`. Chains are fetched only for
  that group's underlying.
- Any other structure is NOT_APPLICABLE with its reason. A protective put's long put is replaced by a ROLL ticket
  the owner names. The roll analysis of long puts, short puts and collars, weekly expirations and an American
  early-exercise model are planned for Stage 2 (not in this release).
- An index underlying (the sandbox cannot quote one) and the setting `[roll] enabled = false` are NOT_APPLICABLE
  too.

```text
NOT_APPLICABLE | AG-20261028-03 roll analysis | AG-20261028-03 is a protective put: the roll analysis covers the short call of a covered call; a ROLL ticket prices the replacement of its long put that you name; the roll analysis of long puts, short puts and collars is planned for Stage 2 (not in this release)
```

## The replacement set

- Every call of every standard monthly expiration that the underlying lists, later than the held call's
  expiration, with its calendar days to expiry inside `[roll] dte`. With the fixture's window `[40, 75]` from
  2026-11-03 that is 2026-12-18 and 2027-01-15.
- The window spans at most 90 days, so at most four monthly chains are needed. One run makes at most 20 requests
  and fetches at most 4 chains. The WMT run above made 4 requests: the quotes, the expirations and two chains.
- If an expiration in the window cannot be served, the analysis is UNAVAILABLE: no best candidate is named on
  partial data.

## The objective and the score

The roll's objective is `extrinsic_per_day`: the bid's time value per calendar day, in percent of the share price.

- Time value E = bid - max(0, S - K), with S the underlying's mid and K the strike. The score is E / S / T x 100,
  with T the calendar days to expiry, quantized to 4 decimals. Higher ranks first.
- It uses only the fresh bid the owner could expect, the spot and the strike. It carries no forecast and no claim
  about how the trade turns out. The fixed line says so: `the score ranks the candidates only on the objective you chose; it is not a forecast`.
- Ties break in a fixed order: the net credit (higher first), the absolute delta (lower first), the expiration
  (earlier first), the strike (higher first), then the contract. A tie that was broken prints
  `tie on extrinsic_per_day broken by <rule words>`.

## Statuses and labels

Each candidate gets one status, with every reason listed (the JSON's `status` and `reasons`):

| Status | Meaning |
| --- | --- |
| `ELIGIBLE` | it passed every criterion |
| `FAILS_CRITERION` | it fails `credit_only`, `strike_floor`, `earnings` or `ex_dividend` |
| `OUTSIDE_BANDS` | it falls outside `dte`, `abs_delta`, `min_credit` or `max_spread_pct` |
| `UNPRICED` | its quote cannot price it: missing, stale, one side stale, crossed, a zero bid, too wide, or no implied volatility |

The precedence is UNPRICED, then OUTSIDE_BANDS, then FAILS_CRITERION, then ELIGIBLE. The table lists ELIGIBLE first,
then FAILS_CRITERION and OUTSIDE_BANDS (each by score and tie-breaks), then UNPRICED. In the text both failing tiers
print alike, `FAILS_CRITERION <key>: <words>`, and an unpriced quote prints `not priced: <words>`.

| Key | Fails when (the fixture's value) | Its words in the text |
| --- | --- | --- |
| `credit_only` | the roll is a net debit; a zero net passes (`true`) | `a net debit of <x>` |
| `strike_floor` | the new strike is below the held strike and below the stock cost (`cost_basis`) | `strike <K> below your stock cost <cost>` |
| `earnings` | an earnings date falls between the held call's cutoff and the new call's (`exclude`) | `earnings on <date> (<time>) fall inside the new call's life` |
| `ex_dividend` | a cum-dividend day falls inside the new call's life and not the held call's (`exclude`) | `the ex-dividend date <date> (cum <date>) falls inside the new call's life` |
| `dte` | outside the window (`[40, 75]` calendar days) | `<n> calendar days outside your window <lo> to <hi>` |
| `abs_delta` | outside the band, read at 2 decimals (`[0.15, 0.35]`) | `absolute delta <d> outside your band <lo> to <hi>` |
| `min_credit` | the new call's bid is below the minimum (`0.50` per share) | `bid <b> below your minimum <m>` |
| `max_spread_pct` | the spread is above the maximum (`25` percent) | `spread <s> % above your maximum <m> %` |

- A new leg is priced only when both sides are fresh, with a mid, at grade A to D. A held call being closed needs
  only its fresh ask.
- An earnings or ex-dividend date the calendar lists as unknown, or does not list, counts as inside the window; so
  does a listed date that has already passed (the next one is unknown). A call whose last trading day is at least 80
  days after the one known ex-date gets a second dividend label: a second ex-date may fall before its last trading
  day, and the calendar holds only one.
- The owner can accept the event labels. `#earnings-ok` on any open row of the group, or `earnings = "allow"`, accepts
  an earnings label; `#assign-ok`, or `ex_dividend = "allow"`, accepts a dividend label. An accepted label keeps the
  candidate ELIGIBLE and prints `accepted <key>: <words> (by #assign-ok)` or `(by your structuring.toml)`.
- The word FAILS_RULE is never used: on this desk a rule is an adopted exit rule, and these are the owner's criteria.

## Reading a roll analysis

```text
STRUCTURING | AG-20261020-01 roll analysis | WMT covered call | criteria structuring.toml (sha 5155427c) | Tradier sandbox, 15-min delayed, as of 19:31 UTC
replacement candidates for the short 2026-11-20 105 call: 1 passed your criteria, 19 failed
BEST CANDIDATE 1 of 20 by your objective extrinsic_per_day (the bid's time value per calendar day, in percent of the share price): 0.0467 | the highest among the 1 candidate that passed every criterion
the score ranks the candidates only on the objective you chose; it is not a forecast
leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | ask 3.50 | quote grade B, 18 min old
leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55 | quote grade B, 18 min old
reasons: 73 calendar days inside your window 40 to 75; absolute delta 0.33 inside your band 0.15 to 0.35; bid 3.55 at or above your minimum 0.50; spread 2.78 % within your maximum 25 %; a net credit of 0.05 (10.00 in all) passes credit_only; strike 115 above your stock cost 98.50
accepted ex_dividend: the ex-dividend date 2026-11-24 (cum 2026-11-23) falls inside the new call's life (by #assign-ok)
after both legs: group delta 134.07 (now 101.71), max loss 19006.00 (now 19016.00), max profit 3994.00 (now 1984.00), breakeven 95.03 (now 95.08)
candidate 2: closes the 2026-11-20 105 call and opens the 2026-12-18 112.5 call | net credit -0.70 | extrinsic_per_day 0.0597 | FAILS_CRITERION credit_only: a net debit of 0.70
candidate 3: closes the 2026-11-20 105 call and opens the 2026-12-18 115 call | net credit -1.40 | extrinsic_per_day 0.0448 | FAILS_CRITERION credit_only: a net debit of 1.40
failed your criteria: 19 (abs_delta 15, credit_only 4, strike_floor 4)
a ticket for one of these: ask the Aegis Structuring Desk for a ROLL ticket with its expiration and strike
prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:31 UTC
```

- The header names the criteria file and its sha, so the owner can tell which criteria scored the candidates.
- The second line counts the candidates that passed and failed. `BEST CANDIDATE k of n` names the objective it
  maximised, in words, and the score. The caveat follows.
- The best candidate's legs, its `reasons:` (each criterion with its value and the owner's band), its accepted
  labels and its after-state.
- Then up to `max_candidates_shown` candidates in all (3 in the fixture), each with its labels. Candidate 2 above
  scores higher but fails `credit_only`: a failing candidate is never the best, whatever its score.
- The failure counts by key, the pointer to a ticket, the model line and the footer. The JSON lists every candidate.

## When no candidate passes

```text
replacement candidates for the short 2026-11-20 150 call: 0 passed your criteria, 20 failed
no candidate passed every criterion: the desk names no best candidate
the net credits close the held 2026-11-20 150 call | ask 3.30 | quote grade C, 18 min old
candidate 1: closes the 2026-11-20 150 call and opens the 2027-01-15 160 call | net credit -0.05 | extrinsic_per_day 0.0299 | FAILS_CRITERION credit_only: a net debit of 0.05 | FAILS_CRITERION ex_dividend: the ex-dividend date 2026-12-03 (cum 2026-12-02) falls inside the new call's life
failed your criteria: 20 (abs_delta 14, min_credit 1, credit_only 6, strike_floor 6, ex_dividend 18, unpriced 2)
```

(Lines 2 to 5 and 8 of AG-20261022-02, the NVDA covered call, whose rows carry `#earnings-ok` but no `#assign-ok`.)

- NONE_QUALIFIES is a complete answer (exit 0). The desk names no best candidate, and the Bot never picks one from
  the failed list or says which failure "matters less".
- The owner's choices stand: a CLOSE ticket, a LET_ASSIGN ticket, keeping the call, a ROLL ticket for a contract the
  owner names (its labels print), or changing a criterion through the structuring interview.

## The approval-level check

The analysis first checks the roll's own legs, closing the held call and then opening a new one, against the
owner's self-attested level in `desk.toml`. A covered call's roll needs level 1. Below it, the run prints the REFUSED
line before any market data is requested (exit 20). The best candidate's legs are checked again before its text
prints. The ticket skill has the convention table.

## After a signal: the Exit Sentinel's plan words

- An alert's plan words are the owner's own plan: the plan close prints `your plan: close`; the plan roll_review
  prints `your plan: review a roll`, for example on DTE21, DELTA_SIGNAL or CAP. They are never the desk's
  instruction, and a roll analysis neither confirms nor overrides them.
- When the owner asks what the choices are, the Sentinel hands off in the Floor with the owner's words, the trade id
  and the signal id. The Structuring Desk runs a tool only on the owner's request.

## Only on request, never in a routine

- `routine.py` refuses `roll_analysis` as a step: `owner-requested only: a structuring text names candidates and
  never runs in a routine`. No alert, sheet, card, brief or routine message carries a candidate or a leg; the lint's
  rules and snapshot footers refuse them.
- Each run raises an Ask-first card; the owner's tap is the record of the request. Every run that printed a text
  appends one record to `journal/structuring.jsonl`; an answer (exit 0) also writes
  `reports/rolls/<trade id>-<YYYYMMDDTHHMMSSZ>.md` and `.json`.

## The exit codes

| Code | Answer |
| --- | --- |
| 0 | BEST, NONE_QUALIFIES or NOT_APPLICABLE |
| 20 | UNAVAILABLE (market data, `criteria_missing`, `config_unacknowledged`) or REFUSED |
| 1 | an error, such as `unknown_trade`, `structuring_invalid` or `lint_failed` |
| 2 | usage |

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.
