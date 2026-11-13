# Set up Aegis Desk

You are a Grok Bot building the owner's read-only options desk. Sections 1 to 8 are the first run (your checklist:
`aegis-desk-bootstrap-and-onboarding`): four Bots, the desk skills, a demo on a sample book and live delayed market
data. It asks the owner for one thing: a free Alpaca paper key (a key ID and its secret), added through the secure
secret card, never pasted in chat. Section 9 holds the later steps, each started by a phrase the owner says:
"add my positions", "adopt my exit rules", "turn on monitoring", or optionally "set up the structuring desk". Steps
marked **You** are the owner's alone.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

The checkout is `/workspace/aegis-grok-desk`; the desk root is `/workspace/aegis-desk`, outside it, and every desk
command passes `--desk-root` (`routine.py` and `desk_init.py` refuse one inside the checkout). An **owner-authority**
command runs only after the owner taps approve on its Ask-first card; never add `--owner-tapped` or `--allow-rth` on
your own. No brokerage credential is ever asked for. Flags and exit codes: [docs/TOOLS.md](docs/TOOLS.md). Output
marked synthetic comes from a copy of the fixture desk (`tests/fixtures/desk`, `tests/fixtures/init`), not the market.

## 1. Get the pinned release onto the desk computer

```text
git clone --depth 1 --branch v0.2.0 https://github.com/fairgradient-ai/aegis-grok-desk.git /workspace/aegis-grok-desk
mkdir -p /workspace/aegis-desk
python3 /workspace/aegis-grok-desk/scripts/desk_doctor.py --desk-root /workspace/aegis-desk
```

If the tag does not exist, stop and tell the owner. An existing checkout stays as it is (no overwrite, reset or
switch). Check 1 passes on the clean release: `release v0.2.0 at <commit>, clean`. Until section 9.1 writes
`desk.toml`, checks 4, 5, 10 and 11 warn and check 8 fails, as expected. If check 1 says `git refuses this checkout:
it is owned by another user (see SETUP)`, record it as pending: its fix is one owner-approved command (section 9.3
step 4), and nothing in the first run needs it (no `routine.py` runs before section 9.3).

## 2. Read the desk and run the gate

Read `README.md`, this file, the skill `aegis-desk-charter` and the four agent files. Run the gate once, from
`/workspace/aegis-grok-desk`: `bash scripts/check.sh`, a few minutes, ending with
`ok: all repository gates passed for /workspace/aegis-grok-desk`. Any failure stops the setup. Afterwards the doctor
and its `--selftest` are the checks.

## 3. Prepare the desk computer and show the demo

Nothing is installed (standard-library Python 3.9 or later). Show useful output before asking anything. Copy the
sample desk first, because tools write into their desk root (if `/workspace/aegis-demo` exists, pick a new name):

```text
cp -r /workspace/aegis-grok-desk/tests/fixtures/desk /workspace/aegis-demo
cd /workspace/aegis-grok-desk
python3 scripts/opening_bell.py --demo
python3 scripts/positions_check.py --offline --desk-root /workspace/aegis-demo --now 2026-11-03T19:50:00Z
python3 scripts/book.py --offline --desk-root /workspace/aegis-demo --now 2026-11-03T19:50:00Z
python3 scripts/trigger_levels.py --offline --desk-root /workspace/aegis-demo --now 2026-11-03T19:50:00Z
```

`--demo` writes nothing. The rest show the sample book's check, P/L and the prices at which its rules fire; each exits
0. The demo runs no `routine.py`, `book_check.py` or `exit_check.py`: on the never-acknowledged sample files they open
with a CONFIG CHANGED line that names an owner-authority command, and `routine.py` also needs the pin check of
section 1. Post each output verbatim, labelled "synthetic demo on a sample book: not market data, not your positions".

## 4. Create the Bots

| File | Name | Job |
| --- | --- | --- |
| `agents/desk-lead.md` | Aegis Desk Lead | Head of the options desk |
| `agents/exit-sentinel.md` | Aegis Exit Sentinel | Exit-rule monitor of the options desk |
| `agents/position-control.md` | Aegis Position Control | Book-of-record and expiry controller of the options desk |
| `agents/structuring-desk.md` | Aegis Structuring Desk | On-request choices desk of the options desk |

If you were created for this desk, you are the Desk Lead; otherwise create one. Create the others and read back each
native Name and Job. A Bot's standing instructions are its role skill, enabled for it only. The Structuring Desk
answers only when asked and owns no routine. Asked for a later-stage seat, a Bot says it is not in this release.

## 5. Install the skills

Install the 18 desk skills; each Bot enables the ones its agent file lists under `skills:`. Every Bot:
`aegis-desk-charter`, `aegis-compliance-voice-and-number-discipline`, `aegis-routines-and-quiet-rules`,
`aegis-market-data-budget-and-quality`, `aegis-desk-incident-playbooks`. The others: `aegis-desk-bootstrap-and-onboarding`
(Lead), `aegis-rules-dsl-and-change-control` (Lead, Sentinel), `aegis-exit-watch-and-signal-protocol`,
`aegis-threshold-levels`, `aegis-vertical-spread-management` (Sentinel), `aegis-book-marking-and-greeks-honesty`,
`aegis-covered-call-and-cash-secured-put-management`, `aegis-protective-put-and-collar-management` (Sentinel,
Structuring Desk), `aegis-positions-csv-book-of-record`, `aegis-expiry-and-assignment-protocol` (Position Control),
`aegis-overlay-proposals`, `aegis-roll-decision-framework`, `aegis-ticket-and-unwind-order` (Structuring Desk).
[skills/README.md](skills/README.md) says what each teaches. Read back each stored name, description and body against
`skills/<name>/SKILL.md` (sha256s in `template/grok-bot.json`). Status: `template` or `installed` (identical),
`pointer` (points to the checkout file) or `mismatch` (not ready; a shared skill with another body is never enabled).

<!-- role-skills:start -->
Role skills (generated from the agents' System prompts by `scripts/gen_role_skills.py`): `aegis-role-desk-lead`, `aegis-role-exit-sentinel`, `aegis-role-position-control`, `aegis-role-structuring-desk`
<!-- role-skills:end -->

## 6. Create the Aegis Options Floor

Create one private group, "Aegis Options Floor", with exactly the four Bots, and read back its members. Nothing is
shared publicly. Routine output goes to each Bot's own chat ([docs/MONITORING.md](docs/MONITORING.md)).

## 7. Connect market data

**You: get the paper key.** Send the owner these steps (Alpaca's documentation, read 2026-11-13):

1. Sign up at https://alpaca.markets with an email address. A paper-only account needs no funding and no brokerage
   application; it starts with simulated money.
2. Log in to the dashboard and make sure it is switched to **Paper Trading**, not Live.
3. In the **API Keys** panel on the right of the dashboard, choose **Generate New Key**. Copy the key ID and the
   secret. The secret is shown only once: if it is lost, generate a new pair, which stops the old one working.
4. Never a live key. Paper key IDs usually start with `PK` and live ones with `AK`; Alpaca calls that a visual hint
   only. The checks that refuse a live key are the Opening Bell below and the desk doctor's paper check (9.1): both
   send the key to Alpaca's paper trading host, which refuses a key from another environment with a 401 or 403
   (Alpaca documents that mismatch error; it publishes no separate paper/live test).

**You: add both through the secure secret card,** never in chat: the key ID as `APCA_API_KEY_ID` and the secret as
`APCA_API_SECRET_KEY`. Fallback: the file `/workspace/.aegis-secrets/alpaca.key`, mode 0600, the key ID on line 1 and
the secret on line 2, with `--credentials-file`.

**Verify with the live Opening Bell,** posted verbatim (a public market snapshot, not a signal; with the file
fallback, add `--credentials-file /workspace/.aegis-secrets/alpaca.key`):
`python3 /workspace/aegis-grok-desk/scripts/opening_bell.py --symbol SPY --desk-root /workspace/aegis-desk`.
Without a key it prints `UNAVAILABLE: no_credential: ...` (exit 20). `UNAVAILABLE: the quotes request failed: auth:
...` is a 401 or 403 from the market-data host: a key ID and secret that do not belong together, a pair regenerated
since, or a deleted one. The bell then lists option contracts on Alpaca's paper trading host with the same pair, and
that host refuses a live key (the rule doctor check 5 rests on), so a live key prints `UNAVAILABLE: not a paper key:
Alpaca's paper trading host refused the key pair (HTTP 401) after the market-data host answered it; delete both
secrets and add a new pair from the Paper Trading dashboard` (exit 20; 403 in place of 401 is the same verdict). On
either line the owner deletes both secrets from the card (or the file), generates a new paper pair and adds it; then
you verify again. A live key can trade and must never stay, so even before the bell runs: if the key ID starts with
`AK`, or the owner is not sure it came from the Paper Trading dashboard, ask the owner to delete both secrets first.
Stocks come from the IEX feed;
option quotes come from Alpaca's free indicative feed, delayed 15 minutes and approximate (not exchange quotes).
Record the key source (the card with `APCA_API_KEY_ID` and `APCA_API_SECRET_KEY`, or the file); the doctor checks it
(checks 4 and 5) once `desk.toml` exists.

## 8. Return the receipt

One message, opening with three plain lines, for example:

```text
Your Aegis desk is built: four Bots on the Aegis Options Floor and the desk skills, release v0.2.0.
Live market data works: SPY was quoted from Alpaca's paper market data, labelled 15-minute delayed.
Nothing else is needed now. When you want the desk to watch your positions, say "add my positions".
```

Then the evidence: the pin check line, the `check.sh` result, the demo commands (synthetic), the Bots by Name and Job,
each skill's status, the Floor's members, the live Opening Bell (or the pending step), the key source, the doctor's
verdict line, and anything pending with its manual step. Then three notes:

- Until "add my positions", no Auto-review rule is in place. The paper key cannot reach a live account; it could
  place paper orders on Alpaca's paper trading host, which the desk's GET-only code cannot and the write-side
  Ask-first rule covers from 9.1. The doctor's paper check in 9.1 is what proves it is a paper key.
- Never sign in to a brokerage in the Bot computer's browser.
- Say "add my positions", "adopt my exit rules", "turn on monitoring", or optionally "set up the structuring desk".

Its last line is exactly the line below, only when the live Opening Bell succeeded. With no key added it is
`Setup stayed read-only: no credential was added; no order was created or sent.` If an added key failed the bell,
name the failed line and its next step (after `auth:`, deleting both secrets) and end with `Setup stayed read-only: a
market-data key pair was added by you but did not pass its check; no order was created or sent.` Never call the desk
ready while a required check failed.

```text
Setup stayed read-only: one Alpaca paper key pair was added by you; no order was created or sent.
```

## 9. When the owner asks for more

Each phrase starts one step, which ends with a short receipt of its tool lines and `no order was created or sent`.
**Prerequisites first.** Every later phrase (9.2, 9.3, 9.4) first checks 9.1's safety settings and desk questions:
both Ask-first rules pasted, the connected-apps answer recorded by `ack.py connectors` (doctor check 10 warns until
then) and `desk.toml` written. If any is missing, do those parts of 9.1 first, then continue with the phrase. No
owner-authority command and no routine comes before the two Ask-first rules. 9.2 also needs `positions.csv`, and 9.3
an adopted rulebook; offer the missing step first.

### 9.1 "add my positions"

**You: two safety settings first,** because owner-authority commands follow. Print the rules with
`python3 /workspace/aegis-grok-desk/scripts/routine_audit.py --rules-text --desk-root /workspace/aegis-desk`. The
owner opens **Settings, General, Auto-review** and adds rules 2 and 3 as "Ask first" rules, exactly as printed:

```text
Ask first: placing, modifying or cancelling an order; moving funds; any POST, PUT, PATCH or DELETE request to a brokerage, and any request to a brokerage's order, position or account endpoints, including any request to api.alpaca.markets, and any request to paper-api.alpaca.markets other than /v2/clock, /v2/calendar and /v2/options/contracts; using a connected app or MCP tool that can place or change orders; signing in to a brokerage; browsing outside the desk's sourcing allow-list.
```

```text
Ask first: any command that carries --owner-tapped or --allow-rth, including ack.py snooze, ack.py acted and ack.py config; rules_adopt.py; desk_init.py; exit_ticket.py, roll_analysis.py and propose.py. These record the owner's own decisions or answer the owner's own request: run them only after the owner taps approve on this card.
```

Then one question: can any Connected app or MCP server in the owner's account place or change orders? They are
account-wide, so it would give every Bot that ability; the owner disconnects it or turns its order tools off. Once
the owner confirms none can, the **owner-authority** command records it (the doctor re-asks after 30 days):
`python3 /workspace/aegis-grok-desk/scripts/ack.py connectors --owner-tapped --desk-root /workspace/aegis-desk`.

**You: four desk questions,** one at a time, written to `/workspace/aegis-desk/setup/desk-answers.json` (`desk_init`
"Input" in [docs/TOOLS.md](docs/TOOLS.md)): (1) the accounts, a name and `cash`, `margin` or `ira` each, and the lowest
options approval level across them, in the desk's convention shown word for word
([docs/STRUCTURING.md](docs/STRUCTURING.md#the-approval-level-check)); (2) the broker's exercise cutoff, ET `HH:MM`;
(3) the declared equity; (4) the risk-free rate, its source and date (the owner's number, never yours). Propose the
rest for one "yes": host `paper`, `key_env` `APCA_API_KEY_ID` and `secret_env` `APCA_API_SECRET_KEY` (the names,
never the values; or `credentials_file`), cadence `lean`, and for the fields no tool
reads in this release (engagement, `chatty_or_quiet`, attention thresholds) the values in
`tests/fixtures/init/answers.json`. `disclosures_acknowledged` stays empty unless the owner names one they
acknowledged. After the tap, the **owner-authority** command writes `desk.toml` (a live host is refused):
`python3 /workspace/aegis-grok-desk/scripts/desk_init.py --answers /workspace/aegis-desk/setup/desk-answers.json --desk-root /workspace/aegis-desk`.

**positions.csv.** The owner describes the book in plain words ("I sold 2 NVDA 20-Nov 150 calls at 3.20 against my
200 shares"). Aegis Position Control drafts the rows from those words ([docs/POSITIONS_CSV.md](docs/POSITIONS_CSV.md)),
asks for what is missing and shows the text; once the owner approves it, it saves exactly that text to
`/workspace/aegis-desk/positions.csv` and runs `positions_check.py` and `csv_recon.py`; the owner confirms the verdict.

**Dividends.** For each held symbol, short calls first: the next ex-dividend date, amount and `confirmed` or
`estimated`, or `none` with a source. A symbol left out is reported UNAVAILABLE, never zero. Write
`/workspace/aegis-desk/setup/dividends.json` (only `ex_dividends`); after the tap, `desk_init.py --answers` with it
(**owner-authority**) writes `research/calendar.toml`. Last, the full doctor:
`python3 /workspace/aegis-grok-desk/scripts/desk_doctor.py --desk-root /workspace/aegis-desk`. Check 4 names the
key source, never a value; check 5 asks the clock of Alpaca's paper trading host, which refuses a key from another
environment (`paper key verified`; a 401 or 403 fails with `not a paper key: remove it and add a paper key`: the owner deletes
both secrets at once and adds a paper pair); check 8 fails until "adopt my exit rules".

### 9.2 "adopt my exit rules"

Prerequisites first: 9.1's safety settings, desk questions and `positions.csv`, done before this step if missing.
Copy the examples: `mkdir -p /workspace/aegis-desk/rules` and
`cp /workspace/aegis-grok-desk/rules/examples/exit_rules-1.0.0.toml /workspace/aegis-desk/rules/candidate-1.0.0.toml`.
One answer: keep the examples as written, or name those to change or drop. Kept rules get `origin = "example-accepted"`,
changed ones `origin = "owner-entered"`, dropped ones are deleted; `origin = "example"` is refused. Propose the
documented defaults of `DAILY` (-1000 usd) and `CONC` (25 %) for a "yes", which sets `owner_must_set = false`. Every
held structure needs a covering take-profit rule (`--allow-uncovered` only when the owner asks; it is recorded). With
a rulebook already adopted, run `rules_diff.py --candidate <file> --latest-snapshot` first. After the tap, with the
phrase the owner types, the **owner-authority** command:

```text
python3 /workspace/aegis-grok-desk/scripts/rules_adopt.py --file /workspace/aegis-desk/rules/candidate-1.0.0.toml --version 1.0.0 --owner-phrase "adopt rules 1.0.0" --desk-root /workspace/aegis-desk
```

During regular trading hours it refuses (`... unless --allow-rth is given`): offer to wait for the close or to tap
the same command with `--allow-rth`, recorded in `rules/CHANGELOG.md` (synthetic, every rule kept: `adopted rules
1.0.0, sha cc09859c, ... (session open)`). Then, after the tap, the **owner-authority** command
`python3 /workspace/aegis-grok-desk/scripts/ack.py config --owner-tapped --desk-root /workspace/aegis-desk`; from then on an unacknowledged change posts CONFIG CHANGED ([docs/EXIT_RULES.md](docs/EXIT_RULES.md)).

### 9.3 "turn on monitoring"

Prerequisites first: 9.1's safety settings and desk questions, and a rulebook adopted in 9.2, done before this step
if missing.

1. **Routines.** Create one paused test routine whose prompt is only "Reply with the single word quiet.", read it
   back, delete it, and read back that it is gone. With no routines the desk cannot monitor: say so and stop.
2. **You: Notifications and Timezone.** In each Bot's own settings (the Desk Lead, the Exit Sentinel and Position
   Control; the exact path is not confirmed: open the Bot, then its settings), turn Notifications on and set Timezone
   to America/New_York, then say "done".
3. **You: the Allow rule.** In **Settings, General, Auto-review**, rule 1 of `--rules-text` as an "Allow
   automatically" rule, exactly as printed:

```text
Allow automatically: the command python3 /workspace/aegis-grok-desk/scripts/routine.py followed by exactly one routine id from this list and, optionally, --slot and a time HH:MM, with nothing else on the command line (no pipe, redirection, semicolon or second command): preopen, book-check, final-call, eod, eod-brief, post-expiry. These runs read delayed market data through the desk's GET-only Alpaca paper client and write files only under /workspace/aegis-desk; they cannot place, modify or cancel an order.
```

4. **You, only if git refuses the checkout** (`git refuses this checkout: it is owned by another user (see SETUP)`):
   approve exactly `git config --global --add safe.directory /workspace/aegis-grok-desk`, run as the account that runs
   the routines, so it lands in that account's own `~/.gitconfig` (the pin check drops `GIT_*` variables, so
   `GIT_CONFIG_GLOBAL` is ignored). Rerun the doctor; check 1 then passes. No desk code writes git configuration.
5. **Routines, created paused.** Each owning Bot creates its own from `routines/manifest.toml`, one per slot, each
   prompt the manifest's template with the slot filled in, and reads each back. Lean, Mon-Fri: Desk Lead `preopen`
   07:30, `eod-brief` 17:10; Exit Sentinel `book-check` 10:05, 12:05, 14:05, 15:35, `eod` 16:35; Position Control
   `final-call` 15:20, plus `post-expiry` 09:30 Saturday. `routine_audit.py --budget`: `lean: 41 runs per week ...`.
6. **One Test run** of the Lead's `preopen` proves routines see the key and the desk files; report any Ask-first
   card. Read back with `heartbeat.py --list` and `routine_audit.py` (with `--desk-root`).
7. **Unpause.** Each Bot unpauses its lean routines and reads them back; then the full doctor.

The receipt adds the dead-man rule: no Morning Desk Sheet by 08:00 ET, or no pre-close line by 16:15 ET, on a trading
day means the desk is not running; rely on the broker's own alerts. More: [docs/MONITORING.md](docs/MONITORING.md).

### 9.4 "set up the structuring desk"

Prerequisites first: 9.1's safety settings and desk questions, done before this step if missing.
Optional: the Aegis Structuring Desk prices choices for a held position only when asked, by the owner's criteria in
`/workspace/aegis-desk/structuring.toml`. In its own chat it runs the interview of [docs/STRUCTURING.md](docs/STRUCTURING.md):
each section with its documented defaults (starting points, not a judgment that they fit), then the disclosure
`structuring-suggestions-1` word for word, recorded only after the owner acknowledges it. After the tap, the **owner-authority** command
`python3 /workspace/aegis-grok-desk/scripts/desk_init.py --answers /workspace/aegis-desk/setup/structuring-answers.json --desk-root /workspace/aegis-desk`,
then `ack.py config --owner-tapped` again. Each later request (`exit_ticket.py`, `roll_analysis.py`, `propose.py`)
raises the owner-authority card; the tap records that the owner asked.

## 10. If you are not Grok Bot

Use your runtime's page: [Claude Code](docs/aegis-claude-code.md), [Cursor](docs/aegis-cursor.md),
[Grok Build](docs/aegis-grok-build.md), [Codex](docs/aegis-codex.md) or [Gemini CLI](docs/aegis-gemini-cli.md). Bots,
the Floor, the secret card, routines and Auto-review are Grok Bot steps. Keep the guarantees: no order path, the
Alpaca paper key pair as the only credential, numbers only from the tools, UNAVAILABLE as a verdict.

Runbook structure adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased changes, commit 75a4c79 (SETUP.md); see [ATTRIBUTION.md](ATTRIBUTION.md).
