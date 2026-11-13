# Aegis Desk on Claude Code

Claude Code loads the desk as a plugin: its skills, its four role profiles as subagents, and a hook that denies a
shell command or MCP tool call that looks like a broker write. Use it to read the desk, run its tools and work on the
repository. The desk that runs on its own every trading day lives on [Grok Bot](aegis-grok-bot.md).

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

## Install

In Claude Code:

```text
/plugin marketplace add fairgradient-ai/aegis-grok-desk
/plugin install aegis-desk@aegis-desk
```

Then load the charter first:

```text
/aegis-desk:aegis-desk-charter
```

The marketplace entry in `.claude-plugin/marketplace.json` pins its GitHub source to the tag `v0.2.0`. Whether Claude
Code honours that pin is checked at release. If it does not, the marketplace follows the default branch, and the
pinned clone below is the reviewed way in.

## What loads

- **The skills** under `skills/`, each as `/aegis-desk:<skill name>`. The list is in
  [skills/README.md](../skills/README.md).
- **The role profiles** under `agents/` as subagents: the Aegis Desk Lead, the Aegis Exit Sentinel, Aegis Position
  Control and the Aegis Structuring Desk. Each declares `tools: Read, Grep, Glob, Bash`, so a subagent inherits no MCP
  tools.
- **The hook** from `hooks/hooks.json`, which Claude Code loads on its own. `.claude-plugin/plugin.json` declares no
  `hooks` key, because Claude Code rejects a second declaration of the same hooks file. `scripts/check_manifests.py`
  fails the repository gate if a manifest adds one.

The root `CLAUDE.md` is the contributor guide for working on this repository. A plugin validation may warn that it is
not loaded as project context; that warning is expected.

## The broker-write hook

`hooks/hooks.json` runs `hooks/broker_write_gate.py` before every call of the tools that run a shell command, `Bash`,
`PowerShell` and `Monitor` (matcher `Bash|PowerShell|Monitor|run_terminal_command`), and of every MCP tool (matcher
`.+__.+`, which matches Claude Code's `mcp__<server>__<tool>` names). Its timeout is 10 seconds. Which Claude Code
tools run a shell command is checked at release: a new one that the matcher does not name is not sent to the gate.

- **It denies** an order call in code, an HTTP write (a write method or a request body) to a broker host or to an
  account or order path, an HTTP call to a broker host outside Tradier's market-data paths, an MCP tool whose name
  holds an order verb as a whole word or closes a position, and an MCP input with an order key or an order-shaped
  value.
- **It has no opinion** on reads (`grep`, `git`, `cat`, `ls`, `find` and the other read commands), on the desk's own
  tools, on every other tool such as Write or Read, and on input it cannot parse (a documented fail-open).
- **A deny** exits 2. Its JSON line carries `"hookSpecificOutput": {"hookEventName": "PreToolUse",
  "permissionDecision": "deny", ...}`, and the reason goes to stderr too. The reason names what the hook saw and ends
  with the boundary clause.
- **It is a backstop.** Claude Code hooks fail open on a timeout, a crash or malformed output, and the hook never reads
  a file the command names. The guarantee rests on the code and the credential ([SECURITY.md](../SECURITY.md),
  layers 1 and 2). The known false positives and the routes it does not cover are listed on the
  [Grok Build page](aegis-grok-build.md#the-broker-write-hook) and in the gate's docstring.

## Release checks

These are observed at release, before the tag. Observed on 2026-11-05 with Claude Code 2.1.318, headless
(`claude -p`), with the plugin loaded by `--plugin-dir` from a clean clone of the release candidate: checks 2 and 3
were refused with exactly the reasons below, the commands never ran, and a read (`git status --short`) ran with no
opinion from the hook. Check 1 is interactive and was not observed; both `PreToolUse` groups ran (`PreToolUse:Bash`
for the sample, `PreToolUse:mcp__blackhole__place_order` for the stub). The Aegis Structuring Desk then changed the
plugin's agents and skills, not the hook, so the checks ran again on 2026-11-11 with Claude Code 2.1.322 on a clean
clone of the Structuring Desk commit, with the same results; the session listed all four agents. Both runs are
recorded in [docs/RELEASE.md](RELEASE.md), step 2. The marketplace install is checked after the tag (the same step).

1. `/plugin` shows the plugin with no error, such as a duplicate hooks file; `/hooks` lists the two `PreToolUse`
   groups, and the shell matcher names every Claude Code tool that runs a shell command.
2. Claude Code is asked to run this sample. It points at the loopback discard port, so it does nothing even if the hook
   failed open. The hook refuses it:

   ```text
   curl -X POST https://127.0.0.1:9/v1/accounts/AEGIS-TEST/orders -d class=option
   ```

   Probed on this release, the reason is
   `Aegis Desk denied this call: it looks like a broker write (an HTTP write to /v1/accounts). This desk never places, modifies or cancels orders; you act in your brokerage account.`
3. A stub MCP tool, `mcp__blackhole__place_order`, is refused. Probed on this release, the reason is
   `Aegis Desk denied this call: it looks like a broker write (an MCP tool named blackhole__place_order (order)). This desk never places, modifies or cancels orders; you act in your brokerage account.`

## The pinned clone

The reviewed release, without the marketplace:

```text
git clone --depth 1 --branch v0.2.0 https://github.com/fairgradient-ai/aegis-grok-desk.git
cd aegis-grok-desk
bash scripts/check.sh
```

The gate takes a few minutes and ends with `ok: all repository gates passed for <the clone's path>`. A first run
needs no token: the recorded synthetic snapshot in the README's [Try it now](../README.md#try-it-now). To run the
routine entry point from this clone, follow [Monitoring from your own machine](aegis-grok-build.md#monitoring-from-your-own-machine):
the desk root stays outside the clone.

## Other runtimes

[Grok Bot](aegis-grok-bot.md) · [Grok Build](aegis-grok-build.md) · [Cursor](aegis-cursor.md) ·
[Codex](aegis-codex.md) · [Gemini CLI](aegis-gemini-cli.md) · the tool reference [TOOLS.md](TOOLS.md)
