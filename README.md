# Aegis Desk

**A read-only options desk for Grok Bot and Grok Build.** It watches your open option positions from a
`positions.csv` you keep by hand, marks them from delayed Tradier sandbox market data, computes implied volatility,
Greeks and P/L with standard-library Python tools, and posts an alert when one of your own adopted exit rules fires.
The Bots narrate; the tools compute every number.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

Status: release 0.1.0 (Stage 1) is being prepared. Its tag `v0.1.0` is created at release, so the Start links below
resolve from then on.

## Try it now

From a clone of this repository, with no token and no setup:

```text
python3 scripts/opening_bell.py --symbol SPY --offline --desk-root tests/fixtures/desk --now 2026-11-03T19:50:00Z
```

It prints:

```text
SPY 648.12 mid (bid 648.10, ask 648.14), as of 19:31 UTC
nearest standard expiration at least 20 calendar days out: 2026-12-18 (45 calendar days)
ATM implied volatility at the 640 strike: 25.02 % (call 25.07 %, put 24.98 %; mids 28.65 and 17.30; r 0.04, q 0)
expected move to 2026-12-18: straddle-based (call mid + put mid) x 0.80 = 36.76; one-sigma S x IV x sqrt(tau) = 56.98
Tradier sandbox, delayed 15 min, as of 19:31 UTC; a public market snapshot, not a signal
public market data · posted by an AI assistant · not a signal and not personalized investment advice · data delayed 15 min, as of 19:31 UTC
```

This replays a recorded **synthetic** fixture (`tests/fixtures/desk/fixtures/tradier/`): every value is made up, so
it is not market data. `--now` pins the instant, so the output never changes. The command writes nothing; the clone
stays clean. The last line is the market-snapshot footer: the Opening Bell names no rule, so it never carries the
footer of your rules.

## Start

Open Grok Bot, create a new Bot (it becomes your Aegis Desk Lead), and paste:

> Set up the Aegis options desk from https://github.com/fairgradient-ai/aegis-grok-desk/blob/v0.1.0/skills/aegis-desk-bootstrap-and-onboarding/SKILL.md. Follow that bootstrap skill and the runbook at https://github.com/fairgradient-ai/aegis-grok-desk/blob/v0.1.0/SETUP.md step by step, keep the desk read-only, and finish with the setup receipt.

The tag `v0.1.0` is created at release; until then these links do not resolve.

The Lead clones the pinned release, checks it, creates the Stage-1 Bots and their group chat, installs the skills
with a readback each, and then asks you for the things only you can do: the sandbox token through the secure
secret card, the Auto-review rules, Notifications and Timezone, and a readback of your connected apps and MCP
servers. It interviews you for the desk configuration, your positions and your rules, offers the routines paused,
and returns a receipt that ends:

```text
Setup stayed read-only: one sandbox market-data token was added by you; no order was created or sent.
```

[SETUP.md](SETUP.md) is the full runbook. No public Grok Bot template is published in this release; see
[docs/GROK_BOT_TEMPLATE.md](docs/GROK_BOT_TEMPLATE.md).

## Meet the desk

Stage 1 runs on the Bots below, in one private group chat, the "Aegis Options Floor". Each owns its files and is the
only voice about them.

| Bot | What it owns | Profile |
| --- | --- | --- |
| Aegis Desk Lead | routes your questions; `desk.toml`; the 07:30 Morning Desk Sheet and the 17:10 EOD Desk Brief; whether the desk ran today; rulebook changes in Stage 1 | [agents/desk-lead.md](agents/desk-lead.md) |
| Aegis Exit Sentinel | runs your adopted exit rules; posts EXIT SIGNAL, WATCH, UNAVAILABLE and CONFIG CHANGED; the pre-close line; the threshold-level sheet and plan cards; the end-of-day book snapshot | [agents/exit-sentinel.md](agents/exit-sentinel.md) |
| Aegis Position Control | keeps `positions.csv` honest and proposes exact lines (you edit the file); the expiry week from Final Call to the Saturday reconciliation | [agents/position-control.md](agents/position-control.md) |

The Aegis Book & Risk Analyst, the Aegis Structuring Desk and the Aegis Events and Calendar Analyst are planned for Stage 2 (not in this release).
The Aegis Rules Steward and the Aegis Review Desk are planned for Stage 3 (not in this release).

The skills the Bots load are listed in [skills/README.md](skills/README.md).

## A day on the desk

The lean profile, in America/New_York time, on a trading day:

| Time | Bot | What you see |
| --- | --- | --- |
| 07:30 | Aegis Desk Lead | the MORNING DESK SHEET: the doctor line, the positions verdict, open signals, the expiry docket, last night's levels (always) |
| 10:05, 12:05, 14:05 | Aegis Exit Sentinel | a book check: a message only when a verdict changed, a rule newly fired, an UNAVAILABLE stands or a signal re-posts; at 12:05 a "watch alive" line if nothing posted since the open |
| 15:20 | Aegis Position Control | the FINAL CALL on an expiry day, the T-1 PREVIEW the day before, or a pin or dividend warning; otherwise nothing |
| 15:35 | Aegis Exit Sentinel | the last book check and the pre-close line (always) |
| 16:35 | Aegis Exit Sentinel | the THRESHOLD LEVELS sheet: each of your rules as the price at which it fires (always) |
| 17:10 | Aegis Desk Lead | the EOD DESK BRIEF: P/L today, month and year to date, signals, which routines ran, your to-do list (always) |
| Saturday 09:30 | Aegis Position Control | the post-expiry reconciliation, only in a week a held contract expired |

Silence between runs means nothing changed. A synthetic example, the first line, the pre-close line and the footer of
the 15:35 book check on the fixture's expiry day (the 15:35 slot of 2026-11-20, run at 19:50 UTC):

```text
EXIT SIGNAL AG-20261020-01 | WMT covered call | your rule TP80 (rules 1.0.0, sha cc09859c) fired: credit captured 95.32 % (your threshold: at or above 80 %); quote grade E: the conservative side stands | already past this threshold at the desk's first observation | quote grade E, Tradier sandbox, 15-min delayed, as of 19:31 UTC | your plan: close | not an instruction to trade; you act in your brokerage account.
pre-close | verdicts: 2 EXIT SIGNAL, 0 UNAVAILABLE, 1 WATCH, 0 HOLD | open signals: 4 | data age: 18 min | rules 1.0.0, sha cc09859c | verify live quotes in your broker before acting
deterministic rules you adopted · posted by an AI assistant · not personalized investment advice · data delayed 15 min, as of 19:31 UTC
```

The lean profile costs 41 runs per week; the active profile, which adds four book checks, costs 61
(`routine_audit.py --budget`). [docs/MONITORING.md](docs/MONITORING.md) has the whole schedule.

## Controls

- **It never trades.** No tool in this repository can send anything but a read-only market-data request, and no Bot
  holds a brokerage credential. You act in your own brokerage account.
- **One credential.** The only one is a Tradier sandbox market-data token, which cannot reach a live account. You add
  it through the secure secret card; it is never pasted in chat.
- **Every number comes from a tool,** with its source, its data delay and a UTC time. The Bots never add, round or
  restate a number.
- **UNAVAILABLE is a verdict.** A stale or missing input is reported as UNAVAILABLE, never as HOLD or a zero.
- **The rules are yours.** You adopt each one, with a typed phrase, outside regular trading hours. Every alert cites the rule's
  id, the rulebook version and its sha. Any change to the rulebook, `desk.toml` or `positions.csv` you have not
  acknowledged posts CONFIG CHANGED first.
- **Your tap is the signature.** Adopting rules, acknowledging a change, snoozing or marking a signal acted, and
  recording the connector readback run only after you approve their Ask-first card.
- **It is a level-setter and an interval checker, not a real-time watcher.** The data is 15 minutes delayed and
  routines can start late. The threshold-level sheet gives each rule as a price, so you can set your broker's own
  alerts, which watch in real time.
- **The dead-man rule.** On a trading day, no Morning Desk Sheet by 08:00 ET, or no pre-close line by 16:15 ET, means
  the desk is not running. Rely on your broker's alerts set from the last threshold-level sheet, and answer any prompt
  that asks to keep the routines running.

How each control is enforced, and what it does not cover, is in [SECURITY.md](SECURITY.md).

## Runtimes

| Runtime | How | Page |
| --- | --- | --- |
| Grok Bot | the Start prompt above and [SETUP.md](SETUP.md) | [docs/aegis-grok-bot.md](docs/aegis-grok-bot.md) |
| Grok Build | `grok plugin install fairgradient-ai/aegis-grok-desk@v0.1.0 --trust` | [docs/aegis-grok-build.md](docs/aegis-grok-build.md) |
| Claude Code | `/plugin marketplace add fairgradient-ai/aegis-grok-desk`, then `/plugin install aegis-desk@aegis-desk` | [docs/aegis-claude-code.md](docs/aegis-claude-code.md) |
| Cursor | `.cursor-plugin/plugin.json` (skills, agents, the desk rule and `hooks/cursor-hooks.json`) | [docs/aegis-cursor.md](docs/aegis-cursor.md) |
| Codex | `.agents/plugins/marketplace.json` | [docs/aegis-codex.md](docs/aegis-codex.md) |
| Gemini CLI | `gemini-extension.json` with `GEMINI.md` | [docs/aegis-gemini-cli.md](docs/aegis-gemini-cli.md) |

Every install is checked at release. Grok Build, Claude Code and Cursor also run a hook, `hooks/broker_write_gate.py`,
that denies a shell command or MCP tool call that looks like a broker write. It is a backstop, not the guarantee;
Grok Bot runs no plugin hooks.

## Inside the repository

```text
SETUP.md     the runbook a Grok Bot follows to build the desk
agents/      the Bot profiles: profile card and full system prompt
skills/      portable skills (aegis-*), including the generated role skills
scripts/     standard-library tools, the engine (scripts/aegis_desk/) and the repository gates
routines/    the routine manifest (owner, schedule, steps, quiet rule) and the Auto-review texts
rules/       the desk rule and the example exit rules (inert until you adopt them)
hooks/       the broker-write gate and its registrations
template/    the Grok Bot template contract
tests/       unit tests, gate tests and the synthetic fixture desk
docs/        architecture, data formats, monitoring, provenance, runtime pages, the tool reference
```

The tool reference, generated from the tools themselves, is [docs/TOOLS.md](docs/TOOLS.md). How it fits together is
[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md); common questions are in [docs/FAQ.md](docs/FAQ.md).

## License and credit

MIT licensed (FairGradient). Repository mechanics are adapted from
[HyperGrok Trading Desk](https://github.com/galleonlabs/hypergrok-trading-desk) by Andrew Wilkinson and Galleon Labs,
used under the MIT license; see [ATTRIBUTION.md](ATTRIBUTION.md). The roles, skills, rules and tools are Aegis Desk's
own.

Aegis Desk is documentation and tools, not personalized investment advice. Options involve substantial risk.
