# Aegis Desk on Grok Bot

Grok Bot is the desk's main runtime. Four Bots share your Grok Bot computer. Three of them, the Aegis Desk Lead, the
Aegis Exit Sentinel and Aegis Position Control, run their routines there and post in their own chats. The fourth, the
Aegis Structuring Desk, runs no routine and answers only when you ask.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

## Start

Create a new Bot and paste the Start prompt from the [README](../README.md#start). It points at the bootstrap skill
and at [SETUP.md](../SETUP.md), both pinned to the tag `v0.2.0`. SETUP.md is the full runbook. This page explains
what protects you on Grok Bot and how you can check it.

### The first run

The Lead clones the pinned release, runs the repository gate, creates the four Bots and their group chat, the Aegis
Options Floor, and installs the skills with a readback of each. It shows you a demo on a copy of the made-up sample
desk, so you see real tool output before you are asked anything. Then it asks you for one thing: a free Tradier
sandbox market-data token, added through the secure secret card, never pasted in chat. The
[FAQ](FAQ.md#how-do-i-get-a-tradier-sandbox-token) explains how to get one. The Lead checks the token with one live
market snapshot and returns the receipt, which names the phrases below. Its last line is:

```text
Setup stayed read-only: one sandbox market-data token was added by you; no order was created or sent.
```

### Then, the phrases

Each later step starts when you say its phrase to the Lead, prerequisites first. Every later phrase first checks
what "add my positions" sets up before it: the two safety settings (both Ask-first rules and the connected-apps
answer) and the desk settings (`desk.toml`). If any is missing, the Lead does those first, then continues. No
owner-tapped command and no routine comes before the two Ask-first rules.

| Say | What the Lead does | What you do |
| --- | --- | --- |
| "add my positions" | first the two safety settings, because an owner-tapped command follows; then the desk settings with suggested defaults; then Position Control drafts your positions, checks them and records dividends for the symbols you hold; then the full doctor | paste the two Ask-first Auto-review rules; answer whether any connected app or MCP server can trade and tap approve on the readback; confirm the settings with one "yes" and acknowledge the disclosures; describe your positions and approve the drafted lines |
| "adopt my exit rules" | proposes the example rulebook, with a suggested value for each threshold the examples leave to you | keep the examples or change some; tap approve and type the adoption phrase, now (recorded) or after the close; acknowledge the change |
| "turn on monitoring" | checks that routines work on your plan, creates the routines paused, runs one Test run, then unpauses them | turn on Notifications and set the Timezone on the three routine-owning Bots; paste the Allow rule; approve the `safe.directory` fix if the doctor asks |
| "set up the structuring desk" (optional) | interviews you for your structuring criteria and writes them | answer, tap approve, acknowledge |

No public Grok Bot template is published in this release. [GROK_BOT_TEMPLATE.md](GROK_BOT_TEMPLATE.md) explains the
template contract and why the paste prompt comes first.

## Where things live

| Path | What it is |
| --- | --- |
| `/workspace/aegis-grok-desk` | the release checkout, a clone of the tag `v0.2.0`; nothing in it is edited |
| `/workspace/aegis-desk` | the desk root: `desk.toml`, `positions.csv`, your adopted rules and every file the tools write |
| `/workspace/.aegis-secrets/tradier.token` | the token file, only if you choose it instead of the secret card (mode 0600) |

As Grok Bot's documentation describes it, your Bots share one computer, `/workspace` survives between sessions,
installed packages do not, and the computer sleeps soon after you disconnect. So the desk needs nothing installed (the
tools are standard-library Python, 3.9 or later), and it runs only when a routine or you start a command.

## What protects you here

Grok Bot runs no plugin hooks, so the broker-write gate in `hooks/` does not run on Grok Bot. These controls do.

1. **No write code.** The only network path in the repository is a market-data client that sends GET requests only.
   The repository gate proves it, and SETUP runs it once: `bash scripts/check.sh`. [SECURITY.md](../SECURITY.md)
   explains each layer.
2. **One sandbox token.** The only credential is a Tradier sandbox market-data token. It cannot reach a live account.
   You add it through the secure secret card in the first run and never paste it in chat. A secret added there is
   visible to every Bot on the computer, as Grok Bot staff have reported, which is why nothing that can reach real
   money may be added: if the production token is pasted by mistake, the live check fails with `auth:` and the Lead
   asks you to delete it from the card at once. The token could place paper orders on Tradier's sandbox; the
   write-side Ask-first rule (item 4) covers that from "add my positions" on, and until then the repository code
   still has no write path (item 1). The full doctor verifies the token with one GET of the sandbox clock:

   ```text
   python3 /workspace/aegis-grok-desk/scripts/desk_doctor.py --desk-root /workspace/aegis-desk
   ```

   A 401 or 403 fails its check 5 with `not a sandbox token: remove and rotate`.
3. **No app or MCP server that can trade.** Connected apps and MCP servers are account-wide: one that can place or
   change orders would give that ability to every Bot. When you say "add my positions", you answer whether any of
   them can trade, and after your tap the Lead records that answer with this owner-authority command:

   ```text
   python3 /workspace/aegis-grok-desk/scripts/ack.py connectors --owner-tapped --desk-root /workspace/aegis-desk
   ```

   It prints `connector readback recorded at HH:MM UTC; desk_doctor re-asks after 30 days`. The doctor's check 10 then
   warns `re-confirm connected apps and MCP servers` once 30 days have passed.
4. **The Auto-review rules.** You paste the two Ask-first rules when you say "add my positions", before the first
   owner-tapped command, and the Allow rule when you say "turn on monitoring" (SETUP step 9). The Lead prints them
   from the routine manifest:

   ```text
   python3 /workspace/aegis-grok-desk/scripts/routine_audit.py --rules-text --desk-root /workspace/aegis-desk
   ```

   On this release, the three texts are:

   ```text
   Allow automatically: the command python3 /workspace/aegis-grok-desk/scripts/routine.py followed by exactly one routine id from this list and, optionally, --slot and a time HH:MM, with nothing else on the command line (no pipe, redirection, semicolon or second command): preopen, book-check, final-call, eod, eod-brief, post-expiry. These runs read delayed market data through the desk's GET-only sandbox client and write files only under /workspace/aegis-desk; they cannot place, modify or cancel an order.
   Ask first: placing, modifying or cancelling an order; moving funds; any POST, PUT, PATCH or DELETE request to a brokerage, including any request to tradier.com/v1/accounts; using a connected app or MCP tool that can place or change orders; signing in to a brokerage; browsing outside the desk's sourcing allow-list.
   Ask first: any command that carries --owner-tapped or --allow-rth, including ack.py snooze, ack.py acted and ack.py config; rules_adopt.py; desk_init.py; exit_ticket.py, roll_analysis.py and propose.py. These record the owner's own decisions or answer the owner's own request: run them only after the owner taps approve on this card.
   ```

   The tool reports their lengths: `allow (506 of 1000 characters)`, `ask_first_writes (315 of 1000 characters)` and
   `ask_first_owner_authority (333 of 1000 characters)`. Auto-review is model-based, and an "Ask first" rule wins over
   an "Allow" rule. Staff have reported that it can still ask for approval despite an Allow rule.
5. **Your tap is the signature.** `desk_init.py`, `rules_adopt.py`, `ack.py config`, `ack.py connectors`,
   `ack.py snooze`, `ack.py acted` and any `--allow-rth` run only after you approve their Ask-first card, and never from
   a routine. A Bot never adds `--owner-tapped` or `--allow-rth` on its own. `rules_adopt.py` also takes the phrase you
   type, for example `--owner-phrase "adopt rules 1.0.0"`.
6. **Choices only on your request.** The Aegis Structuring Desk runs `exit_ticket.py`, `roll_analysis.py` and
   `propose.py` only when you ask, and each run raises the same Ask-first card: your tap is the record of your request.
   `routine.py` refuses them as routine steps. Their answers carry no order fields, and you supply the limit
   ([STRUCTURING.md](STRUCTURING.md)).

## How a routine runs

Each Bot creates its own routines from `routines/manifest.toml`, one Grok routine per slot. Every prompt is the
manifest's template with the slot filled in, for example:

```text
Run python3 /workspace/aegis-grok-desk/scripts/routine.py book-check --slot 10:05 and post its output verbatim.
```

- `routine.py` is the one command a routine runs. It reads the desk root `/workspace/aegis-desk` by default, never the
  current directory. A desk root inside the checkout, or the checkout itself, is a usage error (exit 2), because the
  run's files would make the checkout dirty.
- It runs no step unless the checkout is the clean release: a git work tree whose HEAD is the commit of the tag
  `v0.2.0` and whose `git status` lists nothing. Otherwise it prints one INCIDENT line, writes an incident file and
  exits 20. From a development checkout, on a copy of the fixture desk at 2026-11-03 19:50 UTC (synthetic):

  ```text
  INCIDENT | book-check | the desk checkout is not the pinned release v0.2.0: development checkout (no tag); no step ran | details in incidents/20261103T195000.000000Z-dirty-checkout.md
  deterministic rules you adopted · posted by an AI assistant · not personalized investment advice · data delayed 15 min, as of 19:50 UTC
  ```

- The pin check gives one reason. On the release it is `release v0.2.0 at <commit>, clean`. Otherwise it is one of
  `development checkout (no tag)`, `HEAD <commit> is not the commit of v0.2.0 (<commit>)`,
  `the checkout has <n> uncommitted or untracked path(s)`,
  `git status failed (exit <n>); the checkout cannot be verified`, `not a git checkout`, `git is unavailable` or
  `git refuses this checkout: it is owned by another user (see SETUP)`. For the last one, SETUP gives the one command
  you approve: `git config --global --add safe.directory /workspace/aegis-grok-desk`.
- A run with nothing to post prints the single word `quiet`, and the Bot posts that word. Missed runs are never run
  again later.

## Checks at setup

- **The repository gate,** once, in the first run (SETUP step 2): `bash scripts/check.sh` from the checkout. It takes
  a few minutes.
- **The engine selftest,** any time after:

  ```text
  python3 /workspace/aegis-grok-desk/scripts/desk_doctor.py --selftest --desk-root /workspace/aegis-desk
  ```

  Its first lines name the run instant and your Python version. Its last two lines, on a passing computer:

  ```text
  [PASS] 12 selftest: engine selftest: 5 of 5 sub-checks passed (bsm golden, iv round trip, DST transitions, TOML reader, alert lint)
  verdict: PASS (2 checks; warnings: none; failures: none); nothing written (selftest)
  ```

- **One Test run** of the `preopen` routine, when you say "turn on monitoring". It proves that routines see the token
  and the desk files, and the Lead reports whether it raised an Ask-first card. It runs the quick doctor, which has no
  sandbox check: the interactive full doctor verified the sandbox.
- **Readbacks** in the first run of every Bot's Name and Job, every skill (`template`, `installed`, `pointer` or
  `mismatch`) and the Floor's members; of the connected apps and MCP servers when you say "add my positions"; and of
  Notifications and Timezone on the three routine-owning Bots when you say "turn on monitoring".
- **The weekly cost** in runs:

  ```text
  python3 /workspace/aegis-grok-desk/scripts/routine_audit.py --budget --desk-root /workspace/aegis-desk
  ```

  On this release it prints, among its lines, `lean: 41 runs per week (about 8 runs per weekday plus Saturday)` and
  `active: 61 runs per week (lean plus four paused book-check slots, unpaused only on the conditions the owner chose)`.

## Limits

- Grok Bot routines can start 10 to 37 minutes late, as reported, worst between 08:00 and 12:00 ET. A missed run is not
  replayed, a long absence can pause routines, and every run spends weekly usage.
- The market data is delayed 15 minutes. The desk sets levels and checks at intervals; it is not a real-time watcher.
  The threshold-level sheet gives each of your rules as a price, so your broker's own alerts can watch in real time.
- The dead-man rule: on a trading day, no Morning Desk Sheet by 08:00 ET, or no pre-close line by 16:15 ET, means the
  desk is not running. Rely on your broker's alerts, and answer any prompt that asks to keep the routines running.
  [MONITORING.md](MONITORING.md) has the schedule and the details.

## Other runtimes

[Grok Build](aegis-grok-build.md) · [Claude Code](aegis-claude-code.md) · [Cursor](aegis-cursor.md) ·
[Codex](aegis-codex.md) · [Gemini CLI](aegis-gemini-cli.md) · the tool reference [TOOLS.md](TOOLS.md)
