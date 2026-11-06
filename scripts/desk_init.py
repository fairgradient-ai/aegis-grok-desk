#!/usr/bin/env python3
"""desk_init.py: render desk.toml once from the owner's interview answers, the ex-dividend intake and the structuring
intake.

Usage::

    python3 scripts/desk_init.py --answers PATH.json [--replace --owner-tapped] [--json] [--schema] [--timeout S]
        [--desk-root DIR] [--offline] [--now ISO-8601-UTC]

The Bot collects the answers in chat and writes them as JSON; this tool never prompts (PR 2d decision D2-13, override
O9). It creates the desk root when it is missing and renders, byte for byte:

* ``desk.toml`` (when the answers hold ``desk``, with ``owner``, ``market_data`` and ``rates``), in the key order of
  ``tests/fixtures/desk/desk.toml``: decimals are JSON strings (``"250000.00"``) printed verbatim, strings are printed
  as JSON strings, the cutoff ``HH:MM`` as ``HH:MM:00`` and the as-of date bare; the constants ``timezone =
  "America/New_York"``, ``vendor = "tradier"``, ``feed_delay_min = 15``, ``quote_ttl_min = 5``, ``grace_min = 5``,
  ``pin_band = 0.25`` and ``[levels] order_shape_enabled = false`` are not asked; ``token_file`` follows
  ``token_env`` when given, and the optional ``levels.penny_classes`` / ``penny_all_classes`` lists follow
  ``order_shape_enabled`` when non-empty. The host must be ``sandbox``: ``production`` is exit 1 ``production_host``
  (outside the guarantee before the market-data proxy). The text is validated in memory by
  ``desk_config.parse_desk_toml_text`` before anything is written (a failure is exit 1 ``config_invalid``).
* ``research/calendar.toml`` (when the answers hold ``ex_dividends``): one ``[symbols.<SYM>]`` table per symbol, in
  sorted order, with ``ex_dividend = { date = ..., amount = ..., status = "confirmed" | "estimated", source =
  "owner-entered", checked_utc = <--now> }`` or ``{ status = "none", source = <the owner's>, checked_utc = <--now> }``,
  validated by ``calendar_events.parse_calendar_text`` (exit 1 ``calendar_invalid``). The bootstrap runs the tool a
  second time for this intake once ``positions.csv`` is validated, with answers that hold no ``desk``.
* ``structuring.toml`` (when the answers hold ``structuring``, which may stand alone like ``ex_dividends``; PR 3b
  decision S3 over designer C's D2): the owner's structuring criteria, rendered by :func:`render_structuring_toml`
  with the two header comment lines of :data:`STRUCTURING_HEADER`, then ``schema_version = 1``, then the sections of
  ``structuring_config.SECTIONS`` with their keys in ``structuring_config.SECTION_KEYS`` order. Decimals are JSON
  strings matching :data:`DECIMAL_RE`, printed verbatim; bands are two-element arrays (integers for ``dte``, decimal
  strings otherwise); booleans are JSON booleans; every key is required and no default is injected (the Bot writes the
  documented defaults the owner accepted, and ``disclosure_acknowledged`` only after the owner acknowledges the
  disclosure the interview shows last). A shape error is ``answers_invalid``; the text is then validated by
  ``structuring_config.parse_structuring_toml_text`` before anything is written (a failure is exit 1
  ``structuring_invalid`` with the reader's reason).

An existing ``desk.toml``, ``research/calendar.toml`` or ``structuring.toml`` is refused (exit 1 ``desk_toml_exists``,
``calendar_exists`` or ``structuring_exists``) unless both ``--replace`` and ``--owner-tapped`` are given
(``--owner-tapped`` is the signature of
the owner's tap on the Ask-first card that runs it; routines never run it; one flag without the other is a usage
error). A desk root that is this checkout or lies inside it is refused (exit 1 ``desk_root``: the desk's files never
dirty the release checkout). Everything is checked before the first write, so a refusal or an invalid answer writes
nothing; each file is
written with ``state.atomic_write_text``. desk_init records no acknowledged hash (positions.csv and the adopted
rulebook do not exist yet at that step of the bootstrap): it prints each written file's SHA-256 and, when desk.toml is
written, ``acknowledge with ack.py config --owner-tapped once positions.csv and the adopted rulebook are in place``;
when structuring.toml is written without desk.toml, :data:`ACKNOWLEDGE_STRUCTURING`. It reads no quote and opens no
connection.

Exit 0 written; 1 invalid answers (``answers_invalid``), a refusal, a round-trip failure or an I/O error
(``io_error``); 2 usage. ``--schema`` also carries ``input.answers``, the answers JSON described.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="desk_init")

import datetime  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple  # noqa: E402

from aegis_desk import calendar_events, desk_config, state, structuring_config, version  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "desk_init"
DESCRIPTION = ("render desk.toml once from the owner's interview answers (the ex-dividend intake into "
               "research/calendar.toml and the structuring intake into structuring.toml), byte for byte, validated "
               "before it is written; owner-authority")
NETWORK_ACCESS = "none"
SCHEMA_VERSION = 1
DECIMAL_RE = re.compile(r"^-?[0-9]+([.][0-9]+)?\Z")
CUTOFF_RE = re.compile(r"^([01][0-9]|2[0-3]):[0-5][0-9]\Z")
DATE_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
BARE_KEY_RE = re.compile(r"^[A-Za-z0-9_-]+\Z")
SANDBOX = "sandbox"
PRODUCTION = "production"
OWNER_ENTERED = "owner-entered"
DATED_STATUSES: Tuple[str, ...] = (calendar_events.STATUS_CONFIRMED, calendar_events.STATUS_ESTIMATED)
ACKNOWLEDGE_HINT = ("acknowledge with ack.py config --owner-tapped once positions.csv and the adopted rulebook are in "
                    "place")
ACKNOWLEDGE_STRUCTURING = ("acknowledge with ack.py config --owner-tapped: the structuring tools show nothing until "
                           "structuring.toml is acknowledged")
OWNER_TAP_MESSAGE = ("--replace requires --owner-tapped (and --owner-tapped applies only with --replace): the signature "
                     "of the owner's tap on the Ask-first card that runs it (routines never run it)")
DESK_HEADER: Tuple[str, ...] = (
    "# desk.toml: rendered once by scripts/desk_init.py from the owner's interview answers; a hand edit changes its",
    "# hash and book_check posts CONFIG CHANGED until the owner acknowledges it.",
)
CALENDAR_HEADER: Tuple[str, ...] = (
    "# research/calendar.toml: the owner's ex-dividend intake, rendered by scripts/desk_init.py; every entry is dated",
    "# and sourced, and unknown is never zero. Earnings, settlement values and closures are added by hand.",
)
STRUCTURING_HEADER: Tuple[str, ...] = (
    "# structuring.toml: rendered by scripts/desk_init.py from the owner's structuring interview; a hand edit changes its",
    "# hash, and the structuring tools show nothing until the owner acknowledges it (ack.py config --owner-tapped).",
)
CONSTANTS: Dict[str, str] = {"timezone": json.dumps(desk_config.TIMEZONE), "vendor": json.dumps(desk_config.VENDOR),
                             "feed_delay_min": "15", "quote_ttl_min": "5", "grace_min": "5", "pin_band": "0.25",
                             "order_shape_enabled": "false"}
"""The rendered values the interview does not ask."""
TOP_KEYS: Tuple[str, ...] = ("schema_version", "desk", "owner", "market_data", "rates", "levels", "ex_dividends",
                             "structuring")
DESK_KEYS: Tuple[str, ...] = ("engagement", "cadence_profile", "chatty_or_quiet")
OWNER_KEYS: Tuple[str, ...] = ("broker_exercise_cutoff_et", "approval_level", "declared_equity", "attention_thresholds",
                               "disclosures_acknowledged", "accounts")
ATTENTION_KEYS: Tuple[str, ...] = ("pl_day_pct", "pl_trade_abs")
ACCOUNT_KEYS: Tuple[str, ...] = ("name", "type")
MARKET_DATA_KEYS: Tuple[str, ...] = ("host", "token_env", "token_file")
RATES_KEYS: Tuple[str, ...] = ("risk_free_rate", "source", "as_of")
LEVELS_KEYS: Tuple[str, ...] = ("penny_classes", "penny_all_classes")
DATED_DIVIDEND_KEYS: Tuple[str, ...] = ("date", "amount", "status")
NONE_DIVIDEND_KEYS: Tuple[str, ...] = ("status", "source")
STRUCTURING_KEYS: Tuple[str, ...] = ("disclosure_acknowledged",) + structuring_config.SECTIONS[1:]
"""The keys of the ``structuring`` answers: the disclosure id, then one object per section after ``[disclosure]``."""
STRUCTURING_KINDS: Dict[str, str] = {
    "acknowledged": "string", "max_spread_pct": "decimal", "max_candidates_shown": "integer", "enabled": "boolean",
    "dte": "integer_band", "abs_delta": "decimal_band", "min_credit": "decimal", "credit_only": "boolean",
    "strike_floor": "string", "earnings": "string", "ex_dividend": "string", "objective": "string",
    "floor_pct": "decimal_band", "stress_drop_pct": "decimal", "cap_pct": "decimal_band", "max_net_debit": "decimal",
}
"""How each structuring.toml key is written from its answer (every key of ``structuring_config.SECTION_KEYS``)."""
PAYLOAD_KEYS: Tuple[str, ...] = ("written", "replaced", "desk_toml", "calendar_toml", "structuring_toml", "acknowledge")
_FILE_SHAPE: Dict[str, Any] = {"path": "desk-root relative path", "sha256": "hex of the written bytes",
                               "bytes": "integer", "text": "string (the file exactly)"}
OUTPUT: Dict[str, Any] = {
    "written": ["desk-root relative paths written (desk.toml, research/calendar.toml, structuring.toml)"],
    "replaced": ["the written paths that existed before (--replace --owner-tapped)"],
    "desk_toml": dict(_FILE_SHAPE, note="null when the answers hold no desk"),
    "calendar_toml": dict(_FILE_SHAPE, symbols=["symbol"], note="null when the answers hold no ex_dividends"),
    "structuring_toml": dict(_FILE_SHAPE, note="null when the answers hold no structuring"),
    "acknowledge": "string | null (the next step for the owner once desk.toml or structuring.toml is written)",
}
_DTE_WORDS = "[lo, hi] of integers: calendar days, 7 <= lo < hi <= 400 and hi - lo <= 90"
_EVENT_WORDS = " | ".join(structuring_config.EVENT_POLICIES)
_FLOOR_WORDS = "[lo, hi] of decimal strings: percent of the share price below it, 0 <= lo < hi <= 50"
STRUCTURING_SHAPE: Dict[str, Any] = {
    "disclosure_acknowledged": "%s (given only after the owner acknowledges the disclosure the interview shows "
                               "last)" % structuring_config.DISCLOSURE_ID,
    "general": {"max_spread_pct": "decimal string: percent, 0 < x <= 25",
                "max_candidates_shown": "integer 1-10 (the text only; JSON lists every candidate)"},
    "covered_call": {"enabled": "boolean", "dte": _DTE_WORDS,
                     "abs_delta": "[lo, hi] of decimal strings: 0.05 <= lo < hi <= 0.60, at most 2 places",
                     "min_credit": "decimal string >= 0.01 (per share, at the bid)",
                     "strike_floor": " | ".join(structuring_config.STRIKE_FLOORS), "earnings": _EVENT_WORDS,
                     "ex_dividend": _EVENT_WORDS, "objective": structuring_config.OBJECTIVE_EXTRINSIC_PER_DAY},
    "roll": {"enabled": "boolean", "dte": _DTE_WORDS,
             "abs_delta": "[lo, hi] of decimal strings: 0.05 <= lo < hi <= 0.60, at most 2 places",
             "min_credit": "decimal string >= 0.01 (per share, the new call's bid)",
             "credit_only": "boolean (a net debit fails it; a zero net passes)",
             "strike_floor": " | ".join(structuring_config.STRIKE_FLOORS), "earnings": _EVENT_WORDS,
             "ex_dividend": _EVENT_WORDS, "objective": structuring_config.OBJECTIVE_EXTRINSIC_PER_DAY},
    "protective_put": {"enabled": "boolean", "dte": _DTE_WORDS, "floor_pct": _FLOOR_WORDS,
                       "stress_drop_pct": "decimal string: percent, above the floor band's hi and at most 90",
                       "objective": structuring_config.OBJECTIVE_STRESS_COST_PER_DAY},
    "collar": {"enabled": "boolean", "dte": _DTE_WORDS, "floor_pct": _FLOOR_WORDS,
               "cap_pct": "[lo, hi] of decimal strings: percent of the share price above it, 0 < lo < hi <= 100",
               "max_net_debit": "decimal string >= 0 (per share; 0.00 is a zero cost or a credit)",
               "strike_floor": " | ".join(structuring_config.STRIKE_FLOORS), "earnings": _EVENT_WORDS,
               "ex_dividend": _EVENT_WORDS, "objective": structuring_config.OBJECTIVE_CAP_WITHIN_BUDGET},
}
"""``ANSWERS_SHAPE["structuring"]``: every key required, decimals as JSON strings, no default injected."""
ANSWERS_SHAPE: Dict[str, Any] = {
    "schema_version": "1 (desk, owner, market_data and rates go together; give them, ex_dividends, structuring, or "
                      "any of these together)",
    "desk": {"engagement": "string", "cadence_profile": " | ".join(desk_config.CADENCE_PROFILES),
             "chatty_or_quiet": " | ".join(desk_config.CHATTY_OR_QUIET)},
    "owner": {"broker_exercise_cutoff_et": "HH:MM (America/New_York)", "approval_level": "integer 0-5 (self-attested)",
              "declared_equity": "decimal string, for example \"250000.00\"",
              "attention_thresholds": {"pl_day_pct": "decimal string", "pl_trade_abs": "decimal string"},
              "disclosures_acknowledged": ["string"],
              "accounts": [{"name": "string", "type": " | ".join(desk_config.ACCOUNT_TYPES)}]},
    "market_data": {"host": "sandbox (production is refused in this release)",
                    "token_env": "string (the environment variable that holds the sandbox token)",
                    "token_file": "string (optional; a file with no group or other permission bits)"},
    "rates": {"risk_free_rate": "decimal string", "source": "string", "as_of": "YYYY-MM-DD"},
    "levels": {"penny_classes": ["OCC root (optional)"], "penny_all_classes": ["OCC root (optional)"]},
    "ex_dividends": {"SYMBOL": "{date YYYY-MM-DD, amount decimal string, status confirmed | estimated} or "
                               "{status none, source string} (optional; written to research/calendar.toml)"},
    "structuring": STRUCTURING_SHAPE,
}


class ToolError(Exception):
    """A failure the tool reports with an error envelope (exit 1): ``code`` and ``message``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def answers_error(message: str) -> ToolError:
    return ToolError("answers_invalid", message)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = contract.ReadOnlyParser(tool=TOOL, tool_version=version, description=DESCRIPTION,
                                     desk_root_must_exist=False)
    parser.add_argument("--answers", metavar="PATH.json", default=None,
                        help="the owner's interview answers (JSON; --schema describes them); required")
    parser.add_argument("--replace", action="store_true",
                        help="replace an existing desk.toml or research/calendar.toml (needs --owner-tapped)")
    parser.add_argument("--owner-tapped", action="store_true",
                        help="the signature of the owner's tap on the Ask-first card (needs --replace)")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any) -> None:
    if args.answers is None:
        parser.error("--answers PATH.json is required (--schema describes the answers)")
    if args.replace != args.owner_tapped:
        parser.error(OWNER_TAP_MESSAGE)


def fail(args: Any, version: str, now: datetime.datetime, code: str, message: str) -> int:
    if args.json:
        print(contract.json_dumps(contract.error_envelope(TOOL, version, code, message, contract.STATUS_ERROR,
                                                          generated_at=now)))
    else:
        print("error: %s" % message)
    return contract.EXIT_ERROR


# --------------------------------------------------------------------------------------------
# Reading the answers
# --------------------------------------------------------------------------------------------


def table(value: Any, where: str, keys: Sequence[str], required: Sequence[str]) -> Dict[str, Any]:
    """``value`` as an object with only ``keys`` and every one of ``required`` (``answers_invalid`` otherwise)."""
    if not isinstance(value, dict):
        raise answers_error("%s must be an object" % where)
    unknown = sorted(set(value) - set(keys))
    if unknown:
        raise answers_error("%s has the unknown key %s (expected %s)" % (where, unknown[0], ", ".join(keys)))
    missing = [key for key in required if key not in value]
    if missing:
        raise answers_error("%s is missing %s" % (where, missing[0]))
    return value


def text_value(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise answers_error("%s must be a non-empty string" % where)
    return json.dumps(value)


def choice(value: Any, where: str, allowed: Sequence[str]) -> str:
    if value not in allowed:
        raise answers_error("%s must be one of %s" % (where, ", ".join(allowed)))
    return json.dumps(value)


def decimal_value(value: Any, where: str) -> str:
    """A decimal answer, printed verbatim: a JSON string matching :data:`DECIMAL_RE` (a JSON number could not keep
    the digits the owner gave)."""
    if not isinstance(value, str) or not DECIMAL_RE.match(value):
        raise answers_error("%s must be a decimal string such as \"250000.00\" (no exponent, no JSON number)" % where)
    return value


def date_value(value: Any, where: str) -> str:
    if isinstance(value, str) and DATE_RE.match(value):
        try:
            datetime.date.fromisoformat(value)
            return value
        except ValueError:
            pass
    raise answers_error("%s must be a date YYYY-MM-DD" % where)


def render_desk_toml(answers: Mapping[str, Any]) -> str:
    """The ``desk.toml`` text of the answers (module notes), ending with one newline."""
    for key in ("owner", "market_data", "rates"):
        if key not in answers:
            raise answers_error("answers with desk also need %s" % key)
    desk = table(answers["desk"], "desk", DESK_KEYS, DESK_KEYS)
    owner = table(answers["owner"], "owner", OWNER_KEYS, OWNER_KEYS)
    market = table(answers["market_data"], "market_data", MARKET_DATA_KEYS, ("host", "token_env"))
    rates = table(answers["rates"], "rates", RATES_KEYS, RATES_KEYS)
    levels = table(answers.get("levels", {}), "levels", LEVELS_KEYS, ())
    cutoff = owner["broker_exercise_cutoff_et"]
    if not isinstance(cutoff, str) or not CUTOFF_RE.match(cutoff):
        raise answers_error("owner.broker_exercise_cutoff_et must be HH:MM on the 24-hour clock (America/New_York)")
    level = owner["approval_level"]
    low, high = desk_config.APPROVAL_LEVEL_RANGE
    if isinstance(level, bool) or not isinstance(level, int) or not low <= level <= high:
        raise answers_error("owner.approval_level must be an integer %d-%d" % (low, high))
    attention = table(owner["attention_thresholds"], "owner.attention_thresholds", ATTENTION_KEYS, ATTENTION_KEYS)
    disclosures = owner["disclosures_acknowledged"]
    if not isinstance(disclosures, list):
        raise answers_error("owner.disclosures_acknowledged must be a list of strings")
    accounts = owner["accounts"]
    if not isinstance(accounts, list) or not accounts:
        raise answers_error("owner.accounts must list at least one account")
    if market["host"] == PRODUCTION:
        raise ToolError("production_host", "market_data.host production is refused: this release renders the sandbox "
                                           "host only (production is outside the guarantee before the market-data proxy)")
    lines: List[str] = list(DESK_HEADER)
    lines += ["[desk]", "timezone = %s" % CONSTANTS["timezone"],
              "engagement = %s" % text_value(desk["engagement"], "desk.engagement"),
              "cadence_profile = %s" % choice(desk["cadence_profile"], "desk.cadence_profile",
                                              desk_config.CADENCE_PROFILES),
              "chatty_or_quiet = %s" % choice(desk["chatty_or_quiet"], "desk.chatty_or_quiet",
                                              desk_config.CHATTY_OR_QUIET), ""]
    lines += ["[owner]", "broker_exercise_cutoff_et = %s:00" % cutoff, "approval_level = %d" % level,
              "declared_equity = %s" % decimal_value(owner["declared_equity"], "owner.declared_equity"),
              "attention_thresholds = { pl_day_pct = %s, pl_trade_abs = %s }" % (
                  decimal_value(attention["pl_day_pct"], "owner.attention_thresholds.pl_day_pct"),
                  decimal_value(attention["pl_trade_abs"], "owner.attention_thresholds.pl_trade_abs"))]
    if disclosures:
        lines.append("disclosures_acknowledged = [")
        lines += ["  %s," % text_value(item, "owner.disclosures_acknowledged[%d]" % index)
                  for index, item in enumerate(disclosures)]
        lines.append("]")
    else:
        lines.append("disclosures_acknowledged = []")
    for index, raw in enumerate(accounts):
        account = table(raw, "owner.accounts[%d]" % index, ACCOUNT_KEYS, ACCOUNT_KEYS)
        lines += ["", "[[owner.accounts]]", "name = %s" % text_value(account["name"], "owner.accounts[%d].name" % index),
                  "type = %s" % choice(account["type"], "owner.accounts[%d].type" % index, desk_config.ACCOUNT_TYPES)]
    lines += ["", "[market_data]", "vendor = %s" % CONSTANTS["vendor"],
              "host = %s" % choice(market["host"], "market_data.host", (SANDBOX,)),
              "token_env = %s" % text_value(market["token_env"], "market_data.token_env")]
    if "token_file" in market:
        lines.append("token_file = %s" % text_value(market["token_file"], "market_data.token_file"))
    lines += ["%s = %s" % (key, CONSTANTS[key]) for key in ("feed_delay_min", "quote_ttl_min", "grace_min", "pin_band")]
    lines += ["", "[rates]", "risk_free_rate = %s" % decimal_value(rates["risk_free_rate"], "rates.risk_free_rate"),
              "source = %s" % text_value(rates["source"], "rates.source"),
              "as_of = %s" % date_value(rates["as_of"], "rates.as_of")]
    lines += ["", "[levels]", "order_shape_enabled = %s" % CONSTANTS["order_shape_enabled"]]
    for key in LEVELS_KEYS:
        roots = levels.get(key, [])
        if not isinstance(roots, list) or not all(isinstance(root, str) for root in roots):
            raise answers_error("levels.%s must be a list of OCC roots" % key)
        if roots:
            lines.append("%s = [%s]" % (key, ", ".join(json.dumps(root) for root in roots)))
    return "\n".join(lines) + "\n"


def render_calendar_toml(intake: Any, now: datetime.datetime) -> Tuple[str, List[str]]:
    """``(text, symbols)``: the ``research/calendar.toml`` of the ex-dividend intake (module notes)."""
    if not isinstance(intake, dict) or not intake:
        raise answers_error("ex_dividends must be an object of SYMBOL: entry with at least one symbol")
    checked = now.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines: List[str] = list(CALENDAR_HEADER) + ["schema_version = %d" % calendar_events.SCHEMA_VERSION]
    symbols = sorted(intake)
    for symbol in symbols:
        where = "ex_dividends.%s" % symbol
        if not isinstance(symbol, str) or not calendar_events.SYMBOL_RE.match(symbol):
            raise answers_error("%s: a symbol is written upper-case as the vendor quotes it (%s)"
                                % (where, calendar_events.SYMBOL_RE.pattern))
        entry = intake[symbol]
        status = entry.get("status") if isinstance(entry, dict) else None
        if status in DATED_STATUSES:
            table(entry, where, DATED_DIVIDEND_KEYS, DATED_DIVIDEND_KEYS)
            body = "date = %s, amount = %s, status = %s, source = %s, checked_utc = %s" % (
                date_value(entry["date"], where + ".date"), decimal_value(entry["amount"], where + ".amount"),
                json.dumps(status), json.dumps(OWNER_ENTERED), checked)
        elif status == calendar_events.STATUS_NONE:
            table(entry, where, NONE_DIVIDEND_KEYS, NONE_DIVIDEND_KEYS)
            body = "status = %s, source = %s, checked_utc = %s" % (
                json.dumps(status), text_value(entry["source"], where + ".source"), checked)
        else:
            raise answers_error("%s.status must be confirmed, estimated (with date and amount) or none (with source)"
                                % where)
        key = symbol if BARE_KEY_RE.match(symbol) else json.dumps(symbol)
        lines += ["", "[symbols.%s]" % key, "ex_dividend = { %s }" % body]
    return "\n".join(lines) + "\n", symbols


def structuring_value(kind: str, value: Any, where: str) -> str:
    """One structuring answer as its TOML text (``answers_invalid`` for a wrong JSON shape; the values themselves
    are the reader's to judge)."""
    if kind == "string":
        if not isinstance(value, str):
            raise answers_error("%s must be a JSON string" % where)
        return json.dumps(value)
    if kind == "boolean":
        if not isinstance(value, bool):
            raise answers_error("%s must be a JSON boolean (true or false)" % where)
        return "true" if value else "false"
    if kind == "integer":
        if isinstance(value, bool) or not isinstance(value, int):
            raise answers_error("%s must be a JSON integer" % where)
        return str(value)
    if kind == "decimal":
        return decimal_value(value, where)
    if not isinstance(value, list) or len(value) != 2:
        raise answers_error("%s must be a two-element array [lo, hi]" % where)
    item_kind = "integer" if kind == "integer_band" else "decimal"
    return "[%s]" % ", ".join(structuring_value(item_kind, item, "%s[%d]" % (where, index))
                              for index, item in enumerate(value))


def render_structuring_toml(intake: Any) -> str:
    """The ``structuring.toml`` text of the structuring answers (module notes), ending with one newline."""
    answers = table(intake, "structuring", STRUCTURING_KEYS, STRUCTURING_KEYS)
    lines: List[str] = list(STRUCTURING_HEADER) + ["schema_version = %d" % structuring_config.SCHEMA_VERSION]
    for section in structuring_config.SECTIONS:
        keys = structuring_config.SECTION_KEYS[section]
        if section == structuring_config.SECTION_DISCLOSURE:
            values: Dict[str, Any] = {"acknowledged": answers["disclosure_acknowledged"]}
            places = {"acknowledged": "structuring.disclosure_acknowledged"}
        else:
            values = table(answers[section], "structuring.%s" % section, keys, keys)
            places = {key: "structuring.%s.%s" % (section, key) for key in keys}
        lines += ["", "[%s]" % section]
        lines += ["%s = %s" % (key, structuring_value(STRUCTURING_KINDS[key], values[key], places[key])) for key in keys]
    return "\n".join(lines) + "\n"


def read_answers(path: str) -> Dict[str, Any]:
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise answers_error("cannot read the answers file %s: %s" % (path, exc.strerror or exc)) from None
    try:
        answers = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise answers_error("the answers file %s is not JSON: %s" % (path, exc)) from None
    answers = table(answers, "the answers", TOP_KEYS, ("schema_version",))
    if answers["schema_version"] != SCHEMA_VERSION or isinstance(answers["schema_version"], bool):
        raise answers_error("the answers' schema_version must be %d" % SCHEMA_VERSION)
    if "desk" not in answers and "ex_dividends" not in answers and "structuring" not in answers:
        raise answers_error("the answers render nothing: give desk (with owner, market_data and rates), ex_dividends, "
                            "structuring or several of them")
    for key in ("owner", "market_data", "rates", "levels"):
        if key in answers and "desk" not in answers:
            raise answers_error("%s goes with desk" % key)
    return answers


# --------------------------------------------------------------------------------------------
# The command
# --------------------------------------------------------------------------------------------


def relative(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace(os.sep, "/")


def file_entry(root: str, path: str, text: str) -> Dict[str, Any]:
    data = text.encode("utf-8")
    return {"path": relative(root, path), "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "text": text}


def run(args: Any, now: datetime.datetime) -> Tuple[Dict[str, Any], List[str]]:
    """Validate everything, refuse what exists, then write: ``(payload, human lines)``."""
    root = os.path.abspath(args.desk_root)
    checkout = os.path.realpath(version.repository_root())
    resolved = os.path.realpath(root)
    if resolved == checkout or resolved.startswith(checkout + os.sep):
        raise ToolError("desk_root", "the desk root %s is this checkout or inside it: the desk keeps its files outside "
                                     "the release checkout (for example /workspace/aegis-desk)" % root)
    answers = read_answers(args.answers)
    if os.path.exists(root) and not os.path.isdir(root):
        raise ToolError("desk_root", "the desk root %s exists and is not a directory" % root)
    planned: List[Tuple[str, str]] = []
    desk_text: Optional[str] = None
    calendar_text: Optional[str] = None
    structuring_text: Optional[str] = None
    symbols: List[str] = []
    desk_toml = os.path.join(root, "desk.toml")  # desk_paths.for_root's layout; the root may not exist yet
    calendar_toml = os.path.join(root, "research", "calendar.toml")
    structuring_toml = os.path.join(root, structuring_config.FILE_NAME)
    if "desk" in answers:
        desk_text = render_desk_toml(answers)
        try:
            desk_config.parse_desk_toml_text(desk_text, path=desk_toml)
        except desk_config.DeskConfigError as exc:
            raise ToolError("config_invalid", "the rendered desk.toml does not load: %s" % exc.reason) from None
        planned.append((desk_toml, desk_text))
    if "ex_dividends" in answers:
        calendar_text, symbols = render_calendar_toml(answers["ex_dividends"], now)
        try:
            calendar_events.parse_calendar_text(calendar_text, path=calendar_toml)
        except calendar_events.CalendarError as exc:
            raise ToolError("calendar_invalid", "the rendered research/calendar.toml does not load: %s" % exc) from None
        planned.append((calendar_toml, calendar_text))
    if "structuring" in answers:
        structuring_text = render_structuring_toml(answers["structuring"])
        try:
            structuring_config.parse_structuring_toml_text(structuring_text, path=structuring_toml)
        except structuring_config.StructuringConfigError as exc:
            raise ToolError("structuring_invalid",
                            "the rendered structuring.toml does not load: %s" % exc.reason) from None
        planned.append((structuring_toml, structuring_text))
    replaced = [path for path, _text in planned if os.path.exists(path)]
    if replaced and not (args.replace and args.owner_tapped):
        first = replaced[0]
        code = {desk_toml: "desk_toml_exists", calendar_toml: "calendar_exists",
                structuring_toml: "structuring_exists"}[first]
        raise ToolError(code, "%s already exists at %s: replacing it needs --replace --owner-tapped"
                        % (relative(root, first), root))
    try:
        os.makedirs(root, exist_ok=True)
        for path, text in planned:
            state.atomic_write_text(path, text)
    except OSError as exc:
        raise ToolError("io_error", "cannot write under the desk root %s: %s" % (root, exc.strerror or exc)) from None
    payload: Dict[str, Any] = {
        "written": [relative(root, path) for path, _text in planned],
        "replaced": [relative(root, path) for path in replaced],
        "desk_toml": file_entry(root, desk_toml, desk_text) if desk_text is not None else None,
        "calendar_toml": (dict(file_entry(root, calendar_toml, calendar_text), symbols=symbols)
                          if calendar_text is not None else None),
        "structuring_toml": (file_entry(root, structuring_toml, structuring_text)
                             if structuring_text is not None else None),
        "acknowledge": (ACKNOWLEDGE_HINT if desk_text is not None
                        else ACKNOWLEDGE_STRUCTURING if structuring_text is not None else None),
    }
    lines = ["%s %s (%d bytes, sha256 %s)" % ("replaced" if entry["path"] in payload["replaced"] else "wrote",
                                            entry["path"], entry["bytes"], entry["sha256"])
             for entry in (payload["desk_toml"], payload["calendar_toml"], payload["structuring_toml"])
             if entry is not None]
    if payload["acknowledge"]:
        lines.append(payload["acknowledge"])
    return payload, lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        schema = parser.schema(OUTPUT, NETWORK_ACCESS)
        schema["input"] = {"answers": ANSWERS_SHAPE}
        print(contract.json_dumps(schema))
        return contract.EXIT_QUIET
    validate(parser, args)
    now = args.now or state.utc_now()
    try:
        payload, lines = run(args, now)
    except ToolError as exc:
        return fail(args, version, now, exc.code, exc.message)
    if args.json:
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, generated_at=now)))
    else:
        print("\n".join(lines))
    return contract.EXIT_QUIET


if __name__ == "__main__":
    sys.exit(main())
