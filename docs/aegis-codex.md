# Aegis Desk on Codex

Codex can install the desk from its marketplace entry and read its skills. The role profiles are read as labelled
passes, one role at a time. No hook runs for Codex. The desk that runs on its own every trading day lives on
[Grok Bot](aegis-grok-bot.md).

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

## Install

```text
codex plugin marketplace add fairgradient-ai/aegis-grok-desk
codex plugin add aegis-desk@aegis-desk
```

The entry is `.agents/plugins/marketplace.json`. Its source is pinned to the tag `v0.2.2`. Whether Codex installs
from it as written is checked at release. Without the marketplace, use the pinned clone:

```text
git clone --depth 1 --branch v0.2.2 https://github.com/fairgradient-ai/aegis-grok-desk.git
```

## What loads

- **The skills** under `skills/`, `aegis-desk-charter` first. The list is in [skills/README.md](../skills/README.md).
  Which components Codex loads from the plugin is checked at release; the safe statement is the skills only.
- **The role profiles** under `agents/`, read as labelled passes: ask Codex to read `agents/desk-lead.md`,
  `agents/exit-sentinel.md` or `agents/position-control.md` and answer as that role, one role at a time.
- **`AGENTS.md`**, the contributor guide that agent runtimes read. It repeats the desk rule of
  `rules/aegis-desk.mdc` word for word; `scripts/check_manifests.py` fails the repository gate if the two drift apart.

## What protects you here

There is no hook for Codex. The controls are the code and the credential:

- the repository holds no broker write path, and `bash scripts/check.sh` proves it ([SECURITY.md](../SECURITY.md),
  layers 1 to 3);
- the only credential the desk uses is an Alpaca paper key pair, used only to read market data, which cannot reach a
  live account. Keep anything that can reach real money out of the environment Codex runs in, and configure no MCP
  server whose tools can place or change orders.

A first run needs no key: the recorded synthetic snapshot in the README's [Try it now](../README.md#try-it-now).

## Other runtimes

[Grok Bot](aegis-grok-bot.md) · [Grok Build](aegis-grok-build.md) · [Claude Code](aegis-claude-code.md) ·
[Cursor](aegis-cursor.md) · [Gemini CLI](aegis-gemini-cli.md) · the tool reference [TOOLS.md](TOOLS.md)
