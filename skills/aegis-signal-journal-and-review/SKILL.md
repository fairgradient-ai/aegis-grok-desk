---
name: aegis-signal-journal-and-review
description: How to read and relay the signal journal and the weekly review, for the Aegis Review Desk. Each signal's facts from the desk's own records (posted time, rules, the owner's ack, acted or snooze mark, the group's end-of-day P/L at the mid, UNAVAILABLE days); the counts in fixed words; journal-close and journal/outcomes; the replay of a candidate over the week's snapshots and its limits; and counts only - no money total, rate, ranking or grade, enforced by the alert lint's review mode.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: review
---

# The signal journal and the weekly review

The journal and the review turn the desk's own records into facts about the owner's signals and rules. They never grade
the rules: the owner's answer to open question 6 is counts only.

## The records

- `journal/signals.jsonl`: one record per posted verdict transition (signal id, trade, structure, verdict, the rule
  results that made it, the post time), written by the book check.
- `state/acks.jsonl`: the owner's marks, written by `ack.py`: `ack` (acknowledged), `acted` (marked acted), `snooze`.
- `data/book/<ET date>.json`: the end-of-day snapshot of record, written by `book.py --snapshot` in the `eod` routine.

## A signal's facts (`signal_journal.py`)

One line per signal, starting with its id:

```text
SIG-20261102-001 | AG-20261020-01 covered_call | WATCH (DTE21) posted 2026-11-02 19:50 UTC | acknowledged 2026-11-02 20:10 UTC | P/L at the mid, end of day: 2026-11-02 +$1,145.00; 2026-11-03 +$1,145.00; 2026-11-04 UNAVAILABLE (no snapshot)
```

- The owner's mark is the latest `ack`, `acted` or `snooze` record that names the signal, else `open`.
- The marks are the group's `unrealized_pl_mid` in each end-of-day snapshot from the signal's day on, at most five
  trading days. A trading day without a usable snapshot is UNAVAILABLE; a group no longer in the snapshot is closed.
- `journal-close` (the Exit Sentinel's routine, 17:30 ET) writes the day's facts to `journal/outcomes/<ET date>.json`.

## The counts

Only counts, in fixed words, one line per rule and one for all rules:

```text
DTE21: 2 signals this week; 1 acknowledged, 0 marked acted, 0 snoozed
all rules: 3 signals this week; 1 acknowledged, 1 marked acted, 0 snoozed
```

A signal made by two rules counts once under each.

## The replay (`rules_replay.py`)

A candidate rulebook evaluated on each end-of-day snapshot beside the adopted one: the groups whose verdict would have
differed, day by day, and `verdict differed from your adopted rule on K of N trading days`. Its limits line always
comes with it: end-of-day only (an intraday crossing is invisible), each day one closed session with a scratch rule
state (confirmations across days, re-arm and cooldown are not carried), and no profit or loss.

## The weekly review (`weekly_review.py`, Saturday 11:00)

The week Monday to Friday: the signals part (facts, then counts) and the replay part (the newest
`rules/candidate-X.Y.Z.toml`, or a line saying there is none).

## The review mode of the alert lint

`alert_lint.review_findings` runs beside the strict lint. It refuses a currency amount on any line that is not a
per-signal fact line, and the phrases return, returns, performance, win rate, hit rate, success rate, saved,
avoided, outperform (any form) and total P/L, in any case. A review that fails is not printed.

## Never

- A sum, average or difference of money across signals or days; a rate; a ranking of rules; a money figure on a
  replay verdict.
- Saying a rule worked, failed, helped or should change: the owner decides, and the Aegis Rules Steward runs the change.
