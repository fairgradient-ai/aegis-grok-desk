---
name: aegis-desk-incident-playbooks
description: What each failure of the read-only Aegis desk looks like in the desk's own words and the read-only response, for every Stage-1 Bot. INCIDENT, WITHHELD, UNAVAILABLE and CONFIG CHANGED lines and incident files; a structuring answer that cannot be given (no criteria, unacknowledged files, a withheld text, a chain not served, a closed session); a blind desk (vendor, quota, wrong key or host); an invalid positions.csv at the open; a changed rulebook; an unexpected configuration change; a checkout that is not the pinned release or that git refuses; a computer that slept through the open; a Bot out of its lane; and the owner's check-your-broker-directly list.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: operations
---

# Desk incident playbooks

When something fails, the desk says so in its own fixed words, and the response is always read-only. Nothing in this
skill writes a desk file, retries in a loop, switches to another feed or repairs anything automatically. There is no
automatic recovery, no retry schedule and no failover feed. Messages reach the owner only in the Bots' chats; there is
no outside notification channel.

Three rules hold in every incident:

1. Quote the line the tool printed. Never reword it, add a number, or guess a cause the tool did not name.
2. UNAVAILABLE is a verdict. While an input is unavailable there is no HOLD, and nothing is "probably fine".
3. The fix is the owner's: a file, a key, a git setting or a routine. A Bot never edits a desk file to make an
   incident go away.

## How an incident shows up

| Line | Meaning |
| --- | --- |
| `INCIDENT \| <id> <slot> ET \| <tool> failed (<code>); the other steps ran; details in incidents/<file>` | one step of a routine failed; the rest of the message stands |
| `INCIDENT \| <id> \| the desk checkout is not the pinned release v0.2.2: <reason>; no step ran \| ...` | the pin check refused the run |
| `INCIDENT \| <id> \| another run of this routine holds its lock; this run did nothing` | two runs overlapped |
| `INCIDENT \| <id> \| the routine did not run (routine_crashed): ...` | an unexpected error; a traceback is in `incidents/<stamp>-<id>-crash.md` |
| `WITHHELD \| <tool>: the text failed the alert lint (<codes>) and is withheld; <tool>.py shows it` | one part failed the lint and was not printed |
| `INCIDENT \| <id> \| the routine message failed the alert lint (<codes>) and is withheld` | the whole message failed the lint |
| `UNAVAILABLE ...` | an input a verdict needs is missing or stale |
| `CONFIG CHANGED \| ...` | a configuration file differs from the acknowledged one |
| `UNAVAILABLE \| no structuring.toml: ...` | a structuring tool found no criteria file (exit 20, `criteria_missing`) |
| `UNAVAILABLE \| CONFIG CHANGED: ...` | a structuring tool found `desk.toml`, `positions.csv` or `structuring.toml` unacknowledged (exit 20, `config_unacknowledged`) |
| `WITHHELD \| the text of <tool>.py failed the alert lint (<codes>) and is withheld; ...` | a structuring text failed its lint (exit 1, `lint_failed`) |
| `REFUSED ...` | a structuring answer needs a level above the owner's self-attested one (exit 20) |

Incident files are `incidents/<stamp>-<id>-<slug>.md` in the desk root, with `-dirty-checkout.md` for a pin refusal and
`-crash.md` for a crash. The 17:10 EOD Desk Brief names the day's files in its line
`incidents today: <n> (...); details in incidents/`. A Bot reads an incident file only to tell the owner what it
says, and quotes it only when the owner asks.

## Blind desk: vendor down, quota, wrong key or host

When quotes cannot be had, every rule that needs one is unavailable, and the book check posts a standing banner. From
a synthetic book check on the stale set (2026-11-03 19:50 UTC, the first lines of two groups):

```text
UNAVAILABLE AG-20261020-01 | WMT covered call | rules 1.0.0, sha cc09859c: no verdict while an input is unavailable; this is not HOLD | quote grade F, Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 18:50 UTC, run 45 min late | not an instruction to trade; you act in your brokerage account.
unavailable: your rule TP80: row 4 WMT261120C00105000: quote grade F (stale): no fresh ask
UNAVAILABLE AG-20261022-02 | NVDA covered call | rules 1.0.0, sha cc09859c: no verdict while an input is unavailable; this is not HOLD | quote grade F, Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 18:50 UTC, run 45 min late | not an instruction to trade; you act in your brokerage account.
unavailable: your rule DELTA_WATCH: row 6 NVDA261120C00150000: no Greeks (iv_status unavailable)
```

What the cause looks like:

- **Quota.** The quote cache marks the symbols `quota_guard` and the run stops fetching.
- **Vendor or network failure.** The symbols carry `fetch_failed:<status>`, for example `fetch_failed:network`.
- **No key.** `UNAVAILABLE: no market-data key: add the Alpaca paper key ID and secret through the secret card (or
  the acknowledged credentials file)`.
- **Not a paper key.** The full doctor's check 5 FAILs with `not a paper key: remove it and add a paper key`: a live
  key, or a wrong or deleted one. A live key can trade, so the owner deletes both secrets at once.
- **Plan limit.** The symbols carry `fetch_failed:subscription`: Alpaca refused a request the free plan does not
  include, and the run stops fetching.
- **A host other than `paper` in `desk.toml`.** `desk.toml` no longer loads: the doctor's check 8 FAILs with
  `[market_data] host must be "paper" (got "live"): the desk holds a paper key only, and the live trading host is
  refused`, and every market tool stops with exit 1.

The response:

1. The Bot posts nothing of its own about prices or verdicts. The routine's banner is the message.
2. The Desk Lead runs the full doctor once, `python3 scripts/desk_doctor.py --desk-root /workspace/aegis-desk`, and
   tells the owner the failing check's line. It does not run the doctor or any market tool in a loop: each run spends
   the shared 200-per-minute budget.
3. A key problem is the owner's: the owner deletes both secrets, generates a new paper key pair in the Alpaca
   dashboard (Paper Trading) and adds it through the secret card. A host other than `paper` is fixed only by the
   owner's `desk_init.py --replace --owner-tapped` run with the paper host.
4. The owner checks the broker directly (the list at the end) until the banners clear.

## Invalid positions.csv at the open

A typo in the header stops the whole file. A synthetic pre-open run on a fixture copy whose header says
`cost_basis_totl` (2026-11-03 12:35 UTC):

```text
INCIDENT | preopen 07:30 ET | csv_recon failed (positions_invalid); the other steps ran; details in incidents/20261103T123500.000000Z-preopen-csv_recon.md
INCIDENT | preopen 07:30 ET | expiry_checklist failed (positions_invalid); the other steps ran; details in incidents/20261103T123500.000000Z-preopen-expiry_checklist-week.md
MORNING DESK SHEET 2026-11-03
doctor (state/doctor.json, quick check at 12:35 UTC): FAIL, 1 warning: key_source; 1 failure: desk_files
positions.csv reconciliation (data/positions/2026-11-03.recon.json): ERRORS, 0 proposed edits (csv_recon.py --diff lists them)
```

The response:

1. Position Control runs `python3 scripts/positions_check.py --desk-root /workspace/aegis-desk` and quotes its first
   line, for example
   `ERRORS: positions.csv cannot be used: row 1 did_you_mean: unknown column 'cost_basis_totl': did you mean 'cost_basis_total'?`
2. The owner corrects the file, or sends its exact corrected text for the verbatim save
   (`aegis-positions-csv-book-of-record`).
3. Position Control runs `positions_check.py` again until it prints CLEAN.
4. The next book check posts CONFIG CHANGED until the owner acknowledges with `ack.py config --owner-tapped`.

While the file is unusable no rule is evaluated: the owner checks the broker directly.

## A changed rulebook (rules_tampered)

The adopted rulebook `rules/exit_rules-X.Y.Z.toml` never changes. If its bytes differ from the sha256 recorded in
`rules/ADOPTED`, every book check stops. From a synthetic copy whose adopted file gained one line:

```text
INCIDENT | book-check 14:05 ET | book_check failed (rules_tampered); the other steps ran; details in incidents/20261103T195000.000000Z-book-check-book_check-post.md
```

and `book_check.py` run directly prints (the copy's path shortened to `<desk root>`):

```text
error: <desk root>/rules/exit_rules-1.0.0.toml: the file's sha256 1fa8da1e is not the adopted cc09859c: the rulebook changed after adoption
```

No rule is evaluated and no alert can fire until it is fixed. The Desk Lead tells the owner plainly. The owner either
puts back the exact adopted file, or adopts a new version through `rules_adopt.py` with the owner's typed phrase,
outside regular trading hours (`aegis-rules-dsl-and-change-control`). No Bot edits an adopted
`rules/exit_rules-X.Y.Z.toml`, `rules/ADOPTED` or `rules/CHANGELOG.md`; the only rulebook file a Bot edits is the
candidate, and only as the owner states.

## CONFIG CHANGED you did not expect

Every book check compares `desk.toml`, `positions.csv`, the adopted rulebook and, when it exists,
`structuring.toml` with the hashes the owner last acknowledged, and posts this first on every run until they match. On a fixture copy with nothing acknowledged
(synthetic):

```text
CONFIG CHANGED | desk.toml: not acknowledged -> sha f08a18dc | positions.csv: not acknowledged -> sha 9483c8f3 | adopted rulebook: not acknowledged -> sha cc09859c | evaluated with the files as they are now | acknowledge with ack.py config --owner-tapped
```

The response: the Desk Lead asks the owner whether they changed the named file. If they did, the owner taps the
Ask-first card and the Lead runs `python3 scripts/ack.py config --owner-tapped --desk-root /workspace/aegis-desk`. If
they did not, nobody acknowledges it: an unexplained change is an incident, and the owner looks at the file first. The
Lead never acknowledges a change on the owner's behalf.

## The checkout is not the pinned release, or git refuses it

Every routine first checks that `/workspace/aegis-grok-desk` is the clean commit of tag v0.2.2. If not, no step runs.
From a development checkout (synthetic):

```text
INCIDENT | preopen | the desk checkout is not the pinned release v0.2.2: development checkout (no tag); no step ran | details in incidents/20261104T123500.000000Z-dirty-checkout.md
```

The reasons the pin check can give include `the checkout has <n> uncommitted or untracked path(s)`, `HEAD <commit> is
not the commit of v0.2.2 (<commit>)` and `git refuses this checkout: it is owned by another user (see SETUP)`.

- **Uncommitted or untracked paths.** Something wrote into the checkout. The Lead lists the paths with the read-only
  `git -C /workspace/aegis-grok-desk --no-optional-locks status --short` and reports them; the owner decides. A desk
  root inside the checkout is refused by `routine.py` and `desk_init.py` for this reason. Other tools, `ack.py`
  included, write wherever `--desk-root` points, so every interactive command passes `--desk-root /workspace/aegis-desk`.
- **git refuses the checkout.** Only when an INCIDENT or the doctor shows `git refuses this checkout: it is owned by
  another user (see SETUP)`, the owner approves exactly this command, run as the account that runs the routines:

```text
git config --global --add safe.directory /workspace/aegis-grok-desk
```

  The pin check drops `GIT_*` variables, so the entry must be in that account's `~/.gitconfig`; `GIT_CONFIG_GLOBAL` is
  ignored. No desk code writes git configuration.
- **The development header.** The line `development run: the checkout is not verified (--allow-dirty)` must never
  appear on the desk computer: no routine prompt carries `--allow-dirty`. If it does appear, the Lead reports which
  routine printed it.

## The computer slept through the open

Missed runs are never replayed and never backfilled; the next run evaluates the market as it is then. The brief and
the audit name the gaps. From a synthetic fixture copy where no routine ran on 2026-11-03 (the brief at 17:10 ET):

```text
routines: 0 of 7 ran today; late: none; missed: preopen 07:30 ET, book-check 10:05 ET, book-check 12:05 ET, book-check 14:05 ET, final-call 15:20 ET, book-check 15:35 ET; pending: eod 16:35 ET
```

The owner-side dead-man rule: no Morning Desk Sheet by 08:00 ET, or no pre-close line by 16:15 ET, on a trading day
means the desk is not running. The owner relies on the broker's own alerts, set from the last threshold-level sheet,
and answers any prompt that asks to keep the routines running. The Lead runs
`python3 scripts/routine_audit.py --desk-root /workspace/aegis-desk` for the full day audit.

## Withheld text

A WITHHELD line means a tool's text failed the alert lint, so the desk did not post it. The Bot never re-types or
paraphrases the withheld text. The tool the line names shows it interactively, as a diagnostic, when the owner asks;
the incident file records the codes, never the text.

## A structuring answer that cannot be given

The Structuring Desk's tools fail closed: on any doubt about the files they print one line and the structuring
footer, name no candidate and print no ticket. From synthetic fixture copies, at 19:50 UTC and, for the last line,
with the after-close quotes at 21:30 UTC:

```text
UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your criteria (SETUP step 9)
UNAVAILABLE | CONFIG CHANGED: structuring.toml: not acknowledged -> sha 5155427c | the desk names no candidate and prints no ticket while desk.toml, positions.csv or structuring.toml differ from what you acknowledged | acknowledge with ack.py config --owner-tapped
UNAVAILABLE | AG-20261020-01 roll analysis | an option chain in your window was not served
```

- **No criteria.** The Structuring Desk offers its interview (`aegis-overlay-proposals`); nothing else is priced
  until `structuring.toml` exists and is acknowledged.
- **Unacknowledged files.** The line names each file. The Desk Lead asks the owner whether they changed it; the
  acknowledgement is the Lead's step after the owner's tap, never a Bot's own. An unacknowledged rulebook alone does
  not block: its CONFIG CHANGED line prints first and the answer follows.
- **Withheld.** The structuring text failed its lint and was not printed. Nobody retypes it; `--json` of the same
  tool shows it as a diagnostic when the owner asks.
- **A chain not served.** The quota, the per-run cap of 40 requests or 4 chains, or a vendor failure left an
  expiration of the window unpriced, so the answer is UNAVAILABLE: no best candidate on partial data. An empty
  expiration list from the vendor counts as missing too. The owner asks again later; no Bot repeats the run in a loop.
- **No candidate priced.** `no candidate in your window could be priced`, with the reason the candidates share: the
  answer is UNAVAILABLE, never NONE_QUALIFIES, because no candidate was judged against the criteria.
- **A quarantined group.** `positions.csv has a quarantined group (positions_check.py shows why): the desk cannot
  count the account's coverage`. While any group of `positions.csv` is quarantined, every structuring answer is
  UNAVAILABLE. Aegis Position Control shows the row errors with `positions_check.py`; the owner fixes the rows and
  acknowledges the file again.
- **A closed session.** A ticket needs live quotes: `a ticket needs quotes live enough to act on: the session is not
  open`. A roll analysis or a proposal after the close reads the closing quotes, labelled as of the close.

## A Bot out of its lane

Each question has one owning Bot. A Bot asked about another's file says who owns it and hands off in the Aegis Options
Floor; it never answers with another Bot's numbers.

| The question is about | Owning Bot |
| --- | --- |
| signals, alerts, the threshold-level sheet, the book snapshot | Aegis Exit Sentinel |
| `positions.csv`, the expiry week, assignment and settlement | Aegis Position Control |
| `desk.toml`, the rulebook, the doctor, whether the desk ran | Aegis Desk Lead |
| the owner's choices: tickets, the roll analyses and proposals, `structuring.toml` | Aegis Structuring Desk, only on the owner's request |

A request for a planned seat's work gets the answer that it is not available in this release. Book and risk
analysis and sourced events are planned for Stage 2 (not in this release); rule stewardship and the review desk are
planned for Stage 3 (not in this release).

## The owner's checklist: check your broker directly

When the desk is blind, late, refused or out of step, the brokerage account is the truth:

1. Open the brokerage account and read the positions and any open orders there.
2. Check the broker's own price alerts, set from the last threshold-level sheet (`levels/<date>.md`).
3. Check each expiring contract and the broker's exercise cutoff, especially on an early-close day.
4. Treat every desk number as 15 minutes delayed at best, and older when the desk says so.
5. Tell the Desk Lead what changed, so `positions.csv` can be brought up to date.

The desk never places, modifies or cancels orders; in an incident, as always, the owner acts in the brokerage account.
