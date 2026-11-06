---
name: aegis-overlay-proposals
description: How the owner's criteria file works and how to read and relay a proposal of propose.py, for the Aegis Structuring Desk. Overlays only on shares the owner holds in positions.csv (a covered call on free shares in the same account, a protective put, a collar), never a new symbol, a spread, an uncovered short or a cash-secured put; structuring.toml with every key, bound and documented default, the disclosure id and text, the interview and the acknowledgement; the objectives in words with their tie-breaks; the best-candidate framing and the fixed lines; no free shares and the other NOTHING_TO_PROPOSE answers; the approval levels of each structure; and the files a proposal writes. The desk never places anything; the owner decides, supplies the limit and acts in the brokerage account.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: structuring
---

# Overlay proposals

A proposal prices an overlay on shares the owner already holds: a covered call, a protective put or a collar. A tool
labels every candidate against the owner's criteria in `structuring.toml`, scores them on the owner's objective and,
when one or more pass every criterion, names the highest-scoring one as the best candidate. The Aegis Structuring
Desk runs `propose.py` only when the owner asks, after the owner's tap on its Ask-first card, and posts the text
verbatim. The desk never places anything: the owner decides, supplies the limit and acts in the brokerage account.

The examples are synthetic: runs on copies of `tests/fixtures/desk` with the fixture criteria written by
`desk_init.py` and acknowledged, on 2026-11-03 at 19:50 UTC with the regular quotes.

```text
python3 /workspace/aegis-grok-desk/scripts/propose.py --trade AG-20261028-03 --structure collar --desk-root /workspace/aegis-desk
```

## What may be proposed

- Only on a group of `positions.csv` that holds shares and nothing else, at least 100 shares. The contracts are the
  shares divided by 100, rounded down. When the owner records the fills with Aegis Position Control, the new legs
  join the same trade id, so the group becomes a covered call, a protective put or a collar.
- A covered call needs free shares in the same account: shares that no short call of that account already relies on.
  Shares in one account never cover a call in another. Every share of the group must be free: the desk proposes
  nothing on part of a group's shares.
- While any group of `positions.csv` is quarantined, the desk cannot count which shares are free, and every proposal
  is UNAVAILABLE: `positions.csv has a quarantined group (positions_check.py shows why): the desk cannot count the
  account's coverage`.
- A collar is one unit: a long put and a short call of one expiration, the put opened first.
- Never a new symbol, a spread, an uncovered short or a cash-secured put. Chains are fetched only for the group's own
  underlying; an index underlying, which the sandbox cannot quote, is NOT_APPLICABLE.
- A section with `enabled = false` answers NOT_APPLICABLE: `the <section> section is disabled in your
  structuring.toml`.

Everything else is NOTHING_TO_PROPOSE, with its reason (exit 0). From the main fixture desk and from a copy whose
WMT call row was moved to another trade id:

```text
NOTHING_TO_PROPOSE | AG-20261028-03 covered call proposal | the 100 SPY shares already carry 1 long put
NOTHING_TO_PROPOSE | AG-20261020-01 covered call proposal | AG-20261020-01 is a covered call: ask for a roll analysis
NOTHING_TO_PROPOSE | AG-20261020-01 covered call proposal | no free shares: 200 WMT shares are covered by 2 short calls; the desk proposes no covered call
```

A copy whose WMT group holds 300 shares while one short call of another WMT group in the same account relies on 100
of them:

```text
NOTHING_TO_PROPOSE | AG-20261020-01 covered call proposal | 200 of the 300 WMT shares are free: 1 short call held in another group of this account relies on the rest; the desk proposes no covered call on part of a group's shares
```

A covered call added to shares that already carry a long put would change the group: a collar when the call shares
the put's expiration, otherwise a mix of expirations the desk cannot measure. The desk proposes neither on such a
group; a collar is proposed as a unit, on shares alone, at level 2. Splitting a share row between groups is not in
this release.

## structuring.toml: keys, bounds and the documented defaults

`desk_init.py` writes `/workspace/aegis-desk/structuring.toml` from the owner's interview answers. The values below
are the documented defaults the interview shows, and the fixture's. They are starting points, never a judgment for
any account. Every section is required, an unknown key is refused, a boolean is never a number, a decimal has at
most 2 decimals, and every `dte` window is calendar days with 7 <= lo < hi <= 400 and hi - lo <= 90.

| Section | Key | Default | Bounds and meaning |
| --- | --- | --- | --- |
| `[disclosure]` | `acknowledged` | `"structuring-suggestions-1"` | exactly this id, after the owner acknowledged the disclosure |
| `[general]` | `max_spread_pct` | `25` | above 0, at most 25: the widest spread a new leg may have, in percent |
| | `max_candidates_shown` | `3` | an integer 1 to 10: lines in the text (the JSON lists every candidate) |
| `[covered_call]` | `enabled` | `true` | a boolean |
| | `dte` | `[40, 75]` | the window of the call's expiration |
| | `abs_delta` | `[0.15, 0.35]` | 0.05 <= lo < hi <= 0.60, read at 2 decimals |
| | `min_credit` | `0.50` | per share, at the bid; at least 0.01 |
| | `strike_floor` | `"cost_basis"` | `cost_basis` (no strike below the stock cost) or `none` |
| | `earnings`, `ex_dividend` | `"exclude"` | `exclude` or `allow` |
| | `objective` | `"extrinsic_per_day"` | the one objective of the section |
| the replacement call's section `[roll]` | the covered call's keys | the same values | for a roll analysis and a covered call's ROLL ticket |
| | `credit_only` | `true` | a net debit fails it; a zero net passes |
| `[protective_put]` | `enabled` | `true` | a boolean |
| | `dte` | `[120, 210]` | the window of the put's expiration |
| | `floor_pct` | `[3, 10]` | (S - K) / S x 100 at 2 decimals; 0 <= lo < hi <= 50 |
| | `stress_drop_pct` | `20` | above the floor band's hi, at most 90 |
| | `objective` | `"stress_cost_per_day"` | the one objective of the section |
| `[collar]` | `enabled`, `dte` | `true`, `[120, 210]` | as above |
| | `floor_pct`, `cap_pct` | `[3, 10]`, `[2, 10]` | the cap is (Kc - S) / S x 100 at 2 decimals; 0 < lo < hi <= 100 |
| | `max_net_debit` | `0.00` | per share, at least 0: 0.00 means a zero cost or a credit |
| | `strike_floor`, `earnings`, `ex_dividend` | `"cost_basis"`, `"exclude"`, `"exclude"` | as for the covered call |
| | `objective` | `"cap_within_budget"` | the one objective of the section |

An earnings or ex-dividend date that `research/calendar.toml` lists as unknown, or does not list at all, counts as
inside the window; it is never treated as none. A listed date that has already passed counts as unknown and inside
the window too, because the next one is not known. The labels and their acceptance by `#earnings-ok`, `#assign-ok` or
`allow` are in `aegis-roll-decision-framework`.

## The interview and the acknowledgement

The Structuring Desk runs the interview in its own chat, at SETUP step 9 or on the owner's first request:

1. Section by section, it shows the defaults above and changes only what the owner states.
2. Last, it shows the disclosure below, verbatim, and records `disclosure_acknowledged` only after the owner says
   they acknowledge it.
3. It writes the answers (the `structuring` shape of `desk_init` in [docs/TOOLS.md](../../docs/TOOLS.md)) to
   `/workspace/aegis-desk/setup/structuring-answers.json`, then, after the owner's tap, runs:

```text
python3 /workspace/aegis-grok-desk/scripts/desk_init.py --answers /workspace/aegis-desk/setup/structuring-answers.json --desk-root /workspace/aegis-desk
```

4. After the owner's next tap, the Aegis Desk Lead acknowledges it with `ack.py config --owner-tapped`.

On the fixture answers, then the acknowledgement on the fixture desk (synthetic):

```text
wrote structuring.toml (1090 bytes, sha256 5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c)
acknowledge with ack.py config --owner-tapped: the structuring tools show nothing until structuring.toml is acknowledged
configuration acknowledged at 2026-11-03 19:49 UTC: desk.toml sha 0af433cf, positions.csv sha 9483c8f3, adopted rulebook 1.0.0 sha cc09859c, structuring.toml sha 5155427c
```

- A second run while the file exists is refused (exit 1, `structuring_exists`); a change is the owner's tap on
  `--replace --owner-tapped` with full answers, then the acknowledgement again.
- A value the reader refuses is exit 1 `structuring_invalid`, and nothing is written.
- No routine and no structuring tool ever writes the file. A hand edit changes its sha, and every structuring tool
  then prints only its CONFIG CHANGED line until the owner acknowledges it.

The disclosure, id `structuring-suggestions-1`, exactly:

```text
The Aegis Structuring Desk answers only when you ask. For a position you hold in positions.csv, its tools list candidates from delayed Tradier sandbox quotes: closing or unwinding it, a replacement for a short option you are closing or that is expiring, or a covered call, protective put or collar on shares you hold. They score the candidates by the objective and criteria in this file and name the highest-scoring one with its reasons. That is a personalized suggestion computed by deterministic tools and posted by an AI assistant; it is not an order, not a forecast and not a judgment that a trade suits you. The desk knows only your positions, these criteria and the approval level you stated; it does not see your cash, your other holdings, your objectives or your taxes. It never places anything: you decide, you supply the limit and you act in your brokerage account. Options involve substantial risk; you confirm that you received the OCC booklet Characteristics and Risks of Standardized Options.
```

## The objectives in words

Each section declares one objective, echoed in every answer with the file's sha. The words describe a formula; they
never name a gain of any kind and never forecast.

| Objective | Words in the text | Formula | Order | Ties broken by |
| --- | --- | --- | --- | --- |
| `extrinsic_per_day` | the bid's time value per calendar day, in percent of the share price | (bid - max(0, S - K)) / S / T x 100 | higher first | the absolute delta (lower first), the expiration (earlier first), the strike (higher first), the contract |
| `stress_cost_per_day` | the put's ask per calendar day per 100 of loss it offsets at your stress drop | ask / T / (K - S_x) x 100, with S_x = S x (1 - x / 100) | lower first | the strike (higher first), the expiration (later first), the contract |
| `cap_within_budget` | the cap's distance above the share price, in percent, among pairs within your net-debit budget | (Kc - S) / S x 100 | higher first | the put strike (higher first), the net debit (lower first), the expiration (later first), the contracts |

S is the underlying's mid, K the strike, T the calendar days to expiry and x `stress_drop_pct`. Scores are quantized
to 4 decimals, the cap to 2. A put whose strike is at or below the stressed price covers no loss there and falls
outside the bands. A collar pair whose net debit (the put's ask minus the call's bid) is above `max_net_debit` falls
outside the bands too.

## The best candidate and the fixed lines

- `BEST CANDIDATE k of n by your objective <id> (<words>): <score> | the highest among the m candidates that passed
  every criterion` (`the lowest` for `stress_cost_per_day`). It names the objective it maximised, never a preference.
- `the score ranks the candidates only on the objective you chose; it is not a forecast` always follows it.
- `tie on <id> broken by <rule words>` appears when a tie decided the order.
- `no candidate passed every criterion: the desk names no best candidate` is NONE_QUALIFIES, a complete answer.
- `prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not
  modelled`, then `you supply the limit; the desk prints none` and `if you decide to act, you type it into your
  brokerage account yourself and you supply the limit`, then the structuring footer.
- "Best" means only the highest score on the owner's objective among the candidates that passed every criterion.
  The Bot never adds a word from the lint's advice, claim or urgency categories, and never compares candidates.

## Reading a protective put proposal

A copy whose SPY put row was removed, so AG-20261028-03 holds 100 SPY shares only:

```text
PROPOSAL SPY protective put | on 100 SPY shares you hold in account 2 of desk.toml (ira) | criteria structuring.toml (sha 5155427c) | Tradier sandbox, 15-min delayed, as of 19:31 UTC
candidates for a protective put on the 100 SPY shares: 5 passed your criteria, 7 failed
BEST CANDIDATE 1 of 12 by your objective stress_cost_per_day (the put's ask per calendar day per 100 of loss it offsets at your stress drop): 0.1856 | the lowest among the 5 candidates that passed every criterion
the score ranks the candidates only on the objective you chose; it is not a forecast
leg 1 of 1: opens a new long 1 x SPY 2027-03-19 610 put | bid 22.90, ask 23.10, mid 23.00 | quote grade A, Tradier sandbox, 15-min delayed, as of 19:31 UTC
reasons: 136 calendar days inside your window 120 to 210; floor 5.88 % inside your band 3 to 10; it covers 91.504 per share (9150.40 in all) at your stress drop of 20 % (stressed price 518.496); spread 0.87 % within your maximum 25 %
after the leg: group delta 69.69 (now 100.00), max loss 4550.00 (now 63240.00), max profit unbounded (now unbounded), breakeven 655.50 (now 632.40)
leg order: after leg 1 you hold 100 SPY shares and 1 long put (level 2 by the desk's convention)
approval: the highest step needs level 2 by the desk's convention, within your self-attested level 2 (desk.toml); your broker's own approval for this account decides
candidate 2: the 2027-03-19 620 put | ask 25.65 | stress_cost_per_day 0.1858
candidate 3: the 2027-03-19 625 put | ask 27.05 | stress_cost_per_day 0.1868
failed your criteria: 7 (floor_pct 7)
prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
you supply the limit; the desk prints none
if you decide to act, you type it into your brokerage account yourself and you supply the limit
computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:31 UTC
```

The header names the shares and the account as `account <n> of desk.toml (<type>)`, never the name typed in
`positions.csv`. The best candidate's block carries its legs, its reasons, its after-state and its approval lines.

## A collar's best block

The same copy with `#assign-ok` on the share row, `--structure collar`, lines 3 to 14:

```text
BEST CANDIDATE 1 of 10 by your objective cap_within_budget (the cap's distance above the share price, in percent, among pairs within your net-debit budget): 4.92 | the highest among the 4 candidates that passed every criterion
the score ranks the candidates only on the objective you chose; it is not a forecast
tie on cap_within_budget broken by the put strike (higher first)
leg 1 of 2: opens a new long 1 x SPY 2027-03-19 620 put | bid 25.45, ask 25.65, mid 25.55 | quote grade A, Tradier sandbox, 15-min delayed, as of 19:31 UTC
leg 2 of 2: opens a new short 1 x SPY 2027-03-19 680 call | bid 26.40, ask 26.60, mid 26.50 | quote grade A, Tradier sandbox, 15-min delayed, as of 19:31 UTC
reasons: 136 calendar days inside your window 120 to 210; floor 4.34 % inside your band 3 to 10; cap 4.92 % inside your band 2 to 10; a net credit of 0.75 within your net-debit budget 0.00; spreads 0.78 % and 0.75 % within your maximum 25 %; call strike 680 above your stock cost 632.40
accepted ex_dividend: the ex-dividend date 2026-12-18 (estimated, cum 2026-12-17) falls inside the call's life (by #assign-ok)
accepted ex_dividend: a second ex-date may fall before the call's last trading day; the calendar holds one (by #assign-ok)
after both legs: group delta 24.14 (now 100.00), max loss 1165.00 (now 63240.00), max profit 4835.00 (now unbounded), breakeven 631.65 (now 632.40)
leg order: leg 1 first; after leg 1 you hold 100 SPY shares and 1 long put (level 2 by the desk's convention); after leg 2 you hold 100 SPY shares, 1 short call and 1 long put (level 1)
entered as one multi-leg order, some brokers class a collar as a spread, which can need a higher level
approval: the highest step needs level 2 by the desk's convention, within your self-attested level 2 (desk.toml); your broker's own approval for this account decides
```

Without the tag every pair fails `ex_dividend`, and the same run is NONE_QUALIFIES. Legs outside their own bands are
counted, never paired: `not paired, outside their bands: 7 puts outside your floor band and 10 calls outside your
cap band`.

## Approval levels of a proposal

By the desk's convention a covered call needs level 1, and a protective put or a collar level 2 (the convention table
is in `aegis-ticket-and-unwind-order`). The check reads the structure's own legs before any market data is
requested, and the best candidate's again before its text prints. A copy at level 1, asked for the protective put:

```text
REFUSED AG-20261028-03 | SPY protective put | opening 1 new long put needs level 2 by the desk's convention; desk.toml records your self-attested level 1 | the desk shows nothing above your level; your broker's own approval decides what you can enter
```

## Files and exit codes

- A complete evaluation (BEST or NONE_QUALIFIES) writes `reports/proposals/<trade id>-<structure>-<YYYYMMDDTHHMMSSZ>.md`
  and `.json`; NOT_APPLICABLE and NOTHING_TO_PROPOSE write no report. Every run that printed a text (exit 0 or 20)
  appends one record to `journal/structuring.jsonl`.
- 0: BEST, NONE_QUALIFIES, NOTHING_TO_PROPOSE or NOT_APPLICABLE. 20: UNAVAILABLE (market data, `no_candidate_priced`,
  `positions_quarantined`, `criteria_missing`, `config_unacknowledged`) or REFUSED. 1: an error, such as
  `unknown_trade`, `structuring_invalid` or `lint_failed`. 2: usage, for example a `--structure` outside the three.
  Never 10.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.
