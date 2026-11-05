# Aegis Desk on Gemini CLI

Gemini CLI installs the desk as an extension whose context file is `GEMINI.md`, which holds the desk rule. The
skills and the role profiles are there to read, one role at a time. No hook is claimed for Gemini CLI.
The desk that runs on its own every trading day lives on [Grok Bot](aegis-grok-bot.md).

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

## Install

```text
gemini extensions install https://github.com/fairgradient-ai/aegis-grok-desk
```

This installs the latest GitHub release of the repository. The first release is 0.1.0. Until it is published, there
is nothing to install. Without the extension, use the pinned clone:

```text
git clone --depth 1 --branch v0.1.0 https://github.com/fairgradient-ai/aegis-grok-desk.git
```

## What loads

- **`gemini-extension.json`** names `GEMINI.md` as its context file. `GEMINI.md` repeats the desk rule of
  `rules/aegis-desk.mdc` word for word; `scripts/check_manifests.py` fails the repository gate if the two drift apart.
- **The skills** under `skills/` and **the role profiles** under `agents/` are in the extension to read: ask Gemini CLI
  to load a skill by name, `aegis-desk-charter` first, or to answer as one role from its `agents/` file. Whether
  Gemini CLI loads `skills/` or `agents/` on its own is checked at release.

## What protects you here

No hook is claimed for Gemini CLI. The controls are the code and the credential:

- the repository holds no broker write path, and `bash scripts/check.sh` proves it ([SECURITY.md](../SECURITY.md),
  layers 1 to 3);
- the only credential the desk uses is a Tradier sandbox market-data token, which cannot reach a live account. Keep
  anything that can reach real money out of the environment Gemini CLI runs in.

## Release check

The extension also contains `hooks/hooks.json`, the hook file for Claude Code and Grok Build. At release, the install
is checked for any error Gemini CLI reports while reading it.

A first run needs no token: the recorded synthetic snapshot in the README's [Try it now](../README.md#try-it-now).

## Other runtimes

[Grok Bot](aegis-grok-bot.md) · [Grok Build](aegis-grok-build.md) · [Claude Code](aegis-claude-code.md) ·
[Cursor](aegis-cursor.md) · [Codex](aegis-codex.md) · the tool reference [TOOLS.md](TOOLS.md)
