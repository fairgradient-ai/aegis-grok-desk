# Monitoring

What you can expect from the desk's routines: when they run, what they post and where, how to tell the desk is
running, how late an alert can be, and what the lean and active profiles cost. The schedule itself is
[routines/manifest.toml](../routines/manifest.toml); the routine rules for the Bots are in the skill
[aegis-routines-and-quiet-rules](../skills/aegis-routines-and-quiet-rules/SKILL.md).

## The lean schedule

Every Grok routine runs one command, the manifest's prompt with its slot filled in, and its Bot posts the output
verbatim:

```text
Run python3 /workspace/aegis-grok-desk/scripts/routine.py <id> --slot {slot} and post its output verbatim.
```

| Routine | Bot | America/New_York | What it runs |
| --- | --- | --- | --- |
| `preopen` | Aegis Desk Lead | 07:30, Mon-Fri | the quick doctor, the positions reconciliation, the week's expiry docket, then the Morning Desk Sheet |
| `book-check` | Aegis Exit Sentinel | 10:05, 12:05, 14:05, 15:35, Mon-Fri | the book check of your adopted rules; 10:05 also refreshes the threshold levels; 12:05 may post watch alive; 15:35 adds the pre-close line |
| `final-call` | Aegis Position Control | 15:20, Mon-Fri | the Final Call, the T-1 preview and the assignment watch |
| `eod` | Aegis Exit Sentinel | 16:35, Mon-Fri | the end-of-day book snapshot and `reports/book.md`, the threshold-level sheet from the snapshot, the reconciliation |
| `eod-brief` | Aegis Desk Lead | 17:10, Mon-Fri | the EOD Desk Brief |
| `post-expiry` | Aegis Position Control | 09:30, Saturday | the post-expiry reconciliation |

The weekday routines run on trading days only. No weekday routine runs between 08:00 and 10:00 ET, the window in which
platform queue delays are worst and the delayed quotes of the open are not in yet. `book-check` starts at least 20
minutes after `preopen`, and `eod-brief` at least 20 minutes after `eod` (`depends_on` with `min_gap_min = 20`).

There is no other routine. Acknowledging a signal, snoozing it or marking it acted is always yours, by a command; no
routine runs `ack.py`. The Aegis Structuring Desk owns no routine: `exit_ticket`, `roll_analysis` and `propose` are
refused as routine steps, and they run only when you ask.

## What posts and when

Each routine has a posting policy and one written quiet rule. The quiet rules, verbatim from the manifest:

- `preopen`: "Always one message: the doctor line, the recon verdict, standing signals, the docket and last night's
  levels; a missing or late upstream artifact is UNAVAILABLE."
- `book-check`: "Posts only a verdict change, a new rule firing, a standing UNAVAILABLE or a ladder re-post; 12:05
  posts watch alive when nothing posted since the open; 15:35 always posts the pre-close line."
- `final-call`: "Posts on an expiry day (act-by times), the day before a held contract's last trading day (T-1
  preview) and when a pin or dividend tier fires; otherwise quiet. On an early-close day it runs no step: the 07:30
  Morning Desk Sheet carries the early-close line."
- `eod`: "Always one message: the end-of-day threshold-level sheet from the snapshot."
- `eod-brief`: "Always one message: P/L today, MTD and YTD, signals, the docket, N of N routines ran with late and
  missed runs named, the owner's to-do list."
- `post-expiry`: "Posts only in weeks a held contract expired: the inferred outcomes and the number of proposed edits
  (csv_recon.py --diff shows the rows); confirm against your broker."

A routine with nothing to post prints the single word `quiet`, and its Bot posts that word. A closed day (a weekend,
an NYSE holiday, or a full closure you entered in `research/calendar.toml`) runs no step and prints `quiet`.

The 16:35 `eod` run posts only the THRESHOLD LEVELS sheet. The full book table goes to `reports/book.md` and is never
posted; your P/L per trade arrives in the 17:10 brief.

A book check posts on transitions only, so silence between runs means nothing changed. An EXIT SIGNAL you have not
acknowledged re-posts on the next book check, then three and seven runs after its first post: four posts at most,
never inside its rule's cooldown. A standing UNAVAILABLE banner re-posts on every run until you acknowledge it.
`ack.py ack <signal id>` stops the re-posts; the skill
[aegis-exit-watch-and-signal-protocol](../skills/aegis-exit-watch-and-signal-protocol/SKILL.md) has the details.

## Which chat says what

A routine's output lands in the chat of the Bot that owns it. Turn Notifications on for the three routine-owning Bots:
the Aegis Desk Lead, the Aegis Exit Sentinel and Aegis Position Control.

| Message | Chat |
| --- | --- |
| each EXIT SIGNAL, WATCH, UNAVAILABLE and CONFIG CHANGED message, the rulebook digest, the pre-close line, watch alive, THRESHOLD LEVELS | Aegis Exit Sentinel |
| FINAL CALL, T-1 PREVIEW, the assignment watch, the post-expiry reconciliation | Aegis Position Control |
| MORNING DESK SHEET, EOD DESK BRIEF | Aegis Desk Lead |
| An INCIDENT or WITHHELD line | the chat of the routine that printed it; a structuring tool's WITHHELD line, the Aegis Structuring Desk's |
| A ticket, a roll analysis or a proposal, in reply to your request only | Aegis Structuring Desk (its chat, or the Floor where you asked); no notification is needed, because it never posts unasked |

## Is the desk running?

- **Heartbeats.** Every run that holds its routine's lock writes `state/heartbeat/<id>.json` and appends to
  `state/heartbeat/history.jsonl`. A run is late when it starts more than 20 minutes after its slot; a slot is
  missed when no heartbeat exists 45 minutes after it. A late book check says so in its quote segment, for example
  `run 45 min late`.
- **The brief.** The 17:10 EOD Desk Brief names the day's late and missed runs in its `routines:` line. It counts the
  day's other routines, not itself.
- **The audit.** `python3 /workspace/aegis-grok-desk/scripts/routine_audit.py --desk-root /workspace/aegis-desk`
  prints the day audit. When every run of a day starts a constant 60 or 120 minutes off its slot, it reports
  "timezone offset suspected": a Bot's Timezone is not America/New_York.
- **Missed runs are never replayed.** The next run evaluates the market as it is then.

A synthetic example: the brief on a fixture copy at 2026-11-03 22:10 UTC, where only the `eod` routine had run:

```text
routines: 1 of 7 ran today; late: none; missed: preopen 07:30 ET, book-check 10:05 ET, book-check 12:05 ET, book-check 14:05 ET, final-call 15:20 ET, book-check 15:35 ET
```

**The owner-side dead-man rule.** On a trading day, no Morning Desk Sheet by 08:00 ET, or no pre-close line by 16:15
ET, means the desk is not running. Rely on your broker's own alerts, set from the last threshold-level sheet, and
answer any prompt that asks to keep the routines running. The platform pauses routines after a long absence, and the
desk computer sleeps when nothing wakes it, so these two anchors are how you know.

## How late can an alert be?

- The data is the Tradier sandbox feed, delayed 15 minutes. Every message names its source, the delay and the UTC
  time of its quotes.
- Grok Bot routines can start late: queue delays of 10 to 37 minutes are reported, worst between 08:00 and 12:00 ET.
  A run may be skipped during an outage, and it is not replayed.
- On the lean profile the book checks are about two hours apart.

Together, how late an alert can be depends on the kind of rule:

- A rule that reads only dates (class E, such as `DTE21`) fires at the next book check. On the lean profile its alert
  can trail by about three hours.
- A rule that reads a quote (class M or G, such as a take-profit, a loss or a delta rule) also waits for its
  confirmation (next section). On the lean profile a quote rule first true at the 10:05 check normally fires only at
  the 15:35 check, about five and a half hours later, plus the 15-minute data delay and any queue delay.
- On the active profile the extra book checks let a quote rule confirm during the session.

The desk is a level-setter and an interval checker, not a real-time watcher. The threshold-level sheet is the
real-time half: each of your rules as a price, which your broker's own alerts can watch.

## Confirmations on lean and active

Rules that read a quote (classes M and G, such as a take-profit or a delta rule) need two observations whose quote
times are at least 10 minutes apart, within 90 minutes of the newest, before they fire. One observation is enough when
the session is not live, or when the routine's next run falls after the close. Rules that read only dates (class E,
such as `DTE21`, `LAPSE14`, `LDTE30`, `EARN2` and the rule `ROLL30`) fire at once.

On the lean profile, consecutive book checks are about two hours apart, beyond the 90-minute window. So a pending M or
G rule normally confirms at the 15:35 run, where one observation is enough because the next run is after the close. A
pending rule shows on its group's message as a line such as (synthetic, 2026-11-03 19:50 UTC):

```text
pending confirmation: your rule DELTA_WATCH: absolute delta 0.48 (your threshold: at or above 0.30); observation 1 of 2
```

The active profile's extra book checks run hourly, then every 30 minutes in the afternoon, inside the window, so a
rule can confirm during the session. These are the example rulebook's defaults (`confirmations = 2`,
`confirmation_gap_min = 10`, `confirmation_window_min = 90`); they are yours to change through a rulebook adoption.

## The active block and the weekly budget

Every run spends weekly platform usage. `routine_audit.py --budget` prints the regular runs per week (holidays not
deducted):

```text
lean: 41 runs per week (about 8 runs per weekday plus Saturday)
active: 61 runs per week (lean plus four paused book-check slots, unpaused only on the conditions the owner chose)
```

The active profile is lean plus four Exit Sentinel book checks at 11:05, 13:05, 14:35 and 15:05, created paused at
setup. No tool switches the profile, and no tool measures usage per run. Switching is your choice, in three taps:

1. the Aegis Desk Lead runs `desk_init.py --replace --owner-tapped` with your full answers and
   `cadence_profile = "active"`;
2. it runs `ack.py config --owner-tapped`, because `desk.toml` changed;
3. the Aegis Exit Sentinel unpauses its four extra book-check routines.

Switching back is the reverse: the answers with `cadence_profile = "lean"`, the acknowledgement, and the four routines
paused again.

## Early closes and holidays

- On an NYSE holiday, a weekend or a full closure in `research/calendar.toml`, every routine prints `quiet`.
- On an early-close day (the session ends at 13:00 ET), `final-call` runs no step and prints `quiet`. There is no
  12:20 routine. The 07:30 Morning Desk Sheet carries the line `early close today at 13:00 ET: <n> contracts on their
  last trading day; <act-by text>`, and the previous trading day's 15:20 T-1 preview already prints the early act-by:
  the earlier of your broker's cutoff and 13:00 ET, marked `early close 13:00 ET: confirm your broker's cutoff`.
- The expiry week in full is in the skill
  [aegis-expiry-and-assignment-protocol](../skills/aegis-expiry-and-assignment-protocol/SKILL.md).

## Not in this release

Real-time alerts, notifications outside the Bot chats, automatic profile switching and backfilled runs are not in
this release. Running `routine.py` from a pinned clone on your own machine is described on the
[Grok Build page](aegis-grok-build.md).
