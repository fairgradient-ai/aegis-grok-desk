# Your exit rules

The desk evaluates only the exit rules you adopted. A rule is data: a fixed grammar read by a fixed catalogue of
metrics, with no code in it. This page is for you, the owner. The grammar in full and the change-control steps the
Bots follow are in the skill [aegis-rules-dsl-and-change-control](../skills/aegis-rules-dsl-and-change-control/SKILL.md);
flags and outputs are in [TOOLS.md](TOOLS.md).

## Your rules, adopted one by one

- [rules/examples/exit_rules-1.0.0.toml](../rules/examples/exit_rules-1.0.0.toml) holds example rules. Nothing in it
  is active. [rules/examples/README.md](../rules/examples/README.md) explains each family and why every rule is yours.
- When you say "adopt my exit rules" ([SETUP step 9](../SETUP.md#92-adopt-my-exit-rules)), the Aegis Desk Lead
  copies the file to a candidate in your desk root. One answer is enough: keep the examples as written, or name the
  rules to change or drop. You keep a rule as written (`origin = "example-accepted"`), change its threshold
  (`origin = "owner-entered"`), or leave it out. A rule still marked `origin = "example"` is refused.
- `DAILY` and `CONC` depend on your account. They are refused until you enter a value or write
  `owner_must_set = false` to keep the documented default.
- `rules_adopt.py` adopts the file under a version number, with the phrase you type:
  `--owner-phrase "adopt rules X.Y.Z"`. It refuses during regular trading hours unless you tap `--allow-rth`. It copies
  the file to `rules/exit_rules-X.Y.Z.toml`, which never changes again, records the file's sha256 and every rule's own
  sha256 in `rules/ADOPTED`, and appends an entry to `rules/CHANGELOG.md` that marks each rule `example-accepted` or
  `owner-entered` with its threshold.
- A change is a new version. The Lead copies the adopted file to a candidate, changes only what you stated, shows the
  effect with `rules_diff.py` (it writes nothing), and adopts the new version after the close. The next posted book
  check prints the digest once, and CONFIG CHANGED stands until you tap `ack.py config --owner-tapped`.
- Going back is an adoption too, by either of two routes. Re-adopt the earlier adopted file under its own number (its
  bytes are unchanged), or adopt the earlier content under a new number. A different file under an adopted number is
  refused (`immutable_version`): an adopted version is never rewritten.

A synthetic dry run of the fixture candidate 1.1.0, which raises `TP50` from 50 to 60 percent (2026-11-03, 19:50 UTC):

```text
rules: changed TP50; added none; removed none; 26 unchanged
no group verdict changed (3 groups compared)
verdicts changed: 0
```

`rules_diff.py` exits 10 when a group's verdict would change. After that candidate was adopted (22:00 UTC), the next
posted book check (2026-11-04, 15:05 UTC) printed the digest:

```text
rulebook changed: 0 verdicts changed | rules 1.0.0 -> rules 1.1.0, sha 131490c8
```

## How a rule is evaluated

- Each condition is TRUE, FALSE or UNKNOWN. A rule is the conjunction of its conditions: FALSE if any is FALSE, else
  UNKNOWN if any is UNKNOWN, else TRUE.
- A TRUE rule has fired, or is pending (still waiting for its confirmations), or is in its cooldown (it fired, cleared
  and is true again before it re-arms). An UNKNOWN rule is unavailable, or has no history yet (it needs the desk's
  previous end-of-day snapshot). A FALSE rule is clear. A rule a row tag switches off is suppressed.
- A metric that cannot speak about a position for a reason of record is not applicable, and the rule stays clear: no
  stop written for the group, a dividend or earnings date you confirmed as none, earnings already past, a call that
  expires before the last cum-dividend day.
- Each group gets one verdict. The precedence runs from EXIT_SIGNAL down through UNAVAILABLE and WATCH to HOLD. A
  pending rule counts for neither. HOLD is never the verdict while a rule that applies is unavailable. A quarantined
  group is UNAVAILABLE.
- Underlying rules (`MOVE5_DOWN`, `MOVE5_UP`) and portfolio rules (`DAILY`, `CONC`) are reported in their own lines.
  They never set a group's verdict or the exit code.
- The exit code: 10 when any group is EXIT_SIGNAL, 20 when any is UNAVAILABLE, 0 otherwise.

## The per-structure metric table

Every held structure needs a take-profit rule that reads its own measure, and has at most one loss rule. The
adoption lint refuses anything else.

| Structure | Take-profit metric | Loss metric (at most one rule) |
| --- | --- | --- |
| `cash_secured_put`, `vertical_credit_call`, `vertical_credit_put` | `pct_captured` of the credit | `loss_multiple_of_credit`, `pct_of_max_loss` or `unrealized_pl` |
| `short_call` | `pct_captured` of the credit | `loss_multiple_of_credit` or `unrealized_pl` |
| `long_put`, `vertical_debit_call`, `vertical_debit_put` | `pct_of_debit` or `pct_of_max_profit` | `pct_of_debit`, `pct_of_max_loss` or `unrealized_pl` |
| `long_call` | `pct_of_debit` (no max profit to measure against) | `pct_of_debit`, `pct_of_max_loss` or `unrealized_pl` |
| `covered_call`, `collar` | `pct_captured` of the short call (`applies_to` `short_call_leg`), or `pct_of_max_profit` of the group | on the group only: `unrealized_pl` or `pct_of_max_loss`; never on the short call (`short_call_leg`, `any_short_call_leg`) |
| `protective_put`, `long_stock` | none needed | `pct_of_max_loss` or `unrealized_pl` |
| `short_stock` | none needed | `unrealized_pl` |
| `custom` | any take-profit metric | any loss metric; only `any_group`, `any_option_leg` and the underlying and portfolio rules reach it |

- A credit metric never covers a debit structure. The short leg's credit of a debit vertical covers nothing.
- A metric a structure does not define is refused (`metric_not_applicable`). So is a `pct_of_max_loss` threshold above
  100 (`max_loss_over_100`).
- The structures themselves are decided by `positions_check.py` from your open legs; see
  [POSITIONS_CSV.md](POSITIONS_CSV.md).

## Which side of the quote a rule reads

- A take-profit rule on the liquidation basis (`mark_basis = "liq"`, the default) reads the fresh, conservative side of
  any quote graded A to E: a short leg at its ask, a long leg at its bid. A wide spread can only delay a take-profit.
  The alert then says `quote grade <G>: the conservative side stands`.
- A zero ask is no offer: that read is unavailable (`quote grade <G>: no offer (ask 0)`).
- Every other rule needs a two-sided fresh quote (grade A to D). Without one, the read is unavailable:
  `quote grade <G>: a loss test needs a two-sided fresh quote`.
- The last trade is never used, and no model price feeds a rule.

Grades, marks and freshness are in the skill
[aegis-market-data-budget-and-quality](../skills/aegis-market-data-budget-and-quality/SKILL.md).

## The example families and thresholds

The conditions as the fixture's `rules/CHANGELOG.md` records them when every example is accepted as written. The
class is what a rule reads: E only dates, M a quote, G a model value (delta) from a quote.

| Rule | Condition | Verdict and plan | Class |
| --- | --- | --- | --- |
| Take profit at half the credit (`TP50`) | pct_captured >= 50 percent and dte > 21 trading days | an EXIT SIGNAL, plan `close` | M |
| Take profit at 80 percent of the credit (`TP80`) | pct_captured >= 80 percent | an EXIT SIGNAL, plan `close` | M |
| Loss at two times the credit (`LOSSX`) | loss_multiple_of_credit >= 2 x | an EXIT SIGNAL, plan `close` | M |
| Twenty-one trading days left (`DTE21`) | dte <= 21 trading days | a WATCH, plan `roll_review` | E |
| Short option delta at 0.30 (`DELTA_WATCH`) | abs_delta >= 0.30 delta | a WATCH, plan `hold_review` | G |
| Short option delta at 0.50 (`DELTA_SIGNAL`) | abs_delta >= 0.50 delta | an EXIT SIGNAL, plan `roll_review` | G |
| Short option pinned near the strike (`PIN3`) | pct_from_strike <= 3 percent and dte <= 3 trading days | a WATCH, plan `hold_review` | M |
| Early assignment before the ex-dividend date (`EXDIV`) | days_to_ex_dividend <= 5 days and extrinsic_minus_dividend < 0 usd | an EXIT SIGNAL, plan `roll_review` | M |
| Covered call capped (`CAP`) | stock_above_short_call is_true and dte <= 21 trading days | a WATCH, plan `roll_review` | M |
| Hedge expiring within 14 days (`LAPSE14`) | dte <= 14 calendar days | an EXIT SIGNAL, plan `roll_review` | E |
| Hedge within 30 days of expiry (`ROLL30`) | dte <= 30 calendar days | a WATCH, plan `roll_review` | E |
| Hedge deep in the money (`MONETIZE`) | abs_delta >= 0.80 delta | an EXIT SIGNAL, plan `hold_review` | G |
| Stock at the hedge's floor (`FLOOR`) | stock_below_long_put is_true | a WATCH, plan `hold_review` | M |
| Collar call in the money (`COVER`) | stock_above_short_call is_true | a WATCH, plan `roll_review` | M |
| Automatic exercise at expiry (`AUTOEX`) | auto_exercise_itm is_true | an EXIT SIGNAL, plan `hold_review` | M |
| Long option debit doubled (`LTP100`) | pct_of_debit >= 100 percent | an EXIT SIGNAL, plan `close` | M |
| Long option half the debit lost (`LLOSS50`) | pct_of_debit <= -50 percent | an EXIT SIGNAL, plan `close` | M |
| Long option within 30 days of expiry (`LDTE30`) | dte <= 30 calendar days | a WATCH, plan `hold_review` | E |
| Debit vertical at 60 percent of max profit (`VTP`) | pct_of_max_profit >= 60 percent | an EXIT SIGNAL, plan `close` | M |
| Debit vertical at half its max loss (`VLOSS`) | pct_of_max_loss >= 50 percent | an EXIT SIGNAL, plan `close` | M |
| Vertical between the strikes near expiry (`VBETWEEN`) | between_strikes is_true and dte <= 2 trading days | an EXIT SIGNAL, plan `close` | M |
| Underlying at the stop (`USTOP`) | spot_vs_stop_pct <= 0 percent | an EXIT SIGNAL, plan `hold_review` | M |
| Underlying down 5 percent (`MOVE5_DOWN`) | move_pct_from_prev_eod <= -5 percent | a WATCH, plan `hold_review` | M |
| Underlying up 5 percent (`MOVE5_UP`) | move_pct_from_prev_eod >= 5 percent | a WATCH, plan `hold_review` | M |
| Earnings within two trading days (`EARN2`) | days_to_earnings <= 2 days and days_to_earnings >= 0 days | a WATCH, plan `hold_review` | E |
| Day loss on the book (`DAILY`) | day_pl <= -1000 usd | a WATCH, plan `hold_review` | M |
| Concentration in one underlying (`CONC`) | max_concentration_pct >= 25 percent | a WATCH, plan `hold_review` | M |

Which structures each family reaches is in the families table of
[rules/examples/README.md](../rules/examples/README.md). The plan words an alert prints are "close" for `close`,
"review a roll" for `roll_review` and "review and decide" for `hold_review`. A plan is the note you wrote down for
yourself; the desk never decides it. An alert never prices your choices: when you want them after an alert, ask the
Aegis Structuring Desk, which answers only your request, with the trade id ([STRUCTURING.md](STRUCTURING.md)).

The numbers above are documented defaults for you to accept or change, not a judgment that they suit any account. Each
example names its reference class in its `source`; see [PROVENANCE.md](PROVENANCE.md). A stop alert such as `LOSSX` is
risk control, not edge.

## Confirmations, re-arm and cooldown

The example rulebook's `[defaults]`, each bounded in its safe direction by the grammar:

| Key | Default | Allowed |
| --- | --- | --- |
| `mark_basis` | `liq` | `liq` or `mid` |
| `confirmations` | 2 | 1 to 3 |
| `confirmation_gap_min` | 10 | 1 to 60 minutes |
| `confirmation_window_min` | 90 | the gap to 240 minutes |
| `cooldown_min` | 240 | 0 to 1440 minutes |
| `rearm` | `next_trading_day` | `next_trading_day`, `immediate` or `hysteresis` |
| `hysteresis_pct` | 10 | 0 to 50 percent |

- Class E rules never wait. Class M and G rules need their confirmations: observations whose quote times are at least
  the gap apart, all within the window of the newest. One observation is enough when the session is not live or the
  routine's next run falls after the close. On the lean profile that usually means the 15:35 run; see
  [MONITORING.md](MONITORING.md).
- After a rule fires it re-arms on the next trading day by default. A cooldown holds the re-arm back after a post, but
  never past the open of the next trading session, so a long cooldown cannot keep an exit rule silent into the next
  day.
- A threshold already past at the desk's first observation of a position still fires, and the alert says
  `already past this threshold at the desk's first observation`.
- Rule state is kept per position and rule in `state/rule_state.json`. An adoption keeps the state of every rule whose
  meaning did not change (a new title, origin or source changes nothing) and resets the others.

## Row tags

A tag in a row's `note` switches a rule off for that position's group only when the rule names the tag in its
`suppress_if_tag` key. In the example rulebook:

- `#assign-ok` (you accept assignment) silences `EXDIV`, `DELTA_WATCH`, `DELTA_SIGNAL` and `CAP`;
- `#earnings-ok` silences `EARN2`.

`PIN3` carries no `suppress_if_tag` there, so no tag switches it off. Your own rulebook says which rules a tag
silences. A suppressed rule is still computed and reported, for example
`suppressed by your row tags: DELTA_WATCH, DELTA_SIGNAL, CAP`; it never fires and records no state.

## Anatomy of an alert

A synthetic EXIT SIGNAL from the fixture's expiry day (2026-11-20, 19:50 UTC), one line in the message:

```text
EXIT SIGNAL AG-20261020-01 | WMT covered call | your rule TP80 (rules 1.0.0, sha cc09859c) fired: credit captured 95.32 % (your threshold: at or above 80 %); quote grade E: the conservative side stands | already past this threshold at the desk's first observation | quote grade E, Tradier sandbox, 15-min delayed, as of 19:31 UTC | your plan: close | not an instruction to trade; you act in your brokerage account.
```

Read left to right:

1. the verdict and your position's trade id;
2. the underlying and the structure;
3. your rule's id, the rulebook version and the first eight characters of its sha256;
4. the value that made it fire and your threshold, in plain words, with the quote note when one applies;
5. a first-observation note when the threshold was already past;
6. the quote grade, the source, the delay and the UTC time of the quotes (and `run N min late` when the routine
   started late);
7. your plan;
8. the closing clause: it is not an instruction to trade, and you act in your brokerage account.

Other rules of the same group that fired follow on `also fired:` lines. Every message ends with the fixed footer:

```text
deterministic rules you adopted · posted by an AI assistant · not personalized investment advice · data delayed 15 min, as of 19:31 UTC
```

An UNAVAILABLE banner says `no verdict while an input is unavailable; this is not HOLD` and names each missing input,
for example `unavailable: your rule TP80: row 4 WMT261120C00105000: quote grade F (stale): no fresh ask` (the
fixture's stale set, 2026-11-03 19:50 UTC). Alerts never carry order fields, strikes or expiries to move to, or ranked
alternatives.

## The adoption lint and refusals

`rules_adopt.py` lists every finding by code. The lint findings:

| Code | Meaning |
| --- | --- |
| `not_accepted` | a rule is still `origin = "example"` |
| `owner_must_set` | `DAILY` or `CONC` still needs your value or `owner_must_set = false` |
| `uncovered_structure` | a held structure has no take-profit rule that covers it (`--allow-uncovered` accepts it, recorded) |
| `take_profit_metric` | a take-profit rule does not read the structure's own measure |
| `loss_on_short_call_leg` | a loss rule on the short call of a covered call or collar |
| `loss_metric` | a loss rule with a metric that is not a loss metric, or not a group metric of a covered call or collar |
| `duplicate_loss` | more than one loss rule on one structure |
| `max_loss_over_100` | a `pct_of_max_loss` threshold above 100 |
| `metric_not_applicable` | a metric the structure does not define |
| `input_unavailable` | a metric reads a field the book does not have |
| `selects_nothing` | a leg rule names a structure without a short option leg |

The refusals: `owner_phrase_mismatch` (the phrase is not exactly `adopt rules X.Y.Z`), `version_mismatch` (the file's
`rulebook_version` is not `--version`), `regular_trading_hours`, `lint_failed` and `immutable_version` (that version
was already adopted with other bytes). A refused adoption writes nothing.

A rulebook whose adopted file changed afterwards is refused at the next book check (`rules_tampered`): the desk
evaluates nothing on a file you did not adopt.

## Where to read more

- [rules/examples/README.md](../rules/examples/README.md): the families, the tags, the FINRA reasoning.
- [aegis-rules-dsl-and-change-control](../skills/aegis-rules-dsl-and-change-control/SKILL.md): the grammar and change
  control.
- [aegis-exit-watch-and-signal-protocol](../skills/aegis-exit-watch-and-signal-protocol/SKILL.md): posting,
  acknowledgements and the re-post ladder.
- [aegis-threshold-levels](../skills/aegis-threshold-levels/SKILL.md): each rule as a price.
- [TOOLS.md](TOOLS.md): the tools `rules_adopt`, `rules_diff`, `book_check` and the evaluation tool `exit_check`.
