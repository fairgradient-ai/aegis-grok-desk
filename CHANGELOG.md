# Changelog

## Unreleased

- Release tooling:
  - `scripts/release_bump.py <next> [--check]` moves the version in `plugin.json`, every mirrored manifest, the
    marketplace refs, the template's release and skill hashes, every pin `check.sh` step 4 checks, the role skills
    and the tool versions in `docs/TOOLS.md`, in one pass, writing every file or none; it refuses a version that is
    not plain or not greater, a tag that already exists (read from `.git` as files, worktrees included; a `.git` it
    cannot read refuses the run) and a tree that is not consistent at its version, never edits `CHANGELOG.md` or
    `docs/RELEASE.md`, and lists plain mentions of the old version for review;
  - the stub MCP server of the release deny check, `tests/fixtures/release/blackhole_mcp.py` (one tool that does
    nothing), and its `--mcp-config` file;
  - `docs/RELEASE.md`, the 0.1.0 checklist and receipt, with who does each step and empty evidence rows;
  - 82 more tests, 2584 in all (floor 2584).
- Stage-1 content:
  - three role profiles (`agents/desk-lead.md`, `agents/exit-sentinel.md`, `agents/position-control.md`) and their
    three generated role skills; fifteen Stage-1 skills, each with its `LICENSE` and `ATTRIBUTION.md` (one adapted:
    the bootstrap skill, which carries its notice line in its body);
  - the broker-write gate `hooks/broker_write_gate.py`, a backstop that denies a shell command or MCP tool call that
    looks like a broker write and gives no opinion on reads, registered by `hooks/hooks.json` (Claude Code and Grok
    Build) and `hooks/cursor-hooks.json` (Cursor, fail-closed, answering `ask` when it has no opinion);
    `check_manifests.py` asserts the wiring exactly, with no other key at any level;
  - `check.sh` step 2 requires an adapted skill's in-body notice line; `ack.py connectors --owner-tapped` records the
    owner's readback of connected apps and MCP servers, which the doctor re-asks after 30 days; `routine.py`'s
    `--desk-root` help names its `/workspace/aegis-desk` default;
  - manifest descriptions of the Stage-1 desk without counts; the Cursor manifest's `rules` names
    `rules/aegis-desk.mdc`;
  - `template/grok-bot.json` claims every skill and role skill by its full-file sha256 and exports the bootstrap skill;
    its status stays `unpublished` until the owner publishes it after the tag;
  - `SETUP.md` (the Stage-1 runbook), the README, `SECURITY.md`, the architecture, FAQ, positions, monitoring and
    provenance pages, the page on your exit rules, the runtime pages for Grok Bot, Grok Build (with copy-paste
    `[permission]` deny rules for `~/.grok/config.toml`), Claude Code, Cursor, Codex and Gemini CLI, the template
    page, the skill index and the attribution table;
  - 2502 tests (floor 2502).
- Counsel review before the public release (an owner action; nothing here is signed off):
  - the alert texts (EXIT SIGNAL, WATCH, UNAVAILABLE, CONFIG CHANGED, the pre-close and watch-alive lines) and both
    fixed footers;
  - the threshold-level sheet and the exit-plan card, which carry no order shape (`[levels] order_shape_enabled` stays
    `false`);
  - quoting diagnostic output: a Bot quotes it only when the owner asks, unchanged, in a fenced block headed by the
    exact command, and the proposed `positions.csv` lines of `csv_recon.py --diff` appear only in Aegis Position
    Control's chat;
  - ticket templates are planned for Stage 2 (not in this release); their review comes with them.
- Levels, routines and the doctor:
  - the threshold-level sheet (`levels.py`, `trigger_levels.py`): each adopted rule as an option or spread
    price, an underlying level, a band or a date, with its arithmetic shown, rounded on the class's tick grid in
    the direction that keeps a resting take-profit inside the rule, take-profit levels marked resting-eligible
    and loss levels alert-only, and exit-plan cards;
  - the routine manifest (`routines/manifest.toml`, `routines.py`) with the Stage-1 routines, their posting
    policies, dependencies and weekly budget (lean 41, active 61 runs), the Grok Bot Auto-review texts
    (`routines/autoreview.toml`) and `routine_audit.py`;
  - `routine.py`, the single reviewed routine entry point: it refuses to run a step on a checkout that is not the
    pinned release, runs the chain in-process, lints every part and the whole message, and writes the heartbeat,
    journal and run record;
  - the desk doctor (`doctor.py`, `desk_doctor.py`): twelve checks, including sandbox-token verification and
    broker-looking environment names;
  - the Morning Desk Sheet and EOD Desk Brief (`summaries.py`, `eod_summary.py`), including P/L per open trade;
  - `desk_init.py`, which renders `desk.toml` byte for byte from the interview;
  - `opening_bell.py`, with a market-snapshot footer that the lint accepts only on rule-free text;
  - `gen_tools_doc.py` (`docs/TOOLS.md`) and `gen_role_skills.py` (the role skills), both drift-checked by
    `check.sh`;
  - `csv_recon` and `expiry_checklist` now write their records (`data/positions/<date>.recon.json`,
    `reports/expiry/<date>.<mode>.json`);
  - the no-write gate also rejects loader and reflection routes, laundering through the subprocess-allowed
    modules, git configuration through arguments or the environment, and environment writes;
  - two placeholder agents; twenty-four more journeys (81 in all).
- Rules engine: the strict exit-rulebook grammar with parameters bounded in their safe direction, the metric
  catalogue on the book's fields, Kleene evaluation and the verdict precedence EXIT_SIGNAL > UNAVAILABLE >
  WATCH > HOLD, the adoption lint and per-rule shas (`scripts/aegis_desk/exit_rules.py`); the per-(group, rule)
  state machine with time-based confirmations on quote stamps, bounded re-arm, a cooldown that never holds a
  re-arm past the next session's open, first observation and carry-forward (`rule_state.py`); the alert texts
  with their fixed footer and emitted-number documents (`alerts.py`), the lint that proves every number and
  identifier was emitted and that no trade language appears (`alert_lint.py`), the signal and alert journals
  (`signals.py`); the tools `exit_check.py`, `rules_diff.py`, `rules_adopt.py` (owner-tapped, immutable
  versions, refused in regular trading hours unless `--allow-rth`), `book_check.py` (posting rules, re-post
  ladder, CONFIG CHANGED first, dry run without `--post`), `ack.py` and `alert_lint.py`; the example rulebook
  `rules/examples/exit_rules-1.0.0.toml` (each rule illustrative until adopted), adopted rulebooks in both
  fixture roots, red-team lint fixtures; twenty-eight more tool journeys.
- Book follow-up: every book leg carries `fresh_bid` and `fresh_ask` (the liquidation sides of a grade
  A-E quote; a zero ask stays 0, no offer), and `extrinsic_bid` now uses the assignment watch's geometry
  (any grade A-E fresh bid, the usable underlying mid stamped within 60 s, the strike as a plain decimal),
  so the book and the expiry sheet agree on every quote state; `mark_source` is `mid` only with both sides
  fresh. `quote_cache.py` and `quote_qc.py` classify the session with the research calendar's full
  closures like the book tools (`quote_qc.py` also takes its prior close from it; an unparsable calendar
  is exit 1 `calendar_invalid`). The command-journey step test lists its temporary directories under a
  private root, so concurrent local runs cannot collide.
- Book of record: the hand-edited `positions.csv` parser with the desk's money conventions
  (`scripts/aegis_desk/positions_csv.py`), strategy grouping, the `Decimal` payoff and P/L kernels, the
  book (`book.py`: one quotes request, quality grades, IV and Greeks, group metrics, portfolio totals that
  name every excluded group, the end-of-day snapshot and report; the session classified with the
  calendar's full closures), the reconciliation engine with every settlement branch (`recon.py`), the
  owner-sourced research calendar reader with its freshness lint (`calendar_events.py`), the expiry
  sheet with dividend and pin tiers (`expiry.py`: a dividend tier only while the contract is alive on
  the last cum-dividend day; the act-by instant is the earlier of the broker cutoff and an early close),
  daily-bar history in the quote cache; the tools `positions_check.py`, `csv_recon.py`, `book.py`,
  `calendar_check.py`, `assignment_watch.py` and `expiry_checklist.py`; a second fixture root
  `tests/fixtures/desk_recon/` covering every settlement branch and three more fixture sets (T-1, expiry
  day, post-expiry); seventeen more tool journeys, including multi-step chains on one fixture copy.
- Data plane: the GET-only Tradier market-data client (`scripts/aegis_desk/tradier_client.py`:
  pinned hosts, allow-listed paths, literal `GET` and no body on every request, no redirects, no
  proxy, TLS 1.2 or later, one receipt per request, credential redaction, refusal of a `200` that
  echoes the credential, loopback fake-server tests asserting the method on every request) with
  an offline transport over the recorded fixture desk `tests/fixtures/desk/` (five sets: regular,
  stale, after the close, before the open, holiday); the quote cache with the desk-wide
  60-per-minute ledger, per-run caps and a run-wide stop after a quota or credential error;
  quote quality grades with session-aware freshness; heartbeat records; shared vendor types,
  desk paths and the strict `desk.toml` reader; the tools `quote_cache.py`, `quote_qc.py`,
  `heartbeat.py` and `market_clock.py`; `--now` on the shared command-line contract; nine tool
  journeys. The no-write gate also rejects subclasses of `urllib.request.Request`, assignment of
  a request's `_data` or target fields, `set_proxy`, and reflection into third-party HTTP clients.
- Repository gates: `scripts/check.sh` (structure, Python 3.9 floor, version pins, test-count
  floor, command journeys), `check_manifests.py`, `check_no_write_paths.py` (AST no-write gate and
  prose scanner), `check_grok_template.py`, `cli_journeys.py`; CI on Python 3.9, 3.10, 3.12 and
  3.13 plus the QuantLib oracle workflow; negative fixture trees for every gate.
- Engine library `scripts/aegis_desk/`: Black-Scholes-Merton pricing, implied volatility, desk-unit
  Greeks and units ported from the Aegis quant engine with their golden values; NYSE market clock
  with embedded 2026-2027 calendar and DST rules; OCC symbol parser; strict vendored TOML reader;
  atomic state and file locking; the shared command-line contract and exit-code table.
- Repository scaffold: manifests, licences, placeholder role and skill, documentation skeletons.
