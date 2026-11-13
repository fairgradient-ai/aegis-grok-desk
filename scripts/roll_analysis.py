#!/usr/bin/env python3
"""roll_analysis.py: the replacement candidates for the short call of a covered call, on the owner's request only.

Usage::

    python3 scripts/roll_analysis.py --trade AG-YYYYMMDD-NN [--fixture-set NAME] [--json] [--schema] [--timeout S]
        [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

    python3 scripts/roll_analysis.py --trade AG-20261020-01 --desk-root /workspace/aegis-desk

PR 3b (the addendum S1-S17 over designer Q's D3-D16 and designer C's D3-D7). The Aegis Structuring Desk runs it only
when the owner's own message asks; it is refused as a routine step (``aegis_desk.routines.REFUSED_STEP_TOOLS``) and
never fires an alert. For the open short call of a ``covered_call`` group in ``positions.csv`` it prices every call of
the later standard monthly expirations inside ``[roll] dte`` of the owner's ``structuring.toml``, labels each against
the owner's criteria and ranks them by the declared objective (``aegis_desk.structuring.run_roll_analysis``). Any other
structure, an index underlying or ``[roll] enabled = false`` is NOT_APPLICABLE with its reason (the roll analysis of
long puts, short puts and collars is planned for Stage 2).

The order of designer C's D3.6, each step before the next:

1. ``exit_check.config_changes``: an unacknowledged ``desk.toml``, ``positions.csv`` or ``structuring.toml`` prints only
   the ``config_unacknowledged`` line (exit 20); an unacknowledged adopted rulebook alone prints its CONFIG CHANGED line
   first and the analysis continues.
2. The criteria file (``aegis_desk.structuring_config``): missing is ``criteria_missing`` (exit 20), invalid is
   ``structuring_invalid`` (exit 1).
3. ``desk.toml`` (the acknowledged one; ``owner.approval_level`` is the owner's self-attested level), ``positions.csv``
   and the calendar, then the approval-level check (``aegis_desk.approval``) of the roll's legs in the S7 order (close
   the held call, then open the new one) on the account's holdings: a refusal prints the REFUSED line (exit 20) before
   any market data is requested.
4. Market data through ``aegis_desk.quote_cache`` only: one group-scoped quotes batch, the underlying's expirations, the
   in-window chains (at most four; ``requests`` reports them and the desk-wide ``budget_left``). The best candidate is
   checked again on its own legs (``structuring.approval_check``) before its text is printed.

The text is built only through ``aegis_desk.structuring_text`` (every number emitted, no OCC symbol, no cell the owner
typed) and ends with the structuring footer; it is linted strictly before anything prints (``structuring_text.finish``),
and a text that fails is replaced by the WITHHELD line (exit 1 ``lint_failed``; ``--json`` carries the withheld text).
BEST and NONE_QUALIFIES (a complete evaluation; BD6) write ``reports/rolls/<trade_id>-<YYYYMMDDTHHMMSSZ>.json`` and
``.md`` (the directory created at the first write under the desk lock; identical bytes are a no-op, different bytes are
exit 1 ``ticket_exists`` and nothing is written); NOT_APPLICABLE writes no report. Every run that printed a text (exit 0
or 20, an UNAVAILABLE printed through the error envelope included) appends one record to ``journal/structuring.jsonl``;
exit 1 and 2 write nothing. ``--now`` (or the clock) is read to the whole second, the report stem's resolution.

Exit codes (S5): 0 BEST, NONE_QUALIFIES or NOT_APPLICABLE; 20 UNAVAILABLE (the market data the answer needs:
``market_closed``, ``group_quarantined``, ``positions_quarantined``, ``spot_unusable``, ``held_leg_unusable``,
``expirations_not_served``, ``chain_not_served``, ``no_candidate_priced``; ``criteria_missing``;
``config_unacknowledged``; market data refused: ``no_credential``, ``live_host``, ``credential_file_mode``,
``holiday_table_expired``), REFUSED (``approval_level``, or ``intermediate_exposure``); 1
``unknown_trade``, ``desk_root``, ``config_invalid``, ``positions_invalid``, ``calendar_invalid``,
``structuring_invalid``, ``io_error``, ``ticket_exists``, ``lint_failed``; 2 usage. It never exits 10: it fires
nothing. ``--json`` payload: as ``--schema`` describes.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="roll_analysis")

import datetime  # noqa: E402
import sys  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: E402

import exit_check  # noqa: E402  (config_changes: the CONFIG CHANGED comparison the structuring tools share; eod_summary's precedent)
from aegis_desk import (  # noqa: E402
    approval,
    desk_config,
    desk_paths,
    market_clock,
    positions_csv,
    quote_cache,
    state,
    structuring,
    structuring_config,
    structuring_text,
)
from aegis_desk.version import release_version  # noqa: E402

TOOL = structuring.TOOL_ROLL_ANALYSIS
KIND = structuring.KIND_REPLACEMENT_CALL
SECTION = "roll"
WHAT = "roll analysis"
DESCRIPTION = ("on the owner's request only: the replacement candidates for the short call of a covered call, priced "
               "from that underlying's own chains, labelled against the owner's structuring.toml and ranked by its "
               "declared objective; names one best candidate when one passes every criterion; never in a routine")
NETWORK_ACCESS = ("GET https://data.alpaca.markets/v2/stocks/quotes/latest, /v1beta1/options/quotes/latest and "
                  "/v1beta1/options/snapshots/<UNDERLYING>, and "
                  "https://paper-api.alpaca.markets/v2/options/contracts (expirations and chain listings) only, "
                  "through the quote cache (no new fetch once a run has made 40 requests; at most 4 chains a run); "
                  "none with --offline")
VERDICT_REFUSED = "REFUSED"
CAUSE_CRITERIA_MISSING = "criteria_missing"
CAUSE_CONFIG_UNACKNOWLEDGED = "config_unacknowledged"
CAUSE_APPROVAL_LEVEL = "approval_level"
CAUSE_INTERMEDIATE_EXPOSURE = "intermediate_exposure"
STAGE_BEFORE_MARKET_DATA = "before_market_data"
STAGE_BEST_CANDIDATE = "best_candidate"
QUIET_VERDICTS: Tuple[str, ...] = (structuring.VERDICT_BEST, structuring.VERDICT_NONE_QUALIFIES,
                                   structuring.VERDICT_NOT_APPLICABLE, structuring.VERDICT_NOTHING_TO_PROPOSE)
"""Complete answers: exit 0. UNAVAILABLE and REFUSED are exit 20."""
REPORT_VERDICTS: Tuple[str, ...] = (structuring.VERDICT_BEST, structuring.VERDICT_NONE_QUALIFIES)
"""BD6: only a complete evaluation writes a report; NOT_APPLICABLE and NOTHING_TO_PROPOSE write only their
journal record."""
VERDICTS: Tuple[str, ...] = QUIET_VERDICTS + (structuring.VERDICT_UNAVAILABLE, VERDICT_REFUSED)
BLOCKING_CONFIG: Tuple[str, ...] = ("desk_toml", "positions_csv", "structuring_toml")
"""The acknowledged files that block a structuring text (designer C's D3.6); an unacknowledged rulebook alone does not."""
REASON_CRITERIA_MISSING = "no structuring.toml: the Structuring Desk shows nothing until you set your criteria"
REASON_CONFIG_UNACKNOWLEDGED = "desk.toml, positions.csv or structuring.toml differ from what you acknowledged"
EXTRA_KEYS: Tuple[str, ...] = ("approval", "config_changed", "message", "emitted", "lint", "withheld", "files", "journal")
PAYLOAD_KEYS: Tuple[str, ...] = tuple(structuring.ANSWER_KEYS) + EXTRA_KEYS
REPORT_OMITTED_KEYS: Tuple[str, ...] = ("requests", "files", "journal")
"""Run telemetry the report file leaves out, so a rerun at the same instant (served from the cache) writes the same
bytes."""
HIDDEN_ON_REFUSAL: Tuple[str, ...] = ("best", "tie_break", "after")
"""What a REFUSED answer never carries: the desk shows nothing above the owner's level."""

_LEG_SHAPE: Dict[str, Any] = {
    "occ": "OCC symbol (JSON only; the text never prints one)", "root": "string", "right": "CALL | PUT",
    "strike": "decimal string", "expiration": "YYYY-MM-DD", "last_trading_day": "YYYY-MM-DD | null",
    "dte_calendar": "integer | null", "dte_trading": "integer | null", "bid": "decimal string | null",
    "ask": "decimal string | null", "mid": "decimal string | null", "spread_pct": "decimal string | null",
    "grade": "A-F | null", "quote_status": "string | null", "quote_age_min": "integer | null",
    "as_of": "ISO-8601 UTC | null", "tau_years": "float | null", "iv": "float | null (iv.implied_vol at the mid)",
    "delta": "float | null (greeks.european_greeks)", "abs_delta": "decimal string | null (2 dp, half-even)",
    "greeks": "{delta, gamma, theta, vega, rho: float} | null",
    "unpriced": "UNPRICED code | null (market_closed, quote_missing, ..., iv_<status>)",
}
_AFTER_SHAPE: Dict[str, Any] = {
    "group_delta": "float | null (share-equivalents)", "delta_change": "float | null",
    "max_loss": "decimal string | null", "max_profit": "decimal string | null (unbounded)",
    "breakeven": "decimal string | null", "breakevens": ["decimal string"], "unbounded_up": "boolean",
    "realized_on_close": "decimal string | null (the held leg closed at its conservative side)",
}
_CANDIDATE_SHAPE: Dict[str, Any] = {
    "rank": "integer (1-based table position)", "id": "string (the new leg's OCC symbol; a collar '<put>+<call>')",
    "kind": " | ".join(structuring.KINDS), "expiration": "YYYY-MM-DD", "contracts": "integer",
    "status": " | ".join(structuring.TABLE_ORDER), "score": "decimal string | null (the objective's value)",
    "reasons": [{"key": "structuring.toml key | unpriced", "code": "string", "value": "the value the desk read",
                 "threshold": "the owner's band, window or minimum"}],
    "accepted": [{"key": "earnings | ex_dividend", "code": "string", "by": "#earnings-ok | #assign-ok | your "
                  "structuring.toml", "value": "any", "threshold": "any"}],
    "lost_because": "{by, ...} | null (the status or the first rule that separated it from the best)",
    "american_caveat": "boolean (early exercise matters for this leg; the text names it)",
    "extrinsic_bid": "decimal string | null", "net_credit": "decimal string | null (per share: new bid - held ask)",
    "net_credit_total": "decimal string | null", "credit_total": "decimal string | null",
    "floor_pct": "decimal string | null", "cap_pct": "decimal string | null",
    "covered_per_share": "decimal string | null", "covered_total": "decimal string | null",
    "net_debit": "decimal string | null", "net_debit_total": "decimal string | null",
    "cost_total": "decimal string | null", "legs": [dict(_LEG_SHAPE)], "after": "{...} | null (as after)",
}
OUTPUT: Dict[str, Any] = {
    "tool": "roll_analysis", "kind": "replacement_call", "trade_id": "AG-YYYYMMDD-NN",
    "group_id": "string | null", "structure": "the group's structure | null", "structure_requested": "null",
    "section": "roll (the structuring.toml section the candidates are judged under)",
    "underlying": "string | null", "account": "the positions.csv account (JSON only; the text never prints it) | null",
    "account_type": "cash | margin | ira | null (desk.toml [[owner.accounts]])",
    "verdict": " | ".join(VERDICTS),
    "cause": ("null | the UNAVAILABLE cause (%s, criteria_missing, config_unacknowledged) | the NOT_APPLICABLE code "
              "(structure, index_underlying, disabled, no_open_legs, no_expiration_in_window) | the REFUSED code "
              "(approval_level, "
              "intermediate_exposure)" % ", ".join(cause for cause in structuring.CAUSES
                         if cause not in ('replacement_not_listed',))),
    "reason": "string | null (the desk's own words for a verdict that prices nothing)",
    "objective": {"id": "extrinsic_per_day", "words": "string", "formula": "string", "unit": "string",
                  "direction": "higher | lower"},
    "criteria": "the structuring.toml echo {schema_version, disclosure, general, covered_call, roll, protective_put, "
                "collar, sha256} | null (no criteria file)",
    "spot": "{symbol, bid, ask, mid, grade, as_of} | null",
    "held": ("{row_number, occ, root, right, side, contracts, strike, expiration, last_trading_day, bid, ask, mid, "
             "spread_pct, grade, quote_status, quote_age_min, as_of, iv, delta, entry_price_per_share, "
             "remaining_cost_basis, close_price, close_side, note, realized} | null (the short call every candidate "
             "closes, at its fresh ask)"),
    "before": "{group_delta, max_loss, max_profit, breakeven, unbounded_up} | null (the group-scoped book)",
    "shares": "decimal string | null (the group's open shares)", "contracts": "integer | null",
    "stress": "null", "expirations": "{listed_in_window: [YYYY-MM-DD], evaluated: [YYYY-MM-DD], not_evaluated: "
                                     "[{expiration, reason}]} | null",
    "candidates": [dict(_CANDIDATE_SHAPE)],
    "best": "candidate id | null (rank 1 when ELIGIBLE)",
    "tie_break": "string | null (the rule that separated the best from an ELIGIBLE runner-up of the same score)",
    "passed": "integer | null", "failed": "integer | null (candidates that failed a criterion)",
    "unpriced": "integer | null (candidates never priced, so never judged against the criteria)",
    "failure_counts": "{structuring.toml key: n} | null (the failing candidates; the unpriced apart)",
    "outside_bands": "null", "after": dict(_AFTER_SHAPE),
    "model_note": "string (European Black-Scholes-Merton; early exercise not modelled)",
    "requests": "{made, chains, per_run_max_requests, per_run_max_chains, budget_left} | null (no market data opened)",
    "warnings": ["string"],
    "approval": ("{stage: before_market_data | best_candidate, owner_level, required_level, ok, refusal_step, "
                 "exposure_step, steps: [{index, kind, level, words}]} | null (no check ran: the answer prices "
                 "nothing)"),
    "config_changed": [{"name": " | ".join(exit_check.CONFIG_NAMES), "old": "acknowledged sha256 | null",
                        "new": "current sha256 | null (missing)"}],
    "message": "string: the text exactly as printed (the WITHHELD line and the footer when the lint fails)",
    "emitted": {"emitted_numbers": ["string"], "rule_ids": ["string (none)"],
                "identifiers": ["YYYY-MM-DD | AG-... | sha <8 hex>"], "clocks": ["HH:MM UTC"],
                "as_of": "ISO-8601 UTC (the footer's instant: the oldest stamp of the data used, else the run's)",
                "footer": "structuring (alert_lint re-lints the text with the structuring footer)"},
    "lint": {"ok": "boolean (false: exit 1 lint_failed)", "problems": ["string"], "numbers_checked": "integer"},
    "withheld": "{codes: [string], text: string (the text that failed)} | null",
    "files": ("{json: reports/rolls/<trade_id>-<YYYYMMDDTHHMMSSZ>.json, md: ....md, written: boolean (false: the same "
              "bytes were already there)} | null (written only for BEST and NONE_QUALIFIES, a complete evaluation)"),
    "journal": "journal/structuring.jsonl | null (one record per printed text: exit 0 or 20)",
}


class ToolError(Exception):
    """A failure reported with an error envelope: ``code``, ``message`` and the exit code (1 or 20)."""

    def __init__(self, code: str, message: str, exit_code: int = contract.EXIT_ERROR) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.exit_code = exit_code


@dataclass
class Outcome:
    """What a run decided before anything is printed or written."""

    verdict: str
    cause: Optional[str]
    payload: Dict[str, Any]
    rendered: structuring_text.Rendered
    paths: desk_paths.DeskPaths
    criteria: Optional[structuring_config.StructuringConfig] = None
    market: Optional[quote_cache.MarketData] = None
    answer: Optional[structuring.Answer] = None
    hashes: Dict[str, Optional[str]] = field(default_factory=dict)

    @property
    def exit_code(self) -> int:
        if not self.rendered.ok:
            return contract.EXIT_ERROR
        return contract.EXIT_QUIET if self.verdict in QUIET_VERDICTS else contract.EXIT_UNAVAILABLE


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--trade", metavar="AG-YYYYMMDD-NN", default=None,
                        help="the trade id of a covered call group in positions.csv (required)")
    parser.add_argument("--fixture-set", metavar="NAME", default=None,
                        help="recorded fixture set under <desk-root>/fixtures/alpaca/ (default regular; --offline only)")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any) -> None:
    if args.trade is None:
        parser.error("--trade AG-YYYYMMDD-NN is required")
    args.trade = args.trade.strip()
    if not positions_csv.TRADE_ID_RE.match(args.trade):
        parser.error("--trade must be a trade id such as AG-20261020-01, got %r" % args.trade)
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str, exit_code: int) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, contract.status_for(exit_code),
                                                          generated_at=now)))
    elif exit_code == contract.EXIT_UNAVAILABLE:
        print("UNAVAILABLE: %s" % message)
    else:
        print("error: %s" % message)
    return exit_code


# --------------------------------------------------------------------------------------------
# The steps of designer C's D3.6
# --------------------------------------------------------------------------------------------


def open_paths(desk_root: str) -> desk_paths.DeskPaths:
    try:
        return desk_paths.for_root(desk_root)
    except desk_paths.DeskPathsError as exc:
        raise ToolError("desk_root", str(exc)) from None


def load_criteria(paths: desk_paths.DeskPaths) -> Optional[structuring_config.StructuringConfig]:
    try:
        return structuring_config.load_structuring_config_if_present(paths.structuring_toml)
    except structuring_config.StructuringConfigError as exc:
        raise ToolError("structuring_invalid", str(exc)) from None
    except OSError as exc:
        raise ToolError("io_error", "cannot read structuring.toml: %s" % exc) from None


def open_market(args: Any, now: datetime.datetime, version: str) -> quote_cache.MarketData:
    """``quote_cache.open_market_data`` (desk.toml, the clock, the client; no request) with the tools' error mapping."""
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
    except OSError as exc:
        raise ToolError("io_error", "cannot prepare the desk runtime directories: %s" % exc) from None


def approval_dict(result: approval.ApprovalResult, stage: str) -> Dict[str, Any]:
    return {"stage": stage, "owner_level": result.owner_level, "required_level": result.required_level,
            "ok": result.ok, "refusal_step": None if result.refusal is None else result.refusal.index,
            "exposure_step": result.exposure_step,
            "steps": [{"index": step.index, "kind": step.kind, "level": step.level, "words": step.words}
                      for step in result.steps]}


def roll_legs(group: Any) -> Optional[List[approval.Leg]]:
    """The roll's legs in the S7 order (close the held short call, then open the new one), or ``None`` when the group
    holds its short call on other than exactly one row (the analysis answers that itself)."""
    rows = [row for row in group.open_legs if row.is_option and row.right == "CALL" and row.is_short]
    if len(rows) != 1:
        return None
    quantity = abs(rows[0].open_quantity)
    if quantity != quantity.to_integral_value() or quantity <= 0:
        return None
    contracts = int(quantity)
    return [approval.Leg(approval.EFFECT_CLOSE, approval.SIDE_SHORT, approval.INSTRUMENT_CALL, contracts),
            approval.Leg(approval.EFFECT_OPEN, approval.SIDE_SHORT, approval.INSTRUMENT_CALL, contracts)]


def precheck(target: structuring.Target, criteria: structuring_config.StructuringConfig,
             owner_level: int) -> Optional[approval.ApprovalResult]:
    """The approval-level check before any market data (step 3), or ``None`` when the answer prices nothing anyway
    (a quarantined group, NOT_APPLICABLE) and the analysis says so itself."""
    group = target.group
    if target.unusable or target.others_quarantined or group is None or structuring.roll_applicability(group, criteria) is not None:
        return None
    legs = roll_legs(group)
    if legs is None:
        return None
    try:
        holdings = approval.holdings_of(target.positions.rows, group.account, group.underlying)
        return approval.check_legs(holdings, legs, owner_level)
    except ValueError as exc:
        raise ToolError(structuring.ERROR_POSITIONS_INVALID, str(exc)) from None


def skeleton(trade_id: str, verdict: str, cause: Optional[str], reason: Optional[str],
             criteria: Optional[structuring_config.StructuringConfig],
             market: Optional[quote_cache.MarketData] = None,
             target: Optional[structuring.Target] = None) -> Dict[str, Any]:
    """The payload body of an answer decided before the analysis ran (every key of ``structuring.ANSWER_KEYS``)."""
    body: Dict[str, Any] = {key: None for key in structuring.ANSWER_KEYS}
    body.update(tool=TOOL, kind=KIND, trade_id=trade_id, section=SECTION, verdict=verdict, cause=cause, reason=reason,
                objective=structuring.objective_dict(structuring.kind_objective(KIND)),
                criteria=None if criteria is None else criteria.as_dict(), candidates=[],
                model_note=structuring.MODEL_NOTE, warnings=[],
                requests=None if market is None else structuring.requests_block(market))
    group = None if target is None else target.group
    if group is not None:
        body.update(group_id=group.group_id, structure=group.structure, underlying=group.underlying,
                    account=group.account)
        if market is not None:
            body["account_type"] = next((entry.type for entry in market.cfg.owner.accounts
                                         if entry.name == group.account), None)
    return body


def owner_texts(market: Optional[quote_cache.MarketData], target: Optional[structuring.Target]) -> List[str]:
    """The cells the owner typed that a structuring text must never print: every account name of desk.toml and of
    positions.csv (``structuring.owner_texts``, the set all three tools screen)."""
    if target is not None:
        return structuring.owner_texts(target)
    return sorted({entry.name for entry in market.cfg.owner.accounts if entry.name}) if market is not None else []


def analyse(args: Any, now: datetime.datetime, version: str) -> Outcome:
    """Decide the answer (module notes) without printing or writing anything."""
    trade_id = args.trade
    paths = open_paths(args.desk_root)
    try:
        changes = exit_check.config_changes(paths)
        hashes = exit_check.config_hashes(paths)
    except OSError as exc:
        raise ToolError("io_error", "cannot read the desk files: %s" % exc) from None
    w = structuring_text.Writer()
    blocking = [change for change in changes if change["name"] in BLOCKING_CONFIG]
    if blocking:
        lines = [structuring_text.config_unacknowledged_line(w, changes)]
        body = skeleton(trade_id, structuring.VERDICT_UNAVAILABLE, CAUSE_CONFIG_UNACKNOWLEDGED,
                        REASON_CONFIG_UNACKNOWLEDGED, None)
        return finish(w, lines, now, body, paths, changes, None, hashes=hashes)
    lines = [structuring_text.config_changed_line(w, changes)] if changes else []
    criteria = load_criteria(paths)
    if criteria is None:
        lines.append(structuring_text.criteria_missing_line(w))
        body = skeleton(trade_id, structuring.VERDICT_UNAVAILABLE, CAUSE_CRITERIA_MISSING, REASON_CRITERIA_MISSING,
                        None)
        return finish(w, lines, now, body, paths, changes, None, hashes=hashes)
    market = open_market(args, now, version)
    owner_level = market.cfg.owner.approval_level
    try:
        target = structuring.load_target(market, trade_id)
        checked = precheck(target, criteria, owner_level)
        if checked is not None and (not checked.ok or checked.exposure_step is not None):
            return refused(w, lines, now, paths, changes, criteria, market, target, checked, hashes)
        answer = structuring.run_roll_analysis(market, trade_id, criteria)
    except structuring.StructuringError as exc:
        raise ToolError(exc.code, exc.message) from None
    except market_clock.TableExpiredError as exc:
        raise ToolError("holiday_table_expired", "holiday table expired: %s" % exc, contract.EXIT_UNAVAILABLE) from None
    except OSError as exc:
        raise ToolError("io_error", "cannot read the desk files or use the cache: %s" % exc) from None
    approval_record = None if checked is None else approval_dict(checked, STAGE_BEFORE_MARKET_DATA)
    best = answer.best
    verdict, cause = answer.verdict, answer.cause
    body = answer.as_dict()
    if best is not None:
        try:
            result = structuring.approval_check(answer, best, owner_level)
        except ValueError as exc:
            raise ToolError(structuring.ERROR_POSITIONS_INVALID, str(exc)) from None
        approval_record = approval_dict(result, STAGE_BEST_CANDIDATE)
        if not result.ok:
            verdict, cause = VERDICT_REFUSED, CAUSE_APPROVAL_LEVEL
            lines.append(structuring_text.refused_line(w, answer, result))
        elif result.exposure_step is not None:
            verdict, cause = VERDICT_REFUSED, CAUSE_INTERMEDIATE_EXPOSURE
            group = answer.group_context
            assert group is not None
            lines.append(structuring_text.exposure_refused_line(
                w, trade_id, group.underlying, group.structure, result.exposure_step,
                result.steps[result.exposure_step - 1].after.uncovered_calls))
        if verdict == VERDICT_REFUSED:
            body.update({key: None for key in HIDDEN_ON_REFUSAL}, candidates=[], verdict=verdict, cause=cause,
                        reason=refusal_reason(result))
    if verdict != VERDICT_REFUSED:
        lines.extend(structuring_text.roll_analysis_lines(w, answer))
    body["approval"] = approval_record
    as_of = structuring_text.data_as_of(answer) or now
    return finish(w, lines, as_of, body, paths, changes, criteria, market=market, answer=answer,
                  texts=owner_texts(market, answer.target), hashes=hashes)


def refusal_reason(result: approval.ApprovalResult) -> str:
    step = result.refusal if result.refusal is not None else result.steps[(result.exposure_step or 1) - 1]
    return "%s needs level %d by the desk's convention; your self-attested level is %d" % (
        step.words, step.level, result.owner_level)


def refused(w: structuring_text.Writer, lines: List[str], now: datetime.datetime, paths: desk_paths.DeskPaths,
            changes: List[Dict[str, Optional[str]]], criteria: structuring_config.StructuringConfig,
            market: quote_cache.MarketData, target: structuring.Target, result: approval.ApprovalResult,
            hashes: Dict[str, Optional[str]]) -> Outcome:
    """The REFUSED answer of the check before any market data (step 3)."""
    group = target.group
    assert group is not None
    if not result.ok:
        cause = CAUSE_APPROVAL_LEVEL
        lines.append(approval.refusal_line(result, w.trade_id(target.trade_id), group.underlying,
                                           structuring_text.structure_words(group.structure), w.emitted))
    else:
        cause = CAUSE_INTERMEDIATE_EXPOSURE
        assert result.exposure_step is not None
        lines.append(structuring_text.exposure_refused_line(
            w, target.trade_id, group.underlying, group.structure, result.exposure_step,
            result.steps[result.exposure_step - 1].after.uncovered_calls))
    body = skeleton(target.trade_id, VERDICT_REFUSED, cause, refusal_reason(result), criteria, market, target)
    body["approval"] = approval_dict(result, STAGE_BEFORE_MARKET_DATA)
    return finish(w, lines, now, body, paths, changes, criteria, market=market, texts=owner_texts(market, target),
                  hashes=hashes)


def finish(w: structuring_text.Writer, lines: Sequence[str], as_of: datetime.datetime, body: Dict[str, Any],
           paths: desk_paths.DeskPaths, changes: List[Dict[str, Optional[str]]],
           criteria: Optional[structuring_config.StructuringConfig], *, market: Optional[quote_cache.MarketData] = None,
           answer: Optional[structuring.Answer] = None, texts: Sequence[str] = (),
           hashes: Optional[Dict[str, Optional[str]]] = None) -> Outcome:
    """Close the text with the structuring footer, lint it (``structuring_text.finish``) and complete the payload."""
    rendered = structuring_text.finish(w, lines, as_of, TOOL, owner_texts=texts)
    body.setdefault("approval", None)
    body.update(config_changed=[dict(change) for change in changes], message=rendered.text,
                emitted=rendered.document, lint=rendered.lint_dict(),
                withheld=None if rendered.ok else {"codes": list(rendered.codes), "text": rendered.attempted},
                files=None, journal=None)
    return Outcome(verdict=body["verdict"], cause=body["cause"], payload=body, rendered=rendered, paths=paths,
                   criteria=criteria, market=market, answer=answer, hashes=dict(hashes or {}))


# --------------------------------------------------------------------------------------------
# The record: report files (S9, exit 0 only) and the journal (designer C's D7.5, exit 0 or 20)
# --------------------------------------------------------------------------------------------


def envelope(outcome: Outcome, version: str, now: datetime.datetime, payload: Dict[str, Any]) -> Dict[str, Any]:
    market = outcome.market
    code = outcome.exit_code
    return contract.envelope(TOOL, version, payload,
                             source=None if market is None else quote_cache.source_label(market.cfg),
                             delay_min=None if market is None else market.cfg.market_data.feed_delay_min,
                             generated_at=now, status=contract.STATUS_OK if code == 0 else contract.status_for(code))


def report_title(trade_id: str, now: datetime.datetime) -> str:
    return "%s %s (%s)" % (trade_id, WHAT, structuring_text.stamp(now))


def write_files(outcome: Outcome, version: str, now: datetime.datetime) -> None:
    paths = outcome.paths
    trade_id = outcome.payload["trade_id"]
    report = {key: value for key, value in outcome.payload.items() if key not in REPORT_OMITTED_KEYS}
    json_text = contract.json_dumps(envelope(outcome, version, now, report)) + "\n"
    md_text = structuring_text.report_markdown(report_title(trade_id, now), outcome.rendered.text)
    files = structuring_text.write_report(paths, paths.rolls_dir, structuring_text.roll_stem(trade_id, now), json_text,
                                          md_text)
    outcome.payload["files"] = {"json": paths.relative(files.json_path), "md": paths.relative(files.md_path),
                                "written": files.written}


def append_journal(outcome: Outcome, now: datetime.datetime) -> None:
    paths = outcome.paths
    payload = outcome.payload
    if outcome.market is None:
        desk_paths.ensure_runtime_dirs(paths)  # the lock and journal/ live in the runtime directories
    criteria_sha = outcome.criteria.sha256 if outcome.criteria is not None else outcome.hashes.get("structuring_toml")
    record = structuring_text.journal_record(
        now=now, tool=TOOL, trade_id=payload["trade_id"], verdict=payload["verdict"], text=outcome.rendered.text,
        criteria_sha256=criteria_sha, desk_toml_sha256=outcome.hashes.get("desk_toml"),
        positions_csv_sha256=outcome.hashes.get("positions_csv"), structure=payload["structure_requested"],
        best_candidate=payload["best"], candidates_passed=payload["passed"], candidates_failed=payload["failed"],
        refused_code=None if outcome.exit_code == 0 else payload["cause"])
    structuring_text.append_journal_record(paths, record)
    payload["journal"] = paths.relative(paths.structuring_journal)


def record(outcome: Outcome, version: str, now: datetime.datetime) -> None:
    """Write the report files (exit 0) and append the journal record (exit 0 or 20); nothing for exit 1."""
    if outcome.exit_code == contract.EXIT_ERROR:
        return
    try:
        if outcome.exit_code == contract.EXIT_QUIET and outcome.verdict in REPORT_VERDICTS:
            write_files(outcome, version, now)
        append_journal(outcome, now)
    except structuring_text.ReportExists as exc:
        raise ToolError(exc.code, str(exc)) from None
    except (OSError, state.LockBusy, state.LockUnsupported) as exc:
        raise ToolError("io_error", "cannot write the report or the journal: %s" % exc) from None


def journal_refusal(args: Any, now: datetime.datetime, exc: ToolError) -> None:
    """S5: an UNAVAILABLE printed through the error envelope (market data refused, an expired holiday table) is a
    printed text too: one journal record with its code (review L3-6). A journal that cannot be written does not turn
    the refusal into a second failure."""
    try:
        paths = desk_paths.for_root(args.desk_root)
        structuring_text.append_unavailable_record(paths, now=now, tool=TOOL, trade_id=args.trade, code=exc.code,
                                                   text="UNAVAILABLE: %s" % exc.message,
                                                   hashes=exit_check.config_hashes(paths), structure=None)
    except (desk_paths.DeskPathsError, OSError, state.LockBusy, state.LockUnsupported):
        pass


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    validate(parser, args)
    now = (args.now or state.utc_now()).replace(microsecond=0)  # the stems' resolution (review L3-3)
    try:
        outcome = analyse(args, now, version)
        record(outcome, version, now)
    except ToolError as exc:
        if exc.exit_code == contract.EXIT_UNAVAILABLE:
            journal_refusal(args, now, exc)
        return fail(args, version, now, exc.code, exc.message, exc.exit_code)
    if args.json:
        print(contract.json_dumps(envelope(outcome, version, now, outcome.payload)))
    else:
        print(outcome.rendered.text)
    return outcome.exit_code


if __name__ == "__main__":
    sys.exit(main())
