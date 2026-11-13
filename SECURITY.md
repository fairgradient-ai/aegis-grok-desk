# Security policy

## Reporting

Report a vulnerability privately through GitHub security advisories on this repository. Never put a real key,
account number or personal record in a report.

A vulnerability is any prose, manifest or code that could lead a Bot to place, modify or cancel an order, to request,
store or print a credential, or to report a failed read as a quiet negative verdict. A way around the AST no-write
gate, the alert lint, the release pin check or the broker-write hook counts too, and so does a tool output that reads
as an order, a structuring answer the owner did not ask for, or a best candidate named outside the owner's criteria or
approval level.

## The no-trade guarantee, honestly

This desk never places, modifies or cancels orders. An exit alert tells you which of your adopted rules fired and why; you act in your brokerage account.

Layers 1 to 3 prove that the shipped code cannot write to a broker. Layers 4 to 8 govern what a Bot could do at
runtime with the tools and credentials on its computer. The only credential the desk holds is an Alpaca paper
key pair, used only to read market data. It cannot reach a live account. It could place paper orders on Alpaca's
paper trading host, which is why the write-side Ask-first rule, the hook and the connector check exist. A live key and
the live trading host are outside the guarantee and refused.

When each runtime control arrives on Grok Bot, honestly: setup is staged. The first run asks you for one thing: a free
Alpaca paper key pair, added through the secure secret card, never pasted in chat. Nothing else is asked,
so no Auto-review rule exists yet. The two Ask-first rules (writes, and owner authority) and the connected-apps check
come first when you say "add my positions", before the first owner-tapped command (`desk_init.py`) runs; whichever
later phrase you say first, SETUP section 9 starts with them, so no owner-authority command and no routine comes
before them. The Allow rule for `routine.py` comes when you say "turn on monitoring", before any desk routine is
created (the plan check before it creates and deletes one test routine that runs no command). Between the first run
and "add my positions", the desk holds only the paper key, which cannot reach a live account but could place
paper orders, and no rule yet makes Grok Bot ask you first. In that window the repository code still has no
write path: the market-data client sends GET requests only, and the AST gate of layer 1 proves it on every gate run.
No routine runs in that window, and the Lead runs no owner-authority command. A live key pasted by mistake fails
the first run's live Opening Bell: its contract listing is read on Alpaca's paper trading host, which refuses a key
from another environment with a 401 or 403, and the bell prints `UNAVAILABLE: not a paper key: ...` (SETUP section 7
tells the Lead to have both secrets deleted). The doctor's paper check (layer 2) rests on the same refusal from "add
my positions" on. That refusal is Alpaca's documented environment-mismatch error, not a published paper/live test,
so as defence in depth SETUP section 7 tells the owner to copy the key only from the Paper Trading dashboard, and the
Lead asks for a key ID that starts with `AK` (Alpaca's live prefix, a visual hint only) to be deleted at once; until
a live key is deleted, no Ask-first rule guards it.

### What proves the code cannot write (layers 1 to 3)

1. **No write code.** `scripts/aegis_desk/alpaca_client.py` is the only module that may open a network connection.
   Every request it makes is a literal GET with no body, to one of two pinned hosts on one of seven allow-listed
   paths: on the market-data host `data.alpaca.markets`, `/v2/stocks/quotes/latest`, `/v2/stocks/bars`,
   `/v1beta1/options/quotes/latest` and `/v1beta1/options/snapshots/<UNDERLYING>` (the underlying checked); on the
   paper trading host `paper-api.alpaca.markets`, only `/v2/clock`, `/v2/calendar` and `/v2/options/contracts`. The
   live trading host `api.alpaca.markets` is refused. There are no redirects, no proxy, TLS 1.2 or later, a cap on
   the answer's size, and an answer that echoes the key ID or the secret is refused. `scripts/check_no_write_paths.py` proves this on every gate run by
   reading the syntax tree of every module under `scripts/`, `hooks/` and `tests/`: network modules only in the
   client and GET-only there, `subprocess` only in the listed gates and `tests/gates/`, no dynamic loading, no
   reflection it cannot read. Two independent tests back it: a loopback fake server that records any POST, PUT, PATCH or
   DELETE as a violation, and a patch of `http.client.HTTPConnection.request` that asserts the method GET and no body
   across a journey touching every endpoint. The same gate scans the instruction text (skills, agents, docs, rules,
   template, routines and the root instruction files) for write-shaped snippets; a documented counter-example needs an
   exact entry in `scripts/no_write_allowlist.txt`, and an entry that matches nothing fails.
2. **Credentials.** The doctor (`scripts/desk_doctor.py`) checks them. Check 4 names the key pair's source (the two
   variable names, or the file), never a value; a credentials file must carry no group or other permission bits.
   Check 5 makes one GET of `/v2/clock` on the paper trading host, never the market-data host (which answers live keys
   too): a 401 or 403 fails with `not a paper key: remove it and add a paper key`, and a host other than `paper` fails
   with no request (`desk.toml` itself refuses `live` at load). It rests on Alpaca's documented environment-mismatch
   error (the paper host refuses a key from another environment with a 401 or 403); Alpaca publishes no separate
   paper/live test, which is why the copy-from-the-Paper-Trading-dashboard rule stays as defence in depth. Check 6 derives the machine role from the
   environment (`owner_private` only when `AEGIS_MACHINE_ROLE` says so and `/workspace` does not exist); `desk.toml`
   cannot set it. Check 7 fails on any environment variable whose name looks like a broker credential (names only,
   never values; exactly the configured key and secret names are exempt from the broker-name rule, so any other
   `ALPACA_*` name fails, but not from the rule against an `APCA` name that says live, production, trading or
   account), and
   on `APCA_API_BASE_URL` when it names the live trading host, the one value it reads.
3. **Structural lint.** `bash scripts/check.sh` steps 1 to 3: every agent declares `writes_to_broker: false` exactly
   and `model: inherit`, and carries the boundary sentence inside its System prompt; every skill has the `aegis-`
   prefix, a `LICENSE` and an `ATTRIBUTION.md` with its origin; every `scripts/<name>.py` named in the instruction files
   exists.

### What governs runtime (layers 4 to 8)

4. **No trade-capable tool surface.** Connected apps and MCP servers are account-wide on Grok Bot. When you say "add
   my positions", you confirm that none of them can place or change an order; `ack.py connectors --owner-tapped`
   records that answer, and the doctor's check 10 asks again after 30 days. The agent files declare `tools: Read, Grep, Glob, Bash`,
   so a Claude Code subagent inherits no MCP tools. You never sign in to a brokerage in the Bot computer's browser.
5. **The broker-write hook (a backstop).** `hooks/broker_write_gate.py` is registered by `hooks/hooks.json` for Claude
   Code and Grok Build (`PreToolUse`, a shell group `Bash|PowerShell|Monitor|run_terminal_command` and an MCP group
   `.+__.+`) and by `hooks/cursor-hooks.json` for Cursor (`beforeShellExecution` and `beforeMCPExecution`,
   `--runtime cursor`, `failClosed: true`). It denies, with exit 2, a shell command or MCP call that looks like a broker
   write: an order call, an HTTP write to a broker host or to an account or order path, any other HTTP call to a
   broker host (reads included), an MCP tool whose name holds an order verb or closes a position, an input with
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
6. **Grok Bot Auto-review rules.** You paste three rules, printed by `routine_audit.py --rules-text` from
   `routines/autoreview.toml`: the two Ask-first rules when you say "add my positions", and the Allow rule when you
   say "turn on monitoring" (SETUP step 9; the timing is stated above, under the guarantee):

   ```text
   Allow automatically: the command python3 /workspace/aegis-grok-desk/scripts/routine.py followed by exactly one routine id from this list and, optionally, --slot and a time HH:MM, with nothing else on the command line (no pipe, redirection, semicolon or second command): preopen, book-check, final-call, eod, eod-brief, post-expiry. These runs read delayed market data through the desk's GET-only Alpaca paper client and write files only under /workspace/aegis-desk; they cannot place, modify or cancel an order.
   Ask first: placing, modifying or cancelling an order; moving funds; any POST, PUT, PATCH or DELETE request to a brokerage, and any request to a brokerage's order, position or account endpoints, including any request to api.alpaca.markets, and any request to paper-api.alpaca.markets other than /v2/clock, /v2/calendar and /v2/options/contracts; using a connected app or MCP tool that can place or change orders; signing in to a brokerage; browsing outside the desk's sourcing allow-list.
   Ask first: any command that carries --owner-tapped or --allow-rth, including ack.py snooze, ack.py acted and ack.py config; rules_adopt.py; desk_init.py; exit_ticket.py, roll_analysis.py and propose.py; writing or replacing /workspace/aegis-desk/positions.csv. These record the owner's own decisions or answer the owner's own request: run them only after the owner taps approve on this card.
   ```

   Auto-review is model-based and an "Ask first" rule wins over an "Allow" rule. Your tap on an Ask-first card is the
   signature on every owner-authority command, and the record that you asked for each answer of the Structuring
   Desk.
7. **Prompts and skills.** Every role answers a request to trade with the boundary sentence. No skill documents a
   write path. Alerts say only that your own rule fired, with the numbers that made it fire and your plan; they carry no
   order fields, no strikes or expiries to move to and no ranked alternatives. Every posted message passes the alert
   lint (`scripts/alert_lint.py`): only numbers the tools emitted, the exact footer, no trade or advice language.

   The Structuring Desk's outputs (its tickets, its roll analyses and its proposals) come only on your request, never
   from a routine: `routine.py` refuses `exit_ticket`, `roll_analysis` and `propose` as steps, and the alert lint
   refuses a leg reference, a ranked candidate, an action tag or the first line of a structuring answer under the rules
   and market-snapshot footers. They carry no order fields: no trade-side word, no order type, no limit or stop price
   field, no duration, no route and no account number. A leg line names only the effect, the count and the contract
   (`leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call`), and you supply the limit. They pass the alert lint in
   its structuring mode, which adds a third footer and a fixed leg grammar, and refuses advice, preference, urgency,
   suitability, probability, risk, return and valuation language, any claim that a text is not advice, order-type and
   duration abbreviations, a price after "at", "for", "limit", "pay" or "price", and any line with a leg reference or a
   leg's effect words outside the fixed grammar. The lint is a pattern screen that refuses the forms it names; the
   tools' fixed wording is for counsel to sign ([docs/RELEASE.md](docs/RELEASE.md), step 4). They never print an OCC
   symbol or a cell you typed, such as an account name or a note. The details are in
   [docs/STRUCTURING.md](docs/STRUCTURING.md).
8. **Red-team tests.** `scripts/cli_journeys.py` screens the output of every command journey for order-shaped text.
   The alert lint has red-team fixtures (`tests/fixtures/alerts/`: invented numbers, advice, order fields, order kinds
   and imperative starts) that must fail. The hook's tests run its deny and no-opinion samples in every event shape.

## Structuring: what could steer a suggestion

The Structuring Desk names a best candidate from three inputs it does not check against your broker:
`structuring.toml`, the approval level in `desk.toml` and the shares in `positions.csv`. Each is guarded the same way.

- **Criteria tampering.** An edit to `structuring.toml` could change which candidate scores best. The file joins
  the CONFIG CHANGED set: a book check posts CONFIG CHANGED for an edit you did not acknowledge, and every structuring
  tool refuses to answer (exit 20, UNAVAILABLE) until you tap `ack.py config --owner-tapped`. The file's sha is
  printed in every roll analysis and proposal and recorded in `journal/structuring.jsonl`.
- **Coverage tampering.** An edit to `positions.csv` that adds shares could make an uncovered call look covered,
  and an edit to `desk.toml` could raise the approval level. The same fail-closed rule applies: while `desk.toml`,
  `positions.csv` or `structuring.toml` differ from what you acknowledged, the structuring tools print nothing else.
  A quarantined group anywhere in `positions.csv` could hide a short call that relies on your shares, so while one
  exists every structuring answer is UNAVAILABLE (`positions_quarantined`).
- **Out of scope.** The approval level is self-attested, and the desk does not see your broker's approval, your cash
  or your other holdings. Your broker's own checks decide what you can enter.

## The release pin check: what it covers and what it does not

`routine.py` runs no step unless the checkout is pinned: a git work tree whose HEAD is the commit of the tag `v0.2.0`
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

## Live mode

Refused. The desk holds an Alpaca paper key pair and never points at a live account: `desk.toml` must say `host =
"paper"`, and any other host stops it from loading (`[market_data] host must be "paper" (got "live"): the desk holds
a paper key only, and the live trading host is refused`). `desk_init.py` refuses a live host (`live_host`), the
client refuses the live trading host `api.alpaca.markets`, and the doctor's checks 5 and 8 fail on any host but
`paper`. A live key can trade, so it stays outside the guarantee.

## Supported versions

The latest release. A release is a tag of this repository: the letter v followed by its version.
The first release is 0.1.0. Every manifest, pin and template hash names exactly one release.
