# Contributing

Aegis Desk is instructions, role profiles, routine manifests and standard-library tools for a
read-only options desk. The desk never places, modifies or cancels orders; a change that could
teach a Bot otherwise, or that adds a credential or an order-shaped output, is a rejected change.
This file is the process; `AGENTS.md` is the short contributor guide every agent runtime reads.

## Ground rules

- **Tests first.** Every behaviour lands with its tests in the same pull request: golden values
  (ported numbers stay exact), property checks, contract fixtures or command journeys. A gate
  nobody has watched fail is not a gate, so every checker ships negative fixtures.
- **Standard library only** under `scripts/`, `hooks/` and `tests/`. No `pip`, no third-party
  imports. The single exception is `tests/oracle/test_quantlib_oracle.py`, which imports QuantLib
  behind `unittest.skipUnless` and runs in the oracle workflow. `subprocess` is reserved for the
  repository gates named in `check_no_write_paths.py` and for tests under `tests/gates/`; a test
  that needs to intercept the network client patches it by name
  (`unittest.mock.patch("http.client.HTTPConnection.request", ...)`) rather than importing
  `http.client`.
- **Python 3.9 syntax floor.** The Grok Bot computer's interpreter is undocumented, so every
  module begins with `from __future__ import annotations` and avoids `match`, runtime `X | Y`
  unions, `datetime.UTC`, `tomllib` (use the vendored reader), `zip(strict=...)` and
  parenthesized multi-line `with`. `scripts/check.sh` enforces the floor; CI runs the suite on
  3.9, 3.10, 3.12 and 3.13.
- **Money is `Decimal`; prices and Greeks are `float`.** No network access anywhere but the
  GET-only market-data client. No `print()` in library modules; tools print only their own
  output. Failure modes are explicit statuses, never silently wrong numbers.
- **Formatters respect the floor.** `ruff.toml` pins any editor or hook formatter to the 3.9
  target and the import layout the tests need; ruff is optional and never part of the gate.
- **Attribution.** Anything adapted from HyperGrok Trading Desk carries the header
  `Adapted from HyperGrok Trading Desk (Galleon Labs, MIT), release 1.7.0 plus unreleased
  changes, commit 75a4c79`; every skill folder carries `LICENSE` and an `ATTRIBUTION.md` with
  an `origin: new|adapted` line.

## Layout

- `agents/<role>.md`: frontmatter (`name` equal to the file stem, `title`, `description`,
  `model: inherit`, `seat: floor|off-floor`, `skills`, `writes_to_broker: false`), a
  `## Bot profile` section and a `## System prompt` section that contains the boundary sentence.
- `skills/aegis-<name>/SKILL.md`: frontmatter (`name` equal to the directory, `description`
  under 1024 characters, `license: MIT`, `metadata` with `version`, `author`, `category`), body
  under 320 lines, with `LICENSE` and `ATTRIBUTION.md` beside it, linked from `skills/README.md`
  and named in `SETUP.md`.
- `scripts/`: tools (`--json`, `--schema`, one exit-code table) and the repository gates;
  `scripts/aegis_desk/` is the library.
- `tests/`: `unittest` packages (`__init__.py` in every one); each test module starts with
  `import tests._path  # noqa: F401`. Fixture trees live under `tests/fixtures/`;
  `tests/fixtures/desk/` is the offline desk root (`desk.toml`, `positions.csv`, the recorded
  Tradier shapes in five sets under `fixtures/tradier/`) that tools, tests and journeys copy to a
  temporary directory before writing anything.

## The gate

```bash
bash scripts/check.sh
python3 -m unittest discover -s tests -t . -p 'test_*.py' -v
```

`scripts/check.sh` runs ten steps and stops at the first failing one:

| Step | What it proves |
| --- | --- |
| 1 agents | frontmatter (no repeated keys), `model: inherit`, seat, skills resolve, `writes_to_broker` is exactly `false` (zero writers), both sections, the boundary sentence outside HTML comments and code fences; only top-level `.md` files under `agents/`; at least one agent and one skill |
| 2 skills | `aegis-` prefix, name/directory agreement, description length, MIT, metadata, 320-line body, `LICENSE` + `ATTRIBUTION.md` (`origin:`), an adapted skill's in-body notice line outside HTML comments and code fences, indexed in `skills/README.md` and `SETUP.md` |
| 3 links, emoji, scripts | markdown links resolve; no emoji (U+2300-23FF, U+2600-27BF, U+2B00-2BFF, U+1F000-1FAFF, U+FE0F); every `scripts/<name>.py` named in agents, skills, rules, docs, `SETUP.md`, `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `GEMINI.md`, `CLAUDE.md` and the routine manifest exists, unless its line says it is "not yet" in the tree or "lands with" a later pull request |
| 4 version pins | every `vX.Y.Z` in `*.md`, `*.mdc`, `*.json`, `*.toml` (not `CHANGELOG.md`, not `LICENSES/`) equals `v` + the `plugin.json` version |
| 5 python floor | every module parses as Python 3.9 and begins with the `__future__` import; no 3.10+ runtime features |
| 6 sub-gates | `check_manifests.py` (one version, pinned refs, hooks declared once per runtime and wired exactly: `hooks/hooks.json` holds only `PreToolUse` with the shell and MCP matchers and the gate command, `hooks/cursor-hooks.json` both Cursor events with `--runtime cursor` and `failClosed: true`, no other key at any level of either file, and `hooks/broker_write_gate.py` exists; no `skills`/`agents`/`commands` keys in the manifests Claude Code and Grok Build read, desk-rule paragraph mirrored), `check_no_write_paths.py` (AST no-write gate over a closed list: `urllib.request`, `http.client`, `socket`, `ssl` only in `tradier_client.py` and GET-only there; `xmlrpc`, `ftplib`, `smtplib`, `imaplib`, `poplib`, `nntplib`, `telnetlib`, `multiprocessing.connection`, `webbrowser`, `asyncio`, `urllib.robotparser`, `logging.handlers`, `logging.config`, `wsgiref`, `pip` nowhere; `exec`/`eval`/`compile`, `code`, `runpy`, `marshal`, `pickle`, `shelve`, `timeit`, `ctypes`, `cffi`, `zipimport`, `importlib.machinery` nowhere; `subprocess` only in the listed gates and `tests/gates/`; aliases and literal reflection (`getattr`, `sys.modules`, `__import__`) are resolved and non-literal reflection is rejected; in the client no class subclasses `urllib.request.Request`, no built request has its verb, body (`method`, `data`, `_data`) or target (`full_url`, `selector`, `host`, ...) changed after construction, `set_proxy` is never called, and a name reached through reflection into a third-party HTTP client fails; prose scanner over skills, agents, docs, rules, template, routines and the root instruction files), `check_grok_template.py` (release contract, skill hashes, every tree skill claimed once ready to publish, tag comparison) |
| 7 unit tests | the suite passes, at least one test was discovered (an empty suite fails on every interpreter, whatever the floor), and the number of tests run is at least `scripts/test_count_floor.txt` |
| 8 journeys | `cli_journeys.py` runs the published commands as subprocesses in a scrubbed environment |
| 9 TOOLS.md drift | `gen_tools_doc.py --check`: `docs/TOOLS.md` equals the rendering of every tool's `--schema` and parser (active from PR 2d; a tree without the generator, such as a gate fixture tree, skips it with a notice); run `python3 scripts/gen_tools_doc.py` after changing a tool's flags or output |
| 10 role skills | `gen_role_skills.py --check`: every `skills/aegis-role-<seat>/` and the role-skill index blocks in `skills/README.md` and `SETUP.md` equal their rendering from `agents/<seat>.md` (active from PR 2d; skipped with a notice where the generator is absent); run `python3 scripts/gen_role_skills.py` after editing an agent |

The test-count floor only moves up and is never left at zero. When a pull request adds tests,
raise the floor to the new count (the `Ran N tests` line of the discovery command, skips
included) in the same pull request; a lower count later means tests were deleted or stopped
being discovered.

Examples in the documentation (commands, quoted output lines and their exit codes) are copied from tool runs on a copy of
a fixture root, labelled synthetic, and prefer the strings `scripts/cli_journeys.py` already pins. A reviewer
re-runs every quoted example on a fresh fixture copy before a content pull request merges. An automated
docs-example drift test is planned for a later pull request; until then, that re-run is the check.

## Gates per phase

Every pull request must leave `bash scripts/check.sh` green. The table records what "green"
means at each phase, because some steps have nothing to check until their inputs exist.

| PR | Lands | `check.sh` after merge |
| --- | --- | --- |
| 1 | gates (`check.sh`, `check_manifests.py`, `check_no_write_paths.py`, `check_grok_template.py`, `cli_journeys.py`, CI) and the engine (`bsm`, `iv`, `greeks`, `market_clock`, `occ`, vendored TOML reader, `contract`, `state`) | steps 1-8 on the placeholder tree (one agent, one skill); journeys are the three gate journeys; floor set to this PR's count; template tag comparison and TOOLS.md drift skip out loud; oracle workflow green |
| 2a | data plane (`tradier_client`, `quote_cache`, `quote_qc`, `heartbeat`, the shared `market_types`, `desk_paths`, `desk_config` and `version`, `--now` on `contract`, the fixture desk `tests/fixtures/desk/`, the tools `market_clock.py`, `quote_cache.py`, `quote_qc.py`, `heartbeat.py`) | AST gate proves `tradier_client.py` is the only network module and GET-only (including the subclass, mutation and third-party-reflection rules); a loopback fake server test asserts `method == "GET"` on every request; nine tool journeys run on a temporary copy of the fixture desk; floor raised |
| 2b | book of record (`positions_csv`, `grouping`, `calendar_events`, `payoff`, `pnl`, `book`, `recon`, `expiry`, `quote_cache.get_history`, the fixture root `tests/fixtures/desk_recon/` and the `t1`, `expiry_day` and `post_expiry` sets, the tools `positions_check.py`, `csv_recon.py`, `book.py`, `calendar_check.py`, `assignment_watch.py`, `expiry_checklist.py`) | per-structure P/L goldens (the closed forms equal the payoff grid to the cent), every settlement branch on the recon root, day P/L from the desk's own snapshot; seventeen more tool journeys including multi-step chains on one fixture copy; the AST gate over every new module (no network, no subprocess, no `float` in the money kernels); floor raised |
| 2c | rules (`exit_rules`, `rule_state`, `alerts`, `alert_lint`, `signals`, the example rulebook and the fixture roots' adopted rulebooks, the tools `exit_check.py`, `rules_diff.py`, `rules_adopt.py`, `book_check.py`, `ack.py`, `alert_lint.py`) | freshness and verdict goldens; Kleene truth tables and verdict precedence; the rule-state machine (confirmations, re-arm, cooldown, carry-forward); `alert_lint` red-team fixtures; twenty-eight more tool journeys covering verdicts including UNAVAILABLE, posting, the ladder, adoption and acknowledgement; floor raised |
| 2d | levels (`levels`, `trigger_levels.py`), the routine manifest and audit (`routines`, `routines/manifest.toml`, `routines/autoreview.toml`, `routine_audit.py`), `routine.py`, the doctor (`doctor`, `desk_doctor.py`) with the pin check and the stricter no-write gate, `summaries` and `eod_summary.py`, `desk_init.py`, `opening_bell.py`, `gen_tools_doc.py` and `gen_role_skills.py`, the two placeholder agents | steps 9 and 10 active (`docs/TOOLS.md` and the role skills generated and drift-checked); level arithmetic re-evaluated against the rules at every printed level; every new gate rule has a failing fixture; full Stage-1 routine journeys offline; floor raised |
| 3 | Stage-1 content: the three agents and their generated role skills, the Stage-1 skills, the desk rule, the broker-write gate (`hooks/broker_write_gate.py`) with `hooks/hooks.json` and `hooks/cursor-hooks.json`, the hook-wiring rules in `check_manifests.py`, the adapted-skill notice check, `ack.py connectors`, the manifest descriptions, the template hashes (status `unpublished`), `SETUP.md`, README, `SECURITY.md`, the docs and runtime pages, attribution | all ten steps on the real content: zero writers, every skill indexed and attributed, the bootstrap skill's notice line, the hook wiring asserted, the template claiming every skill and role skill with matching hashes, the prose scanner clean with the runtime pages' counter-examples allow-listed and documented; the gate's deny and no-opinion vectors in every event shape; floor raised |
| 3b | the Aegis Structuring Desk in Stage 1: the criteria reader `structuring_config`, the approval-level check `approval`, the library `structuring`, `structuring_text` and `tickets`, `quote_cache.get_expirations`, the alert lint's structuring mode and third footer, the tools `exit_ticket.py`, `roll_analysis.py` and `propose.py`, the structuring intake of `desk_init.py`, `structuring.toml` in the acknowledgement set, the structuring chains of the fixture desk, the fourth agent and its role skill, three skills, `docs/STRUCTURING.md` and the owner documents | all ten steps: four agents, all on the Floor, and twenty-two skills prefixed `aegis-` (eighteen desk skills and four role skills); the three tools refused as routine steps; the structuring goldens recomputed with the landed engine, and the best legs checked by the QuantLib oracle; journeys for the three tools; the template claiming the new skills and role skill; the Claude Code observation re-run, because the plugin's content changed; floor raised |
| 4 | release 0.1.0 | all of the above plus the observed deny tests (Claude Code and Grok Build), then the tag; after the tag `check_grok_template.py` compares claimed bytes with the release tag instead of skipping |

## Branch mechanics

- The maintainer's working branch is `claude/stoic-noether-w23mqz`; other contributors branch
  from `main`. Pull requests target `main` and are **squash-merged**. One pull request per Linear
  issue; the body carries `Closes FAI-<n>` for its own issue only.
- After each squash merge, reset the working branch onto `main` before the next pull request:

  ```bash
  git fetch && git switch claude/stoic-noether-w23mqz && git reset --hard origin/main && git push --force-with-lease
  ```

- Never commit directly to `main` after the scaffold commit; CI (`.github/workflows/ci.yml`)
  runs `bash scripts/check.sh` on every push and pull request, and the oracle workflow runs the
  QuantLib cross-check. The release workflow (`.github/workflows/release.yml`) runs only by hand, on
  `main`: it repeats the release gate, creates the tag and publishes the GitHub Release.

## Releasing

The manifest version in `plugin.json` is the release being cut; every other manifest, the
marketplace refs (`v<version>`), the template's `source.release` and every documented pin must
agree, and `check_manifests.py` fails the build when they do not. Pre-release suffixes such as
`0.1.0-dev` are rejected: a reader installs exactly one reviewed tag.

The release sequence (from the plan, section 12; [docs/RELEASE.md](docs/RELEASE.md) is the checklist, with
who does each step and the evidence rows):

1. Offline gates green: `bash scripts/check.sh` on every CI interpreter, the QuantLib oracle,
   the command journeys.
2. Claude Code: the plugin loaded from a clean clone with `--plugin-dir`, and the deny checks
   observed. The marketplace install (`/plugin marketplace add fairgradient-ai/aegis-grok-desk`,
   `/plugin install aegis-desk@aegis-desk`) is checked after the tag, because the marketplace
   entry points at the tag ([docs/RELEASE.md](docs/RELEASE.md) step 2).
3. Grok Build local-path install with the deny tests **observed**: the hook refuses the shell
   sample and the stub MCP tool `blackhole__place_order` (the stub server is
   `tests/fixtures/release/blackhole_mcp.py`, which does nothing).
4. **Tag** `vX.Y.Z`: pushed by the owner, or created by the release workflow (Actions, Release, Run
   workflow, on `main`), which repeats the release gate first. From this point `check_grok_template.py` compares the
   template's claimed skill bytes with the tag.
5. The owner's Grok Bot bootstrap from the tag, ending with the receipt line
   "Setup stayed read-only: one sandbox market-data token was added by you; no order was created
   or sent." A failed bootstrap ships the next patch release.
6. GitHub Release, the README "Start" block, template authoring and publishing (an owner action;
   `status: published` requires the share URL in `README.md` and `docs/FAQ.md`).

Counsel sign-off on the wording of the alerts, the level sheet, the tickets, the roll analyses and
the proposals is a Done-when item before the tag and before the public release; every template stays
free of order fields until then. An order field is a trade-side word, an order type, a limit or stop
price field, a duration field, a route or an account number. The Structuring Desk's leg lines name
only the effect, the count and the contract, so they carry none, and the owner supplies the limit.
`[levels] order_shape_enabled` governs only the threshold-level sheet.

**After any tag, the first pull request runs `python3 scripts/release_bump.py <next>`**
before touching skills or template-claimed files, so the manifests, pins and template hashes
move to the next version together and the tagged release is never edited in place. Run it with
`--check` first: it lists every file that would change and writes nothing. The tool writes every
file or none; refuses a version that is not plain `X.Y.Z`, not greater than the current one or
already tagged; never edits `CHANGELOG.md` or the release record `docs/RELEASE.md`; and lists the
lines that name the old version without the leading `v` for a person to review. Until the next
tag exists, `main` then names a tag that does not exist yet; how `main` behaves in that window is
the owner's decision, recorded before the tag in step 8 of [docs/RELEASE.md](docs/RELEASE.md), the
checklist and receipt of a release.

## Credit and reuse

Retain existing authorship and license notices in contributions and derived work.
[ATTRIBUTION.md](ATTRIBUTION.md) names the adapted mechanics and their source commit.
