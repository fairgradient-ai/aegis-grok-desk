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
  `import tests._path  # noqa: F401`. Fixture trees live under `tests/fixtures/`.

## The gate

```bash
bash scripts/check.sh
python3 -m unittest discover -s tests -t . -p 'test_*.py' -v
```

`scripts/check.sh` runs nine steps and stops at the first failing one:

| Step | What it proves |
| --- | --- |
| 1 agents | frontmatter (no repeated keys), `model: inherit`, seat, skills resolve, `writes_to_broker` is exactly `false` (zero writers), both sections, the boundary sentence outside HTML comments and code fences; only top-level `.md` files under `agents/`; at least one agent and one skill |
| 2 skills | `aegis-` prefix, name/directory agreement, description length, MIT, metadata, 320-line body, `LICENSE` + `ATTRIBUTION.md` (`origin:`), indexed in `skills/README.md` and `SETUP.md` |
| 3 links, emoji, scripts | markdown links resolve; no emoji (U+2300-23FF, U+2600-27BF, U+2B00-2BFF, U+1F000-1FAFF, U+FE0F); every `scripts/<name>.py` named in agents, skills, rules, docs, `SETUP.md`, `README.md`, `CONTRIBUTING.md`, `AGENTS.md`, `GEMINI.md`, `CLAUDE.md` and the routine manifest exists, unless its line says it is "not yet" in the tree or "lands with" a later pull request |
| 4 version pins | every `vX.Y.Z` in `*.md`, `*.mdc`, `*.json`, `*.toml` (not `CHANGELOG.md`, not `LICENSES/`) equals `v` + the `plugin.json` version |
| 5 python floor | every module parses as Python 3.9 and begins with the `__future__` import; no 3.10+ runtime features |
| 6 sub-gates | `check_manifests.py` (one version, pinned refs, hooks declared once per runtime, no `skills`/`agents`/`commands` keys in the manifests Claude Code and Grok Build read, desk-rule paragraph mirrored), `check_no_write_paths.py` (AST no-write gate over a closed list: `urllib.request`, `http.client`, `socket`, `ssl` only in `tradier_client.py` and GET-only there; `xmlrpc`, `ftplib`, `smtplib`, `imaplib`, `poplib`, `nntplib`, `telnetlib`, `multiprocessing.connection`, `webbrowser`, `asyncio`, `urllib.robotparser`, `logging.handlers`, `logging.config`, `wsgiref`, `pip` nowhere; `exec`/`eval`/`compile`, `code`, `runpy`, `marshal`, `pickle`, `shelve`, `timeit`, `ctypes`, `cffi`, `zipimport`, `importlib.machinery` nowhere; `subprocess` only in the listed gates and `tests/gates/`; aliases and literal reflection (`getattr`, `sys.modules`, `__import__`) are resolved and non-literal reflection is rejected; prose scanner over skills, agents, docs, rules, template, routines and the root instruction files), `check_grok_template.py` (release contract, skill hashes, every tree skill claimed once ready to publish, tag comparison) |
| 7 unit tests | the suite passes, at least one test was discovered (an empty suite fails on every interpreter, whatever the floor), and the number of tests run is at least `scripts/test_count_floor.txt` |
| 8 journeys | `cli_journeys.py` runs the published commands as subprocesses in a scrubbed environment |
| 9 TOOLS.md drift | `gen_tools_doc.py --check` once that generator exists (skipped with a notice until then) |

The test-count floor only moves up and is never left at zero. When a pull request adds tests,
raise the floor to the new count (the `Ran N tests` line of the discovery command, skips
included) in the same pull request; a lower count later means tests were deleted or stopped
being discovered.

## Gates per phase

Every pull request must leave `bash scripts/check.sh` green. The table records what "green"
means at each phase, because some steps have nothing to check until their inputs exist.

| PR | Lands | `check.sh` after merge |
| --- | --- | --- |
| 1 | gates (`check.sh`, `check_manifests.py`, `check_no_write_paths.py`, `check_grok_template.py`, `cli_journeys.py`, CI) and the engine (`bsm`, `iv`, `greeks`, `market_clock`, `occ`, vendored TOML reader, `contract`, `state`) | steps 1-8 on the placeholder tree (one agent, one skill); journeys are the three gate journeys; floor set to this PR's count; template tag comparison and TOOLS.md drift skip out loud; oracle workflow green |
| 2a | data plane (`tradier_client`, `quote_cache`, `quote_qc`, `heartbeat`) | AST gate proves `tradier_client.py` is the only network module and GET-only; a loopback fake server test asserts `method == "GET"` on every request; tool journeys added; floor raised |
| 2b | book of record (`positions_csv`, `csv_recon`, `pnl`, `grouping`, `book`, `calendar_events`, `calendar_check`, `assignment_watch`, `expiry_checklist`) | per-structure P/L and settlement goldens; journeys cover CSV to book; floor raised |
| 2c | rules (`exit_rules`, `exit_check`, `alerts`, `alert_lint`, `ack`, `book_check`, `rules_adopt`, `rules_diff`) | freshness and verdict goldens; `alert_lint` red-team fixtures; journeys cover verdicts including UNAVAILABLE; floor raised |
| 2d | levels, `routine.py`, `desk_doctor`, `desk_init`, `opening_bell`, `gen_tools_doc`, `gen_role_skills` | step 9 active (`docs/TOOLS.md` generated and drift-checked); role-skill drift checked; full Stage-1 journeys offline; floor raised |
| 3 | Stage-1 content: 3 agents, the Stage-1 skills, rules, hooks (+ Cursor file), manifests, template, routine manifest, `SETUP.md`, README and docs | steps 1-4 on the real content (zero writers, every skill indexed); `check_manifests.py` routine-manifest check in full; template `skills` hashes verified; prose scanner clean with every counter-example allow-listed and documented |
| 4 | release 0.1.0 | all of the above plus the observed deny tests (Claude Code and Grok Build), then the tag; after the tag `check_grok_template.py` compares claimed bytes with `v0.1.0` instead of skipping |

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
  QuantLib cross-check.

## Releasing

The manifest version in `plugin.json` is the release being cut; every other manifest, the
marketplace refs (`v<version>`), the template's `source.release` and every documented pin must
agree, and `check_manifests.py` fails the build when they do not. Pre-release suffixes such as
`0.1.0-dev` are rejected: a reader installs exactly one reviewed tag.

The release sequence (from the plan, section 12):

1. Offline gates green: `bash scripts/check.sh` on every CI interpreter, the QuantLib oracle,
   the command journeys.
2. Claude Code install verified (`/plugin marketplace add fairgradient-ai/aegis-grok-desk`,
   `/plugin install aegis-desk@aegis-desk`).
3. Grok Build local-path install with the deny tests **observed**: the hook refuses the shell
   sample and the stub MCP tool `blackhole__place_order`.
4. **Tag** `vX.Y.Z` (pushed by the owner). From this point `check_grok_template.py` compares the
   template's claimed skill bytes with the tag.
5. The owner's Grok Bot bootstrap from the tag, ending with the receipt line
   "Setup stayed read-only: one sandbox market-data token was added by you; no order was created
   or sent." A failed bootstrap ships the next patch release.
6. GitHub Release, the README "Start" block, template authoring and publishing (an owner action;
   `status: published` requires the share URL in `README.md` and `docs/FAQ.md`).

Counsel sign-off on the alert, level and ticket templates is a Done-when item before the public
release; templates stay order-shape-free until then.

**After any tag, the first pull request runs `python3 scripts/release_bump.py <next>`** (not yet in the tree; it lands with the release tooling in PR 4)
before touching skills or template-claimed files, so the manifests, pins and template hashes
move to the next version together and the tagged release is never edited in place. Until the
tool lands, the bump is the same edit made by hand and checked by `check_manifests.py`.

## Credit and reuse

Retain existing authorship and license notices in contributions and derived work.
[ATTRIBUTION.md](ATTRIBUTION.md) names the adapted mechanics and their source commit.
