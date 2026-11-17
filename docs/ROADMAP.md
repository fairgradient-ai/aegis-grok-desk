# Roadmap: Stage 2 and Stage 3

Release v1.0.0 is Stage 1, with the engine of step 2.1 that no answer reads yet: four Bots (the Aegis Desk Lead, the
Aegis Exit Sentinel, Aegis Position Control and the Aegis Structuring Desk) watch the positions you already hold and
tell you when one of your own exit rules is met. This page collects everything the repository says is planned for
Stage 2 and Stage 3, in the order it can land, with what each step must prove before it merges. Nothing on this page
is available in this release unless a step says it has landed, and a landed engine step changes no answer a Bot gives
until its wiring step lands too.

When a Bot is asked for a planned seat or its work, it says that it is not available in this release
([the charter](../skills/aegis-desk-charter/SKILL.md)). This page does not change that answer.

## How this page stays true

- **Its sources.** Every planned item comes from a line in the tree that names Stage 2 or Stage 3 (most say "planned
  for Stage 2 (not in this release)" or "planned for Stage 3 (not in this release)"), from the planned-routine list in
  `tests/engine/test_routines.py`, or from the `beta_weighted_delta` placeholder in `scripts/aegis_desk/book.py`. Work
  this page adds on its own judgement, which no line in the tree plans, is marked **proposed here** wherever it
  appears, and nothing planned waits for it unless your decision makes it necessary.
- **The ledger.** [Where each item is mentioned today](#where-each-item-is-mentioned-today) maps each item to the
  files that mention it, so the pull request that lands an item can find the lines it makes untrue, and counts, file
  by file, how many times each file names Stage 2 and Stage 3 (as "Stage 2", "Stage-2" or "Stage2", in any case, even
  across a line break). `tests/gates/test_roadmap_ledger.py` fails when those counts differ from the tree, when a
  counted file is in no item row, or when the ledger lists a path that does not exist. This page, the changelog and
  the ledger test (whose fixtures spell the pattern) are not counted. So any pull request that adds, moves or removes a
  later-stage mention updates the counts, and its reviewer checks that the file's item row covers the new line: the
  gate cannot tell which item a sentence is about. A promise is never split so that "Stage" and its number fall in
  different string literals.
- **One pull request per step**, squash-merged into `main` as [CONTRIBUTING.md](../CONTRIBUTING.md) describes, each
  with its tests, its doc changes and `bash scripts/check.sh` green. A step that changes a skill or a template-claimed
  file after a release tag follows the release bump rule of CONTRIBUTING.md first, and every skill it changes gets its
  new sha256 in `template/grok-bot.json` in the same pull request.
- **When a step lands**, its row below says so, and every line it makes untrue is rewritten in the present tense in
  the same pull request. A line that says an item is "planned for Stage N (not in this release)" while that item is in
  the tree is untrue, even when no tool reads the item yet.
- **The completion test.** Stage 2 is complete when no row of the
  [mention table](#mentions-by-file) has a Stage 2 count, with `bash scripts/check.sh` green; Stage 3 likewise with its
  own column. The ledger test holds the table equal to a whitespace-collapsed scan of the tree, so a promise wrapped
  across a line break is counted too (a line-by-line `git grep` would miss it). The rows shrink as steps land and the
  table may end empty, so the gate stays green at every step and after the last.
- **"Needs" means a hard prerequisite**: the step uses that step's code or that decision. An order chosen here for
  convenience, not required by the tree, is written "after ..., by choice".
- **No release numbers are promised here.** A release is cut when the owner decides, with `release_bump.py` and the
  checklist in [RELEASE.md](RELEASE.md).

## What no stage changes

These hold for every step on this page. A step that needs any of them changed is not on the roadmap.

1. The desk never places, modifies or cancels orders. No step adds a broker write path, a credential request or an
   order-shaped output. The AST gate `scripts/check_no_write_paths.py` and the structural lint in `scripts/check.sh`
   are never weakened; a step that needs a new read path adds it to their closed lists as a reviewed change.
2. The only credential stays one Alpaca paper key pair, used only to read market data. No step adds a live key, a
   brokerage login or a second data vendor's key unless the owner decides so first
   ([Open questions](#open-questions-for-the-owner)).
3. Standard-library Python only under `scripts/`, `hooks/` and `tests/`, on the Python 3.9 syntax floor; money is
   `Decimal`, prices and Greeks are `float`.
4. Tests first: every behaviour lands with its tests, and `scripts/test_count_floor.txt` only moves up.
5. Every number a Bot quotes comes from a tool, with its source, data delay and UTC time. Anything that cannot be
   fetched or computed reliably is UNAVAILABLE, never a quiet negative and never a fact from memory.
6. The desk ships no trading strategy of its own and makes no return claims. A review, a replay or a journal reports
   what happened to your rules and your positions; it never presents a rule's results as a return.
7. One file, one writer; owner-authority actions still need your tap on an Ask-first card.
8. Counsel signs the wording of every new owner-facing text before a tag and before any public release
   ([RELEASE.md](RELEASE.md), step 4).

## Seats

A Grok Bot group holds two to six Bots. Stage 1 uses four of the six seats of the "Aegis Options Floor".

| Seat | Stage | Where it sits | Owns (planned) |
| --- | --- | --- | --- |
| Aegis Book & Risk Analyst | 2 | the Floor (seat 5) | a live view of marks, Greeks and limits; exposures, scenario and event stress; volatility context |
| Aegis Events and Calendar Analyst | 2 | the Floor (seat 6) | sourced earnings and ex-dividend dates; the decision calendar |
| Aegis Rules Steward | 3 | direct message only (`seat: off-floor`) | rulebook change control, which the Desk Lead holds until then |
| Aegis Review Desk | 3 | direct message only (`seat: off-floor`) | the weekly review of the signal journal |

Stage 2 fills the Floor. The charter already places the Stage 3 seats on direct message, and once Stage 2 has filled
the Floor that is the only place left; the agent frontmatter's `seat: floor|off-floor` field already allows it. Every
new seat lands as an agent file, a generated role skill (`python3 scripts/gen_role_skills.py`), its skills, `SETUP.md`
steps, the template hashes and the charter's seat table, all in one pull request. This page puts each seat after the
tools it owns, so that the seat's first answer has a tool behind it.

## Stage 2

Stage 2 makes the numbers the desk already shows more complete (American pricing, exposures, stress, volatility
context, sourced dates) and widens the Structuring Desk's scope.

| Step | What lands | Needs | Status |
| --- | --- | --- | --- |
| 2.1 | The American early-exercise lattice, engine only | nothing | landed, not wired |
| 2.2 | Discrete cash dividends in the lattice, engine only (proposed here) | 2.1 | dropped by your decision (open question 3) |
| 2.3 | The lattice wired into the book's Greeks and implied volatility, the structuring prices, and an early-exercise line beside the expiry tiers | 2.1; your decisions on the model of record, the boundary band and the dividend schedule (answered) | landed |
| 2.4 | The Structuring Desk's wider scope: roll analyses and replacements of long puts, short puts, collars and verticals | your decision on a vertical's choices and their approval (answered) | landed |
| 2.5 | Weekly expirations for the Structuring Desk | your decision on the chain budget (answered) | landed |
| 2.6 | A cash-balance check and per-account approval levels | your decision on stated cash (answered) | landed |
| 2.7 | Exposures and limits: beta-weighted delta and two limit metrics in the exit rulebook | your decision on the limits (answered) | landed |
| 2.8 | Scenario and event stress | nothing; after 2.3, by your choice | landed: the tool; its routines are 2.10 |
| 2.9 | Volatility context | nothing | landed: the tool; its seat is 2.10 |
| 2.10 | The Aegis Book & Risk Analyst seat | 2.7, 2.8, 2.9 | planned |
| 2.11 | Sourced events and the decision calendar | your decision on a data source (answered) | planned |
| 2.12 | The Aegis Events and Calendar Analyst seat | 2.11 | planned |

### 2.1 The American early-exercise lattice (landed, not wired)

- **What landed.** `scripts/aegis_desk/lattice.py`: a Cox-Ross-Rubinstein binomial tree with the continuous dividend
  yield the rest of the engine uses. For one option it reports the American value (with the closed form as a control
  variate), the early-exercise premium, delta, gamma and theta, and two flags. `exercise_now` is true only when the
  root and every node of the tree's first two steps exercise, so the spot is at least two node spacings inside the
  tree's exercise region. `near_boundary` is true when those six nodes disagree: there the tree cannot tell exercise
  from holding, and its Greeks are coarse.
- **What it proves.** `tests/engine/test_lattice.py` pins the American put Hull prints in *Options, Futures, and
  Other Derivatives* (4.49 at 5 steps; 4.263, 4.272, 4.278 and 4.283 at 30, 50, 100 and 500), the exact tree
  properties, the no-arbitrage bounds, agreement with `carry.early_exercise_gap`, and the boundary band: spots where
  the default tree's root exercises but a finer tree holds are never `exercise_now`, and the exercise signal next to
  the band is confirmed by a ten-times finer tree. The QuantLib oracle compares it with QuantLib's finite-difference
  American engine on a 480-point grid and on 729 spots placed across the exercise boundary of 24 contracts. Measured
  there and on a wider sweep: `exercise_now` never true where QuantLib holds (1411 cases); price within 1.5e-3 of
  `max(price, 1)` everywhere; away from the band, delta within 1.4e-3 and gamma within 9.4e-4 per $1^2; inside the
  band, delta off by up to 0.028 and gamma by up to 0.33.
- **What it rewrote.** Every line that said the lattice, or an early-exercise model, was planned for Stage 2 now says
  what is still planned: American pricing, American Greeks, or early-exercise analysis beside the expiry tiers. Lines
  that name the lattice also say that its engine is in the tree; the structuring model note says the same. A test in
  `tests/engine/test_lattice.py` fails if any sentence in the repository says again that the lattice, or an
  early-exercise model, engine or tree, is planned.
- **What it does not change.** No tool, routine, `book.json` field or structuring answer reads it, so every answer
  is still European. The lines that say early exercise is not modelled, that the tiers are European-only, or that
  American pricing, American Greeks or early-exercise analysis are planned stay true until 2.3.

### 2.2 Discrete cash dividends in the lattice (dropped by your decision)

- **What it would have been.** An optional schedule of cash dividends in `lattice.american`, so that a model line could
  see the exercise the evening before an ex-date. No line in the tree planned it; this page proposed it.
- **Why it is dropped (open question 3).** The dividend tier of `assignment_watch.py` already tests that risk from the
  quoted extrinsic value on the bid, with no model, and it stays exactly as it is. Prices and Greeks keep the
  continuous yield, made a sourced fact (2.3), and the two blind spots of a yield are closed without a second model:
  a dividend note on every leg whose life spans an ex-date, and the long put's wait line. So no model-based line reads
  a cash dividend, and no event stress models a jump the size of the dividend (2.8).
- **Revisit if** you later want a line that says a short call is likely to be assigned before an ex-date. That needs
  this step and a calendar with a list of ex-dates, together, as a new decision.

### 2.3 The lattice wired in (landed)

- **What landed first: the leg pricer, not wired.** `scripts/aegis_desk/american.py` prices one leg on the model of
  record: the lattice's price, delta, gamma and theta in desk units, vega and rho as central differences of the
  lattice price, the early-exercise premium, the band rule below (`lattice.BAND_DELTA_TOL`), and an implied volatility
  that inverts the lattice price from the European one as its starting bracket (bounds: the undiscounted intrinsic
  value below, the spot or the strike above). `tests/engine/test_american.py` and the QuantLib oracle check it (band
  delta within the tolerance; vega and rho within 6e-3 of QuantLib's differences; the solved volatility repricing
  QuantLib's price within the price bound). No tool reads it yet.
- **What landed next: the sourced yield.** `research/calendar.toml` schema version 2 (the yield inside the
  `ex_dividend` entry, a top-level `dividend_yield` refused, version 1 still loaded), `calendar_events.sourced_yield`,
  the `NO_YIELD` finding of `calendar_check.py` and the `yield` of the `desk_init.py` intake. The book and the
  structuring tools still read whichever yield the file holds until they are wired.
- **What landed third: the book.** The book's IV and Greeks come from the leg pricer, with `model`,
  `early_exercise_premium`, `greeks_quality`, `exercise_now` and `near_boundary` per leg, per-Greek net Greeks with a
  group `greeks_quality`, `dividend_unknown` for a leg without a sourced yield, the exit rules' band guard on
  `abs_delta`, trigger levels on the lattice delta, and `vol_context` on American days only. Still to land: the
  structuring prices, the early-exercise lines with the yield note and the put wait line, the journeys and the
  doctor's American check.
- **What landed last: the structuring prices and the early-exercise line.** The Structuring Desk prices every
  candidate on the leg pricer and answers UNAVAILABLE `dividend_unknown` without a sourced yield; its parity-carry
  estimate alone keeps European volatilities, the input of its own early-exercise bound. The long put's
  `american-style note:` is retired, a short call's dividend note and a short put's note read `early-assignment
  note:`, and the `MODEL_LINE` and `MODEL_NOTE` name the American model. `aegis_desk.early_exercise` gives
  `assignment_watch.py` its early-exercise line under each listed leg (and lists a leg no tier lists when the lattice
  flags it), the dividend note and the long put's wait line; it adds lines only, never a tier change or an exit code.
  One reading of your answer: at a volatility implied from a mid above the intrinsic value, the tree holds at its
  root, so a quoted deep in-the-money put reads `near_boundary` rather than `exercise_now`; the wait line therefore
  applies to a long put the lattice flags either way. The doctor's selftest gains the American check (Hull's five-step
  put and an American volatility round trip), and four journeys on the new root `tests/fixtures/desk_american` (the
  evening before an ex-date) and the main root show the tier that stays HIGH, the wait line, the early-assignment
  warning, the band in the book, the band guard of the exit rules and `dividend_unknown`. Every line this step made
  untrue is rewritten.
- **What lands.** The book's Greeks and implied volatility and the Structuring Desk's prices move to the lattice.
  `book.json` names the model each leg was priced with, and `docs/TOOLS.md` is regenerated. The structuring
  `american-style note:`, the `MODEL_LINE` and the `MODEL_NOTE` change with it, and counsel signs the new wording.
- **The expiry tiers do not move.** `assignment_watch.py` has two tiers, and neither reads a model price. The
  dividend tier compares a short call's quoted extrinsic value on the bid (`bid - max(0, spot - strike)`) with the
  cash dividend you stated, within five trading days of the ex-date (`expiry.dividend_tier`); the pin tier compares
  spot with the strike on the last two trading days. Both stay exactly as they are. What 2.3 adds is an
  early-exercise line beside them, per leg, from the lattice. It may add a warning; it never removes, downgrades or
  replaces a tier.
- **Why the dividend tier must stay.** On a continuous yield the lattice spreads a dividend over the year, so it
  cannot see the exercise the evening before an ex-date. A call at spot 140, strike 100, 30 days, r = 4.5 %, q =
  1.5 %, sigma = 25 % comes out as a hold (`exercise_now` and `near_boundary` both false; price 40.197, so the model
  extrinsic is about 0.20). With a bid at that value, an ex-date tomorrow and a stated $0.235 dividend, the dividend
  tier reads HIGH (an extrinsic of 0.20 is below the dividend). Such deep in-the-money short calls are the legs most
  likely to be assigned. An early-exercise line that read a dividend would need step 2.2, which is dropped, so the
  yield note says the line does not see the cash dividend, and the dividend tier stays the test for that risk.
- **The boundary band, read by side.** For a long leg, `exercise_now` gives an exercise note and `near_boundary` a
  line that says the leg is near the early-exercise boundary, never an exercise signal and never a hold signal. For a
  short leg, where a missed warning is the costly error, `exercise_now` and `near_boundary` both give an
  early-assignment warning.
- **Proposed here: one tree.** `carry.early_exercise_gap` runs its own tree (`carry._tree`, `carry.TREE_STEPS`) inside
  every structuring answer. Making it call `lattice.american` would give the desk one tree; `CarryAgreementTest`
  holds the two within 1e-9 until then. No line in the tree plans this, so it is optional.
- **The model of record (your answer to open question 2).** Everything switches to American, at
  `lattice.DEFAULT_STEPS` (200), and each leg shows one value; no European value is printed beside it.
  - Book IV is solved by inverting the lattice price from the mid; a mid below intrinsic has no solution, so that leg
    is UNAVAILABLE. Delta, gamma and theta come from the lattice; vega and rho are central bumps of the same lattice
    price (sigma ± 0.01 per vol point, r ± 0.01 per 1 %). The structuring prices, IV and `abs_delta` band move with it.
  - `book.json` gains two fields per leg, `model = "american_crr_200"` and `early_exercise_premium` (American minus
    European, from the same tree). Neither appears in the text.
  - One early-exercise line per leg, beside the two expiry tiers, read by side as above, with the dividend note and
    the put wait line below.
  - The long-put `american-style note:` is retired: its only job was to warn that a European price can understate an
    American put. `structuring.short_put_caveat` stays with its tests: it warns at |delta| 0.80, the lattice flags
    start near |delta| 0.95, and for a short leg a missed warning is the costly error.
  - `vol_context` compares only days priced with the same `model`; a snapshot without the field counts as
    `european_bsm`, so the context is UNAVAILABLE until 5 American days are recorded.
  - Exit rules that read `abs_delta` read American delta from the day this step lands; the rulebook does not change,
    and `rules_replay` (3.2) replays each day's recorded delta, never recomputing it.
- **The boundary band (your answer to open question 2).** For a leg with `near_boundary` true:
  - the price as computed (its measured bound, 1.5e-3 of `max(price, 1)`, holds in the band too);
  - delta shown with the ASCII mark `approx` in the text and `"greeks_quality": "near_boundary"` in JSON, within
    `lattice.BAND_DELTA_TOL = 0.05`. That is a bound the QuantLib oracle tests (about 1.8 times the measured worst,
    0.028), not a proof; if an oracle run ever exceeds it the gate fails, and band delta becomes UNAVAILABLE until you
    decide again;
  - gamma and theta UNAVAILABLE (the measured band gamma error reaches 0.33 per $1^2); vega and rho as computed;
  - no repricing pass with more steps.
  - When an `abs_delta` condition's threshold lies within `BAND_DELTA_TOL` of a band delta, that observation reads
    UNAVAILABLE; any other threshold is decided as usual.
  - Net Greeks become per-Greek: a group's net delta includes the approximate delta and is marked `approx`, while net
    gamma and net theta are `None` for that group. Today one missing Greek blanks the group's whole `net_greeks`.
- **The dividend schedule (your answer to open question 3).** Prices and Greeks keep the continuous yield, and the
  yield becomes a sourced, dated fact; 2.2 is dropped.
  - The yield moves into the symbol's `ex_dividend` entry in `research/calendar.toml` (schema version 2), sharing its
    `source`, `checked_utc` and STALE check: `ex_dividend = { date = ..., amount = ..., yield = 0.0090, status =
    "confirmed", source = ..., checked_utc = ... }`. `yield` is a fraction in [0, 1), allowed with status
    `confirmed`, `estimated` or `unknown`; status `none` takes no yield and means q = 0. Version 2 refuses a top-level
    `dividend_yield`; the reader still loads version 1, and a bare version-1 yield counts as unknown.
  - An unknown yield is never zero: with no usable yield and a status other than `none`, the leg's IV and Greeks are
    UNAVAILABLE with `iv_status = "dividend_unknown"`, replacing today's q = 0 warning in the book and the structuring
    tools. `calendar_events.freshness` gains the finding `NO_YIELD` for every held symbol, and `desk_init.py` asks for
    the yield with the ex-dividend date.
  - The yield note, on any leg, call or put, whose life contains the known ex-date, whose dividend status is
    `unknown`, or whose last trading day is 80 or more calendar days after the known ex-date: the dividend is priced as
    a continuous yield, so the drop on the ex-date is not seen here. For calls the dividend tier stays the test.
  - The put wait line: for a long put whose lattice line says `exercise_now` and whose life contains a known ex-date
    with a stated cash amount D, when D > K × (e^{r·d} − 1) (K the strike, r the desk rate, d the years to the
    ex-date), the exercise note gives way to a line that says exercise is not flagged because the stated dividend
    exceeds the interest on the strike until then. Holding just past the ex-date is worth at least
    e^{−rd}(K + D) − S against K − S now, so the test needs no model. With no stated amount the exercise note keeps
    the yield note. Short puts keep their early-assignment warning.
  - **Before this step lands** you restate each held dividend payer's yield inside its `ex_dividend` entry, or set
    its status to `none`; `calendar_check` lists every gap as `NO_YIELD`.
- **Gate.** The command journeys and the structuring goldens re-pinned with the lattice; every dividend-tier and
  pin-tier result identical before and after the step; a journey for the deep in-the-money short call above, whose
  dividend tier stays HIGH; journeys for a long leg and a short leg in the band, a band leg with `approx` delta,
  UNAVAILABLE gamma and theta and a portfolio net delta still present, and an `abs_delta` rule within 0.05 of a band
  delta reading UNAVAILABLE; the oracle asserting band delta within `BAND_DELTA_TOL` and every price within its
  relative bound; a `dividend_unknown` journey; a journey for a long put the evening before an ex-date (the wait
  line); the doctor's selftest gains an American check; every "early exercise is not modelled", "European-only in
  Stage 1", "American pricing is planned", "American Greeks are planned" and "early-exercise analysis is planned"
  line rewritten; counsel signs the new `MODEL_LINE`, `MODEL_NOTE`, early-exercise, yield-note and wait-line wording.

### 2.4 The Structuring Desk's wider scope

- **What landed.** The roll analysis of a protective put's long put: `roll_analysis.py` ranks every put of a later
  standard monthly inside `[protective_put] dte` under that section's criteria and objective, opens the new put
  first (S7) and reads each candidate's net cost per day added. It reuses the pricing and labels of the ROLL ticket
  for a long put, so it needed no new criteria and no new owner-facing words beyond the header, the legs and the held
  put's close.
- **What landed next.** A cash-secured put's short put: its roll analysis and a ROLL ticket for the put the owner
  names, both judged under `[roll]` as a short call's roll is (the bands, the minimum credit and the roll test, with a
  put's time value; `strike_floor` and `ex_dividend` are call rules and are not read). The held put closes at its ask
  first, then the new one opens at its bid, and a note gives the cash each put secures. With it, the short-put
  early-assignment note this step requires: `structuring.short_put_caveat` on the long put's two tests, printed as
  `early-assignment note:` whenever a short put carries it.
- **Then a collar's two legs as a unit.** Its roll analysis ranks new pairs of one later standard monthly under
  `[collar]` (the floor and cap bands, the new collar's own net debit against `max_net_debit`, the strike floor, the
  call's events beyond the held call's life) and adds the roll's net per share; its ROLL ticket takes
  `--new-call-strike` beside `--new-strike`. The four legs open the new put, close the held put, close the held call
  and open the new call, so the shares stay protected and covered at every step. The answer carries the held call
  as `held_call` beside `held`.
- **Last, a vertical's roll (your answer to open question 9).** A vertical's ROLL ticket moves both legs to a later
  expiration at the same width (`--new-strike` names the new short leg). Its four legs open the new long, close the
  held short, close the held long and open the new short. The approval convention now counts a long call of the same
  expiry as a short call's cover, a defined-risk spread at level 3 (`approval.STEP_OPEN_SPREAD_CALL`,
  `Holdings.spread_calls`), so the new short call is not an uncovered one. No `structuring.toml` section judges a
  vertical, so the ticket gives the new vertical's width and net and the roll's net in place of checks, and a
  vertical has no roll analysis.
- **The landed parts needed nothing, with one new caveat.** The tree names no prerequisite. A long put priced on the European model
  already carries the `american-style note:` (`_put_caveat` in `scripts/aegis_desk/structuring.py`: an absolute delta
  of 0.80 or more, or in the money with a time value below the interest on its strike). That caveat covers long puts
  only. A short put's risk is early assignment against you, and no caveat covers it today, so this step adds a
  short-put early-assignment note on the same two tests before any short-put text ships on European prices. If 2.4
  lands before 2.3, 2.3 re-pins its goldens with the others.
- **Still never.** No new symbol, no uncovered short option, no cash-secured put proposed, no order field.
- **Gate.** Fixture chains for each structure, goldens, journeys, the short-put note on a deep in-the-money short
  put and its absence on an out-of-the-money one, the alert lint's structuring mode over every new text, the QuantLib
  oracle over the new best legs, and counsel's sign-off.

### 2.5 Weekly expirations (landed)

- **What landed.** `structuring_config` reads `expirations = "monthly" | "weekly"` in every structure section
  (schema version 2, default `monthly`) and refuses a `weekly` section whose `dte` spans more than 21 days;
  `structuring.is_week_end` and `candidate_expirations(weekly=True)` add the week-end expirations; the notes print
  `expirations: weekly and monthly in your N to M day window`; `desk_init.py` takes the key. Tests cover a holiday
  Friday's Thursday weekly, the 21-day refusal, the dailies left out and, over every window of 2026 and 2027, at most
  four week-ends (the four-chain cap holds; a chain past the cap still answers UNAVAILABLE).
- **What lands.** The structuring tools list weekly expirations as well as the standard monthly ones.
- **The chain budget (your answer to open question 8): narrow the window, keep the cap.** One chain is one
  expiration, and a run may fetch at most 4 chains and 40 requests (`PER_RUN_MAX_CHAINS`, `PER_RUN_MAX_REQUESTS` in
  `scripts/aegis_desk/quote_cache.py`); neither changes.
  - Each structure section of `structuring.toml` gets an optional schema-version-2 key `expirations = "monthly" |
    "weekly"` (in `V2_KEYS`), default `"monthly"`.
  - "Weekly" means a listed expiration on the last trading day of its week: Friday, or Thursday when Friday is a
    holiday in the holiday table. Standard monthlies count, because they also end a week. Daily expirations are not
    listed.
  - With `"weekly"`, the reader requires `hi - lo <= 21` in `dte`, beside `7 <= lo < hi <= 400`. Five consecutive
    week-ends span at least 27 days, so the window holds at most four expirations and the cap holds by arithmetic. A
    chain the run cannot get still makes the answer UNAVAILABLE.
  - The text names the mode, for example `expirations: weekly and monthly in your 30 to 51 day window`.
- **Gate.** Fixture chains that include weeklies; the reader refusing a `weekly` section with `dte = [40, 75]`; a week
  whose Friday is a holiday, so the weekly falls on Thursday; a listing that would still exceed 4 chains answering
  UNAVAILABLE.

### 2.6 A cash-balance check and per-account approval levels (landed)

- **Constraint.** The desk has no brokerage API and does not see your cash. A cash figure can only be one you state,
  per account, in `desk.toml`, with its date, and acknowledged like every other owner value.
- **What landed.** `desk_config` reads an optional `approval_level`, `cash` and `cash_as_of` on each
  `[[owner.accounts]]` entry (`OwnerSection.level_for`, `.account`); the structuring tools read the account's own
  level; `aegis_desk.cash_check` computes the peak cash out over a ticket's or proposal's legs in order (short-put
  collateral included) and compares it with the stated cash, UNAVAILABLE when it is absent, dated ahead or older than
  `CASH_MAX_AGE_TRADING_DAYS` (5); tickets print it under the cash block (and carry `cash.check` in JSON), proposals
  after the approval block; a shortfall is a failed item, never a refusal. `engagement`, `chatty_or_quiet` and
  `attention_thresholds` are optional and read by nothing; `desk_init.py` no longer requires them and takes the new
  account keys.
- **What lands.** An `approval_level` on each `[[owner.accounts]]` entry (each already has a `name` and a `type`),
  read before the desk-wide `[owner] approval_level`, and a cash check on every ticket and proposal.
- **Stated cash (your answer to open question 5).** Two optional keys per account, given together or not at all:
  `cash` (free cash not already securing open short puts; `Decimal >= 0`) and `cash_as_of` (a TOML local date).
  - The check compares the peak cash out while the ticket's legs are worked in the desk's fixed order: the maximum
    over steps k of the sum over legs 1..k of (buy at ask − sell at bid) × 100 × contracts, plus strike × 100 ×
    contracts secured by a short put opened, less that released by one closed. Credit rolls included: the WMT roll's
    leg 1 pays $700 before leg 2's credit arrives, so the need is $700, not the net credit of $10.
  - UNAVAILABLE when the keys are absent, a leg lacks a usable quote on the side it needs, `cash_as_of` is in the
    future, or it is more than `CASH_MAX_AGE_TRADING_DAYS = 5` trading days before today's ET date (by the holiday
    table). Otherwise the line states both figures and the age; a shortfall is a red check item, never a REFUSED.
  - A margin account is compared with the stated cash only; the desk does not model margin, and never infers cash
    from `positions.csv`.
- **The recorded values retired here (open question 7).** This pull request already changes `desk_config.py`, the
  intake and the acknowledgement set, so it also makes `attention_thresholds`, `chatty_or_quiet` and `engagement` optional
  (see [Values recorded for later stages](#values-recorded-for-later-stages)).
- **Gate.** The approval-level check's tests for each account; the peak-cash goldens, the WMT credit roll among them;
  every UNAVAILABLE case; the `desk_init.py` intake; the acknowledgement set; counsel's sign-off on the cash line.

### 2.7 Exposures and limits (landed)

- **What landed.** `aegis_desk.beta` fits each symbol's OLS beta against SPY on split-adjusted daily closes (the 252
  closes ending the previous trading day; UNAVAILABLE under 126 paired returns or past the 40 % guard; `r2_flag` `ok`
  at r2 >= 0.30, else `low`). `beta_refresh.py`, a new read-only tool, fetches those bars through the quote cache with
  `history(..., adjustment="split")` (the default stays `"raw"`) and writes the day's fits to `data/beta/<date>.json`;
  a second run the same day reads the cache. The book reads that file, so a book run still sends only its quotes
  batch: each underlying carries `net_delta`, `beta` and `beta_r2_flag`, and `beta_weighted_delta` is filled in dollars
  with its `r2_flag` (an underlying with no net delta needs no beta; the portfolio line prints the figure when there is
  one). The exit rulebook gains `beta_weighted_delta_pct` (portfolio) and `delta_exposure_pct` (underlying), and
  `rules/examples/limits-1.0.0.toml` holds `BWD` and `UDELTA`, both `owner_must_set`. The fixture desk's regular set
  carries split-adjusted bars (NVDA fits 2.04 with r2 0.50, ok; WMT 0.53 with r2 0.23, low).
- **What lands.** `beta_weighted_delta`, today always `{"value": null, "r2_flag": "unavailable"}` in `book.json`,
  filled in dollars with its `r2_flag`, and two new rule metrics.
- **The limits (your answer to open question 4): two metrics in the exit rulebook.** Limits live where `CONC` and
  `DAILY` already live, as `role = "portfolio"` or `"underlying"` rules adopted with `rules_adopt.py`, your typed
  phrase and your tap, with `WATCH` verdicts and `plan = "hold_review"`. `desk.toml` gets no `[limits]` section and no
  new routine (`midday-limits` is dropped; `book_check` already evaluates the rulebook four times a day).
  - `beta_weighted_delta_pct` (portfolio): the sum over underlyings of net delta shares × spot × beta, over
    `declared_equity`, × 100. `delta_exposure_pct` (underlying, `any_underlying`): net delta shares × spot over
    `declared_equity`, × 100. Either is UNAVAILABLE when an open leg in its scope lacks a delta or its beta is
    UNAVAILABLE.
  - Beta: OLS of daily simple returns against SPY over the 252 closes ending the previous trading day, on daily bars
    from `history` with a new keyword `adjustment="split"` used only here (the default stays `"raw"`; the same GET
    path, its new parameter reviewed with its tests). UNAVAILABLE when there are fewer than 126 returns or any daily
    return in either series lies beyond ±40 %. `r2_flag` is `"ok"` at r² ≥ 0.30, else `"low"`, printed with the
    message, beside a source line naming the feed. Closed bars are fetched once a day.
  - New example rules in `rules/examples/`, each `owner_must_set = true`: `BWD` (`beta_weighted_delta_pct >= 100`)
    and `UDELTA` (`delta_exposure_pct >= 20` on `any_underlying`); a net-short book adds a mirrored `<=` rule.
  - Gamma, vega and theta limits and a P/L limit beyond `DAILY` are out for now; each can be added later as one more
    rule metric.
- **Gate.** Beta goldens on fixture bars; a fixture with a 10:1 split where raw bars trip the ±40 % guard and
  split-adjusted bars fit; UNAVAILABLE when bars are missing; the example rules passing the lint; counsel's sign-off on
  the two metric names, the beta source line and the WATCH wording.

### 2.8 Scenario and event stress (landed: the tool)

- **What landed.** `aegis_desk.scenario` and `scripts/scenario.py`. The book is marked once, as `book.py` marks it,
  and every open group is re-priced through `book.leg_price`, the book's own leg pricer (the American lattice).
  - **The grid (proposed here).** Spot moves of -10, -5, -2, 0, +2, +5 and +10 % crossed with volatility moves of -5,
    0 and +5 points (never below 1 %): 21 cells. Counsel signs its labels.
  - **A zero move equals the book.** Each change is measured between two model prices, so the stressed P/L at a zero
    move is the book's `unrealized_pl_mid` exactly.
  - **Exclusions.** A group the book marks unavailable, a leg without implied volatility, or an unknown dividend
    yield excludes its group, and the portfolio total is then empty.
  - **Event stresses.** `--event earnings|ex_dividend --symbol SYM` prices the close of the first trading day after
    the date. A leg that expires first is valued at its intrinsic value at the moved spot, spot is never cut by the
    dividend, and the dividend tier is named when a short call is in scope.
  - **A hedge through an event.** Each group's change is split into stock and options.
  - **The routines.** `pre-event-stress` (15:05 ET each trading weekday) and `weekend-stress` (Saturday 10:00 ET) are
    proposed here. They add 6 runs a week (lean 47, active 67, pinned in `tests/engine/test_routines.py`), and their
    ids already fit the Auto-review text. They post in the Aegis Book & Risk Analyst's chat, so they land with that
    seat (2.10).
- **What lands.** A scenario tool (not yet in the tree) that re-prices the book under spot and volatility moves, and an
  event stress of a position through an earnings or ex-dividend date, including a hedge through an event. Every price
  comes from the engine; a scenario is labelled as a computation, not a forecast.
- **Order.** After 2.3, by your choice. The scenario tool picks no model of its own: it calls the
  leg-pricing function the book uses, so a stressed price always uses the book's model of record.
- **Event stresses on that model.** An earnings stress applies the grid's spot and volatility moves at a horizon of
  one trading day after the earnings date. An ex-dividend stress prices the book one trading day after the ex-date,
  with spot moved only by the grid: spot is never cut by the cash amount, because the continuous yield already lowers
  the forward and a cut would count the dividend twice. Its line names the dividend tier as the test for early
  assignment. No stress models a jump the size of the dividend (2.2 is dropped).
- **Later decision.** The 2.8 pull request proposes the scenario grid; counsel signs its labels.
- **Gate.** Re-pricing goldens; the stressed book equal to the book at a zero move; the planned `pre-event-stress` and
  `weekend-stress` routines within the budget.

### 2.9 Volatility context

- **What landed.** `scripts/vol_context.py` and `aegis_desk.vol_context`. The record is the desk's own end-of-day
  book snapshots, which already hold each leg's implied volatility: a symbol's value for a day is the median of its
  open option legs with a solved volatility (`iv_status` ok), never the vendor's Greeks. The context places the
  latest day's value among the earlier recorded days (how many were lower, the range, since when); fewer than 5
  earlier days, or no value on the latest day, is UNAVAILABLE. A separate `iv_snapshot` file was not needed. The
  skill `aegis-vol-context` and the seat that relays the context come with 2.10.
- **Constraint.** Volatility context never changes a verdict.
- **Gate.** `tests/engine/test_vol_context.py`: no module but the tool (and the doc generator) imports the context,
  so no verdict can read it; the history comes from the book snapshots only; the tool writes nothing.

### 2.10 The Aegis Book & Risk Analyst seat

- **What lands.** The fifth Floor seat: its agent, role skill and skills (`aegis-exposures-limits-and-stress` and
  `aegis-vol-context`), owning the live view of marks, Greeks and limits from 2.7 to 2.9, and the planned
  `opening-marks` routine. The Desk Lead's "planned for Stage 2" line for it is rewritten.
- **Gate.** All ten steps of `check.sh` with five agents; the template claiming the new skills; the bootstrap and
  `SETUP.md` steps for the new Bot with readbacks.

### 2.11 Sourced events and the decision calendar (landed: the tools)

- **What landed.**
  - **The read path.** `alpaca_client.cash_dividends`, on the eighth allow-listed path `/v1/corporate-actions`
    (`types=cash_dividend`, the market-data host, the same paper key). It keeps the ex-dates of the window and drops
    a record without an ex-date. `SECURITY.md` lists the path.
  - **`dividend_source.py`.** It compares the declarations with `research/calendar.toml` (AGREES, NEW, DIFFERS,
    NONE FOUND) and proposes the new or different ones as `sourced_dividends` answers.
  - **The owner's tap.** `desk_init.py --replace --owner-tapped` rewrites only those `ex_dividend` lines, with status
    `confirmed`, `source = "Alpaca corporate actions (cash_dividend)"` and the fetch time as `checked_utc`. A stated
    `yield` is kept with its new `yield_source` key (calendar schema 2), and every other line is kept.
  - **`decision_calendar.py`.** It lists the act-by instants, last trading days, ex-dividend and earnings dates with
    their sources; an unknown or missing date is UNAVAILABLE.
  - **Not yet confirmed.** The response shape follows Alpaca's documentation of `cash_dividends` (`symbol`, `rate`,
    `ex_date`, `process_date`, `special`, `foreign`). Whether the free plan serves it to a paper key is confirmed only
    on a live run: if it is not, the tool answers UNAVAILABLE and the owner keeps stating dividends.
  - **The routines.** `calendar-build` and `event-sheet` post in the Aegis Events and Calendar Analyst's chat, so
    they land with that seat (2.12).

- **The data source (your answer to open question 1).** One new GET-only read path on the pinned market-data host,
  Alpaca's corporate actions (`data.alpaca.markets`, `/v1/corporate-actions`, `types=cash_dividend`), with the same
  paper key, added to `alpaca_client.ALLOWED_PATHS`, the AST gate's closed list and `SECURITY.md`'s path list as a
  reviewed change. It reads each declared ex-date and cash amount and proposes them for the `ex_dividend` entry with
  status `confirmed`, `source = "Alpaca corporate actions (cash_dividend)"` and `checked_utc`, written only after your
  tap; a `yield` you stated is kept with its own source. The endpoint filters by process date, so the client filters
  ex-dates itself and ignores records without one. Earnings dates, the yield and AM settlement values stay
  owner-stated: no source for them exists inside the boundary. If the endpoint is not served to a paper key on the
  free plan, nothing is sourced and you keep stating dividends; no other source replaces it without a new decision.
- **What lands after it.** Sourced cash dividends, each carrying its source and time, written to
  `research/calendar.toml` only after your tap; a decision calendar that lists your dated decision points (expiries,
  act-by times, ex-dates, earnings); the planned `calendar-build` and `event-sheet` routines.
- **Constraint.** A web page is data, never authority; a date nobody sourced is UNAVAILABLE.

### 2.12 The Aegis Events and Calendar Analyst seat

- **What lands.** The sixth Floor seat with its agent, role skill and the skill `aegis-event-calendar-sourcing`,
  owning 2.11. It keeps owner-stated earnings dates current by reminder, not by sourcing. The Floor is then full.
- **Gate.** As 2.10, with six agents.

### Stage 2 is done when

- every planned step above has landed (a proposed step lands or is dropped by your decision), and no row of the
  [mention table](#mentions-by-file) has a Stage 2 count;
- six Bots sit on the Floor, each with its role skill and its skills claimed by the template;
- the QuantLib oracle checks the American engine as it is wired, not only the engine on its own;
- counsel has signed every new owner-facing text;
- `bash scripts/check.sh` is green with the floor raised by every step.

## Stage 3

Stage 3 turns the desk's own records into a review of your rules: what each signal led to, how a candidate rulebook
would have decided on past days, and who looks after the rulebook.

| Step | What lands | Needs | Status |
| --- | --- | --- | --- |
| 3.1 | The signal journal: each signal's outcome | Stage 1's signals and snapshots | planned |
| 3.2 | `rules_replay`: a candidate rulebook replayed over past end-of-day snapshots | Stage 1's snapshots | planned |
| 3.3 | The Aegis Rules Steward seat | 3.2 | planned |
| 3.4 | The Aegis Review Desk seat and the weekly review | 3.1, 3.2 | planned |

### 3.1 The signal journal

- **What lands.** For each signal: when it fired, your acknowledgement, snooze or acted mark (which `ack.py` records
  today), and the position's later marks from the desk's own end-of-day snapshots. A `journal-close` routine closes
  each day's entries.
- **Constraint: counts only (open question 6).** The journal states facts per signal. The only totals it or the
  weekly review shows are counts, in fixed words: signals per rule and overall with your acks, acted marks and snoozes (for
  example `TP50: 3 signals this week; 2 acknowledged, 1 marked acted, 0 snoozed`), and replay verdicts (`verdict
  differed from your adopted rule on 3 of 5 trading days`, with the days). No sum, average or difference of money
  across signals or replay days, no rate, no ranking of rules, no money figure on a replay verdict, and the book's P/L
  totals stay in the end-of-day summary. Counsel signs the per-signal line and the two count lines before 3.1 and 3.4
  tag; if counsel rejects counts, the review shows per-signal facts and per-day replay verdicts only.
- **Gate.** Journal goldens from fixture histories; a torn or missing snapshot is UNAVAILABLE for that day.

### 3.2 Rules replay

- **What lands.** `rules_replay` (not yet in the tree): a candidate rulebook evaluated over the end-of-day book
  snapshots the Exit Sentinel already writes, reporting for each day which verdicts would have differed from the
  adopted rulebook's, as `rules_diff.py` does for today.
- **Limits it states.** End-of-day snapshots only, so an intraday crossing is invisible; confirmations, re-arm and
  cooldown replayed from the recorded rule state; no hypothetical profit or loss.
- **Gate.** Replay of the adopted rulebook reproduces the recorded verdicts; goldens for the fixture candidate; no
  write to `rules/`.

### 3.3 The Aegis Rules Steward seat

- **What lands.** A direct-message seat that takes rulebook change control from the Desk Lead: the candidate-file
  interview, the adoption lint, the replay before adoption, and the planned `rules-sanity` and `rulebook-memo`
  routines. Adoption still needs your typed phrase `adopt rules X.Y.Z` and your tap.
- **Gate.** All ten steps of `check.sh` with the new agent off the Floor; every "the Desk Lead holds rulebook change
  control in Stage 1" line rewritten.

### 3.4 The Aegis Review Desk seat and the weekly review

- **What lands.** A direct-message seat that posts the weekly review from the journal and the replay, its skill
  `aegis-signal-journal-and-review`, and the planned `weekly-review` routine.
- **Constraint: counts only (open question 6).** As 3.1. The alert lint's review mode refuses a currency amount on any
  line that is not a per-signal fact line, and the phrases return, performance, win rate, hit rate, success rate,
  saved, avoided, outperform and total P/L. "Would have" is allowed: rule 6 lets a replay report what happened to your
  rules.
- **Gate.** Review goldens; the alert lint over the review text; counsel's sign-off.

### Stage 3 is done when

- every step above has landed, and no row of the [mention table](#mentions-by-file) has a Stage 3 count;
- the two direct-message seats run with their role skills;
- no review, replay or journal text presents a return.

## Planned routines

`tests/engine/test_routines.py` checks that the Auto-review allow text still fits fifteen routine ids: the six Stage
1 routines and nine planned ones. The planned roll-window routine was dropped (PR 3b decision S14), because the
Structuring Desk never posts alerts. Your answer to open question 7 dropped two more: `t1-call`, because the T-1
PREVIEW already runs in `final-call` (`expiry_checklist --tomorrow`, 15:20 ET), and `midday-limits`, because limits are exit rules that
`book_check` already evaluates (2.7). The test records only the names; this page places the nine by their names, which
is an inference, not a statement of the tree.

| Routine id | Step |
| --- | --- |
| `opening-marks` | 2.10 |
| `pre-event-stress`, `weekend-stress` | 2.8 |
| `calendar-build`, `event-sheet` | 2.11 |
| `journal-close` | 3.1 |
| `rules-sanity`, `rulebook-memo` | 3.3 |
| `weekly-review` | 3.4 |

Each new routine passes `routine_audit.py --budget` and `--rules-text` and posts only in its owning Bot's chat.

## Values recorded for later stages

`desk.toml` records four owner values that no tool reads. Your answer to open question 7 settled each:

| Value | Fate |
| --- | --- |
| `attention_thresholds` (`pl_day_pct`, `pl_trade_abs`) | retired: `DAILY` already states a day-loss line, and limits are rules (2.7) |
| `chatty_or_quiet` | retired: each routine's quiet rule and the `cadence_profile` already decide what is posted |
| `engagement` | retired: nothing needs it |
| `disclosures_acknowledged` | kept as a required record; no stage gates behaviour on it |

Retired means the key becomes optional in `desk_config.py`, so existing files still parse under the strict reader;
nothing reads it, `desk_init.py` no longer requires it (an answers file that still gives it is rendered as before, so
an existing desk keeps its hash) and the interview no longer asks for it, and the Desk Lead lines say so. Landed with
2.6. Counsel decides whether any later owner-facing text must check `disclosures_acknowledged` first.

## Not on the roadmap

- Placing, modifying or cancelling an order, in any stage.
- A live brokerage key, a brokerage login or reading a brokerage account.
- A trading strategy of the desk's own, a candidate symbol you do not hold, or a return claim.
- Alerts outside the Grok Bot chats.
- A roll-window routine (dropped in PR 3b), and the `t1-call` and `midday-limits` routines (dropped by your answer to open question 7).
- Discrete cash dividends in the lattice (2.2, dropped by your decision; revisit only with a list of ex-dates).
- A second data vendor, scraping, or a sourced earnings date (your answer to open question 1).

## Open questions for the owner

All nine are answered. The full reasoning behind answers 1 to 8 and the timing of 2.8 (after 2.3) was recorded with
the owner's acceptance on 2026-11-15; each step above carries the decision it needs.

1. **Event data (2.11). Answered:** one GET-only path for cash dividends on the pinned Alpaca data host with the same
   key; earnings dates, yields and settlement values stay owner-stated.
2. **The model of record and the band (2.3). Answered:** American everywhere, one value per leg, no European value
   printed; in the band delta is `approx` within `BAND_DELTA_TOL = 0.05`, gamma and theta UNAVAILABLE, nothing
   repriced.
3. **The dividend schedule (2.3). Answered:** the continuous yield, moved into the sourced `ex_dividend` entry; an
   unknown yield makes IV and Greeks UNAVAILABLE; a dividend note on every leg spanning an ex-date and the long put's
   wait line; 2.2 dropped.
4. **Limits (2.7). Answered:** two rule metrics in the exit rulebook (`beta_weighted_delta_pct`,
   `delta_exposure_pct`), beta on split-adjusted bars with a ±40 % guard; no `[limits]` section, no new routine.
5. **Cash (2.6). Answered:** optional `cash` and `cash_as_of` per account, checked at the peak cash out over the
   ticket's legs, UNAVAILABLE when older than 5 trading days.
6. **The journal (3.1). Answered:** counts only, in fixed words, with counsel.
7. **`t1-call` and the four recorded `desk.toml` values. Answered:** `t1-call` and `midday-limits` dropped; three
   values retired; `disclosures_acknowledged` kept as a record.
8. **The chain budget for weeklies (2.5). Answered:** narrow the window to at most 21 days when weeklies are on; the
   four-chain cap stays.
9. **A vertical's choices (2.4). Answered:** a ROLL ticket of both legs to a later expiration at the same width, and
   a long call of the same expiry counts as a short call's cover (a defined-risk spread, level 3). The convention's
   new step is counsel-signed text like the rest.

## Where each item is mentioned today

The ledger. The pull request that lands an item rewrites the lines its row points to. Role skills are generated from
the agent files: a line in `aegis-role-*` changes by editing `agents/<seat>.md` and running
`python3 scripts/gen_role_skills.py`. Some files are listed for an item without naming a stage (for example
`scripts/aegis_desk/carry.py` and `scripts/aegis_desk/bsm.py`); the [mention table](#mentions-by-file) below counts
only the files that do.

| Item | Step | Mentioned in |
| --- | --- | --- |
| American pricing: the desk's prices and Greeks, and early-exercise analysis beside the expiry tiers (landed) | 2.3 (2.1 rewrote the lines that named the lattice or an early-exercise model; 2.3 rewrote the rest) | `docs/ARCHITECTURE.md`, `docs/FAQ.md`, `docs/PROVENANCE.md`, `docs/STRUCTURING.md`, `skills/aegis-book-marking-and-greeks-honesty/SKILL.md`, `skills/aegis-expiry-and-assignment-protocol/SKILL.md`, `skills/aegis-covered-call-and-cash-secured-put-management/SKILL.md`, `skills/aegis-roll-decision-framework/SKILL.md`, `agents/position-control.md`, `skills/aegis-role-position-control/SKILL.md`, `scripts/aegis_desk/structuring.py` (`MODEL_NOTE`, `_put_caveat`), `scripts/aegis_desk/structuring_text.py` (`MODEL_LINE`), `scripts/aegis_desk/bsm.py` and `scripts/aegis_desk/iv.py` (module notes), `tests/engine/test_lattice.py` (the `MODEL_NOTE` assertion) |
| The parity-carry bound's own tree (proposed here) | 2.3 | `scripts/aegis_desk/carry.py` (`_tree`, `early_exercise_gap`, `TREE_STEPS`), `docs/STRUCTURING.md` (the carry of each expiration) |
| Roll analyses (a long put's, a short put's and a collar's landed; a vertical rolls by ticket) | 2.4 | `docs/ARCHITECTURE.md`, `docs/STRUCTURING.md`, `skills/aegis-roll-decision-framework/SKILL.md`, `agents/structuring-desk.md`, `skills/aegis-role-structuring-desk/SKILL.md`, `scripts/roll_analysis.py`, `scripts/aegis_desk/structuring.py` |
| Replacements of a short put, a collar and a vertical (landed) | 2.4 | `docs/STRUCTURING.md`, `tests/engine/test_structuring.py`, `skills/aegis-ticket-and-unwind-order/SKILL.md`, `skills/aegis-covered-call-and-cash-secured-put-management/SKILL.md`, `skills/aegis-protective-put-and-collar-management/SKILL.md`, `skills/aegis-vertical-spread-management/SKILL.md`, `scripts/aegis_desk/structuring.py` (the ROLL refusals, among them the collar refusal whose text is two adjacent literals) |
| Weekly expirations (landed) | 2.5 | `docs/ARCHITECTURE.md`, `docs/STRUCTURING.md` (overview, "Expirations", limits), `skills/aegis-roll-decision-framework/SKILL.md`, `scripts/aegis_desk/quote_cache.py` (`PER_RUN_MAX_CHAINS`) |
| Cash-balance check, per-account approval levels (landed) | 2.6 | `docs/ARCHITECTURE.md`, `docs/STRUCTURING.md` |
| Beta-weighted delta, exposures, limits and limit messages (landed) | 2.7 | `skills/aegis-book-marking-and-greeks-honesty/SKILL.md`, `skills/aegis-desk-charter/SKILL.md` (message table), `skills/README.md` (`aegis-exposures-limits-and-stress`), `scripts/aegis_desk/book.py` (the `beta_weighted_delta` placeholder), `agents/desk-lead.md`, `skills/aegis-role-desk-lead/SKILL.md` |
| Scenario and event stress, a hedge through an event (landed: the tool) | 2.8 | `docs/ARCHITECTURE.md`, `docs/STRUCTURING.md` (`scenario`), `skills/aegis-protective-put-and-collar-management/SKILL.md`, `skills/aegis-desk-charter/SKILL.md`, `skills/README.md` |
| Volatility context | 2.9 | `docs/ARCHITECTURE.md`, `docs/STRUCTURING.md` (`iv_snapshot`), `skills/README.md` (`aegis-vol-context`), `skills/aegis-book-marking-and-greeks-honesty/SKILL.md` |
| The Aegis Book & Risk Analyst | 2.10 | `skills/aegis-desk-charter/SKILL.md`, `agents/desk-lead.md`, `skills/aegis-role-desk-lead/SKILL.md`, `skills/aegis-desk-incident-playbooks/SKILL.md`, `skills/aegis-desk-bootstrap-and-onboarding/SKILL.md` (the Stage-2 seats), `docs/ARCHITECTURE.md` |
| Sourced earnings and ex-dividend dates, dividends, AM settlement values, the decision calendar (landed: the tools) | 2.11 | `docs/ARCHITECTURE.md`, `docs/FAQ.md`, `skills/aegis-expiry-and-assignment-protocol/SKILL.md`, `agents/position-control.md`, `skills/aegis-role-position-control/SKILL.md`, `agents/desk-lead.md`, `skills/aegis-role-desk-lead/SKILL.md`, `skills/README.md` (`aegis-event-calendar-sourcing`) |
| The Aegis Events and Calendar Analyst | 2.12 | `skills/aegis-desk-charter/SKILL.md`, `agents/desk-lead.md`, `skills/aegis-role-desk-lead/SKILL.md`, `skills/aegis-desk-incident-playbooks/SKILL.md`, `skills/aegis-desk-bootstrap-and-onboarding/SKILL.md`, `docs/ARCHITECTURE.md` |
| Signal outcomes and the journal | 3.1 | `docs/ARCHITECTURE.md`, `skills/README.md` (`aegis-signal-journal-and-review`) |
| `rules_replay` | 3.2 | `skills/aegis-rules-dsl-and-change-control/SKILL.md`, `docs/ARCHITECTURE.md` |
| The Aegis Rules Steward | 3.3 | `skills/aegis-desk-charter/SKILL.md`, `agents/desk-lead.md`, `skills/aegis-role-desk-lead/SKILL.md`, `skills/aegis-rules-dsl-and-change-control/SKILL.md`, `skills/aegis-desk-incident-playbooks/SKILL.md` |
| The Aegis Review Desk, the weekly review | 3.4 | `skills/aegis-desk-charter/SKILL.md`, `agents/desk-lead.md`, `skills/aegis-role-desk-lead/SKILL.md`, `skills/aegis-desk-incident-playbooks/SKILL.md`, `skills/README.md` |
| Any Stage 2 or Stage 3 feature presented as available (the Desk Lead's refusal line) | the last step of each stage | `agents/desk-lead.md`, `skills/aegis-role-desk-lead/SKILL.md` |
| Planned routine ids | the table above | `tests/engine/test_routines.py` |

### Mentions by file

How many times each file names Stage 2 and Stage 3, counted on its whitespace-collapsed text, so a mention wrapped
across a line break counts. `tests/gates/test_roadmap_ledger.py` holds this table equal to the tree; this page, the
changelog and that test are not counted. A pull request that changes a count updates its row, and removes the row when
both counts reach zero.

| File | Stage 2 | Stage 3 |
| --- | --- | --- |
| `agents/desk-lead.md` | 3 | 3 |
| `docs/ARCHITECTURE.md` | 1 | 1 |
| `docs/FAQ.md` | 1 | 0 |
| `scripts/roll_analysis.py` | 1 | 0 |
| `skills/README.md` | 3 | 1 |
| `skills/aegis-desk-bootstrap-and-onboarding/SKILL.md` | 2 | 1 |
| `skills/aegis-desk-charter/SKILL.md` | 2 | 1 |
| `skills/aegis-desk-incident-playbooks/SKILL.md` | 1 | 1 |
| `skills/aegis-expiry-and-assignment-protocol/SKILL.md` | 1 | 0 |
| `skills/aegis-protective-put-and-collar-management/SKILL.md` | 1 | 0 |
| `skills/aegis-role-desk-lead/SKILL.md` | 3 | 3 |
| `skills/aegis-rules-dsl-and-change-control/SKILL.md` | 0 | 2 |