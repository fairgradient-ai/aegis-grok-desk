#!/usr/bin/env python3
"""opening_bell.py: one symbol's quote, the nearest standard expiration, the ATM implied volatility and the move it implies.

Usage::

    python3 scripts/opening_bell.py --symbol SPY [--rate 0.04] [--credentials-file PATH] [--fixture-set NAME] [--json]
        [--schema] [--timeout S] [--desk-root DIR] [--offline] [--now ISO-8601-UTC]
    python3 scripts/opening_bell.py --demo [--symbol SPY] [--rate 0.04] [--json]

    python3 scripts/opening_bell.py --symbol SPY --offline --desk-root tests/fixtures/desk --now 2026-11-03T19:50:00Z
    python3 scripts/opening_bell.py --demo

The README's "try it now" (PR 2d decision D2-14): a public market snapshot, not a signal; it touches no position and
no desk record. Three read-only calls through ``aegis_desk.alpaca_client`` (Alpaca's paper environment):
``quotes([SYMBOL])`` (one request on the IEX stock feed), ``option_expirations(SYMBOL, today)`` (the contract
listing) and ``option_chain(SYMBOL, expiration)`` (the expiration's contract listing, then its snapshots on the free
indicative options feed): four requests, more only when a listing pages.

* Live, no desk file is read: the paper key pair is ``alpaca_client.load_credential("APCA_API_KEY_ID",
  "APCA_API_SECRET_KEY", --credentials-file or the default /workspace/.aegis-secrets/alpaca.key)`` (both environment
  variables first, then the file: the key ID on line 1, the secret on line 2, with no group or other permission bits;
  neither is exit 20 ``no_credential``) and the client is the paper host's, on the indicative options feed.
* ``--offline`` (which needs ``--desk-root``) replays ``<desk root>/fixtures/alpaca/<--fixture-set, default
  regular>/`` with ``--now`` as the receipt instant, and writes nothing: no quote cache, no runtime directory.
* ``--demo`` is the README's three-command try-it: ``--offline --desk-root <this checkout>/tests/fixtures/desk --now
  2026-11-03T19:50:00Z`` (:data:`DEMO_DESK_ROOT`, :data:`DEMO_NOW`) on the regular fixture set, ``--symbol`` SPY unless
  given. The desk root is found from this script's own location, so the command works from any directory. It writes
  nothing and loads no credential, and the text opens with :data:`DEMO_LINE`, a plain line that says the data is
  synthetic sample data (it prints no number and passes the same strict lint). ``--demo`` with ``--desk-root``,
  ``--now``, ``--fixture-set`` or ``--credentials-file`` (a live run's credential) is a usage error; a redundant
  ``--offline`` is accepted.

The arithmetic: the mid ``S = (bid + ask) / 2`` (Decimal); the expiration is the first listed date at least 20
calendar days after the ET date of ``--now`` that is the settlement day of its month's third Friday
(``market_clock.settlement_day``: a holiday Friday moves to the Thursday); the ATM strike is the chain strike with a
call and a put that minimises ``(|K - S|, K)``; the call and put mids are inverted with ``iv.implied_vol`` at ``r =
--rate`` (default 0.04) and ``q = 0`` over ``tau = (market_clock.exercise_cutoff_utc(expiration) - the call's as-of)
/ units.SECONDS_PER_YEAR``, the as-of being ``min(bid_date, ask_date)``; the ATM IV is the mean of the two. The
expected move is stated both ways and labelled: straddle-based ``(call mid + put mid) x 0.80`` (Decimal, to the cent,
half-even) and one-sigma ``S x IV x sqrt(tau)`` (to the cent).

The text (on a ``--demo`` run, :data:`DEMO_LINE` first; D2-14's five lines, the fifth naming the source with
:data:`aegis_desk.data_source.WORDS`, so the indicative option quotes read as approximate; then the market-snapshot footer
``alert_lint.snapshot_footer_line``: a public quote shown before any rule exists, so the rules footer's "deterministic
rules you adopted" would not be true) is relayed
verbatim, so it is held to every post's rule: each
number in it is printed by a formatter that emits it (a price with at least two decimals and every decimal kept,
``alerts.fmt_pct``, ``fmt_days``, ``fmt_strike``, ``fmt_minutes``), the expiration date and the clock are recorded
as printed, the last line is ``alerts.fmt_footer`` at the oldest of the three quote stamps, and the whole text is
linted strictly (``alert_lint.check``) before anything prints: a failure is exit 1 ``lint_failed`` (the finding codes
named, the text not printed). ``--json`` carries ``payload.emitted`` (``alert_lint.EMITTED_DOCUMENT_KEYS``, the strict
document a relay is re-linted against) and ``payload.lint``; its money fields are the printed strings' Decimals
(``648.10``, never ``648.1``).

Exit 0; 20 when the market answer is unusable: no key pair (``no_credential``, ``credential_file_mode``), a request fails
(``request_failed``), the paper trading host refuses with 401/403 a key pair the market-data host has just answered
(``not_paper_key``: the contract listing lives on the paper host, which refuses a live key, so the owner deletes
both secrets and adds a new paper pair; a refusal of the first, market-data request stays ``request_failed``: a
mismatched or deleted pair), no two-sided quote (``no_quote``, ``no_two_sided_quote``), no qualifying expiration
(``no_expiration``), no strike with both a call and a put (``no_strike_pair``), a solver status that is not ``ok``
(``iv_failed``) or an option stamp more than 60 s from the underlying's (``stamps_unaligned``); 1 on a desk-root or
fixture-set error (``desk_root``, ``fixtures_missing``) or a text that fails the lint (``lint_failed``); 2 on usage. ``--json`` payload: as ``--schema`` describes.
"""

from __future__ import annotations

from aegis_desk import contract

contract.python_floor_check(tool="opening_bell")

import datetime  # noqa: E402
import math  # noqa: E402
import os  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation  # noqa: E402
from typing import Any, Dict, List, Optional, Sequence, Tuple  # noqa: E402

from aegis_desk import alert_lint, alerts, alpaca_client, data_source, desk_config, desk_paths, expiry, iv, market_clock, quote_cache, state, units  # noqa: E402
from aegis_desk.market_types import Quote  # noqa: E402
from aegis_desk.version import release_version  # noqa: E402

TOOL = "opening_bell"
DESCRIPTION = ("a public market snapshot of one symbol: its quote, the nearest standard expiration at least 20 calendar "
               "days out, the ATM implied volatility and the expected move to it, stated two ways; not a signal")
NETWORK_ACCESS = ("GET https://data.alpaca.markets/v2/stocks/quotes/latest, "
                  "https://paper-api.alpaca.markets/v2/options/contracts (the expirations, then one expiration's "
                  "contracts) and https://data.alpaca.markets/v1beta1/options/snapshots/<SYMBOL> only (at least four "
                  "requests, more when a listing pages); none with --offline")
KEY_ENV = desk_config.DEFAULT_KEY_ENV
SECRET_ENV = desk_config.DEFAULT_SECRET_ENV
CREDENTIALS_FILE = desk_config.DEFAULT_CREDENTIALS_FILE
HOST = desk_config.HOSTS[0]
SOURCE = data_source.label(desk_config.VENDOR, HOST)
OPTIONS_FEED = desk_config.DEFAULT_OPTIONS_FEED
SOURCE_WORDS = data_source.words_for_feed(OPTIONS_FEED)
FEED_DELAY_MIN = desk_config.INDICATIVE_MIN_FEED_DELAY_MIN
DEFAULT_RATE = "0.04"
DEFAULT_FIXTURE_SET = "regular"
DEMO_DESK_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tests", "fixtures", "desk")
"""``--demo``'s desk root: this checkout's main fixture desk, found from this script's location."""
DEMO_NOW = "2026-11-03T19:50:00Z"
"""``--demo``'s instant: the regular fixture set's (14:50 ET on Tuesday 2026-11-03)."""
DEMO_SYMBOL = "SPY"
DEMO_LINE = "Demo: synthetic sample data from the desk's test fixtures, not real market prices; nothing is written"
"""The first line of a ``--demo`` text: it prints no number, so the strict snapshot lint passes it unchanged."""
MIN_DAYS_OUT = 20
STRADDLE_FACTOR = Decimal("0.80")
ALIGNMENT_S = expiry.SPOT_ALIGNMENT_S
NOT_PAPER_KEY_MESSAGE = ("not a paper key: Alpaca's paper trading host refused the key pair (HTTP %s) after the "
                         "market-data host answered it; delete both secrets and add a new pair from the Paper Trading "
                         "dashboard")
"""``not_paper_key``: the contract listing is read on the paper trading host, which answers 401/403 to a live key
(Alpaca documents the environment-mismatch error; it publishes no separate paper/live test)."""
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.]{0,9}\Z")
RATE_RE = re.compile(r"^[0-9]+([.][0-9]+)?\Z")
CENT = Decimal("0.01")
LIVE_TRANSPORT: Any = None
"""The transport of a live client (``None``: the client's own HTTPS transport); the tests' seam."""
QUOTE_LINE = "%s %s mid (bid %s, ask %s), as of %s"
EXPIRATION_LINE = "nearest standard expiration at least %s out: %s (%s)"
IV_LINE = "ATM implied volatility at the %s strike: %s (call %s, put %s; mids %s and %s; r %s, q %s)"
MOVE_LINE = ("expected move to %s: straddle-based (call mid + put mid) x %s = %s; one-sigma S x IV x sqrt(tau) = %s")
SNAPSHOT_LINE = "%s, delayed %s, as of %s; a public market snapshot, not a signal"
PAYLOAD_KEYS: Tuple[str, ...] = ("symbol", "quote", "expiration", "dte_calendar", "strike", "call", "put", "atm_iv",
                                 "tau_years", "rate", "q", "expected_move", "requests_made", "fixture_set", "demo",
                                 "message", "emitted", "lint")
OUTPUT: Dict[str, Any] = {
    "symbol": "string",
    "quote": {"bid": "decimal string (at least two decimals, as printed)", "ask": "decimal string (as printed)",
              "mid": "decimal string (S, as printed)", "as_of": "ISO-8601 UTC (min of the bid and ask stamps)"},
    "expiration": "YYYY-MM-DD (the nearest standard expiration at least 20 calendar days out)",
    "dte_calendar": "integer (calendar days from the ET date of --now)",
    "strike": "decimal string (the ATM strike)",
    "call": {"mid": "decimal string (as printed)", "iv": "float (iv.implied_vol)"},
    "put": {"mid": "decimal string (as printed)", "iv": "float (iv.implied_vol)"},
    "atm_iv": "float (the mean of the call and put IV)",
    "tau_years": "float (seconds from the call's as-of to the exercise cutoff / 31,536,000)",
    "rate": "decimal string (--rate)",
    "q": "decimal string (0)",
    "expected_move": {"straddle": "decimal string ((call mid + put mid) x 0.80, to the cent)",
                      "one_sigma": "decimal string (S x IV x sqrt(tau), to the cent)"},
    "requests_made": "integer (requests sent: 4, more only when a contract listing or the snapshots page)",
    "fixture_set": "string | null (offline runs)",
    "demo": "boolean (true: --demo, the checkout's synthetic fixture desk; the text opens with the demo line)",
    "message": "string: the text exactly as printed (five lines, then the market-snapshot footer; a --demo run opens "
               "with the demo line)",
    "emitted": {"emitted_numbers": ["string"], "rule_ids": ["string (none)"], "identifiers": ["YYYY-MM-DD"],
                "clocks": ["HH:MM UTC"], "as_of": "ISO-8601 UTC (the footer's instant: the oldest quote stamp)",
                "footer": "snapshot (the market-snapshot footer; alert_lint re-lints the text with it)"},
    "lint": {"ok": "boolean (true: a failing lint is exit 1 lint_failed)", "problems": ["string"],
             "numbers_checked": "integer"},
}


class Unavailable(Exception):
    """The market answer cannot give the snapshot: exit 20 with ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class ToolError(Unavailable):
    """The tool cannot run (exit 1)."""


def user_agent(version: str) -> str:
    return "aegis-desk/%s %s (read-only market data)" % (version, TOOL)


class Parser(contract.ReadOnlyParser):
    """The shared parser; under ``--demo`` a redundant ``--offline`` is dropped before the shared checks (which would
    otherwise ask for a ``--desk-root`` the demo names itself)."""

    def parse_args(self, args: Optional[Sequence[str]] = None, namespace: Any = None) -> Any:
        argv = list(sys.argv[1:] if args is None else args)
        if "--demo" in argv:
            argv = [arg for arg in argv if arg != "--offline"]
        return super().parse_args(argv, namespace)


def build_parser(version: str) -> contract.ReadOnlyParser:
    parser = Parser(tool=TOOL, tool_version=version, description=DESCRIPTION)
    parser.add_argument("--symbol", metavar="SYMBOL", default=None,
                        help="the underlying, for example SPY (required, except with --demo: SPY)")
    parser.add_argument("--demo", action="store_true",
                        help="synthetic sample data: --offline on this checkout's tests/fixtures/desk at %s (the regular "
                             "fixture set); writes nothing; not with --desk-root, --now, --fixture-set or "
                             "--credentials-file" % DEMO_NOW)
    parser.add_argument("--rate", metavar="R", default=DEFAULT_RATE,
                        help="the risk-free rate the IV is solved at, a decimal in [0, 1) (default %s)" % DEFAULT_RATE)
    parser.add_argument("--credentials-file", metavar="PATH", default=None,
                        help="read the Alpaca paper key pair (key ID on line 1, secret on line 2) from this file when %s "
                             "and %s are not set (default %s; live runs only)" % (KEY_ENV, SECRET_ENV, CREDENTIALS_FILE))
    parser.add_argument("--fixture-set", metavar="NAME", default=None,
                        help="recorded fixture set under <desk-root>/fixtures/alpaca/ (default regular; --offline only)")
    return parser


def validate(parser: contract.ReadOnlyParser, args: Any) -> None:
    if args.demo:
        given = [flag for flag, present in (("--desk-root", args.desk_root_explicit), ("--now", args.now is not None),
                                            ("--fixture-set", args.fixture_set is not None),
                                            ("--credentials-file", args.credentials_file is not None)) if present]
        if given:
            parser.error("--demo names its own fixture desk, instant and fixture set and loads no credential; it cannot "
                         "be combined with %s" % ", ".join(given))
        args.offline = True
        args.desk_root = DEMO_DESK_ROOT
        args.now = contract.parse_utc_instant(DEMO_NOW, "--now")
        if args.symbol is None:
            args.symbol = DEMO_SYMBOL
    if args.symbol is None:
        parser.error("--symbol SYMBOL is required")
    args.symbol = args.symbol.strip().upper()
    if not SYMBOL_RE.match(args.symbol):
        parser.error("--symbol must be a ticker such as SPY, got %r" % args.symbol)
    rate: Optional[Decimal] = None
    if RATE_RE.match(args.rate.strip()):
        try:
            rate = Decimal(args.rate.strip())
        except InvalidOperation:
            rate = None
    if rate is None or not rate < 1:
        parser.error("--rate must be a plain decimal in [0, 1) such as 0.04, got %r" % args.rate)
    args.rate_decimal = rate
    if args.fixture_set is not None and not args.offline:
        parser.error("--fixture-set applies only to --offline runs")
    if args.credentials_file is not None and args.offline:
        parser.error("--credentials-file applies to live runs (offline runs load no credential)")


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
# The arithmetic
# --------------------------------------------------------------------------------------------


def third_friday(day: datetime.date) -> datetime.date:
    """The third Friday of ``day``'s month: the first Friday plus 14 days."""
    first = day.replace(day=1)
    return first + datetime.timedelta(days=(4 - first.weekday()) % 7 + 14)


def standard_expiration(dates: Sequence[datetime.date], today: datetime.date) -> Optional[datetime.date]:
    """The first listed date at least :data:`MIN_DAYS_OUT` calendar days out that is its month's standard (third
    Friday) settlement day."""
    for day in dates:
        if (day - today).days >= MIN_DAYS_OUT and day == market_clock.settlement_day(third_friday(day), False):
            return day
    return None


def stamp(quote: Quote) -> Optional[datetime.datetime]:
    """``min(bid_date, ask_date)``, or ``None`` when either is missing."""
    if quote.bid_date is None or quote.ask_date is None:
        return None
    return min(quote.bid_date, quote.ask_date)


def two_sided(quote: Optional[Quote]) -> bool:
    return quote is not None and quote.bid is not None and quote.ask is not None and quote.ask >= quote.bid >= 0


def mid(quote: Quote) -> Decimal:
    return (quote.bid + quote.ask) / 2


def pct(sigma: float) -> Decimal:
    """A volatility as a percentage to two decimals, half-even, through its ``repr``: ``25.07``."""
    return (Decimal(repr(sigma)) * 100).quantize(CENT, rounding=ROUND_HALF_EVEN)


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_EVEN)


def printed(value: Decimal) -> Decimal:
    """A price as the text prints it: at least two decimals, every decimal kept (``648.1`` -> ``648.10``)."""
    exponent = value.as_tuple().exponent
    return value.quantize(CENT) if isinstance(exponent, int) and exponent > -2 else value


class Writer:
    """The text's lines with every number, date and clock they print (the strict emitted document)."""

    def __init__(self) -> None:
        self.emitted: List[str] = []
        self.identifiers: List[str] = []
        self.clocks: List[str] = []

    def number(self, text: str) -> str:
        """A number written by this tool (a price, the rate, the factor): its numeric tokens are emitted."""
        self.emitted.extend(token.text for token in alert_lint.numeric_tokens(text))
        return text

    def price(self, value: Decimal) -> str:
        return self.number(format(printed(value), "f"))

    def date(self, day: datetime.date) -> str:
        text = day.isoformat()
        if text not in self.identifiers:
            self.identifiers.append(text)
        return text

    def clock(self, moment: datetime.datetime) -> str:
        text = utc_clock(moment)
        if text not in self.clocks:
            self.clocks.append(text)
        return text

    def document(self, as_of: datetime.datetime) -> Dict[str, Any]:
        return {"emitted_numbers": list(self.emitted), "rule_ids": [], "identifiers": list(self.identifiers),
                "clocks": list(self.clocks), "as_of": as_of.astimezone(datetime.timezone.utc).isoformat(),
                "footer": alert_lint.FOOTER_SNAPSHOT}


def utc_clock(moment: datetime.datetime) -> str:
    return moment.astimezone(datetime.timezone.utc).strftime("%H:%M UTC")


def open_client(args: Any, now: datetime.datetime, version: str, receipts: List[Any]) -> alpaca_client.AlpacaClient:
    """The offline replay client of the desk root's fixture set, or the live paper-host client with the key pair."""
    agent = user_agent(version)
    if args.offline:
        try:
            paths = desk_paths.for_root(args.desk_root)
        except desk_paths.DeskPathsError as exc:
            raise ToolError("desk_root", str(exc)) from None
        try:
            transport = alpaca_client.OfflineTransport(paths.alpaca_fixtures_dir,
                                                       args.fixture_set or DEFAULT_FIXTURE_SET, now=now)
        except ValueError as exc:
            raise ToolError("fixtures_missing", str(exc)) from None
        return alpaca_client.AlpacaClient(HOST, None, options_feed=OPTIONS_FEED, timeout_s=args.timeout,
                                          user_agent=agent, transport=transport, on_request=receipts.append)
    try:
        credential = alpaca_client.load_credential(KEY_ENV, SECRET_ENV, args.credentials_file or CREDENTIALS_FILE)
    except alpaca_client.AlpacaError as exc:
        raise Unavailable(exc.status, str(exc)) from None
    return alpaca_client.AlpacaClient(HOST, credential, options_feed=OPTIONS_FEED, timeout_s=args.timeout,
                                      user_agent=agent, transport=LIVE_TRANSPORT, on_request=receipts.append)


def request(what: str, call: Any) -> Any:
    """Run one client call; a failure is ``request_failed``, except an ``auth`` refusal on the PAPER trading host
    (the contract listing), which is ``not_paper_key``: the quotes request ran first, so the market-data host has
    already answered this key pair, and the paper host refuses a key from another environment (module notes)."""
    try:
        return call()
    except alpaca_client.AlpacaError as exc:
        if exc.status == "auth" and exc.path is not None and _on_paper_host(exc.path):
            raise Unavailable("not_paper_key", NOT_PAPER_KEY_MESSAGE % exc.http_status) from None
        raise Unavailable("request_failed", "the %s request failed: %s" % (what, exc)) from None
    except ValueError as exc:  # a MarketTypesError is a ValueError
        raise Unavailable("request_failed", "the %s request failed: %s" % (what, exc)) from None


def _on_paper_host(path: str) -> bool:
    try:
        return alpaca_client.host_for_path(path) == alpaca_client.PAPER_HOST
    except alpaca_client.AlpacaError:
        return False


def snapshot(args: Any, now: datetime.datetime, version: str) -> Tuple[Dict[str, Any], List[str]]:
    """``(payload, lines)`` of the three calls (module notes)."""
    receipts: List[Any] = []
    client = open_client(args, now, version, receipts)
    symbol = args.symbol
    quotes = request("quotes", lambda: client.quotes([symbol]))
    underlying = quotes.quotes.get(symbol)
    if not two_sided(underlying) or underlying.bid <= 0:
        missing = list(getattr(quotes, "not_returned", ())) + list(quotes.unmatched)
        raise Unavailable("no_quote", "no two-sided quote for %s (not returned: %s)" % (
            symbol, ", ".join(missing) or "none"))
    spot = mid(underlying)
    spot_as_of = stamp(underlying)
    if spot_as_of is None:
        raise Unavailable("no_quote", "the %s quote carries no bid or ask stamp" % symbol)
    today = market_clock.to_et(now).date()
    expirations = request("expirations", lambda: client.option_expirations(symbol, today))
    expiration = standard_expiration(expirations.dates, today)
    if expiration is None:
        raise Unavailable("no_expiration", "no standard expiration of %s at least %d calendar days after %s"
                          % (symbol, MIN_DAYS_OUT, today.isoformat()))
    chain = request("chain", lambda: client.option_chain(symbol, expiration))
    sides: Dict[Decimal, Dict[str, Quote]] = {}
    for option in chain.options:
        if option.strike is not None and option.option_type in ("call", "put"):
            sides.setdefault(option.strike, {}).setdefault(option.option_type, option)
    pairs = [strike for strike, found in sides.items() if "call" in found and "put" in found]
    if not pairs:
        raise Unavailable("no_strike_pair", "the %s %s chain has no strike with both a call and a put"
                          % (symbol, expiration.isoformat()))
    strike = min(pairs, key=lambda value: (abs(value - spot), value))
    call, put = sides[strike]["call"], sides[strike]["put"]
    if not (two_sided(call) and two_sided(put)):
        raise Unavailable("no_two_sided_quote", "the %s strike's call or put has no two-sided quote"
                          % expiry.plain(strike))
    call_as_of, put_as_of = stamp(call), stamp(put)
    if call_as_of is None or put_as_of is None:
        raise Unavailable("stamps_unaligned", "the %s strike's call or put carries no stamp" % expiry.plain(strike))
    for as_of in (call_as_of, put_as_of):
        if abs((as_of - spot_as_of).total_seconds()) > ALIGNMENT_S:
            raise Unavailable("stamps_unaligned", "an option stamp is more than %d s from the %s stamp"
                              % (ALIGNMENT_S, symbol))
    tau = (market_clock.exercise_cutoff_utc(expiration, False) - call_as_of).total_seconds() / units.SECONDS_PER_YEAR
    rate = float(args.rate_decimal)
    call_mid, put_mid = mid(call), mid(put)
    solved = {}
    for name, right, price in (("call", "CALL", call_mid), ("put", "PUT", put_mid)):
        result = iv.implied_vol(float(price), right, float(spot), float(strike), tau, rate, 0.0)
        if result.status != iv.STATUS_OK or result.sigma is None:
            raise Unavailable("iv_failed", "the %s mid %s gives no implied volatility (%s)"
                              % (name, quote_cache.format_money(price), result.status))
        solved[name] = result.sigma
    atm = (solved["call"] + solved["put"]) / 2
    straddle = cents((call_mid + put_mid) * STRADDLE_FACTOR)
    one_sigma = cents(Decimal(repr(float(spot) * atm * math.sqrt(tau))))
    as_of = min(spot_as_of, call_as_of, put_as_of)
    days = (expiration - today).days
    w = Writer()
    lines = [
        QUOTE_LINE % (alerts.fmt_reason(symbol, w.emitted), w.price(spot), w.price(underlying.bid),
                      w.price(underlying.ask), w.clock(spot_as_of)),
        EXPIRATION_LINE % (alerts.fmt_days(MIN_DAYS_OUT, w.emitted, market_clock.UNIT_CALENDAR), w.date(expiration),
                           alerts.fmt_days(days, w.emitted, market_clock.UNIT_CALENDAR)),
        IV_LINE % (alerts.fmt_strike(strike, w.emitted), alerts.fmt_pct(pct(atm), w.emitted),
                   alerts.fmt_pct(pct(solved["call"]), w.emitted), alerts.fmt_pct(pct(solved["put"]), w.emitted),
                   w.price(call_mid), w.price(put_mid), w.number(args.rate.strip()), w.number("0")),
        MOVE_LINE % (w.date(expiration), w.number(format(STRADDLE_FACTOR, "f")), w.price(straddle), w.price(one_sigma)),
        SNAPSHOT_LINE % (SOURCE_WORDS, alerts.fmt_minutes(FEED_DELAY_MIN, w.emitted), w.clock(as_of)),
        alerts.fmt_footer(as_of, FEED_DELAY_MIN, w.emitted, w.clocks, kind=alert_lint.FOOTER_SNAPSHOT),
    ]
    if args.demo:
        lines.insert(0, DEMO_LINE)
    message = "\n".join(lines)
    result = alert_lint.check(message, w.emitted, rule_ids=[], identifiers=w.identifiers, clocks=w.clocks, as_of=as_of,
                              footer=alert_lint.FOOTER_SNAPSHOT)
    if not result.ok:
        codes = sorted({finding.code for finding in result.findings}) or ["a finding"]
        raise ToolError("lint_failed", "the snapshot failed alert_lint and was not printed: %s" % ", ".join(codes))
    payload: Dict[str, Any] = {
        "symbol": symbol,
        "quote": {"bid": printed(underlying.bid), "ask": printed(underlying.ask), "mid": printed(spot),
                  "as_of": spot_as_of},
        "expiration": expiration, "dte_calendar": days, "strike": expiry.plain_decimal(strike),
        "call": {"mid": printed(call_mid), "iv": solved["call"]}, "put": {"mid": printed(put_mid), "iv": solved["put"]},
        "atm_iv": atm, "tau_years": tau, "rate": args.rate_decimal, "q": Decimal("0"),
        "expected_move": {"straddle": printed(straddle), "one_sigma": printed(one_sigma)},
        "requests_made": len(receipts), "fixture_set": (args.fixture_set or DEFAULT_FIXTURE_SET) if args.offline else None,
        "demo": bool(args.demo), "message": message, "emitted": w.document(as_of),
        "lint": {"ok": result.ok, "problems": list(result.problems), "numbers_checked": result.numbers_checked},
    }
    return payload, lines


def main(argv: Optional[Sequence[str]] = None) -> int:
    version = release_version()
    parser = build_parser(version)
    args = parser.parse_args(argv)
    if args.schema:
        print(contract.json_dumps(parser.schema(OUTPUT, NETWORK_ACCESS)))
        return contract.EXIT_QUIET
    validate(parser, args)
    now = args.now or state.utc_now()
    try:
        payload, lines = snapshot(args, now, version)
    except ToolError as exc:
        return fail(args, version, now, exc.code, exc.message, contract.EXIT_ERROR)
    except Unavailable as exc:
        return fail(args, version, now, exc.code, exc.message, contract.EXIT_UNAVAILABLE)
    if args.json:
        print(contract.json_dumps(contract.envelope(TOOL, version, payload, source=SOURCE, delay_min=FEED_DELAY_MIN,
                                                    generated_at=now)))
    else:
        print("\n".join(lines))
    return contract.EXIT_QUIET


if __name__ == "__main__":
    sys.exit(main())
