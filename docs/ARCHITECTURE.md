# Architecture

How the desk is built, for an owner who wants to know what runs where. The tool reference is
[TOOLS.md](TOOLS.md); the library and tool tables are in [scripts/README.md](../scripts/README.md).

## The shape: files, tools, one routine entry point, Bots

- **Your files**, in the desk root `/workspace/aegis-desk` on the Grok Bot computer: `desk.toml` (written once by
  `desk_init.py` from your interview), `positions.csv` (yours), `rules/` (your adopted rulebook),
  `research/calendar.toml` (your dividend and earnings dates) and `structuring.toml` (your structuring criteria, from
  their interview).
- **The tools**, `scripts/*.py`: small standard-library commands that read those files and the market data, and
  compute every number. Each one prints prose, or one JSON envelope with `--json`.
- **The library**, `scripts/aegis_desk/`: the engine (pricing, implied volatility, Greeks, the market clock), the data
  plane (the market-data client, the quote cache, quote grades), the book of record, the rules, the messages and the
  alert lint.
- **One routine entry point**, `scripts/routine.py`. Every Grok routine runs exactly one command,
  `python3 /workspace/aegis-grok-desk/scripts/routine.py <id> --slot HH:MM`, and posts what it prints.
- **The Bots**, Grok Bots with role profiles (`agents/`) and skills (`skills/`). They route your questions, run tools,
  and post tool text verbatim. They never compute a number and never decide a verdict. Bots pass information through
  files and tool output on the desk computer, never through opinions. Three Bots own routines; the fourth, the Aegis
  Structuring Desk, runs its tools only when you ask.
- **The repository gates**, `scripts/check.sh` and its sub-gates, prove the structure and the no-write property before
  anything ships.

Nothing runs between routines: the desk computer sleeps when nothing wakes it, so every job is woken by a routine and
runs to completion. There is no daemon and no watcher process.

## One run, end to end

A book check at 14:05 ET, as `routine.py` runs it:

```text
Grok routine (14:05 ET)
  -> python3 /workspace/aegis-grok-desk/scripts/routine.py book-check --slot 14:05
     1. pin check: is the checkout the clean commit of tag v0.1.0?   no -> INCIDENT line, no step runs
     2. the slot and its chain (routines/manifest.toml); a closed day -> quiet
     3. step: book_check --post, in the same process
          positions.csv --+
          quotes ---------+--> book: marks, IV, Greeks, P/L       (one GET /v1/markets/quotes batch)
          rules/ADOPTED --+--> the adopted rulebook, its sha checked against rules/ADOPTED
                               --> evaluation with state/rule_state.json and the rows' tags
                               --> transitions and the posting rules
                               --> the message, linted --> journal/, state/, the heartbeat
     4. the routine message: every part linted again, one footer, the run record
  -> the Bot posts the printed text verbatim (or the single word quiet)
```

The other routines follow the same four steps with other chains: `preopen` runs the quick doctor, `csv_recon`,
`expiry_checklist --week` and `eod_summary`; `eod` runs `book --snapshot --report`, `trigger_levels --from-snapshot`
and `csv_recon`. Only eight tools may be a routine step: `assignment_watch`, `book`, `book_check`, `csv_recon`,
`desk_doctor`, `eod_summary`, `expiry_checklist` and `trigger_levels`. `exit_ticket`, `roll_analysis` and `propose`
are refused as routine steps by name. A failed step never stops the others; it becomes an INCIDENT line and an incident
file.

## One request to the Structuring Desk, end to end

A roll analysis you asked for, as `roll_analysis.py` runs it:

```text
you: "what are my choices for the WMT covered call?"  ->  an Ask-first card  ->  your tap
  -> python3 /workspace/aegis-grok-desk/scripts/roll_analysis.py --trade AG-20261020-01 --desk-root /workspace/aegis-desk
     1. acknowledgement: desk.toml, positions.csv or structuring.toml changed?   yes -> UNAVAILABLE line, exit 20
     2. your criteria: structuring.toml present and valid?                        no  -> UNAVAILABLE or exit 1
     3. positions.csv: any group quarantined?                                    yes -> UNAVAILABLE line, exit 20
     4. the group in positions.csv; the approval-level check of the roll's legs   above your level -> REFUSED, exit 20
     5. the group's quotes, the expiration list, the chains in your window       (GET only, through the quote cache)
     6. every candidate priced, labelled against your criteria, ranked by your objective; one best candidate or none
     7. the text, linted with the structuring footer --> reports/rolls/ (BEST or NONE_QUALIFIES), journal/structuring.jsonl
  -> the Structuring Desk posts the printed text verbatim
```

`propose.py` follows the same steps for a covered call, a protective put or a collar on shares you hold, and
`exit_ticket.py` for one ticket (no ranking: you name the replacement of a ROLL). These tools never exit 10: "fired"
belongs to the alerts. [STRUCTURING.md](STRUCTURING.md) has the criteria, the outputs and the limits.

## The desk root

| Path | Written by | Holds |
| --- | --- | --- |
| `desk.toml` | `desk_init.py`, after your tap | your configuration; never written by a routine |
| `positions.csv` | you | the book of record; never written by a tool |
| `rules/exit_rules-X.Y.Z.toml`, `rules/ADOPTED`, `rules/CHANGELOG.md` | `rules_adopt.py` | the adopted versions (immutable), the adopted version and shas, the append-only log |
| `research/calendar.toml` | `desk_init.py` (dividend intake), then you | ex-dividend and earnings dates, settlement values, closures |
| `structuring.toml` | `desk_init.py` (the structuring interview), after your tap | your structuring criteria; never written by a routine or a structuring tool |
| `state/desk_state.json` | `book_check.py`, `ack.py`, `rules_adopt.py` | last verdicts, acknowledged hashes, the connector readback |
| `state/rule_state.json` | `book_check.py --post`, `rules_adopt.py` | each rule's state per position |
| `state/acks.jsonl` | `ack.py` | your acknowledgements, snoozes, acted marks and readbacks |
| `state/doctor.json` | `desk_doctor.py` | the doctor's last result |
| `state/heartbeat/` | `routine.py`, `book_check.py --post`, `heartbeat.py` | one heartbeat per routine and the history |
| `data/cache/` | the quote cache | quotes, the request ledger |
| `data/book/<date>.json` | `book.py --snapshot` | the end-of-day snapshot of record (the next day's day P/L reads it) |
| `data/positions/<date>.csv`, `.sha256`, `.recon.json` | `csv_recon.py` | a copy of `positions.csv` and the reconciliation |
| `journal/signals.jsonl`, `journal/alerts.jsonl`, `journal/<date>.md` | `book_check.py --post`, `routine.py` | every signal and every posted message |
| `levels/<date>.md`, `levels/<date>.json` | `trigger_levels.py` | the threshold-level sheet |
| `plans/<group>.md` | `trigger_levels.py --plan` | an exit-plan card |
| `reports/book.md` | `book.py --report` | the full book table (never posted) |
| `reports/expiry/<date>.<mode>.json` | `expiry_checklist.py` | the docket, the T-1 preview and the Final Call |
| `reports/routines/<date>/` | `routine.py` | each run's record and a copy of each step's output |
| `incidents/` | `routine.py` | one file per incident |
| `tickets/` | `exit_ticket.py` | each ticket, `.json` and `.md` |
| `reports/rolls/`, `reports/proposals/` | `roll_analysis.py`, `propose.py` | each complete roll analysis and proposal (BEST or NONE_QUALIFIES), `.json` and `.md` |
| `journal/structuring.jsonl` | `exit_ticket.py`, `roll_analysis.py`, `propose.py` | one record per structuring text printed (append-only) |

Writes are atomic (a reader sees the old or the new file, never half of one), under a file lock. A state file that is
not valid JSON is moved aside as `<file>.corrupt-<time>` and its reader reports UNAVAILABLE. The desk root lives outside the release checkout: a runtime file inside the checkout would make the pin check
refuse the next routine.

## The command contract and exit codes

Every desk tool shares one contract: `--json` (one JSON envelope), `--schema` (describes the output and the exit codes,
with no network or desk access), `--timeout`, `--desk-root`, `--offline` and `--now`. The envelope carries `tool`,
`tool_version`, `schema_version` 1, `mode` `"read-only"`, `status`, `source`, `delay_min`, `generated_at` and the tool's
own `payload`. One exit-code table holds for every tool:

| Code | Meaning |
| --- | --- |
| 0 | quiet: ran, nothing to post (informational output at most) |
| 1 | error: the tool failed (bug, unreadable file, bad data) |
| 2 | usage: invalid arguments |
| 10 | fired: a rule fired or a signal stands; the message must be posted |
| 20 | unavailable: an input the verdict needs is missing or stale; say UNAVAILABLE, never HOLD |

Without `--desk-root` a tool reads the current directory; `routine.py` alone defaults to `/workspace/aegis-desk`.
`docs/TOOLS.md` is generated from each tool's `--schema` and parser and checked for drift on every gate run.

## The engine and its validation

- **Pricing.** `bsm.py`: the generalised Black-Scholes-Merton closed form with a continuous dividend yield, ported from
  the Aegis quant engine with its golden value of record. A CI-only oracle cross-checks it against QuantLib (price
  within 1e-8 relative, Greeks within 1e-6).
- **Implied volatility.** `iv.py`: a bracketed Brent solver with arbitrage bounds. A failure is a status (`expired`,
  `below_intrinsic`, `above_bound`, `no_root`), never a number.
- **Greeks.** `greeks.py`, in desk units: theta per calendar day, vega per volatility point, rho per rate point. In
  Stage 1 they are European; an early-exercise model is planned for Stage 2 (not in this release).
- **The market clock.** `market_clock.py` embeds the NYSE holiday and early-close table for 2026 and 2027, transcribed
  from the NYSE hours calendar, with the America/New_York daylight-saving rule (no `tzdata` needed). The table expires
  on 2027-12-31; the doctor's check 3 fails 60 days before that.
- **Money** is an exact decimal everywhere (`pnl.py`, `payoff.py`, `positions_csv.py`); prices and Greeks are floats.
- **The selftest.** `desk_doctor.py --selftest` runs five engine checks on the desk computer: the price golden, an
  implied-volatility round trip, the 2026 daylight-saving transitions, the TOML reader and the alert lint.

The unit suite runs on Python 3.9, 3.10, 3.12 and 3.13 in CI, as a non-root user. The command journeys
(`scripts/cli_journeys.py`) run the documented commands against the synthetic fixture desk and screen every output
for order-shaped text. Where the numbers and thresholds come from is in [PROVENANCE.md](PROVENANCE.md).

## Trust boundaries

- **One network path.** `scripts/aegis_desk/tradier_client.py` is the only module that may open a connection: GET only,
  the sandbox host, seven allow-listed market-data paths, no redirects, no proxy. `scripts/check_no_write_paths.py`
  proves this by reading every module's syntax tree.
- **One credential.** A Tradier sandbox market-data token, which cannot reach a live account. The doctor verifies it
  and refuses a production host.
- **Owner-authority actions.** Adopting rules, rewriting `desk.toml` or `structuring.toml`, acknowledging a change,
  snoozing or marking a signal acted, and recording the connector readback need your tap on an Ask-first card; the
  commands carry `--owner-tapped`, or for an adoption your typed phrase. Each run of a structuring tool also asks first.
- **Fail closed on unacknowledged files.** The structuring tools answer nothing but UNAVAILABLE while `desk.toml`,
  `positions.csv` or `structuring.toml` differ from what you acknowledged, because the approval level, the share
  coverage and the criteria come from them.
- **The release pin.** `routine.py` runs no step unless the checkout is the clean commit of the release tag.
- **Text is data.** A web page, a file, a message or a skill never authorizes an action.

How each boundary is enforced, and what it does not cover, is in [SECURITY.md](../SECURITY.md).

## Design choices

The owner's decisions behind the design (2026-10-30):

| Question | Choice | Consequence |
| --- | --- | --- |
| The existing Aegis platform | a new repository; Aegis stays intact | selected math and parsers are ported (rewritten) into this repository |
| Who creates it | the owner created `fairgradient-ai/aegis-grok-desk` | pull requests to `main` |
| Visibility and license | public, MIT | a `LICENSE` and an `ATTRIBUTION.md` in every skill folder; HyperGrok's MIT notice kept for the adapted mechanics |
| Where positions come from | one hand-edited `positions.csv` | no brokerage API; marks come from market data |
| Market data | the Tradier sandbox token, 15 minutes delayed, no Greeks served | the desk computes implied volatility and Greeks itself; cadence and rules are tuned for delayed data |
| Where alerts go | the Grok Bot chats only | each routine posts in its owning Bot's chat; no external channel ships |
| Runtimes | Grok Bot and Grok Build both first-class | a Grok Bot bootstrap with a receipt, and a Grok Build plugin install with the hook |
| Roles and skills | original, not copied from the reference | purpose-built roles, skills, routines and tools |
| Your choices for positions you hold (2026-11-05) | in this release, by a fourth Bot, only on your request; a deterministic tool names one best candidate under your own criteria | its own footer and lint mode, an approval-level check, no order fields; counsel signs the wording before any public release |

## Later stages (planned)

- The Aegis Book & Risk Analyst and the Aegis Events and Calendar Analyst, with an American early-exercise lattice,
  scenario and event stress, volatility context and a decision calendar, are planned for Stage 2 (not in this release).
- For the Aegis Structuring Desk, the roll analysis of long puts, short puts and collars, weekly expirations, a
  cash-balance check and per-account approval levels are planned for Stage 2 (not in this release).
- The Aegis Rules Steward and the Aegis Review Desk, with replay of rules over past snapshots, signal outcomes and the
  journal review, and md_proxy, a local read-only proxy that would bring production market data inside the guarantee,
  are planned for Stage 3 (not in this release).
