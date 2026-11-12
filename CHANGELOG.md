# Changelog

## 0.1.0 (2026-11-12)

- Release workflow: `.github/workflows/release.yml` tags `main` and publishes the GitHub Release. It runs only when
  someone with write access starts it by hand (Actions, Release, Run workflow, on `main`), with the version (as in
  `plugin.json`) and a pre-release switch. A read-only `gate` job, which keeps no credential in its checkout, repeats
  the gate of `docs/RELEASE.md` step 5: the version matches `plugin.json` and has a section here, and
  `bash scripts/check.sh` and `release_bump.py <version> --check` pass. Only then does the `release` job, the one job
  with write access (`contents: write`), create the annotated tag on the same commit, push it, run
  `check_grok_template.py` and publish the Release with this section as its notes. A re-run after a failure reuses a
  tag already on that commit and an existing Release, and refuses a tag on any other commit. The inputs reach the
  shell only through `env:`. `tests/gates/test_release_workflow.py` pins these properties (12 more tests, 3040 in
  all; floor 3040).
- A short option's act-by (FAI-689): the expiry checklist (the FINAL CALL, the T-1 preview and the PIN lines), the
  assignment watch and the morning sheet print a short option leg's act-by as the session close of its last trading
  day (16:00 ET; 13:00 ET on an early close), or `broker_exercise_cutoff_et` when that is earlier, because a short
  option is closed only while the market trades; its text ends `; the session close: a short option is closed only
  while the market trades` and carries no early-close mark. A long leg keeps the broker cutoff, with the early-close
  rule and mark (it governs exercise and do-not-exercise instructions):
  - one helper, `expiry.leg_act_by`, serves the expiry sheet and the LET_ASSIGN ticket, whose printed text is
    unchanged;
  - `ActBy.basis` and the leg JSON's `act_by_basis` (`session_close` or `broker_cutoff`) name the instant that
    governed, described by the `--schema` of `assignment_watch.py` and `expiry_checklist.py`;
  - the FINAL CALL header names the earliest act-by of the listed legs when they differ, each leg line keeping its own;
  - each docket line of the Morning Desk Sheet and the EOD brief names its contract, side and size first
    (`<trade_id> <OCC symbol> <long|short> <contracts> | ...`), since the legs of one group can act by different
    times; a docket record whose legs lack them is not the record the desk writes;
  - 15 more tests, 3028 in all (floor 3028).
- The Aegis Structuring Desk joins Stage 1 as a fourth Bot on the Floor. It answers only the owner's own request,
  never runs in a routine and never fires an alert:
  - `scripts/exit_ticket.py` (`--action` CLOSE, UNWIND_STOCK, LET_ASSIGN, MONETIZE_PUT, or ROLL with
    `--new-expiration` and `--new-strike`), `scripts/roll_analysis.py` (the replacements for a covered call's short
    call) and `scripts/propose.py` (a covered call, protective put or collar on a group that holds shares only), on
    the library `scripts/aegis_desk/structuring.py`, `structuring_text.py` and `tickets.py`: candidates from the held
    underlying's own monthly chains, priced with the European model and labelled as such, labelled against the owner's
    criteria and ranked by the objective the owner declared; one best candidate when one passes every criterion;
    tickets with the legs in a fixed order, their quotes, the cash at the conservative side and at the mid, and no
    order fields; no `positions.csv` lines (Position Control proposes them after a fill);
  - the owner's criteria `structuring.toml` and its strict reader (`scripts/aegis_desk/structuring_config.py`),
    rendered by `desk_init.py` from the structuring interview with its one-time disclosure; it joins the CONFIG
    CHANGED set acknowledged by `ack.py config`, and the structuring tools answer UNAVAILABLE while `desk.toml`,
    `positions.csv` or `structuring.toml` are unacknowledged, or while any group of `positions.csv` is quarantined;
  - the approval-level check (`scripts/aegis_desk/approval.py`), the first reader of `desk.toml`'s self-attested
    `approval_level`, in the desk's convention;
  - a third footer and the alert lint's structuring mode (`alert_lint.py --footer structuring`), with a fixed leg
    grammar; the rules and market-snapshot footers refuse a leg reference, a ranked candidate or a structuring
    answer;
  - `quote_cache.get_expirations`, cached per ET date (an empty list is never cached), and synthetic structuring
    chains in the fixture desk;
  - `routine.py` refuses the three tools as routine steps; the owner-authority Auto-review text names them (333 of
    1000 characters), so each run asks first;
  - `tickets/`, `reports/rolls/`, `reports/proposals/` and the audit journal `journal/structuring.jsonl`;
  - the agent `agents/structuring-desk.md` and its generated role skill; the skills `aegis-overlay-proposals`,
    `aegis-roll-decision-framework` and `aegis-ticket-and-unwind-order`; `docs/STRUCTURING.md`, the owner and counsel
    reference; the provenance of the criteria defaults and the three objectives (`docs/PROVENANCE.md`); SETUP step
    9's structuring criteria; the README, FAQ, monitoring, security and runtime pages for four Bots;
  - 429 more tests, 3013 in all (floor 3013).
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
  - four role profiles (`agents/desk-lead.md`, `agents/exit-sentinel.md`, `agents/position-control.md` and, with the
    Structuring Desk, `agents/structuring-desk.md`) and their four generated role skills; eighteen Stage-1 skills,
    each with its `LICENSE` and `ATTRIBUTION.md` (one adapted: the bootstrap skill, which carries its notice line in its
    body);
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
  - the Structuring Desk (rows 1 to 9 of `docs/RELEASE.md` step 4): the structuring footer and the disclosure, the texts
    of the tickets, the roll analyses and the proposals, the approval convention and the REFUSED line, the criteria
    defaults and the interview, the FAQ answer and the README description, whether publishing software that names a best
    candidate raises investment-adviser questions, and whether the release may advertise the Structuring Desk.
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
