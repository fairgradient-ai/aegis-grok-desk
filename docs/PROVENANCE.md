# Provenance

Where the desk's thresholds, formulas, calendars and fixtures come from. Each is stated as a reference class or a
fact of the code, never as a claim of edge.

## Thresholds: reference classes, your choices

Every example rule in [rules/examples/exit_rules-1.0.0.toml](../rules/examples/exit_rules-1.0.0.toml) names its
reference class in its own `source` field:

| Reference class | Example rules |
| --- | --- |
| tastylive manage-winners research | `TP50` (half the credit with more than 21 trading days left), `TP80` (most of the credit, whatever the time left) |
| tastylive 21-DTE management research | `DTE21` (the last 21 trading days carry the most gamma) |
| tastylive spread-management research | `VTP` (60 percent of a debit spread's max profit) |
| OIC/Cboe assignment mechanics | `DELTA_WATCH`, `DELTA_SIGNAL`, `CAP`, `COVER` |
| OIC/Cboe pin mechanics | `PIN3`, `VBETWEEN` |
| OIC/Cboe early-exercise mechanics | `EXDIV` (a short call whose extrinsic value (bid) is below the coming dividend may be assigned early; an unknown dividend is never zero) |
| OCC exercise by exception | `AUTOEX` (a long option at least 0.01 in the money on its last trading day is exercised automatically) |
| owner risk control | `LOSSX`, `LAPSE14`, `MONETIZE`, `FLOOR`, `LLOSS50`, `VLOSS`, `USTOP`, `MOVE5_DOWN`, `MOVE5_UP`, `EARN2`, `DAILY`, `CONC` and the hedge review rule `ROLL30` |
| owner threshold | `LTP100`, `LDTE30` |

Those references describe how such thresholds are commonly reasoned about. The numbers in the file are documented
defaults for you to accept or change, not a judgment that they suit any account. `LOSSX`'s own source says it
plainly: "a stop alert at loss 2.0 x credit = cost-to-close 3.0 x credit; stop alerts are risk control, not edge".
The rules are cited by name, as their sources are; this page adds no study or paper the code does not name.

## Pricing and Greeks

- `scripts/aegis_desk/bsm.py` is the generalised Black-Scholes-Merton closed form with a continuous dividend yield,
  ported from the Aegis quant engine (FairGradient), whose values were validated against the Thales oracle and
  cross-checked against QuantLib. The only substitution is the normal distribution function, built on the `math`
  module's complementary error function.
- The golden value of record is the call value `13.494231234329277` at S = 162.06, K = 170.163, tau = 1.553 years,
  r = 0.05, q = 0.052 and sigma = 0.226 (`tests/engine/test_golden_price.py`). The doctor's selftest checks it on the
  desk computer.
- The CI oracle (`tests/oracle/test_quantlib_oracle.py`, run where QuantLib is installed) compares price within 1e-8
  relative and Greeks within 1e-6 relative, against QuantLib's own formulas and solver.
- Implied volatility (`iv.py`) is a bracketed Brent solver with the arbitrage bounds of the price; a failure is a
  status, never a number.
- Greeks (`greeks.py`) are European, in desk units: theta per calendar day, vega per volatility point, rho per rate
  point. The dividend yield is the symbol's `dividend_yield` in `research/calendar.toml`, else 0 with a warning on the
  leg. This European approximation is the Stage-1 model; an American early-exercise lattice is planned for Stage 2 (not in this release).
- The risk-free rate is yours: `desk.toml [rates]`, with its source and date.

## Expected move: two definitions

The Opening Bell states the expected move to the nearest standard expiration two ways, each labelled:

- straddle-based: `(call mid + put mid) x 0.80`, from the at-the-money call and put;
- one-sigma: `S x IV x sqrt(tau)`, from the at-the-money implied volatility.

They are different quantities and never interchangeable. Under Black-Scholes an at-the-money straddle is worth about
0.80 x S x sigma x sqrt(tau) (the constant is the square root of 2/pi, about 0.798), so the straddle-based figure is
about 0.64 times the one-sigma figure. On the synthetic fixture the two print `36.76` and `56.98`.

## Market calendar

- `scripts/aegis_desk/market_clock.py` embeds the NYSE holiday and early-close table for 2026 and 2027, transcribed
  from https://www.nyse.com/markets/hours-calendars, and the America/New_York daylight-saving rule.
- The table expires on 2027-12-31. Classifying a later instant fails rather than guess, and the doctor's check 3 fails
  60 days before the expiry.
- You can add a closure the table does not know (`[[closures]]` in `research/calendar.toml`); every desk tool that reads
  a desk root then treats that day as closed.

## Ticks and rounding

The threshold-level sheet rounds each level on a tick grid (`scripts/aegis_desk/levels.py`):

| Class | Grid |
| --- | --- |
| standard (the default) | 0.05 below 3.00, 0.10 at or above |
| penny (the Penny Interval Program; `desk.toml [levels] penny_classes`) | 0.01 below 3.00, 0.05 at or above |
| penny everywhere (`desk.toml [levels] penny_all_classes`) | 0.01 at every price |
| vertical spreads (the complex-order increment) | 0.01 |
| stock levels | 0.01 (0.0001 below 1.00) |

The desk cannot learn an option's class offline, so it uses the standard grid unless you name the class: its prices are
valid on every grid. The direction of rounding keeps a resting take-profit inside its rule's firing region and a loss
alert no later than its rule; the skill [aegis-threshold-levels](../skills/aegis-threshold-levels/SKILL.md) explains it.

## Quote quality and freshness: desk policy

The quote grades and the freshness bound are the desk's own policy, set in the plan and the code, not an external
standard:

- grades on the printed two-decimal spread: A at most 2 %, B at most 5 %, C at most 10 %, D at most 25 % or exactly
  one side stale, E a zero bid or a wider spread (display only), F missing, undated, stale, crossed or a closed day;
  rules may use A to D;
- the freshness bound during the session: `feed_delay_min + quote_ttl_min + grace_min` from `desk.toml`, 15 + 5 + 5
  minutes as `desk_init.py` writes it;
- after the close, the session's closing quotes; before the open, the prior close;
- the last trade is never a mark.

## Structuring criteria and objectives: the desk's design choices

The Structuring Desk's criteria defaults and its three objectives are the desk's own design choices, set in the
design of this release and the code (`scripts/aegis_desk/structuring_config.py`, `scripts/aegis_desk/structuring.py`).
No study or paper is behind them, and none is claimed: they say what is measured, never that a candidate will turn
out well. [STRUCTURING.md](STRUCTURING.md) has every key and its bounds.

The defaults, which the interview shows and the synthetic examples assume, are starting points for you to keep or
change, not a judgment that they fit any account:

| Default | Why the desk starts there |
| --- | --- |
| `max_spread_pct = 25` | the top of quote grade D, the widest spread the desk's own grades treat as usable (above it a quote is grade E, display only) |
| `max_candidates_shown = 3` | a short text; the JSON lists every candidate |
| `dte` windows at most 90 days wide | four consecutive standard monthly expirations span at least 84 days and five at least 112, so a window never needs more than the per-run cap of 4 chains |
| `dte = [40, 75]` for a covered call and a roll, `[120, 210]` for a protective put and a collar | starting points: a call window of about one to two and a half months, and a longer window for protection, so that it is not renewed every month and starts well above the hedge rules' 30-calendar-day review and 14-calendar-day lapse (`ROLL30`, `LAPSE14`); on the synthetic fixture at 2026-11-03 they hold two monthlies each for WMT and NVDA and one for SPY |
| `abs_delta = [0.15, 0.35]` | a starting band for a short call, read at 2 decimals as the exit rules read the absolute delta |
| `min_credit = 0.50` | a starting minimum per share, at the bid |
| `strike_floor = "cost_basis"`, `credit_only = true` | by default a call strike below your stock cost fails, and so does a roll for a net debit; a zero net passes |
| `earnings = "exclude"`, `ex_dividend = "exclude"` | an event inside a short call's life is labelled, as the exit rules label it (`EARN2`; `EXDIV`, from the early-exercise mechanics above); `#earnings-ok`, `#assign-ok` or `allow` accept it |
| `floor_pct = [3, 10]`, `cap_pct = [2, 10]` | starting bands for the put's distance below and the call's distance above the share price |
| `stress_drop_pct = 20` | above the floor band's top, so every put inside the band offsets some loss at the stress |
| `max_net_debit = 0.00` | a collar of zero cost or a credit |
| a second ex-date label at 80 calendar days | `research/calendar.toml` holds one ex-date per symbol; 80 days is under a quarter of a year, so a quarterly payer's next ex-date is flagged with room to spare |
| a passed or unknown date counts as inside | the same honesty rule as an unknown dividend: a date the desk does not know is never read as none |

The three objectives, each computed in `Decimal` with a 28-digit context and ranked on the quantized value:

- **`extrinsic_per_day`** (covered call and roll): E = bid - max(0, S - K), score = E / S / T_c x 100 at 4 decimals,
  higher first. It reads only the fresh bid you could expect, the spot and the strike, so it needs no model and no
  forecast. The intrinsic part is subtracted because, for an in-the-money strike, it only offsets the value of the
  shares given up at the strike on assignment; what remains is the price of time. Dividing by the share price gives a
  percent, and dividing by the calendar days lets expirations compare. A model's expected value was not used: the
  implied volatility is solved from the mid, so under the model's own risk-neutral measure a sale at the bid is worth
  half the spread less than the model's value, and that value would rank the candidates by their spreads alone; any
  other measure needs a forecast of the share price, which the desk does not make. The risk is left to your delta band
  and your window.
- **`stress_cost_per_day`** (protective put): S_x = S x (1 - x / 100) with x = `stress_drop_pct`, score =
  ask / T_c / (K - S_x) x 100 at 4 decimals, lower first. It is the premium per day of cover per 100 of loss the
  put offsets at your own stress drop, from the fresh ask, the strike and the spot. Without the stress denominator
  the put with the lowest ask, the one furthest below the share price, would rank first, however little it offsets.
- **`cap_within_budget`** (collar): U = (Kc - S) / S x 100 at 2 decimals, higher first, among the put and call pairs
  of one expiration whose net debit (the put's ask minus the call's bid) is within `max_net_debit`. A collar gives
  up the upside above the call to pay for the put, so within your budget the desk ranks by how much upside is kept;
  the floor is your band's. A cost of protection per unit of downside covered was not used for collars: it ranks
  first the tightest cap with the deepest floor, which brings in the largest net credit. On the synthetic fixture it
  would put the SPY 2027-03-19 590 put and 680 call first, at a 7.65 credit.

The tie-breaks give every candidate set one total order, so the best candidate never depends on the order in which a
chain lists its contracts: a covered call by the absolute delta (lower first), the expiration (earlier first) and the
strike (higher first); a replacement call by the net credit (higher first), then the same; a protective put by the
strike (higher first, the higher floor) and the expiration (later first, the longer cover); a collar by the put
strike (higher first), the net debit (lower first) and the expiration (later first); the contract symbol last.

## Vendor behaviour

From Alpaca's public documentation (read 2026-11-13), as built into the client:

- market data is read on `data.alpaca.markets` with a paper key pair, sent as the headers `APCA-API-KEY-ID` and
  `APCA-API-SECRET-KEY`; the clock, the calendar and the option contract listing are read on the paper trading host
  `paper-api.alpaca.markets`, where Alpaca's order, position and account endpoints also live, so the client allows
  only those three paths there;
- on the free plan, stocks come from the IEX feed (the only one a paper-only account is entitled to) and options from
  the indicative feed, which Alpaca describes as delayed 15 minutes and not actual exchange (OPRA) quotes; the desk
  labels those quotes approximate, and `feed_delay_min = 15` is the conservative bound for every quote;
- open item, inferred and not yet observed on a live account: the IEX stock quotes are real time while the
  indicative option quotes are delayed, so an option stamp may sit about 15 minutes behind its underlying's stamp.
  The desk inverts an option price only against a spot stamped within 60 seconds of it
  (`expiry.SPOT_ALIGNMENT_S`), so in that case the implied-volatility outputs are UNAVAILABLE (the Opening Bell's
  `stamps_unaligned`, the book's IV and extrinsic, the Structuring Desk's `spot_unaligned`), never a 15-minute-old
  option price set against a current spot; `tests/tools/test_opening_bell_tool.py` pins that verdict. The fix is the
  owner's choice once a live read shows the gap: take the spot at the option's stamp (for example a one-minute IEX
  bar at that instant, on the allow-listed bars path) or a feed-aware tolerance up to the declared delay, disclosed in
  the text;
- the market-data limit is 200 requests a minute; a rate-limit fault is HTTP 429 with `X-RateLimit-*` headers, and a
  request the plan does not cover is refused with a message containing "subscription does not permit" (the published
  code numbers disagree, so the client matches the text);
- answers are paginated with `next_page_token`; the client follows at most 10 pages and counts each page as a request;
- a quote carries one time `t` (RFC 3339, nanoseconds), which the client writes as both the bid and the ask stamp, so
  the per-side freshness rules still apply;
- option snapshots carry Greeks and implied volatility computed from the indicative quotes; the desk ignores them and
  computes its own;
- the market-data host answers live keys too, which is why the doctor tells a paper key from a live one only on the
  paper trading host (a live key gets a 401 or 403 there; inferred from Alpaca's error guidance, not documented as a
  check, and confirmed at release);
- a paper key cannot reach a live account, though it could place paper orders on the paper trading host; the
  Ask-first rule, the hook and the connector check exist for that reason;
- whether the free indicative feed serves index options to a paper account is not confirmed, so the desk treats index
  options as ones it cannot quote;
- an Alpaca staff reply on its forum says the data is licensed for personal use only and may not be provided to third
  parties; the desk shows it only to its one owner.

## Fixtures

Every value in `tests/fixtures/desk/fixtures/alpaca/` is synthetic. The prices, sizes, stamps, bars and expirations
are the desk's synthetic world; the response shapes follow Alpaca's public API reference as read at authoring (quotes,
snapshots and bars from the market-data API; the option contract listing, the clock and the calendar from the paper
trading API). The Structuring Desk's chains are generated by `tests/fixtures/desk/fixtures/alpaca/_generate.py` from a
stated volatility surface, which the [fixture README](../tests/fixtures/desk/fixtures/alpaca/README.md) gives with
every input. No live capture has been made yet. A live paper read is checked at release, and this page records it
then. Every example output in the
documentation and the skills was produced by the tools on a copy of the fixture desk (a few on a deliberately
modified copy, which the example says) and is labelled synthetic.

## Compliance references

- FINRA Notice to Members 01-23, on when an online communication is tailored enough to read as a call to action; how
  the desk is designed to stay on the informational side is in [rules/examples/README.md](../rules/examples/README.md). That is a
  description of the design, not legal advice.
- OCC exercise by exception: a contract at least 0.01 in the money at expiry is exercised automatically. The desk uses
  this one-cent rule for in the money.
- The OCC exercise cutoff of 17:30 ET is a reference only. Your broker's cutoff is earlier, and the expiry tools'
  act-by time for a long option is your `broker_exercise_cutoff_et` from `desk.toml`. For a short option, the expiry
  tools and a LET_ASSIGN ticket use one rule: the session close of its last trading day (16:00 ET, 13:00 ET on an
  early close), or that cutoff when it is earlier, because a short option can be closed only while the session
  trades.
- AM-settled index options settle on the opening value of the settlement morning; their values are recorded by you in
  `research/calendar.toml` in Stage 1.

## Adapted repository mechanics

The repository mechanics (the gates, the manifests, the template contract, the hook's decision shape and the runbook
structure) are adapted from HyperGrok Trading Desk by Andrew Wilkinson and Galleon Labs, release 1.7.0 plus unreleased
changes, commit 75a4c79, under the MIT license reproduced in
[LICENSES/hypergrok-trading-desk.txt](../LICENSES/hypergrok-trading-desk.txt). The roles, skills, rules and tools are
Aegis Desk's own. [ATTRIBUTION.md](../ATTRIBUTION.md) names every adapted file.

Ported from the Aegis platform (FairGradient) and rewritten in standard-library Python: the pricing and Greeks engine,
the OCC symbol parser and the CSV import columns and rules of `positions.csv`.
