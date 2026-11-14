# Roadmap: Stage 2 and Stage 3

Release v0.3.0 is Stage 1: four Bots (the Aegis Desk Lead, the Aegis Exit Sentinel, Aegis Position Control and the
Aegis Structuring Desk) watch the positions you already hold and tell you when one of your own exit rules is met. This
page collects everything the repository says is planned for Stage 2 and Stage 3, in the order it can land, with what
each step must prove before it merges. Nothing on this page is available in this release unless a step says it has
landed, and a landed engine step changes no answer a Bot gives until its wiring step lands too.

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
| 2.2 | Discrete cash dividends in the lattice, engine only (proposed here) | 2.1 | proposed |
| 2.3 | The lattice wired into the book's Greeks and implied volatility, the structuring prices, and an early-exercise line beside the expiry tiers | 2.1; your decisions on the model of record, the boundary band and the dividend schedule; 2.2 for any early-exercise line that reads a dividend | planned |
| 2.4 | The Structuring Desk's wider scope: roll analyses and replacements of long puts, short puts, collars and verticals | nothing | planned |
| 2.5 | Weekly expirations for the Structuring Desk | your decision on the chain budget | planned |
| 2.6 | A cash-balance check and per-account approval levels | your decision on stated cash | planned |
| 2.7 | Exposures and limits: beta-weighted delta and limit messages | your decision on the limits | planned |
| 2.8 | Scenario and event stress | nothing; after 2.3, by choice | planned |
| 2.9 | Volatility context | nothing | planned |
| 2.10 | The Aegis Book & Risk Analyst seat | 2.7, 2.8, 2.9 | planned |
| 2.11 | Sourced events and the decision calendar | your decision on a data source | planned |
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

### 2.2 Discrete cash dividends in the lattice (proposed here)

- **Why it is proposed.** No line in the tree plans it. A short call is most likely to be assigned early the evening
  before an ex-dividend date, and a continuous yield spreads that dividend over the year. The ex-dividend test of
  [the covered-call skill](../skills/aegis-covered-call-and-cash-secured-put-management/SKILL.md) is the European
  extrinsic-versus-dividend bound today.
- **What would land.** `lattice.american` takes an optional schedule of cash dividends, each a time in years from now
  and an amount per share, priced as known cash amounts on a tree that still recombines. Engine only: the caller
  passes the schedule, and nothing reads a file.
- **Whether it is needed.** It is a hard prerequisite of any early-exercise line in 2.3 that reads a dividend (the
  line that would say a short call is likely to be assigned before an ex-date): on a continuous yield the lattice
  cannot see that dividend (see 2.3). For prices and Greeks it is needed only if your answer to
  [open question 3](#open-questions-for-the-owner) uses cash amounts. If you want no dividend-aware line and choose
  the continuous yield or UNAVAILABLE for prices, this step can wait or be dropped.
- **Gate.** An empty schedule reproduces step 2.1's results bit for bit; goldens for a call before and after an
  ex-date; the QuantLib oracle extended with the same schedule (QuantLib's `FdBlackScholesVanillaEngine` accepts a
  `DividendVector`, checked with QuantLib 1.43).

### 2.3 The lattice wired in

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
  likely to be assigned. So an early-exercise line that reads a dividend
  needs step 2.2 and the dividend's cash amount; without them the line says it does not see the cash dividend, and
  the dividend tier stays the test for that risk.
- **The boundary band, read by side.** For a long leg, `exercise_now` gives an exercise note and `near_boundary` a
  line that says the leg is near the early-exercise boundary, never an exercise signal and never a hold signal. For a
  short leg, where a missed warning is the costly error, `exercise_now` and `near_boundary` both give an
  early-assignment warning. In the band, delta, gamma and theta are marked approximate, repriced with more steps, or
  UNAVAILABLE, as you decide ([open question 2](#open-questions-for-the-owner)).
- **Proposed here: one tree.** `carry.early_exercise_gap` runs its own tree (`carry._tree`, `carry.TREE_STEPS`) inside
  every structuring answer. Making it call `lattice.american` would give the desk one tree; `CarryAgreementTest`
  holds the two within 1e-9 until then. No line in the tree plans this, so it is optional.
- **Owner decisions.** The model of record: which answers switch, and whether the European value is printed beside
  the American one. The band (above). The dividend schedule (open question 3).
- **Gate.** The command journeys and the structuring goldens re-pinned with the lattice; every dividend-tier and
  pin-tier result identical before and after the step; a journey for the deep in-the-money short call above, whose
  dividend tier stays HIGH; journeys for a long leg and a short leg in the band; the doctor's selftest gains an
  American check; every "early exercise is not modelled", "European-only in Stage 1", "American pricing is planned",
  "American Greeks are planned" and "early-exercise analysis is planned" line rewritten.

### 2.4 The Structuring Desk's wider scope

- **What lands.** The roll analysis of a long put, a short put and a collar; a ROLL ticket for a short put; a collar's
  two legs replaced as a unit; a vertical's other choices beyond the CLOSE ticket. Today each answers NOT_APPLICABLE
  with "planned for Stage 2 (not in this release)" (`scripts/aegis_desk/structuring.py`).
- **Needs nothing, with one new caveat.** The tree names no prerequisite. A long put priced on the European model
  already carries the `american-style note:` (`_put_caveat` in `scripts/aegis_desk/structuring.py`: an absolute delta
  of 0.80 or more, or in the money with a time value below the interest on its strike). That caveat covers long puts
  only. A short put's risk is early assignment against you, and no caveat covers it today, so this step adds a
  short-put early-assignment note on the same two tests before any short-put text ships on European prices. If 2.4
  lands before 2.3, 2.3 re-pins its goldens with the others.
- **Still never.** No new symbol, no uncovered short option, no cash-secured put proposed, no order field.
- **Gate.** Fixture chains for each structure, goldens, journeys, the short-put note on a deep in-the-money short
  put and its absence on an out-of-the-money one, the alert lint's structuring mode over every new text, the QuantLib
  oracle over the new best legs, and counsel's sign-off.

### 2.5 Weekly expirations

- **What lands.** The structuring tools list weekly expirations as well as the standard monthly ones.
- **The decision first: the chain budget.** One chain is one expiration (`quote_cache.get_chain(symbol, expiration,
  ...)`), and `load_chains` fetches one per candidate expiration. A run may fetch at most 4 chains and 40 requests
  (`PER_RUN_MAX_CHAINS`, `PER_RUN_MAX_REQUESTS` in `scripts/aegis_desk/quote_cache.py`), inside the desk-wide 200
  requests per minute. That cap holds today only because expirations are monthly: a `dte` window of at most 90 days
  holds at most four monthlies ([the structuring page](STRUCTURING.md), "Expirations"). With weeklies the same window
  holds about thirteen. So this step needs your choice ([open question 8](#open-questions-for-the-owner)): raise the
  chain cap and the request budget (and show the run cost), keep four chains and sample the window (for example the
  monthlies plus the nearest weeklies, said in the text), or narrow the window when weeklies are on.
- **Gate.** Fixture chains with weekly expirations; the budget tests for the chosen rule, including a window that
  would exceed it; the expiry arithmetic for a weekly last trading day in the holiday table.

### 2.6 A cash-balance check and per-account approval levels

- **Constraint.** The desk has no brokerage API and does not see your cash. A cash figure can only be one you state,
  per account, in `desk.toml`, with its date, and acknowledged like every other owner value.
- **What lands.** An `approval_level` on each `[[owner.accounts]]` entry (each already has a `name` and a `type`),
  read before the desk-wide `[owner] approval_level`, and a cash check on a ticket or proposal that needs cash. A cash
  figure older than a stated age makes the check UNAVAILABLE, not passed.
- **Gate.** The approval-level check's tests for each account; the `desk_init.py` intake; the acknowledgement set;
  counsel's sign-off on the new lines.

### 2.7 Exposures and limits

- **What lands.** `beta_weighted_delta`, today always `{"value": null, "r2_flag": "unavailable"}` in `book.json`,
  computed from daily bars the client already reads, with the fit's quality as `r2_flag`; exposures by underlying;
  limit messages against limits you state.
- **The limits are your decision.** The tree names no limit values: `desk.toml` has no exposure or Greek limit today.
  Its `attention_thresholds` (`pl_day_pct` and `pl_trade_abs`) are profit-and-loss thresholds, recorded for later
  stages with no stage named, and this page does not assign them to limits. New limit keys go through the
  `desk_init.py` intake and the acknowledgement set ([open question 4](#open-questions-for-the-owner)).
- **Gate.** Beta goldens on fixture bars; UNAVAILABLE when bars are missing; the limit message linted, with its chat
  assigned in the charter's message table; a `midday-limits` routine within the `routine_audit.py --budget` limits.

### 2.8 Scenario and event stress

- **What lands.** A scenario tool (not yet in the tree) that re-prices the book under spot and volatility moves, and an
  event stress of a position through an earnings or ex-dividend date, including a hedge through an event. Every price
  comes from the engine; a scenario is labelled as a computation, not a forecast.
- **Order.** Nothing in the tree requires 2.3 first. This page puts 2.8 after 2.3 so that a stressed price uses the
  same model of record as the book it stresses.
- **Gate.** Re-pricing goldens; the stressed book equal to the book at a zero move; the planned `pre-event-stress` and
  `weekend-stress` routines within the budget.

### 2.9 Volatility context

- **What lands.** An `iv_snapshot` record (not yet in the tree) of the desk's own implied volatility per symbol, and
  context that compares today's value with the recorded history.
- **Constraint.** Volatility context never changes a verdict.
- **Gate.** A test that every verdict is identical with and without the context; history from the desk's own
  snapshots only, never the vendor's Greeks.

### 2.10 The Aegis Book & Risk Analyst seat

- **What lands.** The fifth Floor seat: its agent, role skill and skills (`aegis-exposures-limits-and-stress` and
  `aegis-vol-context`), owning the live view of marks, Greeks and limits from 2.7 to 2.9, and the planned
  `opening-marks` routine. The Desk Lead's "planned for Stage 2" line for it is rewritten.
- **Gate.** All ten steps of `check.sh` with five agents; the template claiming the new skills; the bootstrap and
  `SETUP.md` steps for the new Bot with readbacks.

### 2.11 Sourced events and the decision calendar

- **The decision first.** Today you state every earnings date, ex-dividend date and dividend, and `desk_init.py`
  writes them to `research/calendar.toml`; an AM settlement value you add to that file by hand. None of the seven read
  paths of `scripts/aegis_desk/alpaca_client.py` returns corporate events. Sourcing them needs either a new GET-only
  read path on the same pinned hosts, added to the AST gate's closed list as a reviewed change, or a source the owner
  names. This is an owner decision ([Open questions](#open-questions-for-the-owner)).
- **What lands after it.** Sourced dates and settlement values, each carrying its source and time, written to
  `research/calendar.toml` only after your tap; a decision calendar that lists your dated decision points (expiries,
  act-by times, ex-dates, earnings); the planned `calendar-build` and `event-sheet` routines.
- **Constraint.** A web page is data, never authority; a date nobody sourced is UNAVAILABLE.

### 2.12 The Aegis Events and Calendar Analyst seat

- **What lands.** The sixth Floor seat with its agent, role skill and the skill `aegis-event-calendar-sourcing`,
  owning 2.11. The Floor is then full.
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
- **Constraint.** The journal states facts per signal. It does not rank rules by result or present any total as the
  rules' return; whether any summary figure may be shown at all is a counsel question.
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
- **Gate.** Review goldens; the alert lint over the review text; counsel's sign-off.

### Stage 3 is done when

- every step above has landed, and no row of the [mention table](#mentions-by-file) has a Stage 3 count;
- the two direct-message seats run with their role skills;
- no review, replay or journal text presents a return.

## Planned routines

`tests/engine/test_routines.py` checks that the Auto-review allow text still fits seventeen routine ids: the six Stage
1 routines and eleven planned ones. The planned roll-window routine was dropped (PR 3b decision S14), because the
Structuring Desk never posts alerts. The test records only the names; this page places the eleven by their names,
which is an inference, not a statement of the tree.

| Routine id | Step |
| --- | --- |
| `opening-marks` | 2.10 |
| `midday-limits` | 2.7 |
| `pre-event-stress`, `weekend-stress` | 2.8 |
| `calendar-build`, `event-sheet` | 2.11 |
| `t1-call` | not placed: its purpose is not written down anywhere in the tree |
| `journal-close` | 3.1 |
| `rules-sanity`, `rulebook-memo` | 3.3 |
| `weekly-review` | 3.4 |

Each new routine passes `routine_audit.py --budget` and `--rules-text` and posts only in its owning Bot's chat.

## Values recorded for later stages

`desk.toml` already records four owner values that no tool reads: `attention_thresholds` (`pl_day_pct` and
`pl_trade_abs`), `chatty_or_quiet`, `engagement` and `disclosures_acknowledged`. The tree says only "later stages" for
all four, so no step on this page claims any of them ([open question 7](#open-questions-for-the-owner)).

## Not on the roadmap

- Placing, modifying or cancelling an order, in any stage.
- A live brokerage key, a brokerage login or reading a brokerage account.
- A trading strategy of the desk's own, a candidate symbol you do not hold, or a return claim.
- Alerts outside the Grok Bot chats.
- A roll-window routine (dropped in PR 3b).

## Open questions for the owner

1. **Event data (2.11).** Which source, if any, may the desk read corporate events from, and does it fit the one-key,
   GET-only, pinned-host boundary? Until you decide, you keep stating dates.
2. **The model of record and the band (2.3).** When the lattice is wired, which answers switch to American values,
   is the European value printed beside them, and what does a leg near the early-exercise boundary show for its
   Greeks: approximate values, values repriced with more steps, or UNAVAILABLE?
3. **The dividend schedule (2.3; 2.2 if needed).** The calendar holds one next ex-dividend per symbol (`ex_dividend`
   in `research/calendar.toml`, schema version 1). For a leg whose life spans more: a calendar with a list of
   ex-dates (a schema change, with `desk_init.py` intake and counsel), the stated dividend as a cash amount and the
   yield after it (this needs 2.2), the continuous yield as today with a note on the leg, or UNAVAILABLE? An unknown
   dividend is never zero. Whatever you choose for prices, the dividend tier keeps its quoted-extrinsic test, and an
   early-exercise line that reads a dividend needs 2.2.
4. **Limits (2.7).** Which exposure or Greek limits do you want messages for, and where do you state them?
5. **Cash (2.6).** Do you want to state a cash figure per account, and how old may it be before the check is
   UNAVAILABLE?
6. **The journal (3.1).** May the journal or the review show any total at all, and in which words (with counsel)?
7. **`t1-call` and the four recorded `desk.toml` values.** Which stage, if any, uses them?
8. **The chain budget for weeklies (2.5).** Raise the four-chain cap and the request budget, sample the window, or
   narrow it when weeklies are on?

## Where each item is mentioned today

The ledger. The pull request that lands an item rewrites the lines its row points to. Role skills are generated from
the agent files: a line in `aegis-role-*` changes by editing `agents/<seat>.md` and running
`python3 scripts/gen_role_skills.py`. Some files are listed for an item without naming a stage (for example
`scripts/aegis_desk/carry.py` and `scripts/aegis_desk/bsm.py`); the [mention table](#mentions-by-file) below counts
only the files that do.

| Item | Step | Mentioned in |
| --- | --- | --- |
| American pricing: the desk's prices and Greeks, and early-exercise analysis beside the expiry tiers | 2.3 (2.1 rewrote the lines that named the lattice or an early-exercise model) | `docs/ARCHITECTURE.md`, `docs/FAQ.md`, `docs/PROVENANCE.md`, `docs/STRUCTURING.md`, `skills/aegis-book-marking-and-greeks-honesty/SKILL.md`, `skills/aegis-expiry-and-assignment-protocol/SKILL.md`, `skills/aegis-covered-call-and-cash-secured-put-management/SKILL.md`, `skills/aegis-roll-decision-framework/SKILL.md`, `agents/position-control.md`, `skills/aegis-role-position-control/SKILL.md`, `scripts/aegis_desk/structuring.py` (`MODEL_NOTE`, `_put_caveat`), `scripts/aegis_desk/structuring_text.py` (`MODEL_LINE`), `scripts/aegis_desk/bsm.py` and `scripts/aegis_desk/iv.py` (module notes), `tests/engine/test_lattice.py` (the `MODEL_NOTE` assertion) |
| The parity-carry bound's own tree (proposed here) | 2.3 | `scripts/aegis_desk/carry.py` (`_tree`, `early_exercise_gap`, `TREE_STEPS`), `docs/STRUCTURING.md` (the carry of each expiration) |
| Roll analysis of long puts, short puts and collars | 2.4 | `docs/ARCHITECTURE.md`, `docs/STRUCTURING.md`, `skills/aegis-roll-decision-framework/SKILL.md`, `agents/structuring-desk.md`, `skills/aegis-role-structuring-desk/SKILL.md`, `scripts/roll_analysis.py`, `scripts/aegis_desk/structuring.py`, `tests/engine/test_structuring_text.py`, `tests/tools/test_roll_analysis_tool.py` |
| Short put, collar-as-a-unit and vertical replacements | 2.4 | `docs/STRUCTURING.md`, `skills/aegis-ticket-and-unwind-order/SKILL.md`, `skills/aegis-covered-call-and-cash-secured-put-management/SKILL.md`, `skills/aegis-protective-put-and-collar-management/SKILL.md`, `skills/aegis-vertical-spread-management/SKILL.md`, `scripts/aegis_desk/structuring.py` (the ROLL refusals, among them the collar refusal whose text is two adjacent literals) |
| Weekly expirations | 2.5 | `docs/ARCHITECTURE.md`, `docs/STRUCTURING.md` (overview, "Expirations", limits), `skills/aegis-roll-decision-framework/SKILL.md`, `scripts/aegis_desk/quote_cache.py` (`PER_RUN_MAX_CHAINS`) |
| Cash-balance check, per-account approval levels | 2.6 | `docs/ARCHITECTURE.md`, `docs/STRUCTURING.md` |
| Beta-weighted delta, exposures, limits and limit messages | 2.7 | `skills/aegis-book-marking-and-greeks-honesty/SKILL.md`, `skills/aegis-desk-charter/SKILL.md` (message table), `skills/README.md` (`aegis-exposures-limits-and-stress`), `scripts/aegis_desk/book.py` (the `beta_weighted_delta` placeholder), `agents/desk-lead.md`, `skills/aegis-role-desk-lead/SKILL.md` |
| Scenario and event stress, a hedge through an event | 2.8 | `docs/ARCHITECTURE.md`, `docs/STRUCTURING.md` (`scenario`), `skills/aegis-protective-put-and-collar-management/SKILL.md`, `skills/aegis-desk-charter/SKILL.md`, `skills/README.md` |
| Volatility context | 2.9 | `docs/ARCHITECTURE.md`, `docs/STRUCTURING.md` (`iv_snapshot`), `skills/README.md` (`aegis-vol-context`), `skills/aegis-book-marking-and-greeks-honesty/SKILL.md` |
| The Aegis Book & Risk Analyst | 2.10 | `skills/aegis-desk-charter/SKILL.md`, `agents/desk-lead.md`, `skills/aegis-role-desk-lead/SKILL.md`, `skills/aegis-desk-incident-playbooks/SKILL.md`, `skills/aegis-desk-bootstrap-and-onboarding/SKILL.md` (the Stage-2 seats), `docs/ARCHITECTURE.md` |
| Sourced earnings and ex-dividend dates, dividends, AM settlement values, the decision calendar | 2.11 | `docs/ARCHITECTURE.md`, `docs/FAQ.md`, `skills/aegis-expiry-and-assignment-protocol/SKILL.md`, `agents/position-control.md`, `skills/aegis-role-position-control/SKILL.md`, `agents/desk-lead.md`, `skills/aegis-role-desk-lead/SKILL.md`, `skills/README.md` (`aegis-event-calendar-sourcing`) |
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
| `agents/position-control.md` | 2 | 0 |
| `agents/structuring-desk.md` | 1 | 0 |
| `docs/ARCHITECTURE.md` | 3 | 1 |
| `docs/FAQ.md` | 2 | 0 |
| `docs/PROVENANCE.md` | 1 | 0 |
| `docs/STRUCTURING.md` | 5 | 0 |
| `scripts/roll_analysis.py` | 1 | 0 |
| `scripts/aegis_desk/book.py` | 1 | 0 |
| `scripts/aegis_desk/structuring.py` | 4 | 0 |
| `skills/README.md` | 3 | 1 |
| `skills/aegis-book-marking-and-greeks-honesty/SKILL.md` | 2 | 0 |
| `skills/aegis-covered-call-and-cash-secured-put-management/SKILL.md` | 2 | 0 |
| `skills/aegis-desk-bootstrap-and-onboarding/SKILL.md` | 2 | 1 |
| `skills/aegis-desk-charter/SKILL.md` | 2 | 1 |
| `skills/aegis-desk-incident-playbooks/SKILL.md` | 1 | 1 |
| `skills/aegis-expiry-and-assignment-protocol/SKILL.md` | 3 | 0 |
| `skills/aegis-protective-put-and-collar-management/SKILL.md` | 1 | 0 |
| `skills/aegis-role-desk-lead/SKILL.md` | 3 | 3 |
| `skills/aegis-role-position-control/SKILL.md` | 2 | 0 |
| `skills/aegis-role-structuring-desk/SKILL.md` | 1 | 0 |
| `skills/aegis-roll-decision-framework/SKILL.md` | 2 | 0 |
| `skills/aegis-rules-dsl-and-change-control/SKILL.md` | 0 | 2 |
| `skills/aegis-ticket-and-unwind-order/SKILL.md` | 1 | 0 |
| `skills/aegis-vertical-spread-management/SKILL.md` | 1 | 0 |
| `tests/engine/test_lattice.py` | 1 | 0 |
| `tests/engine/test_structuring_text.py` | 1 | 0 |
| `tests/tools/test_roll_analysis_tool.py` | 1 | 0 |
