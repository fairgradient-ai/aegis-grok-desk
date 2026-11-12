# Aegis Desk on Grok Build

Grok Build loads the desk as a plugin: its skills, its four role profiles and a hook that denies a shell command or
MCP tool call that looks like a broker write. For the desk that runs on its own every trading day, use
[Grok Bot](aegis-grok-bot.md); Grok Build is for reading the desk, running its tools and monitoring from your own
machine.

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

## Install

Pinned to the release tag, trusted so that its hook runs:

```text
grok plugin install fairgradient-ai/aegis-grok-desk@v0.1.1 --trust
```

Or through the marketplace in `.grok-plugin/marketplace.json`, whose source is pinned to the same tag:

```text
grok plugin marketplace add fairgradient-ai/aegis-grok-desk
grok plugin install aegis-desk --trust
```

Grok Build runs a plugin's hooks only when the plugin was installed with `--trust`.

## What loads

Grok Build reads the first manifest it finds: `plugin.json`, then `.grok-plugin/plugin.json`, then
`.claude-plugin/plugin.json`. None of them declares skills, agents or hooks, so Grok Build discovers `skills/`,
`agents/` and `hooks/hooks.json` on its own. `scripts/check_manifests.py` fails the repository gate if a manifest
declares them. What Grok Build actually lists is checked at release (below).

`rules/aegis-desk.mdc` is Cursor's file; this page does not claim that Grok Build loads it.

## What protects you here

1. **No write code.** The repository holds no broker write path; `bash scripts/check.sh` proves it
   ([SECURITY.md](../SECURITY.md), layers 1 to 3).
2. **One sandbox token.** The only credential the desk uses is a Tradier sandbox market-data token, which cannot reach a
   live account. Keep anything that can reach real money out of the environment Grok Build runs in.
3. **No MCP server that can trade.** Configure no MCP server with tools that can place or change orders.
4. **The hook, a backstop.** `hooks/hooks.json` runs `hooks/broker_write_gate.py` before every shell call
   (`Bash|PowerShell|Monitor|run_terminal_command`) and every MCP call (`.+__.+`). It is described below. Grok Build hooks fail
   open on a timeout, a crash or malformed output, so the guarantee rests on layers 1 and 2, not on the hook.
5. **The deny rules in `~/.grok/config.toml`.** As Grok Build documents them, `[permission]` deny rules are
   deterministic, never fail open, win over any allow rule and still apply when every tool call is approved
   automatically. The block below is ready to paste.

## The broker-write hook

It reads only the call: the command text, the MCP tool name and the MCP input. It never opens a file the command names.

- **It denies** an order call in code, an HTTP write (a write method or a request body) to a broker host or to an
  account or order path, any HTTP call to a broker host outside Tradier's market-data paths (`/v1/markets/`), an MCP
  tool whose name holds an order verb as a whole word or closes a position, an MCP input with an order key or an
  order-shaped value, and any shell or MCP call Grok Build marks as truncated (`toolInputTruncated`).
- **It has no opinion** on reads: `grep`, `cat`, `ls`, `find` and the other read commands, `git status`, `git log`,
  `git diff`, `git commit` and git's other read subcommands, the desk's own tools, and every other tool such as Write
  or Read. Any other git subcommand, such as `git rebase -x`, is read in full like any other command. It gives no opinion on input it cannot parse either: that is a
  documented fail-open.
- **A deny** exits 2 and prints one JSON line carrying both `"decision": "deny"` and Claude Code's
  `"permissionDecision": "deny"`, with the reason on stderr too. The reason always ends with the boundary clause.
  Probed on this release, the shell sample of the release check gives:

  ```text
  Aegis Desk denied this call: it looks like a broker write (an HTTP write to /v1/accounts). This desk never places, modifies or cancels orders; you act in your brokerage account.
  ```

- **Known false positives,** by design: an MCP tool whose name or identifier input carries an order verb as a whole word
  (a pull-request review tool, a `replace_text` tool, a one-word search for "order"), free text that quotes a write
  sample, a shell command that writes a file holding a write sample, and any HTTP call to a broker host outside
  Tradier's market-data paths, a broker's own web page included. The desk ships no MCP server; if one ever wrapped the
  structuring tools, an input key named `trade` would be denied as an order key, so such a wrapper names it `trade_id`.
  On the command line, `--trade AG-...` gives the gate no opinion. A sort direction such as `"order": "desc"` is not one
  of them: the gate reads it as a read.
- **Not covered:** aliases and functions defined earlier, variables, encodings such as base64, a script written first
  and run later, a broker command-line tool that speaks no HTTP, a broker host behind a proxy, and an MCP tool that
  changes a position under a name the gate's rules do not name.

The full contract, with every pattern, is the docstring of `hooks/broker_write_gate.py`.

## Deny rules for `~/.grok/config.toml`

Paste this block into `~/.grok/config.toml`. If the file already has a `[permission]` table, add these entries to its
`rules` array instead: TOML allows only one `[permission]` table.

```toml
# Aegis Desk: deterministic deny rules for Grok Build. Paste into ~/.grok/config.toml.
# If the file already has a [permission] table, add these entries to its rules array instead.
[permission]
rules = [
  { action = "deny", tool = "bash", pattern = "*tradier.com/v1/accounts*" },
  { action = "deny", tool = "bash", pattern = "*/v1/accounts/*/orders*" },
  { action = "deny", tool = "bash", pattern = "*curl*-X*POST*tradier.com*" },
  { action = "deny", tool = "bash", pattern = "*curl*tradier.com*-X*POST*" },
  { action = "deny", tool = "bash", pattern = "*curl*--data*tradier.com*" },
  { action = "deny", tool = "bash", pattern = "*curl*tradier.com*--data*" },
  { action = "deny", tool = "MCPTool", pattern = "*order*" },
  { action = "deny", tool = "MCPTool", pattern = "*trade*" },
  { action = "deny", tool = "MCPTool", pattern = "*cancel*" },
  { action = "deny", tool = "MCPTool", pattern = "*replace*" },
  { action = "deny", tool = "MCPTool", pattern = "*submit*" },
  { action = "deny", tool = "MCPTool", pattern = "*buy*" },
  { action = "deny", tool = "MCPTool", pattern = "*sell*" },
  { action = "deny", tool = "WebFetch", pattern = "*tradier.com/v1/accounts*" },
]
```

- Every Tradier account and order request goes under `/v1/accounts`, so the first rule covers them all. The `curl`
  rules add a POST or a request body that `curl` sends to any other Tradier path.
- The block parses as TOML (14 rules), with Python's `tomllib` and with the desk's own TOML reader on Python 3.9.
- How Grok Build matches a pattern (the whole string or a part, whether `*` crosses `/` and spaces, case) and what the
  `MCPTool` and `WebFetch` patterns are matched against are checked at release.

## Release checks

These are observed at release, before the tag. Until then, nothing on this page claims that Grok Build ran the hook or
honoured a deny.

1. `grok plugin details aegis-desk` and `grok inspect --json` list the skills, the role profiles and the plugin's
   `PreToolUse` hooks, once each; the `/hooks` tab shows them.
2. Grok Build is asked to run this sample. It points at the loopback discard port, so it does nothing even if a hook
   failed open. The hook refuses it with the reason above:

   ```text
   curl -X POST https://127.0.0.1:9/v1/accounts/AEGIS-TEST/orders -d class=option
   ```

3. A stub MCP tool named `blackhole__place_order` is refused. Probed on this release, the reason is
   `Aegis Desk denied this call: it looks like a broker write (an MCP tool named blackhole__place_order (order)). This desk never places, modifies or cancels orders; you act in your brokerage account.`
4. With the hook disabled, the same two calls are refused by the `[permission]` rules alone.

## Monitoring from your own machine

This is optional. The routines that run your desk live on Grok Bot. You can also run the routine entry point from a
clone on your own machine.

- Use a pinned shallow clone, not the plugin install directory, which may not be a git checkout:

  ```text
  git clone --depth 1 --branch v0.1.1 https://github.com/fairgradient-ai/aegis-grok-desk.git
  ```

  `routine.py` runs no step unless the checkout is the clean tag; on this clone the pin check reads
  `release v0.1.1 at <commit>, clean`.
- Keep the desk root outside the clone. A desk root inside it is refused (exit 2), because the run's files would make
  the checkout dirty. Pass it every time: `python3 scripts/routine.py <routine id> --desk-root <your desk directory>`.
  The desk root holds `desk.toml`, `positions.csv` and your adopted rules (SETUP step 9).
- First try it offline on a copy of the fixture desk, made outside the clone. It needs no token and no network:

  ```text
  cp -r tests/fixtures/desk ../aegis-fixture-desk
  python3 scripts/routine.py book-check --slot 14:05 --offline --desk-root ../aegis-fixture-desk --fixture-set regular --now 2026-11-03T19:50:00Z
  ```

  Synthetic, the first and last lines on that copy:

  ```text
  CONFIG CHANGED | desk.toml: not acknowledged -> sha 0af433cf | positions.csv: not acknowledged -> sha 9483c8f3 | adopted rulebook: not acknowledged -> sha cc09859c | evaluated with the files as they are now | acknowledge with ack.py config --owner-tapped
  deterministic rules you adopted · posted by an AI assistant · not personalized investment advice · data delayed 15 min, as of 19:31 UTC
  ```

- This release ships no scheduler for your machine and no delivery channel: the command prints its message in your
  terminal. The same delays apply: the data is 15 minutes old.

## Other runtimes

[Grok Bot](aegis-grok-bot.md) · [Claude Code](aegis-claude-code.md) · [Cursor](aegis-cursor.md) ·
[Codex](aegis-codex.md) · [Gemini CLI](aegis-gemini-cli.md) · the tool reference [TOOLS.md](TOOLS.md)
