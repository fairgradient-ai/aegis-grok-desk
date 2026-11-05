# Aegis Desk on Grok Bot

Grok Bot is the desk's main runtime. Three Bots, the Aegis Desk Lead, the Aegis Exit Sentinel and Aegis Position
Control, share your Grok Bot computer, run their routines there and post in their own chats.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

## Start

Create a new Bot and paste the Start prompt from the [README](../README.md#start). It points at the bootstrap skill
and at [SETUP.md](../SETUP.md), both pinned to the tag `v0.1.0`. The tag is created at release; until then the links
do not resolve. SETUP.md is the full runbook. This page explains what protects you on Grok Bot and how you can check
it.

No public Grok Bot template is published in this release. [GROK_BOT_TEMPLATE.md](GROK_BOT_TEMPLATE.md) explains the
template contract and why the paste prompt comes first.

## Where things live

| Path | What it is |
| --- | --- |
| `/workspace/aegis-grok-desk` | the release checkout, a clone of the tag `v0.1.0`; nothing in it is edited |
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
   You add it through the secure secret card and never paste it in chat. A secret added there is visible to every Bot
   on the computer, as Grok Bot staff have reported, which is why nothing that can reach real money may be added. The
   full doctor verifies the token with one GET of the sandbox clock:

   ```text
   python3 /workspace/aegis-grok-desk/scripts/desk_doctor.py --desk-root /workspace/aegis-desk
   ```

   A 401 or 403 fails its check 5 with `not a sandbox token: remove and rotate`.
3. **No app or MCP server that can trade.** Connected apps and MCP servers are account-wide: one that can place or
   change orders would give that ability to every Bot. At setup you read both lists back, and after your tap the Lead
   records that readback with this owner-authority command:

   ```text
   python3 /workspace/aegis-grok-desk/scripts/ack.py connectors --owner-tapped --desk-root /workspace/aegis-desk
   ```

   It prints `connector readback recorded at HH:MM UTC; desk_doctor re-asks after 30 days`. The doctor's check 10 then
   warns `re-confirm connected apps and MCP servers` once 30 days have passed.
4. **The Auto-review rules.** You paste three rules at setup (SETUP step 7). The Lead prints them from the routine
   manifest:

   ```text
   python3 /workspace/aegis-grok-desk/scripts/routine_audit.py --rules-text --desk-root /workspace/aegis-desk
   ```

   On this release, the three texts are:

   ```text
   Allow automatically: the command python3 /workspace/aegis-grok-desk/scripts/routine.py followed by exactly one routine id from this list and, optionally, --slot and a time HH:MM, with nothing else on the command line (no pipe, redirection, semicolon or second command): preopen, book-check, final-call, eod, eod-brief, post-expiry. These runs read delayed market data through the desk's GET-only sandbox client and write files only under /workspace/aegis-desk; they cannot place, modify or cancel an order.
   Ask first: placing, modifying or cancelling an order; moving funds; any POST, PUT, PATCH or DELETE request to a brokerage, including any request to tradier.com/v1/accounts; using a connected app or MCP tool that can place or change orders; signing in to a brokerage; browsing outside the desk's sourcing allow-list.
   Ask first: any command that carries --owner-tapped or --allow-rth, including ack.py snooze, ack.py acted and ack.py config; rules_adopt.py; desk_init.py. These record the owner's own decisions: run them only after the owner taps approve on this card.
   ```

   The tool reports their lengths: `allow (506 of 1000 characters)`, `ask_first_writes (315 of 1000 characters)` and
   `ask_first_owner_authority (250 of 1000 characters)`. Auto-review is model-based, and an "Ask first" rule wins over
   an "Allow" rule. Staff have reported that it can still ask for approval despite an Allow rule.
5. **Your tap is the signature.** `desk_init.py`, `rules_adopt.py`, `ack.py config`, `ack.py connectors`,
   `ack.py snooze`, `ack.py acted` and any `--allow-rth` run only after you approve their Ask-first card, and never from
   a routine. A Bot never adds `--owner-tapped` or `--allow-rth` on its own. `rules_adopt.py` also takes the phrase you
   type, for example `--owner-phrase "adopt rules 1.0.0"`.

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
  `v0.1.0` and whose `git status` lists nothing. Otherwise it prints one INCIDENT line, writes an incident file and
  exits 20. From a development checkout, on a copy of the fixture desk at 2026-11-03 19:50 UTC (synthetic):

  ```text
  INCIDENT | book-check | the desk checkout is not the pinned release v0.1.0: development checkout (no tag); no step ran | details in incidents/20261103T195000.000000Z-dirty-checkout.md
  deterministic rules you adopted · posted by an AI assistant · not personalized investment advice · data delayed 15 min, as of 19:50 UTC
  ```

- The pin check gives one reason. On the release it is `release v0.1.0 at <commit>, clean`. Otherwise it is one of
  `development checkout (no tag)`, `HEAD <commit> is not the commit of v0.1.0 (<commit>)`,
  `the checkout has <n> uncommitted or untracked path(s)`,
  `git status failed (exit <n>); the checkout cannot be verified`, `not a git checkout`, `git is unavailable` or
  `git refuses this checkout: it is owned by another user (see SETUP)`. For the last one, SETUP step 1 gives the one
  command you approve: `git config --global --add safe.directory /workspace/aegis-grok-desk`.
- A run with nothing to post prints the single word `quiet`, and the Bot posts that word. Missed runs are never run
  again later.

## Checks at setup

- **The repository gate,** once, in SETUP step 2: `bash scripts/check.sh` from the checkout. It takes a few minutes.
- **The engine selftest,** any time after:

  ```text
  python3 /workspace/aegis-grok-desk/scripts/desk_doctor.py --selftest --desk-root /workspace/aegis-desk
  ```

  Its first lines name the run instant and your Python version. Its last two lines, on a passing computer:

  ```text
  [PASS] 12 selftest: engine selftest: 5 of 5 sub-checks passed (bsm golden, iv round trip, DST transitions, TOML reader, alert lint)
  verdict: PASS (2 checks; warnings: none; failures: none); nothing written (selftest)
  ```

- **One Test run** of the `preopen` routine (SETUP step 10). It proves that routines see the token and the desk files,
  and the Lead reports whether it raised an Ask-first card. It runs the quick doctor, which has no sandbox check: the
  interactive full doctor verified the sandbox.
- **Readbacks** of every Bot's Name and Job, every skill (`template`, `installed`, `pointer` or `mismatch`), the
  Floor's members, Notifications and Timezone, and the connected apps and MCP servers.
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
