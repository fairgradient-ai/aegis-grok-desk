# Aegis Desk on Cursor

Cursor loads the desk from `.cursor-plugin/plugin.json`: the skills, the four role profiles, the desk rule and a
fail-closed hook that denies a shell command or MCP tool call that looks like a broker write. Use it to read the desk,
run its tools and work on the repository. The desk that runs on its own every trading day lives on
[Grok Bot](aegis-grok-bot.md).

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

## Install

Clone the reviewed release:

```text
git clone --depth 1 --branch v0.2.2 https://github.com/fairgradient-ai/aegis-grok-desk.git
```

Open the `aegis-grok-desk` folder in Cursor and enable the plugin. There is no Cursor marketplace or directory listing
for this repository.

## What loads

Cursor reads only `.cursor-plugin/plugin.json`, and each component it declares replaces Cursor's own folder discovery
for that component:

| Key | Path | What it is |
| --- | --- | --- |
| `skills` | `./skills/` | the desk skills, `aegis-desk-charter` first ([skills/README.md](../skills/README.md)) |
| `agents` | `./agents/` | the Aegis Desk Lead, the Aegis Exit Sentinel and Aegis Position Control |
| `rules` | `./rules/aegis-desk.mdc` | the desk rule, applied to every session (`alwaysApply: true`) |
| `hooks` | `./hooks/cursor-hooks.json` | the broker-write gate in Cursor's own hook format |

The `rules` key names the one desk-rule file, not the `rules/` folder, so it does not point Cursor at the example
rulebook under `rules/examples/`. What Cursor lists as rules is checked at release.
`scripts/check_manifests.py` checks that every declared path exists and that the hooks file wires the gate as
below: exactly the two events, the exact command and `failClosed: true`, and no other key. A `matcher` key, for
example, would limit which calls run the gate, so the check refuses it.

## The fail-closed gate

`hooks/cursor-hooks.json` runs the same gate as the other runtimes, in Cursor mode:

```json
{
  "version": 1,
  "hooks": {
    "beforeShellExecution": [
      {"command": "python3 \"${CURSOR_PLUGIN_ROOT}/hooks/broker_write_gate.py\" --runtime cursor", "timeout": 10, "failClosed": true}
    ],
    "beforeMCPExecution": [
      {"command": "python3 \"${CURSOR_PLUGIN_ROOT}/hooks/broker_write_gate.py\" --runtime cursor", "timeout": 10, "failClosed": true}
    ]
  }
}
```

- Before a shell command, Cursor sends the command text. Before an MCP call, it sends the server name, the tool name and
  the tool input, and for a stdio server also the command that starts the server. The gate checks the name
  `<server>__<tool>` and the decoded input, whatever the server type; the start command is not the call, so the gate
  does not read it.
- The gate denies what it denies everywhere: an order call in code, an HTTP write to a broker host or to an account or
  order path, any other HTTP call to a broker host (reads included), an MCP tool whose name holds an order
  verb as a whole word or closes a position, and an MCP input with an order key or an order-shaped value.
- **A deny** exits 2. Its JSON carries the reason in the two fields Cursor documents, `user_message` and
  `agent_message`. Probed on this release, the release-check shell sample (on the
  [Grok Build page](aegis-grok-build.md#release-checks)) gives:

  ```text
  {"permission": "deny", "user_message": "Aegis Desk denied this call: it looks like a broker write (an HTTP write to /v1/accounts). This desk never places, modifies or cancels orders; you act in your brokerage account.", "agent_message": "Aegis Desk denied this call: it looks like a broker write (an HTTP write to /v1/accounts). This desk never places, modifies or cancels orders; you act in your brokerage account."}
  ```

- **Every other call gets `ask`.** Cursor documents that a fail-closed hook which prints nothing has failed, and blocks
  the call. So for a call it has no opinion on, such as a read, the gate exits 0 and prints `{"permission": "ask"}`:
  Cursor then asks you before the call runs. The gate never answers `allow`, so it never approves a call for you.
  Probed on this release, `git status` gives exactly that line. If you let Cursor run commands without asking, the
  hook brings the question back for every shell and MCP call.
- **Fail-closed.** With `failClosed: true`, a hook that crashes, times out or prints nothing blocks the call instead of
  letting it run, as Cursor documents it. On that documented behaviour, Cursor is the one runtime where a crash of the
  gate does not let the call through. Input the gate cannot parse at all gets no output, so Cursor blocks it. This is
  checked at release, with the other items below.
- **The hook never reads a file** the command names, so a write inside a script the command runs is not seen. The
  guarantee rests on the code and the credential ([SECURITY.md](../SECURITY.md), layers 1 and 2), not on the hook.

## Release checks

These are observed at release, at one Cursor install. Until then, nothing on this page claims that Cursor ran the hook.

1. Cursor lists the skills, the four agents and the desk rule.
2. Cursor expands `${CURSOR_PLUGIN_ROOT}` and runs the hook from the plugin directory.
3. A read such as `git status` gets the gate's `{"permission": "ask"}` answer, and Cursor asks before it runs it. If
   the install shows that Cursor reads `ask` differently, the hook is not claimed.
4. The release-check shell sample and a stub MCP tool, `blackhole__place_order`, are refused with the gate's reason.

If these are not confirmed at that install, the release says "Cursor: skills only": the skills, the agents and the
desk rule load, and the hook is not claimed.

## Other runtimes

[Grok Bot](aegis-grok-bot.md) · [Grok Build](aegis-grok-build.md) · [Claude Code](aegis-claude-code.md) ·
[Codex](aegis-codex.md) · [Gemini CLI](aegis-gemini-cli.md) · the tool reference [TOOLS.md](TOOLS.md)
