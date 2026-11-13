#!/usr/bin/env python3
"""exit_check.py: the adopted exit rules evaluated against the book, one verdict per group.

Usage::

    python3 scripts/exit_check.py [--rules PATH --dry-run] [--book PATH] [--next-run-utc ISO-8601-UTC]
        [--fixture-set NAME] [--json] [--schema] [--timeout S] [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

The book is built in-process through ``aegis_desk.book.load_book`` exactly as ``scripts/book.py`` builds it (one
quotes request, the calendar's full closures, the same offline flags and error mapping), or read back from a saved
book with ``--book PATH`` (``state.read_json``, then ``aegis_desk.exit_rules.book_from_json``; a missing, corrupt or
malformed file is exit 1 ``book_invalid``). A saved book is evaluated at its own ``generated_at`` (its quotes, DTEs
and session are of that instant; ``--now`` stamps the envelope only), with the rows' ``#tags`` and opened dates read
from ``positions.csv`` when it can be read (otherwise nothing is suppressed and a warning says so).

The rulebook is the adopted one (``rules/ADOPTED`` and the file it names, verified byte for byte and rule by rule:
``not_adopted``, ``adopted_invalid``, ``rules_missing``, ``rules_tampered``, ``config_invalid`` are exit 1), or a
candidate given with ``--rules PATH``, which needs ``--dry-run``. The rulebook is read before any quote is fetched.

Rule state: exit_check reads ``state/rule_state.json`` (``aegis_desk.rule_state.load_with_status``) so a pending M or
G rule shows the confirmations the routine has recorded, and it NEVER writes it, adopted rulebook or candidate:
``book_check.py --post`` is the one writer of rule state, so the state says ``fired`` exactly when a signal was posted
and an inspection run can never consume a transition the routine must post. ``--dry-run`` is the explicit statement
a candidate (``--rules``) needs; on the adopted rulebook it changes nothing, because nothing is written either way.
``--next-run-utc`` is the caller's next run: a single observation suffices when it is later than the session's close
or the session is not live; without it another observation is coming and M and G rules wait for their
confirmations (``pending``, with ``observation N of M``).

Output: one line per group (``<group> <underlying> <structure>: <VERDICT>``) followed by every fired, pending,
unavailable, no-history and suppressed rule with its values; rules that are clear are silent (listed by id in the
JSON). Then the portfolio and underlying sections (their WATCH, pending, unavailable and no-history results are
reported there and never make a group UNAVAILABLE nor change the exit code), the one no-history line (decision 11:
``MOVE5_DOWN, MOVE5_UP and DAILY need the previous end-of-day snapshot (none for YYYY-MM-DD)``), the verdict counts,
the CONFIG CHANGED list (the sha256 of ``desk.toml``, ``positions.csv``, the adopted rulebook file and
``structuring.toml`` against ``state/desk_state.json["acknowledged_hashes"]``; a missing key is unacknowledged, a
missing structuring.toml with no acknowledged hash is no change; it never changes the exit code) and the data footer ``Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of HH:MM UTC``. Every number printed is the
value the engine decided on (``aegis_desk.exit_rules``, decision 15), written as a plain decimal.

Exit codes, by the one verdict table: 10 when any group is EXIT_SIGNAL, 20 when any group is UNAVAILABLE (and none
EXIT_SIGNAL), 0 otherwise; 20 also when the market data is refused (a host other than paper, no key pair, an expired holiday
table); 1 on a desk, configuration, rulebook or book file error (``desk_root``, ``config_invalid``,
``positions_missing``, ``positions_invalid``, ``calendar_invalid``, ``book_invalid``, the rulebook codes,
``io_error``); 2 on usage. ``--json`` payload: ``{rulebook {version, sha, path, adopted, rules}, dry_run,
evaluation_utc, et_date, session, single_observation, next_run_utc, book {...}, verdicts [...], group_verdicts,
counts, portfolio, underlyings, no_history, config_changed [...], state {status, written, updates}, warnings}``.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="exit_check")

import datetime  # noqa: E402
import hashlib  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from decimal import Decimal  # noqa: E402
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple  # noqa: E402

from aegis_desk import (  # noqa: E402
    book,
    calendar_events,
    desk_config,
    desk_paths,
    exit_rules,
    market_clock,
    positions_csv,
    quote_cache,
    rule_state,
    state,
)
from aegis_desk.version import release_version  # noqa: E402

TOOL = "exit_check"
DESCRIPTION = ("the adopted exit rules evaluated against the book: one verdict per group (EXIT_SIGNAL, UNAVAILABLE, "
               "WATCH, HOLD) with every fired, pending, unavailable and suppressed rule; reads the rule state, never "
               "writes it")
NETWORK_ACCESS = ("GET https://data.alpaca.markets/v2/stocks/quotes/latest and /v1beta1/options/quotes/latest only "
                  "(through the quote cache); none with --offline or --book")

ORIGIN_QUOTES = "quotes"
ORIGIN_FILE = "file"
CONFIG_NAMES: Tuple[str, ...] = ("desk_toml", "positions_csv", "rulebook", "structuring_toml")
"""The files CONFIG CHANGED compares, by their ``acknowledged_hashes`` key (the names ``alerts.config_changed_text``
prints; a test keeps ``ack.HASH_NAMES``, ``book_check.HASH_NAMES`` and this tuple equal)."""
CONFIG_LABELS: Dict[str, str] = {"desk_toml": "desk.toml", "positions_csv": "positions.csv",
                                 "rulebook": "adopted rulebook", "structuring_toml": "structuring.toml"}
ACKNOWLEDGE_HINT = "the rules were evaluated as the files are now; acknowledge with ack.py config --owner-tapped"
SECTIONS_NOTE = "the portfolio and underlying sections never set a group verdict or the exit code"
SNAPSHOT_NAME_RE = re.compile(r"^([0-9]{4}-[0-9]{2}-[0-9]{2})\.json\Z")
WARNING_ROWS = "positions.csv could not be read for the rows' tags and opened dates (%s)"
KIND_WORDS: Dict[str, str] = {exit_rules.RESULT_FIRED: "fired", exit_rules.RESULT_PENDING: "pending",
                              exit_rules.RESULT_COOLDOWN: "cooldown",
                              exit_rules.RESULT_UNAVAILABLE: "unavailable", exit_rules.RESULT_NO_HISTORY: "no history",
                              exit_rules.RESULT_SUPPRESSED: "suppressed"}
UNIT_WORDS: Dict[str, str] = {"percent": "percent", "usd": "usd", "days": "days", "delta": "delta", "multiple": "x",
                              "pct_of_threshold": "percent of the threshold"}

INPUT_KEYS: Tuple[str, ...] = ("name", "value", "source", "as_of", "grade", "note")
CONDITION_KEYS: Tuple[str, ...] = ("metric", "op", "threshold", "unit", "dte_unit", "truth", "kind", "value", "reason")
RESULT_KEYS: Tuple[str, ...] = (
    "rule_id", "rule_sha", "result", "verdict", "plan", "title", "role", "scope", "input_class", "basis", "metric",
    "op", "value", "threshold", "unit", "dte_unit", "subject", "reason", "quote_grade", "as_of", "observations",
    "confirmations", "first_observation", "new_signal", "state_status", "transition", "suppress_tag", "notes",
    "inputs", "conditions",
)
GROUP_KEYS: Tuple[str, ...] = ("group_id", "trade_id", "underlying", "structure", "verdict", "data_quality",
                               "quarantined", "reasons", "fired", "unavailable", "pending", "cooldown", "no_history",
                               "suppressed", "clear")
SECTION_KEYS: Tuple[str, ...] = ("scope", "subject", "fired", "unavailable", "pending", "cooldown", "no_history",
                                 "suppressed", "clear")
PAYLOAD_KEYS: Tuple[str, ...] = ("rulebook", "dry_run", "evaluation_utc", "et_date", "session", "single_observation",
                                 "next_run_utc", "book", "verdicts", "group_verdicts", "counts", "portfolio",
                                 "underlyings", "no_history", "config_changed", "state", "warnings")

_RESULT_SHAPE: Dict[str, Any] = {
    "rule_id": "string", "rule_sha": "sha256 of the rule's canonical semantics",
    "result": "fired | pending | cooldown | unavailable | no_history | suppressed",
    "verdict": "EXIT_SIGNAL | WATCH (the rule's own)", "plan": "close | roll_review | hold_review", "title": "string",
    "role": "take_profit | loss | time | risk | event | portfolio | watch", "scope": "group | leg | underlying | portfolio",
    "input_class": "E | M | G", "basis": "liq | mid", "metric": "the first condition's metric",
    "op": "< | <= | > | >= | == | is_true | is_false", "value": "decimal string | integer | boolean | null (the value compared)",
    "threshold": "decimal string | null", "unit": "percent | usd | days | delta | multiple | null",
    "dte_unit": "calendar | trading | null", "subject": "group id | row n SYMBOL | symbol | portfolio",
    "reason": "string (why unavailable, no history, suppressed or cooling down; empty when fired or pending)",
    "quote_grade": "A-F | null (the worst grade of the inputs)", "as_of": "ISO-8601 UTC | null (the oldest input stamp)",
    "observations": "integer (TRUE observations recorded, this one included)",
    "confirmations": "integer (observations needed; 1 when a single observation suffices)",
    "first_observation": "boolean", "new_signal": "boolean (this evaluation would move the rule into fired)",
    "state_status": "armed | pending | fired | cooldown | null", "transition": "string | null",
    "suppress_tag": "assign-ok | earnings-ok | null", "notes": ["string"],
    "inputs": [{"name": "string", "value": "decimal string | integer | boolean | date | null", "source": "book field",
                "as_of": "ISO-8601 UTC | null", "grade": "A-F | null", "note": "string | null"}],
    "conditions": [{"metric": "string", "op": "string", "threshold": "decimal string | null", "unit": "string | null",
                    "dte_unit": "calendar | trading | null", "truth": "true | false | unknown",
                    "kind": "value | not_applicable | unavailable | no_history", "value": "the metric's value | null",
                    "reason": "string"}],
}
_SECTION_SHAPE: Dict[str, Any] = {"scope": "portfolio | underlying", "subject": "portfolio | SYMBOL",
                                  "fired": [dict(_RESULT_SHAPE)], "unavailable": [dict(_RESULT_SHAPE)],
                                  "pending": [dict(_RESULT_SHAPE)], "cooldown": [dict(_RESULT_SHAPE)],
                                  "no_history": [dict(_RESULT_SHAPE)], "suppressed": [dict(_RESULT_SHAPE)],
                                  "clear": ["rule id"]}
OUTPUT: Dict[str, Any] = {
    "rulebook": {"version": "X.Y.Z", "sha": "sha256 of the rulebook file's bytes",
                 "path": "rules/exit_rules-X.Y.Z.toml (adopted) | absolute path (candidate)",
                 "adopted": "boolean (false for a --rules candidate)", "rules": "integer"},
    "dry_run": "boolean",
    "evaluation_utc": "ISO-8601 UTC (the run's instant; a saved book's own generated_at)",
    "et_date": "YYYY-MM-DD", "session": " | ".join(market_clock.SESSIONS),
    "single_observation": "boolean (one observation suffices: the session is not live or --next-run-utc is after its close)",
    "next_run_utc": "ISO-8601 UTC | null",
    "book": {"origin": "quotes | file", "path": "absolute path | null", "generated_at": "ISO-8601 UTC",
             "as_of_label": "string", "data_exit_code": "0 | 20 (the book's own data verdict)"},
    "verdicts": [{"group_id": "string", "trade_id": "string | null", "underlying": "string | null",
                  "structure": "string | null", "verdict": "EXIT_SIGNAL | UNAVAILABLE | WATCH | HOLD",
                  "data_quality": "ok | unavailable | null", "quarantined": "boolean", "reasons": ["string"],
                  "fired": [dict(_RESULT_SHAPE)], "unavailable": [dict(_RESULT_SHAPE)], "pending": [dict(_RESULT_SHAPE)],
                  "cooldown": [dict(_RESULT_SHAPE)], "no_history": [dict(_RESULT_SHAPE)],
                  "suppressed": [dict(_RESULT_SHAPE)],
                  "clear": ["rule id (clear rules are silent in prose)"]}],
    "group_verdicts": {"GROUP_ID": "EXIT_SIGNAL | UNAVAILABLE | WATCH | HOLD"},
    "counts": {"EXIT_SIGNAL": "n", "UNAVAILABLE": "n", "WATCH": "n", "HOLD": "n"},
    "portfolio": dict(_SECTION_SHAPE),
    "underlyings": [dict(_SECTION_SHAPE)],
    "no_history": {"rule_ids": ["rule id"], "history_date": "YYYY-MM-DD (the previous trading day)",
                   "line": "string | null"},
    "config_changed": [{"name": " | ".join(CONFIG_NAMES), "old": "acknowledged sha256 | null",
                        "new": "current sha256 | null (missing)"}],
    "state": {"status": " | ".join(rule_state.LOAD_STATUSES), "written": "false (exit_check never writes rule state)",
              "updates": "integer (transitions the routine would record)"},
    "warnings": ["string"],
}


class ToolError(Exception):
    """A failure the tool reports with an error envelope: ``code``, ``message`` and the exit code."""

    def __init__(self, code: str, message: str, exit_code: int) -> None:
        self.code = code
        self.message = message
        self.exit_code = exit_code
        super().__init__(message)


@dataclass
class BookSource:
    """The book a rulebook is evaluated against and everything the evaluation needs with it."""

    document: Dict[str, Any]
    session: market_clock.SessionInfo
    calendar: Optional[calendar_events.CalendarFile]
    rows: Optional[List[Any]]
    evaluated_at: datetime.datetime
    origin: str
    path: Optional[str]
    source_label: Optional[str]
    delay_min: Optional[int]
    data_exit_code: int
    warnings: List[str] = field(default_factory=list)


def user_agent(version: str, tool: str = TOOL) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, tool)


# --------------------------------------------------------------------------------------------
# Inputs: the desk, the rulebook, the book, the rows
# --------------------------------------------------------------------------------------------


def open_paths(desk_root: str) -> desk_paths.DeskPaths:
    try:
        return desk_paths.for_root(desk_root)
    except desk_paths.DeskPathsError as exc:
        raise ToolError("desk_root", str(exc), contract.EXIT_ERROR) from None


def load_rulebook_for(paths: desk_paths.DeskPaths, rules_path: Optional[str]) -> Tuple[exit_rules.Rulebook, bool]:
    """``(rulebook, adopted)``: the adopted rulebook (``rules_path`` None) or the candidate at ``rules_path``."""
    try:
        if rules_path is None:
            return exit_rules.load_adopted_rulebook(paths)[1], True
        return exit_rules.load_rulebook(os.path.abspath(rules_path)), False
    except exit_rules.RulebookError as exc:
        raise ToolError(exc.code, str(exc), contract.EXIT_ERROR) from None
    except OSError as exc:
        raise ToolError("io_error", "cannot read the rulebook: %s" % exc, contract.EXIT_ERROR) from None


def load_rows(paths: desk_paths.DeskPaths, today: datetime.date) -> Tuple[Optional[List[Any]], List[str]]:
    """The positions rows (tags and opened dates) for a saved book, or ``None`` with the warning saying why."""
    try:
        positions = positions_csv.load_positions(paths.positions_csv, today=today)
    except FileNotFoundError:
        return None, [WARNING_ROWS % "positions.csv not found"]
    except OSError as exc:
        return None, [WARNING_ROWS % ("cannot read positions.csv: %s" % exc)]
    if positions.file_errors:
        return None, [WARNING_ROWS % "; ".join(str(error) for error in positions.file_errors)]
    return list(positions.rows), []


def load_built_book(desk_root: str, *, now: datetime.datetime, offline: bool, fixture_set: Optional[str],
                    timeout_s: float, agent: str) -> BookSource:
    """The book built now from quotes through ``book.load_book`` (``scripts/book.py``'s path and error mapping)."""
    try:
        market = quote_cache.open_market_data(desk_root, now=now, offline=offline, fixture_set=fixture_set,
                                              timeout_s=timeout_s, user_agent=agent)
    except desk_paths.DeskPathsError as exc:
        raise ToolError("desk_root", str(exc), contract.EXIT_ERROR) from None
    except desk_config.DeskConfigError as exc:
        raise ToolError("config_invalid", str(exc), contract.EXIT_ERROR) from None
    except market_clock.TableExpiredError as exc:
        raise ToolError("holiday_table_expired", "holiday table expired: %s" % exc, contract.EXIT_UNAVAILABLE) from None
    except quote_cache.MarketDataRefused as exc:
        raise ToolError(exc.status, exc.message, contract.EXIT_UNAVAILABLE) from None
    except OSError as exc:
        raise ToolError("io_error", "cannot prepare the desk runtime directories: %s" % exc, contract.EXIT_ERROR) from None
    try:
        run = book.load_book(market)
    except book.BookInputError as exc:
        raise ToolError(exc.code, exc.message, contract.EXIT_ERROR) from None
    except OSError as exc:
        raise ToolError("io_error", "cannot read the desk files or use the cache: %s" % exc, contract.EXIT_ERROR) from None
    return BookSource(document=run.document, session=run.session, calendar=run.calendar, rows=list(run.positions.rows),
                      evaluated_at=market.now, origin=ORIGIN_QUOTES, path=None,
                      source_label=quote_cache.source_label(market.cfg), delay_min=market.cfg.market_data.feed_delay_min,
                      data_exit_code=run.exit_code)


def load_saved_book(paths: desk_paths.DeskPaths, path: str) -> BookSource:
    """A saved book (a ``--book`` file or a snapshot of record) read back for evaluation at its own instant."""
    try:
        raw, status = state.read_json(path)
    except OSError as exc:
        raise ToolError("book_invalid", "cannot read the book %s: %s" % (path, exc), contract.EXIT_ERROR) from None
    if status != state.STATUS_OK:
        what = "not found" if status == state.STATUS_MISSING else "not valid JSON (moved aside by the state layer)"
        raise ToolError("book_invalid", "the book %s is %s" % (path, what), contract.EXIT_ERROR)
    try:
        document = exit_rules.book_from_json(raw)
    except exit_rules.BookFormatError as exc:
        raise ToolError("book_invalid", "the book %s is not a book document: %s" % (path, exc), contract.EXIT_ERROR) from None
    try:
        calendar = calendar_events.load_calendar_if_present(paths.calendar_toml)
    except calendar_events.CalendarError as exc:
        raise ToolError("calendar_invalid", str(exc), contract.EXIT_ERROR) from None
    at = document["generated_at"]
    try:
        session = market_clock.session_at(at, extra_holidays=calendar_events.extra_closures(calendar))
    except market_clock.TableExpiredError as exc:
        raise ToolError("holiday_table_expired", "holiday table expired: %s" % exc, contract.EXIT_UNAVAILABLE) from None
    rows, warnings = load_rows(paths, session.et_date)
    source = document["sources"][0] if document["sources"] else None
    return BookSource(document=document, session=session, calendar=calendar, rows=rows, evaluated_at=at,
                      origin=ORIGIN_FILE, path=path, source_label=None if source is None else str(source["name"]),
                      delay_min=None if source is None else source["delay_min"],
                      data_exit_code=book.exit_code_for(document), warnings=warnings)


def latest_snapshot_path(paths: desk_paths.DeskPaths) -> Optional[str]:
    """The newest snapshot of record ``data/book/<YYYY-MM-DD>.json`` (by its date), or ``None`` when there is none;
    quarantined copies (``.corrupt-...``) and other names are ignored."""
    try:
        names = os.listdir(paths.book_dir)
    except FileNotFoundError:
        return None
    dated: List[Tuple[datetime.date, str]] = []
    for name in names:
        match = SNAPSHOT_NAME_RE.match(name)
        if match is None:
            continue
        try:
            dated.append((datetime.date.fromisoformat(match.group(1)), name))
        except ValueError:
            continue
    if not dated:
        return None
    return os.path.join(paths.book_dir, max(dated)[1])


def evaluate_book(rulebook: exit_rules.Rulebook, source: BookSource, entries: Mapping[str, Any],
                  next_run_utc: Optional[datetime.datetime]) -> exit_rules.Evaluation:
    """``exit_rules.evaluate`` of ``rulebook`` against ``source`` with the rule state ``entries`` (never changed)."""
    return exit_rules.evaluate(rulebook, source.document, now=source.evaluated_at, session=source.session,
                               calendar=source.calendar, state=entries, next_run_utc=next_run_utc, rows=source.rows)


# --------------------------------------------------------------------------------------------
# CONFIG CHANGED
# --------------------------------------------------------------------------------------------


def file_sha256(path: Optional[str]) -> Optional[str]:
    """The SHA-256 of the file's bytes, or ``None`` when there is no file."""
    if path is None:
        return None
    try:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()
    except FileNotFoundError:
        return None


def adopted_target(paths: desk_paths.DeskPaths) -> Optional[str]:
    """The rulebook file ``rules/ADOPTED`` names, or ``None`` when nothing usable is adopted."""
    try:
        adopted = exit_rules.load_adopted(paths.adopted_file)
    except exit_rules.RulebookError:
        return None
    return exit_rules.rulebook_path(paths, adopted.version)


def config_hashes(paths: desk_paths.DeskPaths) -> Dict[str, Optional[str]]:
    """The current SHA-256 of each file CONFIG CHANGED compares (:data:`CONFIG_NAMES`; ``None`` for a missing one, so
    a missing structuring.toml with no acknowledged hash is no change)."""
    return {"desk_toml": file_sha256(paths.desk_toml), "positions_csv": file_sha256(paths.positions_csv),
            "rulebook": file_sha256(adopted_target(paths)), "structuring_toml": file_sha256(paths.structuring_toml)}


def config_changes(paths: desk_paths.DeskPaths) -> List[Dict[str, Optional[str]]]:
    """``[{name, old, new}]`` for every file whose current sha differs from ``state/desk_state.json``'s
    ``acknowledged_hashes`` (a missing desk state, key or entry is unacknowledged: ``old`` null)."""
    document, _ = state.read_json(paths.desk_state_json)
    acknowledged = document.get("acknowledged_hashes") if isinstance(document, dict) else None
    if not isinstance(acknowledged, dict):
        acknowledged = {}
    hashes = config_hashes(paths)
    changes: List[Dict[str, Optional[str]]] = []
    for name in CONFIG_NAMES:
        old = acknowledged.get(name)
        old = old if isinstance(old, str) and exit_rules.SHA256_RE.match(old) else None  # anything else: unacknowledged
        if old != hashes[name]:
            changes.append({"name": name, "old": old, "new": hashes[name]})
    return changes


def config_changed_line(changes: Sequence[Mapping[str, Optional[str]]]) -> str:
    segments = ["CONFIG CHANGED"]
    for change in changes:
        old, new = change["old"], change["new"]
        segments.append("%s: %s -> %s" % (CONFIG_LABELS.get(str(change["name"]), str(change["name"])),
                                          "not acknowledged" if old is None else "sha %s" % old[:8],
                                          "missing" if new is None else "sha %s" % new[:8]))
    return ": ".join(segments[:2]) + "".join(" | %s" % segment for segment in segments[2:]) + " | " + ACKNOWLEDGE_HINT


# --------------------------------------------------------------------------------------------
# JSON and prose
# --------------------------------------------------------------------------------------------


def plain(value: Any) -> str:
    """A value as the verdict table prints it: plain decimals (never an exponent), ``true``/``false``, ``unknown``."""
    if value is None:
        return "unknown"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        return repr(value)
    return str(value)


def condition_text(condition: exit_rules.ConditionResult) -> str:
    """``pct_captured 95.32 >= 80 percent``, ``dte 13 <= 21 trading days``, ``stock_above_short_call true``."""
    if condition.op in exit_rules.BOOL_OPS:
        return "%s %s" % (condition.metric, plain(condition.value))
    metric = exit_rules.METRICS.get(condition.metric)
    unit = condition.unit or (metric.unit if metric is not None else None)
    words = "%s days" % condition.dte_unit if condition.dte_unit else UNIT_WORDS.get(str(unit), "")
    text = "%s %s %s %s" % (condition.metric, plain(condition.value), condition.op, plain(condition.threshold))
    return text + (" " + words if words else "")


def result_body(kind: str, result: exit_rules.RuleResult) -> str:
    """One rule's item in the verdict table, without its kind word."""
    if kind in (exit_rules.RESULT_FIRED, exit_rules.RESULT_PENDING, exit_rules.RESULT_COOLDOWN):
        text = "%s %s" % (result.rule_id, " and ".join(condition_text(item) for item in result.conditions))
        if kind == exit_rules.RESULT_PENDING:
            text += ", observation %d of %d" % (result.observations, result.confirmations)
        if kind == exit_rules.RESULT_COOLDOWN:
            text += ", %s" % result.reason
        if result.first_observation:
            text += ", already past this threshold at the desk's first observation"
        return "%s (%s, plan %s)" % (text, result.verdict, result.plan)
    if kind == exit_rules.RESULT_SUPPRESSED:
        return "%s (%s)" % (result.rule_id, result.reason)
    return "%s: %s" % (result.rule_id, result.reason)


def result_json(result: exit_rules.RuleResult) -> Dict[str, Any]:
    return {
        "rule_id": result.rule_id, "rule_sha": result.rule_sha, "result": result.result, "verdict": result.verdict,
        "plan": result.plan, "title": result.title, "role": result.role, "scope": result.scope,
        "input_class": result.input_class, "basis": result.basis, "metric": result.metric, "op": result.op,
        "value": result.value, "threshold": result.threshold, "unit": result.unit, "dte_unit": result.dte_unit,
        "subject": result.subject, "reason": result.reason, "quote_grade": result.quote_grade, "as_of": result.as_of,
        "observations": result.observations, "confirmations": result.confirmations,
        "first_observation": result.first_observation, "new_signal": result.new_signal,
        "state_status": result.state_status, "transition": result.transition, "suppress_tag": result.suppress_tag,
        "notes": list(result.notes),
        "inputs": [{"name": item.name, "value": item.value, "source": item.source, "as_of": item.as_of,
                    "grade": item.grade, "note": item.note} for item in result.inputs],
        "conditions": [{"metric": item.metric, "op": item.op, "threshold": item.threshold, "unit": item.unit,
                        "dte_unit": item.dte_unit, "truth": item.truth, "kind": item.kind, "value": item.value,
                        "reason": item.reason} for item in result.conditions],
    }


def group_json(verdict: exit_rules.GroupVerdict) -> Dict[str, Any]:
    return {"group_id": verdict.group_id, "trade_id": verdict.trade_id, "underlying": verdict.underlying,
            "structure": verdict.structure, "verdict": verdict.verdict, "data_quality": verdict.data_quality,
            "quarantined": verdict.quarantined, "reasons": list(verdict.reasons),
            "fired": [result_json(item) for item in verdict.fired],
            "unavailable": [result_json(item) for item in verdict.unavailable],
            "pending": [result_json(item) for item in verdict.pending],
            "cooldown": [result_json(item) for item in verdict.cooldown],
            "no_history": [result_json(item) for item in verdict.no_history],
            "suppressed": [result_json(item) for item in verdict.suppressed],
            "clear": [item.rule_id for item in verdict.clear]}


def section_json(section: exit_rules.SectionVerdict) -> Dict[str, Any]:
    return {"scope": section.scope, "subject": section.subject,
            "fired": [result_json(item) for item in section.fired],
            "unavailable": [result_json(item) for item in section.unavailable],
            "pending": [result_json(item) for item in section.pending],
            "cooldown": [result_json(item) for item in section.cooldown],
            "no_history": [result_json(item) for item in section.no_history],
            "suppressed": [result_json(item) for item in section.suppressed],
            "clear": [item.rule_id for item in section.clear]}


def join_and(items: Sequence[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return "%s and %s" % (", ".join(items[:-1]), items[-1])


def no_history_line(evaluation: exit_rules.Evaluation) -> Optional[str]:
    """Decision 11's one line, or ``None`` when no rule waits for the previous end-of-day snapshot."""
    rule_ids = evaluation.no_history_rule_ids
    if not rule_ids:
        return None
    return "no history: %s %s the previous end-of-day snapshot (none for %s)" % (
        join_and(rule_ids), "needs" if len(rule_ids) == 1 else "need", evaluation.history_date.isoformat())


def rulebook_json(rulebook: exit_rules.Rulebook, adopted: bool, paths: desk_paths.DeskPaths) -> Dict[str, Any]:
    path = paths.relative(rulebook.path) if adopted else os.path.abspath(rulebook.path)
    return {"version": rulebook.rulebook_version, "sha": rulebook.sha256, "path": path, "adopted": adopted,
            "rules": len(rulebook.rules)}


def book_json(source: BookSource) -> Dict[str, Any]:
    return {"origin": source.origin, "path": source.path, "generated_at": source.document["generated_at"],
            "as_of_label": source.document["as_of_label"], "data_exit_code": source.data_exit_code}


def payload_for(evaluation: exit_rules.Evaluation, *, rulebook: exit_rules.Rulebook, adopted: bool, dry_run: bool,
                source: BookSource, paths: desk_paths.DeskPaths, state_status: str,
                changes: Sequence[Mapping[str, Optional[str]]]) -> Dict[str, Any]:
    """The ``--json`` payload (:data:`PAYLOAD_KEYS`)."""
    return {
        "rulebook": rulebook_json(rulebook, adopted, paths),
        "dry_run": dry_run,
        "evaluation_utc": evaluation.evaluated_at,
        "et_date": evaluation.et_date,
        "session": source.session.session,
        "single_observation": evaluation.single_observation,
        "next_run_utc": evaluation.next_run_utc,
        "book": book_json(source),
        "verdicts": [group_json(verdict) for verdict in evaluation.groups],
        "group_verdicts": {verdict.group_id: verdict.verdict for verdict in evaluation.groups},
        "counts": evaluation.verdict_counts(),
        "portfolio": section_json(evaluation.portfolio),
        "underlyings": [section_json(section) for section in evaluation.underlyings],
        "no_history": {"rule_ids": evaluation.no_history_rule_ids, "history_date": evaluation.history_date,
                       "line": no_history_line(evaluation)},
        "config_changed": [dict(change) for change in changes],
        "state": {"status": state_status, "written": False, "updates": len(evaluation.state_updates)},
        "warnings": list(source.warnings) + list(evaluation.warnings),
    }


def utc_minute(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def book_phrase(source: BookSource) -> str:
    where = ("the book built from quotes" if source.origin == ORIGIN_QUOTES
             else "the saved book %s" % source.path)
    return "%s at %s (session %s, %s)" % (where, utc_minute(source.evaluated_at), source.session.session,
                                          source.document["as_of_label"])


def group_heading(verdict: exit_rules.GroupVerdict) -> str:
    parts = [verdict.group_id]
    if verdict.underlying:
        parts.append(str(verdict.underlying))
    if verdict.structure:
        parts.append(str(verdict.structure))
    elif verdict.quarantined:
        parts.append("quarantined")
    return "%s: %s" % (" ".join(parts), verdict.verdict)


def group_lines(verdict: exit_rules.GroupVerdict) -> List[str]:
    lines = [group_heading(verdict)]
    lines.extend("  quarantined: %s" % reason for reason in verdict.reasons if verdict.quarantined)
    for kind, results in ((exit_rules.RESULT_FIRED, verdict.fired), (exit_rules.RESULT_PENDING, verdict.pending),
                          (exit_rules.RESULT_COOLDOWN, verdict.cooldown),
                          (exit_rules.RESULT_UNAVAILABLE, verdict.unavailable),
                          (exit_rules.RESULT_NO_HISTORY, verdict.no_history),
                          (exit_rules.RESULT_SUPPRESSED, verdict.suppressed)):
        lines.extend("  %s: %s" % (KIND_WORDS[kind], result_body(kind, result)) for result in results)
    return lines


def section_lines(section: exit_rules.SectionVerdict) -> List[str]:
    label = "portfolio" if section.scope == exit_rules.SCOPE_PORTFOLIO else "underlying %s" % section.subject
    lines: List[str] = []
    for kind, results in ((exit_rules.RESULT_FIRED, section.fired), (exit_rules.RESULT_PENDING, section.pending),
                          (exit_rules.RESULT_COOLDOWN, section.cooldown),
                          (exit_rules.RESULT_UNAVAILABLE, section.unavailable),
                          (exit_rules.RESULT_SUPPRESSED, section.suppressed)):
        lines.extend("%s: %s %s" % (label, KIND_WORDS[kind], result_body(kind, result)) for result in results)
    return lines


def counts_line(evaluation: exit_rules.Evaluation) -> str:
    counts = evaluation.verdict_counts()
    return "verdicts: %s (%s)" % (", ".join("%s %d" % (verdict, counts[verdict]) for verdict in exit_rules.GROUP_VERDICTS),
                                  SECTIONS_NOTE)


def evaluation_lines(evaluation: exit_rules.Evaluation) -> List[str]:
    """The verdict table: every group, then the sections, the no-history line and the counts."""
    lines: List[str] = []
    for verdict in evaluation.groups:
        lines.extend(group_lines(verdict))
    for section in [evaluation.portfolio] + list(evaluation.underlyings):
        lines.extend(section_lines(section))
    line = no_history_line(evaluation)
    if line is not None:
        lines.append(line)
    lines.append(counts_line(evaluation))
    return lines


def prose_lines(evaluation: exit_rules.Evaluation, *, rulebook: exit_rules.Rulebook, adopted: bool, dry_run: bool,
                source: BookSource, paths: desk_paths.DeskPaths, state_status: str,
                changes: Sequence[Mapping[str, Optional[str]]]) -> List[str]:
    which = "adopted" if adopted else "candidate %s" % os.path.abspath(rulebook.path)
    lines = ["exit check%s: rules %s, sha %s (%s), on %s" % (" (dry run)" if dry_run else "", rulebook.rulebook_version,
                                                          rulebook.sha256[:8], which, book_phrase(source))]
    if changes:
        lines.append(config_changed_line(changes))
    lines.extend(evaluation_lines(evaluation))
    lines.extend("warning: %s" % warning for warning in list(source.warnings) + list(evaluation.warnings))
    lines.append("rule state: read %s (%s); exit_check writes nothing (book_check.py --post records the transitions)"
                 % (paths.relative(paths.rule_state_json), state_status))
    lines.append(book.footer_from_document(source.document))
    return lines


# --------------------------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------------------------


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--rules", metavar="PATH", default=None,
                        help="evaluate this candidate rulebook instead of the adopted one (needs --dry-run)")
    parser.add_argument("--dry-run", action="store_true",
                        help="state that nothing is written (required with --rules; exit_check never writes rule state)")
    parser.add_argument("--book", metavar="PATH", default=None,
                        help="evaluate a saved book (a book.json or a data/book/ snapshot) instead of building one")
    parser.add_argument("--next-run-utc", metavar="ISO-8601-UTC", default=None,
                        help="the caller's next run: after the session's close a single observation suffices")
    parser.add_argument("--fixture-set", metavar="NAME", default=None,
                        help="recorded fixture set under <desk-root>/fixtures/alpaca/ (default regular; --offline only)")
    return parser


def parse_next_run(parser: contract.ReadOnlyParser, text: Optional[str],
                   now: datetime.datetime) -> Optional[datetime.datetime]:
    """``--next-run-utc`` parsed and checked against the run instant (``rule_state.check_next_run``, the one rule
    book_check, exit_check and rules_diff share); usage exit 2 for a malformed value or one not later than ``now``."""
    if text is None:
        return None
    try:
        return rule_state.check_next_run(contract.parse_utc_instant(text, "--next-run-utc"), now)
    except ValueError as exc:
        parser.error(str(exc))
    return None  # pragma: no cover - parser.error exits


def validate(parser: contract.ReadOnlyParser, args: Any, now: datetime.datetime) -> Optional[datetime.datetime]:
    """Refuse inconsistent flags (exit 2) and return the parsed ``--next-run-utc`` (later than ``now``)."""
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")
    if args.book is not None and args.fixture_set is not None:
        parser.error("--fixture-set applies to a book built from quotes, not to --book")
    if args.rules is not None and not args.dry_run:
        parser.error("--rules evaluates a candidate rulebook and needs --dry-run")
    return parse_next_run(parser, args.next_run_utc, now)


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int,
         tool: str = TOOL) -> int:
    status = contract.status_for(exit_code)
    if args.json:
        print(contract.json_dumps(contract.error_envelope(tool, version, code, message, status, generated_at=now)))
    elif exit_code == contract.EXIT_UNAVAILABLE:
        print("UNAVAILABLE: %s" % message)
    else:
        print("error: %s" % message)
    return exit_code


def load_source(args: Any, paths: desk_paths.DeskPaths, now: datetime.datetime, agent: str) -> BookSource:
    """``--book`` read back, or the book built now from quotes."""
    if args.book is not None:
        return load_saved_book(paths, os.path.abspath(args.book))
    return load_built_book(args.desk_root, now=now, offline=args.offline, fixture_set=args.fixture_set,
                           timeout_s=args.timeout, agent=agent)


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    now = args.now or state.utc_now()
    next_run_utc = validate(parser, args, now)
    try:
        paths = open_paths(args.desk_root)
        rulebook, adopted = load_rulebook_for(paths, args.rules)
        source = load_source(args, paths, now, user_agent(version))
        entries, state_status = rule_state.load_with_status(paths)
        evaluation = evaluate_book(rulebook, source, entries, next_run_utc)
        changes = config_changes(paths)
    except ToolError as exc:
        return fail(args, version, now, exc.code, exc.message, exc.exit_code)
    except OSError as exc:
        return fail(args, version, now, "io_error", "cannot read the desk files: %s" % exc, contract.EXIT_ERROR)
    exit_code = evaluation.exit_code()
    common = dict(rulebook=rulebook, adopted=adopted, dry_run=args.dry_run, source=source, paths=paths,
                  state_status=state_status, changes=changes)
    if args.json:
        envelope = contract.envelope(TOOL, version, payload_for(evaluation, **common), source=source.source_label,
                                     delay_min=source.delay_min, generated_at=now,
                                     status=contract.status_for(exit_code) if exit_code else contract.STATUS_OK)
        print(contract.json_dumps(envelope))
        return exit_code
    print("\n".join(prose_lines(evaluation, **common)))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
