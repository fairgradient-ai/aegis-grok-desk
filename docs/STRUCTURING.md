# The Aegis Structuring Desk

This page is the reference for the owner and for counsel. It covers the fourth Stage-1 Bot, the Aegis Structuring
Desk: what you can ask it, your criteria file and its interview, how a tool picks one best candidate, the
approval-level check, the three kinds of answer with synthetic examples, the footer and the disclosure, and the
limits. Every command, flag and exit code is in [TOOLS.md](TOOLS.md); the Bot's own instructions are
[agents/structuring-desk.md](../agents/structuring-desk.md).

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

The Structuring Desk answers only when you ask. Its tools price your choices for a position you already hold in
`positions.csv`, score the candidates by the objective and criteria you set in `structuring.toml`, and name the
highest-scoring candidate with its reasons. That is a personalized suggestion, computed by deterministic tools and
posted by an AI assistant. It is not an order. You decide, you supply the limit, and you act in your brokerage account.
Counsel has not signed this wording yet ([RELEASE.md](RELEASE.md), step 4).

## What you can ask for

| Your question | Tool | Applies to |
| --- | --- | --- |
| a ticket that closes the option legs of a group (`--action CLOSE`) | `exit_ticket.py` | any group with open option legs; the shares stay |
| a ticket that closes the shares and every option leg (`--action UNWIND_STOCK`) | `exit_ticket.py` | a group that holds shares |
| what happens if a short option is left to expiry (`--action LET_ASSIGN`) | `exit_ticket.py` | short options only |
| a ticket that closes the long put for its value (`--action MONETIZE_PUT`) | `exit_ticket.py` | protective put and collar groups |
| a ticket that replaces a held leg with one you name (`--action ROLL`) | `exit_ticket.py` | the short call of a covered call, and the long put of a protective put |
| the replacement candidates for a covered call's short call | `roll_analysis.py` | covered call groups |
| a covered call, a protective put or a collar on shares you hold (`--structure`) | `propose.py` | groups that hold shares only |

The commands, with the trade ids of the synthetic fixture:

```text
python3 /workspace/aegis-grok-desk/scripts/roll_analysis.py --trade AG-20261020-01 --desk-root /workspace/aegis-desk
python3 /workspace/aegis-grok-desk/scripts/propose.py --trade AG-20261028-03 --structure protective_put --desk-root /workspace/aegis-desk
python3 /workspace/aegis-grok-desk/scripts/exit_ticket.py --trade AG-20261020-01 --action CLOSE --desk-root /workspace/aegis-desk
python3 /workspace/aegis-grok-desk/scripts/exit_ticket.py --trade AG-20261020-01 --action ROLL --new-expiration 2027-01-15 --new-strike 115 --desk-root /workspace/aegis-desk
```

- Every tool takes `--trade`: the trade id of a group in `positions.csv`. There is no symbol flag, so the desk never
  looks at a symbol you do not hold. Chains are fetched only for that group's underlying.
- A ROLL ticket needs both `--new-expiration` and `--new-strike`, and any other action refuses them (exit 2, usage).
  The replacement keeps the held leg's root and right, and its expiration must be later than the held leg's. A
  contract the chain does not list answers UNAVAILABLE: `the chain does not list that contract`; a listed one the
  desk cannot price answers UNAVAILABLE with its reason ([A ticket](#a-ticket) below).
- A ticket never depends on an earlier report: you name the replacement by its expiration and strike, as the roll
  analysis prints them.
- Every answer is the tool's own text, linted, which the Structuring Desk posts verbatim, footer included.

What the desk never does: it proposes no new symbol, no spread, no uncovered short option and no cash-secured put. A
group that already holds an option leg, or fewer than 100 shares, or whose shares a short call of another group in the
same account partly relies on, gets NOTHING_TO_PROPOSE with its reason; index underlyings are NOT_APPLICABLE, because
the data source cannot quote index options. The roll analysis of long puts, short puts and collars, weekly
expirations, American pricing, scenario and iv_snapshot, a cash-balance check and per-account approval levels are
planned for Stage 2 (not in this release). A collar's two legs are replaced as a unit, which is also planned for Stage 2 (not in this release); a
ROLL of either collar leg is NOT_APPLICABLE.

## Only when you ask

- The Structuring Desk runs its tools only when your own message asks, directly or quoted word for word in a handoff
  from another Bot. It never runs them on its own initiative or because another Bot asked. No other Bot runs them.
- Each run asks first: the owner-authority Auto-review rule of SETUP step 9 names `exit_ticket.py`,
  `roll_analysis.py` and `propose.py`, and your tap on its card is the record of your request.
- They never run in a routine. `routine.py` refuses them as routine steps with the reason
  `owner-requested only: a structuring text names candidates and never runs in a routine`.
- No alert, level sheet, plan card, brief or routine message can carry a leg or a candidate: under the rules footer
  and the market-snapshot footer, the alert lint refuses a leg reference, a ranked candidate, an action tag and the
  first line of a structuring answer.
- The Structuring Desk never fires an alert and owns no routine. The Aegis Exit Sentinel, which fires the signals,
  never prices the choices.

## Your criteria: structuring.toml

`/workspace/aegis-desk/structuring.toml` holds your criteria. You write it through an interview; no routine and no
structuring tool ever writes it. Until it exists and you have acknowledged it, the structuring tools show nothing.

### The interview

The Desk Lead runs it in its own chat (the one chat you use), at SETUP step 9 or at your first request. If you ask before you have
said "add my positions", the Desk Lead first adds that step's two safety settings (the two Ask-first Auto-review rules
and the connected-apps check), because the interview ends in an owner-tapped command:

1. It shows each section with its documented defaults (below). You keep each value, change it within its bounds, or
   switch a structure off with `enabled = false`.
2. For each structure you keep on, it asks which objective ranks the candidates. It shows every objective that
   section accepts ([The objectives](#the-objectives) below), with its words and the documented default first. You
   choose one; if you don't, the default stays, and a file that keeps the defaults ranks exactly as before. Where you
   choose `band_centre`, it also asks whether you want your own centres or weights (below); without them the centre
   of each band and equal weights stand.
3. Last, it shows the disclosure (below) word for word. It records `disclosure_acknowledged` only after you say that
   you acknowledge it.
4. It writes your answers as JSON to `/workspace/aegis-desk/setup/structuring-answers.json`, in the shape of
   `desk_init` "Input" in [TOOLS.md](TOOLS.md), and after your tap runs the **owner-authority** command:

   ```text
   python3 /workspace/aegis-grok-desk/scripts/desk_init.py --answers /workspace/aegis-desk/setup/structuring-answers.json --desk-root /workspace/aegis-desk
   ```

   Synthetic, a copy of the fixture desk with the fixture answers `tests/fixtures/init/structuring-answers.json`:

   ```text
   wrote structuring.toml (1090 bytes, sha256 5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c)
   acknowledge with ack.py config --owner-tapped: the structuring tools show nothing until structuring.toml is acknowledged
   ```

5. The Aegis Desk Lead then runs `ack.py config --owner-tapped` after your tap. Synthetic, the same copy:

   ```text
   configuration acknowledged at 2026-11-03 19:49 UTC: desk.toml sha f08a18dc, positions.csv sha 9483c8f3, adopted rulebook 1.0.0 sha cc09859c, structuring.toml sha 5155427c
   ```

An existing `structuring.toml` is refused (exit 1, `structuring_exists`) unless you tap `--replace --owner-tapped`,
which rewrites the whole file from full answers. Answers that the reader would refuse are exit 1
(`structuring_invalid`) and nothing is written.

### The documented defaults

They are starting points, not a judgment that they fit your account. They are also the answers the synthetic
examples on this page assume.

| Section | Key | Default | Allowed | What it does |
| --- | --- | --- | --- | --- |
| `general` | `max_spread_pct` | 25 | above 0, at most 25 | the widest spread of a new leg, in percent of its mid; a new leg also needs both sides fresh at quote grade A to D |
| `general` | `max_candidates_shown` | 3 | 1 to 10 | how many candidates the text shows; the JSON lists every one |
| every structure | `enabled` | true | true or false | `false` makes that structure NOT_APPLICABLE: `disabled in your structuring.toml` |
| every structure | `dte` | [40, 75] for `covered_call` and `roll`; [120, 210] for `protective_put` and `collar` | calendar days, 7 to 400, low below high, at most 90 apart | the window of expirations considered |
| `covered_call`, `roll` | `abs_delta` | [0.15, 0.35] | 0.05 to 0.60, low below high | the new call's absolute delta, read at 2 decimal places as the exit rules read it |
| `covered_call`, `roll` | `min_credit` | 0.50 | at least 0.01 | the new call's bid per share |
| `roll` | `credit_only` | true | true or false | a net debit fails; a net of zero passes |
| `covered_call`, `roll`, `collar` | `strike_floor` | `cost_basis` | `cost_basis` or `none` | a call strike below your stock cost fails (for a roll: below both the held strike and your stock cost) |
| `covered_call`, `roll`, `collar` | `earnings` | `exclude` | `exclude` or `allow` | an earnings date inside the call's life fails, unless allowed |
| `covered_call`, `roll`, `collar` | `ex_dividend` | `exclude` | `exclude` or `allow` | an ex-dividend date inside the call's life fails, unless allowed |
| `protective_put`, `collar` | `floor_pct` | [3, 10] | 0 to 50, low below high | the put's distance below the share price, (S - K) / S x 100 |
| `protective_put` | `stress_drop_pct` | 20 | above the top of `floor_pct`, at most 90 | the drop in the share price the put's cost is measured against; it shapes the score only, because every put inside `floor_pct` is then struck above the stressed price |
| `collar` | `cap_pct` | [2, 10] | above 0, at most 100, low below high | the call's distance above the share price, (Kc - S) / S x 100 |
| `collar` | `max_net_debit` | 0.00 | at least 0 | the put's ask minus the call's bid, per share; 0.00 means a zero cost or a credit |
| every structure | `objective` | the section's first objective (below) | one of the section's objectives (below) | the score the candidates are ranked by, echoed with the file's sha |

Every decimal carries at most 2 decimal places. The reader is strict: every section and key is required, an unknown
key is an error, and a boolean is never a number.

**Your own centres and weights (`schema_version = 2`).** A section that ranks by `band_centre` (`[covered_call]`,
`[protective_put]`, `[collar]`) may also set, after its `objective`:

| Key | Allowed | What it does |
| --- | --- | --- |
| `dte_centre`, `abs_delta_centre`, `floor_pct_centre`, `cap_pct_centre` | a number inside that band | the point in the band you prefer; without it, the band's midpoint |
| `band_weights` | an inline table, for example `{ dte = 0.5, quote = 2 }`, of numbers above 0 and at most 10 for the section's bands and `quote` | how much one half-width of that band (or one leg quoted at your spread limit) counts; each band you leave out counts 1 |

**Your roll test (`schema_version = 2`).** `[roll]` may give `roll_test` in place of `credit_only`: `"credit"` fails a
net debit (as `credit_only = true`), `"none"` tests neither (as `credit_only = false`), and `"net_time_value"` fails a
roll whose new call's time value at its bid is below the held call's time value at its ask, N = E_new - E_held < 0.
It reads time value, not cash: a held call in the money carries your own share gain as intrinsic value, so a roll
out of it can need a debit yet keep more time value (FAILS_CRITERION `roll_test`: `it gives up 0.70 a share of time
value`), and a deep in-the-money roll can pay a credit yet give time value up. Its checks row shows N per share, with
`(within the held call's own quote spread)` when N is smaller than the held call's half-spread. Giving both keys is
refused.

The interview asks for them only for a section where you chose `band_centre`, and `desk_init.py` then writes
`schema_version = 2`; a file without them stays `schema_version = 1`, byte for byte. Under `schema_version = 1`
either key is unknown, and either key with another objective is refused. Answers that set them print `band_centre
with your own settings in structuring.toml: ...` under the objective's words, naming each centre and weight. One rule is fixed, not a key: an earnings or ex-dividend date that
`research/calendar.toml` lists as unknown, or does not list at all, counts as inside the window; it is never treated
as none. A listed date that has already passed counts the same way, because the next one is not known.

The file the fixture answers render, byte for byte (`tests/fixtures/init/expected_structuring.toml`):

```toml
# structuring.toml: rendered by scripts/desk_init.py from the owner's structuring interview; a hand edit changes its
# hash, and the structuring tools show nothing until the owner acknowledges it (ack.py config --owner-tapped).
schema_version = 1

[disclosure]
acknowledged = "structuring-suggestions-1"

[general]
max_spread_pct = 25
max_candidates_shown = 3

[covered_call]
enabled = true
dte = [40, 75]
abs_delta = [0.15, 0.35]
min_credit = 0.50
strike_floor = "cost_basis"
earnings = "exclude"
ex_dividend = "exclude"
objective = "extrinsic_per_day"

[roll]
enabled = true
dte = [40, 75]
abs_delta = [0.15, 0.35]
min_credit = 0.50
credit_only = true
strike_floor = "cost_basis"
earnings = "exclude"
ex_dividend = "exclude"
objective = "extrinsic_per_day"

[protective_put]
enabled = true
dte = [120, 210]
floor_pct = [3, 10]
stress_drop_pct = 20
objective = "stress_cost_per_day"

[collar]
enabled = true
dte = [120, 210]
floor_pct = [3, 10]
cap_pct = [2, 10]
max_net_debit = 0.00
strike_floor = "cost_basis"
earnings = "exclude"
ex_dividend = "exclude"
objective = "cap_within_budget"
```

### Acknowledgement, and failing closed

`structuring.toml` joins `desk.toml`, `positions.csv` and the adopted rulebook in the set that `ack.py config
--owner-tapped` acknowledges and that every book check compares. A change you have not acknowledged posts CONFIG
CHANGED at the next book check, so an edit that would steer the best candidate is visible.

- No `structuring.toml`: every structuring tool answers UNAVAILABLE and exits 20. Synthetic, an acknowledged fixture
  copy without the file:

  ```text
  UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your criteria (SETUP step 9)
  computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:50 UTC
  ```

- An unacknowledged `desk.toml`, `positions.csv` or `structuring.toml`: the tool prints nothing else and exits 20.
  Your approval level and your share coverage come from those files, so an unacknowledged edit, such as shares added
  by mistake, never lets the desk call an uncovered call covered. Synthetic, the same copy after `structuring.toml`
  was written and before it was acknowledged:

  ```text
  UNAVAILABLE | CONFIG CHANGED: structuring.toml: not acknowledged -> sha 5155427c | the desk names no candidate and prints no ticket while desk.toml, positions.csv or structuring.toml differ from what you acknowledged | acknowledge with ack.py config --owner-tapped
  computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:52 UTC
  ```

- A quarantined group anywhere in `positions.csv` (a row `positions_check.py` refuses): the desk cannot count which
  shares are free, so every structuring answer is UNAVAILABLE (exit 20) until the file is fixed. Synthetic, a copy
  whose `positions.csv` gained a WMT call row of another trade id, its cost and its premium in disagreement:

  ```text
  UNAVAILABLE | AG-20261020-01 roll analysis | positions.csv has a quarantined group (positions_check.py shows why): the desk cannot count the account's coverage
  computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:50 UTC
  ```

- An unacknowledged rulebook alone does not block: its CONFIG CHANGED line prints first, and the answer follows.
- A `structuring.toml` the reader refuses is exit 1 (`structuring_invalid`) with the reader's reason.

## How a tool picks one best candidate

### The candidates

- **Expirations.** Standard monthly expirations whose calendar days to expiry lie in your `dte` window and, for a
  replacement, that expire after the held leg. The window is at most 90 days wide, so a run needs at most four chains.
  A window that holds no listed standard monthly is NOT_APPLICABLE and writes no report. Synthetic, the fixture with
  `[roll] dte = [80, 100]`: `no listed standard monthly expiration lies inside your roll window of 80 to 100 calendar
  days`.
- **Prices.** A new leg is priced only when both sides of its quote are fresh, with a mid, at quote grade A to D, and it
  is a standard contract of the underlying; a spread above `max_spread_pct` is still priced and falls OUTSIDE_BANDS
  `max_spread_pct`. A contract the chain lists more than once with different quotes is not priced (`the chain lists the
  contract more than once with different quotes`); identical copies count once. Its implied volatility is solved from
  the mid with the European Black-Scholes-Merton model and the dividend yield of `research/calendar.toml` (0 with a
  warning), unless that expiration's own quotes rule it out (the next item). A held leg you would close is priced at its conservative fresh side, which is the ask for a short and the
  bid for a long. At grade E that side still stands, with the note `quote grade E: the conservative side stands`.
- **The carry of each expiration.** Before the candidates are priced, each expiration's near-the-money calls and puts
  give the carry their put-call parity implies, with two bounds: one for the quotes' own width, and one, from a
  binomial tree, for American early exercise, which can only bias the estimate upward. When the calendar's dividend
  rate lies outside that interval, the quotes rule it out and the expiration is priced at the parity carry; the text
  then says so after the model line, for example `carry: the 2027-03-19 options are priced at the dividend carry their
  put-call parity implies, 0.01 % a year, because those quotes exclude the calendar's 1.15 %`. Otherwise the calendar
  stands: when its rate lies inside the interval (the quotes cannot resolve the carry), when the parity estimate lies
  more than 5 points from it (more likely a data error), or when fewer than two strikes quote both a call and a put.
  The JSON's `carry` lists each expiration's source, the carry used and the estimate. Only the structuring tools read
  it; the book, the levels and the exit rules keep the calendar's rate. On the fixture the SPY chains switch (they were
  generated with no dividend) and the WMT and NVDA chains keep the calendar.
- **Statuses.** Each candidate gets every reason that applies and one status, in this precedence:

  | Status | Means |
  | --- | --- |
  | `UNPRICED` | no usable quote, conflicting copies in the chain, no implied volatility, or less than an hour to the exercise cutoff |
  | `OUTSIDE_BANDS` | outside a band or window: `dte`, `abs_delta`, `min_credit`, `max_spread_pct`, `floor_pct`, `cap_pct`, `stress_drop_pct` (no cover at the stress), `max_net_debit` |
  | `FAILS_CRITERION` | fails `strike_floor`, `credit_only`, `earnings` or `ex_dividend` |
  | `ELIGIBLE` | passes every criterion |

  In the text, both failing tiers print alike: a red status mark with the keys a candidate fails and, under the
  candidates list of an answer with no best candidate, `<ranks> fail <key>: <words>` with the value and your band. A
  ticket's checks shows a failing criterion as a red item. The JSON carries each reason as `{key, code, value,
  threshold}`.
- **Not priced.** An UNPRICED candidate's item ends `not priced`, with `<rank> not priced: <words>` under the
  list, and it is counted apart, as `<n> not priced`: it was never judged against your criteria, so it never counts
  as failing them. When not one candidate of the window could
  be priced, the answer is UNAVAILABLE (exit 20, no report): `no candidate in your window could be priced`, followed by
  the reason they share or by `(the reasons differ; the JSON lists each)`.
- **The stock price each option is priced against.** An option's implied volatility is solved against a stock price
  from the same moment: the stock quote when it is stamped within 60 seconds of the option's, otherwise the stock's
  one-minute IEX bar of the minute the option was quoted in (one extra request; the free indicative feed updates a
  thinly traded option less often than the stock). The Greeks are then read at the current spot. When a bar was used,
  a note follows the model line: `spot: <n> option legs quoted more than 60 seconds from the stock quote were priced
  against the stock's one-minute IEX bar of the minute each was quoted in`, and each such leg's JSON carries
  `spot_bar_utc`. An option with no bar for its minute stays `not priced` (`the underlying's quote is stamped too far
  from the option's`). A stock quote reused from an earlier run's cache but older than the chains is refetched first.

### The objectives

Each section declares one of the objectives it accepts, so the file's sha names how the candidates were ranked:

| Objective | Ranks | Words the text prints | Formula |
| --- | --- | --- | --- |
| `extrinsic_per_day` | higher first | the bid's time value per calendar day, in percent of the share price | E = bid - max(0, S - K); score = E / S / T_c x 100, quantized 0.0001 half-even |
| `stress_cost_per_day` | lower first | the put's ask per calendar day per 100 of loss it offsets at your stress drop | S_x = S x (1 - x / 100) with x = stress_drop_pct; score = ask / T_c / (K - S_x) x 100, quantized 0.0001 half-even (no cover at the stress when K <= S_x) |
| `cap_within_budget` | higher first | the cap's distance above the share price, in percent, among pairs within your net-debit budget | U = (Kc - S) / S x 100, quantized 0.01 half-even, among the put and call pairs of one expiration whose net debit (put ask - call bid) is at most max_net_debit |
| `net_extrinsic_per_added_day` | higher first | the new call's time value at its bid less the held call's time value at its ask, in percent of the share price per calendar day added when the roll adds time value, and the whole amount, not divided by days, when it gives some up | E_new = bid - max(0, S - K_new); E_held = ask_held - max(0, S - K_held); N = E_new - E_held; score = N / S / (T_c,new - T_c,held) x 100 when N >= 0, else N / S x 100, quantized 0.000001 half-even |
| `band_centre` | lower first | the distance from the centre of your bands, each band counted in its own half-width, plus each leg's quote spread counted in your max_spread_pct, all summed with equal weight (a desk convention: one half-width of days counts as much as one half-width of delta, floor or cap, and as one leg quoted at your spread limit) | c = (lo + hi) / 2 and w = (hi - lo) / 2 for each band of the section (dte with abs_delta, dte with floor_pct, or dte with floor_pct and cap_pct); score = sum of abs(x - c) / w + sum over the legs of spread_pct / max_spread_pct, quantized 0.01 half-even |

S is the underlying's mid, K the strike, Kc the call strike, and T_c the whole calendar days to the last trading day
(the pricing model's time to expiry counts seconds to the exercise cutoff instead; the two differ by less than a day,
and each objective's JSON formula says which it reads). The objectives each
section accepts, its default first: `covered_call` takes `extrinsic_per_day` or `band_centre`; `roll` takes
`extrinsic_per_day` or `net_extrinsic_per_added_day`; `protective_put` takes `stress_cost_per_day` or `band_centre`;
`collar` takes `cap_within_budget` or `band_centre`. A file that names the default ranks exactly as before. `roll` does
not take `band_centre`: a roll trades a call you already hold, and a score that ignores the time value the roll buys
back can name a roll that gives up more than it sells. The quote part of `band_centre` makes a wide quote cost score in
the same units as a band: a leg quoted at your `max_spread_pct` adds 1, as much as a band edge. Under `band_centre` the
first tie-break is the section's money measure: `extrinsic_per_day` (higher first) for a covered call,
`stress_cost_per_day` (lower first) for a put, and the net debit (lower first) for a collar. Nothing is sorted ahead of
the `band_centre` score. A collar pairs a put and a call of the same expiration; a leg outside its band is counted, not
paired.

### The ranking and the best candidate

- The order is ELIGIBLE, then FAILS_CRITERION, then OUTSIDE_BANDS, each by score and the tie-breaks, then
  UNPRICED by contract. A candidate's rank is its place in that order.
- Ties on the score are broken in a fixed order: a covered call by the absolute delta (lower first), the expiration
  (earlier first), the strike (higher first); a replacement call by the net credit (higher first) and then the same
  keys; a protective put by the strike (higher first), the expiration (later first); a collar by the put strike
  (higher first), the net debit (lower first), the expiration (later first). The contract decides last. The text says
  so when a tie was broken, for example `tie on cap_within_budget broken by the put strike (higher first)`.
- Quote precision, information only: when the best's score at its mid differs from its score at the conservative side
  (bid for a call sold, ask for a put bought) by h, every other ELIGIBLE candidate whose score lies within h of the
  best's is noted, for example `rank 2 lies within the best's own quote spread on stress_cost_per_day: these quotes do
  not tell it apart from the best`. Only the best's own spread sets h and only a candidate's conservative score admits
  it, so a wider quote never earns a note. The order and the best are unchanged.
- Share-price sensitivity under `band_centre`, information only: the delta, floor and cap readings move with the
  share price, so the text names the nearest share price above and below the spot (within 5 %) at which another
  candidate would be best, holding every quote, volatility and day count fixed (for a collar, pairs with a leg that
  enters its floor or cap band within that reach count too), and compares the nearer one with one
  feed delay's move at the best's volatility, sigma x sqrt(15 / (252 x 390)). For example `band_centre at these quotes
  and volatilities: another candidate is best if the stock rises 0.31 % (to 104.55) or falls 1.77 % (to 102.39); one
  15-min delay is a move of about 0.48 % at the best's volatility, so these quotes do not settle the best`.
- The best candidate is rank 1 when it is ELIGIBLE. It means only this: the highest score (or, for
  `stress_cost_per_day`, the lowest) on the objective you chose, among the candidates that passed every criterion.
  The text says so in its fixed caveat: `the score ranks the candidates only on the objective you chose; it is not a
  forecast`.
- When no candidate passes every criterion, the answer is NONE_QUALIFIES, and the text says `no candidate passed every
  criterion: the desk names no best candidate`. That is a complete answer: closing, letting a short call be assigned
  and leaving the position as it is remain your choices.

### Earnings and ex-dividend dates

A short call (a covered call, a replacement call, a collar's call) is checked against the dates in
`research/calendar.toml`; a long put never is.

- An earnings date inside the call's life fails `earnings`. A release before the open, or at an unknown time, counts
  at 09:30 ET; a release after the close counts at 16:00 ET, so one on a call's last trading day falls inside that
  call's life (holders decide whether to exercise after the close). For a replacement, a release after the close on
  the held call's last trading day belongs to the held call. `#earnings-ok` on any open row of the group, or
  `earnings = "allow"`, accepts the label.
- An ex-dividend date whose last cum-dividend day falls inside the call's life fails `ex_dividend` (for a replacement,
  only when the held call's life did not already include it). `#assign-ok` on any open row of the group, or
  `ex_dividend = "allow"`, accepts it.
- A listed earnings or ex-dividend date that has already passed counts as unknown and inside the call's life,
  because the next one is not known. Synthetic, the expiry-day roll analysis of AG-20261020-01 (`--fixture-set
  expiry_day` at 2026-11-20 19:50 UTC), which names no best candidate, says under its candidates list:

  ```text
  - 1, 2, 3 fail earnings: the listed earnings date 2026-11-17 has passed, so the next one is unknown and counts as inside the new call's life
  ```

  The same day's ROLL ticket for the 2027-01-15 115 call shows it as a red item of its checks: `earnings`,
  `2026-11-17 has passed, the next is unknown: counted inside the new call's life`, `excluded by your structuring.toml`.

- The calendar holds one next ex-date per symbol. When a call's last trading day is at least 80 calendar days after
  that date, a second ex-date may fall inside its life, so the call also gets `a second ex-date may fall before the
  call's last trading day; the calendar holds one`.
- An accepted label stays visible, as a yellow item whose criteria part reads `accepted by #assign-ok`, `accepted by
  #earnings-ok` or `accepted by your structuring.toml`.
- A short call in the money whose bid's time value is below a dividend before its last trading day, or a long put
  with an absolute delta of 0.80 or more, or a long put in the money (K > S) whose time value at the mid is below the
  interest on its strike to expiry, K x (1 - e^(-r x tau)), gets an `american-style note:` in the notes: early
  exercise can matter there, and the European model does not price it. A put at or out of the money never gets the
  note from the interest test: exercising it now pays nothing.

## The approval-level check

The structuring tools are the only reader of `approval_level` in `desk.toml`. The level is self-attested: you state it
in the desk's convention, and the desk records it.

Approval levels by the desk's convention (brokers number and define their levels differently; state yours in this convention, and give the lowest level across your accounts):

- `0` no options approval: the desk shows closing tickets only;
- `1` covered calls on shares held in the same account, and cash-secured puts;
- `2` level 1 plus long calls and long puts (protective puts, collars);
- `3` level 2 plus defined-risk spreads;
- `4` level 3 plus uncovered short options;
- `5` level 4 plus uncovered index options and other advanced strategies.

By the desk's convention, a closing transaction needs no approval (level 0); the broker's own rules apply.

- A ticket or a proposal is checked step by step, against the holdings before each leg, in the same account and
  underlying. A short call is covered only by 100 shares per contract in the same account; shares in another account
  never cover it. The holdings are every open row of that account and underlying, whatever its group; while any group
  of `positions.csv` is quarantined, the answer is UNAVAILABLE `positions_quarantined` (above). Coverage counts whole
  hundreds of shares, and an UNWIND_STOCK ticket closes the exact open quantity, fractional shares included.

  | Step | Level |
  | --- | --- |
  | a step that closes a short or long option you hold | 0 |
  | a step that closes shares no remaining short call needs | 0 |
  | a step that closes shares or a long option and leaves a remaining short option uncovered | 4 |
  | a step that opens a short call covered by free shares in the same account (the shares minus 100 for each short call already open, at least 100 per new contract) | 1 |
  | a step that opens a short call without enough free shares | 4 |
  | a step that opens a short put (cash-secured; the desk does not see your cash) | 1 |
  | a step that opens a long call or a long put | 2 |

- The leg order is fixed so that no step opens or uncovers a short option. A CLOSE ticket closes the shorts, then the
  longs. UNWIND_STOCK closes the shorts, then the shares, then the longs, so the protection goes last. A replacement
  short call closes the old call first; a replacement long put opens the new put first, so the protection never lapses.
  A collar opens its put, then its call. MONETIZE_PUT closes the put.
- When the reverse order would leave an uncovered call, the text says so, for example `in the other order, after leg
  2 you would hold 2 uncovered calls (level 4)`. A ticket or a proposal with more than one leg adds the multi-leg
  note, worded by what the order is. Its four forms, the tools' fixed words:
  - a roll: `entered as one multi-leg order, some brokers class a roll as a spread, which can need a higher level`;
  - a collar proposal: `entered as one multi-leg order, some brokers class a collar as a spread, which can need a
    higher level`;
  - a CLOSE of two option legs or more: `entered as one multi-leg order, some brokers class closing option legs as a
    spread, which can need a higher level`;
  - an UNWIND_STOCK of shares and options: `entered as one multi-leg order, some brokers class shares and options
    together as a combination, which can need a higher level`.
- A step above your level refuses the answer (exit 20): the desk shows nothing above your level. Synthetic, the
  fixture with `approval_level = 0`:

  ```text
  REFUSED AG-20261020-01 | WMT covered call | opening 2 new short calls, covered by 200 free shares needs level 1 by the desk's convention; desk.toml records your self-attested level 0 | the desk shows nothing above your level; your broker's own approval decides what you can enter
  computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:50 UTC
  ```

- Give the lowest level across your accounts: the desk uses one level for every account. Your broker's own approval
  decides what you can enter. The desk calls this the approval-level check and makes no suitability determination.

## The three answers (synthetic)

Every example below ran on a copy of `tests/fixtures/desk` with `structuring.toml` rendered from the fixture answers
and acknowledged, at 2026-11-03 19:50 UTC (`--offline --now 2026-11-03T19:50:00Z`), on the recorded quotes of 19:31
UTC. Every value is made up; it is not market data.

Each answer is a desk view in markdown, built to read in a chat on a phone: a title, the context in one italic line
under it, the legs as list items, captioned items for the checks, the group's numbers and the ranked candidates,
and the notes as a quoted list. It holds no table (a chat client that draws a table as a card of its own splits the
answer into several bubbles), so it reads as one message. The Aegis Structuring Desk posts it as it is, never inside a
code block. A status mark opens each check item: a green circle for a criterion that passes, a yellow circle for an earnings or ex-dividend label you accepted
(`#earnings-ok`, `#assign-ok`, or `allow` in the section), and a red circle for a criterion that fails, with the keys
it fails. The quote source is named once, in the notes.

### A roll analysis

`roll_analysis.py --trade AG-20261020-01`: the WMT covered call, 200 shares at 98.50 and 2 short 2026-11-20 105
calls whose row carries `#assign-ok`. One replacement passes every criterion.

```text
### STRUCTURING AG-20261020-01 | WMT covered call: the roll analysis of the short 2026-11-20 105 call
*account 1 of desk.toml (margin) | WMT mid 104.23 | your cost 98.50 | quotes 19:31 UTC, 15-min delayed*

**BEST CANDIDATE 1 of 20** by your objective extrinsic_per_day: **0.0467**, the highest among the 1 candidate that passed every criterion
- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | ask 3.50 | quote grade B, 18 min old
- leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55 | quote grade B, 18 min old

> the score ranks the candidates only on the objective you chose; it is not a forecast

**Checks for this candidate**
- 🟢 **net per share** credit 0.05 (10.00 in all) | your criteria: credit only
- 🟢 **strike** 115 | your criteria: at or above your cost 98.50 or the held strike 105
- 🟢 **absolute delta** 0.33 | your criteria: 0.15 to 0.35
- 🟢 **bid** 3.55 | your criteria: at least 0.50
- 🟢 **calendar days** 73 | your criteria: 40 to 75
- 🟢 **spread** 2.78 % | your criteria: at most 25 %
- 🟡 **ex-dividend** 2026-11-24 (cum 2026-11-23), inside the new call's life | accepted by #assign-ok

**The group, now and after both legs**
- **max loss** 19,016.00 now, 19,006.00 after
- **max profit** 1,984.00 now, 3,994.00 after
- **breakeven** 95.08 now, 95.03 after
- **group delta** 101.71 now, 134.07 after

**Replacement candidates by rank** 1 of 20 passed your criteria; 19 failed (abs_delta 15, credit_only 4, strike_floor 4)
- 🟢 **1** 2027-01-15 | new call 115 | credit 0.05 | extrinsic_per_day 0.0467
- 🔴 **2** 2026-12-18 | new call 112.5 | debit 0.70 | extrinsic_per_day 0.0597 | fails credit_only
- 🔴 **3** 2026-12-18 | new call 115 | debit 1.40 | extrinsic_per_day 0.0448 | fails credit_only

a ticket for one of these: ask the Aegis Desk Lead for a ROLL ticket with its expiration and strike

> - extrinsic_per_day: the bid's time value per calendar day, in percent of the share price
> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC
> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
> - criteria: your structuring.toml (sha 5155427c)

computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:31 UTC
```

- `leg 1 of 2: closes your short ...` and `leg 2 of 2: opens a new short ...` are the fixed leg grammar. They name the
  effect, the count, the root, the expiration, the strike and the right, and no trade-side word or order field.
- The best candidate's checks shows each criterion with its value and your band; its group block shows the
  group now and after both legs. The candidates list ranks the candidates, the best first, each with its net per
  share, its score and its status. The JSON lists every candidate with its after-state.
- The caption's counts list each key the failing candidates fail, so one candidate can count twice.

The same request for the NVDA covered call, whose row carries `#earnings-ok` but not `#assign-ok`, names no best
candidate; the items under the list say why each shown candidate fails:

```text
### STRUCTURING AG-20261022-02 | NVDA covered call: the roll analysis of the short 2026-11-20 150 call
*account 1 of desk.toml (margin) | NVDA mid 148.925 | your cost 141.20 | quotes 19:31 UTC, 15-min delayed*

**no candidate passed every criterion: the desk names no best candidate**
- the net credits close the held 2026-11-20 150 call | ask 3.30 | quote grade C, 18 min old

**Replacement candidates by rank** 0 of 20 passed your criteria; 18 failed (abs_delta 14, min_credit 1, credit_only 6, strike_floor 6, ex_dividend 18); 2 not priced
- 🔴 **1** 2027-01-15 | new call 160 | debit 0.05 | extrinsic_per_day 0.0299 | fails credit_only, ex_dividend
- 🔴 **2** 2026-12-18 | new call 160 | debit 1.45 | extrinsic_per_day 0.0276 | fails credit_only, ex_dividend
- 🔴 **3** 2027-01-15 | new call 165 | debit 1.35 | extrinsic_per_day 0.0179 | fails credit_only, ex_dividend

**Why each one fails**
- 1 fails credit_only: a net debit of 0.05
- 1, 2, 3 fail ex_dividend: the ex-dividend date 2026-12-03 (cum 2026-12-02) falls inside the new call's life
- 2 fails credit_only: a net debit of 1.45
- 3 fails credit_only: a net debit of 1.35

a ticket for one of these: ask the Aegis Desk Lead for a ROLL ticket with its expiration and strike

> - extrinsic_per_day: the bid's time value per calendar day, in percent of the share price
> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC
> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
> - criteria: your structuring.toml (sha 5155427c)

computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:31 UTC
```

The `2 not priced` are the two 180 calls, whose quotes the desk cannot price (a zero bid, a spread too wide); they
are never counted as failing your criteria.

### A ticket

A ticket of two legs or more ends its cash block with `spread cost against the mids`, the mids' net cash less the
conservative one (half of each leg's spread), and a collar proposal's checks with `spread cost`, half of both
legs' spreads per share: two narrow spreads can still be large against a collar's small net, which a per-leg
`max_spread_pct` does not bound.

A ROLL of a protective put's long put adds one item to its checks, `net cost per day added`: the new put's ask
less the held put's bid, per share, over the calendar days the replacement adds, in percent of the share price
((ask_new - bid_held) / S / (T_new - T_held) x 100, quantized 0.0001). On the fixture, the March 610 put in place of
the December 620 put reads `0.0226 % of the share price (ask 23.10 less the held put's bid 9.80, over 91 days added)`.

`exit_ticket.py --trade AG-20261020-01 --action ROLL --new-expiration 2027-01-15 --new-strike 115`, the replacement
named by the roll analysis above:

```text
### TICKET AG-20261020-01 | WMT covered call: a roll of the short call (action ROLL)
*account 1 of desk.toml (margin) | computed 19:50 UTC | valid until 20:20 UTC (30 min) | quotes 19:31 UTC, 15-min delayed*

**The legs**
- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 3.35, ask 3.50, mid 3.425 | quote grade B
- leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55, ask 3.65, mid 3.60 | quote grade B

**The cash**
- **net cash** +10.00 at the conservative prices, +35.00 at the mids
- **realized on the closing leg** -16.00 at the conservative prices, -1.00 at the mids
- **the most the steps need at once** 700.00
- **spread cost against the mids** 25.00

**Checks for the new call**
- 🟢 **net per share** credit 0.05 (10.00 in all) | your criteria: credit only
- 🟢 **strike** 115 | your criteria: at or above your cost 98.50 or the held strike 105
- 🟢 **absolute delta** 0.33 | your criteria: 0.15 to 0.35
- 🟢 **bid** 3.55 | your criteria: at least 0.50
- 🟢 **calendar days** 73 | your criteria: 40 to 75
- 🟢 **spread** 2.78 % | your criteria: at most 25 %
- 🟡 **ex-dividend** 2026-11-24 (cum 2026-11-23), inside the new call's life | accepted by #assign-ok

**The group, now and after both legs**
- **max loss** 19,016.00 now, 19,006.00 after
- **max profit** 1,984.00 now, 3,994.00 after
- **breakeven** 95.08 now, 95.03 after
- **group delta** 101.71 now, 134.07 after

**Approval-level check (leg 1 first):** the highest step needs level 1 by the desk's convention, within your self-attested level 2 (desk.toml); your broker's own approval for this account decides
- after leg 1: you hold 200 WMT shares, no option | level 0
- after leg 2: you hold 200 WMT shares, 2 short calls | level 1
- in the other order, after leg 2 you would hold 2 uncovered calls (level 4)
- entered as one multi-leg order, some brokers class a roll as a spread, which can need a higher level

> - after 20:20 UTC its quotes are too old, and a new ticket needs a new request
> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC
> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
> - the desk does not see your cash balance
> - after a fill, tell the Aegis Desk Lead the fill; it proposes the positions.csv lines
> - you supply the limit; the desk prints none
> - if you decide to act, you type it into your brokerage account yourself

computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:31 UTC
```

- Each leg shows its bid, ask and mid and its quote grade; the context strip gives the quotes' time. You supply the
  limit; the desk prints none.
- The cash block is at the conservative side of each leg (the bid for what you would receive, the ask for what you
  would pay), then at the mid. "The most the steps need at once" is the largest amount paid out before a later leg
  brings cash in. The desk does not see your cash balance.
- `realized on the closing leg` estimates the realized P/L of the closed leg at the conservative side and at the mid.
- A ticket is computed only while the session is open. It stays valid for 30 minutes, or until the close if that
  comes first; after that its quotes are too old, and a new ticket needs a new request.
- A replacement that fails a criterion is still a ticket (exit 0), its failing criteria in red: you named it.
- A replacement the desk cannot price is UNAVAILABLE (exit 20, `replacement_unpriced`), with the reason. Synthetic,
  two ROLL tickets of the NVDA group:

  ```text
  UNAVAILABLE | AG-20261022-02 ROLL ticket | the 2026-12-18 180 call cannot be priced: no bid
  UNAVAILABLE | AG-20261022-02 ROLL ticket | the 2027-01-15 180 call cannot be priced: the spread is wider than the quote grades allow
  ```

- Only a ROLL ticket prints the model line, because only its replacement carries model numbers (implied volatility,
  delta and the after-state). The other tickets print quotes and cash only.

A LET_ASSIGN ticket has no leg to enter:

```text
### TICKET AG-20261020-01 | WMT covered call: the short option left to expiry (action LET_ASSIGN)
*account 1 of desk.toml (margin) | computed 19:50 UTC | valid until 20:20 UTC (30 min) | quotes 19:31 UTC, 15-min delayed*

no leg to enter: if the 105 call is at least 0.01 in the money at expiry, OCC exercises it by exception; your broker's cutoff is 16:30 ET (desk.toml)

**The 2026-11-20 105 call**
- **now** out of the money: the spot mid 104.23 is below its strike
- **if you decide otherwise** you act by Fri 2026-11-20 16:00 ET (21:00 UTC): a short option left to expiry can be closed only while the session trades
- **if assigned** 200 WMT shares are called away at the strike, cash +21,000.00; realized on the call and the shares +1,984.00
- **ex-dividend** the ex-dividend date 2026-11-24 falls after the call's last trading day 2026-11-20

> - after 20:20 UTC its quotes are too old, and a new ticket needs a new request
> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC
> - after expiry, the post-expiry check of Aegis Position Control proposes the positions.csv lines

computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:31 UTC
```

- A short option left to expiry can be closed only while the session trades, so its act-by is the last trading day's
  session close (16:00 ET, 13:00 ET on an early close), or your broker's cutoff when that is earlier. On the last
  trading day `[today]` follows the time.
- The broker's cutoff in the OCC line is for an exercise instruction on a long option; OCC exercises an option at least
  0.01 in the money at expiry by exception.

### A proposal

`propose.py --trade AG-20261028-03 --structure protective_put` on a copy of the fixture whose `positions.csv` drops the
long put row, so the SPY group holds shares only, and whose share row carries `#assign-ok`:

```text
### PROPOSAL AG-20261028-03 | a protective put on the 100 SPY shares you hold
*account 2 of desk.toml (ira) | SPY mid 648.12 | your cost 632.40 | quotes 19:31 UTC, 15-min delayed*

**BEST CANDIDATE 1 of 12** by your objective stress_cost_per_day: **0.1856**, the lowest among the 5 candidates that passed every criterion
- leg 1 of 1: opens a new long 1 x SPY 2027-03-19 610 put | bid 22.90, ask 23.10, mid 23.00 | quote grade A

> the score ranks the candidates only on the objective you chose; it is not a forecast

**Checks for this candidate**
- 🟢 **floor** 5.88 % below | your criteria: 3 to 10 %
- **cost** ask 23.10 (2,310.00 in all)
- 🟢 **covered at your stress drop** 91.504 per share (9,150.40 in all) | your criteria: drop 20 % (stressed price 518.496)
- 🟢 **calendar days** 136 | your criteria: 120 to 210
- 🟢 **spread** 0.87 % | your criteria: at most 25 %

**The group, now and after the leg**
- **max loss** 63,240.00 now, 4,550.00 after
- **max profit** unbounded now, unbounded after
- **breakeven** 632.40 now, 655.50 after
- **group delta** 100.00 now, 70.17 after

**Approval-level check:** the highest step needs level 2 by the desk's convention, within your self-attested level 2 (desk.toml); your broker's own approval for this account decides
- after leg 1: you hold 100 SPY shares, 1 long put | level 2

**Candidates by rank** 5 of 12 passed your criteria; 7 failed (floor_pct 7)
- 🟢 **1** 2027-03-19 | strike 610 | floor 5.88 % | ask 23.10 | stress_cost_per_day 0.1856
- 🟢 **2** 2027-03-19 | strike 620 | floor 4.34 % | ask 25.65 | stress_cost_per_day 0.1858
- 🟢 **3** 2027-03-19 | strike 625 | floor 3.57 % | ask 27.05 | stress_cost_per_day 0.1868

> - stress_cost_per_day: the put's ask per calendar day per 100 of loss it offsets at your stress drop
> - rank 2 lies within the best's own quote spread on stress_cost_per_day: these quotes do not tell it apart from the best
> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC
> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
> - carry: the 2027-03-19 options are priced at the dividend carry their put-call parity implies, 0.01 % a year, because those quotes exclude the calendar's 1.15 %
> - criteria: your structuring.toml (sha 5155427c)
> - you supply the limit; the desk prints none
> - if you decide to act, you type it into your brokerage account yourself

computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:31 UTC
```

On the same copy, `--structure collar`:

```text
### PROPOSAL AG-20261028-03 | a collar on the 100 SPY shares you hold
*account 2 of desk.toml (ira) | SPY mid 648.12 | your cost 632.40 | quotes 19:31 UTC, 15-min delayed*

**BEST CANDIDATE 1 of 10** by your objective cap_within_budget: **4.92**, the highest among the 4 candidates that passed every criterion
- leg 1 of 2: opens a new long 1 x SPY 2027-03-19 620 put | bid 25.45, ask 25.65, mid 25.55 | quote grade A
- leg 2 of 2: opens a new short 1 x SPY 2027-03-19 680 call | bid 26.40, ask 26.60, mid 26.50 | quote grade A

> the score ranks the candidates only on the objective you chose; it is not a forecast

**Checks for this candidate**
- 🟢 **floor** 4.34 % below | your criteria: 3 to 10 %
- 🟢 **cap** 4.92 % above | your criteria: 2 to 10 %
- 🟢 **net per share** credit 0.75 (75.00 in all) | your criteria: net debit at most 0.00
- 🟢 **call strike** 680 | your criteria: at or above your cost 632.40
- 🟢 **calendar days** 136 | your criteria: 120 to 210
- 🟢 **spreads** 0.78 % and 0.75 % | your criteria: at most 25 %
- **spread cost** 0.20 per share (20.00 in all)
- 🟡 **ex-dividend** 2026-12-18 (estimated, cum 2026-12-17), inside the call's life | accepted by #assign-ok
- 🟡 **ex-dividend** a second ex-date may fall before the call's last trading day | accepted by #assign-ok

**The group, now and after both legs**
- **max loss** 63,240.00 now, 1,165.00 after
- **max profit** unbounded now, 4,835.00 after
- **breakeven** 632.40 now, 631.65 after
- **group delta** 100.00 now, 23.75 after

**Approval-level check (leg 1 first):** the highest step needs level 2 by the desk's convention, within your self-attested level 2 (desk.toml); your broker's own approval for this account decides
- after leg 1: you hold 100 SPY shares, 1 long put | level 2
- after leg 2: you hold 100 SPY shares, 1 short call, 1 long put | level 1
- entered as one multi-leg order, some brokers class a collar as a spread, which can need a higher level

**Candidates by rank** 4 of 10 passed your criteria; 6 failed (max_net_debit 6)
- 🟢 **1** 2027-03-19 | put 620 / call 680 | floor 4.34 % | cap 4.92 % | credit 0.75
- 🟢 **2** 2027-03-19 | put 610 / call 680 | floor 5.88 % | cap 4.92 % | credit 3.30
- 🟢 **3** 2027-03-19 | put 600 / call 680 | floor 7.42 % | cap 4.92 % | credit 5.60

not paired, outside their bands: 7 puts outside your floor band and 10 calls outside your cap band

> - cap_within_budget: the cap's distance above the share price, in percent, among pairs within your net-debit budget
> - tie on cap_within_budget broken by the put strike (higher first)
> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC
> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled
> - carry: the 2027-03-19 options are priced at the dividend carry their put-call parity implies, 0.01 % a year, because those quotes exclude the calendar's 1.15 %
> - criteria: your structuring.toml (sha 5155427c)
> - you supply the limit; the desk prints none
> - if you decide to act, you type it into your brokerage account yourself

computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:31 UTC
```

When the contracts do not cover every share (`floor(shares / 100)` contracts), an item under the group block says so,
for example `2 contracts cover 200 of your 250.5 shares; the other 50.5 are not part of the covered call`.

On the fixture itself, the SPY shares already carry a put, so a proposal has nothing to add:

```text
NOTHING_TO_PROPOSE | AG-20261028-03 protective put proposal | the 100 SPY shares already carry 1 long put

computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:50 UTC
```

### Answers without a candidate

- NOT_APPLICABLE names the reason, for example the roll analysis of the SPY protective put:

  ```text
  NOT_APPLICABLE | AG-20261028-03 roll analysis | AG-20261028-03 is a protective put: the roll analysis covers the short call of a covered call; a ROLL ticket prices the replacement of its long put that you name; the roll analysis of a long put, a short put or a collar is planned for Stage 2 (not in this release)

  computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:50 UTC
  ```

- A ticket outside an open session, here on the recorded after-close quotes at 22:00 UTC:

  ```text
  UNAVAILABLE | AG-20261020-01 CLOSE ticket | a ticket needs quotes live enough to act on: the session is not open

  computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 22:00 UTC
  ```

- On a lint failure the tool prints only `WITHHELD | the text of <tool>.py failed the alert lint (<codes>) and is
  withheld; the JSON of <tool>.py --json shows it`, a blank line and the footer, and exits 1.

## What each answer exits with, and what it writes

| Code | Answers | Files | Journal record |
| --- | --- | --- | --- |
| 0 | BEST, NONE_QUALIFIES, NOT_APPLICABLE and NOTHING_TO_PROPOSE (roll analyses and proposals); TICKET and NOT_APPLICABLE (tickets) | a roll analysis or a proposal writes its report only for BEST and NONE_QUALIFIES (a complete evaluation); NOT_APPLICABLE and NOTHING_TO_PROPOSE write no report, only their journal record; a ticket writes its file only on TICKET | yes |
| 20 | UNAVAILABLE (a quote, an expiration list or a chain the answer needs is missing or stale, `no_candidate_priced`, a quarantined group in `positions.csv` (`positions_quarantined`), a closed session for a ticket, `replacement_not_listed`, `replacement_unpriced`, `criteria_missing`, `config_unacknowledged`, and the market-data refusals `no_credential`, `live_host`, `credential_file_mode` and `holiday_table_expired`) and REFUSED (`approval_level`, or `intermediate_exposure`, which the fixed leg order rules out) | none | yes |
| 1 | `unknown_trade`, `desk_root`, `config_invalid`, `positions_invalid`, `calendar_invalid`, `structuring_invalid`, `io_error`, `ticket_exists`, `lint_failed` (the WITHHELD line) | none | none |
| 2 | usage: a missing or malformed flag | none | none |

These tools never exit 10: "fired" belongs to the alerts. An answer printed with exit 0 or 20 is posted verbatim.

| File | Written by |
| --- | --- |
| `reports/rolls/<trade_id>-<YYYYMMDDTHHMMSSZ>.json` and `.md` | `roll_analysis.py` |
| `reports/proposals/<trade_id>-<structure>-<YYYYMMDDTHHMMSSZ>.json` and `.md` | `propose.py` |
| `tickets/TK-<trade_id>-<ACTION>-<YYYYMMDDTHHMMSSZ>.json` and `.md` | `exit_ticket.py` |
| `journal/structuring.jsonl` | all three, one record per printed text |

Each tool creates its folder at its first write. A rerun at the same instant with the same bytes writes nothing new; a
different text under the same name is exit 1 (`ticket_exists`). The run instant is read to the whole second, so a
rerun within the same second is the same instant, and a second ROLL ticket of one group for another replacement in
that second is exit 1 `ticket_exists`: ask again a second later. The journal is the audit trail of what was suggested
and when: each record carries `utc`, `tool`, `action`, `structure`, `trade_id`, `verdict`, the sha256 of
`structuring.toml`, `desk.toml` and `positions.csv`, `best_candidate`, `candidates_passed`, `candidates_failed`,
`refused_code` and the sha256 of the printed text. No book check, brief or routine reads it.

## The footer and the disclosure

Every structuring text ends with the structuring footer, for example:

```text
computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of 19:31 UTC
```

The desk has three footers, and the alert lint keeps them apart: the rules footer closes every alert, sheet and brief;
the market-snapshot footer closes only the Opening Bell; the structuring footer closes only the Structuring Desk's
tickets, its roll analyses and its proposals. It refuses rule content under the structuring footer, and a leg
reference, a ranked candidate, an action tag or the first line of a structuring answer under the other two. In
structuring mode it also refuses advice, preference, urgency, suitability, probability, risk, return and valuation
language, any claim that a text is not advice, order-type and duration abbreviations, a price after "at", "for",
"limit", "pay" or "price", and any line with a leg reference or a leg's effect words outside the fixed grammar. Its
one exception to plain ASCII is the desk view's three status circles, and only right after the `- ` that opens a
list item (or at the start of a table cell) of a structuring text; the EOD brief and the book view use the same three
at a list item's start under the rules footer, for a gain, an unavailable figure and a loss.

The one-time disclosure, `structuring-suggestions-1`, which the interview shows last and `structuring.toml` records as
acknowledged:

```text
The Aegis Structuring Desk answers only when you ask. For a position you hold in positions.csv, its tools list candidates from delayed Alpaca paper quotes (indicative option quotes, which are approximate): closing or unwinding it, a replacement for a short option you are closing or that is expiring, or a covered call, protective put or collar on shares you hold. They score the candidates by the objective and criteria in this file and name the highest-scoring one with its reasons. That is a personalized suggestion computed by deterministic tools and posted by an AI assistant; it is not an order, not a forecast and not a judgment that a trade suits you. The desk knows only your positions, these criteria and the approval level you stated; it does not see your cash, your other holdings, your objectives or your taxes. It never places anything: you decide, you supply the limit and you act in your brokerage account. Options involve substantial risk; you confirm that you received the OCC booklet Characteristics and Risks of Standardized Options.
```

## After a fill

A ticket carries no `positions.csv` lines: the fill price is unknown until you have it, and the file has one owner.
After a fill, tell the Aegis Desk Lead the fill; it proposes the `positions.csv` lines with Position Control's close,
partial-close and roll recipes, and your tap on the save's card applies them. After an expiry, Position Control's
post-expiry check proposes them. Once `positions.csv`
changes, every structuring tool answers `config_unacknowledged` until you tap `ack.py config --owner-tapped`.

## Limits

- **Pricing.** European Black-Scholes-Merton with a continuous dividend yield, on the quotes' mids. US equity options
  are American-style, and early exercise is not modelled; the `american-style note:` line marks the cases where it can
  matter. American pricing is planned for Stage 2 (not in this release). Its engine, the lattice
  `scripts/aegis_desk/lattice.py`, is in the tree, and no tool reads it yet ([the roadmap](ROADMAP.md), step 2.1).
- **Data.** Alpaca's free feed: IEX stock quotes (real time, IEX only) and indicative option quotes, delayed 15
  minutes and approximate (not exchange quotes), so every option price a structuring text names is approximate; the
  desk treats every quote as 15 minutes delayed. A ticket is computed only while the session is open, and it
  is valid for 30 minutes at most.
- **What the desk knows.** Your positions, your criteria and the approval level you stated. It does not see your cash,
  your other holdings, your objectives or your taxes, and it makes no cash check.
- **Expirations.** Standard monthly expirations only; weekly expirations are planned for Stage 2 (not in this
  release).
- **Dates.** One next earnings date and one next ex-dividend date per symbol, from `research/calendar.toml`. A date
  that is unknown, not listed or already passed counts as inside a call's life, and a call whose last trading day is
  at least 80 calendar days after the known ex-date also gets the second-ex-date label.
- **Budget.** No new fetch starts once a run has made 40 requests, and at most 4 chains per run, inside the desk-wide
  200 requests per minute (a chain costs at least 2: the contract listing, then the snapshots, and starts only while
  those 2 still fit; a paginated answer can finish past 40). A chain the run
  cannot get makes the answer UNAVAILABLE: no best candidate is named on partial data. An empty expiration list from
  the vendor is never cached; it counts as missing, and the answer is UNAVAILABLE.

## For counsel

[RELEASE.md](RELEASE.md) step 4 lists the items to sign before the tag and before any public release: the structuring
footer and the disclosure, the ticket texts, the roll-analysis texts and the proposal texts, the approval convention and
the REFUSED line, the criteria defaults and how the interview presents them, the FAQ answer and the README description,
and two questions about publishing this software. This page and the tools' outputs above are the material. Whether your
own use of these answers is investment advice is a question for your counsel.

## Reproduce the examples

From a clone of this repository, with no key. The fixture copy goes outside the clone:

```text
cp -r tests/fixtures/desk ../aegis-structuring-desk
python3 scripts/desk_init.py --answers tests/fixtures/init/structuring-answers.json --offline --desk-root ../aegis-structuring-desk --now 2026-11-03T19:48:00Z
python3 scripts/ack.py config --owner-tapped --offline --desk-root ../aegis-structuring-desk --now 2026-11-03T19:49:00Z
python3 scripts/roll_analysis.py --trade AG-20261020-01 --offline --desk-root ../aegis-structuring-desk --now 2026-11-03T19:50:00Z
python3 scripts/exit_ticket.py --trade AG-20261020-01 --action ROLL --new-expiration 2027-01-15 --new-strike 115 --offline --desk-root ../aegis-structuring-desk --now 2026-11-03T19:50:00Z
```

The proposals need a copy whose SPY group holds shares only, as described above; the REFUSED line needs
`approval_level = 0` in the copy's `desk.toml`. Each copy is acknowledged again after the edit.
