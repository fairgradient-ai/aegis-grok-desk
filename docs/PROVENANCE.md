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

## Vendor behaviour

From the plan's discovery of Tradier's documentation, as built into the client:

- the sandbox feed is delayed 15 minutes, serves no Greeks or implied volatility, no index quotes and no streaming;
- the request budget is 60 a minute; a rate-limit fault is an HTTP 400 whose text contains "Quota Violation";
- quotes carry a time for each side (`bid_date`, `ask_date`), which is how the desk measures freshness per side;
- a personal production token can trade and cannot be scoped read-only, which is why production data is refused in
  this release;
- a sandbox token cannot reach a live account, though it could place paper orders on the sandbox host; the Ask-first
  rule, the hook and the connector check exist for that reason.

## Fixtures

Every value in `tests/fixtures/desk/fixtures/tradier/` is synthetic. The quote and chain shapes follow the recorded
Tradier sandbox responses kept in the Aegis repository (FairGradient); the expirations, strikes, history, clock and
calendar shapes follow Tradier's public documentation as known at authoring. No live capture has been made yet. A live
sandbox read is checked at release, and this page records it then. Every example output in the documentation and the
skills was produced by the tools on a copy of the fixture desk (a few on a deliberately modified copy, which the
example says) and is labelled synthetic.

## Compliance references

- FINRA Notice to Members 01-23, on when an online communication is tailored enough to read as a call to action; how
  the desk is designed to stay on the informational side is in [rules/examples/README.md](../rules/examples/README.md). That is a
  description of the design, not legal advice.
- OCC exercise by exception: a contract at least 0.01 in the money at expiry is exercised automatically. The desk uses
  this one-cent rule for in the money.
- The OCC exercise cutoff of 17:30 ET is a reference only. Your broker's cutoff is earlier, and the desk's act-by
  time is your `broker_exercise_cutoff_et` from `desk.toml`.
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
