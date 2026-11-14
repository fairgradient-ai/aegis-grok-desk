---
name: aegis-routines-and-quiet-rules
description: How the Aegis desk's routines run on Grok Bot, for every Bot. The platform's limits as reported, routines/manifest.toml as the schedule, one reviewed routine.py command per run posted verbatim, the written quiet rules, heartbeats and the day audit with no backfill, stale upstream records refused, the re-post ladder, the lean and active profiles with their weekly budget, Timezone America/New_York and the owner-side dead-man rule.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: desk
---

# Routines and quiet rules

## What Grok Bot routines can and cannot do

These are the platform's limits as reported by its documentation and staff; the desk does not measure them.

- Routines run at least 5 minutes apart, plus up to about 10 minutes of randomisation.
- A Bot holds at most 50 routines; 20 run records are kept; at most 300 runs an hour.
- A routine's output lands in the chat of the Bot that owns it.
- Queue delays of 10 to 37 minutes are reported, worst between 08:00 and 12:00 ET.
- A missed run is not replayed. Routines pause themselves after a long absence of the owner.
- Routines need a Pro-or-higher plan, and every run spends weekly usage.
- The desk computer sleeps minutes after the app disconnects, so only routine-woken jobs that run to completion work.
- An approval card raised by a routine expires after 30 minutes.

So the desk is an interval checker and a level-setter, not a real-time watcher. The data is delayed 15 minutes on top
of any queue delay.

## The manifest is the schedule

[routines/manifest.toml](../../routines/manifest.toml) lists every routine: its id, the owning Bot, the stage, the
America/New_York slots per cadence profile, the days, whether it runs on trading days only, the step chain, the tools
whose text it may post, the posting policy, the written quiet rule and the prompt. `bash scripts/check.sh` checks it:

- no weekday slot falls in the delay window from 08:00 to 10:00 ET (08:00 inside, 10:00 outside);
- a routine with `depends_on` runs at least 20 minutes after the routine it depends on (`min_gap_min` 20 or more);
- a chain holds only the eight step tools: `assignment_watch`, `book`, `book_check`, `csv_recon`, `desk_doctor`,
  `eod_summary`, `expiry_checklist` and `trigger_levels`;
- `ack` and `routine_audit` are never steps, and neither are the Structuring Desk's three tools: a manifest step
  naming `exit_ticket` or `roll_analysis` or `propose` is refused with the reason
  `owner-requested only: a structuring text names candidates and never runs in a routine`;
- no chain carries `--owner-tapped`, `--allow-rth`, `--diff` or `--allow-dirty`;
- every prompt is the one template below;
- each Auto-review text in `routines/autoreview.toml` stays within 1,000 characters, and the Allow text names every
  routine id.

## One reviewed command per run

One Grok routine runs one slot. Its prompt is exactly the template, with the routine id and the slot filled in:

```text
Run python3 /workspace/aegis-grok-desk/scripts/routine.py <id> --slot {slot} and post its output verbatim.
```

The Allow-automatically rule covers exactly that command and nothing else: no pipe, no redirection, no second
command. The Ask-first owner-authority rule names `desk_init.py` and the Structuring Desk's three tools, so each
interactive run of them raises a card for the owner's tap. What `routine.py` does in one run:

1. It checks the release pin: the checkout must be the clean commit of tag v0.3.0. Otherwise no step runs.
2. It picks the slot (`--slot`, else the latest slot at or before now) and the slot's chain.
3. On a closed day it runs no step and prints `quiet`: a weekend, an NYSE holiday, or a full closure the owner entered
   in `research/calendar.toml`. The final-call routine also prints `quiet` on an early-close day.
4. It runs each step once, in order, in the same process. A failed step never stops the others.
5. It composes one message, lints it, writes the heartbeat and a run record under `reports/routines/<date>/`, and
   prints the message.

Post the output verbatim: every line, in order, the footer included. The single word `quiet` means there was
nothing to post, and it is posted as printed. Never run `routine.py` with `--allow-dirty`: that flag is for
development checkouts only, and no routine prompt carries it.

The posting policies: `always` posts every post step's text; `transitions` posts the book check's message when it has
one; `fired` posts each post step that exited 10 or 20, and is otherwise quiet.

Lines a routine prints in place of a part, each ending with the fixed footer (shapes from `scripts/routine.py`):

```text
INCIDENT | <id> <slot> ET | <tool> failed (<error code>); the other steps ran; details in incidents/<file>
WITHHELD | <tool>: the text failed the alert lint (<codes>) and is withheld; <tool>.py shows it
INCIDENT | <id> | another run of this routine holds its lock; this run did nothing
INCIDENT | <id> | the desk checkout is not the pinned release v0.3.0: <reason>; no step ran | details in incidents/<stamp>-dirty-checkout.md
```

A synthetic example of the last one, from a development checkout with no tag (a fixture copy, 2026-11-03 19:50 UTC):

```text
INCIDENT | book-check | the desk checkout is not the pinned release v0.3.0: development checkout (no tag); no step ran | details in incidents/20261103T195000.000000Z-dirty-checkout.md
```

## The Stage-1 routines per Bot

| Bot | Grok routines (lean profile) | Slots (America/New_York) |
| --- | --- | --- |
| Aegis Desk Lead | `preopen`, `eod-brief` | 07:30 and 17:10, Mon-Fri |
| Aegis Exit Sentinel | `book-check` (four routines), `eod` | 10:05, 12:05, 14:05, 15:35 and 16:35, Mon-Fri |
| Aegis Position Control | `final-call`, `post-expiry` | 15:20 Mon-Fri, and 09:30 Saturday |
| Aegis Structuring Desk | none: it answers only when the owner asks | none |

The active profile adds four book-check routines on the Exit Sentinel, at 11:05, 13:05, 14:35 and 15:05. They are
created paused. Each owning Bot creates its own routines, reads them back to the owner, and keeps them paused until
the owner turns monitoring on.

The 10:05 book check also refreshes the threshold levels, the 12:05 one may post the watch-alive line, and the 15:35
one always posts the pre-close line. The 16:35 eod run snapshots the book, builds the threshold-level sheet from the
snapshot and reconciles the positions file. There is no 12:20 routine on early-close days.

## Quiet rules (verbatim)

Every routine has one written quiet rule in the manifest:

- preopen: "Always one message: the doctor line, the recon verdict, standing signals, the docket and last night's
  levels; a missing or late upstream artifact is UNAVAILABLE."
- book-check: "Posts only a verdict change, a new rule firing, a standing UNAVAILABLE or a ladder re-post; 12:05 posts
  watch alive when nothing posted since the open; 15:35 always posts the pre-close line."
- final-call: "Posts on an expiry day (act-by times), the day before a held contract's last trading day (T-1
  preview) and when a pin or dividend tier fires; otherwise quiet. On an early-close day it runs no step: the 07:30
  Morning Desk Sheet carries the early-close line."
- eod: "Always one message: the end-of-day threshold-level sheet from the snapshot."
- eod-brief: "Always one message: P/L today, MTD and YTD, signals, the docket, N of N routines ran with late and
  missed runs named, the owner's to-do list."
- post-expiry: "Posts only in weeks a held contract expired: the inferred outcomes and the number of proposed edits
  (csv_recon.py --diff shows the rows); confirm against your broker."

## Heartbeats, the day audit and missed runs

- Every run that holds its routine's lock writes `state/heartbeat/<id>.json` and appends to
  `state/heartbeat/history.jsonl`, a pin refusal included. The book check's `book_check.py --post` step writes its own
  heartbeat; `routine.py` writes it for every other routine.
- A run is late when it starts more than 20 minutes after its slot. A slot is missed when no heartbeat exists 45
  minutes after it.
- A late run says so in its quote segment, for example `run 45 min late`.
- Missed runs are never backfilled and never replayed. The next run evaluates the market as it is then.
- The EOD Desk Brief names the day's late and missed runs in its `routines:` line; the brief's own run is not
  counted. `routine_audit.py` gives the full audit:

```text
python3 /workspace/aegis-grok-desk/scripts/routine_audit.py --desk-root /workspace/aegis-desk
```

A synthetic example on a fresh fixture copy at 2026-11-03 22:10 UTC, where no routine had run (the first two lines):

```text
routine audit Tue 2026-11-03 (profile lean, as of 22:10 UTC): 0 of 8 due slots ran; missed: preopen 07:30 ET, book-check 10:05 ET, book-check 12:05 ET, book-check 14:05 ET, final-call 15:20 ET, book-check 15:35 ET; pending: eod 16:35 ET, eod-brief 17:10 ET
  preopen 07:30 ET: missed (no heartbeat 45 min after the slot)
```

When every run of a day starts a constant 60 or 120 minutes off its slot, the audit reports "timezone offset
suspected": the owning Bot's Timezone is not America/New_York.

## Upstream late: consumers refuse stale records

A routine reads the records earlier routines wrote: the morning sheet reads the doctor's result, the reconciliation,
the docket and last night's levels; the brief reads the day's book snapshot and journals. A record that is missing,
or older than the day it should be from, is never reused. Its line becomes UNAVAILABLE:

```text
UNAVAILABLE: <label> is from <date> (upstream late)
UNAVAILABLE: <label> <path> missing
```

## The re-post ladder

- An unacknowledged EXIT SIGNAL re-posts on the next book-check run after its first post, then three runs and seven
  runs after it: four posts at most. It never re-posts inside its primary rule's `cooldown_min` after the last post.
- WATCH signals have no ladder.
- A standing UNAVAILABLE banner re-posts on every run until the owner acknowledges it.
- `ack.py ack <signal id>` stops the re-posts. No routine ever runs `ack.py`. Run `ack.py ack` only when the owner
  says, in chat, that they have seen this signal; never on your own initiative and never to quiet a banner.

## Cadence profiles and the weekly budget

```text
python3 /workspace/aegis-grok-desk/scripts/routine_audit.py --budget --desk-root /workspace/aegis-desk
```

On this release's manifest it prints, among its lines (holidays are not deducted):

```text
lean: 41 runs per week (about 8 runs per weekday plus Saturday)
active: 61 runs per week (lean plus four paused book-check slots, unpaused only on the conditions the owner chose)
```

- `desk.toml`'s `cadence_profile` names the profile, `lean` or `active`. `active` is strictly additive: lean plus
  the four paused book-check slots.
- No tool switches the profile and no tool measures usage per run. Switching is the owner's choice: the Desk Lead
  runs `desk_init.py --replace --owner-tapped` with the full answers and `cadence_profile = "active"`, then
  `ack.py config --owner-tapped`, and the Exit Sentinel unpauses its four extra routines. Switching back is the
  reverse.

## Timezone and the owner-side dead-man rule

- Each routine-owning Bot (the Desk Lead, the Exit Sentinel and Position Control in Stage 1) has its Timezone set to
  America/New_York and notifications on. Both are set when the owner says "turn on monitoring" (SETUP step 9) and read
  back in that step's receipt; no tool records them. The Aegis Structuring Desk owns no routine, so it needs neither.
- The owner-side dead-man rule: no Morning Desk Sheet by 08:00 ET, or no pre-close line by 16:15 ET, on a trading
  day means the desk is not running. The owner then relies on the broker's own alerts, set from the last
  threshold-level sheet, and answers any prompt that asks to keep the routines running.
