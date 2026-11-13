# Alert lint fixtures

Messages for `scripts/alert_lint.py --message-file <name>.txt --emitted-file <name>.emitted.json` and for
`tests/engine/test_alert_lint.py`. Every `.emitted.json` is the strict emitted document `Alert.emitted_document()`
gives: `emitted_numbers` (the number strings the formatters of `aegis_desk.alerts` printed), `rule_ids` (the rule
ids the message names), `identifiers` (the trade ids, `rules X.Y.Z` and `sha <8 hex>` it prints), `clocks` (its
`HH:MM UTC` times) and `as_of` (the footer's instant). Nothing here is written at run time.

## Passing messages (exit 0)

The text of each is exactly what `aegis_desk.alerts` renders for the hand-written evaluations of
`tests/engine/test_alerts.py` on books built from copies of `tests/fixtures/desk` (rulebook `rules 1.0.0`, sha
`3f9a01bc...`, posted 2026-11-20 19:50 UTC); `FixtureGoldenTest` there fails when the rendering changes.

| Fixture | Book | Message |
| --- | --- | --- |
| `pass_exit_signal` | `expiry_day` at 2026-11-20T19:50:00Z | EXIT SIGNAL on the WMT covered call: TP80 at 95.32 % (the fresh ask 0.16 of a grade-E quote), DTE21 and PIN3 also fired, three rules suppressed by `#assign-ok` |
| `pass_watch` | `regular` at 2026-11-03T19:50:00Z | WATCH on the NVDA covered call: DTE21 fired, DELTA_WATCH pending its second observation |
| `pass_unavailable` | `stale` at 2026-11-03T19:50:00Z | the UNAVAILABLE banner of the WMT covered call with each unavailable rule's reason |
| `pass_composed` | `expiry_day` | CONFIG CHANGED first, then both EXIT SIGNAL messages, one footer |
| `pass_pre_close` | `regular` | the pre-close line |

## Red-team messages (exit 1)

Each is `pass_exit_signal` or `pass_watch` with one documented change (`fail_fixture_table()` in
`tests/engine/test_alert_lint.py`), and fails for that reason only:

| Fixture | Change | Lint finding |
| --- | --- | --- |
| `fail_2x_credit` | the metric sentence reads `loss 2x credit`; `2.0` is emitted, `2` is not | `number_not_emitted` |
| `fail_21_dte` | `21-DTE` in the text; `34` is emitted, `21` is not | `number_not_emitted` |
| `fail_pct_52_4` | `52.4 %` in the text; `52.1` is emitted | `number_not_emitted` |
| `fail_you_should` | an advice phrase (lint table entry FL01) added after the plan | `forbidden_language` |
| `fail_missing_footer` | the footer line removed | `footer_missing` |
| `fail_order_field` | a line of order-ticket fields (entries FL21, FL22, FL23) | `order_field` |
| `fail_imperative_start` | a segment that starts with an imperative word (IS01) | `imperative_start` |
| `fail_split_phrase` | an advice phrase (FL02) broken across two lines | `forbidden_language` |
| `fail_homoglyph` | a Cyrillic letter inside the plan word | `non_ascii` |
| `fail_imperative_execute` | a segment that starts with the imperative IS08 (added in PR 2d) | `imperative_start` |
| `fail_imperative_fill` | a segment that starts with the imperative IS09 (added in PR 2d) | `imperative_start` |
| `fail_order_kind` | a resting order kind (lint table entry FL25, added in PR 2d) after the plan | `forbidden_language` |

## Structuring messages (the third footer, PR 3b)

The Structuring Desk's texts end with the structuring footer (`computed by the desk's tools from your criteria ·
posted by an AI assistant · a personalized suggestion for positions you hold, not an order · you decide and act in
your brokerage account · data delayed 15 min, as of HH:MM UTC`) and their emitted documents carry
`"footer": "structuring"`, so `alert_lint.py` lints them in structuring mode without a `--footer` flag. The three
passing texts are the 0.2.1 desk view, rendered by the tools' own builders (`structuring_text`, `tickets`) on a copy
of the fixture desk with the fixture criteria at the `regular` instant (quotes 2026-11-03 19:31 UTC), each with the
emitted document its writer recorded: a markdown title, tables whose status cells carry the three status symbols
(`alert_lint.STATUS_SYMBOLS`), the legs as list items in the fixed leg grammar, and the notes as a quoted list.
`rule_ids` is `[]` in every structuring document.

| Fixture | Message |
| --- | --- |
| `pass_structuring_ticket` | the WMT ROLL ticket: the context strip, the legs (close the 2026-11-20 105 call, open the 2027-01-15 115 call), the cash table, the new call's checks table with its accepted ex-dividend row, the group table, the approval block and the notes |
| `pass_structuring_roll` | the WMT roll analysis: the best candidate line, its legs and the caveat; the checks table with the accepted ex-dividend row; the group table; the ranked candidates table (two failing `credit_only`) and the ticket pointer; the notes |
| `pass_structuring_proposal` | the SPY collar proposal on 100 shares: the best pair (620 put, 680 call), its legs and the caveat; the checks table with two accepted ex-dividend rows; the group and approval tables; the ranked candidates and the legs outside their bands; the notes |

Each red-team message is one documented change (`structuring_fail_fixture_table()` in
`tests/engine/test_alert_lint.py`) and fails for that reason only:

| Fixture | Change | Lint finding |
| --- | --- | --- |
| `fail_structuring_under_rules_footer` | `pass_structuring_ticket` closed by the rules footer (document `footer`: `rules`): the rules footer cannot close a leg or a candidate, and the status symbols stand only in a structuring text | `non_ascii` (first), `footer_missing` |
| `fail_rules_text_under_structuring_footer` | `pass_exit_signal` closed by the structuring footer (document `footer`: `structuring`): the structuring footer cannot close rule content, and its `your plan: close` is no exception in structuring mode | `imperative_start` (first), `footer_missing` |
| `fail_structuring_leg_grammar` | the first leg line reworded with a side word and no effect words, outside the fixed leg grammar (the side word at a segment start is a structuring imperative too) | `order_field` (first), `imperative_start` |
| `fail_structuring_go_with` | an advice phrase (structuring table entry SL04) after the caveat | `forbidden_language` |
| `fail_structuring_at_price` | a price after `at` (entry SL14) in place of `the desk prints none` | `order_field` |
