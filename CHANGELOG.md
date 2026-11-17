# Changelog

## 1.0.0 (unreleased)

- **Weekly expirations** (roadmap step 2.5; the owner's answer to open question 8). Every structure section of
  `structuring.toml` may set `expirations = "weekly"` (schema version 2; default `monthly`). Such a section adds every
  listed week-end expiration (the last trading day of its week: a Friday, or the Thursday when that Friday is a holiday)
  to the standard monthlies, and its `dte` window may span at most 21 days, so it holds at most four expirations and the
  four-chain cap stays as it is. The notes print `expirations: weekly and monthly in your N to M day window`, and
  `desk_init.py` takes the key. Daily expirations are never listed.
- **Step 2.3 landed: the structuring prices and the early-exercise line** (the owner's answers to open questions 2
  and 3).
  - **Structuring prices.** The Structuring Desk prices every candidate on the American model of record. Without a
    sourced yield an answer is UNAVAILABLE `dividend_unknown`, which replaces the q = 0 warning. The parity-carry
    estimate alone keeps European volatilities, the input of its own early-exercise bound.
  - **Notes and model wording.** The long put's `american-style note:` is retired. A short call's ex-dividend note and
    a short put's note read `early-assignment note:`. `MODEL_LINE` and `MODEL_NOTE` name the American model. The
    structuring texts say "dividend rate", because the structuring lint refuses the word yield.
  - **The early-exercise line.** New `aegis_desk.early_exercise` gives `assignment_watch.py` a line under each leg it
    lists, read by side: a long leg's exercise note or near-boundary line, a short leg's early-assignment warning, a
    long put's wait line when a stated dividend exceeds the interest on its strike, or why the line was not
    evaluated. It also gives the dividend note. A leg no tier lists is listed as `EARLY EXERCISE <leg>` when the lattice
    flags it, and `--json` adds `early_exercise`. The tiers, verdicts and exit codes do not change.
  - **Doctor.** The selftest gains an `american model` check: Hull's five-step put and an American volatility round
    trip, so the selftest now has six sub-checks.
  - **Journeys.** Four new journeys run on the new fixture root `tests/fixtures/desk_american` (the evening before an
    ex-date) and on the main root. They cover a dividend tier that stays HIGH where the lattice flags nothing, the
    wait line, the early-assignment warning, the band in the book, the exit rules' band guard, and `dividend_unknown`
    with `NO_YIELD`.
  - **Oracle.** The structuring-legs check compares with QuantLib's American engine.
  - **Docs.** Every "European-only" and "American pricing is planned" line is rewritten, and the roadmap marks 2.3
    landed.
- **The book on the American model of record** (roadmap step 2.3, third part; the owner's answers to open questions 2
  and 3). Every option leg's IV inverts the American lattice price from its mid, and its Greeks come from the lattice
  (`aegis_desk.american`). Each leg gains `model` (`american_crr_200`), `early_exercise_premium`, `greeks_quality`,
  `exercise_now` and `near_boundary`. A leg in the boundary band keeps an approximate delta and has null gamma and
  theta. A group's `net_greeks` go Greek by Greek and gain a `greeks_quality`, so one band leg no longer blanks a net
  delta. A leg without a sourced yield is `iv_status = "dividend_unknown"` (no IV, no Greeks), which replaces the q = 0
  warning in the book. An `abs_delta` rule threshold within 0.05 of a band delta reads UNAVAILABLE. Trigger levels'
  model levels solve the lattice delta with the sourced yield, and `vol_context` counts only days priced on the
  American model. The exit rules still read snapshots written before this change. The fixture book's American values
  move a few goldens: SPY's put IV 24.68 % to 24.59 %, net delta 278.84 to 278.67, and the MONETIZE level 572.79 to
  575.48. The structuring tools still price European until the last part lands.
- **The dividend yield as a sourced fact** (roadmap step 2.3, second part; the owner's answer to open question 3).
  `research/calendar.toml` is schema version 2: the yield is the `yield` of the symbol's `ex_dividend` entry
  (`ex_dividend = { date = ..., amount = ..., yield = 0.0090, status = ..., source = ..., checked_utc = ... }`), so
  it shares that entry's source, time and STALE check. Version 2 refuses a top-level `dividend_yield`; status `none`
  takes no yield and means zero. Version 1 files still load. `calendar_events.sourced_yield` says whether a yield is
  sourced (a version-2 `yield`, or status `none`); a bare version-1 yield is not. `calendar_check.py` gains the finding
  `NO_YIELD` for every held symbol without a sourced yield. `desk_init.py` writes version 2 and takes an optional
  `yield` with each dated ex-dividend. The fixture calendars are version 2. The book still prices with whichever yield
  the file holds until the wiring part lands.
- **The American leg pricer** (roadmap step 2.3, first part; engine only, not wired). `aegis_desk.american` prices
  one leg on the owner's model of record: the lattice's price, delta, gamma and theta in desk units, vega and rho as
  central differences of the lattice price, the early-exercise premium and `model = "american_crr_200"`. A leg in the
  boundary band keeps its price, vega, rho and an approximate delta (`lattice.BAND_DELTA_TOL = 0.05`); its gamma and
  theta are `None`. `american.implied_vol` inverts the lattice price (bounds: the undiscounted intrinsic value and the
  spot or strike; a quote on the intrinsic value has no single volatility), starting from the European volatility,
  which it never exceeds. The QuantLib oracle now asserts band delta within the tolerance, and the new pricer's vega,
  rho and implied volatility against QuantLib's finite-difference engine.
- **The owner's answers to the roadmap's open questions 1 to 8** (`docs/ROADMAP.md`). Step 2.3 comes next and 2.8
  after it. The model of record is American everywhere, one value per leg; in the early-exercise band delta is marked
  `approx` within a tested 0.05, and gamma and theta are UNAVAILABLE. The dividend stays a continuous yield, moved into
  the sourced `ex_dividend` entry, with a note on every leg spanning an ex-date and a long put's wait line; step 2.2
  (discrete dividends) is dropped. Weeklies narrow the `dte` window to 21 days under the same four-chain cap. Cash is
  optional per account, checked at the peak cash out and UNAVAILABLE after 5 trading days. Limits are two rule metrics
  in the exit rulebook. One GET-only path reads cash dividends. Journal and review totals are counts only. The planned
  `t1-call` and `midday-limits` routines are dropped (`tests/engine/test_routines.py` now fits fifteen ids), and
  three recorded `desk.toml` values are retired with step 2.6.
- **Volatility context** (roadmap step 2.9). The new read-only tool `vol_context.py` (`aegis_desk.vol_context`) reads
  the desk's own end-of-day book snapshots: a symbol's value for a day is the median implied volatility of its open
  option legs with `iv_status` ok (never the vendor's Greeks), and the context places the latest day's value among
  the earlier recorded days (`VOL CONTEXT | SPY | implied volatility 17.0 % on ... | 2 of 6 earlier days lower (33 %)
  | range ...`). Fewer than 5 earlier days, no value on the latest day or no snapshot is UNAVAILABLE (exit 20). It is
  information only: a test proves no module but the tool and the doc generator imports it, so no rule, alert, ticket
  or ranking can read it. No network, nothing written; a journey and `docs/TOOLS.md` cover it. `docs/ARCHITECTURE.md`
  no longer lists the collar's roll analysis as planned (it landed in step 2.4).
- **A vertical's roll and call-spread coverage** (roadmap step 2.4, the owner's answer to open question 9).
  `exit_ticket.py --action ROLL` on a vertical moves both legs to a later expiration at the same width:
  `--new-strike` names the new short leg and the long leg keeps the held distance. The four legs open the new long,
  close the held short, close the held long and open the new short. The approval convention gains the step
  `open_spread_call` (level 3): `open a short call covered by a long call of the same expiry (a defined-risk spread)`.
  `approval.Holdings.spread_calls` counts short calls a same-expiry long call covers after the shares have covered
  what they can (`holdings_of` pairs them per expiry), `approval.Leg.spread` marks a spread's short call (opening, or
  closing so that its spread goes with it), and closing a spread's long call first is `close_uncovers` (level 4). The
  kind is `replacement_vertical`, judged under no `structuring.toml` section: the ticket's `**The new vertical**` block
  gives the width, the new vertical's net and the roll's net per share in place of checks, and the payload's
  `replacement.held_other` carries a vertical's held long leg (or a collar's held call). A vertical has no roll
  analysis; its NOT_APPLICABLE reason says that a vertical's roll is a ROLL ticket you name.
- **A collar's two legs replaced as a unit** (roadmap step 2.4). `roll_analysis.py --trade` on a `collar` group ranks
  every pair of a later standard monthly inside `[collar] dte` and the floor and cap bands, judged as a fresh collar
  (its own net debit against `max_net_debit`, the strike floor, the call's events beyond the held call's life) and
  ranked by the section's objective; `exit_ticket.py --action ROLL` takes the new `--new-call-strike` beside
  `--new-strike` (the new put's). The kind is `replacement_collar`; the answer carries the held call as `held_call`
  beside `held` (a new key of the roll analysis and proposal payloads, null elsewhere). The four legs open the new
  put, close the held put, close the held call and open the new call (`open_long` 2, `close_option` 0 twice,
  `open_covered_call` 1), so the shares stay protected and covered at every step. The checks add `net of the roll per
  share` and name the new pair's own `new collar net per share`; with no best candidate both held legs' closes are
  quoted (`the roll's net closes the held ...`); the pointer asks for a ticket with `its expiration and both strikes`.
  A collar whose put and call differ in size is NOT_APPLICABLE. `after_state` closes more than one held leg, and its
  realized amount sums them. A vertical's other choices wait on the owner (the roadmap's open question 9: the approval
  convention counts only shares as a short call's cover).
- **A cash-secured put's short put: its roll analysis, a ROLL ticket and the early-assignment note** (roadmap step
  2.4). `roll_analysis.py --trade` on a `cash_secured_put` group ranks every put of a later standard monthly inside
  `[roll] dte`, and `exit_ticket.py --action ROLL` prices the one the owner names; both judge it under `[roll]` as a
  short call's roll is (the bands, `min_credit`, the roll test), with a put's time value `bid - max(0, K - S)` and the
  lower strike first among ties. `strike_floor` and `ex_dividend` are call rules and are not read; an earnings release
  inside the new put's life still counts. The held put closes at its ask first, then the new one opens at its bid
  (`close_option` level 0, `open_short_put` level 1), and the cash note says what each put secures
  (`cash: the new put is secured by ... and the held put by ...; the desk does not see your cash balance`). The kind
  is `replacement_short_put`, the header `the roll analysis of the short <date> <strike> put` and the ticket's
  `a roll of the short put`. `structuring.short_put_caveat` reads a long put's two tests (absolute delta 0.80 or more,
  or in the money with a time value at the mid below the interest on its strike) and prints `early-assignment note:
  ... can be assigned before expiry, and the European model does not price that`. An analysis of a group that holds no
  shares drops the `your cost` segment, and a ROLL ticket's checks caption names the new leg's right. Collars and
  verticals stay NOT_APPLICABLE.
- **The roll analysis of a protective put's long put** (roadmap step 2.4, its first part). `roll_analysis.py --trade`
  on a `protective_put` group ranks every put of a later standard monthly inside `[protective_put] dte`, labelled
  against that section's criteria and ranked by its objective (`stress_cost_per_day` by default), with the same
  pricing as a ROLL ticket for the long put. The text reads `the roll analysis of the long <date> <strike> put`, the
  best candidate opens the new put at its ask first and then closes the held put at its bid (S7), each check carries
  the net cost per day added, and with no best candidate the held put's close is quoted (`the net costs close the held
  ... put | bid ...`). The approval-level check runs on those two legs before any market data (opening a long put is
  level 2 by the desk's convention). `[protective_put] enabled = false` makes it NOT_APPLICABLE `disabled`, naming
  that section. A short put, a collar and any other structure stay NOT_APPLICABLE, now with `the roll analysis covers
  the short call of a covered call and the long put of a protective put`. "over" joins the structuring text's template
  words (the replacement put's net-cost row already printed it). The roadmap marks step 2.4 in progress.
- **Release 1.0.0** (owner's decision): `release_bump.py 1.0.0` moves the manifests, pins and template hashes from
  0.3.0, which was never tagged. It is the first major release: the Stage 1 desk with the Stage 2 and Stage 3
  roadmap and the roadmap's first engine step (below). No answer of the desk changes from 0.3.0.
- Version bump to 0.3.0 (`release_bump.py 0.3.0`): v0.2.1 is tagged, so the skills this release changes are
  claimed at 0.3.0 (`docs/RELEASE.md`, step 8). It merges the desk-view work of the 0.2.2 branch (one message per
  answer, the book view, the brief's totals) with the structuring quant review.
- **One answer, one message.** A chat client that draws a markdown table as a card of its own split every structuring
  answer into several bubbles. The Structuring Desk's desk view now prints no table: the context is one italic line
  under the title (`*account 2 of desk.toml (ira) | SPY mid 648.12 | your cost 632.40 | quotes 19:31 UTC, 15-min
  delayed*`), and the checks, the group now and after the legs, the cash, the approval steps, the ranked candidates
  and a ticket's key-value blocks are list items under capitalised captions (`**Checks for this candidate**`, `- <green>
  **floor** 4.34 % below | your criteria: 3 to 10 %`; `**The group, now and after both legs**`, `- **max loss**
  63,240.00 now, 1,165.00 after`; `**Candidates by rank**`, `- <red> **2** 2026-12-18 | new call 112.5 | debit 0.70 |
  extrinsic_per_day 0.0597 | fails credit_only`). The reasons none passed have their own caption (`**Why each one
  fails**`), and the ticket pointer and the collar's outside-bands count are plain lines, so no list runs into
  another. The wording, the numbers, the JSON and the compliance lines are unchanged. `approval.step_row` is
  `approval.step_item`; `structuring_text.table`, `status_cell`, `context_table`, `checks_table`, `group_table`,
  `candidates_table` and `cash_table` are replaced by `strip_line`, `status_mark`, `item_line`, `labelled_items`,
  `context_line`, `checks_block`, `check_item`, `group_block`, `candidate_items` and `cash_block`.
- The alert lint admits the three status circles at the start of a list item (`- ` at the very start of a line, then
  the circle and a space) of a structuring text, beside the start of a table cell; an indented or `*` item, a quoted
  item, a circle inside a word or a number, and any circle under the rules or snapshot footer stay `non_ascii`.
- **The Morning Desk Sheet and the EOD Desk Brief are a desk view too**: a `###` title, bold section titles (`**P/L**`,
  `**By trade**`, `**Desk status**`, `**To do**`), items with their details nested under them (`- **WMT covered
  call** +$1,145.00 at the mids (+$1,124.00 at liquidation)` over `  - *AG-20261020-01 | today +$84.00 (row 3),
  -$50.00 (row 4)*`; `- **Routines** 1 of 7 ran today` over the late, missed and pending slots), commands in code
  spans, the records read in one italic line at the end, and a blank line before the fixed footer so a chat never
  reads the footer as part of the last item. A gain carries its `+`. The numbers are the same; nothing is summed.
- **One total, one today, percentages and colours** in the EOD brief and the book view. `**Total**` is the open
  positions' unrealized P/L plus what was closed this year, with its percentage of the open positions' cost and the
  liquidation figure, and an italic line of what it is made of and which groups it leaves out; `**Today**` is the
  book's day P/L with its percentage of the value at the start of the day (or the book's own reason it is
  unavailable). Each position reads `- <colour> **WMT collar** +$7,634.14 (+17.99 %) | today ...` with `since entry,
  on a $42,436.22 cost | +$4,963.13 at liquidation` under it, so its P/L says what it is measured from, and one day
  P/L per position instead of one per row. An unavailable position says which row had no usable quote and why
  (`row 2 had no usable quote (grade E, stale)`). The new `aegis_desk.pl_figures` computes every total, per-group day
  P/L and percentage from the book document in `Decimal` (a group's day P/L only when every open leg has one, never
  partially; no percentage on a credit or on a start value at or below zero); the book and its snapshot are
  unchanged. A gain is green, a loss red and an unavailable figure yellow: the alert lint now admits its three status
  circles at a list item's start under the rules footer too (never in a table cell, a nested item, the footer line
  or under the snapshot footer); `fail_structuring_under_rules_footer` now fails `footer_missing` alone.
- **`book.py --view`**: the book of record as one linted markdown message for "how's my book?" and "analyze my
  positions". A `### YOUR BOOK <date>` title with an italic line of the open positions and the quotes' source and
  time; `**The whole book**` (unrealized at the mids and at liquidation, today, realized, net delta, the groups the
  totals leave out); a `#### <UNDERLYING> <structure>` section per open group with its id and account in italics, its
  P/L and net cost, its payoff and one item per open leg in words (`**2 short WMT 105 calls, 2026-11-20** cost $3.42,
  now $3.425 | P/L -$1.00 | today -$50.00`, the quote grade, liquidation mark, IV and days left in an italic sub-item);
  then the session line and the fixed footer. An unbounded max profit names the shares the short calls leave
  uncovered (`21.6301 of the 321.6301 shares carry no call`), so no one subtracts. The account prints as `account <n>
  of desk.toml (<type>)` and never an OCC symbol. A view that fails its lint prints only `WITHHELD: ...` (exit 1
  `lint_failed`); `--json --view` carries the message, its emitted document and the lint. The plain `book.py` output
  is unchanged.
- The Aegis Desk Lead answers "how's my book?" with `book.py --view` and may post it, like the sheet, the brief and the
  opening bell, as one message, as it is, never in a code block, with at most one plain sentence before it and no
  number of its own. The Structuring Desk posts its answers as one message, never split. The agents, the role skills,
  the structuring, roll, proposals, ticket, threshold-level and incident skills, `docs/STRUCTURING.md`,
  `docs/MONITORING.md`, `SECURITY.md`, `docs/TOOLS.md`, the alert fixtures `pass_structuring_*` and
  `fail_structuring_*`, the CLI journeys (one more, for `book.py --view`) and the template hashes follow.
- Multi-leg answers show what the quotes' spreads cost: a ticket of two legs or more adds `spread cost against the
  mids` to its cash block, and a collar proposal adds `spread cost` (half of both legs' spreads per share) to its
  checks. Each leg's JSON carries `spot_used`, the stock price its volatility was solved against, and a call's
  time value is now measured against that same price. The per-day objectives' JSON formulas say that T_c counts
  calendar days to the last trading day.
- Threshold levels: the `N sigma` distance of a model level is now the lognormal measure |ln(level / S)| / (sigma x
  sqrt(tau)) instead of the arithmetic |level - S| / (sigma x sqrt(tau) x S), so a rise and a fall of the same ratio
  read the same. The fixture's SPY MONETIZE level reads 1.42 sigma (was 1.34); the NVDA DELTA_SIGNAL level is unchanged.
- A ROLL ticket of a protective put's long put adds the row `net cost per day added`: (the new put's ask - the held
  put's bid) / S / (days added) x 100, in percent of the share price, so the cost reads net of what the held put still
  fetches. Candidates carry `incremental_cost_per_day` and `days_added`.
- `[roll] roll_test` (`schema_version = 2`, in place of `credit_only`): `"credit"`, `"none"` or `"net_time_value"`,
  which fails a roll whose new call holds less time value than the held call (N = E_new - E_held < 0) and passes an
  in-the-money roll that `credit_only` would fail. Its checks row shows N per share and says when N lies within the
  held call's own quote spread. Candidates carry `net_time_value` in the JSON.
- `structuring.toml` `schema_version = 2`: a section that ranks by `band_centre` may set its own centre in any band
  (`dte_centre`, `abs_delta_centre`, `floor_pct_centre`, `cap_pct_centre`, each inside its band) and `band_weights`
  (an inline table of numbers above 0 and at most 10 per band and for `quote`). The interview asks only where the
  owner chose `band_centre`; `desk_init.py` writes version 2 only when an answer uses these keys, so every version-1
  file and its sha are unchanged. The answer names the owner's settings under the objective's words.
- Structuring Desk: each expiration is priced at the carry its own put-call parity implies when those quotes rule out
  the calendar's dividend rate (with a quote-width bound and a one-sided early-exercise bound from a binomial tree);
  otherwise the calendar stands, with the reason in the JSON's new `carry` list. The text adds one `carry:` line per
  switched expiration after the model line. On the fixture the SPY March chain switches (call and put volatilities now
  agree within a few hundredths of a point instead of about 1.9 points apart), so the SPY group deltas read 70.17 and
  23.75 instead of 69.69 and 24.14; the WMT and NVDA chains keep the calendar, and no best candidate changes. The book,
  the levels and the exit rules keep the calendar's rate.
- Setup asks which objective ranks each structuring section. The interview (SETUP step 9) shows every objective a
  section accepts, with its words and the documented default first, and records the owner's choice; with no choice
  the default stays. An update (docs/UPDATING.md step 7) asks the same for an existing `structuring.toml`: keeping is
  the default and rewrites nothing; a switch is the owner's tap on `desk_init.py --replace --owner-tapped` and the
  acknowledgement.
- Structuring Desk: two optional objectives. `band_centre` (`[covered_call]`, `[protective_put]`, `[collar]`) ranks by
  the distance from the centre of your bands, each band counted in its own half-width, plus each leg's quote spread
  counted in your `max_spread_pct`, all summed with equal weight; `net_extrinsic_per_added_day`
  (`[roll]`) ranks a replacement call by the time value it sells less the time value the roll buys back, per calendar
  day added when that is at least zero and as the whole amount given up otherwise. Each section still defaults to its release-1 objective, and a file that names it ranks exactly as before.
- Quote precision, information only: an answer notes the ELIGIBLE candidates whose score lies within the best's own
  quote spread (`rank 2 lies within the best's own quote spread on stress_cost_per_day: ...`). The order and the best
  never change. The SPY protective-put example gains that line.
- `band_centre` answers name the share prices, above and below, at which another candidate would be best, against one
  feed delay's move at the best's volatility. Information only.
- The American caveat of a long put also applies to an in-the-money put whose time value at the mid is below the
  interest on its strike to expiry, K x (1 - e^(-r x tau)); the absolute-delta rule (0.80) is kept.
- `aegis_desk.carry` (not yet wired): the carry an expiration's quotes imply by put-call parity, with a quote-resolution
  bound and a one-sided bound on the American early-exercise bias; the calendar yield is replaced only when the quotes
  exclude it.
- Candidates carry `objective_scores` (every objective the section accepts, at the conservative side and at the mid)
  and a ranking carries `near_best` in JSON.
- **The roadmap** (`docs/ROADMAP.md`, linked from the README's table of docs and from `docs/ARCHITECTURE.md`): every
  item the tree marks "planned for Stage 2" or "planned for Stage 3", ordered into steps with their prerequisites, the
  gates each must pass, the open owner questions and a ledger of the files each step rewrites when it lands. Work the
  roadmap adds on its own judgement is marked "proposed here". The ledger counts, per file, how many times each file
  names Stage 2 and Stage 3 (spaced, hyphenated or joined, even across a line break), and
  `tests/gates/test_roadmap_ledger.py` fails when the counts differ from the tree, when a counted file is in no item
  row, or when a listed path does not exist; a stage is complete when its column is empty. The comment in
  `tests/engine/test_routines.py` about the dropped roll-window routine no longer names a stage.
- `aegis_desk.lattice` (not yet wired; step 2.1 of the roadmap): the American early-exercise lattice, a
  Cox-Ross-Rubinstein tree with a continuous dividend yield. It reports the American value with the closed form as a
  control variate, the early-exercise premium, delta, gamma and theta, `exercise_now` (only when the root and every
  node of the first two steps exercise, so a spot just inside the tree's exercise boundary, where a finer tree holds,
  is never an exercise signal) and `near_boundary` (the band where the tree cannot tell, and its Greeks are coarse).
  Tested against Hull's printed American put, the exact tree properties, `carry.early_exercise_gap` and the band; the
  QuantLib oracle compares it with QuantLib's finite-difference American engine on a grid and across the exercise
  boundary. No answer of the desk changes. The lines that said the lattice or an early-exercise model was planned now
  say what is still planned (American pricing, American Greeks, or early-exercise analysis beside the expiry tiers),
  and those that name the lattice say its engine is in the tree: `docs/FAQ.md`, `docs/PROVENANCE.md`,
  `docs/STRUCTURING.md`, `docs/ARCHITECTURE.md` (which also lists the lattice among the engine's modules),
  `agents/position-control.md` and its generated role skill, the covered-call, expiry-and-assignment,
  book-marking and roll-decision skills (their template hashes move with them) and the structuring `model_note`,
  whose new wording counsel signs before the tag; "lattice" leaves the structuring text's template word list with it.

## 0.2.1 (2026-11-13)

- Version bump to 0.2.1 (`release_bump.py 0.2.1`).
- The alert lint's fixed leg grammar takes one leading `- ` list mark, and a leg's quote segment may end at its grade
  (`| bid P, ask P, mid P | quote grade G`) when the text names its quote source once in its header: the groundwork
  for the Structuring Desk's easier-to-read layout. Any other list mark, an indent or a free tail after a leg is
  still `order_field`.
- **The Structuring Desk's answers are now a desk view in markdown**, built to read in a chat on a phone instead of
  long `|`-joined lines. A roll analysis, a proposal and a ticket each print a `###` title; a context strip (the
  account, the underlying's mid, your cost or the ticket's times, the quotes' time); the legs as list items in the
  fixed leg grammar (never a table with order columns); the caveat as a quote; a checks table with each criterion's
  value, your band and a status column; the group now and after the legs; the approval-level check with a table of
  the steps; the ranked candidates with their status; and the notes as a quoted list (the objective's words, the
  quote source named once, the model line, the criteria sha, the limit and act lines), then a blank line and the
  footer. Amounts carry thousands separators, the best candidate's rank and score are bold, and a net prints in words
  (`credit 0.05 (10.00 in all)`, `debit 0.70`) instead of a negative net credit. The Aegis Structuring Desk posts the
  text as it is, never in a code block.
- The status column: a green circle (U+1F7E2) for a criterion that passes, a yellow circle (U+1F7E1) for an earnings
  or ex-dividend label you accepted (`accepted by #assign-ok`, `#earnings-ok` or `your structuring.toml`), a red
  circle (U+1F534) with the keys a candidate fails. An answer with no best candidate lists under its candidates table
  why each shown candidate fails (`1, 2, 3 fail ex_dividend: ...`, `4 not priced: ...`).
- A proposal whose contracts cover fewer shares than the group holds says so under the group table (`3 contracts
  cover 300 of your 321.6301 shares; the other 21.6301 are not part of the collar`), which explains an unbounded max
  profit on an odd lot.
- Tickets: the cash at the conservative prices and at the mids, the realized P/L of a closing leg and the most the
  steps need at once form one table; a LET_ASSIGN ticket's short options and a MONETIZE_PUT ticket's puts are
  key-value tables; the act
  line reads `if you decide to act, you type it into your brokerage account yourself`, beside the unchanged `you
  supply the limit; the desk prints none`. `approval.approval_lines` returns the heading line and a steps table
  (`leg_order_line` and `step_line` are gone).
- The alert lint's one exception to plain ASCII: the three status circles, only at the start of a table cell (`| `
  before, a space after) of a structuring text and never in its footer; anywhere else they are `non_ascii`. The
  structuring-content check also finds a first line behind `###` or `**`. `check.sh` step 3 exempts the three circles
  from the docs emoji gate only inside a closed fenced block that quotes the desk's output.
- The alert fixtures `pass_structuring_*` are regenerated and the `fail_structuring_*` fixtures re-derived from the
  desk view (`fail_structuring_under_rules_footer` now also fails `non_ascii`); the CLI journeys match the new text.
  The guide's examples (`docs/STRUCTURING.md`), the agent's "Reading the output", the roll, proposals, ticket and role
  skills, `SECURITY.md`, `CONTRIBUTING.md`, `docs/POSITIONS_CSV.md` and the counsel items in `docs/RELEASE.md` follow
  the desk view; the template's skill hashes are updated.
- 4 more tests, 3108 in all (floor 3108).
- **One front door.** The owner talks to one Bot, the Aegis Desk Lead, in its 1:1 chat, for setup and everything
  after it: questions, position changes, plan cards, tickets, roll analyses, proposals, the criteria interview and
  re-acknowledgements. The Lead runs each seat's tools by that seat's agent file and enables the skills they need; no
  Bot sends the owner to another chat. The Ask-first card is the confirmation (no typed "approve", "re-ack" or "run
  it" before a card), the owner-authority commands of one request run as one `&&`-joined command line so one tap
  covers them, a trade id is found from the symbol instead of copied by the owner, one Bot answers the owner in the
  Floor, and Bots other than the Lead stay quiet until `desk.toml` exists (no tool on the empty desk, no routine
  before SETUP section 9.3). Saving `positions.csv` joins the owner-authority Auto-review rule (391 of 1000
  characters), so the save's card replaces the typed approval. Ticket texts now say `after a fill, tell the Aegis Desk
  Lead the fill` and `ask the Aegis Desk Lead for a ROLL ticket`.
- **Options quoted minutes before the stock are priced, not dropped.** On a live paper account the indicative feed
  updated thinly traded WMT options minutes before the IEX stock quote, and every collar candidate came back
  UNPRICED `spot_unaligned`. The Structuring Desk now solves such an option's implied volatility against the stock's
  one-minute IEX bar of the minute the option was quoted in (`alpaca_client.minute_bars`, `timeframe=1Min` on the
  allow-listed bars path, cached by `quote_cache.get_minute_bars`, one request), and reads its Greeks at the current
  spot; the text adds `spot: <n> option legs quoted more than 60 seconds from the stock quote were priced against the
  stock's one-minute IEX bar of the minute each was quoted in`, and the leg JSON carries `spot_bar_utc`. An option
  with no bar for its minute stays unpriced. A stock quote reused from an earlier run's cache but stamped more than 60
  seconds before the chains is refetched once first (`structuring.align_spot`). PROVENANCE's open item is closed.
- **Earnings dates through the intake.** `desk_init.py` takes an `earnings` answer (`{date, time pre | post |
  unknown, status confirmed | estimated}` or `{status none, source}`) beside `ex_dividends` and writes both into
  `research/calendar.toml`; SETUP 9.1 asks for each held symbol's next earnings date, and the new phrase "update the
  calendar" (SETUP 9.5) adds a date later through one card (`--replace --owner-tapped` joined to `ack.py config`).
- **Criteria that can never pass are said up front.** `structuring_config.never_passing_notes` names each call-writing
  section whose `dte` window starts past 100 calendar days with `earnings` or `ex_dividend` = `exclude` (the
  documented collar defaults): every such call spans a quarterly report or ex-date, so no candidate on a reporting or
  dividend-paying stock passes. `desk_init.py` prints them as `note:` lines and in its payload's new `notes`; the
  interview asks the owner to choose. Past such a failure the Bots offer `allow` in `structuring.toml` (structuring
  only, no alert silenced) before the row tags, whose alert silencing they now state.
- **Plainer first-run lines.** A new desk's first adoption posts `rulebook adopted: your first rulebook, no earlier
  verdicts to compare | rules X.Y.Z, sha ...` instead of `rulebook changed: digest unavailable (no snapshot)`; the
  morning sheet says `last night's levels: none yet (the end-of-day level sheet has not run on this desk)` instead of
  an UNAVAILABLE line while no level sheet exists; `positions_check.py` prints a group's net cost to the cent (the JSON
  keeps every digit). The charter adds two rules: no date or dividend from memory (UNAVAILABLE, never "has not
  declared one") and one clock (ET as the tools print it, never a converted time beside it).
- **Updating a desk already set up.** `docs/UPDATING.md`, the README's "Update a desk you already set up" prompt and
  SETUP 9.6: the changelog in plain words, the checkout moved to the new tag after the owner's tap, the gate, each
  Bot's skills refreshed with a readback, any changed Auto-review text re-pasted, the routines re-checked and an
  update receipt; the desk files are never rewritten, and the page lists what 0.2.1 changes for an existing desk.
- 12 more tests, 3120 in all (floor 3120).

## 0.2.0 (2026-11-13)

- Version bump to 0.2.0 (`release_bump.py 0.2.0`).
- **Market data now comes from Alpaca, read with a paper key; Tradier support is removed.** The owner's one first-run
  action is a free Alpaca paper key pair (a key ID and its secret, from a paper-only account opened with an email
  address), added through the secure secret card as `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`, never pasted in chat
  (SETUP section 7, the bootstrap skill, the FAQ). The fallback is the 0600 file `/workspace/.aegis-secrets/alpaca.key`
  (key ID on line 1, secret on line 2); `opening_bell.py --token-file` becomes `--credentials-file`.
- The one network module is now `scripts/aegis_desk/alpaca_client.py` (`check_no_write_paths.NETWORK_CLIENT`), with
  every earlier safety property kept: one literal GET with no body, redirects refused, no proxy handler, TLS 1.2 or
  later, a response-size cap, redaction of both the key ID and the secret, and refusal of an answer that echoes
  either (a non-200 body that echoes either in JSON escapes is withheld whole). Two hosts are pinned: `data.alpaca.markets` (`/v2/stocks/quotes/latest`, `/v2/stocks/bars`,
  `/v1beta1/options/quotes/latest`, `/v1beta1/options/snapshots/<UNDERLYING>`) and `paper-api.alpaca.markets` (only
  `/v2/clock`, `/v2/calendar` and `/v2/options/contracts`). The live trading host `api.alpaca.markets` is refused.
  Answers are paginated (`next_page_token`, at most 10 pages, each counted as a request); new statuses
  `no_credential`, `credential_file_mode`, `subscription` (a request the free plan does not cover) and `incomplete`
  replace `no_token` and `token_file_mode`.
- The free plan's feeds, labelled: stocks from IEX; options from Alpaca's indicative feed, delayed 15 minutes and not
  exchange (OPRA) quotes. Every quote segment, footer, alert, structuring text and the Opening Bell's snapshot line
  now name the source from one constant (`aegis_desk.data_source`): `Alpaca paper (IEX stocks, indicative options:
  approximate)`. `options_feed = "opra"` is accepted for a subscriber. `feed_delay_min` stays 15, the conservative
  bound, and the indicative feed requires at least 15. The Structuring Desk's disclosure text now says the quotes are
  delayed Alpaca paper quotes, the option quotes approximate (its id is unchanged).
- `desk.toml` `[market_data]`: `vendor = "alpaca"`, `host = "paper"` (any other host, `live` included, fails at load),
  `key_env`, `secret_env`, optional `credentials_file`, `options_feed`, `stock_feed = "iex"`; `token_env` and
  `token_file` are gone. `key_env` and `secret_env` must be upper-case variable names with an underscore
  (`APCA_API_KEY_ID`), one rule shared with `desk_init.py`, so a pasted key ID or secret is refused and never shown.
  `desk_init.py` writes the new block and refuses a live host (`live_host`).
- The desk doctor: check 4 is `key_source` (names the two variable names or the file, never a value); check 5 is
  `paper_key`, one GET of `/v2/clock` on the paper trading host (which refuses a key from another environment):
  `paper key verified`, or a 401/403 FAIL `not a paper key: remove it and add a paper key`. Check 7 exempts exactly
  the configured key and secret names from the broker-name rule (any other `ALPACA_*` name still fails; a configured
  `APCA_LIVE_*` name fails too) and fails on an `APCA_API_BASE_URL` that names the live trading host.
- The live Opening Bell reads its contract listing on the paper trading host, so a key that host refuses after the
  market-data host answered it is `UNAVAILABLE: not a paper key: ...` (exit 20, `not_paper_key`): a live key fails the
  first run's check.
- Quote grading: an ask of 0 under a positive bid (Alpaca's "no active offer") is a missing ask, `ask missing (no
  offer)`, grade F, never a crossed market.
- The quote cache counts every request, not every call: a quotes batch with stocks and options is 2 requests, a chain
  at least 2 (the contract listing, then the snapshots). The desk-wide window is 200 requests a minute (the free
  plan's market-data limit) minus a headroom of 20; no new fetch starts once a run has made 40 requests (a chain
  only while its minimum of 2 still fits; a paginated answer can finish past 40), and a run fetches at most 4
  chains. A symbol the vendor did not quote is `fetch_failed:not_returned`. Index options stay unquotable, now worded
  "the data source cannot quote index options".
- The offline fixtures moved to `tests/fixtures/desk*/fixtures/alpaca/` in Alpaca's response shapes, generated from
  the same synthetic values, so every price, size and stamp golden holds; request counts and the `desk.toml` hash in
  the journeys changed.
- The write-side Ask-first Auto-review rule now names Alpaca: any request to a brokerage's order, position or account
  endpoints, including any request to `api.alpaca.markets`, and any request to `paper-api.alpaca.markets` other than
  `/v2/clock`, `/v2/calendar` and `/v2/options/contracts` (487 characters; the Allow rule says "GET-only Alpaca paper
  client").
  Paste the reprinted rules (`routine_audit.py --rules-text`) again. The Grok Build deny rules name the Alpaca hosts.
- The broker-write hook no longer exempts any market-data read on a broker host: an HTTP call that names a broker host
  is denied, reads included (the desk's tools read market data from Python, never through a shell or an MCP tool).
  The replaced vendor's host left the hook's broker-host list; a write to any account or order path is still denied
  on every host.
- 3103 tests in all (floor 3103).
- Every document, skill, agent and role skill is rewritten for Alpaca, including the FAQ's paper-key answer, the free
  plan's limits and the personal-use data licence note.

## 0.1.1 (2026-11-13)

- Onboarding on Grok Bot is staged. The first run (SETUP sections 1 to 8) asks the owner for one thing: a free
  Tradier sandbox market-data token, added through the secure secret card, never pasted in chat. It shows a demo on
  a copy of the sample desk before anything is asked, then returns a receipt that opens with three plain lines and
  ends with `Setup stayed read-only: one sandbox market-data token was added by you; no order was created or sent.`
  Everything else waits for a phrase the owner says, each a part of SETUP section 9:
  - "add my positions" (9.1): first the two Ask-first Auto-review rules and the connected-apps readback
    (`ack.py connectors --owner-tapped`), because `desk_init.py` follows; then four desk questions with the other
    settings proposed for one "yes"; Aegis Position Control drafts the `positions.csv` rows from the owner's own
    words and saves them once the owner approves; dividends for held symbols; the full doctor;
  - "adopt my exit rules" (9.2): one answer (keep the examples, or name those to change or drop), proposed
    defaults for `DAILY` and `CONC`, and `--allow-rth` offered during regular trading hours;
  - "turn on monitoring" (9.3): the routine capability probe, Notifications and Timezone, the Allow rule for
    `routine.py`, the `safe.directory` fix if the doctor asks, routines created paused, one Test run, unpause;
  - "set up the structuring desk" (9.4, optional): the structuring interview. "SETUP step 9" still names the step
    that holds the structuring criteria.

  9.1 comes first: whichever phrase the owner says first, its two safety settings come before any owner-authority
  command and any routine (SETUP section 9, the bootstrap skill, `agents/desk-lead.md`, `agents/structuring-desk.md`,
  `docs/STRUCTURING.md`, the README, the FAQ and `SECURITY.md`). 9.2 needs 9.1's files, and 9.3 an adopted rulebook.
- The first run's edge cases: when git refuses the checkout, the `routine.py` demo is skipped until the
  `safe.directory` fix of 9.3 (never `--allow-dirty`); the demo's CONFIG CHANGED line is named as the sample desk's;
  the live Opening Bell takes `--token-file` with the file fallback; a token that fails with `auth:` (often the
  production token pasted by mistake) is deleted before anything else; and the receipt's last line says a sandbox
  token was added only when the live bell succeeded.
- Aegis Position Control may save rows it drafted from the owner's plain description, once the owner approves the
  exact text shown, byte for byte, with only the owner's numbers (`agents/position-control.md`, the role skill,
  `aegis-positions-csv-book-of-record`, `docs/POSITIONS_CSV.md`).
- `SETUP.md` is rewritten for that shape (516 lines to 269). The bootstrap skill
  `aegis-desk-bootstrap-and-onboarding` (metadata version 0.2.0) now covers only the first run and points to SETUP
  section 9 for the rest.
- `scripts/opening_bell.py --demo`: the Opening Bell on the checkout's own sample desk at its fixture instant
  (`--offline --desk-root <checkout>/tests/fixtures/desk --now 2026-11-03T19:50:00Z`, SPY by default). It writes
  nothing, reads no credential, and its first line says the data is a synthetic sample, not market prices. With
  `--desk-root`, `--now`, `--fixture-set` or `--token-file` it is a usage error (exit 2). The JSON payload gains
  `demo` (true or false). A new journey runs it exactly as published (the `Journey` record gains `checkout_only`, so
  it never runs against another tree). 8 more tests.
- `README.md` is rewritten for a first-time reader: see it, try it, start it. A hero image, three badges, a
  "Try it now" with three commands ending in `--demo`, one Start prompt and its phrases, "Meet the desk",
  "A day on the desk", "Controls", the runtimes and the repository map. The anchors other pages link to (`#start`,
  `#try-it-now`, `#meet-the-desk`, `#controls`) are kept.
- `assets/`: five original SVGs (the hero and the desk flow, each with a phone-width variant, and a terminal card
  of the real `--demo` output), built by the standard-library script `assets/src/build_graphics.py`, whose
  `--check` compares them with the committed files. `tests/gates/test_readme_assets.py` runs that check, holds every
  text colour at 4.5:1 contrast or more, and requires a markdown link to each SVG (in `assets/src/README.md`) so the
  link gate sees them. 4 more tests, 3052 in all (floor 3052).
- Docs follow the staged path:
  - `docs/FAQ.md` answers "What does setup ask me for?" and "How do I get a Tradier sandbox token?", and marks
    unconfirmed facts as such;
  - `SECURITY.md` says when each Grok Bot control arrives, and is honest about the window before
    "add my positions": the sandbox token could place sandbox paper orders, while the repository code still has no
    write path;
  - `docs/aegis-grok-bot.md` describes the first run and the phrases;
  - the runtime pages drop "the tag is created at release";
  - `docs/GROK_BOT_TEMPLATE.md`, `docs/STRUCTURING.md`, `docs/EXIT_RULES.md`, `skills/README.md` and the anchors in
    `docs/RELEASE.md` follow the new SETUP numbering.
- Skills `aegis-routines-and-quiet-rules`, `aegis-expiry-and-assignment-protocol` and
  `aegis-rules-dsl-and-change-control` name the phrase that starts their part of setup; `template/grok-bot.json`
  records the new sha256 of each, of the bootstrap skill, of `aegis-positions-csv-book-of-record` and of the three
  role skills regenerated from the edited agents.
- Version bump to 0.1.1 (`release_bump.py 0.1.1`): every manifest, marketplace ref, tag pin, template hash, role
  skill and tool version moves together.

## 0.1.0 (2026-11-12)

- Release workflow: `.github/workflows/release.yml` tags `main` and publishes the GitHub Release. It runs only when
  someone with write access starts it by hand (Actions, Release, Run workflow, on `main`), with the version (as in
  `plugin.json`) and a pre-release switch. A read-only `gate` job, which keeps no credential in its checkout, repeats
  the gate of `docs/RELEASE.md` step 5: the version matches `plugin.json` and has a section here, and
  `bash scripts/check.sh` and `release_bump.py <version> --check` pass. Only then does the `release` job, the one job
  with write access (`contents: write`), create the annotated tag on the same commit, push it, run
  `check_grok_template.py` and publish the Release with this section as its notes. A re-run after a failure reuses a
  tag already on that commit and an existing Release, and refuses a tag on any other commit. The inputs reach the
  shell only through `env:`. `tests/gates/test_release_workflow.py` pins these properties (12 more tests, 3040 in
  all; floor 3040).
- A short option's act-by (FAI-689): the expiry checklist (the FINAL CALL, the T-1 preview and the PIN lines), the
  assignment watch and the morning sheet print a short option leg's act-by as the session close of its last trading
  day (16:00 ET; 13:00 ET on an early close), or `broker_exercise_cutoff_et` when that is earlier, because a short
  option is closed only while the market trades; its text ends `; the session close: a short option is closed only
  while the market trades` and carries no early-close mark. A long leg keeps the broker cutoff, with the early-close
  rule and mark (it governs exercise and do-not-exercise instructions):
  - one helper, `expiry.leg_act_by`, serves the expiry sheet and the LET_ASSIGN ticket, whose printed text is
    unchanged;
  - `ActBy.basis` and the leg JSON's `act_by_basis` (`session_close` or `broker_cutoff`) name the instant that
    governed, described by the `--schema` of `assignment_watch.py` and `expiry_checklist.py`;
  - the FINAL CALL header names the earliest act-by of the listed legs when they differ, each leg line keeping its own;
  - each docket line of the Morning Desk Sheet and the EOD brief names its contract, side and size first
    (`<trade_id> <OCC symbol> <long|short> <contracts> | ...`), since the legs of one group can act by different
    times; a docket record whose legs lack them is not the record the desk writes;
  - 15 more tests, 3028 in all (floor 3028).
- The Aegis Structuring Desk joins Stage 1 as a fourth Bot on the Floor. It answers only the owner's own request,
  never runs in a routine and never fires an alert:
  - `scripts/exit_ticket.py` (`--action` CLOSE, UNWIND_STOCK, LET_ASSIGN, MONETIZE_PUT, or ROLL with
    `--new-expiration` and `--new-strike`), `scripts/roll_analysis.py` (the replacements for a covered call's short
    call) and `scripts/propose.py` (a covered call, protective put or collar on a group that holds shares only), on
    the library `scripts/aegis_desk/structuring.py`, `structuring_text.py` and `tickets.py`: candidates from the held
    underlying's own monthly chains, priced with the European model and labelled as such, labelled against the owner's
    criteria and ranked by the objective the owner declared; one best candidate when one passes every criterion;
    tickets with the legs in a fixed order, their quotes, the cash at the conservative side and at the mid, and no
    order fields; no `positions.csv` lines (Position Control proposes them after a fill);
  - the owner's criteria `structuring.toml` and its strict reader (`scripts/aegis_desk/structuring_config.py`),
    rendered by `desk_init.py` from the structuring interview with its one-time disclosure; it joins the CONFIG
    CHANGED set acknowledged by `ack.py config`, and the structuring tools answer UNAVAILABLE while `desk.toml`,
    `positions.csv` or `structuring.toml` are unacknowledged, or while any group of `positions.csv` is quarantined;
  - the approval-level check (`scripts/aegis_desk/approval.py`), the first reader of `desk.toml`'s self-attested
    `approval_level`, in the desk's convention;
  - a third footer and the alert lint's structuring mode (`alert_lint.py --footer structuring`), with a fixed leg
    grammar; the rules and market-snapshot footers refuse a leg reference, a ranked candidate or a structuring
    answer;
  - `quote_cache.get_expirations`, cached per ET date (an empty list is never cached), and synthetic structuring
    chains in the fixture desk;
  - `routine.py` refuses the three tools as routine steps; the owner-authority Auto-review text names them (333 of
    1000 characters), so each run asks first;
  - `tickets/`, `reports/rolls/`, `reports/proposals/` and the audit journal `journal/structuring.jsonl`;
  - the agent `agents/structuring-desk.md` and its generated role skill; the skills `aegis-overlay-proposals`,
    `aegis-roll-decision-framework` and `aegis-ticket-and-unwind-order`; `docs/STRUCTURING.md`, the owner and counsel
    reference; the provenance of the criteria defaults and the three objectives (`docs/PROVENANCE.md`); SETUP step
    9's structuring criteria; the README, FAQ, monitoring, security and runtime pages for four Bots;
  - 429 more tests, 3013 in all (floor 3013).
- Release tooling:
  - `scripts/release_bump.py <next> [--check]` moves the version in `plugin.json`, every mirrored manifest, the
    marketplace refs, the template's release and skill hashes, every pin `check.sh` step 4 checks, the role skills
    and the tool versions in `docs/TOOLS.md`, in one pass, writing every file or none; it refuses a version that is
    not plain or not greater, a tag that already exists (read from `.git` as files, worktrees included; a `.git` it
    cannot read refuses the run) and a tree that is not consistent at its version, never edits `CHANGELOG.md` or
    `docs/RELEASE.md`, and lists plain mentions of the old version for review;
  - the stub MCP server of the release deny check, `tests/fixtures/release/blackhole_mcp.py` (one tool that does
    nothing), and its `--mcp-config` file;
  - `docs/RELEASE.md`, the 0.1.0 checklist and receipt, with who does each step and empty evidence rows;
  - 82 more tests, 2584 in all (floor 2584).
- Stage-1 content:
  - four role profiles (`agents/desk-lead.md`, `agents/exit-sentinel.md`, `agents/position-control.md` and, with the
    Structuring Desk, `agents/structuring-desk.md`) and their four generated role skills; eighteen Stage-1 skills,
    each with its `LICENSE` and `ATTRIBUTION.md` (one adapted: the bootstrap skill, which carries its notice line in its
    body);
  - the broker-write gate `hooks/broker_write_gate.py`, a backstop that denies a shell command or MCP tool call that
    looks like a broker write and gives no opinion on reads, registered by `hooks/hooks.json` (Claude Code and Grok
    Build) and `hooks/cursor-hooks.json` (Cursor, fail-closed, answering `ask` when it has no opinion);
    `check_manifests.py` asserts the wiring exactly, with no other key at any level;
  - `check.sh` step 2 requires an adapted skill's in-body notice line; `ack.py connectors --owner-tapped` records the
    owner's readback of connected apps and MCP servers, which the doctor re-asks after 30 days; `routine.py`'s
    `--desk-root` help names its `/workspace/aegis-desk` default;
  - manifest descriptions of the Stage-1 desk without counts; the Cursor manifest's `rules` names
    `rules/aegis-desk.mdc`;
  - `template/grok-bot.json` claims every skill and role skill by its full-file sha256 and exports the bootstrap skill;
    its status stays `unpublished` until the owner publishes it after the tag;
  - `SETUP.md` (the Stage-1 runbook), the README, `SECURITY.md`, the architecture, FAQ, positions, monitoring and
    provenance pages, the page on your exit rules, the runtime pages for Grok Bot, Grok Build (with copy-paste
    `[permission]` deny rules for `~/.grok/config.toml`), Claude Code, Cursor, Codex and Gemini CLI, the template
    page, the skill index and the attribution table;
  - 2502 tests (floor 2502).
- Counsel review before the public release (an owner action; nothing here is signed off):
  - the alert texts (EXIT SIGNAL, WATCH, UNAVAILABLE, CONFIG CHANGED, the pre-close and watch-alive lines) and both
    fixed footers;
  - the threshold-level sheet and the exit-plan card, which carry no order shape (`[levels] order_shape_enabled` stays
    `false`);
  - quoting diagnostic output: a Bot quotes it only when the owner asks, unchanged, in a fenced block headed by the
    exact command, and the proposed `positions.csv` lines of `csv_recon.py --diff` appear only in Aegis Position
    Control's chat;
  - the Structuring Desk (rows 1 to 9 of `docs/RELEASE.md` step 4): the structuring footer and the disclosure, the texts
    of the tickets, the roll analyses and the proposals, the approval convention and the REFUSED line, the criteria
    defaults and the interview, the FAQ answer and the README description, whether publishing software that names a best
    candidate raises investment-adviser questions, and whether the release may advertise the Structuring Desk.
- Levels, routines and the doctor:
  - the threshold-level sheet (`levels.py`, `trigger_levels.py`): each adopted rule as an option or spread
    price, an underlying level, a band or a date, with its arithmetic shown, rounded on the class's tick grid in
    the direction that keeps a resting take-profit inside the rule, take-profit levels marked resting-eligible
    and loss levels alert-only, and exit-plan cards;
  - the routine manifest (`routines/manifest.toml`, `routines.py`) with the Stage-1 routines, their posting
    policies, dependencies and weekly budget (lean 41, active 61 runs), the Grok Bot Auto-review texts
    (`routines/autoreview.toml`) and `routine_audit.py`;
  - `routine.py`, the single reviewed routine entry point: it refuses to run a step on a checkout that is not the
    pinned release, runs the chain in-process, lints every part and the whole message, and writes the heartbeat,
    journal and run record;
  - the desk doctor (`doctor.py`, `desk_doctor.py`): twelve checks, including sandbox-token verification and
    broker-looking environment names;
  - the Morning Desk Sheet and EOD Desk Brief (`summaries.py`, `eod_summary.py`), including P/L per open trade;
  - `desk_init.py`, which renders `desk.toml` byte for byte from the interview;
  - `opening_bell.py`, with a market-snapshot footer that the lint accepts only on rule-free text;
  - `gen_tools_doc.py` (`docs/TOOLS.md`) and `gen_role_skills.py` (the role skills), both drift-checked by
    `check.sh`;
  - `csv_recon` and `expiry_checklist` now write their records (`data/positions/<date>.recon.json`,
    `reports/expiry/<date>.<mode>.json`);
  - the no-write gate also rejects loader and reflection routes, laundering through the subprocess-allowed
    modules, git configuration through arguments or the environment, and environment writes;
  - two placeholder agents; twenty-four more journeys (81 in all).
- Rules engine: the strict exit-rulebook grammar with parameters bounded in their safe direction, the metric
  catalogue on the book's fields, Kleene evaluation and the verdict precedence EXIT_SIGNAL > UNAVAILABLE >
  WATCH > HOLD, the adoption lint and per-rule shas (`scripts/aegis_desk/exit_rules.py`); the per-(group, rule)
  state machine with time-based confirmations on quote stamps, bounded re-arm, a cooldown that never holds a
  re-arm past the next session's open, first observation and carry-forward (`rule_state.py`); the alert texts
  with their fixed footer and emitted-number documents (`alerts.py`), the lint that proves every number and
  identifier was emitted and that no trade language appears (`alert_lint.py`), the signal and alert journals
  (`signals.py`); the tools `exit_check.py`, `rules_diff.py`, `rules_adopt.py` (owner-tapped, immutable
  versions, refused in regular trading hours unless `--allow-rth`), `book_check.py` (posting rules, re-post
  ladder, CONFIG CHANGED first, dry run without `--post`), `ack.py` and `alert_lint.py`; the example rulebook
  `rules/examples/exit_rules-1.0.0.toml` (each rule illustrative until adopted), adopted rulebooks in both
  fixture roots, red-team lint fixtures; twenty-eight more tool journeys.
- Book follow-up: every book leg carries `fresh_bid` and `fresh_ask` (the liquidation sides of a grade
  A-E quote; a zero ask stays 0, no offer), and `extrinsic_bid` now uses the assignment watch's geometry
  (any grade A-E fresh bid, the usable underlying mid stamped within 60 s, the strike as a plain decimal),
  so the book and the expiry sheet agree on every quote state; `mark_source` is `mid` only with both sides
  fresh. `quote_cache.py` and `quote_qc.py` classify the session with the research calendar's full
  closures like the book tools (`quote_qc.py` also takes its prior close from it; an unparsable calendar
  is exit 1 `calendar_invalid`). The command-journey step test lists its temporary directories under a
  private root, so concurrent local runs cannot collide.
- Book of record: the hand-edited `positions.csv` parser with the desk's money conventions
  (`scripts/aegis_desk/positions_csv.py`), strategy grouping, the `Decimal` payoff and P/L kernels, the
  book (`book.py`: one quotes request, quality grades, IV and Greeks, group metrics, portfolio totals that
  name every excluded group, the end-of-day snapshot and report; the session classified with the
  calendar's full closures), the reconciliation engine with every settlement branch (`recon.py`), the
  owner-sourced research calendar reader with its freshness lint (`calendar_events.py`), the expiry
  sheet with dividend and pin tiers (`expiry.py`: a dividend tier only while the contract is alive on
  the last cum-dividend day; the act-by instant is the earlier of the broker cutoff and an early close),
  daily-bar history in the quote cache; the tools `positions_check.py`, `csv_recon.py`, `book.py`,
  `calendar_check.py`, `assignment_watch.py` and `expiry_checklist.py`; a second fixture root
  `tests/fixtures/desk_recon/` covering every settlement branch and three more fixture sets (T-1, expiry
  day, post-expiry); seventeen more tool journeys, including multi-step chains on one fixture copy.
- Data plane: the GET-only Tradier market-data client (`scripts/aegis_desk/tradier_client.py`:
  pinned hosts, allow-listed paths, literal `GET` and no body on every request, no redirects, no
  proxy, TLS 1.2 or later, one receipt per request, credential redaction, refusal of a `200` that
  echoes the credential, loopback fake-server tests asserting the method on every request) with
  an offline transport over the recorded fixture desk `tests/fixtures/desk/` (five sets: regular,
  stale, after the close, before the open, holiday); the quote cache with the desk-wide
  60-per-minute ledger, per-run caps and a run-wide stop after a quota or credential error;
  quote quality grades with session-aware freshness; heartbeat records; shared vendor types,
  desk paths and the strict `desk.toml` reader; the tools `quote_cache.py`, `quote_qc.py`,
  `heartbeat.py` and `market_clock.py`; `--now` on the shared command-line contract; nine tool
  journeys. The no-write gate also rejects subclasses of `urllib.request.Request`, assignment of
  a request's `_data` or target fields, `set_proxy`, and reflection into third-party HTTP clients.
- Repository gates: `scripts/check.sh` (structure, Python 3.9 floor, version pins, test-count
  floor, command journeys), `check_manifests.py`, `check_no_write_paths.py` (AST no-write gate and
  prose scanner), `check_grok_template.py`, `cli_journeys.py`; CI on Python 3.9, 3.10, 3.12 and
  3.13 plus the QuantLib oracle workflow; negative fixture trees for every gate.
- Engine library `scripts/aegis_desk/`: Black-Scholes-Merton pricing, implied volatility, desk-unit
  Greeks and units ported from the Aegis quant engine with their golden values; NYSE market clock
  with embedded 2026-2027 calendar and DST rules; OCC symbol parser; strict vendored TOML reader;
  atomic state and file locking; the shared command-line contract and exit-code table.
- Repository scaffold: manifests, licences, placeholder role and skill, documentation skeletons.
