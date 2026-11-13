"""The GET-only Alpaca client (``aegis_desk.alpaca_client``): the key pair, errors, the offline
transport and its naming rule, the normalisation into the shared ``market_types`` records, and a
loopback fake server that fails on any non-GET request.

The module builds its own Alpaca-shaped fixture set in a temporary directory (``ALPACA_SET``,
written by :func:`write_set` from the same synthetic values the desk's fixture sets use: SPY
648.10/648.14 stamped 2026-11-03 19:31:12 UTC, the WMT 2026-11-20 105 call at 3.35/3.50, the
clock read at 19:50:00 UTC), with file names computed by :func:`expected_name`, an independent
copy of the documented naming rule; the committed desk fixture trees are not read here.

The loopback server (``http.server.ThreadingHTTPServer`` on 127.0.0.1:0) serves that set for
every allow-listed path of both pinned hosts, records every request line and header set, answers
404 elsewhere and records a violation (answering 405) for POST, PUT, PATCH, DELETE, HEAD and
OPTIONS; every test asserts the violation list is empty. A second, independent proof patches
``http.client.HTTPConnection.request`` by dotted name (tests may not import ``http.client``) and
asserts ``method == "GET"`` and ``body is None`` across a journey that touches every endpoint.
Neither the key ID nor the secret ever appears in ``str(err)``, ``repr(credential)``,
``repr(client)`` or a receipt, even when the server echoes the authentication headers back.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
import check_no_write_paths
# isort: on

import ast
import dataclasses
import http.server
import json
import os
import shutil
import stat
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from aegis_desk import alpaca_client as ac
from aegis_desk import market_types as mt

UTC = timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
NOW = datetime(2026, 11, 3, 19, 50, 0, tzinfo=UTC)
STAMP = datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC)
T = "2026-11-03T19:31:12Z"
UA = "aegis-desk-tests/0.0"
# Neither is key-shaped (no run of 20 alphanumerics); never real credentials.
KEY_ID = "fake-paper-key-id-0000-1111"
SECRET = "fake-paper-secret-2222-3333-4444"
SIX = ("SPY", "WMT", "NVDA", "WMT261120C00105000", "NVDA261120C00150000", "SPY261218P00620000")
EXP = date(2026, 11, 20)
EXPIRATIONS_START = date(2026, 11, 3)
EXPIRATIONS_END = EXPIRATIONS_START + timedelta(days=ac.EXPIRATIONS_WINDOW_DAYS)

_TMP = tempfile.TemporaryDirectory(prefix="aegis-alpaca-set-")
FIXTURES = _TMP.name
SET = "regular"
ALPACA_SET = os.path.join(FIXTURES, SET)


def tearDownModule() -> None:  # noqa: N802 - unittest hook name
    _TMP.cleanup()


# --------------------------------------------------------------------------------------------
# The synthetic Alpaca set
# --------------------------------------------------------------------------------------------


def expected_name(path: str, query: Dict[str, str]) -> Optional[str]:
    """The documented naming rule, written independently of the client (``None`` for no rule)."""
    def symbols() -> str:
        return ",".join(sorted(s.strip().upper() for s in query.get("symbols", "").split(",") if s.strip()))
    if path == "/v2/stocks/quotes/latest":
        stem = "stock_quotes__" + symbols()
    elif path == "/v1beta1/options/quotes/latest":
        stem = "option_quotes__" + symbols()
    elif path.startswith("/v1beta1/options/snapshots/"):
        stem = "snapshots__" + path.rsplit("/", 1)[1].upper()
        if query.get("expiration_date"):
            stem += "__" + query["expiration_date"]
    elif path == "/v2/options/contracts":
        underlying = query.get("underlying_symbols", "").upper()
        if query.get("expiration_date"):
            stem = "contracts__%s__%s" % (underlying, query["expiration_date"])
        else:
            stem = "contracts__%s__%s__%s" % (underlying, query.get("expiration_date_gte", ""),
                                              query.get("expiration_date_lte", ""))
    elif path == "/v2/stocks/bars":
        stem = "bars__%s__%s__%s" % (query.get("symbols", "").upper(), query.get("start", ""), query.get("end", ""))
    elif path == "/v2/clock":
        stem = "clock"
    elif path == "/v2/calendar":
        stem = "calendar__%s__%s" % (query.get("start", ""), query.get("end", ""))
    else:
        return None
    if "page_token" in query:
        stem += "__page-" + query["page_token"]
    return stem + ".json"


def quote(bp: Any, ap: Any, t: str = T, bs: int = 3, as_: int = 2, exch: str = "V") -> Dict[str, Any]:
    return {"ap": ap, "as": as_, "ax": exch, "bp": bp, "bs": bs, "bx": exch, "c": ["R"], "t": t, "z": "B"}


STOCKS = {"SPY": quote(648.10, 648.14), "WMT": quote(104.20, 104.26), "NVDA": quote(181.50, 181.58)}
OPTIONS = {
    "WMT261120C00105000": quote(3.35, 3.50, bs=46, as_=28, exch="Z"),
    "NVDA261120C00150000": quote(32.10, 32.60, exch="Z"),
    "SPY261218P00620000": quote(7.85, 8.05, exch="Z"),
}
WMT_STRIKES = ("100", "105", "110")


def contract(symbol: str, strike: str, right: str, expiration: str = "2026-11-20", underlying: str = "WMT",
             root: str = "WMT", size: str = "100", multiplier: str = "100", open_interest: str = "5755") -> Dict[str, Any]:
    return {"id": "00000000-0000-0000-0000-%012d" % abs(hash(symbol) % 10 ** 12), "symbol": symbol,
            "name": "%s %s %s %s" % (underlying, expiration, right, strike), "status": "active", "tradable": True,
            "expiration_date": expiration, "root_symbol": root, "underlying_symbol": underlying, "type": right,
            "style": "american", "strike_price": strike, "multiplier": multiplier, "size": size,
            "open_interest": open_interest, "open_interest_date": "2026-11-02", "close_price": "3.40",
            "close_price_date": "2026-11-02"}


def wmt_symbol(strike: str, right: str) -> str:
    return "WMT261120%s%08d" % ("C" if right == "call" else "P", int(Decimal(strike) * 1000))


def snapshot(bp: float, ap: float, last: float = 3.40) -> Dict[str, Any]:
    return {"latestQuote": quote(bp, ap, exch="Z"),
            "latestTrade": {"c": ["I"], "p": last, "s": 5, "t": "2026-11-03T19:30:58.123456789Z", "x": "Z"},
            "greeks": {"delta": 0.5, "gamma": 0.01, "theta": -0.02, "vega": 0.1, "rho": 0.01},
            "impliedVolatility": 0.25,
            "dailyBar": {"c": 3.4, "h": 3.5, "l": 3.2, "n": 10, "o": 3.3, "t": "2026-11-03T05:00:00Z", "v": 822,
                         "vw": 3.38}}


def trading_days(year: int, month: int) -> List[Dict[str, str]]:
    days = []
    day = date(year, month, 1)
    while day.month == month:
        if day.weekday() < 5 and day != date(2026, 11, 26):
            close, session_close = ("13:00", "1700") if day == date(2026, 11, 27) else ("16:00", "2000")
            days.append({"date": day.isoformat(), "open": "09:30", "close": close, "session_open": "0400",
                         "session_close": session_close, "settlement_date": (day + timedelta(days=1)).isoformat()})
        day += timedelta(days=1)
    return days


def bars(closes: List[Tuple[str, float, int]]) -> List[Dict[str, Any]]:
    out = []
    for day, close, volume in closes:
        offset = "05:00:00Z" if day >= "2026-11-02" else "04:00:00Z"  # midnight ET (EST from Nov 1, 2026)
        out.append({"t": "%sT%s" % (day, offset), "o": close - 1.0, "h": close + 1.5, "l": close - 2.0, "c": close,
                    "v": volume, "n": 1000, "vw": close})
    return out


def write_set(set_dir: str) -> None:
    os.makedirs(set_dir, exist_ok=True)

    def put(path: str, query: Dict[str, str], document: Any) -> None:
        name = expected_name(path, query)
        assert name is not None
        with open(os.path.join(set_dir, name), "w", encoding="utf-8") as handle:
            json.dump(document, handle, indent=2, sort_keys=True)

    for combo in (["SPY"], ["WMT"], ["NVDA"], ["SPY", "WMT"], ["NVDA", "SPY", "WMT"]):
        put("/v2/stocks/quotes/latest", {"symbols": ",".join(combo)},
            {"quotes": {s: STOCKS[s] for s in combo}, "currency": "USD"})
    put("/v2/stocks/quotes/latest", {"symbols": "SPX"}, {"quotes": {}, "currency": "USD"})
    put("/v2/stocks/quotes/latest", {"symbols": "SPX,SPY"}, {"quotes": {"SPY": STOCKS["SPY"]}, "currency": "USD"})
    for combo in (["WMT261120C00105000"], ["NVDA261120C00150000", "SPY261218P00620000", "WMT261120C00105000"]):
        put("/v1beta1/options/quotes/latest", {"symbols": ",".join(combo)}, {"quotes": {s: OPTIONS[s] for s in combo}})
    contracts = [contract(wmt_symbol(k, r), k, r) for k in WMT_STRIKES for r in ("call", "put")]
    put("/v2/options/contracts", {"underlying_symbols": "WMT", "expiration_date": "2026-11-20"},
        {"option_contracts": contracts, "next_page_token": None})
    priced = [wmt_symbol(k, r) for k in WMT_STRIKES for r in ("call", "put")][:-1]  # the 110 put has no snapshot
    quotes_by_symbol = {wmt_symbol("100", "call"): (6.15, 6.35), wmt_symbol("100", "put"): (1.75, 1.85),
                        wmt_symbol("105", "call"): (3.35, 3.50), wmt_symbol("105", "put"): (3.95, 4.15),
                        wmt_symbol("110", "call"): (1.40, 1.50)}
    first = {s: snapshot(*quotes_by_symbol[s]) for s in priced[:3]}
    second = {s: snapshot(*quotes_by_symbol[s]) for s in priced[3:]}
    put("/v1beta1/options/snapshots/WMT", {"expiration_date": "2026-11-20"}, {"snapshots": first, "next_page_token": "p2"})
    put("/v1beta1/options/snapshots/WMT", {"expiration_date": "2026-11-20", "page_token": "p2"},
        {"snapshots": second, "next_page_token": None})
    window = {"underlying_symbols": "SPY", "expiration_date_gte": EXPIRATIONS_START.isoformat(),
              "expiration_date_lte": EXPIRATIONS_END.isoformat()}
    spy = [contract("SPY261120C00650000", "650", "call", "2026-11-20", "SPY", "SPY"),
           contract("SPY261120C00655000", "655", "call", "2026-11-20", "SPY", "SPY"),
           contract("SPY261218C00650000", "650", "call", "2026-12-18", "SPY", "SPY")]
    put("/v2/options/contracts", window, {"option_contracts": spy, "next_page_token": "p2"})
    put("/v2/options/contracts", dict(window, page_token="p2"),
        {"option_contracts": [contract("SPY270319C00650000", "650", "call", "2027-03-19", "SPY", "SPY"),
                              contract("SPY261218C00660000", "660", "call", "2026-12-18", "SPY", "SPY")],
         "next_page_token": ""})
    put("/v2/stocks/bars", {"symbols": "SPY", "start": "2026-10-27", "end": "2026-11-02"},
        {"bars": {"SPY": bars([("2026-10-27", 640.10, 50100000), ("2026-10-28", 641.20, 51200000),
                               ("2026-10-29", 642.30, 52300000), ("2026-10-30", 643.40, 53400000),
                               ("2026-11-02", 645.90, 55600000)])}, "next_page_token": None})
    put("/v2/stocks/bars", {"symbols": "NVDA", "start": "2026-10-27", "end": "2026-11-02"},
        {"bars": {"NVDA": bars([("2026-10-27", 180.0, 1), ("2026-10-28", 181.0, 2), ("2026-10-29", 182.0, 3),
                                ("2026-10-30", 183.0, 4), ("2026-11-02", 184.0, 5)])}, "next_page_token": None})
    put("/v2/clock", {}, {"timestamp": "2026-11-03T14:50:00.123456789-05:00", "is_open": True,
                          "next_open": "2026-11-04T09:30:00-05:00", "next_close": "2026-11-03T16:00:00-05:00"})
    put("/v2/calendar", {"start": "2026-11-01", "end": "2026-11-30"}, trading_days(2026, 11))


write_set(ALPACA_SET)


def read_bytes(*parts: str) -> bytes:
    with open(os.path.join(*parts), "rb") as handle:
        return handle.read()


def read_json(*parts: str) -> Any:
    return json.loads(read_bytes(*parts).decode("utf-8"))


def credential() -> ac.Credential:
    return ac.Credential(KEY_ID, SECRET, "env")


def offline(fixture_set: str = SET, fixture_dir: str = FIXTURES, **kwargs: Any) -> ac.OfflineTransport:
    return ac.OfflineTransport(fixture_dir, fixture_set, **kwargs)


def offline_client(on_request: Optional[Callable] = None, fixture_dir: str = FIXTURES, fixture_set: str = SET,
                   **kwargs: Any) -> ac.AlpacaClient:
    kwargs.setdefault("now", NOW)
    return ac.AlpacaClient("paper", None, user_agent=UA, on_request=on_request,
                           transport=offline(fixture_set, fixture_dir, **kwargs))


# --------------------------------------------------------------------------------------------
# Constants, Credential, load_credential, AlpacaError
# --------------------------------------------------------------------------------------------


class ConstantsTest(unittest.TestCase):
    def test_hosts_paths_and_limits(self) -> None:
        self.assertEqual("data.alpaca.markets", ac.DATA_HOST)
        self.assertEqual("paper-api.alpaca.markets", ac.PAPER_HOST)
        self.assertEqual("api.alpaca.markets", ac.LIVE_HOST)
        self.assertEqual(("paper",), ac.HOST_KINDS)
        self.assertEqual(("/v2/stocks/quotes/latest", "/v2/stocks/bars", "/v1beta1/options/snapshots/{underlying}",
                          "/v1beta1/options/quotes/latest"), ac.DATA_PATHS)
        self.assertEqual(("/v2/clock", "/v2/calendar", "/v2/options/contracts"), ac.PAPER_PATHS)
        self.assertEqual(ac.DATA_PATHS + ac.PAPER_PATHS, ac.ALLOWED_PATHS)
        self.assertEqual(200, ac.RATE_LIMIT_PER_MIN)
        self.assertEqual(50, ac.MAX_BATCH)
        self.assertLessEqual(ac.MAX_BATCH, 100, "Alpaca serves at most 100 option symbols per latest-quotes call")
        self.assertEqual((1000, 10000, 10000, 10), (ac.SNAPSHOTS_PAGE_LIMIT, ac.CONTRACTS_PAGE_LIMIT,
                                                    ac.BARS_PAGE_LIMIT, ac.MAX_PAGES))
        self.assertEqual(400, ac.EXPIRATIONS_WINDOW_DAYS)
        self.assertEqual(r"^[A-Za-z0-9.$^:_-]{1,32}$", ac.SYMBOL_RE.pattern)
        self.assertEqual(("indicative", "opra"), ac.OPTIONS_FEEDS)
        self.assertEqual(("iex",), ac.STOCK_FEEDS)
        self.assertEqual(("indicative", "iex"), (ac.DEFAULT_OPTIONS_FEED, ac.DEFAULT_STOCK_FEED))
        self.assertEqual(("APCA-API-KEY-ID", "APCA-API-SECRET-KEY"), (ac.HEADER_KEY_ID, ac.HEADER_SECRET))
        self.assertIs(ac.Quote, mt.Quote)
        self.assertIs(ac.Bar, mt.Bar)
        self.assertIs(ac.RequestReceipt, mt.RequestReceipt)

    def test_symbol_pattern(self) -> None:
        for good in ("SPY", "spy", "WMT261120C00105000", "BRK.B", "$SPX.X", "^VIX", "A:B", "X_Y-Z", "a" * 32):
            self.assertIsNotNone(ac.SYMBOL_RE.match(good), good)
        for bad in ("", " SPY", "SPY WMT", "SPY,WMT", "SPY/1", "a" * 33, "SP\nY", "sp%y"):
            self.assertIsNone(ac.SYMBOL_RE.match(bad), repr(bad))
        self.assertIsNone(ac.SYMBOL_RE.fullmatch("SPY\n"))

    def test_hosts_for_paths(self) -> None:
        for path in ("/v2/stocks/quotes/latest", "/v2/stocks/bars", "/v1beta1/options/quotes/latest",
                     "/v1beta1/options/snapshots/SPY", "/v1beta1/options/snapshots/BRK.B"):
            self.assertEqual(ac.DATA_HOST, ac.host_for_path(path), path)
        for path in ("/v2/clock", "/v2/calendar", "/v2/options/contracts"):
            self.assertEqual(ac.PAPER_HOST, ac.host_for_path(path), path)
        self.assertEqual("/v1beta1/options/snapshots/SPY", ac.snapshots_path("SPY"))
        for bad in ("spy", "", "../v2", "SPY/x", "S.P.Y", "SPY%2F", "A" * 11):
            with self.assertRaises(ac.AlpacaError) as caught:
                ac.snapshots_path(bad)
            self.assertEqual("refused_path", caught.exception.status, bad)


class CredentialTest(unittest.TestCase):
    def test_repr_and_str_hide_both_values(self) -> None:
        pair = ac.Credential(KEY_ID, SECRET, "env")
        self.assertEqual("Credential(source=env)", repr(pair))
        self.assertEqual("Credential(source=env)", str(pair))
        self.assertEqual("Credential(source=file)", repr(ac.Credential(KEY_ID, SECRET, "file")))
        for value in (KEY_ID, SECRET):
            self.assertNotIn(value, repr(pair) + str(pair) + format(pair))
        self.assertEqual("env", pair.source)
        self.assertEqual({"APCA-API-KEY-ID": KEY_ID, "APCA-API-SECRET-KEY": SECRET}, pair.auth_headers())
        self.assertFalse(hasattr(pair, "__dict__"), "a slotted Credential exposes no instance dictionary")

    def test_programming_errors(self) -> None:
        for key_id, secret in (("   ", SECRET), (KEY_ID, ""), (KEY_ID, "has newline\nx"), ("two words", SECRET),
                               (KEY_ID, KEY_ID), ("café", SECRET)):
            with self.assertRaises(ValueError, msg=repr((key_id, secret))):
                ac.Credential(key_id, secret, "env")
        with self.assertRaises(ValueError):
            ac.Credential(KEY_ID, SECRET, "card")
        with self.assertRaises(TypeError):
            ac.Credential(12345, SECRET, "env")  # type: ignore[arg-type]
        stripped = ac.Credential("  " + KEY_ID + "\n", " " + SECRET + " ", "env")
        self.assertEqual({"APCA-API-KEY-ID": KEY_ID, "APCA-API-SECRET-KEY": SECRET}, stripped.auth_headers())


class LoadCredentialTest(unittest.TestCase):
    KEY_ENV = "AEGIS_TEST_APCA_KEY_ID"
    SECRET_ENV = "AEGIS_TEST_APCA_SECRET"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-key-")
        self.dir = self._tmp.name
        self.file = os.path.join(self.dir, "alpaca.key")
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(self.KEY_ENV, None)
        os.environ.pop(self.SECRET_ENV, None)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def load(self, credentials_file: Optional[str] = "default") -> ac.Credential:
        return ac.load_credential(self.KEY_ENV, self.SECRET_ENV, self.file if credentials_file == "default"
                                  else credentials_file)

    def write_file(self, text: str, mode: int = 0o600) -> str:
        # A previous step may have left the file read-only (0400); replace it rather than reopen it.
        if os.path.lexists(self.file):
            os.remove(self.file)
        with open(self.file, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(self.file, mode)
        return self.file

    def assert_status(self, status: str, call: Callable[[], Any], *hidden: str) -> ac.AlpacaError:
        with self.assertRaises(ac.AlpacaError) as caught:
            call()
        self.assertEqual(status, caught.exception.status)
        for value in hidden:
            self.assertNotIn(value, str(caught.exception))
        return caught.exception

    def test_environment_wins_over_the_file(self) -> None:
        self.write_file("file-key-id-value\nfile-secret-value\n")
        os.environ[self.KEY_ENV] = "  env-key-id-value  "
        os.environ[self.SECRET_ENV] = "env-secret-value"
        pair = self.load()
        self.assertEqual("env", pair.source)
        self.assertEqual({"APCA-API-KEY-ID": "env-key-id-value", "APCA-API-SECRET-KEY": "env-secret-value"},
                         pair.auth_headers())

    def test_one_variable_without_the_other_is_no_credential_never_half_from_the_file(self) -> None:
        self.write_file("file-key-id-value\nfile-secret-value\n")
        os.environ[self.KEY_ENV] = "env-key-id-value"
        err = self.assert_status("no_credential", self.load, "env-key-id-value", "file-secret-value")
        self.assertIn(self.KEY_ENV, err.message)
        self.assertIn(self.SECRET_ENV, err.message)
        os.environ.pop(self.KEY_ENV)
        os.environ[self.SECRET_ENV] = "env-secret-value"
        err = self.assert_status("no_credential", self.load, "env-secret-value", "file-key-id-value")
        self.assertIn(self.KEY_ENV, err.message)

    def test_blank_environment_counts_as_absent(self) -> None:
        self.write_file("file-key-id-value\nfile-secret-value\nthird line ignored\n")
        os.environ[self.KEY_ENV] = "   "
        os.environ[self.SECRET_ENV] = ""
        pair = self.load()
        self.assertEqual("file", pair.source)
        self.assertEqual({"APCA-API-KEY-ID": "file-key-id-value", "APCA-API-SECRET-KEY": "file-secret-value"},
                         pair.auth_headers())

    def test_file_mode_must_have_no_group_or_other_bits(self) -> None:
        for mode in (0o644, 0o640, 0o604, 0o660, 0o666, 0o700 | 0o010, 0o600 | 0o001):
            self.write_file("file-key-id-value\nfile-secret-value\n", mode)
            err = self.assert_status("credential_file_mode", self.load, "file-key-id-value", "file-secret-value")
            self.assertIn("%04o" % mode, err.message)
            self.assertIn(self.file, err.message)
        for mode in (0o600, 0o400, 0o700):
            self.write_file("file-key-id-value\nfile-secret-value\n", mode)
            self.assertEqual("file", self.load().source)

    def test_file_must_be_a_regular_file(self) -> None:
        self.assert_status("credential_file_mode", lambda: self.load(self.dir))

    def test_nothing_configured_is_no_credential(self) -> None:
        err = self.assert_status("no_credential", lambda: self.load(os.path.join(self.dir, "missing.key")))
        self.assertIn(self.KEY_ENV, err.message)
        self.assertIn(self.SECRET_ENV, err.message)
        self.assertIn("missing.key", err.message)
        self.assert_status("no_credential", lambda: self.load(None))

    def test_empty_short_or_undecodable_file_is_no_credential(self) -> None:
        for text in ("", "\n", "\n\n", "only-the-key-id\n", "only-the-key-id", "\nonly-the-secret\n"):
            self.write_file(text)
            self.assert_status("no_credential", self.load, "only-the-key-id", "only-the-secret")
        with open(self.file, "wb") as handle:
            handle.write(b"\xff\xfe\x00garbage\nmore\n")
        os.chmod(self.file, 0o600)
        self.assert_status("no_credential", self.load)

    def test_unusable_values_are_no_credential_without_echoing_them(self) -> None:
        os.environ[self.KEY_ENV] = "two words"
        os.environ[self.SECRET_ENV] = "env-secret-value"
        self.assert_status("no_credential", lambda: self.load(None), "two words", "env-secret-value")
        os.environ[self.KEY_ENV] = "same-value-twice"
        os.environ[self.SECRET_ENV] = "same-value-twice"
        self.assert_status("no_credential", lambda: self.load(None), "same-value-twice")
        os.environ.pop(self.KEY_ENV)
        os.environ.pop(self.SECRET_ENV)
        self.write_file("café-key\nfile-secret-value\n")
        self.assert_status("no_credential", self.load, "caf", "file-secret-value")

    def test_mode_check_uses_the_permission_bits_only(self) -> None:
        self.write_file("file-key-id-value\nfile-secret-value\n", 0o600)
        self.assertEqual(0o600, stat.S_IMODE(os.stat(self.file).st_mode))
        self.assertEqual("file", self.load().source)


class AlpacaErrorTest(unittest.TestCase):
    def test_str_attributes_and_statuses(self) -> None:
        err = ac.AlpacaError("quota", "HTTP 429: too many requests.", http_status=429, path="/v2/stocks/quotes/latest")
        self.assertEqual("quota: HTTP 429: too many requests.", str(err))
        self.assertEqual(("quota", 429, "/v2/stocks/quotes/latest"), (err.status, err.http_status, err.path))
        self.assertEqual(("no_credential", "credential_file_mode", "auth", "subscription", "quota", "http", "network",
                          "timeout", "decode", "incomplete", "refused_redirect", "refused_host", "refused_path",
                          "bad_symbol", "bad_request"), ac.STATUSES)
        with self.assertRaises(ValueError):
            ac.AlpacaError("unknown_status", "x")
        self.assertEqual("network: network", str(ac.AlpacaError("network")))


# --------------------------------------------------------------------------------------------
# Construction, URL building, no I/O
# --------------------------------------------------------------------------------------------


class ConstructionTest(unittest.TestCase):
    def test_host_kinds_and_the_host_of_each_path(self) -> None:
        paper = ac.AlpacaClient("paper", credential(), user_agent=UA)
        self.assertEqual(("https", "data.alpaca.markets", "paper-api.alpaca.markets", "paper"),
                         (paper.scheme, paper.data_host, paper.paper_host, paper.host_kind))
        self.assertEqual(("indicative", "iex"), (paper.options_feed, paper.stock_feed))
        self.assertEqual("https://paper-api.alpaca.markets/v2/clock", paper.url_for("/v2/clock", {}))
        self.assertEqual("https://paper-api.alpaca.markets/v2/options/contracts?underlying_symbols=SPY",
                         paper.url_for("/v2/options/contracts", {"underlying_symbols": "SPY"}))
        self.assertEqual("https://data.alpaca.markets/v2/stocks/quotes/latest?symbols=SPY%2CWMT&feed=iex",
                         paper.url_for("/v2/stocks/quotes/latest", {"symbols": "SPY,WMT", "feed": "iex"}))
        self.assertEqual("https://data.alpaca.markets/v1beta1/options/snapshots/WMT?feed=indicative",
                         paper.url_for("/v1beta1/options/snapshots/WMT", {"feed": "indicative"}))
        loopback = ac.AlpacaClient("loopback:8123", credential(), user_agent=UA)
        self.assertEqual(("http", "127.0.0.1:8123", "127.0.0.1:8123"),
                         (loopback.scheme, loopback.data_host, loopback.paper_host))
        self.assertEqual("http://127.0.0.1:8123/v2/clock", loopback.url_for("/v2/clock", {}))
        self.assertEqual(10.0, paper.timeout_s)
        self.assertEqual(UA, paper.user_agent)
        self.assertIsInstance(paper.transport, ac.UrllibTransport)
        opra = ac.AlpacaClient("paper", credential(), user_agent=UA, options_feed="opra")
        self.assertEqual("opra", opra.options_feed)

    def test_the_live_host_is_refused(self) -> None:
        for kind in ("live", "api.alpaca.markets"):
            with self.assertRaises(ac.AlpacaError) as caught:
                ac.AlpacaClient(kind, credential(), user_agent=UA)
            self.assertEqual("refused_host", caught.exception.status, kind)
            self.assertIn("api.alpaca.markets", caught.exception.message)
            self.assertIn("paper", caught.exception.message)

    def test_unknown_host_kind_is_refused(self) -> None:
        for kind in ("data.alpaca.markets", "paper-api.alpaca.markets", "https://data.alpaca.markets", "Paper",
                     "sandbox", "production", "loopback", "loopback:0", "loopback:70000", "loopback:abc", "",
                     "loopback:8080:1", None, 1):
            with self.assertRaises(ac.AlpacaError) as caught:
                ac.AlpacaClient(kind, credential(), user_agent=UA)  # type: ignore[arg-type]
            self.assertEqual("refused_host", caught.exception.status, kind)

    def test_credential_and_feed_rules(self) -> None:
        with self.assertRaises(ac.AlpacaError) as caught:
            ac.AlpacaClient("paper", None, user_agent=UA)
        self.assertEqual("no_credential", caught.exception.status)
        with self.assertRaises(TypeError):
            ac.AlpacaClient("paper", SECRET, user_agent=UA)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            ac.AlpacaClient("paper", credential(), user_agent=UA, on_request="not callable")  # type: ignore[arg-type]
        for options_feed, stock_feed in (("sip", "iex"), ("indicative", "sip"), ("Indicative", "iex"), ("opra", "")):
            with self.assertRaises(ValueError):
                ac.AlpacaClient("paper", credential(), user_agent=UA, options_feed=options_feed, stock_feed=stock_feed)
        self.assertIsNone(offline_client().credential)  # None is fine with an OfflineTransport

    def test_argument_validation(self) -> None:
        for timeout in (0, -1.0, True, "10"):
            with self.assertRaises(ValueError):
                ac.AlpacaClient("paper", credential(), user_agent=UA, timeout_s=timeout)  # type: ignore[arg-type]
        for agent in ("", "   ", "bad\r\nagent", "non-ascii-é"):
            with self.assertRaises(ValueError):
                ac.AlpacaClient("paper", credential(), user_agent=agent)

    def test_refused_paths(self) -> None:
        client = ac.AlpacaClient("paper", credential(), user_agent=UA)
        for path in ("/v2/orders", "/v2/account", "/v2/positions", "/v2/positions/NVDA", "/v2/options/contracts/SPY",
                     "/v2/clock/", "v2/clock", "/v2/clock?x=1", "/v3/clock", "/v2/stocks/snapshots",
                     "/v2/stocks/trades/latest", "/v1beta1/options/snapshots/", "/v1beta1/options/snapshots/spy",
                     "/v1beta1/options/snapshots/../v2/orders", "/v1beta1/options/snapshots/{underlying}",
                     "/v1beta1/options/snapshots/SPY/extra", "/v2/watchlists", "/v1/markets/quotes"):
            with self.assertRaises(ac.AlpacaError) as caught:
                client.url_for(path, {})
            self.assertEqual("refused_path", caught.exception.status, path)
            self.assertEqual(path, caught.exception.path)

    def test_repr_is_safe(self) -> None:
        client = ac.AlpacaClient("paper", credential(), user_agent=UA)
        text = repr(client)
        self.assertIn("data.alpaca.markets", text)
        self.assertIn("paper-api.alpaca.markets", text)
        self.assertIn("Credential(source=env)", text)
        self.assertNotIn(KEY_ID, text)
        self.assertNotIn(SECRET, text)
        self.assertFalse(hasattr(client, "__dict__"))

    def test_constructor_performs_no_io(self) -> None:
        with mock.patch("http.client.HTTPConnection.connect", autospec=True) as connect, \
                mock.patch("http.client.HTTPConnection.request", autospec=True) as request, \
                mock.patch("ssl.create_default_context", autospec=True) as context, \
                mock.patch("socket.getaddrinfo", autospec=True) as resolve:
            ac.AlpacaClient("paper", credential(), user_agent=UA)
            ac.AlpacaClient("loopback:1", credential(), user_agent=UA)
            ac.AlpacaClient("paper", None, user_agent=UA, transport=offline(now=NOW))
        self.assertEqual(0, connect.call_count)
        self.assertEqual(0, request.call_count)
        self.assertEqual(0, context.call_count)
        self.assertEqual(0, resolve.call_count)

    def test_https_transport_verifies_certificates(self) -> None:
        transport = ac.UrllibTransport("https")
        context = transport.tls_context
        self.assertIsNotNone(context)
        self.assertTrue(context.check_hostname)
        self.assertEqual("CERT_REQUIRED", context.verify_mode.name)
        self.assertEqual("TLSv1_2", context.minimum_version.name)
        self.assertIsNone(ac.UrllibTransport("http").tls_context)
        with self.assertRaises(ValueError):
            ac.UrllibTransport("ftp")


# --------------------------------------------------------------------------------------------
# OfflineTransport over the synthetic set
# --------------------------------------------------------------------------------------------


class OfflineTransportTest(unittest.TestCase):
    def test_fixture_file_names(self) -> None:
        cases = (
            ("/v2/stocks/quotes/latest", {"symbols": "wmt,SPY", "feed": "iex"}, "stock_quotes__SPY,WMT.json"),
            ("/v1beta1/options/quotes/latest", {"symbols": "WMT261120C00105000,nvda261120c00150000",
                                                "feed": "indicative"},
             "option_quotes__NVDA261120C00150000,WMT261120C00105000.json"),
            ("/v1beta1/options/snapshots/WMT", {"feed": "indicative", "expiration_date": "2026-11-20", "limit": "1000"},
             "snapshots__WMT__2026-11-20.json"),
            ("/v1beta1/options/snapshots/WMT", {"expiration_date": "2026-11-20", "page_token": "p2"},
             "snapshots__WMT__2026-11-20__page-p2.json"),
            ("/v1beta1/options/snapshots/SPY", {"feed": "indicative"}, "snapshots__SPY.json"),
            ("/v2/options/contracts", {"underlying_symbols": "wmt", "expiration_date": "2026-11-20", "status": "active",
                                       "limit": "10000"}, "contracts__WMT__2026-11-20.json"),
            ("/v2/options/contracts", {"underlying_symbols": "SPY", "type": "call", "expiration_date_gte": "2026-11-03",
                                       "expiration_date_lte": "2027-12-08"}, "contracts__SPY__2026-11-03__2027-12-08.json"),
            ("/v2/options/contracts", {"underlying_symbols": "SPY", "expiration_date_gte": "2026-11-03",
                                       "expiration_date_lte": "2027-12-08", "page_token": "p2"},
             "contracts__SPY__2026-11-03__2027-12-08__page-p2.json"),
            ("/v2/stocks/bars", {"symbols": "spy", "timeframe": "1Day", "start": "2026-10-27", "end": "2026-11-02",
                                 "adjustment": "raw", "feed": "iex"}, "bars__SPY__2026-10-27__2026-11-02.json"),
            ("/v2/clock", {}, "clock.json"),
            ("/v2/calendar", {"start": "2026-11-01", "end": "2026-11-30"}, "calendar__2026-11-01__2026-11-30.json"),
        )
        for path, params, expected in cases:
            self.assertEqual(expected, ac.fixture_file_name(path, params), (path, params))
            self.assertEqual(expected, expected_name(path, params), (path, params))
        for path, params in (("/v2/orders", {}), ("/v1/markets/quotes", {}),
                             ("/v2/clock", {"page_token": "../x"}), ("/v2/stocks/bars", {"page_token": "a/b=="})):
            with self.assertRaises(ValueError):
                ac.fixture_file_name(path, params)

    def test_the_set_directory_and_bad_sets_or_names_are_refused(self) -> None:
        self.assertEqual(ALPACA_SET, offline(now=NOW).set_dir)
        with self.assertRaises(ValueError):
            offline("no_such_set", now=NOW)
        for bad in ("../regular", "regular/", "", "regular;rm"):
            with self.assertRaises(ValueError):
                offline(bad, now=NOW)
        with self.assertRaises(ValueError):
            offline(now=datetime(2026, 11, 3, 19, 50))  # naive

    def test_default_now_is_the_set_clock_timestamp(self) -> None:
        transport = offline()
        self.assertEqual(NOW, transport.receipt_utc)
        client = ac.AlpacaClient("paper", None, user_agent=UA, transport=transport)
        self.assertEqual(NOW, client.clock().receipt.receipt_utc)

    def test_stock_quote(self) -> None:
        receipts: List[mt.RequestReceipt] = []
        result = offline_client(on_request=receipts.append).quotes(["SPY"])
        self.assertEqual(["SPY"], sorted(result.quotes))
        self.assertEqual(([], ()), (result.unmatched, result.not_returned))
        spy = result.quotes["SPY"]
        self.assertIsInstance(spy, ac.Quote)
        self.assertEqual("stock", spy.type)
        self.assertFalse(spy.is_option)
        self.assertEqual(Decimal("648.10"), spy.bid)
        self.assertEqual(Decimal("648.14"), spy.ask)
        self.assertEqual("648.1", str(spy.bid))  # Decimal built from the JSON text, never a float
        self.assertEqual(Decimal("648.12"), spy.mid)
        self.assertEqual((3, 2, "V", "V"), (spy.bidsize, spy.asksize, spy.bidexch, spy.askexch))
        self.assertEqual((STAMP, STAMP, STAMP), (spy.bid_date, spy.ask_date, spy.as_of))
        for name in ("last", "volume", "prevclose", "close", "description", "trade_date", "greeks", "contract_size",
                     "underlying", "strike", "open_interest"):
            self.assertIsNone(dataclasses.asdict(spy)[name], name)
        self.assertEqual({"symbol": "SPY", "type": "stock", "bid": 648.1, "ask": 648.14, "bidsize": 3, "asksize": 2,
                          "bidexch": "V", "askexch": "V", "bid_date": mt.to_epoch_ms(STAMP),
                          "ask_date": mt.to_epoch_ms(STAMP)}, spy.raw)
        self.assertEqual(spy, mt.Quote.from_record(json.loads(json.dumps(spy.raw))),
                         "the record round-trips through JSON (what the cache stores) to the same quote")
        self.assertEqual([result.receipt], receipts)
        receipt = result.receipt
        self.assertEqual(("/v2/stocks/quotes/latest", 1, NOW, "ok", 200, 0),
                         (receipt.path, receipt.symbols_count, receipt.receipt_utc, receipt.status,
                          receipt.http_status, receipt.elapsed_ms))
        self.assertIsNone(receipt.ratelimit_allowed)
        self.assertIsNone(receipt.ratelimit_expiry_utc)

    def test_option_quote_fields_come_from_the_occ_symbol(self) -> None:
        result = offline_client().quotes(["wmt261120c00105000"])
        option = result.quotes["WMT261120C00105000"]
        self.assertTrue(option.is_option)
        self.assertEqual(("WMT", Decimal("105"), date(2026, 11, 20), "call", "WMT"),
                         (option.underlying, option.strike, option.expiration_date, option.option_type,
                          option.root_symbol))
        self.assertEqual("105", str(option.strike))
        self.assertIsNone(option.contract_size, "the latest-quotes path publishes no deliverable")
        self.assertIsNone(option.greeks)
        self.assertEqual(Decimal("3.425"), option.mid)
        self.assertEqual((46, 28, STAMP), (option.bidsize, option.asksize, option.as_of))
        self.assertEqual("/v1beta1/options/quotes/latest", result.receipt.path)

    def test_six_symbols_are_two_requests_stocks_first(self) -> None:
        receipts: List[mt.RequestReceipt] = []
        result = offline_client(on_request=receipts.append).quotes(SIX)
        self.assertEqual(sorted(SIX), sorted(result.quotes))
        self.assertEqual(([], ()), (result.unmatched, result.not_returned))
        self.assertEqual([("/v2/stocks/quotes/latest", 3), ("/v1beta1/options/quotes/latest", 3)],
                         [(receipt.path, receipt.symbols_count) for receipt in result.receipts])
        self.assertEqual(list(result.receipts), receipts)
        self.assertEqual(result.receipts[-1], result.receipt)

    def test_duplicates_collapse_and_a_lone_string_is_one_symbol(self) -> None:
        result = offline_client().quotes(["SPY", "spy", " SPY "])
        self.assertEqual(1, result.receipt.symbols_count)
        self.assertEqual(["SPY"], sorted(result.quotes))
        self.assertEqual(["WMT"], sorted(offline_client().quotes("WMT").quotes))
        polygon = offline_client().quotes(["O:WMT261120C00105000"])
        self.assertEqual(["WMT261120C00105000"], sorted(polygon.quotes), "an OCC symbol is sent compact")

    def test_a_symbol_alpaca_does_not_quote_is_not_returned(self) -> None:
        only = offline_client().quotes(["SPX"])
        self.assertEqual(({}, [], ("SPX",)), (only.quotes, only.unmatched, only.not_returned))
        mixed = offline_client().quotes(["SPY", "SPX"])
        self.assertEqual((["SPY"], [], ("SPX",)), (sorted(mixed.quotes), mixed.unmatched, mixed.not_returned))

    def test_option_chain_merges_the_listing_and_the_snapshot_pages(self) -> None:
        receipts: List[mt.RequestReceipt] = []
        result = offline_client(on_request=receipts.append).option_chain("wmt", EXP)
        self.assertEqual(sorted(wmt_symbol(k, r) for k in WMT_STRIKES for r in ("call", "put")),
                         [quote.symbol for quote in result.options])
        self.assertTrue(all(quote.is_option and quote.greeks is None for quote in result.options))
        self.assertTrue(all(quote.contract_size == 100 for quote in result.options))
        anchor = [quote for quote in result.options if quote.symbol == "WMT261120C00105000"][0]
        standalone = offline_client().quotes(["WMT261120C00105000"]).quotes["WMT261120C00105000"]
        self.assertEqual((standalone.bid, standalone.ask), (anchor.bid, anchor.ask))
        self.assertEqual(("WMT", "WMT", Decimal("105"), "call", EXP, 5755),
                         (anchor.underlying, anchor.root_symbol, anchor.strike, anchor.option_type,
                          anchor.expiration_date, anchor.open_interest))
        self.assertEqual((Decimal("3.4"), 5, datetime(2026, 11, 3, 19, 30, 58, 123000, tzinfo=UTC), STAMP),
                         (anchor.last, anchor.last_volume, anchor.trade_date, anchor.as_of))
        unquoted = [quote for quote in result.options if quote.symbol == "WMT261120P00110000"][0]
        self.assertEqual((None, None, None, 100), (unquoted.bid, unquoted.ask, unquoted.as_of, unquoted.contract_size),
                         "a listed contract without a snapshot is an option with no quote")
        self.assertEqual([("/v2/options/contracts", "ok"), ("/v1beta1/options/snapshots/WMT", "ok"),
                          ("/v1beta1/options/snapshots/WMT", "ok")], [(r.path, r.status) for r in receipts])
        self.assertEqual(tuple(receipts), result.receipts)
        self.assertEqual(receipts[-1], result.receipt)
        self.assertEqual(6, len(result.raw["option_contracts"]))
        self.assertEqual(5, len(result.raw["snapshots"]))

    def test_option_expirations_follow_the_pages_and_deduplicate(self) -> None:
        receipts: List[mt.RequestReceipt] = []
        result = offline_client(on_request=receipts.append).option_expirations("SPY", EXPIRATIONS_START)
        self.assertEqual([date(2026, 11, 20), date(2026, 12, 18), date(2027, 3, 19)], result.dates)
        self.assertEqual(2, len(receipts), "two pages, two requests (the empty token ends the listing)")
        self.assertEqual(tuple(receipts), result.receipts)
        with self.assertRaises(ValueError):
            offline_client().option_expirations("SPY", datetime(2026, 11, 3, tzinfo=UTC))  # type: ignore[arg-type]
        for window in (0, 1101, True, "400"):
            with self.assertRaises(ValueError):
                offline_client().option_expirations("SPY", EXPIRATIONS_START, window_days=window)  # type: ignore[arg-type]

    def test_option_strikes(self) -> None:
        result = offline_client().option_strikes("WMT", EXP)
        self.assertEqual([Decimal("100"), Decimal("105"), Decimal("110")], result.strikes)
        self.assertTrue(all(isinstance(strike, Decimal) for strike in result.strikes))

    def test_history_bars_carry_their_eastern_dates(self) -> None:
        result = offline_client().history("SPY", date(2026, 10, 27), date(2026, 11, 2))
        self.assertEqual([date(2026, 10, 27), date(2026, 10, 28), date(2026, 10, 29), date(2026, 10, 30),
                          date(2026, 11, 2)], [bar.date for bar in result.bars])
        last = result.bars[-1]
        self.assertIsInstance(last, ac.Bar)
        self.assertEqual((Decimal("645.90"), 55600000), (last.close, last.volume))
        self.assertEqual({"date": "2026-11-02", "open": 644.9, "high": 647.4, "low": 643.9, "close": 645.9,
                          "volume": 55600000}, last.raw)
        self.assertEqual("/v2/stocks/bars", result.receipt.path)
        with self.assertRaises(ValueError):
            offline_client().history("SPY", date(2026, 11, 2), date(2026, 10, 27))
        with self.assertRaises(ValueError):
            offline_client().history("SPY", date(2026, 10, 27), date(2026, 11, 2), interval="hourly")

    def test_clock(self) -> None:
        result = offline_client().clock()
        self.assertEqual((date(2026, 11, 3), "open", NOW, "16:00", "closed", None),
                         (result.date, result.state, result.timestamp_utc, result.next_change, result.next_state,
                          result.description))
        self.assertEqual(("/v2/clock", 0), (result.receipt.path, result.receipt.symbols_count))
        self.assertTrue(result.raw["is_open"])

    def test_calendar_fills_every_day_of_the_month(self) -> None:
        result = offline_client().calendar(11, 2026)
        self.assertEqual((11, 2026), (result.month, result.year))
        self.assertEqual(30, len(result.days))
        by_date = {day.date: day for day in result.days}
        self.assertEqual(20, sum(1 for day in result.days if day.status == "open"))
        thanksgiving = by_date[date(2026, 11, 26)]
        self.assertEqual(("closed", None, None), (thanksgiving.status, thanksgiving.open, thanksgiving.premarket))
        self.assertEqual("closed", by_date[date(2026, 11, 1)].status, "a weekend is closed")
        early = by_date[date(2026, 11, 27)]
        self.assertEqual(("open", "13:00", "17:00"), (early.status, early.open.end.strftime("%H:%M"),
                                                      early.postmarket.end.strftime("%H:%M")))
        regular = by_date[date(2026, 11, 3)]
        self.assertEqual(("04:00", "09:30", "16:00", "20:00"),
                         (regular.premarket.start.strftime("%H:%M"), regular.open.start.strftime("%H:%M"),
                          regular.open.end.strftime("%H:%M"), regular.postmarket.end.strftime("%H:%M")))
        for month, year in ((0, 2026), (13, 2026), (11, 99), ("11", 2026)):
            with self.assertRaises(ValueError):
                offline_client().calendar(month, year)  # type: ignore[arg-type]

    def test_unknown_key_is_a_network_error_with_the_brief_message(self) -> None:
        receipts: List[mt.RequestReceipt] = []
        client = offline_client(on_request=receipts.append)
        with self.assertRaises(ac.AlpacaError) as caught:
            client.quotes(["SPY", "FOO"])
        err = caught.exception
        self.assertEqual(("network", "no offline fixture for /v2/stocks/quotes/latest?FOO,SPY", None),
                         (err.status, err.message, err.http_status))
        self.assertEqual("/v2/stocks/quotes/latest", err.path)
        self.assertEqual([("network", NOW, None, 2)], [(r.status, r.receipt_utc, r.http_status, r.symbols_count)
                                                       for r in receipts])
        with self.assertRaises(ac.AlpacaError) as caught:
            client.option_chain("SPY", EXP)
        self.assertEqual("no offline fixture for /v2/options/contracts?SPY__2026-11-20", caught.exception.message)
        with self.assertRaises(ac.AlpacaError) as caught:
            client.calendar(12, 2026)
        self.assertEqual("no offline fixture for /v2/calendar?2026-12-01__2026-12-31", caught.exception.message)
        with self.assertRaises(ac.AlpacaError) as caught:
            offline(now=NOW).fetch("https://paper-api.alpaca.markets/v2/clock/extra", {}, 1.0)
        self.assertEqual("network", caught.exception.status)

    def test_bad_symbol_sends_nothing(self) -> None:
        receipts: List[mt.RequestReceipt] = []
        client = offline_client(on_request=receipts.append)
        for bad in ("SPY WMT", "", "SPY,WMT", "a" * 33, 42, "SP\nY", "SPY\nWMT"):
            with self.assertRaises(ac.AlpacaError) as caught:
                client.quotes(["SPY", bad])  # type: ignore[list-item]
            self.assertEqual("bad_symbol", caught.exception.status, repr(bad))
        with self.assertRaises(ac.AlpacaError) as caught:
            client.option_chain("SPY WMT", EXP)
        self.assertEqual("bad_symbol", caught.exception.status)
        with self.assertRaises(ac.AlpacaError) as caught:
            client.option_chain("$SPX.X", EXP)  # a valid symbol, but not a valid chain path segment
        self.assertEqual("refused_path", caught.exception.status)
        with self.assertRaises(ValueError):
            client.quotes([])
        with self.assertRaises(ValueError):
            client.option_chain("SPY", datetime(2026, 11, 20, tzinfo=UTC))  # a datetime is not a date
        self.assertEqual([], receipts)

    def test_offline_transport_never_needs_a_credential_and_ignores_headers(self) -> None:
        response = offline(now=NOW).fetch("https://paper-api.alpaca.markets/v2/clock", {"APCA-API-KEY-ID": "x"}, 1.0)
        self.assertEqual((200, {}, NOW, 0), (response.status, response.headers, response.receipt_utc,
                                             response.elapsed_ms))
        self.assertEqual(read_bytes(ALPACA_SET, "clock.json"), response.body)


class OfflineShapesTest(unittest.TestCase):
    """Vendor shapes the synthetic set does not carry, written into a temporary fixture set."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-offline-")
        self.set_dir = os.path.join(self._tmp.name, "shapes")
        os.makedirs(self.set_dir)
        self.receipts: List[mt.RequestReceipt] = []
        self.client = ac.AlpacaClient("paper", None, user_agent=UA, on_request=self.receipts.append,
                                      transport=ac.OfflineTransport(self._tmp.name, "shapes", now=NOW))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, document: Any) -> None:
        with open(os.path.join(self.set_dir, name), "w", encoding="utf-8") as handle:
            if isinstance(document, str):
                handle.write(document)
            else:
                json.dump(document, handle)

    def test_empty_and_null_answers(self) -> None:
        self.write("stock_quotes__SPY.json", {"quotes": None})
        self.assertEqual(("SPY",), self.client.quotes(["SPY"]).not_returned)
        self.write("stock_quotes__WMT.json", {"quotes": {}})
        self.assertEqual(("WMT",), self.client.quotes(["WMT"]).not_returned)
        self.write("bars__X__2026-11-02__2026-11-02.json", {"bars": {}, "next_page_token": None})
        self.assertEqual([], self.client.history("X", date(2026, 11, 2), date(2026, 11, 2)).bars)
        self.write("bars__Y__2026-11-02__2026-11-02.json", {"bars": None})
        self.assertEqual([], self.client.history("Y", date(2026, 11, 2), date(2026, 11, 2)).bars)
        self.write("contracts__X__2026-11-20.json", {"option_contracts": None, "next_page_token": None})
        self.write("snapshots__X__2026-11-20.json", {"snapshots": {}, "next_page_token": None})
        self.assertEqual([], self.client.option_chain("X", EXP).options)
        self.assertEqual([], self.client.option_strikes("X", EXP).strikes)
        self.write("calendar__2026-12-01__2026-12-31.json", [])
        self.assertEqual({"closed"}, {day.status for day in self.client.calendar(12, 2026).days})

    def test_decode_statuses(self) -> None:
        cases = (
            ("stock_quotes__A.json", {"quotes": {"A": quote("abc", 1.0)}}, "bid"),
            ("stock_quotes__B.json", "not json at all", "not JSON"),
            ("stock_quotes__C.json", ["a", "list"], "not a JSON object"),
            ("stock_quotes__D.json", {"other": 1}, "quotes"),
            ("stock_quotes__E.json", {"quotes": {"E": "text"}}, "expected a JSON object"),
            ("stock_quotes__F.json", '{"quotes": {"F": {"bp": NaN, "ap": 1.0, "t": "%s"}}}' % T, "not JSON"),
            ("stock_quotes__G.json", b"\xff\xfe".decode("latin-1"), "not JSON"),
            ("stock_quotes__H.json", {"quotes": {"H": quote(1.0, 1.1, t="yesterday")}}, "RFC 3339"),
            ("stock_quotes__I.json", {"quotes": {"I": quote(True, 1.1)}}, "bid"),
            ("stock_quotes__J.json", {"quotes": []}, "quotes"),
        )
        for name, document, needle in cases:
            self.write(name, document)
            symbol = name[len("stock_quotes__"):-len(".json")]
            before = len(self.receipts)
            with self.assertRaises(ac.AlpacaError) as caught:
                self.client.quotes([symbol])
            err = caught.exception
            self.assertEqual("decode", err.status, name)
            self.assertIn(needle, err.message, name)
            self.assertEqual(200, err.http_status, name)
            self.assertEqual(before + 1, len(self.receipts), name)
            self.assertEqual(("decode", 200), (self.receipts[-1].status, self.receipts[-1].http_status), name)

    def test_an_option_quote_keyed_by_a_non_occ_symbol_is_decode(self) -> None:
        self.write("option_quotes__WMT261120C00105000.json", {"quotes": {"WMT": quote(1.0, 1.1)}})
        with self.assertRaises(ac.AlpacaError) as caught:
            self.client.quotes(["WMT261120C00105000"])
        self.assertEqual("decode", caught.exception.status)
        self.assertIn("OCC", caught.exception.message)

    def test_decode_message_is_capped_at_200_printable_characters(self) -> None:
        self.write("stock_quotes__LONG.json", "<html>" + "x" * 1000 + "\n\t</html>")
        with self.assertRaises(ac.AlpacaError) as caught:
            self.client.quotes(["LONG"])
        message = caught.exception.message
        self.assertTrue(message.startswith("HTTP 200 body is not JSON"), message)
        self.assertLessEqual(len(message.rsplit("): ", 1)[1]), 200 + len("..."))
        self.assertNotIn("\n", message)
        self.assertNotIn("\t", message)

    def test_contract_fields_must_agree_with_the_occ_symbol(self) -> None:
        self.write("snapshots__X__2026-11-20.json", {"snapshots": {}, "next_page_token": None})
        for bad in (contract("X261120C00100000", "105", "call", underlying="X", root="X"),
                    contract("X261120C00100000", "100", "put", underlying="X", root="X"),
                    contract("X261120C00100000", "100", "call", expiration="2026-11-27", underlying="X", root="X"),
                    contract("X261120C00100000", "100", "straddle", underlying="X", root="X"),
                    dict(contract("X261120C00100000", "100", "call", underlying="X", root="X"), symbol="X"),
                    dict(contract("X261120C00100000", "100", "call", underlying="X", root="X"), size="abc")):
            self.write("contracts__X__2026-11-20.json", {"option_contracts": [bad], "next_page_token": None})
            with self.assertRaises(ac.AlpacaError) as caught:
                self.client.option_chain("X", EXP)
            self.assertEqual(("decode", 200), (caught.exception.status, caught.exception.http_status), bad)

    def test_a_snapshot_of_another_expiration_is_decode(self) -> None:
        self.write("contracts__X__2026-11-20.json", {"option_contracts": [], "next_page_token": None})
        self.write("snapshots__X__2026-11-20.json", {"snapshots": {"X261127C00100000": snapshot(1.0, 1.1)},
                                                     "next_page_token": None})
        with self.assertRaises(ac.AlpacaError) as caught:
            self.client.option_chain("X", EXP)
        self.assertEqual("decode", caught.exception.status)

    def test_size_and_multiplier_that_disagree_leave_the_deliverable_unknown(self) -> None:
        listed = [contract("X261120C00100000", "100", "call", underlying="X", root="X", size="100"),
                  contract("X1261120C00100000", "100", "call", underlying="X", root="X1", size="150",
                           multiplier="100"),
                  dict(contract("X261120P00100000", "100", "put", underlying="X", root="X"), size=None)]
        self.write("contracts__X__2026-11-20.json", {"option_contracts": listed, "next_page_token": None})
        self.write("snapshots__X__2026-11-20.json", {"snapshots": {"X261120C00105000": snapshot(1.0, 1.1)},
                                                     "next_page_token": None})
        options = {quote.symbol: quote for quote in self.client.option_chain("X", EXP).options}
        self.assertEqual(100, options["X261120C00100000"].contract_size)
        self.assertIsNone(options["X1261120C00100000"].contract_size, "150 shares on a 100 multiplier is non-standard")
        self.assertEqual(("X1", "X"), (options["X1261120C00100000"].root_symbol, options["X1261120C00100000"].underlying))
        self.assertEqual(100, options["X261120P00100000"].contract_size, "the multiplier stands in for a missing size")
        unlisted = options["X261120C00105000"]
        self.assertEqual((None, "X", Decimal("1.0")), (unlisted.contract_size, unlisted.underlying, unlisted.bid),
                         "a snapshot without a listing keeps its OCC fields and no deliverable")

    def test_pages_are_bounded_and_a_repeated_token_is_refused(self) -> None:
        def page(name: str, token: Optional[str]) -> None:
            self.write(name, {"option_contracts": [contract("X261120C00100000", "100", "call", underlying="X", root="X")],
                              "next_page_token": token})

        page("contracts__X__2026-11-20.json", "t1")
        for index in range(1, ac.MAX_PAGES + 1):
            page("contracts__X__2026-11-20__page-t%d.json" % index, "t%d" % (index + 1))
        with self.assertRaises(ac.AlpacaError) as caught:
            self.client.option_strikes("X", EXP)
        self.assertEqual("incomplete", caught.exception.status)
        self.assertEqual(ac.MAX_PAGES, len(self.receipts), "every page sent was recorded, and no more were sent")
        self.assertEqual({"ok"}, {receipt.status for receipt in self.receipts})
        page("contracts__Y__2026-11-20.json", "again")
        page("contracts__Y__2026-11-20__page-again.json", "again")
        with self.assertRaises(ac.AlpacaError) as caught:
            self.client.option_strikes("Y", EXP)
        self.assertEqual("decode", caught.exception.status)
        self.write("contracts__Z__2026-11-20.json", {"option_contracts": [], "next_page_token": 7})
        with self.assertRaises(ac.AlpacaError) as caught:
            self.client.option_strikes("Z", EXP)
        self.assertEqual("decode", caught.exception.status)

    def test_clock_and_calendar_shapes(self) -> None:
        self.write("clock.json", {"timestamp": "2026-11-26T10:00:00-05:00", "is_open": False,
                                  "next_open": "2026-11-27T09:30:00-05:00", "next_close": "2026-11-27T13:00:00-05:00"})
        closed = self.client.clock()
        self.assertEqual((date(2026, 11, 26), "closed", "09:30", "open"),
                         (closed.date, closed.state, closed.next_change, closed.next_state))
        for document in ({"timestamp": "2026-11-26T10:00:00-05:00", "is_open": "true"},
                         {"timestamp": "2026-11-26T10:00:00-05:00"},
                         {"is_open": True},
                         {"timestamp": "yesterday", "is_open": True},
                         ["not", "an", "object"]):
            self.write("clock.json", document)
            with self.assertRaises(ac.AlpacaError) as caught:
                self.client.clock()
            self.assertEqual("decode", caught.exception.status, document)
        for document in ({"calendar": []},
                         [{"date": "2026-10-30", "open": "09:30", "close": "16:00"}],
                         [{"date": "2026-12-01", "open": "9:30", "close": "16:00"}],
                         [{"date": "2026-12-01", "close": "16:00"}],
                         [{"date": "2026-12-01", "open": "09:30", "close": "16:00", "session_open": "4am"}]):
            self.write("calendar__2026-12-01__2026-12-31.json", document)
            with self.assertRaises(ac.AlpacaError) as caught:
                self.client.calendar(12, 2026)
            self.assertEqual("decode", caught.exception.status, document)

    def test_batching_over_max_batch(self) -> None:
        symbols = ["%s%d" % (letter, digit) for letter in "ABCDEF" for digit in range(10)][:ac.MAX_BATCH + 7]
        first, second = symbols[:ac.MAX_BATCH], symbols[ac.MAX_BATCH:]
        for batch in (first, second):
            self.write("stock_quotes__%s.json" % ",".join(sorted(batch)),
                       {"quotes": {symbol: quote(1.0, 1.1) for symbol in batch}})
        result = self.client.quotes(symbols)
        self.assertEqual(len(symbols), len(result.quotes))
        self.assertEqual([ac.MAX_BATCH, 7], [receipt.symbols_count for receipt in result.receipts])
        self.assertEqual(result.receipts[-1], result.receipt)
        self.assertEqual(2, len(self.receipts))

    def test_on_request_exceptions_propagate(self) -> None:
        def explode(receipt: mt.RequestReceipt) -> None:
            raise RuntimeError("ledger full")

        self.write("stock_quotes__SPY.json", {"quotes": {"SPY": quote(1.0, 1.1)}})
        client = ac.AlpacaClient("paper", None, user_agent=UA, on_request=explode,
                                 transport=ac.OfflineTransport(self._tmp.name, "shapes", now=NOW))
        with self.assertRaises(RuntimeError):
            client.quotes(["SPY"])

    def test_default_now_needs_a_readable_clock(self) -> None:
        with self.assertRaises(ValueError):
            ac.OfflineTransport(self._tmp.name, "shapes")
        self.write("clock.json", {"timestamp": "2026-11-03T14:50:00-05:00", "is_open": True})
        self.assertEqual(NOW, ac.OfflineTransport(self._tmp.name, "shapes").receipt_utc)


# --------------------------------------------------------------------------------------------
# Loopback fake server
# --------------------------------------------------------------------------------------------

Responder = Callable[[str, Dict[str, str], Dict[str, str]], Tuple[int, List[Tuple[str, str]], bytes]]


def fixture_body(path: str, query: Dict[str, str]) -> Optional[bytes]:
    """The synthetic set's body for an allow-listed request, named independently of the client."""
    name = expected_name(path, query)
    if name is None:
        return None
    full = os.path.join(ALPACA_SET, name)
    if not os.path.isfile(full):
        return None
    return read_bytes(full)


class RecordingHandler(http.server.BaseHTTPRequestHandler):
    """Records every request; serves fixtures on GET; any other verb is a violation."""

    server_version = "FakeAlpaca/0"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - http.server signature
        return

    def record(self) -> None:
        with self.server.lock:  # type: ignore[attr-defined]
            self.server.requests.append({  # type: ignore[attr-defined]
                "method": self.command,
                "path": self.path,
                "headers": {key.lower(): value for key, value in self.headers.items()},
            })

    def reply(self, status: int, headers: List[Tuple[str, str]], body: bytes) -> None:
        try:
            self.send_response(status)
            for name, value in headers:
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)
        except OSError:
            return  # the client gave up (the timeout test): nothing to report

    def do_GET(self) -> None:
        self.record()
        parts = urlsplit(self.path)
        query = {key: values[0] for key, values in parse_qs(parts.query, keep_blank_values=True).items()}
        headers = {key.lower(): value for key, value in self.headers.items()}
        server = self.server
        delay = server.delay_s  # type: ignore[attr-defined]
        if delay:
            time.sleep(delay)
        responder = server.script.get(parts.path)  # type: ignore[attr-defined]
        if responder is not None:
            status, extra, body = responder(parts.path, query, headers)
        else:
            fixture = fixture_body(parts.path, query)
            if fixture is None:
                status, extra, body = 404, [], b'{"message": "endpoint not found."}'
            else:
                status, extra, body = 200, [("Content-Type", "application/json; charset=UTF-8")], fixture
        self.reply(status, extra, body)

    def refuse(self) -> None:
        self.record()
        with self.server.lock:  # type: ignore[attr-defined]
            self.server.violations.append("%s %s" % (self.command, self.path))  # type: ignore[attr-defined]
        self.reply(405, [("Allow", "GET")], b"")

    def do_POST(self) -> None:
        self.refuse()

    def do_PUT(self) -> None:
        self.refuse()

    def do_PATCH(self) -> None:
        self.refuse()

    def do_DELETE(self) -> None:
        self.refuse()

    def do_HEAD(self) -> None:
        self.refuse()

    def do_OPTIONS(self) -> None:
        self.refuse()


class FakeAlpaca(http.server.ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), RecordingHandler)
        self.lock = threading.Lock()
        self.requests: List[Dict[str, Any]] = []
        self.violations: List[str] = []
        self.script: Dict[str, Responder] = {}
        self.delay_s = 0.0

    @property
    def port(self) -> int:
        return int(self.server_address[1])

    def handle_error(self, request: Any, client_address: Any) -> None:
        return  # a connection the client abandoned is not a test failure


def static(status: int, body: bytes, headers: Optional[List[Tuple[str, str]]] = None) -> Responder:
    def respond(path: str, query: Dict[str, str], request_headers: Dict[str, str]) -> Tuple[int, List[Tuple[str, str]], bytes]:
        return status, list(headers or []), body
    return respond


def free_port() -> int:
    """A port that was bound a moment ago and is now closed (connections are refused)."""
    probe = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    port = int(probe.server_address[1])
    probe.server_close()
    return port


UNAUTHORIZED = b'{"message":"unauthorized."}'
FORBIDDEN = b'{"message":"forbidden."}'
TOO_MANY = b'{"message":"too many requests."}'
SUBSCRIPTION = b'{"code":42210000,"message":"subscription does not permit querying recent SIP data"}'
RATE_HEADERS = [("X-RateLimit-Limit", "200"), ("X-RateLimit-Remaining", "0"), ("X-RateLimit-Reset", "1793735460")]


class LoopbackTest(unittest.TestCase):
    server: FakeAlpaca
    thread: threading.Thread

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = FakeAlpaca()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        with self.server.lock:
            del self.server.requests[:]
            del self.server.violations[:]
        self.server.script.clear()
        self.server.delay_s = 0.0
        self.receipts: List[mt.RequestReceipt] = []
        self.client = self.make_client()

    def tearDown(self) -> None:
        self.server.script.clear()
        self.server.delay_s = 0.0
        self.assertEqual([], self.server.violations, "a non-GET request reached the fake server")

    def make_client(self, **kwargs: Any) -> ac.AlpacaClient:
        kwargs.setdefault("user_agent", UA)
        kwargs.setdefault("on_request", self.receipts.append)
        return ac.AlpacaClient("loopback:%d" % self.server.port, credential(), **kwargs)

    def requests(self) -> List[Dict[str, Any]]:
        with self.server.lock:
            return list(self.server.requests)

    def test_get_with_exactly_the_documented_headers_and_no_body(self) -> None:
        result = self.client.quotes(["SPY"])
        self.assertEqual(Decimal("648.10"), result.quotes["SPY"].bid)
        seen = self.requests()
        self.assertEqual(1, len(seen))
        request = seen[0]
        self.assertEqual("GET", request["method"])
        self.assertEqual("/v2/stocks/quotes/latest?symbols=SPY&feed=iex", request["path"])
        headers = request["headers"]
        self.assertEqual(KEY_ID, headers["apca-api-key-id"])
        self.assertEqual(SECRET, headers["apca-api-secret-key"])
        self.assertEqual("application/json", headers["accept"])
        self.assertEqual(UA, headers["user-agent"])
        # The client's four headers plus the three urllib always adds; nothing else (no
        # Authorization, no body, no proxy or cookie header).
        self.assertEqual({"apca-api-key-id", "apca-api-secret-key", "accept", "user-agent", "host", "accept-encoding",
                          "connection"}, set(headers))
        self.assertEqual("identity", headers["accept-encoding"])
        receipt = result.receipt
        self.assertEqual(("ok", 200, "/v2/stocks/quotes/latest", 1),
                         (receipt.status, receipt.http_status, receipt.path, receipt.symbols_count))
        self.assertEqual(UTC, receipt.receipt_utc.tzinfo)
        self.assertGreaterEqual(receipt.elapsed_ms, 0)
        self.assertEqual([receipt], self.receipts)

    def test_every_endpoint_under_the_get_only_wrapper(self) -> None:
        seen: List[Tuple[str, str, Any]] = []
        patcher = mock.patch("http.client.HTTPConnection.request", autospec=True)
        mocked = patcher.start()
        self.addCleanup(patcher.stop)
        original = patcher.temp_original  # the real HTTPConnection.request, called through
        self.assertTrue(callable(original))

        def recording(connection: Any, method: str, url: str, body: Any = None, headers: Any = None,
                      **kwargs: Any) -> Any:
            seen.append((method, url, body))
            return original(connection, method, url, body, headers if headers is not None else {}, **kwargs)

        mocked.side_effect = recording
        quotes = self.client.quotes(SIX)
        chain = self.client.option_chain("WMT", EXP)
        expirations = self.client.option_expirations("SPY", EXPIRATIONS_START)
        strikes = self.client.option_strikes("WMT", EXP)
        history = self.client.history("NVDA", date(2026, 10, 27), date(2026, 11, 2))
        clock = self.client.clock()
        calendar = self.client.calendar(11, 2026)
        self.assertEqual(6, len(quotes.quotes))
        self.assertEqual(6, len(chain.options))
        self.assertIn(date(2026, 12, 18), expirations.dates)
        self.assertIn(Decimal("105"), strikes.strikes)
        self.assertEqual(5, len(history.bars))
        self.assertEqual("open", clock.state)
        self.assertEqual(30, len(calendar.days))
        # 2 quote batches + 1 listing + 2 snapshot pages + 2 expiration pages + 1 listing + bars + clock + calendar
        self.assertEqual(11, len(seen))
        self.assertEqual({"GET"}, {method for method, _url, _body in seen})
        self.assertTrue(all(body is None for _method, _url, body in seen))
        paths = [urlsplit(url).path for _method, url, _body in seen]
        self.assertEqual(["/v2/stocks/quotes/latest", "/v1beta1/options/quotes/latest", "/v2/options/contracts",
                          "/v1beta1/options/snapshots/WMT", "/v1beta1/options/snapshots/WMT", "/v2/options/contracts",
                          "/v2/options/contracts", "/v2/options/contracts", "/v2/stocks/bars", "/v2/clock",
                          "/v2/calendar"], paths)
        self.assertEqual(set(ac.ALLOWED_PATHS), {p if not p.startswith(ac.SNAPSHOTS_PREFIX)
                                                 else ac.PATH_OPTION_SNAPSHOTS for p in paths})
        queries = [parse_qs(urlsplit(url).query) for _method, url, _body in seen]
        self.assertEqual(["iex"], queries[0]["feed"])
        self.assertEqual(["indicative"], queries[1]["feed"], "the indicative feed is always sent explicitly")
        self.assertEqual(["indicative"], queries[3]["feed"])
        self.assertEqual(["p2"], queries[4]["page_token"])
        self.assertEqual((["call"], ["2026-11-03"], [EXPIRATIONS_END.isoformat()]),
                         (queries[5]["type"], queries[5]["expiration_date_gte"], queries[5]["expiration_date_lte"]))
        self.assertEqual((["1Day"], ["raw"], ["iex"]), (queries[8]["timeframe"], queries[8]["adjustment"],
                                                        queries[8]["feed"]))
        self.assertEqual((["2026-11-01"], ["2026-11-30"]), (queries[10]["start"], queries[10]["end"]))
        self.assertEqual(11, len(self.requests()))
        self.assertEqual({"GET"}, {request["method"] for request in self.requests()})
        self.assertEqual(["ok"] * 11, [receipt.status for receipt in self.receipts])
        self.assertEqual([3, 3, 1, 1, 1, 1, 1, 1, 1, 0, 0], [receipt.symbols_count for receipt in self.receipts])

    def test_rate_limit_headers_reach_the_receipt(self) -> None:
        body = read_bytes(ALPACA_SET, "clock.json")
        self.server.script["/v2/clock"] = static(200, body, [
            ("X-RateLimit-Limit", "200"), ("X-RateLimit-Remaining", "153"), ("X-RateLimit-Reset", "1793735460")])
        receipt = self.client.clock().receipt
        self.assertEqual((200, 47, 153), (receipt.ratelimit_allowed, receipt.ratelimit_used,
                                          receipt.ratelimit_available))
        self.assertEqual(datetime(2026, 11, 3, 19, 51, 0, tzinfo=UTC), receipt.ratelimit_expiry_utc)
        self.server.script["/v2/clock"] = static(200, body, [("X-RateLimit-Limit", "two hundred"),
                                                             ("X-RateLimit-Reset", "soon")])
        receipt = self.client.clock().receipt
        self.assertEqual((None, None, None, None), (receipt.ratelimit_allowed, receipt.ratelimit_used,
                                                    receipt.ratelimit_available, receipt.ratelimit_expiry_utc))

    def assert_error(self, status: str, http_status: Optional[int], call: Callable[[], Any]) -> ac.AlpacaError:
        before = len(self.receipts)
        with self.assertRaises(ac.AlpacaError) as caught:
            call()
        err = caught.exception
        self.assertEqual(status, err.status)
        self.assertEqual(http_status, err.http_status)
        self.assertEqual(before + 1, len(self.receipts))
        self.assertEqual(status, self.receipts[-1].status)
        self.assertEqual(http_status, self.receipts[-1].http_status)
        for value in (KEY_ID, SECRET):
            self.assertNotIn(value, str(err))
            self.assertNotIn(value, repr(err))
        self.assertNotIn("symbols=", err.message)
        self.assertNotIn("APCA-API", err.message)
        return err

    def test_401_and_403_are_auth(self) -> None:
        self.server.script["/v2/clock"] = static(401, UNAUTHORIZED)
        err = self.assert_error("auth", 401, self.client.clock)
        self.assertEqual('auth: HTTP 401: {"message":"unauthorized."}', str(err))
        self.assertEqual("/v2/clock", err.path)
        self.server.script["/v2/stocks/quotes/latest"] = static(403, FORBIDDEN)
        self.assert_error("auth", 403, lambda: self.client.quotes(["SPY"]))
        self.server.script["/v2/clock"] = static(403, b"")
        self.assertEqual("HTTP 403 (empty body)", self.assert_error("auth", 403, self.client.clock).message)

    def test_429_is_quota_with_the_rate_limit_headers_on_the_receipt(self) -> None:
        self.server.script["/v2/stocks/quotes/latest"] = static(429, TOO_MANY, RATE_HEADERS)
        err = self.assert_error("quota", 429, lambda: self.client.quotes(["SPY"]))
        self.assertIn("too many requests", err.message)
        receipt = self.receipts[-1]
        self.assertEqual((200, 200, 0, datetime(2026, 11, 3, 19, 51, 0, tzinfo=UTC)),
                         (receipt.ratelimit_allowed, receipt.ratelimit_used, receipt.ratelimit_available,
                          receipt.ratelimit_expiry_utc))

    def test_subscription_errors_have_their_own_status(self) -> None:
        for code in (403, 400, 422):
            self.server.script["/v1beta1/options/quotes/latest"] = static(code, SUBSCRIPTION)
            err = self.assert_error("subscription", code, lambda: self.client.quotes(["WMT261120C00105000"]))
            self.assertIn("subscription does not permit", err.message)
        self.server.script["/v2/stocks/bars"] = static(403, b'{"message":"Subscription Does Not Permit this feed"}')
        self.assert_error("subscription", 403, lambda: self.client.history("SPY", date(2026, 10, 27),
                                                                           date(2026, 11, 2)))

    def test_400_and_422_are_bad_request_and_5xx_or_404_is_http(self) -> None:
        self.server.script["/v2/stocks/quotes/latest"] = static(400, b'{"code":40010001,"message":"invalid symbol"}')
        self.assertIn("invalid symbol", self.assert_error("bad_request", 400, lambda: self.client.quotes(["SPY"])).message)
        self.server.script["/v2/options/contracts"] = static(422, b'{"message":"invalid expiration_date"}')
        self.assert_error("bad_request", 422, lambda: self.client.option_strikes("WMT", EXP))
        self.server.script["/v2/stocks/quotes/latest"] = static(500, b"<html>internal error</html>")
        self.assertIn("internal error", self.assert_error("http", 500, lambda: self.client.quotes(["SPY"])).message)
        self.server.script["/v2/stocks/quotes/latest"] = static(503, b"")
        self.assert_error("http", 503, lambda: self.client.quotes(["SPY"]))
        self.server.script["/v2/stocks/quotes/latest"] = static(204, b"")
        self.assert_error("http", 204, lambda: self.client.quotes(["SPY"]))
        self.server.script.clear()
        self.assert_error("http", 404, lambda: self.client.quotes(["NOFILE"]))

    def test_redirects_are_refused_without_a_second_request(self) -> None:
        target = "http://127.0.0.1:%d/v2/clock" % self.server.port
        for code in (301, 302, 303, 307, 308):
            self.server.script["/v2/stocks/quotes/latest"] = static(code, b"", [("Location", target)])
            before = len(self.requests())
            err = self.assert_error("refused_redirect", code, lambda: self.client.quotes(["SPY"]))
            self.assertEqual(before + 1, len(self.requests()), "a redirect must not be followed (code %d)" % code)
            self.assertEqual("/v2/stocks/quotes/latest?symbols=SPY&feed=iex", self.requests()[-1]["path"])
            self.assertIn("redirect", err.message)
            self.assertNotIn(target, err.message)
        self.server.script["/v2/clock"] = static(302, b"", [("Location", "https://api.alpaca.markets/v2/orders")])
        before = len(self.requests())
        self.assert_error("refused_redirect", 302, self.client.clock)
        self.assertEqual(before + 1, len(self.requests()))
        self.server.script["/v2/stocks/quotes/latest"] = static(304, b"")
        self.assert_error("refused_redirect", 304, lambda: self.client.quotes(["SPY"]))

    def test_non_json_200_is_decode(self) -> None:
        self.server.script["/v2/stocks/quotes/latest"] = static(200, b"<!DOCTYPE html><html>maintenance</html>")
        self.assertIn("maintenance", self.assert_error("decode", 200, lambda: self.client.quotes(["SPY"])).message)
        self.server.script["/v2/stocks/quotes/latest"] = static(200, b"\xff\xfe\x00")
        self.assert_error("decode", 200, lambda: self.client.quotes(["SPY"]))

    def test_timeout(self) -> None:
        self.server.delay_s = 0.6
        client = self.make_client(timeout_s=0.15)
        err = self.assert_error("timeout", None, lambda: client.quotes(["SPY"]))
        self.assertIn("0.1", err.message)
        self.assertIsNotNone(self.receipts[-1].elapsed_ms)
        self.server.delay_s = 0.0

    def test_connection_refused_is_network(self) -> None:
        client = ac.AlpacaClient("loopback:%d" % free_port(), credential(), user_agent=UA,
                                 on_request=self.receipts.append)
        err = self.assert_error("network", None, client.clock)
        self.assertTrue(err.message)
        self.assertEqual("/v2/clock", err.path)
        self.assertEqual(0, len(self.requests()))

    def test_either_value_echoed_in_json_escapes_in_an_error_body_is_withheld(self) -> None:
        # Raw redaction cannot see an echo written as \uXXXX escapes; decoded JSON can, so the excerpt is withheld.
        def escaped(value: str) -> str:
            return "".join("\\u%04x" % ord(char) for char in value)

        for name, value in (("key id", KEY_ID), ("secret", SECRET)):
            with self.subTest(value=name):
                body = ('{"message":"bad key %s"}' % escaped(value)).encode("ascii")
                self.server.script["/v2/clock"] = lambda path, query, headers, body=body: (403, [], body)
                err = self.assert_error("auth", 403, self.client.clock)
                self.assertEqual("HTTP 403: (body withheld: it echoes the credential)", err.message)
                self.assertNotIn(escaped(value), err.message)
                self.assertNotIn(escaped(value)[:12], err.message)
                self.assertNotIn(value, repr(err.args))
        # An ordinary JSON error body is still shown.
        self.server.script["/v2/clock"] = lambda path, query, headers: (401, [], b'{"message":"unauthorized."}')
        err = self.assert_error("auth", 401, self.client.clock)
        self.assertEqual('HTTP 401: {"message":"unauthorized."}', err.message)

    def test_both_values_echoed_by_the_server_are_redacted(self) -> None:
        def echo(path: str, query: Dict[str, str], headers: Dict[str, str]) -> Tuple[int, List[Tuple[str, str]], bytes]:
            text = "forbidden for %s / %s (%s)" % (headers.get("apca-api-key-id", ""),
                                                   headers.get("apca-api-secret-key", ""), SECRET + KEY_ID)
            return 403, [], text.encode("utf-8")

        self.server.script["/v2/clock"] = echo
        err = self.assert_error("auth", 403, self.client.clock)
        self.assertEqual("HTTP 403: forbidden for [redacted] / [redacted] ([redacted][redacted])", err.message)
        receipt_json = json.dumps([dataclasses.asdict(receipt) for receipt in self.receipts], default=str)
        for value in (KEY_ID, SECRET):
            self.assertNotIn(value, err.message)
            self.assertNotIn(value, repr(err.args))
            self.assertNotIn(value, receipt_json)
            self.assertNotIn(value, repr(self.client))
            self.assertNotIn(value, repr(self.client.credential))

    def test_either_value_echoed_in_a_200_body_is_refused_before_it_can_be_cached(self) -> None:
        # Brief non-negotiable 3: neither value ever reaches a cache file. A vendor that echoes a
        # header into a quote field would otherwise land in the cache through the record, so the
        # client refuses the whole answer as ``decode``.
        template = read_json(ALPACA_SET, "stock_quotes__SPY.json")
        for header in ("apca-api-key-id", "apca-api-secret-key"):
            def echo(path: str, query: Dict[str, str], headers: Dict[str, str],
                     name: str = header) -> Tuple[int, List[Tuple[str, str]], bytes]:
                document = json.loads(json.dumps(template))
                document["quotes"]["SPY"]["z"] = "echo %s" % headers.get(name, "")
                return 200, [("Content-Type", "application/json")], json.dumps(document).encode("utf-8")

            self.server.script["/v2/stocks/quotes/latest"] = echo
            err = self.assert_error("decode", 200, lambda: self.client.quotes(["SPY"]))
            self.assertIn("echoes the credential", err.message, header)
        # The same values written as JSON escapes are invisible in the raw bytes and still refused
        # once the body is decoded (one in a value, one in a key).
        for value, place in ((KEY_ID, "value"), (SECRET, "key")):
            document = json.loads(json.dumps(template))
            if place == "value":
                document["quotes"]["SPY"]["z"] = "MARKER"
            else:
                document["MARKER"] = 1
            escaped = "".join("\\u%04x" % ord(ch) for ch in value)
            body = json.dumps(document).replace("MARKER", escaped).encode("utf-8")
            self.assertNotIn(value.encode("utf-8"), body)
            self.server.script["/v2/stocks/quotes/latest"] = static(200, body)
            self.assertIn("echoes the credential",
                          self.assert_error("decode", 200, lambda: self.client.quotes(["SPY"])).message)
        # An echo inside a calendar list is refused too.
        days = trading_days(2026, 11)
        days[0]["settlement_date"] = SECRET
        self.server.script["/v2/calendar"] = static(200, json.dumps(days).encode("utf-8"))
        self.assert_error("decode", 200, lambda: self.client.calendar(11, 2026))
        # An honest 200 from the same server is served as before.
        self.server.script.clear()
        self.assertEqual(Decimal("648.1"), self.client.quotes(["SPY"]).quotes["SPY"].bid)

    def test_batching_over_max_batch_sends_consecutive_requests(self) -> None:
        def synth(path: str, query: Dict[str, str], headers: Dict[str, str]) -> Tuple[int, List[Tuple[str, str]], bytes]:
            return 200, [], json.dumps({"quotes": {s: quote(1.0, 1.1) for s in query["symbols"].split(",")}}).encode()

        self.server.script["/v2/stocks/quotes/latest"] = synth
        symbols = ["B%03d" % index for index in range(120)]
        result = self.client.quotes(symbols)
        self.assertEqual(set(symbols), set(result.quotes))
        self.assertEqual(([], ()), (result.unmatched, result.not_returned))
        sent: List[str] = []
        for request in self.requests():
            batch = parse_qs(urlsplit(request["path"]).query)["symbols"][0].split(",")
            self.assertLessEqual(len(batch), ac.MAX_BATCH)
            sent.extend(batch)
        self.assertEqual(symbols, sent, "batches are consecutive and keep the request order")
        self.assertEqual([50, 50, 20], [receipt.symbols_count for receipt in result.receipts])
        self.assertEqual(3, len(self.receipts))

    def test_failure_in_a_later_batch_raises_after_recording_the_earlier_receipts(self) -> None:
        calls: List[int] = []

        def flaky(path: str, query: Dict[str, str], headers: Dict[str, str]) -> Tuple[int, List[Tuple[str, str]], bytes]:
            calls.append(1)
            if len(calls) == 2:
                return 429, RATE_HEADERS, TOO_MANY
            return 200, [], json.dumps({"quotes": {s: quote(1.0, 1.1) for s in query["symbols"].split(",")}}).encode()

        self.server.script["/v2/stocks/quotes/latest"] = flaky
        with self.assertRaises(ac.AlpacaError) as caught:
            self.client.quotes(["C%03d" % index for index in range(60)])
        self.assertEqual("quota", caught.exception.status)
        self.assertEqual(["ok", "quota"], [receipt.status for receipt in self.receipts])
        self.assertEqual(2, len(self.requests()))

    def test_symbols_are_normalised_and_the_query_is_encoded(self) -> None:
        self.client.quotes(["spy", " wmt ", "SPY"])
        self.assertEqual("/v2/stocks/quotes/latest?symbols=SPY%2CWMT&feed=iex", self.requests()[-1]["path"])
        self.assertEqual(2, self.receipts[-1].symbols_count)
        with self.assertRaises(ac.AlpacaError) as caught:
            self.client.calendar(3, 2026)  # no March fixture: the fake server answers 404
        self.assertEqual(("http", 404), (caught.exception.status, caught.exception.http_status))
        self.assertEqual("/v2/calendar?start=2026-03-01&end=2026-03-31", self.requests()[-1]["path"])

    def test_bad_symbol_and_refused_paths_send_nothing(self) -> None:
        with self.assertRaises(ac.AlpacaError) as caught:
            self.client.quotes(["SPY", "SPY WMT"])
        self.assertEqual("bad_symbol", caught.exception.status)
        for path in ("/v2/orders", "/v2/account", "/v2/positions", "/v1beta1/options/snapshots/../../v2/orders"):
            with self.assertRaises(ac.AlpacaError) as caught:
                self.client.get_json(path, {})
            self.assertEqual("refused_path", caught.exception.status, path)
        self.assertEqual([], self.requests())
        self.assertEqual([], self.receipts)

    def test_get_json_is_the_single_request_primitive(self) -> None:
        document, receipt = self.client.get_json("/v2/clock", {})
        self.assertIs(True, document["is_open"])
        self.assertEqual(("ok", 200, 0), (receipt.status, receipt.http_status, receipt.symbols_count))
        self.assertEqual([receipt], self.receipts)
        with self.assertRaises(TypeError):
            self.client.get_json("/v2/stocks/quotes/latest", {"symbols": 5})  # type: ignore[dict-item]

    def test_transport_error_carries_a_receipt_time_for_the_ledger(self) -> None:
        err = ac.TransportError("network", "simulated", receipt_utc=NOW, elapsed_ms=3)
        self.assertIsInstance(err, ac.AlpacaError)
        self.assertEqual((NOW, 3, "network: simulated"), (err.receipt_utc, err.elapsed_ms, str(err)))


# --------------------------------------------------------------------------------------------
# The AST gate over the finished client and this test module
# --------------------------------------------------------------------------------------------


class NoWritePathGateTest(unittest.TestCase):
    def test_client_is_the_registered_network_module(self) -> None:
        self.assertEqual("scripts/aegis_desk/alpaca_client.py", check_no_write_paths.NETWORK_CLIENT)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, check_no_write_paths.NETWORK_CLIENT)))

    def test_client_and_this_module_have_no_findings(self) -> None:
        for rel in ("scripts/aegis_desk/alpaca_client.py", "tests/engine/test_alpaca_client.py"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
                source = handle.read()
            findings = check_no_write_paths.ModuleGate(rel, source).run()
            self.assertEqual([], [finding.render() for finding in findings], rel)

    def test_ast_gate_passes_over_the_whole_tree(self) -> None:
        findings, scanned = check_no_write_paths.scan_python(ROOT)
        self.assertEqual([], [finding.render() for finding in findings])
        self.assertGreater(scanned, 40)

    def test_client_source_names_only_the_literal_get(self) -> None:
        with open(os.path.join(ROOT, "scripts", "aegis_desk", "alpaca_client.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('method="GET"', source)
        for verb in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"'):
            self.assertNotIn(verb, source)
        self.assertNotIn("data=", source)
        for path in ("/v2/orders", "/v2/positions", "/v2/account"):
            self.assertNotIn(path, source, "no order, position or account path is even named in the client")
        # The same facts read from the AST, the way the gate reads them: every Request() call
        # carries the literal method, no attribute that decides a request's verb, body or target
        # is ever assigned, and nothing subclasses Request (the redirect handler is the one
        # urllib class the client derives from).
        tree = ast.parse(source)
        request_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                         and ast.unparse(node.func) == "urllib.request.Request"]
        self.assertEqual(1, len(request_calls))
        for call in request_calls:
            self.assertEqual(["GET"], [kw.value.value for kw in call.keywords if kw.arg == "method"])
            self.assertEqual([], [kw for kw in call.keywords if kw.arg == "data"])
            self.assertEqual(1, len(call.args))
        assigned = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                targets = node.targets
            elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
                targets = [node.target]
            else:
                continue
            assigned.extend(target.attr for target in targets if isinstance(target, ast.Attribute))
        for attr in check_no_write_paths.MUTATION_ATTRS + check_no_write_paths.TARGET_ATTRS:
            self.assertNotIn(attr, assigned)
        bases = [ast.unparse(base) for node in ast.walk(tree) if isinstance(node, ast.ClassDef) for base in node.bases]
        self.assertNotIn("urllib.request.Request", bases)
        self.assertIn("urllib.request.HTTPRedirectHandler", bases)

    def test_no_proxy_handler_is_ever_installed(self) -> None:
        with mock.patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
                                          "HTTP_PROXY": "http://127.0.0.1:9", "http_proxy": "http://127.0.0.1:9"}):
            for scheme in ("https", "http"):
                director = ac.UrllibTransport(scheme)._director()
                kinds = {type(handler).__name__ for handler in director.handlers}
                self.assertNotIn("ProxyHandler", kinds, scheme)
                self.assertIn("RefuseRedirects", kinds, scheme)
                self.assertFalse(any(name.startswith("HTTPRedirect") for name in kinds), scheme)


if __name__ == "__main__":
    unittest.main()
