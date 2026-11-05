---
name: aegis-desk-bootstrap-and-onboarding
description: The staged Stage-1 setup of the read-only Aegis options desk on Grok Bot, for the Aegis Desk Lead. Step 0 proves routines exist; then the pinned release and its checks, the three Stage-1 Bots, the Aegis Options Floor and the skills with a readback each, Notifications, Timezone, the connector and MCP readback, the Auto-review rules, the desk interview, the sandbox token and the full doctor, the live Opening Bell, positions.csv, the dividend intake, the rule-by-rule rulebook interview, the routines offered paused, and the receipt. Use for a first setup, a repair or a readiness check.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: desk
---

# Desk bootstrap and onboarding

Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), commit 75a4c79; see ATTRIBUTION.md.

The Aegis Desk Lead follows these steps in order to build the owner's Stage-1 desk. Each step ends with evidence: a
tool line, a readback, or the owner's own confirmation. A step that cannot be done is reported with the exact manual
step for the owner, and every step that depends on it stays pending. Nothing is ever reported as done on a guess.

## Safety boundary for setup

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

- Setup is read-only. No brokerage credential is asked for, read or stored, and no order is created or sent.
- The only credential is the Tradier sandbox market-data token. The owner adds it through the secure secret card; it
  is never asked for or pasted in chat.
- Local writes stay under `/workspace/aegis-grok-desk` (the release checkout) and `/workspace/aegis-desk` (the desk
  root). Creating the three Bots and one private group is in scope; sharing anything publicly is not.
- Owner-authority commands (`desk_init.py`, `rules_adopt.py`, `ack.py config`, `ack.py connectors`) run only after the
  owner taps approve on the Ask-first card. The Lead never adds `--owner-tapped` or `--allow-rth` on its own.
- Every desk tool the Lead runs interactively passes `--desk-root /workspace/aegis-desk`.

## Step 0: prove routines exist

Before anything else, the Lead creates one paused test routine whose prompt is only "Reply with the single word
quiet.", reads back its name, schedule and paused state, deletes it, and reads back that it is gone. If routines are
not available on the owner's plan, the desk cannot monitor anything: the Lead says so, records it for the receipt, and
continues only the interactive steps.

## 1. Get and verify the pinned release

```text
git clone --depth 1 --branch v0.1.0 https://github.com/fairgradient-ai/aegis-grok-desk.git /workspace/aegis-grok-desk
mkdir -p /workspace/aegis-desk
cd /workspace/aegis-grok-desk
bash scripts/check.sh
python3 scripts/desk_doctor.py --desk-root /workspace/aegis-desk
```

- If `/workspace/aegis-grok-desk` already exists, the Lead keeps it as it is: no overwrite, reset or switch. It reports
  what the doctor's check 1 says about it.
- If the clone fails because the tag does not exist, the release is not published yet: stop and tell the owner.
- `bash scripts/check.sh` runs once, here. It takes a few minutes and ends with
  `ok: all repository gates passed for /workspace/aegis-grok-desk`. Any failure stops the setup. After setup, the
  routine checks are `python3 /workspace/aegis-grok-desk/scripts/desk_doctor.py --selftest --desk-root /workspace/aegis-desk`
  and the doctor itself.
- The doctor's check 1 (`release_pin`) PASSes with `release v0.1.0 at <commit>, clean`. At this point its check 8
  FAILs (no `desk.toml` yet); that is expected until step 7.
- If check 1 says `git refuses this checkout: it is owned by another user (see SETUP)`, the owner approves exactly
  `git config --global --add safe.directory /workspace/aegis-grok-desk`, run as the account that runs the routines,
  and the Lead runs the doctor again. No desk code writes git configuration.

## 2. Create the Stage-1 Bots, the Floor and the skills

- From `agents/desk-lead.md`: Name "Aegis Desk Lead", Job "Head of the options desk".
- From `agents/exit-sentinel.md`: Name "Aegis Exit Sentinel", Job "Exit-rule monitor of the options desk".
- From `agents/position-control.md`: Name "Aegis Position Control", Job "Book-of-record and expiry controller of the
  options desk".

- Each Bot gets its native Name and Job label from the list above. Its standing instructions are its role skill
  (`aegis-role-desk-lead`, `aegis-role-exit-sentinel` or `aegis-role-position-control`), enabled for that Bot only.
- Each Bot also gets the desk skills its agent file lists under `skills:`.
- The Lead creates one private group, "Aegis Options Floor", with exactly these three Bots, and reads back its
  members. A group holds two to six Bots, which leaves room for the seats that come later. The Aegis Book & Risk
  Analyst, the Aegis Structuring Desk and the Aegis Events and Calendar Analyst are
  planned for Stage 2 (not in this release). The Aegis Rules Steward and the Aegis Review Desk, by direct message, are
  planned for Stage 3 (not in this release).

Every skill is installed with a readback. The Lead reads back the stored name, description and body and compares them
with `skills/<name>/SKILL.md` in the checkout; `template/grok-bot.json` records the sha256 of each file (`skills` and
`roleSkills`). Each skill gets one status for the receipt:

- `template`: already present from the template and identical;
- `installed`: saved now, and the readback is identical;
- `pointer`: the product could not hold the body, so the stored text points to the checkout file;
- `mismatch`: the stored text differs or cannot be read back. That skill is not ready, and a shared skill with the
  same name but a different body is never enabled.

## 3. The owner's switches

The owner does these; the Lead asks, then reads back what the owner reports.

1. **Notifications** on for all three Bots, and **Timezone** America/New_York on all three. No tool records these;
   the receipt reports them.
2. **Auto-review rules.** The Lead prints them:

```text
python3 /workspace/aegis-grok-desk/scripts/routine_audit.py --rules-text --desk-root /workspace/aegis-desk
```

   It prints `paste each rule exactly as printed, one rule per line` and three rules: `allow` (506 of 1000
   characters), `ask_first_writes` (315) and `ask_first_owner_authority` (250). The owner pastes each one exactly.
3. **Connected apps and MCP servers.** The owner opens both lists and reads each entry back to the Lead. None may be
   able to place or change an order; an app that can is disconnected, or its order tools turned off, by the owner. The
   owner never signs in to a brokerage in the Bot computer's browser. When the owner confirms, and taps the Ask-first
   card, the Lead records it:

```text
python3 /workspace/aegis-grok-desk/scripts/ack.py connectors --owner-tapped --desk-root /workspace/aegis-desk
```

   It prints `connector readback recorded at HH:MM UTC; desk_doctor re-asks after 30 days`. The doctor's check 10 then
   passes for 30 days.

## 4. The desk interview

The Lead asks, one topic at a time, and writes the answers as JSON (the shape is `desk_init` "Input" in
[docs/TOOLS.md](../../docs/TOOLS.md)) to `/workspace/aegis-desk/setup/desk-answers.json`:

- the accounts (a name and `cash`, `margin` or `ira` each) and the broker's exercise cutoff in ET, `HH:MM`;
- the self-attested options approval level (0 to 5), the declared equity, the attention thresholds, the disclosures
  acknowledged, the engagement, `chatty` or `quiet`, and the cadence profile, `lean` or `active`;
- the market data: host `sandbox` and the token variable `TRADIER_API_TOKEN` (or a token file);
- the risk-free rate, its source and its date.

The approval level, attention thresholds, `chatty_or_quiet`, engagement and disclosures are recorded for later
stages; no tool reads them in this release. The Lead says so. After the owner's tap:

```text
python3 /workspace/aegis-grok-desk/scripts/desk_init.py --answers /workspace/aegis-desk/setup/desk-answers.json --desk-root /workspace/aegis-desk
```

On the fixture answers it printed (synthetic):

```text
wrote desk.toml (962 bytes, sha256 f011fe058582203498b9082ec247cbfd388ac5b241d486ee26584c333eb91a94)
acknowledge with ack.py config --owner-tapped once positions.csv and the adopted rulebook are in place
```

A production host is refused (exit 1, `production_host`). Answers that would rewrite an existing `desk.toml` are
refused unless the owner taps `--replace --owner-tapped`, which rewrites the whole file from full answers.

## 5. The sandbox token, the full doctor, the live Opening Bell

1. The owner adds `TRADIER_API_TOKEN` through the secure secret card. The fallback is the file
   `/workspace/.aegis-secrets/tradier.token` with mode 0600, named in the answers as the token file.
2. The Lead runs the full doctor,
   `python3 /workspace/aegis-grok-desk/scripts/desk_doctor.py --desk-root /workspace/aegis-desk`. Check 4 names
   the token's source. Check 5 makes one GET of the sandbox clock; a 401 or 403 FAILs with
   `not a sandbox token: remove and rotate`, and the owner rotates the token. Check 8 still FAILs until step 7.
3. Then the live Opening Bell, posted verbatim (it is a public market snapshot, not a signal):

```text
python3 /workspace/aegis-grok-desk/scripts/opening_bell.py --symbol SPY --desk-root /workspace/aegis-desk
```

   With the token in the file, add `--token-file /workspace/.aegis-secrets/tradier.token`. Without a token the bell
   prints `UNAVAILABLE: no_token: ...` and exits 20. Before the token, only the recorded synthetic fixture may be
   shown, labelled as such, run from `/workspace/aegis-grok-desk`:
   `python3 scripts/opening_bell.py --symbol SPY --offline --desk-root tests/fixtures/desk --now 2026-11-03T19:50:00Z`.
   It replays a recorded synthetic fixture: it is not market data.

## 6. positions.csv and the dividend intake

Aegis Position Control takes this step in the Floor (`aegis-positions-csv-book-of-record`):

1. The owner supplies the file's exact text. On the owner's explicit request, Position Control saves those exact bytes
   to `/workspace/aegis-desk/positions.csv`: a verbatim transcription, never an edit and never derived rows.
2. It runs `python3 /workspace/aegis-grok-desk/scripts/positions_check.py --desk-root /workspace/aegis-desk` and
   reports the verdict, the number of rows and the file's sha256 from the snapshot line of
   `python3 /workspace/aegis-grok-desk/scripts/csv_recon.py --desk-root /workspace/aegis-desk`. The owner confirms.
   Errors go back to the owner with the exact row and message.

Then the dividend intake, for every held symbol (short calls first): the next ex-dividend date, the amount and
`confirmed` or `estimated`, or `none` with the owner's source. A symbol the owner cannot answer is left out: the desk
then reports it UNAVAILABLE, never zero. The Lead writes `/workspace/aegis-desk/setup/dividends.json` with only
`ex_dividends`, and after the owner's tap runs the tool a second time:

```text
python3 /workspace/aegis-grok-desk/scripts/desk_init.py --answers /workspace/aegis-desk/setup/dividends.json --desk-root /workspace/aegis-desk
```

It writes `research/calendar.toml`. Earnings, settlement values, closures and dividend yields are the owner's to add
to that file by hand. A later `--replace --owner-tapped` run rewrites the whole calendar, so a re-intake re-enters
every entry.

## 7. The rulebook interview

The Lead copies the examples to a candidate file and changes only what the owner states:

```text
mkdir -p /workspace/aegis-desk/rules
cp /workspace/aegis-grok-desk/rules/examples/exit_rules-1.0.0.toml /workspace/aegis-desk/rules/candidate-1.0.0.toml
```

- Rule by rule, the Lead shows the rule's id, title, threshold, plan and source. The owner keeps it as written
  (`origin = "example-accepted"`), changes the threshold (`origin = "owner-entered"`), or leaves it out (the rule is
  deleted from the candidate). A rule left at `origin = "example"` is refused.
- `DAILY` and `CONC` need an owner-entered value, or `owner_must_set = false` to keep the documented default.
- Every held structure needs a take-profit rule that covers it; `--allow-uncovered` overrides that only when the owner
  asks, and it is recorded.
- Adoption runs outside regular trading hours, after the owner's tap, with the phrase the owner types:

```text
python3 /workspace/aegis-grok-desk/scripts/rules_adopt.py --file /workspace/aegis-desk/rules/candidate-1.0.0.toml --version 1.0.0 --owner-phrase "adopt rules 1.0.0" --desk-root /workspace/aegis-desk
```

During the session it refuses with `regular_trading_hours: regular trading hours (session open): an adoption waits for
the close unless --allow-rth is given (recorded in rules/CHANGELOG.md)`. On a fixture copy with every rule accepted as
written, it printed first (synthetic): `adopted rules 1.0.0, sha cc09859c, as rules/exit_rules-1.0.0.toml at 2026-11-03
22:00 UTC (session closed)`. When a rulebook is already adopted, `rules_diff.py --candidate <file> --latest-snapshot
--desk-root /workspace/aegis-desk` runs first. The grammar and change control are in `aegis-rules-dsl-and-change-control`.

Then, after the owner's tap, the configuration is acknowledged:

```text
python3 /workspace/aegis-grok-desk/scripts/ack.py config --owner-tapped --desk-root /workspace/aegis-desk
```

Synthetic: `configuration acknowledged at 2026-11-03 22:05 UTC: desk.toml sha f011fe05, positions.csv sha 9483c8f3,
adopted rulebook 1.0.0 sha cc09859c`.

## 8. The routines: offered, created paused, one Test run

- Each owning Bot creates its own routines from `routines/manifest.toml`: one Grok routine per slot, each prompt
  exactly the manifest's template with the slot filled in, all created paused and read back (name, schedule, prompt,
  paused). On the lean profile that is 2 for the Desk Lead, 5 for the Exit Sentinel and 2 for Position Control. On the
  active profile the Sentinel adds its four extra routines, paused (`aegis-routines-and-quiet-rules`).
- One Test run of the Lead's preopen routine. It proves that routines see the token and the desk files: preopen runs
  the quick doctor (checks 2, 3, 4, 8 and 9), which has no sandbox check; the full doctor of step 5 verified the
  sandbox. The Lead reports whether the Test run raised an Ask-first card; the Allow rule should cover it.
- When the owner says "turn on monitoring", each Bot unpauses its own lean routines and reads them back.
- Last, the full doctor once more. Its heartbeat check warns until the routines have run.

## 9. The receipt

The Lead returns one message with the evidence of each step:

- the release and the pin check line; the `check.sh` result;
- the three Bots by Name and Job; each skill and its status; the Floor and its members;
- Notifications and Timezone per Bot, as the owner reported them;
- the three Auto-review rules pasted; whether the Test run raised a card;
- the connector readback line; the token source (check 4) and the sandbox check (check 5);
- the `desk.toml` sha256, the `positions_check.py` verdict and rows, the calendar written, the rulebook version and
  sha, the configuration acknowledgement line;
- the routines created, paused or on; the doctor's verdict line;
- the dead-man rule: no Morning Desk Sheet by 08:00 ET or no pre-close line by 16:15 ET on a trading day means the
  desk is not running;
- every pending item with its manual step.

Its last line is exactly:

```text
Setup stayed read-only: one sandbox market-data token was added by you; no order was created or sent.
```
