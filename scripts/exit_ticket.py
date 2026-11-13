#!/usr/bin/env python3
"""exit_ticket.py: a ticket for a position you hold: CLOSE, UNWIND_STOCK, LET_ASSIGN, MONETIZE_PUT or ROLL.

Usage::

    python3 scripts/exit_ticket.py --trade AG-YYYYMMDD-NN --action CLOSE|UNWIND_STOCK|LET_ASSIGN|MONETIZE_PUT|ROLL
        [--new-expiration YYYY-MM-DD --new-strike K] [--fixture-set NAME] [--json] [--schema] [--timeout S]
        [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

The Aegis Structuring Desk's ticket tool (PR 3b; designer Q's D11-D13 and designer C's D3, D6, D7 and D15 under the
addendum S1, S2 and S5-S11), run only when the owner asks (an Ask-first card per run; never a routine step). It names
the legs of the action for one group of ``positions.csv`` in the fixed leg grammar, in the safe order, with each leg's
bid, ask, mid, quote grade and age, the approval-level check, the cash at the conservative side and at the mid with
the most cash the steps need at once, the realized estimate of the closing legs and, for ROLL, the replacement the
owner names (``--new-expiration`` and ``--new-strike``: the held leg's root and right) with its criteria labels and
after-state. The owner supplies the limit (the desk prints none) and acts in the brokerage account.
``aegis_desk.tickets`` computes everything; this tool gathers the inputs in designer C's D3.6 order, prints the linted
text and records it:

1. ``exit_check.config_changes`` first: an unacknowledged desk.toml, positions.csv or structuring.toml prints the
   ``config_unacknowledged`` line and nothing else (exit 20); an unacknowledged adopted rulebook alone prints its
   CONFIG CHANGED line first and the ticket follows.
2. ``structuring.toml`` (``structuring_config``): missing is exit 20 ``criteria_missing``; unusable is exit 1
   ``structuring_invalid``.
3. ``positions.csv`` and the approval-level check (``aegis_desk.approval`` over every prefix state, the owner's level
   from the acknowledged desk.toml): REFUSED ``approval_level`` or ``intermediate_exposure`` (exit 20) before any quote.
4. Market data, only while the session trades (else UNAVAILABLE ``market_closed``, exit 20): one group-scoped quotes
   batch, and for ROLL the one chain of the replacement's expiration (``structuring.run_replacement``).

The text ends with the structuring footer and is linted in structuring mode before it prints: a finding prints only
the WITHHELD line and the footer (exit 1 ``lint_failed``; ``--json`` shows the text that failed). A TICKET (exit 0)
writes ``tickets/TK-<trade_id>-<ACTION>-<YYYYMMDDTHHMMSSZ>.json`` (the standard envelope around the payload without
the run's own keys) and ``.md`` atomically under the desk lock (the directory created at the first write; identical
bytes are a no-op, different bytes exit 1 ``ticket_exists`` and nothing is written or recorded); no other answer
writes a ticket. Every run that prints a text (exit 0 or 20, an UNAVAILABLE printed through the error envelope
included) appends one record to ``journal/structuring.jsonl``; the tool writes nothing else (the quote cache and its
ledger aside). ``--now`` (or the clock) is read to the whole second, the ticket id's resolution: two requests in one
second for the same action are one ticket (a second ROLL ticket for another replacement in that second is exit 1
``ticket_exists``).

Exit codes: 0 TICKET or NOT_APPLICABLE (a ROLL replacement that fails a criterion is still a TICKET: its labels
print); 20 UNAVAILABLE (``market_closed``, ``group_quarantined``, ``positions_quarantined``, ``spot_unusable``,
``held_leg_unusable``,
``chain_not_served``, ``replacement_not_listed``, ``replacement_unpriced``, ``config_unacknowledged``,
``criteria_missing``; and, with an error envelope, ``no_credential``, ``live_host``, ``credential_file_mode`` and
``holiday_table_expired``) and REFUSED; 1 ``desk_root``, ``config_invalid``, ``unknown_trade``, ``positions_invalid``,
``calendar_invalid``, ``structuring_invalid``, ``io_error``, ``ticket_exists``, ``lint_failed``; 2 usage (a malformed
trade id, ``--new-expiration``/``--new-strike`` missing with ROLL or given with another action, a replacement that does
not expire after the held leg). Never 10: a ticket is not an alert. ``--json`` payload: as ``--schema`` describes.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="exit_ticket")

import datetime  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from decimal import Decimal, InvalidOperation  # noqa: E402
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple  # noqa: E402

import exit_check  # noqa: E402  (config_changes: the CONFIG CHANGED comparison book_check, eod_summary and ack share)
from aegis_desk import (  # noqa: E402
    calendar_events,
    desk_config,
    desk_paths,
    market_clock,
    positions_csv,
    quote_cache,
    state,
    structuring,
    structuring_config,
    structuring_text,
    tickets,
)
from aegis_desk.version import release_version  # noqa: E402

TOOL = structuring.TOOL_EXIT_TICKET
DESCRIPTION = ("a ticket for a group you hold in positions.csv, only when you ask: the legs of CLOSE, UNWIND_STOCK, "
               "LET_ASSIGN, MONETIZE_PUT or ROLL in the safe order with their quotes, the approval-level check, the "
               "cash and the realized estimate; you supply the limit and act in your brokerage account")
NETWORK_ACCESS = ("GET https://data.alpaca.markets/v2/stocks/quotes/latest and /v1beta1/options/quotes/latest (one "
                  "batch of the group's symbols) and, for ROLL, the replacement expiration's chain "
                  "(https://paper-api.alpaca.markets/v2/options/contracts, then "
                  "https://data.alpaca.markets/v1beta1/options/snapshots/<UNDERLYING>), through the quote cache; "
                  "none with --offline")
BLOCKING_CHANGES: Tuple[str, ...] = ("desk_toml", "positions_csv", "structuring_toml")
"""The files whose unacknowledged change stops every structuring text (designer C's D3.6); the rulebook does not."""
CAUSE_CONFIG_UNACKNOWLEDGED = "config_unacknowledged"
CAUSE_CRITERIA_MISSING = "criteria_missing"
REASON_CONFIG_UNACKNOWLEDGED = "desk.toml, positions.csv or structuring.toml differ from what you acknowledged"
REASON_CRITERIA_MISSING = "no structuring.toml: the Structuring Desk shows nothing until you set your criteria"
"""The payload ``reason`` of the two answers decided before the ticket engine runs (the words of roll_analysis.py and
propose.py)."""
ERROR_TICKET_EXISTS = structuring_text.ReportExists.code
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
STRIKE_RE = re.compile(r"^[0-9]+(?:[.][0-9]+)?\Z")
EXTRA_KEYS: Tuple[str, ...] = ("config_changed", "message", "emitted", "lint", "withheld", "files", "journal",
                               "exit_code")
PAYLOAD_KEYS: Tuple[str, ...] = tickets.TICKET_KEYS + EXTRA_KEYS
FILE_EXCLUDED: Tuple[str, ...] = ("requests", "warnings", "files", "journal", "exit_code")
"""The run's own keys, left out of the ticket file so that a rerun at the same instant writes the same bytes."""

_HOLDINGS = {"shares": "integer | decimal string (a fractional share count)", "long_calls": "integer", "short_calls": "integer", "long_puts": "integer",
             "short_puts": "integer"}
OUTPUT: Dict[str, Any] = {
    "ticket_id": "TK-<trade_id>-<ACTION>-<YYYYMMDDTHHMMSSZ> | null (no ticket)",
    "trade_id": "AG-YYYYMMDD-NN (--trade)",
    "group_id": "string | null",
    "underlying": "string | null",
    "structure": "the grouping structure (covered_call, protective_put, ...) | null",
    "account": "the account cell of positions.csv (JSON only: the text names the account by its desk.toml number) | null",
    "account_type": "the desk.toml account type (margin, ira, ...) | null",
    "action": " | ".join(structuring.ACTIONS),
    "verdict": " | ".join(tickets.VERDICTS),
    "cause": "why nothing was ticketed: an UNAVAILABLE cause, config_unacknowledged, criteria_missing or a "
             "NOT_APPLICABLE code | null",
    "reason": "the desk's own words for the cause | null",
    "refused_code": " | ".join(tickets.REFUSED_CODES) + " | null",
    "computed_utc": "ISO-8601 UTC (--now)",
    "valid_until": "ISO-8601 UTC: min(now + 30 min, the session's close) | null",
    "valid_full": "boolean: the full 30 minutes fit before the close | null",
    "session": {"session": "the market_clock session", "et_date": "YYYY-MM-DD", "close_utc": "ISO-8601 UTC | null",
                "live": "boolean"},
    "owner_level": "integer 0-5: desk.toml's self-attested approval level | null",
    "criteria": "structuring.toml echoed (StructuringConfig.as_dict, its sha256 included) | null",
    "legs": [{"step": "integer (1-based, the order the owner enters them)",
              "intent": " | ".join(tickets.INTENTS), "effect": "open | close", "side": "long | short",
              "instrument": "call | put | shares",
              "quantity": "integer (contracts or shares) | decimal string (a fractional share count)",
              "row_number": "integer (the positions.csv row) | null (the new leg of a ROLL)",
              "contract": "the contract's symbol (JSON only) or the share symbol", "root": "string",
              "right": "CALL | PUT | null", "strike": "decimal string | null", "expiration": "YYYY-MM-DD | null",
              "multiplier": "integer", "bid": "decimal string | null", "ask": "decimal string | null",
              "mid": "decimal string | null", "spread_pct": "decimal string | null", "grade": "A-E",
              "quote_status": "string", "quote_age_min": "integer | null", "as_of": "ISO-8601 UTC",
              "conservative_side": "bid | ask (the side the leg is valued at)",
              "conservative": "decimal string (that side)", "note": "the grade E note | null",
              "cash_conservative": "decimal string (+ received, - paid)", "cash_mid": "decimal string | null",
              "tick": "decimal string: the increment of the owner's own limit (display only)"}],
    "approval": {"check": "the approval-level check", "ok": "boolean", "required_level": "integer",
                 "owner_level": "integer",
                 "refusal": {"step": "integer", "kind": "string", "level": "integer", "words": "string"},
                 "exposure_step": "integer | null"},
    "steps": [{"step": "integer", "kind": "the approval step kind", "level": "integer", "words": "string",
               "after": _HOLDINGS, "uncovered_calls": "integer"}],
    "cash": {"net_conservative": "decimal string", "net_mid": "decimal string | null",
             "peak_needed": "decimal string: the most cash the steps need at once", "prefix": ["decimal string"]},
    "realized": [{"row_number": "integer", "conservative": "decimal string", "mid": "decimal string | null"}],
    "let_assign": [{"row_number": "integer", "contract": "string", "right": "CALL | PUT", "strike": "decimal string",
                    "expiration": "YYYY-MM-DD", "last_trading_day": "YYYY-MM-DD", "contracts": "integer",
                    "shares": "integer", "spot_mid": "decimal string", "itm": "boolean (the one-cent rule)",
                    "act_by_utc": "ISO-8601 UTC", "act_by_et": "YYYY-MM-DD HH:MM ET", "act_by_passed": "boolean",
                    "act_by_text": "string", "cash_if_assigned": "decimal string",
                    "realized_if_assigned": "decimal string | null",
                    "dividend": {"known": "boolean", "applicable": "boolean", "status": "string",
                                 "ex_date": "YYYY-MM-DD | null", "amount": "decimal string | null",
                                 "cum_day": "YYYY-MM-DD | null", "reason": "string | null"}}],
    "puts": [{"row_number": "integer", "strike": "decimal string", "expiration": "YYYY-MM-DD",
              "spot_mid": "decimal string", "bid": "decimal string", "itm": "boolean (the one-cent rule)",
              "intrinsic": "decimal string", "time_value": "decimal string"}],
    "replacement": {"kind": "replacement_call | replacement_put", "section": "roll | protective_put",
                    "objective": {"id": "string", "words": "string", "formula": "string", "unit": "string",
                                  "direction": "higher | lower"},
                    "candidate": "structuring.Candidate.as_dict (status, reasons, accepted, after, ...)",
                    "held": "structuring.HeldLeg.as_dict", "before": "structuring.BeforeState.as_dict | null"},
    "spot": {"symbol": "string", "bid": "decimal string | null", "ask": "decimal string | null",
             "mid": "decimal string | null", "grade": "A-F", "as_of": "ISO-8601 UTC | null"},
    "model_note": "the European model's note (ROLL) | null",
    "requests": {"made": "integer", "chains": "integer", "per_run_max_requests": "integer",
                 "per_run_max_chains": "integer", "budget_left": "integer"},
    "warnings": ["string"],
    "config_changed": [{"name": " | ".join(exit_check.CONFIG_NAMES), "old": "acknowledged sha256 | null",
                        "new": "current sha256 | null"}],
    "message": "string: the text exactly as printed (the last line is the structuring footer)",
    "emitted": {"emitted_numbers": ["string"], "rule_ids": ["string (none)"], "identifiers": ["string"],
                "clocks": ["HH:MM UTC | HH:MM ET"], "as_of": "ISO-8601 UTC (the footer's instant)",
                "footer": "structuring"},
    "lint": {"ok": "boolean", "problems": ["string"], "numbers_checked": "integer"},
    "withheld": "{codes: [string], text: string (the text that failed the lint; exit 1 lint_failed)} | null",
    "files": {"json": "tickets/<ticket_id>.json (this envelope without requests, warnings, files, journal and "
                      "exit_code)",
              "md": "tickets/<ticket_id>.md (the text exactly as printed)",
              "written": "boolean (false: identical bytes already there)"},
    "journal": "journal/structuring.jsonl | null (nothing printed)",
    "exit_code": "0 | 1 | 20",
}


class ToolError(Exception):
    """A failure the tool reports with an error envelope: exit 1, or 20 for market data the desk cannot use as
    configured (``no_credential``, ``live_host``, ``credential_file_mode``) and an expired holiday table."""

    def __init__(self, error: str, message: str, exit_code: int = contract.EXIT_ERROR) -> None:
        super().__init__(message)
        self.error = error
        self.message = message
        self.exit_code = exit_code


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--trade", metavar="AG-YYYYMMDD-NN", default=None,
                        help="the trade id of the group in positions.csv (required)")
    parser.add_argument("--action", choices=structuring.ACTIONS, default=None,
                        help="CLOSE (the option legs), UNWIND_STOCK (the shares and every option leg), LET_ASSIGN (the "
                             "short option left to expiry), MONETIZE_PUT (the long put of a protective put or a collar) "
                             "or ROLL (a covered call's short call or a protective put's long put replaced) (required)")
    parser.add_argument("--new-expiration", metavar="YYYY-MM-DD", default=None,
                        help="ROLL only, with --new-strike: the replacement's expiration, later than the held leg's")
    parser.add_argument("--new-strike", metavar="K", default=None,
                        help="ROLL only, with --new-expiration: the replacement's strike (its root and right are the "
                             "held leg's)")
    parser.add_argument("--fixture-set", metavar="NAME", default=None,
                        help="recorded fixture set under <desk-root>/fixtures/alpaca/ (default regular; --offline only)")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any) -> None:
    """Refuse a malformed command line (exit 2); ``args.new_expiration`` / ``args.new_strike`` become a date and a
    Decimal."""
    if args.trade is None:
        parser.error("--trade AG-YYYYMMDD-NN is required")
    if not positions_csv.TRADE_ID_RE.match(args.trade):
        parser.error("--trade must be a trade id such as AG-20261020-01, got %r" % args.trade)
    if args.action is None:
        parser.error("--action is required (one of %s)" % ", ".join(structuring.ACTIONS))
    given = (args.new_expiration is not None, args.new_strike is not None)
    if args.action == structuring.ACTION_ROLL and given != (True, True):
        parser.error("--action ROLL needs both --new-expiration YYYY-MM-DD and --new-strike K")
    if args.action != structuring.ACTION_ROLL and any(given):
        parser.error("--new-expiration and --new-strike apply to --action ROLL only")
    if args.new_expiration is not None:
        day: Optional[datetime.date] = None
        if DATE_RE.match(args.new_expiration):
            try:
                day = datetime.date.fromisoformat(args.new_expiration)
            except ValueError:
                day = None
        if day is None:
            parser.error("--new-expiration must be a date YYYY-MM-DD, got %r" % args.new_expiration)
        args.new_expiration = day
    if args.new_strike is not None:
        strike: Optional[Decimal] = None
        if STRIKE_RE.match(args.new_strike):
            try:
                strike = Decimal(args.new_strike)
            except InvalidOperation:
                strike = None
        if strike is None or not strike > 0:
            parser.error("--new-strike must be a positive number such as 115 or 112.5, got %r" % args.new_strike)
        args.new_strike = strike
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")


def fail(args: Any, version: str, now: datetime.datetime, error: str, message: str,
         exit_code: int = contract.EXIT_ERROR) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, error, message, contract.status_for(exit_code),
                                                          generated_at=now)))
    elif exit_code == contract.EXIT_UNAVAILABLE:
        print("UNAVAILABLE: %s" % message)
    else:
        print("error: %s" % message)
    return exit_code


def open_market(args: Any, now: datetime.datetime, version: str) -> quote_cache.MarketData:
    try:
        return quote_cache.open_market_data(args.desk_root, now=now, offline=args.offline, fixture_set=args.fixture_set,
                                            timeout_s=args.timeout, user_agent=user_agent(version))
    except desk_paths.DeskPathsError as exc:
        raise ToolError("desk_root", str(exc)) from None
    except desk_config.DeskConfigError as exc:
        raise ToolError("config_invalid", str(exc)) from None
    except market_clock.TableExpiredError as exc:
        raise ToolError("holiday_table_expired", "holiday table expired: %s" % exc, contract.EXIT_UNAVAILABLE) from None
    except quote_cache.MarketDataRefused as exc:
        raise ToolError(exc.status, exc.message, contract.EXIT_UNAVAILABLE) from None


def empty_payload(trade_id: str, action: str, now: datetime.datetime) -> Dict[str, Any]:
    """Every payload key, for an answer decided before the ticket engine runs."""
    payload: Dict[str, Any] = {key: None for key in PAYLOAD_KEYS}
    payload.update({"trade_id": trade_id, "action": action, "computed_utc": now, "legs": [], "steps": [],
                    "realized": [], "let_assign": [], "puts": [], "warnings": [], "config_changed": []})
    return payload


def exit_code_of(verdict: str) -> int:
    return contract.EXIT_QUIET if verdict in (tickets.VERDICT_TICKET, tickets.VERDICT_NOT_APPLICABLE) \
        else contract.EXIT_UNAVAILABLE


def journal(paths: desk_paths.DeskPaths, *, now: datetime.datetime, payload: Mapping[str, Any], text: str,
            criteria_sha256: Optional[str]) -> str:
    """One ``journal/structuring.jsonl`` record of the printed text (designer C's D7.5); ``refused_code`` carries the
    refusal or the UNAVAILABLE cause."""
    hashes = exit_check.config_hashes(paths)
    verdict = str(payload["verdict"])
    refused = payload["refused_code"]
    if verdict == tickets.VERDICT_UNAVAILABLE:
        refused = payload["cause"]
    record = structuring_text.journal_record(
        now=now, tool=TOOL, trade_id=str(payload["trade_id"]), verdict=verdict, text=text,
        criteria_sha256=criteria_sha256, desk_toml_sha256=hashes["desk_toml"],
        positions_csv_sha256=hashes["positions_csv"], action=str(payload["action"]), structure=payload["structure"],
        refused_code=refused)
    structuring_text.append_journal_record(paths, record)
    return paths.relative(paths.structuring_journal)


def quiet_text(lines: Sequence[str], now: datetime.datetime) -> structuring_text.Rendered:
    """A line the tool decides before the engine runs (config, criteria), closed with the footer at ``now``."""
    return structuring_text.finish(structuring_text.Writer(), lines, now, TOOL)


def ticket_document(payload: Mapping[str, Any], version: str, now: datetime.datetime,
                    market: Optional[quote_cache.MarketData]) -> str:
    """The ``tickets/<ticket_id>.json`` text: the standard envelope (as roll_analysis.py and propose.py write their
    reports) around the ticket without the run's own keys (:data:`FILE_EXCLUDED`)."""
    body = {key: payload[key] for key in PAYLOAD_KEYS if key not in FILE_EXCLUDED}
    return contract.json_dumps(contract.envelope(
        TOOL, version, body, source=None if market is None else quote_cache.source_label(market.cfg),
        delay_min=None if market is None else market.cfg.market_data.feed_delay_min, generated_at=now,
        status=contract.STATUS_OK)) + "\n"


def emit(args: Any, version: str, now: datetime.datetime, payload: Dict[str, Any], text: str, exit_code: int,
         market: Optional[quote_cache.MarketData]) -> int:
    payload["exit_code"] = exit_code
    if args.json:
        status = contract.STATUS_OK if exit_code == contract.EXIT_QUIET else contract.status_for(exit_code)
        source = None if market is None else quote_cache.source_label(market.cfg)
        delay = None if market is None else market.cfg.market_data.feed_delay_min
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, source=source, delay_min=delay,
                                                    generated_at=now, status=status)))
    else:
        print(text)
    return exit_code


def apply_rendered(payload: Dict[str, Any], rendered: structuring_text.Rendered) -> None:
    payload["message"] = rendered.text
    payload["emitted"] = rendered.document
    payload["lint"] = rendered.lint_dict()
    if not rendered.ok:
        payload["withheld"] = {"codes": list(rendered.codes), "text": rendered.attempted}


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    validate(parser, args)
    now = (args.now or state.utc_now()).replace(microsecond=0)  # the ticket id's resolution (review L3-3)
    payload = empty_payload(args.trade, args.action, now)
    market: Optional[quote_cache.MarketData] = None
    try:
        try:
            paths = desk_paths.for_root(args.desk_root)
        except desk_paths.DeskPathsError as exc:
            raise ToolError("desk_root", str(exc)) from None
        changes = exit_check.config_changes(paths)
        payload["config_changed"] = changes
        blocking = [change for change in changes if change["name"] in BLOCKING_CHANGES]
        rulebook = [change for change in changes if change["name"] not in BLOCKING_CHANGES]
        if blocking:
            payload.update(verdict=tickets.VERDICT_UNAVAILABLE, cause=CAUSE_CONFIG_UNACKNOWLEDGED,
                           reason=REASON_CONFIG_UNACKNOWLEDGED)
            w = structuring_text.Writer()
            rendered = structuring_text.finish(w, [structuring_text.config_unacknowledged_line(w, changes)], now, TOOL)
            return printed(args, version, now, payload, rendered, paths, exit_check.config_hashes(paths)["structuring_toml"],
                           None)
        try:
            criteria = structuring_config.load_structuring_config_if_present(paths.structuring_toml)
        except structuring_config.StructuringConfigError as exc:
            raise ToolError("structuring_invalid", str(exc)) from None
        if criteria is None:
            payload.update(verdict=tickets.VERDICT_UNAVAILABLE, cause=CAUSE_CRITERIA_MISSING,
                           reason=REASON_CRITERIA_MISSING)
            w = structuring_text.Writer()
            lines = [structuring_text.config_changed_line(w, rulebook)] if rulebook else []
            rendered = structuring_text.finish(w, lines + [structuring_text.criteria_missing_line(w)], now, TOOL)
            return printed(args, version, now, payload, rendered, paths, None, None)
        market = open_market(args, now, version)
        try:
            ticket = tickets.run_ticket(market, args.trade, args.action, criteria, new_expiration=args.new_expiration,
                                        new_strike=args.new_strike)
        except tickets.TicketUsageError as exc:
            parser.error(str(exc))
        except structuring.StructuringError as exc:
            raise ToolError(exc.code, exc.message) from None
        except calendar_events.CalendarError as exc:
            raise ToolError("calendar_invalid", str(exc)) from None
        payload.update(ticket.as_dict())
        rendered = tickets.render_ticket(ticket, rulebook)
        return printed(args, version, now, payload, rendered, paths, criteria.sha256, market,
                       ticket.ticket_id if ticket.verdict == tickets.VERDICT_TICKET else None)
    except ToolError as exc:
        if exc.exit_code == contract.EXIT_UNAVAILABLE:
            journal_refusal(args, now, exc)
        return fail(args, version, now, exc.error, exc.message, exc.exit_code)
    except structuring_text.ReportExists as exc:
        return fail(args, version, now, ERROR_TICKET_EXISTS, str(exc))
    except (OSError, state.LockBusy, state.LockUnsupported) as exc:
        return fail(args, version, now, "io_error", "cannot read or write the desk files: %s" % exc)


def journal_refusal(args: Any, now: datetime.datetime, exc: "ToolError") -> None:
    """S5: an UNAVAILABLE printed through the error envelope (market data refused, an expired holiday table) is a
    printed text too: one journal record with its code (review L3-6). A journal that cannot be written does not turn
    the refusal into a second failure."""
    try:
        paths = desk_paths.for_root(args.desk_root)
        structuring_text.append_unavailable_record(paths, now=now, tool=TOOL, trade_id=args.trade, code=exc.error,
                                                   text="UNAVAILABLE: %s" % exc.message,
                                                   hashes=exit_check.config_hashes(paths), action=args.action)
    except (desk_paths.DeskPathsError, OSError, state.LockBusy, state.LockUnsupported):
        pass


def printed(args: Any, version: str, now: datetime.datetime, payload: Dict[str, Any],
            rendered: structuring_text.Rendered, paths: desk_paths.DeskPaths, criteria_sha256: Optional[str],
            market: Optional[quote_cache.MarketData], ticket_id: Optional[str] = None) -> int:
    """Write the ticket pair (a TICKET only), record the text and print it; a text the lint refuses prints only the
    WITHHELD line (exit 1, nothing written or recorded)."""
    apply_rendered(payload, rendered)
    if not rendered.ok:
        return emit(args, version, now, payload, rendered.text, contract.EXIT_ERROR, market)
    exit_code = exit_code_of(str(payload["verdict"]))
    if ticket_id is not None and exit_code == contract.EXIT_QUIET:
        files = structuring_text.write_report(paths, paths.tickets_dir, ticket_id,
                                              ticket_document(payload, version, now, market),
                                              structuring_text.report_markdown("Ticket %s" % ticket_id, rendered.text))
        payload["files"] = {"json": paths.relative(files.json_path), "md": paths.relative(files.md_path),
                            "written": files.written}
    payload["journal"] = journal(paths, now=now, payload=payload, text=rendered.text, criteria_sha256=criteria_sha256)
    return emit(args, version, now, payload, rendered.text, exit_code, market)


if __name__ == "__main__":
    sys.exit(main())
