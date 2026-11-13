---
name: aegis-ticket-and-unwind-order
description: How to read and relay a ticket of exit_ticket.py, for the Aegis Structuring Desk. The five actions (CLOSE, UNWIND_STOCK, LET_ASSIGN, MONETIZE_PUT and ROLL), the ticket line by line, the fixed leg grammar and why it carries no trade-side words, the canonical leg order and the intermediate-exposure check, the approval-level convention and the REFUSED line, the multi-leg note, the cash at the conservative side and the mid with the most cash the steps need at once, the realized estimate, the 30-minute validity, exercise by exception under LET_ASSIGN, the short call closed before the shares, the put closed for its value, and what follows a fill. The desk never places anything; the owner supplies the limit and acts in the brokerage account.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: structuring
---

# Tickets and the unwind order

A ticket lists the legs of one choice for one group of `positions.csv`, in a fixed order, with each leg's quote.
The Aegis Structuring Desk runs `exit_ticket.py` only when the owner asks, after the owner taps approve on its
Ask-first card, and posts the text verbatim. The desk never places anything. The owner supplies the limit, and the
owner decides whether to act, in the brokerage account.

The examples are synthetic: runs on copies of `tests/fixtures/desk` with the fixture criteria written by
`desk_init.py` and acknowledged, on 2026-11-03 at 19:50 UTC with the regular quotes unless the line says otherwise.
[docs/TOOLS.md](../../docs/TOOLS.md) is the authority on every flag, key and exit code.

## The five actions

| Flag | The ticket's words | What it lists | Applies to |
| --- | --- | --- | --- |
| `--action CLOSE` | the option legs closed | every open option leg: the shorts first, then the longs; the shares stay | a group with an open option leg |
| `--action UNWIND_STOCK` | the shares and every option leg closed | the shorts, then the shares, then the longs | a group that holds shares |
| `--action LET_ASSIGN` | the short option left to expiry | no leg: the exercise-by-exception facts | a group with a short option |
| `--action MONETIZE_PUT` | the long put closed for its value | the long put | a protective put or a collar |
| `--action ROLL` | a roll of the short call, or of the long put | the held leg and its replacement | a covered call's short call, a protective put's long put |

- A ROLL ticket needs `--new-expiration YYYY-MM-DD` and `--new-strike K`. The replacement keeps the held leg's root
  and right, and its expiration must be later than the held leg's, or the command is a usage error (exit 2).
- A covered call's replacement is labelled against `[roll]` of `structuring.toml`; a protective put's, against
  `[protective_put]`. A replacement that fails a criterion is still a TICKET (exit 0): its checks table prints the
  failing criteria with a red status circle, because the owner named the contract.
- A contract the chain does not list is UNAVAILABLE: `the chain does not list that contract`.
- A listed replacement the desk cannot price is UNAVAILABLE too (exit 20, `replacement_unpriced`), with its reason.
  Synthetic, two ROLL tickets of the NVDA covered call (AG-20261022-02):

```text
UNAVAILABLE | AG-20261022-02 ROLL ticket | the 2026-12-18 180 call cannot be priced: no bid
UNAVAILABLE | AG-20261022-02 ROLL ticket | the 2027-01-15 180 call cannot be priced: the spread is wider than the quote grades allow
```

- Every other replacement is NOT_APPLICABLE with its reason. Replacing a collar's legs as a unit, a short put or a
  vertical is planned for Stage 2 (not in this release).

```text
python3 /workspace/aegis-grok-desk/scripts/exit_ticket.py --trade AG-20261020-01 --action ROLL --new-expiration 2027-01-15 --new-strike 115 --desk-root /workspace/aegis-desk
```

## A ticket, block by block

The ROLL ticket of that command, a markdown desk view the Bot posts as it is (never in a code block):

```text
### TICKET AG-20261020-01 | WMT covered call: a roll of the short call (action ROLL)

| account | computed | valid until | quotes |
|---|---|---|---|
| 1 of desk.toml (margin) | 19:50 UTC | 20:20 UTC (30 min) | 19:31 UTC, 15-min delayed |

**the legs:**
- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 3.35, ask 3.50, mid 3.425 | quote grade B
- leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55, ask 3.65, mid 3.60 | quote grade B

| cash | at the conservative prices | at the mids |
|---|--:|--:|
| net cash | +10.00 | +35.00 |
| realized on the closing leg | -16.00 | -1.00 |
| the most the steps need at once | 700.00 |  |

| check | the new call | your criteria |  |
|---|---|---|:-:|
| net per share | credit 0.05 (10.00 in all) | credit only | 🟢 |
| strike | 115 | at or above your cost 98.50 or the held strike 105 | 🟢 |
| absolute delta | 0.33 | 0.15 to 0.35 | 🟢 |
| bid | 3.55 | at least 0.50 | 🟢 |
| calendar days | 73 | 40 to 75 | 🟢 |
| spread | 2.78 % | at most 25 % | 🟢 |
| ex-dividend | 2026-11-24 (cum 2026-11-23), inside the new call's life | accepted by #assign-ok | 🟡 |

| the group | now | after both legs |
|---|--:|--:|
| max loss | 19,016.00 | 19,006.00 |
| max profit | 1,984.00 | 3,994.00 |
| breakeven | 95.08 | 95.03 |
| group delta | 101.71 | 134.07 |

**approval-level check (leg 1 first):** the highest step needs level 1 by the desk's convention, within your self-attested level 2 (desk.toml); your broker's own approval for this account decides

| after | you hold | level |
|---|---|:-:|
| leg 1 | 200 WMT shares, no option | 0 |
| leg 2 | 200 WMT shares, 2 short calls | 1 |

- in the other order, after leg 2 you would hold 2 uncovered calls (level 4)
- entered as one multi-leg order, some brokers class a roll as a spread, which can need a higher level

> - after 20:20 UTC its quotes are too old, and a new ticket needs a new request
> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC
> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
> - the desk does not see your cash balance
> - after a fill, tell Aegis Position Control the fill; it proposes the positions.csv lines
> - you supply the limit; the desk prints none
> - if you decide to act, you type it into your brokerage account yourself

computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:31 UTC
```

1. The title (the trade id, the structure, the action in words and its name), then the context strip: the account
   as `<n> of desk.toml (<type>)` (never the name the owner typed), the computation time, the validity and the
   quotes' as-of and delay.
2. The legs as list items, in the canonical order, each with its bid, ask, mid and quote grade. A quote of grade E
   adds `quote grade E: the conservative side stands`.
3. The cash table: the net cash and the realized estimate at the conservative prices and at the mids, and the most
   the steps need at once.
4. On a ROLL ticket: the replacement's checks table (a green status circle for a criterion it passes, a yellow one
   for a label the owner accepted, a red one for a criterion it fails) and the group table, now and after both legs.
5. The approval block: the highest step's level against the owner's, what the owner holds after each step, the
   other-order line and the multi-leg note (worded by what the order is) when they apply.
6. The notes: the validity, the quote source, a ROLL's model line, what follows a fill, the limit and the owner's act.

## Why the legs carry no trade-side words

- A leg line says what the leg does to the owner's holdings: `closes your short`, `closes your long`, `opens a new
  short`, `opens a new long`, or `closes 200 of your long WMT shares`. It names the contract by root, ISO date,
  strike and right, never by an OCC symbol.
- It carries no trade verb, no side, no order type, no price field, no duration and no route. The owner turns the
  effect into the broker's own terms when entering it; the desk never does.
- The structuring lint refuses any line with a leg reference anywhere (any case, any joining, the numbers in words or
  with a slash) or a leg's effect words, unless the line is exactly the fixed grammar (listed with one leading `- ` at
  most) with only the quote segments the tools print (`order_field`). It refuses the structuring categories of words: advice and preference from any subject,
  claims, urgency, suitability, order-kind abbreviations, the bare order-instruction verb, a trade verb and a price
  after a price word (SL01 to SL22), and the imperative starts IS10 to IS42. The compliance skill has the categories;
  this skill never spells them out.

## The canonical leg order and intermediate exposure

The tool builds every plan in one fixed order, so that no step leaves a short call uncovered:

- A CLOSE ticket closes the short legs, then the long legs.
- UNWIND_STOCK closes the short legs, then the shares, then the long legs: protection goes last.
- A short call's ROLL closes the held call, then opens the new one.
- A long put's ROLL opens the new put, then closes the held one, so the shares are never unhedged between the legs.
- A collar proposal opens the put, then the call. MONETIZE_PUT closes the put.

The approval-level check reads every prefix of the plan: the holdings before leg 1, after leg 1, and so on. Shares
cover a short call only in the same account, 100 shares per contract. The line `in the other order, after leg 2 you
would hold 2 uncovered calls (level 4)` shows why the order is fixed. The holdings are every open row of that
account and underlying, whatever its group. While any group of `positions.csv` is quarantined, those rows cannot be
counted, so every ticket is UNAVAILABLE: `positions.csv has a quarantined group (positions_check.py shows why): the
desk cannot count the account's coverage`.

A plan that would ever leave a short call uncovered is REFUSED with the code `intermediate_exposure` (exit 20). The
canonical orders never cause it, so it marks an invariant breach.

## Approval levels

The owner states one level, the lowest across the accounts, in the desk's convention, as `owner.approval_level` in
`desk.toml`. It is self-attested; the broker's own approval decides what the owner can enter.

```text
Approval levels by the desk's convention (brokers number and define their levels differently; state yours in this convention, and give the lowest level across your accounts):
0 no options approval: the desk shows closing tickets only
1 covered calls on shares held in the same account, and cash-secured puts
2 level 1 plus long calls and long puts (protective puts, collars)
3 level 2 plus defined-risk spreads
4 level 3 plus uncovered short options
5 level 4 plus uncovered index options and other advanced strategies
By the desk's convention, a closing transaction needs no approval (level 0); the broker's own rules apply.
```

The level of each step, on the state before it:

- Closing a short or long option you hold: level 0.
- Closing shares that no remaining short call needs: level 0.
- Closing shares or a long that leaves a remaining short option uncovered: level 4.
- Opening a short call covered by free shares in the same account: level 1; without enough free shares: level 4.
- Opening a short put (cash-secured; the desk does not see your cash): level 1.
- Opening a long call or a long put: level 2.

The ticket needs the highest step's level. Above the owner's level, the ticket is refused before any quote is fetched.
A copy whose `desk.toml` says level 0, asked for the same ROLL:

```text
REFUSED AG-20261020-01 | WMT covered call | opening 2 new short calls, covered by 200 free shares needs level 1 by the desk's convention; desk.toml records your self-attested level 0 | the desk shows nothing above your level; your broker's own approval decides what you can enter
```

The multi-leg note is printed because a broker may class a multi-leg order differently from its legs. It is worded
by what the order is: a roll (`some brokers class a roll as a spread`), a collar (`a collar as a spread`), closing
option legs (`closing option legs as a spread`) or shares with options (`shares and options together as a
combination`), each ending `which can need a higher level`. The roll ticket's line:
`entered as one multi-leg order, some brokers class a roll as a spread, which can need a higher level`.

## Cash and the realized estimate

- The cash table totals the steps at the conservative prices: a held short closes at the ask, a held long and the
  shares close at the bid, a new short opens at the bid, a new long opens at the ask. Then the same at the mids.
- `the most the steps need at once` is the largest cash need along the steps in their order. The desk does not see
  the owner's cash balance, and no tool checks it.
- `realized on the closing leg` (or `legs`) estimates the realized result of the closed rows at the conservative
  side and at the mid. The realized figure itself comes from `positions.csv` once the owner records the fill.

From the WMT UNWIND_STOCK ticket of the same run:

```text
| cash | at the conservative prices | at the mids |
|---|--:|--:|
| net cash | +20,140.00 | +20,161.00 |
| realized on the closing legs | +1,124.00 | +1,145.00 |
| the most the steps need at once | 700.00 |  |
```

## The 30-minute validity

- A ticket is valid until 30 minutes after its computation, or until the session's close when that comes first. The
  context strip's `(30 min)` appears only when it runs its full 30 minutes.
- After that its quotes are too old: a new ticket needs a new request (the first note says so, with the time).
- A ticket is computed only while the session is open (a regular or an early-close day). Otherwise:

```text
UNAVAILABLE | AG-20261020-01 CLOSE ticket | a ticket needs quotes live enough to act on: the session is not open
```

(Synthetic: the after-close quotes at 21:30 UTC.)

## UNWIND_STOCK: the short call before the shares

Its legs and the two notes under its approval table:

```text
**the legs:**
- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 3.35, ask 3.50, mid 3.425 | quote grade B
- leg 2 of 2: closes 200 of your long WMT shares | bid 104.20, ask 104.26, mid 104.23 | quote grade A

- in the other order, after leg 2 you would hold 2 uncovered calls (level 4)
- entered as one multi-leg order, some brokers class shares and options together as a combination, which can need a higher level
```

The shares go only after the call that relies on them. On a protective put the shares go first and the put last.

## LET_ASSIGN: exercise by exception

A LET_ASSIGN ticket lists no leg. Under its title and context strip it states the facts of leaving the short option
to expiry; its notes end with the post-expiry check of Aegis Position Control:

```text
no leg to enter: if the 105 call is at least 0.01 in the money at expiry, OCC exercises it by exception; your broker's cutoff is 16:30 ET (desk.toml)

| the 2026-11-20 105 call |  |
|---|---|
| now | out of the money: the spot mid 104.23 is below its strike |
| if you decide otherwise | you act by Fri 2026-11-20 16:00 ET (21:00 UTC): a short option left to expiry can be closed only while the session trades |
| if assigned | 200 WMT shares are called away at the strike, cash +21,000.00; realized on the call and the shares +1,984.00 |
| ex-dividend | the ex-dividend date 2026-11-24 falls after the call's last trading day 2026-11-20 |
```

- OCC exercises an option at least 0.01 in the money at expiry by exception. The broker's cutoff in that line,
  `broker_exercise_cutoff_et` in `desk.toml`, is for an exercise instruction on a long option.
- A short option left to expiry can be closed only while the session trades, so the act-by is the last trading day's
  session close (16:00 ET, 13:00 ET on an early close), or the broker's cutoff when that is earlier; the expiry
  sheet of Aegis Position Control applies the same rule to a short leg. On the last trading day `[today]` follows the
  time.
- The expiry and assignment skill of Aegis Position Control has the protocol; the outcome after expiry is an
  inference the owner confirms with the broker.

## MONETIZE_PUT: the put closed for its value

From the SPY protective put (AG-20261028-03), its legs and the put table that follows its cash table:

```text
**the legs:**
- leg 1 of 1: closes your long 1 x SPY 2026-12-18 620 put | bid 9.80, ask 10.10, mid 9.95 | quote grade B

| the put |  |
|---|---|
| the 2026-12-18 620 put | out of the money: the spot mid 648.12 is above its strike, so its bid 9.80 is all time value |
| after the leg | your 100 SPY shares carry no put: their downside is unhedged |
```

## A long put's ROLL

The same group with `--action ROLL --new-expiration 2027-03-19 --new-strike 610`: the new put opens first, and its
checks read `[protective_put]`.

```text
**the legs:**
- leg 1 of 2: opens a new long 1 x SPY 2027-03-19 610 put | bid 22.90, ask 23.10, mid 23.00 | quote grade A
- leg 2 of 2: closes your long 1 x SPY 2026-12-18 620 put | bid 9.80, ask 10.10, mid 9.95 | quote grade B

| check | the new put | your criteria |  |
|---|---|---|:-:|
| floor | 5.88 % below | 3 to 10 % | 🟢 |
| cost | ask 23.10 (2,310.00 in all) |  |  |
| covered at your stress drop | 91.504 per share (9,150.40 in all) | drop 20 % (stressed price 518.496) | 🟢 |
| calendar days | 136 | 120 to 210 | 🟢 |
| spread | 0.87 % | at most 25 % | 🟢 |
```

## After a fill

- A ticket carries no `positions.csv` lines: the fill price is unknown until the owner reports it, and the file's
  edits have one owner. The ticket says `after a fill, tell Aegis Position Control the fill; it proposes the
  positions.csv lines`.
- Aegis Position Control proposes the lines from the owner's own fill, with the close, partial-close and roll
  recipes. The owner applies them. The next book check posts CONFIG CHANGED until `ack.py config --owner-tapped`.
- After a LET_ASSIGN ticket, the post-expiry reconciliation proposes the lines after expiry.

## Files and the record

- A TICKET (exit 0) writes `tickets/TK-<trade id>-<ACTION>-<YYYYMMDDTHHMMSSZ>.md` and `.json`, for example
  `tickets/TK-AG-20261020-01-ROLL-20261103T195000Z.md`. Nothing else writes a ticket file. The same bytes again at the
  same instant are a no-op; different bytes are exit 1 `ticket_exists`. The instant is read to the whole second, so a
  second ROLL ticket of the group for another replacement in the same second is `ticket_exists` too.
- Every run that printed a text (exit 0 or 20) appends one record to `journal/structuring.jsonl`: the instant, the
  tool, the action, the trade id, the verdict, the shas of `structuring.toml`, `desk.toml` and `positions.csv`, a
  refusal code if any, and the sha of the printed text. No other tool reads it.

## What a ticket never does

- It never prints a limit, a side, an order type, a duration or an OCC symbol, and it never prints a cell the owner
  typed.
- It never fires an alert and never runs in a routine. It never exits 10.
- It never claims the owner's broker approves the order, and never sees the owner's cash.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.
