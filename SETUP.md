# Set up Aegis Desk

This is the Stage-1 runbook. The Aegis Desk Lead, a Grok Bot, follows it top to bottom to build your read-only
options desk. You can read along: the steps marked **You** are yours, and the Lead never does them for you. The
Lead's own checklist for the same steps is the skill `aegis-desk-bootstrap-and-onboarding`.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

Setup is read-only. No brokerage credential is asked for, read or stored, and no order is created or sent. The only
credential is a Tradier sandbox market-data token, and only you add it.

How the commands below work:

- The release checkout is `/workspace/aegis-grok-desk`. The desk root, where the desk keeps its files, is
  `/workspace/aegis-desk`, outside the checkout.
- Every desk command passes `--desk-root /workspace/aegis-desk`. The tools read the current directory otherwise;
  only `routine.py` defaults to `/workspace/aegis-desk`. `routine.py` and `desk_init.py` refuse a desk root inside the
  checkout, because a file written there would make the next routine refuse to start.
- A command marked **owner-authority** runs only after you tap approve on its Ask-first card. The Lead never adds
  `--owner-tapped` or `--allow-rth` on its own.
- Every flag, output and exit code is in [docs/TOOLS.md](docs/TOOLS.md), generated from the tools themselves.
- Example output marked synthetic comes from the fixture desk in this repository (`tests/fixtures/desk` or
  `tests/fixtures/init`), copied first and run at the instant named. It is not market data.

## 0. Prove routines work on your plan

The desk needs Grok Bot routines. Before anything else, the Lead creates one paused test routine whose prompt is only
"Reply with the single word quiet.", reads back its name, schedule and paused state, deletes it, and reads back that
it is gone. If your plan has no routines, the desk cannot monitor anything. The Lead says so, notes it for the
receipt, and continues with the interactive steps only.

## 1. Get the pinned release onto the desk computer

```text
git clone --depth 1 --branch v0.1.0 https://github.com/fairgradient-ai/aegis-grok-desk.git /workspace/aegis-grok-desk
mkdir -p /workspace/aegis-desk
python3 /workspace/aegis-grok-desk/scripts/desk_doctor.py --desk-root /workspace/aegis-desk
```

- The tag `v0.1.0` is created when this release is published. If the clone says the tag does not exist, the release
  is not out yet: the Lead stops and tells you.
- If `/workspace/aegis-grok-desk` already exists, the Lead leaves it as it is (no overwrite, reset or switch) and
  reports what the doctor says about it.
- The doctor's check 1 (`release_pin`) is the pin check. On the clean release it passes with the reason
  `release v0.1.0 at <commit>, clean`. Its check 8 (`desk_files`) fails until step 9 is done; that is expected.

**You, only if git refuses the checkout.** When the doctor or a routine shows
`git refuses this checkout: it is owned by another user (see SETUP)`, approve exactly this command, run as the
account that runs the routines:

```text
git config --global --add safe.directory /workspace/aegis-grok-desk
```

The entry must be in that account's own `~/.gitconfig`. The pin check drops git's `GIT_*` environment variables,
so `GIT_CONFIG_GLOBAL` is ignored. No desk code writes git configuration. The Lead then runs the doctor
again.

## 2. Read the desk and run the gate

The Lead reads `README.md`, this file, the skill `aegis-desk-charter` and the four agent files. Then it runs the
repository gate once:

```text
cd /workspace/aegis-grok-desk
bash scripts/check.sh
```

It takes a few minutes and ends with `ok: all repository gates passed for /workspace/aegis-grok-desk`. Any failure
stops the setup. The gate is not run again on the desk computer. After setup, the routine checks are the doctor and
its engine selftest:

```text
python3 /workspace/aegis-grok-desk/scripts/desk_doctor.py --selftest --desk-root /workspace/aegis-desk
```

## 3. Prepare the desk computer (read-only)

- The desk root `/workspace/aegis-desk` exists from step 1. The tools create the folders they write into (`state/`,
  `data/`, `journal/`, `levels/`, `plans/`, `reports/`, `incidents/`, and `tickets/` at the first ticket). `desk.toml`,
  `positions.csv`, `rules/`, `research/` and `structuring.toml` come from the interviews in steps 8 and 9.
- Nothing is installed: the tools are standard-library Python, 3.9 or later. The doctor's check 2 confirms the
  interpreter.
- If you choose the token file instead of the secret card in step 8, it lives at
  `/workspace/.aegis-secrets/tradier.token`, outside both folders.
- **You:** never sign in to a brokerage in the Bot computer's browser.

## 4. Create the Stage-1 Bots

| File | Name | Job |
| --- | --- | --- |
| `agents/desk-lead.md` | Aegis Desk Lead | Head of the options desk |
| `agents/exit-sentinel.md` | Aegis Exit Sentinel | Exit-rule monitor of the options desk |
| `agents/position-control.md` | Aegis Position Control | Book-of-record and expiry controller of the options desk |
| `agents/structuring-desk.md` | Aegis Structuring Desk | On-request choices desk of the options desk |

- Each Bot gets its native Name and Job label from the table.
- Its standing instructions are its role skill, enabled for that Bot only: `aegis-role-desk-lead`,
  `aegis-role-exit-sentinel`, `aegis-role-position-control` or `aegis-role-structuring-desk`. A role skill is the agent
  file's System prompt, generated word for word.
- The Aegis Structuring Desk answers only when you ask. It never fires an alert and owns no routine.
- Each Bot also gets the desk skills its agent file lists under `skills:` (step 5).
- The Lead reads back each Bot's Name and Job.

The Aegis Book & Risk Analyst and the Aegis Events and Calendar Analyst are planned for Stage 2 (not in this release).
The Aegis Rules Steward and the Aegis Review Desk are planned for Stage 3 (not in this release).

## 5. Install the skills

Skills to install (Stage 1), with the Bots whose agent file lists them:

| Skill | Aegis Desk Lead | Aegis Exit Sentinel | Aegis Position Control | Aegis Structuring Desk |
| --- | --- | --- | --- | --- |
| `aegis-desk-charter` | yes | yes | yes | yes |
| `aegis-compliance-voice-and-number-discipline` | yes | yes | yes | yes |
| `aegis-routines-and-quiet-rules` | yes | yes | yes | yes |
| `aegis-market-data-budget-and-quality` | yes | yes | yes | yes |
| `aegis-desk-incident-playbooks` | yes | yes | yes | yes |
| `aegis-desk-bootstrap-and-onboarding` | yes | | | |
| `aegis-rules-dsl-and-change-control` | yes | yes | | |
| `aegis-book-marking-and-greeks-honesty` | | yes | | yes |
| `aegis-exit-watch-and-signal-protocol` | | yes | | |
| `aegis-threshold-levels` | | yes | | |
| `aegis-covered-call-and-cash-secured-put-management` | | yes | | yes |
| `aegis-protective-put-and-collar-management` | | yes | | yes |
| `aegis-vertical-spread-management` | | yes | | |
| `aegis-positions-csv-book-of-record` | | | yes | |
| `aegis-expiry-and-assignment-protocol` | | | yes | |
| `aegis-overlay-proposals` | | | | yes |
| `aegis-roll-decision-framework` | | | | yes |
| `aegis-ticket-and-unwind-order` | | | | yes |

What each one teaches is in [skills/README.md](skills/README.md).

Every skill is installed with a readback. The Lead reads back the stored name, description and body and compares them
with `skills/<name>/SKILL.md` in the checkout. `template/grok-bot.json` records the sha256 of each skill file (under
`skills`) and of each role skill (under `roleSkills`). Each skill gets one status for the receipt:

- `template`: already present from the template, and identical;
- `installed`: saved now, and the readback is identical;
- `pointer`: the product could not hold the body, so the stored text points to the checkout file;
- `mismatch`: the stored text differs or cannot be read back. That skill is not ready. A shared skill with the same
  name but a different body is never enabled.

<!-- role-skills:start -->
Role skills (generated from the agents' System prompts by `scripts/gen_role_skills.py`): `aegis-role-desk-lead`, `aegis-role-exit-sentinel`, `aegis-role-position-control`, `aegis-role-structuring-desk`
<!-- role-skills:end -->

## 6. Create the Aegis Options Floor

The Lead creates one private group, "Aegis Options Floor", with exactly the four Stage-1 Bots, and reads back its
members. A group holds two to six Bots, so the two Stage-2 seats fit later. Nothing is shared publicly.

Each Bot posts its own routine output in its own chat (see [docs/MONITORING.md](docs/MONITORING.md) for which chat
says what). The Aegis Structuring Desk answers in its own chat or in the Floor, and only in reply to you. Bots pass
information to each other through files and tool output on the desk computer, never through opinions.

## 7. Approvals and the connector check

**You: Notifications and Timezone.** Turn Notifications on for the three routine-owning Bots (the Aegis Desk Lead,
the Aegis Exit Sentinel and Aegis Position Control), and set Timezone to America/New_York on all three. The Aegis
Structuring Desk runs no routine and answers only when you ask, so it needs neither. No tool records these settings;
the Lead reads them back from you and reports them in the receipt.

**You: the Auto-review rules.** The Lead prints them from the routine manifest:

```text
python3 /workspace/aegis-grok-desk/scripts/routine_audit.py --rules-text --desk-root /workspace/aegis-desk
```

It prints `paste each rule exactly as printed, one rule per line`, then three rules. Paste each one exactly as an
Auto-review rule, one rule per line. On this release they are:

1. `allow` (506 of 1000 characters), as an "Allow automatically" rule:

   ```text
   Allow automatically: the command python3 /workspace/aegis-grok-desk/scripts/routine.py followed by exactly one routine id from this list and, optionally, --slot and a time HH:MM, with nothing else on the command line (no pipe, redirection, semicolon or second command): preopen, book-check, final-call, eod, eod-brief, post-expiry. These runs read delayed market data through the desk's GET-only sandbox client and write files only under /workspace/aegis-desk; they cannot place, modify or cancel an order.
   ```

2. `ask_first_writes` (315 of 1000 characters), as an "Ask first" rule:

   ```text
   Ask first: placing, modifying or cancelling an order; moving funds; any POST, PUT, PATCH or DELETE request to a brokerage, including any request to tradier.com/v1/accounts; using a connected app or MCP tool that can place or change orders; signing in to a brokerage; browsing outside the desk's sourcing allow-list.
   ```

3. `ask_first_owner_authority` (333 of 1000 characters), as an "Ask first" rule:

   ```text
   Ask first: any command that carries --owner-tapped or --allow-rth, including ack.py snooze, ack.py acted and ack.py config; rules_adopt.py; desk_init.py; exit_ticket.py, roll_analysis.py and propose.py. These record the owner's own decisions or answer the owner's own request: run them only after the owner taps approve on this card.
   ```

   It also covers the Aegis Structuring Desk's three tools: each run asks first, and your tap is the record that you
   asked.

**You: Connected apps and MCP servers.** Open both lists in your Grok account and read every entry back to the Lead.
They are account-wide: an app that can place or change orders would give every Bot that ability. None may be able
to. Disconnect such an app, or turn its order tools off. When you confirm, approve the Ask-first card for this
**owner-authority** command:

```text
python3 /workspace/aegis-grok-desk/scripts/ack.py connectors --owner-tapped --desk-root /workspace/aegis-desk
```

It records the readback in `state/desk_state.json` and `state/acks.jsonl` and prints (synthetic, a fixture copy at
2026-11-03 21:15 UTC):

```text
connector readback recorded at 21:15 UTC; desk_doctor re-asks after 30 days
```

The doctor's check 10 then passes for 30 days, for example
`[PASS] 10 connector_readback: connected apps and MCP servers confirmed 2026-11-03 (0 days ago)`. After 30 days it
warns `re-confirm connected apps and MCP servers` until you read the lists back again.

## 8. The desk interview, the sandbox token, the doctor and the live Opening Bell

The interview comes first: the doctor reads the token's variable and the market-data host from `desk.toml`.

**The desk interview.** The Lead asks one topic at a time and writes your answers as JSON to
`/workspace/aegis-desk/setup/desk-answers.json`. The shape is `desk_init` "Input" in [docs/TOOLS.md](docs/TOOLS.md):

- your accounts (a name and `cash`, `margin` or `ira` each) and your broker's exercise cutoff in ET, `HH:MM` (the
  act-by time of a long option; a short option acts by the session close, or this cutoff when earlier);
- your self-attested options approval level (0 to 5) in the desk's convention, declared equity, attention thresholds,
  the disclosures you acknowledged, the engagement, `chatty` or `quiet`, and the cadence profile, `lean` or `active`;
- the market data: host `sandbox` and the token variable `TRADIER_API_TOKEN` (or a token file);
- the risk-free rate, its source and its date.

The Lead shows the approval convention word for word:

Approval levels by the desk's convention (brokers number and define their levels differently; state yours in this convention, and give the lowest level across your accounts):

- `0` no options approval: the desk shows closing tickets only;
- `1` covered calls on shares held in the same account, and cash-secured puts;
- `2` level 1 plus long calls and long puts (protective puts, collars);
- `3` level 2 plus defined-risk spreads;
- `4` level 3 plus uncovered short options;
- `5` level 4 plus uncovered index options and other advanced strategies.

By the desk's convention, a closing transaction needs no approval (level 0); the broker's own rules apply.

The structuring tools read the approval level (the approval-level check, [docs/STRUCTURING.md](docs/STRUCTURING.md)).
The attention thresholds, `chatty_or_quiet`, the engagement and the disclosures are recorded for later stages; no tool
reads them in this release. After your tap, the **owner-authority** command:

```text
python3 /workspace/aegis-grok-desk/scripts/desk_init.py --answers /workspace/aegis-desk/setup/desk-answers.json --desk-root /workspace/aegis-desk
```

Synthetic, on a copy of the fixture answers:

```text
wrote desk.toml (962 bytes, sha256 f011fe058582203498b9082ec247cbfd388ac5b241d486ee26584c333eb91a94)
acknowledge with ack.py config --owner-tapped once positions.csv and the adopted rulebook are in place
```

A production host is refused (exit 1). Answers that would rewrite an existing `desk.toml` are refused unless you tap
`--replace --owner-tapped`, which rewrites the whole file from full answers.

**You: the sandbox token.** Add `TRADIER_API_TOKEN` through the secure secret card. Never paste it in chat. The
fallback is the file `/workspace/.aegis-secrets/tradier.token` with mode 0600 (`chmod 600`), named as
`market_data.token_file` in the answers.

**The full doctor.** The Lead runs it interactively:

```text
python3 /workspace/aegis-grok-desk/scripts/desk_doctor.py --desk-root /workspace/aegis-desk
```

- Check 4 (`token_source`) names where the token came from, never its value.
- Check 5 (`sandbox`) makes one GET of the sandbox clock. A 401 or 403 fails with
  `not a sandbox token: remove and rotate`: remove that token and add a sandbox one.
- Check 8 still fails until step 9. Synthetic, a fixture copy after the interview and before step 9:
  `[FAIL] 8 desk_files: desk.toml loads (sha f011fe05); positions.csv missing; not_adopted: no rules/ADOPTED: no rulebook has been adopted; research/calendar.toml missing`

This interactive run is the sandbox verification. The routines run the quick doctor, which has no sandbox check.

**The live Opening Bell.** Once the doctor has verified the token, the Lead posts a public market snapshot verbatim:

```text
python3 /workspace/aegis-grok-desk/scripts/opening_bell.py --symbol SPY --desk-root /workspace/aegis-desk
```

With the token in the file, it adds `--token-file /workspace/.aegis-secrets/tradier.token`. Without a token it prints
`UNAVAILABLE: no_token: no market-data token: environment variable TRADIER_API_TOKEN is unset or empty` and exits 20.
Before the token exists, the Lead may show only the recorded synthetic snapshot, labelled as such (the command in
[README.md](README.md), "Try it now").

## 9. Positions, dividends, the rulebook and your structuring criteria

**positions.csv.** The file is yours: the book of record, one row per position leg. Its format is in
[docs/POSITIONS_CSV.md](docs/POSITIONS_CSV.md).

1. You supply the file's exact text.
2. On your explicit request, Aegis Position Control saves those exact bytes to `/workspace/aegis-desk/positions.csv`.
   It is a verbatim transcription: never an edit, never rows it derived.
3. It runs the check and the reconciliation and reports the verdict, the number of rows and the file's sha256:

   ```text
   python3 /workspace/aegis-grok-desk/scripts/positions_check.py --desk-root /workspace/aegis-desk
   python3 /workspace/aegis-grok-desk/scripts/csv_recon.py --desk-root /workspace/aegis-desk
   ```

   Synthetic, the fixture file (2026-11-03, 19:50 and 21:20 UTC):

   ```text
   CLEAN: 7 rows, 4 groups (3 open, 1 closed), 0 quarantined; plausibility passed for 6 open legs
   snapshot: data/positions/2026-11-03.csv sha256 9483c8f3dffd4b7ad4b3358a6a9b7b453829a85f6b4f8ac246a96f16ae60afd3
   ```

4. You confirm the verdict and the sha256. Errors come back to you with the exact row and message.

Later changes follow the same path, with rows you supply or approve. The next book check then posts CONFIG CHANGED
until you tap `ack.py config --owner-tapped`, so every change is visible.

**The dividend intake.** For every held symbol, short calls first: the next ex-dividend date, the amount and
`confirmed` or `estimated`, or `none` with your source. A symbol you cannot answer is left out: the desk then reports
it UNAVAILABLE, never zero. The Lead writes `/workspace/aegis-desk/setup/dividends.json` with only `ex_dividends`, and
after your tap runs the **owner-authority** command again:

```text
python3 /workspace/aegis-grok-desk/scripts/desk_init.py --answers /workspace/aegis-desk/setup/dividends.json --desk-root /workspace/aegis-desk
```

Synthetic, the fixture's three symbols at 2026-11-03 21:25 UTC:
`wrote research/calendar.toml (687 bytes, sha256 36f6304c9dda43b801851193ad34168d15dd9a40d4cbb21e588286edf08a5746)`.

- Earnings dates, settlement values, closures and dividend yields are yours to add to `research/calendar.toml` by
  hand.
- A later `desk_init.py --replace --owner-tapped` rewrites the whole calendar, so a re-intake re-enters every entry.
  A merging intake is planned for Stage 2 (not in this release).
- `python3 /workspace/aegis-grok-desk/scripts/calendar_check.py --desk-root /workspace/aegis-desk` reports entries
  checked more than 7 days ago as STALE. That finding is expected until you re-confirm them.

**The rulebook interview.** The Lead copies the example rules to a candidate file and changes only what you state:

```text
mkdir -p /workspace/aegis-desk/rules
cp /workspace/aegis-grok-desk/rules/examples/exit_rules-1.0.0.toml /workspace/aegis-desk/rules/candidate-1.0.0.toml
```

- Rule by rule, the Lead shows the rule's id, title, threshold, plan and source. You keep it as written
  (`origin = "example-accepted"`), change its threshold (`origin = "owner-entered"`), or leave it out (it is deleted
  from the candidate). A rule left at `origin = "example"` is refused.
- `DAILY` and `CONC` need a value you enter, or `owner_must_set = false` to keep the documented default.
- Every structure you hold needs a take-profit rule that covers it; stock and a protective put need none.
- Adoption runs outside regular trading hours, after your tap, with the phrase you type. The **owner-authority**
  command:

```text
python3 /workspace/aegis-grok-desk/scripts/rules_adopt.py --file /workspace/aegis-desk/rules/candidate-1.0.0.toml --version 1.0.0 --owner-phrase "adopt rules 1.0.0" --desk-root /workspace/aegis-desk
```

Synthetic, a fixture copy with every rule accepted as written. At 2026-11-03 19:50 UTC (session open) it refused:

```text
refused: rules 1.0.0 was not adopted; nothing was written
refused: regular_trading_hours: regular trading hours (session open): an adoption waits for the close unless --allow-rth is given (recorded in rules/CHANGELOG.md)
```

At 22:00 UTC, after the close:

```text
adopted rules 1.0.0, sha cc09859c, as rules/exit_rules-1.0.0.toml at 2026-11-03 22:00 UTC (session closed)
previous: none
lint: clean for the held covered_call, protective_put
rule state: no state file to carry forward
no snapshot: digest unavailable
written: rules/exit_rules-1.0.0.toml, rules/ADOPTED, rules/CHANGELOG.md, state/desk_state.json
```

The rules, their grammar and later changes are in [docs/EXIT_RULES.md](docs/EXIT_RULES.md) and the skill
`aegis-rules-dsl-and-change-control`.

**Your structuring criteria.** The Aegis Structuring Desk prices your choices for a position you hold only when you
ask, and only by criteria you set in `/workspace/aegis-desk/structuring.toml`. Without that file it shows nothing, so
you may skip this block and do it at your first request instead. The Structuring Desk runs the interview in its own
chat:

1. It shows each section of the criteria (the covered call, the replacement of a short call, the protective put and
   the collar) with its documented defaults. You keep a value, change it within its bounds, or switch a structure off.
   The defaults are starting points, not a judgment that they fit your account; the full table is in
   [docs/STRUCTURING.md](docs/STRUCTURING.md).
2. Last, it shows the one-time disclosure `structuring-suggestions-1` word for word. It records your acknowledgement
   only after you say that you acknowledge it.
3. It writes your answers to `/workspace/aegis-desk/setup/structuring-answers.json` and, after your tap, runs the
   **owner-authority** command:

```text
python3 /workspace/aegis-grok-desk/scripts/desk_init.py --answers /workspace/aegis-desk/setup/structuring-answers.json --desk-root /workspace/aegis-desk
```

Synthetic, the fixture answers `tests/fixtures/init/structuring-answers.json` at 22:03 UTC:

```text
wrote structuring.toml (1090 bytes, sha256 5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c)
acknowledge with ack.py config --owner-tapped: the structuring tools show nothing until structuring.toml is acknowledged
```

An existing `structuring.toml` is refused unless you tap `--replace --owner-tapped`, which rewrites the whole file
from full answers. The doctor's check 8 then adds `structuring.toml loads (sha 5155427c)`.

**The configuration acknowledgement.** Last, after your tap, the **owner-authority** command:

```text
python3 /workspace/aegis-grok-desk/scripts/ack.py config --owner-tapped --desk-root /workspace/aegis-desk
```

Synthetic, the same copy at 22:05 UTC:

```text
configuration acknowledged at 2026-11-03 22:05 UTC: desk.toml sha f011fe05, positions.csv sha 9483c8f3, adopted rulebook 1.0.0 sha cc09859c, structuring.toml sha 5155427c
```

Without `structuring.toml` the line ends after the rulebook. From now on, a change to `desk.toml`, `positions.csv`, the
adopted rulebook or `structuring.toml` that you have not acknowledged posts CONFIG CHANGED, and the structuring tools
answer UNAVAILABLE while `desk.toml`, `positions.csv` or `structuring.toml` differ from what you acknowledged.

## 10. Offer the routines, verify, turn on monitoring

The schedule is `routines/manifest.toml`. Each owning Bot creates its own Grok routines: one routine per slot, all
created **paused**, each read back (name, schedule, prompt, paused). Every prompt is the manifest's template with the
slot filled in, for example:

```text
Run python3 /workspace/aegis-grok-desk/scripts/routine.py book-check --slot 10:05 and post its output verbatim.
```

| Bot | Routines on the lean profile (America/New_York) |
| --- | --- |
| Aegis Desk Lead | `preopen` 07:30 and `eod-brief` 17:10, Mon-Fri |
| Aegis Exit Sentinel | `book-check` 10:05, 12:05, 14:05 and 15:35, and `eod` 16:35, Mon-Fri |
| Aegis Position Control | `final-call` 15:20 Mon-Fri, and `post-expiry` 09:30 Saturday |

That is nine routines. The Aegis Structuring Desk creates none: its tools are refused as routine steps. The active
profile adds four Exit Sentinel book-check routines at 11:05, 13:05, 14:35 and 15:05, created paused. The weekly cost in
runs:

```text
python3 /workspace/aegis-grok-desk/scripts/routine_audit.py --budget --desk-root /workspace/aegis-desk
```

On this release it prints, among its lines (holidays not deducted):

```text
lean: 41 runs per week (about 8 runs per weekday plus Saturday)
active: 61 runs per week (lean plus four paused book-check slots, unpaused only on the conditions the owner chose)
```

**One Test run.** The Lead runs a Test of its `preopen` routine. It proves that routines see the token and the desk
files. The Lead reports whether the run raised an Ask-first card; the Allow rule should cover it. Preopen runs the
quick doctor (checks 2, 3, 4, 8 and 9), which has no sandbox check: step 8 verified the sandbox. On the desk's first
day some lines are UNAVAILABLE, because no earlier run has written their records yet. Synthetic, a fresh fixture copy
at 2026-11-03 12:35 UTC:

```text
MORNING DESK SHEET 2026-11-03
doctor (state/doctor.json, quick check at 12:35 UTC): PASS, 1 warning: token_source
positions.csv reconciliation (data/positions/2026-11-03.recon.json): CLEAN, 0 proposed edits (csv_recon.py --diff lists them)
open signals: 0
expiry docket (reports/expiry/2026-11-03.week.json): 0 contracts due
UNAVAILABLE: levels sheet levels/2026-11-02.json missing
deterministic rules you adopted · posted by an AI assistant · not personalized investment advice · data delayed 15 min, as of 20:59 UTC
```

The heartbeat and the day audit read back the run:

```text
python3 /workspace/aegis-grok-desk/scripts/heartbeat.py --list --desk-root /workspace/aegis-desk
python3 /workspace/aegis-grok-desk/scripts/routine_audit.py --desk-root /workspace/aegis-desk
```

Synthetic, the same copy at 12:40 UTC:

```text
heartbeat preopen: unavailable (exit 20), run 2026-11-03T12:35:00+00:00, age 5 min, lateness 5 min
routine audit Tue 2026-11-03 (profile lean, as of 12:40 UTC): 1 of 1 due slots ran
```

**You: turn on monitoring.** When you say "turn on monitoring", each Bot unpauses its own lean routines and reads
them back. Then the Lead runs the full doctor once more; its heartbeat check warns until the routines have run.

Switching to the active profile later is your choice, in three taps; [docs/MONITORING.md](docs/MONITORING.md) has the
steps.

## 11. Return the receipt

The Lead returns one message with the evidence of each step:

- the release and the pin check line; the `check.sh` result;
- the four Bots by Name and Job; each skill and its status; the Floor and its members;
- Notifications and Timezone on each routine-owning Bot, as you reported them;
- the three Auto-review rules pasted, and whether the Test run raised a card;
- the connector readback line; the token source (check 4) and the sandbox check (check 5);
- the `desk.toml` sha256, the `positions.csv` verdict, rows and sha256, the calendar written, the rulebook version and
  sha, the `structuring.toml` sha256 (or that you chose to set your criteria later), and the configuration
  acknowledgement line;
- the routines created, paused or on, and the doctor's verdict line;
- the dead-man rule: no Morning Desk Sheet by 08:00 ET, or no pre-close line by 16:15 ET, on a trading day means the
  desk is not running, and you rely on your broker's own alerts;
- every pending item with its manual step.

Its last line is exactly:

```text
Setup stayed read-only: one sandbox market-data token was added by you; no order was created or sent.
```

## Later stages (planned)

- The Aegis Book & Risk Analyst and the Aegis Events and Calendar Analyst, with their skills and routines, are planned for Stage 2 (not in this release).
- The Aegis Rules Steward and the Aegis Review Desk, by direct message, are planned for Stage 3 (not in this release).

When asked for any of them, a Bot says it is not available in this release.

Runbook structure adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased changes, commit 75a4c79 (SETUP.md); see [ATTRIBUTION.md](ATTRIBUTION.md).
