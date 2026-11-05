---
name: aegis-exit-watch-and-signal-protocol
description: How the Aegis book check decides and posts, for the Aegis Exit Sentinel. The run order of book_check, three-valued rules and the verdict precedence, transitions-only posting with the one HOLD notice, confirmations and what they mean on the lean profile, re-arm and the cooldown that ends at the next session's open, row tags, UNAVAILABLE as a standing banner, acknowledgements with snooze and acted, the re-post ladder, late-run disclosure, the pre-close and watch-alive lines and the exit codes.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: exits
---

# The exit watch and signal protocol

`book_check.py` decides every verdict from the rules the owner adopted. The Aegis Exit Sentinel posts what the routine
printed and explains it in the tool's own words. This skill says how the tool decides, so the Sentinel can answer
"why did this post?" and "why did nothing post?" without a number or a verdict of its own.

The examples are synthetic: tool runs on copies of `tests/fixtures/desk` at the instants named beside them.
[docs/TOOLS.md](../../docs/TOOLS.md) is the authority on every flag, output and exit code.

## One run, in order

The `book-check` routine runs `book_check.py --post` through `routine.py`, which adds the slot (`--scheduled-et`) and
the routine's next run (`--next-run-utc`). One run, in this order:

1. The book: one quotes request, graded in the session of the run instant, with the calendar's full closures.
2. The adopted rulebook that `rules/ADOPTED` names. Its bytes, its version and every per-rule sha must match the
   adoption. If they do not, the run stops with `rules_tampered` and evaluates nothing.
3. The evaluation: every adopted rule against every open group, with the rule state in `state/rule_state.json` and
   the row tags and opened dates in `positions.csv`.
4. The transitions of the rule state.
5. The posting rules (below).
6. The message: every part linted on its own, then the whole message linted under one footer.
7. With `--post`, the records: `journal/signals.jsonl`, `journal/alerts.jsonl`, the day's `journal/<date>.md`,
   `state/rule_state.json`, the last verdicts in `state/desk_state.json` and `state/heartbeat/book-check.json`.

On a day without a session (a weekend, an NYSE holiday, a full closure in `research/calendar.toml`) the run prints
`NYSE closed (<reason>); no evaluation`, fetches nothing and exits 0. Without `--post` the run is a dry run: its first
line is `dry run`, its exit code is the one a posted run would return, and it writes none of the desk's records.

```text
python3 /workspace/aegis-grok-desk/scripts/book_check.py --desk-root /workspace/aegis-desk
```

Never run `book_check.py --post` by hand. Only the routine posts, so the rule state records exactly what was posted.

## Three-valued rules

- Each condition of a rule reads TRUE, FALSE or UNKNOWN. UNKNOWN means an input the rule needs cannot be read now: a
  stale quote, no Greeks, no usable mark of the underlying.
- A rule is the conjunction of its conditions. It is FALSE if any condition is FALSE, else UNKNOWN if any is UNKNOWN,
  else TRUE. A rule whose date condition is already false therefore stays quiet even while its quote is stale.
- A metric that cannot speak for a reason of record is not applicable, and the rule is clear: a group without a stop
  in `positions.csv`, a confirmed absence of earnings, a contract gone before the last cum-dividend day.
- Each rule ends in one result: `fired`, `pending` (waiting for a confirmation), `cooldown` (true again before it
  re-arms), `unavailable`, `no_history` (it needs the desk's previous end-of-day snapshot), `suppressed` (a row tag)
  or `clear`.

## The verdict precedence

The precedence is EXIT_SIGNAL, then UNAVAILABLE, then WATCH, then HOLD.

- A fired rule whose verdict is EXIT_SIGNAL makes the group's verdict EXIT_SIGNAL, even while another rule cannot be
  read. Every other fired and unavailable rule is listed under it in the same message.
- Otherwise one applicable rule that cannot be read makes the group UNAVAILABLE. HOLD never stands while an
  applicable rule is unavailable.
- Otherwise a fired WATCH rule makes it WATCH. Otherwise the group is HOLD.
- A `pending` or `cooldown` result counts for no verdict.
- A group that `positions_check.py` quarantines is UNAVAILABLE with its reasons.
- `no_history` results and the underlying and portfolio rules are reported in their own lines. They never set a
  group's verdict and never change the exit code.

A synthetic example (stale quotes, the 14:05 slot, 2026-11-03 19:50 UTC): DTE21 fired, but TP80 cannot be read, so
the group is UNAVAILABLE and the fired rule is listed under the banner.

```text
UNAVAILABLE AG-20261020-01 | WMT covered call | rules 1.0.0, sha cc09859c: no verdict while an input is unavailable; this is not HOLD | quote grade F, Tradier sandbox, 15-min delayed, as of 18:50 UTC, run 45 min late | not an instruction to trade; you act in your brokerage account.
unavailable: your rule TP80: row 4 WMT261120C00105000: quote grade F (stale): no fresh ask
unavailable: your rule USTOP: underlying WMT: no usable mark (quote grade F)
also fired: your rule DTE21: time to expiry 13 trading days (your threshold: at or below 21 trading days) | already past this threshold at the desk's first observation | your plan: review a roll
suppressed by your row tags: DELTA_WATCH, DELTA_SIGNAL, CAP
```

## When a message posts: transitions only

- A group posts a new signal when its verdict changes: a WATCH, an EXIT_SIGNAL or an UNAVAILABLE begins or replaces
  another verdict. The first sight of a group at HOLD is not a change.
- A group also posts a new signal when one of its rules newly fires more than that rule's `cooldown_min` after its
  last post.
- A group back at HOLD after a posted verdict gets one HOLD notice, once. It is not a signal.
- A verdict that stands, a pending rule and a rule in cooldown post nothing. Silence between runs means nothing
  changed.
- The portfolio section (the underlying and portfolio rules) posts its WATCH on the same rules. It never makes a group
  UNAVAILABLE and never sets the exit code.

The parts of one message, in order: CONFIG CHANGED; the rulebook-changed digest, once after an adoption; each group
that posts, in evaluation order, then the portfolio; the pre-close line at 15:35; the watch-alive line at 12:05 when
nothing else posts. Under a group's first line, these lines may follow: `also fired:`, `pending confirmation:`,
`unavailable:`, `suppressed by your row tags:` and `no history:`.

The HOLD notice, on 2026-11-03 (a copy where a stale-quote run posted at 19:40 UTC and the regular quotes were read at
19:46 UTC):

```text
HOLD AG-20261028-03 | SPY protective put | rules 1.0.0, sha cc09859c: none of your rules fires now; was UNAVAILABLE | not an instruction to trade; you act in your brokerage account.
```

## Confirmations, and what they mean on the lean profile

- Each rule has an input class. Class E reads dates and day counts and never waits: in the example rulebook,
  DTE21, LAPSE14, LDTE30, EARN2 and ROLL30 fire at once. Class M reads quote marks and class G reads Greeks. M and G rules
  need confirmations.
- The example defaults: `confirmations = 2`, `confirmation_gap_min = 10`, `confirmation_window_min = 90`. So an M or G
  rule fires when two observations have quote stamps at least 10 minutes apart, both within 90 minutes of the newest.
  A re-run on the same quotes is never a second confirmation.
- One observation suffices when the session is not live, or when the routine's next run falls after the close.
  `routine.py` passes the routine's next slot of either profile as `--next-run-utc`. Only the 15:35 slot has its
  next run after the close.
- On the lean profile, book checks run at 10:05, 12:05, 14:05 and 15:35, 90 to 120 minutes apart, at or beyond the
  90-minute window. A pending M or G rule therefore normally confirms at 15:35, where one observation suffices
  because the next run falls after the close.
- The active profile adds 11:05, 13:05, 14:35 and 15:05. Those slots allow an intraday confirmation.

A pending rule, from the 14:05 slot on 2026-11-03 (regular quotes, 19:50 UTC):

```text
pending confirmation: your rule DELTA_WATCH: absolute delta 0.48 (your threshold: at or above 0.30); observation 1 of 2
```

On the expiry-day quotes (2026-11-20, 19:50 UTC), the 15:35 routine fired TP80 and DELTA_SIGNAL on one observation.
The level sheet runs with no next run: on a fresh copy at the same instant it shows TP80 as
`threshold already crossed (pending confirmation: observation 1 of 2)`.

## Re-arm and the cooldown

- A rule that fired and then cleared waits in `cooldown` until it re-arms. True again before that, it reports its
  values and posts nothing. A condition that flaps across its threshold posts one signal, then the HOLD notice once,
  then nothing until the rule re-arms.
- `rearm = "next_trading_day"` (the example's default) re-arms at 00:00 ET of the next trading day. `"immediate"` re-arms at
  the next observation. `"hysteresis"` re-arms once the value has retreated by `hysteresis_pct` percent of the
  threshold, and never later than the next-trading-day instant.
- `cooldown_min` (240 in the example) is the minimum time between two posts of one group and rule. A re-arm waits for
  it, but the hold always ends at the latest at the session open (09:30 ET) of the first trading day after the post's
  date, closures skipped. A long cooldown therefore never keeps an exit rule silent into the next session.
- The re-post ladder of a standing signal still waits for the full `cooldown_min`.

## Row tags and the first observation

- A row tag silences a rule only when the rule names that tag in its `suppress_if_tag` key. In the example rulebook,
  `#assign-ok` on a row of the group silences EXDIV, DELTA_WATCH, DELTA_SIGNAL and CAP, and `#earnings-ok` silences
  EARN2. PIN3 carries no `suppress_if_tag` there: near the strike, assignment stays uncertain. The owner's adopted
  rulebook may differ, so read its `suppress_if_tag` keys before you explain a silence.
- A silenced rule's values are still computed and reported. It never fires and records no state. The line reads
  `suppressed by your row tags: DELTA_WATCH, DELTA_SIGNAL, CAP`.
- A rule first seen already true, on a group opened before today, posts with the sentence
  `already past this threshold at the desk's first observation`. A real condition is never silently skipped.

## UNAVAILABLE is a standing banner

- UNAVAILABLE is a verdict. It is never silence and never HOLD.
- A standing UNAVAILABLE re-posts on every run, under its signal id, until the owner acknowledges it. A snooze holds it
  until its `--until`.
- An acknowledged banner stays quiet until the verdict changes. The run then prints `quiet` and still exits 20:
  the code states the data, and the acknowledgement only stops the post. The 15:35 pre-close line still counts it.

```text
SIG-20261103-001 | AG-20261020-01 covered_call | UNAVAILABLE | first posted 2026-11-03 19:50 UTC | posted 1 time; re-posted on every run until acknowledged | open
```

## Acknowledgements, snooze and acted

```text
python3 /workspace/aegis-grok-desk/scripts/ack.py list --desk-root /workspace/aegis-desk
python3 /workspace/aegis-grok-desk/scripts/ack.py show SIG-20261120-001 --desk-root /workspace/aegis-desk
python3 /workspace/aegis-grok-desk/scripts/ack.py ack SIG-20261120-001 --desk-root /workspace/aegis-desk
python3 /workspace/aegis-grok-desk/scripts/ack.py snooze SIG-20261120-001 --until "15:30 ET" --owner-tapped --desk-root /workspace/aegis-desk
python3 /workspace/aegis-grok-desk/scripts/ack.py acted SIG-20261120-001 --owner-tapped --desk-root /workspace/aegis-desk
```

- `list` and `show` only read. `ack` records that the owner has seen the signal: it needs no tap and stops the
  re-posts, both the EXIT SIGNAL ladder and a standing banner. Run `ack.py ack` only when the owner says, in chat,
  that they have seen this signal; never on your own initiative and never to quiet a banner.
- `snooze` keeps the signal open but quiet until `--until`, an ISO-8601 UTC instant or `HH:MM ET` today.
- `acted` records that the owner acted in the brokerage account, with a hash of the group's rows. While those rows
  stay unchanged a trading day later, the reconciliation reports `ACTED_UNCHANGED`: the owner records the change in
  `positions.csv` with Aegis Position Control.
- `snooze` and `acted` need `--owner-tapped`. The Sentinel runs them only after the owner taps approve on the
  Ask-first card, never on its own initiative and never from a routine. No routine runs `ack.py`.

The replies on a copy after the expiry-day post (2026-11-20, 19:55 and 19:56 UTC):

```text
SIG-20261120-001 snoozed until 2026-11-20 20:30 UTC (AG-20261020-01 covered_call)
SIG-20261120-002 recorded as acted on at 2026-11-20 19:56 UTC (AG-20261022-02 covered_call); rows sha ea13f8e3: csv_recon reports ACTED_UNCHANGED while the group's rows in positions.csv stay unchanged
```

## The re-post ladder

- An unacknowledged EXIT SIGNAL, whose group is still EXIT_SIGNAL, re-posts under its signal id on the next run of
  its routine after the first post, then three runs and seven runs after it: four posts at most.
- It never re-posts inside its primary rule's `cooldown_min` after the last post. The runs are counted only outside
  that cooldown, so `ack.py list` names the earliest instant.
- WATCH signals have no ladder. The 17:10 EOD Desk Brief lists unacknowledged signals in the owner's to-do list.

From `ack.py list` after the expiry-day 15:35 run (2026-11-20, 19:51 UTC):

```text
SIG-20261120-001 | AG-20261020-01 covered_call | EXIT_SIGNAL, your plan: close | first posted 2026-11-20 19:50 UTC | posted 1 of 4 times; next re-post 1 run after the first post, not before 2026-11-20 23:50 UTC (rule TP80 cooldown 240 min) | open
SIG-20261120-003 | AG-20261028-03 protective_put | WATCH, your plan: review a roll | first posted 2026-11-20 19:50 UTC | posted 1 time; no re-post ladder for WATCH | open
```

## Late runs, the pre-close line and watch alive

- A routine can start late. `--scheduled-et` gives the run's lateness, printed in the quote segment, for example
  `as of 19:31 UTC, run 45 min late`. A missed run is never replayed: the next run reads the market as it is then.
- The 15:35 run always posts the pre-close line. From the expiry-day run above:

```text
pre-close | verdicts: 2 EXIT SIGNAL, 0 UNAVAILABLE, 1 WATCH, 0 HOLD | open signals: 4 | data age: 18 min | rules 1.0.0, sha cc09859c | verify live quotes in your broker before acting
```

- The 12:05 run posts the watch-alive line only when no linted message has posted since the open. From a copy whose
  only earlier post was before the open (`book_check.py --post --watch-alive`, 2026-11-03 19:50 UTC):

```text
watch alive | nothing posted since the open | rules 1.0.0, sha cc09859c | data age: 18 min
```

- The owner-side dead-man rule: no pre-close line by 16:15 ET on a trading day means the desk is not running. The
  owner then relies on the broker's own alerts, set from the last threshold-level sheet.

## CONFIG CHANGED and the rulebook digest

- CONFIG CHANGED comes first in every run while `desk.toml`, `positions.csv` or the adopted rulebook file differs
  from the hashes the owner acknowledged. The run still evaluates the files as they are now.
- The digest posts once after an adoption. From a copy where rules 1.1.0 was adopted at 22:00 UTC on 2026-11-03
  (the dry run at 22:05 UTC):

```text
CONFIG CHANGED | adopted rulebook: sha cc09859c -> sha 131490c8 | evaluated with the files as they are now | acknowledge with ack.py config --owner-tapped
rulebook changed: 0 verdicts changed | rules 1.0.0 -> rules 1.1.0, sha 131490c8
```

- `ack.py config --owner-tapped` is the Aegis Desk Lead's step, after the owner's tap. The Sentinel never runs it.

## The exit codes

| Code | Meaning for a book check |
| --- | --- |
| 10 | the message holds an EXIT SIGNAL, new or re-posted |
| 20 | a group is UNAVAILABLE and none is EXIT_SIGNAL (the banners, or `quiet` once each is acknowledged) |
| 0 | quiet or informational: WATCH, a HOLD notice, CONFIG CHANGED, the digest, the pre-close and watch-alive lines, a closed day |
| 1 | an error, such as `rules_tampered`, `lint_failed`, `positions_invalid` or `lock_busy` |
| 2 | usage: invalid arguments |

The sections and the extra lines under a group never change the exit code.

## What this skill never allows

- A verdict, a softened verdict or a confirmation decided by a Bot. The tool decides; the Sentinel posts.
- HOLD in place of UNAVAILABLE, or silence in place of a standing banner.
- A claim of real-time watching. The desk checks at fixed slots on data delayed 15 minutes.
- `ack.py snooze`, `ack.py acted` or `ack.py config` without the owner's tap.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.
