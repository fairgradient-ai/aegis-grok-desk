---
name: aegis-rules-dsl-and-change-control
description: The Aegis exit rulebook as data and how it changes, for the Aegis Desk Lead (who holds rulebook change control in Stage 1) and the Aegis Exit Sentinel (who reads it). The strict TOML grammar with no eval, the defaults and their safe bounds, rule keys, conditions, units and dte_unit, applies_to and the metric catalogue, origin marks and owner_must_set, the adoption lint and the refusal codes, the candidate-file interview, the rules_diff dry run, the owner phrase and tap, adoption outside regular trading hours, immutability, per-rule shas and state carry-forward, tamper detection, the digest and CONFIG CHANGED, and rollback.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: rules
---

# Rules DSL and change control

The owner's exit rules are data in one TOML file per version. A fixed grammar reads them and a fixed catalogue
evaluates them; nothing in a rule is ever run as code. Every rule is the owner's, adopted one by one. In Stage 1 the
Aegis Desk Lead holds rulebook change control. The Aegis Exit Sentinel runs the adopted rules and never edits one.

The examples are synthetic: tool runs on copies of `tests/fixtures/desk` at the instants named beside them.
[rules/examples/README.md](../../rules/examples/README.md) explains the example rulebook and its sources.

## The file

The top level holds `schema_version = 1`, `rulebook_version = "X.Y.Z"` (never with a leading v), a `title`, one
`[defaults]` table and at least one `[[rule]]`. The reader is strict: an unknown key or section is an error, and every
problem is listed at once (`config_invalid`). A missing file is `rules_missing`.

The defaults of [rules/examples/exit_rules-1.0.0.toml](../../rules/examples/exit_rules-1.0.0.toml), with their bounds:

| Key | Example | Allowed |
| --- | --- | --- |
| `mark_basis` | `"liq"` | `liq` or `mid` |
| `confirmations` | 2 | 1 to 3 |
| `confirmation_gap_min` | 10 | 1 to 60 |
| `confirmation_window_min` | 90 | from the gap to 240 |
| `cooldown_min` | 240 | 0 to 1440 (the hold ends at the latest at the next session's open) |
| `rearm` | `"next_trading_day"` | `next_trading_day` or `immediate` or `hysteresis` |
| `hysteresis_pct` | 10 | 0 to 50 |

Every key is required. A rule may override any of them, within the same bounds.

## A rule

One rule from the example file, as written there:

```toml
[[rule]]
id = "TP50"
title = "Take profit at half the credit"
role = "take_profit"
scope = "group"
applies_to = ["cash_secured_put", "short_call", "vertical_credit_call", "vertical_credit_put", "short_call_leg"]
input_class = "M"
when = [
  { metric = "pct_captured", op = ">=", value = 50, unit = "percent" },
  { metric = "dte", op = ">", value = 21, dte_unit = "trading" },
]
verdict = "EXIT_SIGNAL"
plan = "close"
origin = "example"
source = "tastylive manage-winners research: half the credit captured with more than 21 trading days left (owner threshold)"
```

| Key | What it holds |
| --- | --- |
| `id` | an upper-case id of 2 to 16 characters, unique (`^[A-Z][A-Z0-9_]{1,15}$`) |
| `title` | a short name |
| `role` | `take_profit`, `loss`, `time`, `risk`, `event`, `portfolio` or `watch` |
| `scope` | `group`, `leg`, `underlying` or `portfolio` |
| `applies_to` | the structures or pseudo-structures the rule reaches (below) |
| `input_class` | `E`, `M` or `G`, at least the class of every metric it reads |
| `when` | a non-empty list of conditions; the rule holds when all of them hold |
| `verdict` | either `EXIT_SIGNAL` or `WATCH`; an underlying or portfolio rule is always `WATCH` |
| `plan` | the word `close` or `roll_review` or `hold_review`, printed as the owner's plan |
| `origin` | `example`, `example-accepted` or `owner-entered` |
| `source` | the public reference class or the owner's reason |
| `suppress_if_tag` | optional: `assign-ok` or `earnings-ok` (group and leg rules only) |
| `owner_must_set` | optional: true while the owner still has to decide the value |

## Conditions, units and dte_unit

- A condition is an inline table `{ metric, op, value, unit, dte_unit }`.
- A numeric metric takes `op` `<`, `<=`, `>`, `>=` or `==` and a TOML number as `value`. A yes-or-no metric takes
  `is_true` or `is_false`, with no `value` and no `unit`.
- `unit` is required on every percent and usd metric and, when given, must be the metric's own unit.
- `dte_unit` (`calendar` or `trading`) is required on every `dte` condition and refused on any other.
- A threshold on a days metric is a whole number of days.

The catalogue, by unit (input class E unless marked M or G):

| Unit | Metrics |
| --- | --- |
| percent | `pct_captured`, `pct_of_max_profit`, `pct_of_debit`, `pct_of_max_loss`, `spot_vs_stop_pct`, `pct_from_strike`, `move_pct_from_prev_eod`, `max_concentration_pct` (all M) |
| usd | `unrealized_pl`, `extrinsic_bid`, `extrinsic_minus_dividend`, `day_pl` (all M); `dividend_amount` |
| multiple | `loss_multiple_of_credit` (M) |
| days | `dte`, `days_to_earnings`, `days_to_ex_dividend` |
| delta | `abs_delta`, `net_delta` (both G) |
| yes or no | `between_strikes`, `stock_above_short_call`, `stock_below_long_put`, `itm`, `auto_exercise_itm` (all M) |

`applies_to` names structures (`covered_call`, `cash_secured_put`, `protective_put`, `collar`, the four verticals,
`long_call`, `long_put`, `short_call`, `long_stock`, `short_stock`, `custom`) or pseudo-structures:
`short_call_leg` (the short call of a covered call or collar), `long_put_leg` (the long put of a protective put or
collar), `any_short_call_leg`, `any_option_leg`, `any_group`, `any_underlying` and `portfolio`. In a leg rule a structure name selects that
structure's open short option legs.

## Origin marks and owner_must_set

- A rule marked `origin = "example"` is refused at adoption (`not_accepted`): every rule is adopted individually.
- `example-accepted` means the owner keeps the documented threshold. `owner-entered` means the owner wrote the value.
- DAILY and CONC carry `owner_must_set = true`: their values depend on the owner's account. The lint refuses them
  until the owner enters a value (marked `owner-entered`) or writes `owner_must_set = false` to keep the documented
  default.
- Each adoption appends one entry to `rules/CHANGELOG.md` with every rule's mark and threshold. One line of the
  fixture's entry:

  ```text
  - TP50 example-accepted: pct_captured >= 50 percent and dte > 21 trading days; EXIT_SIGNAL, plan close
  ```

## The adoption lint and the refusals

`rules_adopt.py` lints the file against the structures held in `positions.csv`. The lint codes:

| Code | Meaning |
| --- | --- |
| `not_accepted` | a rule still marked `example` |
| `owner_must_set` | a value the owner has still to decide |
| `uncovered_structure` | a held structure with no take-profit rule (a protective put and plain stock need none) |
| `take_profit_metric` | a take-profit rule that does not read the structure's own metric |
| `loss_on_short_call_leg` | a loss rule on the short call of a covered call or collar |
| `loss_metric` | a loss rule on a metric that is no loss metric for the structure |
| `duplicate_loss` | more than one loss rule on one held structure |
| `max_loss_over_100` | a `pct_of_max_loss` threshold above 100 |
| `metric_not_applicable` | a metric the named structure does not define |
| `input_unavailable` | a metric that reads a field the book does not have |
| `selects_nothing` | a leg rule naming a structure with no short option leg |

The refusals, reported together, with nothing written: `owner_phrase_mismatch`, `version_mismatch`,
`regular_trading_hours`, `lint_failed` and `immutable_version`. `--allow-uncovered` waives only
`uncovered_structure`, and the adoption records it. A synthetic refusal: the unmarked example file, its version
rewritten to 1.2.0, on a fixture copy after the close of 2026-11-03:

```text
refused: lint_failed: 29 lint findings block the adoption (not_accepted, owner_must_set)
```

## Change control, step by step

1. **The candidate file.** The Bot copies a rulebook to a candidate file in the desk root, never named
   `rules/exit_rules-*.toml`. For the first adoption it copies the example file; for a change, the adopted file, with
   a new `rulebook_version`.

   ```text
   mkdir -p /workspace/aegis-desk/rules
   cp /workspace/aegis-grok-desk/rules/examples/exit_rules-1.0.0.toml /workspace/aegis-desk/rules/candidate-1.0.0.toml
   cp /workspace/aegis-desk/rules/exit_rules-1.0.0.toml /workspace/aegis-desk/rules/candidate-1.1.0.toml
   ```

   The first line makes sure the folder exists; the second starts a first adoption; the third starts a change, whose
   `rulebook_version` line then reads `"1.1.0"`.

2. **The interview.** The Bot shows the owner each rule and its source. For a first adoption one answer is enough
   (SETUP step 9, "adopt my exit rules"): keep the examples as written, or name those to change or drop. It changes
   only what the owner states: an origin mark, a threshold, a deleted rule. A changed threshold is marked
   `owner-entered`; the lint does not check that mark, so the Bot sets it as the owner states. Nothing else in the
   file is touched.
3. **The dry run**, when a rulebook is already adopted. It writes nothing:

   ```text
   python3 /workspace/aegis-grok-desk/scripts/rules_diff.py --candidate /workspace/aegis-desk/rules/candidate-1.1.0.toml --desk-root /workspace/aegis-desk
   ```

   It exits 10 when a group's verdict would change. After the close, `--latest-snapshot` compares on the newest
   snapshot of record in `data/book/`, the book the adoption's digest also reads. From the fixture's candidate 1.1.0
   (TP50 at 60 %), on the regular quotes of 2026-11-03 at 19:50 UTC, after its first line:

   ```text
   rules: changed TP50; added none; removed none; 26 unchanged
   no group verdict changed (3 groups compared)
   verdicts changed: 0
   ```

4. **The owner's phrase and tap.** The owner types the phrase `adopt rules X.Y.Z` for the candidate's version. The
   Desk Lead runs the adoption with that exact phrase, and the owner taps approve on its Ask-first card:

   ```text
   python3 /workspace/aegis-grok-desk/scripts/rules_adopt.py --file /workspace/aegis-desk/rules/candidate-1.1.0.toml --version 1.1.0 --owner-phrase "adopt rules 1.1.0" --desk-root /workspace/aegis-desk
   ```

5. **Outside regular trading hours.** During a live session the adoption is refused, unless the owner asks for
   `--allow-rth`, which the CHANGELOG records. A Bot never adds `--allow-rth` or `--allow-uncovered` on its own.

   ```text
   refused: regular_trading_hours: regular trading hours (session open): an adoption waits for the close unless --allow-rth is given (recorded in rules/CHANGELOG.md)
   ```

6. **The adoption.** After the close on 2026-11-03 (22:00 UTC), with that day's snapshot of record in `data/book/`:

   ```text
   adopted rules 1.1.0, sha 131490c8, as rules/exit_rules-1.1.0.toml at 2026-11-03 22:00 UTC (session closed)
   previous: rules 1.0.0, sha cc09859c
   lint: clean for the held covered_call, protective_put
   rule state: no state file to carry forward
   rulebook changed: 0 verdicts changed
   written: rules/exit_rules-1.1.0.toml, rules/ADOPTED, rules/CHANGELOG.md, state/desk_state.json
   ```

7. **The digest and CONFIG CHANGED.** The next book check prints CONFIG CHANGED for the rulebook and the digest
   once. Then the owner taps `ack.py config --owner-tapped`, which the Desk Lead runs after the tap:

   ```text
   CONFIG CHANGED | adopted rulebook: sha cc09859c -> sha 131490c8 | evaluated with the files as they are now | acknowledge with ack.py config --owner-tapped
   rulebook changed: 0 verdicts changed | rules 1.0.0 -> rules 1.1.0, sha 131490c8
   ```

A first adoption has no previous rulebook: it prints `previous: none`, and without a snapshot
`no snapshot: digest unavailable`. Re-adopting exactly the adopted file writes nothing. A candidate's own verdicts,
without the comparison, come from a dry run that writes nothing:

```text
python3 /workspace/aegis-grok-desk/scripts/exit_check.py --rules /workspace/aegis-desk/rules/candidate-1.1.0.toml --dry-run --desk-root /workspace/aegis-desk
```

## Identity: shas, carry-forward and tamper detection

- `rules/ADOPTED` names the adopted version, the SHA-256 of the file's bytes, the instant, the SHA-256 of the owner
  phrase and every rule's own sha. It is written by `rules_adopt.py` only.
- An adopted version never changes. A candidate with other bytes under an adopted number is refused:

  ```text
  refused: immutable_version: rules/exit_rules-1.1.0.toml already holds other bytes (sha 131490c8): an adopted version never changes; give the candidate a new rulebook_version
  ```

- A rule's own sha covers everything that changes what it evaluates or how it confirms, re-arms and posts. It leaves
  out `title`, `origin`, `source` and `owner_must_set`. Accepting an example or rewording its title keeps its state.
- At adoption the rule state carries forward: an entry whose rule sha is unchanged is kept, a changed rule's entry is
  reset (its next observation is a first observation), and a removed rule's entry is dropped. From a copy that had
  posted on 2026-11-20, adopting 1.1.0 after the close: `rule state carried forward: 24 kept, 2 reset, 0 dropped`.
- An adopted file edited after its adoption is `rules_tampered`. The book check then evaluates nothing and exits 1.
  No Bot repairs it. The owner either puts back the exact adopted file or adopts a new version through the steps
  above.
- No Bot edits an adopted file, `rules/ADOPTED` or `rules/CHANGELOG.md`. The only rulebook file a Bot edits is the
  candidate, and only as the owner states.

## Rolling back

A rollback is an adoption like any other, through the same steps. Either re-adopt the earlier adopted file under its
own number (its bytes are unchanged), or adopt the earlier content under a new number. From the same copy, re-adopting
rules 1.0.0 at 22:30 UTC:

```text
adopted rules 1.0.0, sha cc09859c, as rules/exit_rules-1.0.0.toml at 2026-11-03 22:30 UTC (session closed)
previous: rules 1.1.0, sha 131490c8
```

## Not in this release

- A Rules Steward Bot is planned for Stage 3 (not in this release); the Desk Lead holds the duty until then.
- Replaying a candidate over past end-of-day snapshots (`rules_replay`) is planned for Stage 3 (not in this release).
- A rule holds no order shape and no destination of a roll. The grammar has no field for either.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.
