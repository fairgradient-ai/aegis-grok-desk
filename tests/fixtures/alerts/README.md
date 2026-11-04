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
