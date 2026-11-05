# Security policy

## Reporting

Report a vulnerability privately through GitHub security advisories on this repository. Never put a real token,
account number or personal record in a report.

A vulnerability is any prose, manifest or code that could lead a Bot to place, modify or cancel an order, to request,
store or print a credential, or to report a failed read as a quiet negative verdict. A way around the AST no-write
gate, the alert lint, the release pin check or the broker-write hook counts too, and so does a tool output that reads
as an order.

## The no-trade guarantee, honestly

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

Layers 1 to 3 prove that the shipped code cannot write to a broker. Layers 4 to 8 govern what a Bot could do at
runtime with the tools and credentials on its computer. The only credential the desk holds is a Tradier sandbox
market-data token. It cannot reach a live account. It could place paper orders on the sandbox host, which is why the
write-side Ask-first rule, the hook and the connector check exist. Production market data is outside the guarantee
and refused in this release.

### What proves the code cannot write (layers 1 to 3)

1. **No write code.** `scripts/aegis_desk/tradier_client.py` is the only module that may open a network connection.
   Every request it makes is a literal GET with no body, to the pinned host `sandbox.tradier.com`, on one of seven
   allow-listed paths (`/v1/markets/quotes`, `/v1/markets/options/chains`, `/v1/markets/options/expirations`,
   `/v1/markets/options/strikes`, `/v1/markets/history`, `/v1/markets/clock`, `/v1/markets/calendar`), with no
   redirects, no proxy and TLS 1.2 or later. `scripts/check_no_write_paths.py` proves this on every gate run by
   reading the syntax tree of every module under `scripts/`, `hooks/` and `tests/`: network modules only in the
   client and GET-only there, `subprocess` only in the listed gates and `tests/gates/`, no dynamic loading, no
   reflection it cannot read. Two independent tests back it: a loopback fake server that records any POST, PUT, PATCH or
   DELETE as a violation, and a patch of `http.client.HTTPConnection.request` that asserts the method GET and no body
   across a journey touching every endpoint. The same gate scans the instruction text (skills, agents, docs, rules,
   template, routines and the root instruction files) for write-shaped snippets; a documented counter-example needs an
   exact entry in `scripts/no_write_allowlist.txt`, and an entry that matches nothing fails.
2. **Credentials.** The doctor (`scripts/desk_doctor.py`) checks them. Check 4 names the token's source, never its
   value; a token file must carry no group or other permission bits. Check 5 makes one GET of the sandbox clock: a 401
   or 403 fails with `not a sandbox token: remove and rotate`, and a production host fails with no request:
   `production host is outside the guarantee before the market-data proxy`. Check 6 derives the machine role from the
   environment (`owner_private` only when `AEGIS_MACHINE_ROLE` says so and `/workspace` does not exist); `desk.toml`
   cannot set it. Check 7 fails on any environment variable whose name looks like a broker credential (names only,
   never values).
3. **Structural lint.** `bash scripts/check.sh` steps 1 to 3: every agent declares `writes_to_broker: false` exactly
   and `model: inherit`, and carries the boundary sentence inside its System prompt; every skill has the `aegis-`
   prefix, a `LICENSE` and an `ATTRIBUTION.md` with its origin; every `scripts/<name>.py` named in the instruction files
   exists.

### What governs runtime (layers 4 to 8)

4. **No trade-capable tool surface.** Connected apps and MCP servers are account-wide on Grok Bot. At setup you read
   both lists back and confirm that none can place or change an order; `ack.py connectors --owner-tapped` records that
   readback, and the doctor's check 10 asks again after 30 days. The agent files declare `tools: Read, Grep, Glob, Bash`,
   so a Claude Code subagent inherits no MCP tools. You never sign in to a brokerage in the Bot computer's browser.
5. **The broker-write hook (a backstop).** `hooks/broker_write_gate.py` is registered by `hooks/hooks.json` for Claude
   Code and Grok Build (`PreToolUse`, a shell group `Bash|PowerShell|Monitor|run_terminal_command` and an MCP group
   `.+__.+`) and by `hooks/cursor-hooks.json` for Cursor (`beforeShellExecution` and `beforeMCPExecution`,
   `--runtime cursor`, `failClosed: true`). It denies, with exit 2, a shell command or MCP call that looks like a broker
   write: an order call, an HTTP write to a broker host or to an account or order path, an HTTP call to a broker host
   outside Tradier's market-data paths, an MCP tool whose name holds an order verb or closes a position, an input with
   an order key, and any input it was not shown in full. It gives no opinion on reads, and none on input it cannot
   parse (a documented fail-open). In Cursor, where a fail-closed hook that prints nothing blocks the call, its no
   opinion is an explicit `ask`, so Cursor asks you; it never answers `allow`. It reads only the call, never a file
   the call names. `check_manifests.py` asserts the wiring exactly: the event names, the two matchers, the command,
   `--runtime cursor` and `failClosed`, and no other key at any level of either file.
   Its limits:
   - Claude Code and Grok Build hooks fail open on a timeout, a crash or malformed output; the guarantee rests on
     layers 1 and 2, not on the hook.
   - Grok Bot runs no plugin hooks; there the Auto-review rules of layer 6 are the runtime control.
   - It does not see aliases, variables, encodings, a script written first and run later, a broker command-line tool
     that speaks no HTTP, or a broker host behind a proxy.
   - Whether each runtime runs the hook and honours its deny is checked at release.

   For Grok Build, deterministic deny rules for `~/.grok/config.toml` (they do not fail open) are on the
   [Grok Build page](docs/aegis-grok-build.md), to be checked at release.
6. **Grok Bot Auto-review rules.** You paste three rules at setup, printed by `routine_audit.py --rules-text` from
   `routines/autoreview.toml` (SETUP step 7):

   ```text
   Allow automatically: the command python3 /workspace/aegis-grok-desk/scripts/routine.py followed by exactly one routine id from this list and, optionally, --slot and a time HH:MM, with nothing else on the command line (no pipe, redirection, semicolon or second command): preopen, book-check, final-call, eod, eod-brief, post-expiry. These runs read delayed market data through the desk's GET-only sandbox client and write files only under /workspace/aegis-desk; they cannot place, modify or cancel an order.
   Ask first: placing, modifying or cancelling an order; moving funds; any POST, PUT, PATCH or DELETE request to a brokerage, including any request to tradier.com/v1/accounts; using a connected app or MCP tool that can place or change orders; signing in to a brokerage; browsing outside the desk's sourcing allow-list.
   Ask first: any command that carries --owner-tapped or --allow-rth, including ack.py snooze, ack.py acted and ack.py config; rules_adopt.py; desk_init.py. These record the owner's own decisions: run them only after the owner taps approve on this card.
   ```

   Auto-review is model-based and an "Ask first" rule wins over an "Allow" rule. Your tap on an Ask-first card is the
   signature on every owner-authority command.
7. **Prompts and skills.** Every role answers a request to trade with the boundary sentence. No skill documents a
   write path. Alerts say only that your own rule fired, with the numbers that made it fire and your plan; they carry no
   order fields, no strikes or expiries to move to and no ranked alternatives. Every posted message passes the alert
   lint (`scripts/alert_lint.py`): only numbers the tools emitted, the exact footer, no trade or advice language.
8. **Red-team tests.** `scripts/cli_journeys.py` screens the output of every command journey for order-shaped text.
   The alert lint has red-team fixtures (`tests/fixtures/alerts/`: invented numbers, advice, order fields, order kinds
   and imperative starts) that must fail. The hook's tests run its deny and no-opinion samples in every event shape.

## The release pin check: what it covers and what it does not

`routine.py` runs no step unless the checkout is pinned: a git work tree whose HEAD is the commit of the tag `v0.1.0`
and whose `git status` lists no path, tracked or untracked (ignored paths are not listed). Otherwise it prints one
INCIDENT line, writes an incident file and exits 20. Within that check:

- The step tools are imported only after the pin check passes, so a modified step module never runs on a refused
  checkout. `routine.py` neither writes bytecode into the checkout nor reads any cached there.
- git runs four literal read-only commands, never writes git configuration, and runs with every `GIT_*` environment
  variable dropped (except `GIT_CEILING_DIRECTORIES` and `GIT_CONFIG_NOSYSTEM`, which only narrow what git reads), with
  `core.fsmonitor=false` and `core.hooksPath` and `core.attributesFile` set to the null device.
- Your own global git configuration is still read; its `safe.directory` entry is the SETUP fix when git refuses a
  checkout owned by another user.

What it does not cover:

- Interactive tool runs are not pin-checked. The doctor's check 1 (`release_pin`) reports any drift.
- It detects drift and unreviewed edits. It is not a defence against a hostile local writer.

## Accepted residual: git clean filters

A repository's own clean filter (`filter.<name>.clean`), named in its `.git/config` together with an attributes file,
runs during `git status`. The pin check cannot switch that off. Writing a repository's `.git/` is a file write that
this read-only check can neither make nor prevent, and a writer of `.git/config` could equally replace any module of
the checkout. This residual is accepted as part of the file-write class.

## Production mode

Refused in this release. `desk_init.py` refuses a production host, the quote cache refuses it with
`production market data is not supported before the market-data proxy (Stage 3); use the sandbox host`, and the
doctor's checks 5 and 8 fail on it. A production token can trade, so it stays outside the guarantee. A local read-only
proxy that would hold the production token outside the Bots' reach, md_proxy, is planned for Stage 3 (not in this release).

## Supported versions

The latest release. A release is a tag of this repository (`v0.1.0` is the first); every manifest, pin and template
hash names exactly one release.
