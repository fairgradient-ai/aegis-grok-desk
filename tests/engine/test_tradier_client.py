"""The GET-only Tradier client (``aegis_desk.tradier_client``): tokens, errors, the offline
transport over the fixture sets, and a loopback fake server that fails on any non-GET request.

The loopback server (``http.server.ThreadingHTTPServer`` on 127.0.0.1:0) records every request
line and header set, serves the ``regular`` fixture bodies for the allow-listed paths, answers
404 elsewhere and records a violation (answering 405) for POST, PUT, PATCH and DELETE; every
test asserts the violation list is empty. A second, independent proof patches
``http.client.HTTPConnection.request`` by dotted name (tests may not import ``http.client``)
and asserts ``method == "GET"`` and ``body is None`` across a journey that touches every
endpoint. The token never appears in ``str(err)``, ``repr(token)``, ``repr(client)`` or a
receipt, even when the server echoes the Authorization header back in an error body.
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
import stat
import tempfile
import threading
import time
import unittest
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Tuple
from unittest import mock
from urllib.parse import parse_qs, urlsplit

from aegis_desk import market_types as mt
from aegis_desk import tradier_client as tc

UTC = timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TRADIER_FIXTURES = os.path.join(ROOT, "tests", "fixtures", "desk", "fixtures", "tradier")
REGULAR = os.path.join(TRADIER_FIXTURES, "regular")
NOW = datetime(2026, 11, 3, 19, 50, 0, tzinfo=UTC)
STAMP = datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC)
UA = "aegis-desk-tests/0.0"
# Not token-shaped (no run of 20 alphanumerics), never a real credential.
SECRET = "fake-sandbox-secret-0000-1111-2222-3333"
SIX = ("SPY", "WMT", "NVDA", "WMT261120C00105000", "NVDA261120C00150000", "SPY261218P00620000")
SET_STAMPS = {
    "regular": datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC),
    "stale": datetime(2026, 11, 3, 18, 50, 0, tzinfo=UTC),
    "after_close": datetime(2026, 11, 3, 20, 59, 40, tzinfo=UTC),
    "pre_open": datetime(2026, 11, 2, 20, 59, 55, tzinfo=UTC),
    "holiday": datetime(2026, 11, 25, 20, 59, 50, tzinfo=UTC),
}
SET_NOWS = {
    "regular": NOW,
    "stale": NOW,
    "after_close": datetime(2026, 11, 3, 21, 25, 0, tzinfo=UTC),
    "pre_open": datetime(2026, 11, 3, 13, 0, 0, tzinfo=UTC),
    "holiday": datetime(2026, 11, 26, 15, 0, 0, tzinfo=UTC),
}


def read_bytes(*parts: str) -> bytes:
    with open(os.path.join(*parts), "rb") as handle:
        return handle.read()


def read_json(*parts: str) -> Any:
    return json.loads(read_bytes(*parts).decode("utf-8"))


def env_token() -> tc.Token:
    return tc.Token(SECRET, "env")


def offline(fixture_set: str = "regular", **kwargs: Any) -> tc.OfflineTransport:
    return tc.OfflineTransport(TRADIER_FIXTURES, fixture_set, **kwargs)


def offline_client(fixture_set: str = "regular", on_request: Optional[Callable] = None,
                   **kwargs: Any) -> tc.TradierClient:
    kwargs.setdefault("now", SET_NOWS[fixture_set])
    return tc.TradierClient("sandbox", None, user_agent=UA, transport=offline(fixture_set, **kwargs),
                            on_request=on_request)


# --------------------------------------------------------------------------------------------
# Constants, Token, load_token, TradierError
# --------------------------------------------------------------------------------------------


class ConstantsTest(unittest.TestCase):
    def test_hosts_paths_and_limits(self) -> None:
        self.assertEqual("sandbox.tradier.com", tc.SANDBOX_HOST)
        self.assertEqual("api.tradier.com", tc.PRODUCTION_HOST)
        self.assertEqual({"sandbox": tc.SANDBOX_HOST, "production": tc.PRODUCTION_HOST}, tc.HOSTS)
        self.assertEqual(("/v1/markets/quotes", "/v1/markets/options/chains",
                          "/v1/markets/options/expirations", "/v1/markets/options/strikes",
                          "/v1/markets/history", "/v1/markets/clock", "/v1/markets/calendar"),
                         tc.ALLOWED_PATHS)
        self.assertEqual(60, tc.RATE_LIMIT_PER_MIN)
        self.assertEqual(50, tc.MAX_BATCH)
        self.assertEqual(r"^[A-Za-z0-9.$^:_-]{1,32}$", tc.SYMBOL_RE.pattern)
        self.assertIs(tc.Quote, mt.Quote)
        self.assertIs(tc.Bar, mt.Bar)
        self.assertIs(tc.RequestReceipt, mt.RequestReceipt)

    def test_symbol_pattern(self) -> None:
        for good in ("SPY", "spy", "WMT261120C00105000", "BRK.B", "$SPX.X", "^VIX", "A:B", "X_Y-Z", "a" * 32):
            self.assertIsNotNone(tc.SYMBOL_RE.match(good), good)
        for bad in ("", " SPY", "SPY WMT", "SPY,WMT", "SPY/1", "a" * 33, "SP\nY", "sp%y"):
            self.assertIsNone(tc.SYMBOL_RE.match(bad), repr(bad))
        # "$" matches before a trailing newline, so the client strips and uses fullmatch.
        self.assertIsNotNone(tc.SYMBOL_RE.match("SPY\n"))
        self.assertIsNone(tc.SYMBOL_RE.fullmatch("SPY\n"))


class TokenTest(unittest.TestCase):
    def test_repr_and_str_hide_the_secret(self) -> None:
        token = tc.Token(SECRET, "env")
        self.assertEqual("Token(source=env)", repr(token))
        self.assertEqual("Token(source=env)", str(token))
        self.assertEqual("Token(source=file)", repr(tc.Token(SECRET, "file")))
        self.assertNotIn(SECRET, repr(token) + str(token) + format(token))
        self.assertEqual("env", token.source)
        self.assertEqual("Bearer " + SECRET, token.authorization_header())
        self.assertFalse(hasattr(token, "__dict__"), "a slotted Token exposes no instance dictionary")

    def test_blank_secret_bad_source_and_non_text_are_programming_errors(self) -> None:
        with self.assertRaises(ValueError):
            tc.Token("   ", "env")
        with self.assertRaises(ValueError):
            tc.Token(SECRET, "card")
        with self.assertRaises(TypeError):
            tc.Token(12345, "env")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            tc.Token("has newline\n", "env")
        self.assertEqual("Bearer " + SECRET, tc.Token("  " + SECRET + "\n", "env").authorization_header())


class LoadTokenTest(unittest.TestCase):
    ENV = "AEGIS_TEST_TRADIER_TOKEN"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-token-")
        self.dir = self._tmp.name
        self.file = os.path.join(self.dir, "tradier.token")
        patcher = mock.patch.dict(os.environ, {}, clear=False)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(self.ENV, None)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_file(self, text: str, mode: int = 0o600) -> str:
        # A previous step may have left the file read-only (0400). Only root may reopen such a file for
        # writing, and the CI runners are not root, so the file is replaced rather than rewritten in place
        # (removing it needs write access to the test's own directory, not to the file).
        if os.path.lexists(self.file):
            os.remove(self.file)
        with open(self.file, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(self.file, mode)
        return self.file

    def test_environment_wins_over_the_file(self) -> None:
        self.write_file("file-secret-value\n")
        os.environ[self.ENV] = "  env-secret-value  "
        token = tc.load_token(self.ENV, self.file)
        self.assertEqual("env", token.source)
        self.assertEqual("Bearer env-secret-value", token.authorization_header())

    def test_blank_environment_counts_as_absent(self) -> None:
        self.write_file("file-secret-value\nsecond line ignored\n")
        os.environ[self.ENV] = "   "
        token = tc.load_token(self.ENV, self.file)
        self.assertEqual("file", token.source)
        self.assertEqual("Bearer file-secret-value", token.authorization_header())

    def test_file_mode_must_have_no_group_or_other_bits(self) -> None:
        for mode in (0o644, 0o640, 0o604, 0o660, 0o666, 0o700 | 0o010, 0o600 | 0o001):
            self.write_file("file-secret-value\n", mode)
            with self.assertRaises(tc.TradierError) as caught:
                tc.load_token(self.ENV, self.file)
            self.assertEqual("token_file_mode", caught.exception.status, oct(mode))
            self.assertIn("%04o" % mode, caught.exception.message)
            self.assertIn(self.file, caught.exception.message)
            self.assertNotIn("file-secret-value", str(caught.exception))
        for mode in (0o600, 0o400, 0o700):
            self.write_file("file-secret-value\n", mode)
            self.assertEqual("file", tc.load_token(self.ENV, self.file).source)

    def test_file_must_be_a_regular_file(self) -> None:
        with self.assertRaises(tc.TradierError) as caught:
            tc.load_token(self.ENV, self.dir)  # a directory
        self.assertEqual("token_file_mode", caught.exception.status)

    def test_nothing_configured_is_no_token(self) -> None:
        with self.assertRaises(tc.TradierError) as caught:
            tc.load_token(self.ENV, os.path.join(self.dir, "missing.token"))
        self.assertEqual("no_token", caught.exception.status)
        self.assertIn(self.ENV, caught.exception.message)
        self.assertIn("missing.token", caught.exception.message)
        with self.assertRaises(tc.TradierError) as caught:
            tc.load_token(self.ENV, None)
        self.assertEqual("no_token", caught.exception.status)

    def test_empty_or_blank_file_is_no_token(self) -> None:
        for text in ("", "\n", "   \n\nlater\n"):
            self.write_file(text)
            with self.assertRaises(tc.TradierError) as caught:
                tc.load_token(self.ENV, self.file)
            self.assertEqual("no_token", caught.exception.status, repr(text))

    def test_undecodable_file_is_no_token_not_a_crash(self) -> None:
        with open(self.file, "wb") as handle:
            handle.write(b"\xff\xfe\x00garbage")
        os.chmod(self.file, 0o600)
        with self.assertRaises(tc.TradierError) as caught:
            tc.load_token(self.ENV, self.file)
        self.assertEqual("no_token", caught.exception.status)

    def test_unusable_secret_text_is_no_token_without_echoing_it(self) -> None:
        os.environ[self.ENV] = "two words"
        with self.assertRaises(tc.TradierError) as caught:
            tc.load_token(self.ENV, None)
        self.assertEqual("no_token", caught.exception.status)
        self.assertIn(self.ENV, caught.exception.message)
        self.assertNotIn("two words", str(caught.exception))
        os.environ.pop(self.ENV)
        self.write_file("caf\u00e9-secret\n")
        with self.assertRaises(tc.TradierError) as caught:
            tc.load_token(self.ENV, self.file)
        self.assertEqual("no_token", caught.exception.status)
        self.assertNotIn("caf", str(caught.exception))

    def test_mode_check_uses_the_permission_bits_only(self) -> None:
        self.write_file("file-secret-value\n", 0o600)
        self.assertEqual(0o600, stat.S_IMODE(os.stat(self.file).st_mode))
        self.assertEqual("file", tc.load_token(self.ENV, self.file).source)


class TradierErrorTest(unittest.TestCase):
    def test_str_attributes_and_statuses(self) -> None:
        err = tc.TradierError("quota", "HTTP 400: Rate limit quota violation", http_status=400,
                              path="/v1/markets/quotes")
        self.assertEqual("quota: HTTP 400: Rate limit quota violation", str(err))
        self.assertEqual("quota", err.status)
        self.assertEqual(400, err.http_status)
        self.assertEqual("/v1/markets/quotes", err.path)
        self.assertEqual("HTTP 400: Rate limit quota violation", err.message)
        self.assertIsInstance(err, Exception)
        self.assertEqual(("no_token", "token_file_mode", "auth", "quota", "http", "network", "timeout",
                          "decode", "refused_redirect", "refused_host", "refused_path", "bad_symbol",
                          "bad_request"), tc.STATUSES)
        with self.assertRaises(ValueError):
            tc.TradierError("unknown_status", "x")
        self.assertEqual("network: network", str(tc.TradierError("network")))


# --------------------------------------------------------------------------------------------
# Construction, URL building, no I/O
# --------------------------------------------------------------------------------------------


class ConstructionTest(unittest.TestCase):
    def test_host_kinds(self) -> None:
        token = env_token()
        sandbox = tc.TradierClient("sandbox", token, user_agent=UA)
        self.assertEqual(("https", "sandbox.tradier.com", "sandbox"), (sandbox.scheme, sandbox.host, sandbox.host_kind))
        production = tc.TradierClient("production", token, user_agent=UA)
        self.assertEqual(("https", "api.tradier.com"), (production.scheme, production.host))
        loopback = tc.TradierClient("loopback:8123", token, user_agent=UA)
        self.assertEqual(("http", "127.0.0.1:8123"), (loopback.scheme, loopback.host))
        self.assertEqual("https://sandbox.tradier.com/v1/markets/clock", sandbox.url_for("/v1/markets/clock", {}))
        self.assertEqual("https://api.tradier.com/v1/markets/quotes?symbols=SPY%2CWMT&greeks=true",
                         production.url_for("/v1/markets/quotes", {"symbols": "SPY,WMT", "greeks": "true"}))
        self.assertEqual("http://127.0.0.1:8123/v1/markets/clock", loopback.url_for("/v1/markets/clock", {}))
        self.assertEqual(10.0, sandbox.timeout_s)
        self.assertEqual(UA, sandbox.user_agent)
        self.assertIsInstance(sandbox.transport, tc.UrllibTransport)
        self.assertIs(token, sandbox.token)

    def test_unknown_host_kind_is_refused(self) -> None:
        for kind in ("api.tradier.com", "https://sandbox.tradier.com", "Sandbox", "loopback", "loopback:0",
                     "loopback:70000", "loopback:abc", "", "loopback:8080:1"):
            with self.assertRaises(tc.TradierError) as caught:
                tc.TradierClient(kind, env_token(), user_agent=UA)
            self.assertEqual("refused_host", caught.exception.status, kind)

    def test_token_rules(self) -> None:
        with self.assertRaises(tc.TradierError) as caught:
            tc.TradierClient("sandbox", None, user_agent=UA)
        self.assertEqual("no_token", caught.exception.status)
        with self.assertRaises(TypeError):
            tc.TradierClient("sandbox", SECRET, user_agent=UA)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            tc.TradierClient("sandbox", env_token(), user_agent=UA, on_request="not callable")  # type: ignore[arg-type]
        client = offline_client()  # None is fine with an OfflineTransport
        self.assertIsNone(client.token)

    def test_argument_validation(self) -> None:
        for timeout in (0, -1.0, True, "10"):
            with self.assertRaises(ValueError):
                tc.TradierClient("sandbox", env_token(), user_agent=UA, timeout_s=timeout)  # type: ignore[arg-type]
        for agent in ("", "   ", "bad\r\nagent", "non-ascii-é"):
            with self.assertRaises(ValueError):
                tc.TradierClient("sandbox", env_token(), user_agent=agent)

    def test_refused_path(self) -> None:
        client = tc.TradierClient("sandbox", env_token(), user_agent=UA)
        for path in ("/v1/user/profile", "/v1/accounts", "/v1/markets/quotes/", "v1/markets/quotes",
                     "/v1/markets/quotes?symbols=SPY", "/v1/markets/search"):
            with self.assertRaises(tc.TradierError) as caught:
                client.url_for(path, {})
            self.assertEqual("refused_path", caught.exception.status, path)
            self.assertEqual(path, caught.exception.path)

    def test_repr_is_safe(self) -> None:
        client = tc.TradierClient("sandbox", env_token(), user_agent=UA)
        text = repr(client)
        self.assertIn("sandbox.tradier.com", text)
        self.assertIn("Token(source=env)", text)
        self.assertNotIn(SECRET, text)
        self.assertFalse(hasattr(client, "__dict__"))

    def test_constructor_performs_no_io(self) -> None:
        with mock.patch("http.client.HTTPConnection.connect", autospec=True) as connect, \
                mock.patch("http.client.HTTPConnection.request", autospec=True) as request, \
                mock.patch("ssl.create_default_context", autospec=True) as context, \
                mock.patch("socket.getaddrinfo", autospec=True) as resolve:
            tc.TradierClient("sandbox", env_token(), user_agent=UA)
            tc.TradierClient("production", env_token(), user_agent=UA)
            tc.TradierClient("loopback:1", env_token(), user_agent=UA)
            tc.TradierClient("sandbox", None, user_agent=UA, transport=offline(now=NOW))
        self.assertEqual(0, connect.call_count)
        self.assertEqual(0, request.call_count)
        self.assertEqual(0, context.call_count)
        self.assertEqual(0, resolve.call_count)

    def test_https_transport_verifies_certificates(self) -> None:
        transport = tc.UrllibTransport("https")
        context = transport.tls_context
        self.assertIsNotNone(context)
        self.assertTrue(context.check_hostname)
        self.assertEqual("CERT_REQUIRED", context.verify_mode.name)
        self.assertEqual("TLSv1_2", context.minimum_version.name)
        self.assertIsNone(tc.UrllibTransport("http").tls_context)
        with self.assertRaises(ValueError):
            tc.UrllibTransport("ftp")


# --------------------------------------------------------------------------------------------
# OfflineTransport over the fixture sets
# --------------------------------------------------------------------------------------------


class OfflineTransportTest(unittest.TestCase):
    def test_fixture_file_names(self) -> None:
        cases = (
            ("/v1/markets/quotes", {"symbols": "wmt,SPY,WMT261120C00105000"}, "quotes__SPY,WMT,WMT261120C00105000.json"),
            ("/v1/markets/quotes", {"symbols": "SPY", "greeks": "true"}, "quotes__SPY.json"),
            ("/v1/markets/options/chains", {"symbol": "WMT", "expiration": "2026-11-20", "greeks": "true"},
             "chains__WMT__2026-11-20.json"),
            ("/v1/markets/options/expirations", {"symbol": "spy", "includeAllRoots": "true"}, "expirations__SPY.json"),
            ("/v1/markets/options/strikes", {"symbol": "NVDA", "expiration": "2026-11-20"}, "strikes__NVDA__2026-11-20.json"),
            ("/v1/markets/history", {"symbol": "SPY", "interval": "daily", "start": "2026-10-27", "end": "2026-11-02"},
             "history__SPY__2026-10-27__2026-11-02.json"),
            ("/v1/markets/clock", {}, "clock.json"),
            ("/v1/markets/calendar", {"month": "11", "year": "2026"}, "calendar__2026-11.json"),
            ("/v1/markets/calendar", {"month": "3", "year": "2027"}, "calendar__2027-03.json"),
        )
        for path, params, expected in cases:
            self.assertEqual(expected, tc.fixture_file_name(path, params), (path, params))
        with self.assertRaises(ValueError):
            tc.fixture_file_name("/v1/user/profile", {})

    def test_every_set_directory_exists_and_unknown_sets_or_names_are_refused(self) -> None:
        for name in SET_STAMPS:
            self.assertEqual(os.path.join(TRADIER_FIXTURES, name), offline(name, now=NOW).set_dir)
        with self.assertRaises(ValueError):
            offline("no_such_set", now=NOW)
        for bad in ("../regular", "regular/", "", "regular;rm"):
            with self.assertRaises(ValueError):
                offline(bad, now=NOW)
        with self.assertRaises(ValueError):
            offline("regular", now=datetime(2026, 11, 3, 19, 50))  # naive

    def test_default_now_is_the_set_clock_timestamp(self) -> None:
        for name, now in SET_NOWS.items():
            transport = offline(name)
            self.assertEqual(now, transport.receipt_utc, name)
            self.assertEqual(now, offline_client(name).clock().receipt.receipt_utc, name)

    def test_quotes_one_object(self) -> None:
        receipts: List[mt.RequestReceipt] = []
        client = offline_client(on_request=receipts.append)
        result = client.quotes(["SPY"])
        self.assertEqual(["SPY"], sorted(result.quotes))
        self.assertEqual(([], ()), (result.unmatched, result.not_returned))
        quote = result.quotes["SPY"]
        self.assertIsInstance(quote, tc.Quote)
        self.assertEqual("etf", quote.type)
        self.assertEqual(Decimal("648.10"), quote.bid)
        self.assertEqual(Decimal("648.14"), quote.ask)
        self.assertEqual("648.1", str(quote.bid))  # Decimal built from the JSON text, never a float
        self.assertEqual(Decimal("648.12"), quote.mid)
        self.assertEqual(Decimal("645.90"), quote.prevclose)
        self.assertEqual(STAMP, quote.as_of)
        self.assertEqual(STAMP, quote.trade_date)
        self.assertIsNone(quote.close)
        self.assertEqual(41250000, quote.volume)
        self.assertEqual(0, quote.open_interest)
        self.assertEqual("SPY", quote.raw["symbol"])
        self.assertEqual([result.receipt], receipts)
        self.assertEqual(result.receipt, result.receipts[0])
        self.assertEqual(1, len(result.receipts))
        receipt = result.receipt
        self.assertEqual(("/v1/markets/quotes", 1, NOW, "ok", 200, 0),
                         (receipt.path, receipt.symbols_count, receipt.receipt_utc, receipt.status,
                          receipt.http_status, receipt.elapsed_ms))
        self.assertIsNone(receipt.ratelimit_allowed)
        self.assertIsNone(receipt.ratelimit_expiry_utc)

    def test_quotes_many_and_the_option_fields(self) -> None:
        result = offline_client().quotes(["spy", "WMT", "wmt261120c00105000"])  # case and order normalised
        self.assertEqual(["SPY", "WMT", "WMT261120C00105000"], sorted(result.quotes))
        option = result.quotes["WMT261120C00105000"]
        self.assertTrue(option.is_option)
        self.assertEqual(("WMT", Decimal("105"), 100, date(2026, 11, 20), "call", "WMT", "standard", 8420),
                         (option.underlying, option.strike, option.contract_size, option.expiration_date,
                          option.option_type, option.root_symbol, option.expiration_type, option.open_interest))
        self.assertIsNone(option.greeks)
        self.assertEqual(Decimal("3.425"), option.mid)
        self.assertEqual(result.receipt.symbols_count, 3)
        six = offline_client().quotes(SIX)
        self.assertEqual(sorted(SIX), sorted(six.quotes))
        self.assertEqual([], six.unmatched)
        self.assertEqual(6, six.receipt.symbols_count)

    def test_greeks_flag_only_adds_the_parameter(self) -> None:
        result = offline_client().quotes(["SPY"], greeks=True)
        self.assertEqual(["SPY"], sorted(result.quotes))

    def test_duplicates_collapse_and_a_lone_string_is_one_symbol(self) -> None:
        result = offline_client().quotes(["SPY", "spy", " SPY "])
        self.assertEqual(1, result.receipt.symbols_count)
        self.assertEqual(["SPY"], sorted(result.quotes))
        self.assertEqual(["WMT"], sorted(offline_client().quotes("WMT").quotes))

    def test_unmatched_symbols(self) -> None:
        only = offline_client().quotes(["SPX"])
        self.assertEqual({}, only.quotes)
        self.assertEqual(["SPX"], only.unmatched)
        mixed = offline_client().quotes(["SPY", "SPX"])
        self.assertEqual(["SPY"], sorted(mixed.quotes))
        self.assertEqual(["SPX"], mixed.unmatched)
        holiday = offline_client("holiday").quotes(SIX)
        self.assertEqual(["NVDA", "SPY", "SPY261218P00620000", "WMT"], sorted(holiday.quotes))
        self.assertEqual(["NVDA261120C00150000", "WMT261120C00105000"], holiday.unmatched)
        expired = offline_client("holiday").quotes(["WMT261120C00105000"])
        self.assertEqual(({}, ["WMT261120C00105000"]), (expired.quotes, expired.unmatched))
        two = offline_client("holiday").quotes(["SPY", "WMT", "WMT261120C00105000"])
        self.assertEqual(["SPY", "WMT"], sorted(two.quotes))
        self.assertEqual(["WMT261120C00105000"], two.unmatched)

    def test_every_set_serves_its_documented_stamp(self) -> None:
        for name, stamp in SET_STAMPS.items():
            result = offline_client(name).quotes(["SPY"])
            quote = result.quotes["SPY"]
            self.assertEqual(stamp, quote.as_of, name)
            self.assertEqual(stamp, quote.bid_date, name)
            self.assertEqual(stamp, quote.ask_date, name)
            self.assertEqual(SET_NOWS[name], result.receipt.receipt_utc, name)

    def test_option_chain(self) -> None:
        result = offline_client().option_chain("wmt", date(2026, 11, 20))
        self.assertEqual(10, len(result.options))
        symbols = [quote.symbol for quote in result.options]
        self.assertIn("WMT261120C00105000", symbols)
        self.assertTrue(all(quote.is_option for quote in result.options))
        self.assertTrue(all(quote.greeks is None for quote in result.options))
        anchor = [quote for quote in result.options if quote.symbol == "WMT261120C00105000"][0]
        standalone = offline_client().quotes(["WMT261120C00105000"]).quotes["WMT261120C00105000"]
        self.assertEqual((standalone.bid, standalone.ask), (anchor.bid, anchor.ask))
        self.assertEqual(("/v1/markets/options/chains", 1, "ok"),
                         (result.receipt.path, result.receipt.symbols_count, result.receipt.status))
        self.assertIn("options", result.raw)
        with_greeks = offline_client().option_chain("WMT", date(2026, 11, 20), greeks=True)
        self.assertEqual(10, len(with_greeks.options))

    def test_null_chain_and_null_strikes_are_empty_lists(self) -> None:
        self.assertEqual([], offline_client("holiday").option_chain("WMT", date(2026, 11, 20)).options)
        self.assertEqual([], offline_client("holiday").option_strikes("NVDA", date(2026, 11, 20)).strikes)

    def test_option_expirations(self) -> None:
        result = offline_client().option_expirations("SPY")
        self.assertTrue(all(isinstance(day, date) for day in result.dates))
        self.assertIn(date(2026, 11, 20), result.dates)
        self.assertIn(date(2026, 12, 18), result.dates)
        self.assertEqual(sorted(result.dates), result.dates)
        self.assertEqual(result.dates, offline_client().option_expirations("SPY", include_all_roots=False).dates)
        holiday = offline_client("holiday").option_expirations("SPY")
        self.assertEqual(date(2026, 11, 27), holiday.dates[0])

    def test_option_strikes(self) -> None:
        result = offline_client().option_strikes("WMT", date(2026, 11, 20))
        self.assertEqual([Decimal("85"), Decimal("90"), Decimal("95"), Decimal("97.5"), Decimal("100"),
                          Decimal("102.5"), Decimal("105"), Decimal("107.5"), Decimal("110"), Decimal("112.5"),
                          Decimal("115"), Decimal("120")], result.strikes)
        self.assertTrue(all(isinstance(strike, Decimal) for strike in result.strikes))

    def test_history(self) -> None:
        result = offline_client().history("SPY", date(2026, 10, 27), date(2026, 11, 2))
        self.assertEqual(5, len(result.bars))
        last = result.bars[-1]
        self.assertIsInstance(last, tc.Bar)
        self.assertEqual((date(2026, 11, 2), Decimal("645.90"), 55600000), (last.date, last.close, last.volume))
        single = offline_client("holiday").history("WMT", date(2026, 11, 20), date(2026, 11, 20))
        self.assertEqual(1, len(single.bars))
        self.assertEqual(Decimal("104.86"), single.bars[0].close)
        with self.assertRaises(ValueError):
            offline_client().history("SPY", date(2026, 11, 2), date(2026, 10, 27))
        with self.assertRaises(ValueError):
            offline_client().history("SPY", date(2026, 10, 27), date(2026, 11, 2), interval="hourly")

    def test_clock(self) -> None:
        result = offline_client().clock()
        self.assertEqual((date(2026, 11, 3), "open", NOW, "16:00", "postmarket"),
                         (result.date, result.state, result.timestamp_utc, result.next_change, result.next_state))
        self.assertEqual("Market is open from 09:30 to 16:00", result.description)
        self.assertEqual(0, result.receipt.symbols_count)
        self.assertEqual("closed", offline_client("holiday").clock().state)

    def test_calendar(self) -> None:
        result = offline_client().calendar(11, 2026)
        self.assertEqual((11, 2026), (result.month, result.year))
        self.assertEqual(30, len(result.days))
        by_date = {day.date: day for day in result.days}
        thanksgiving = by_date[date(2026, 11, 26)]
        self.assertEqual("closed", thanksgiving.status)
        self.assertIsNone(thanksgiving.open)
        early = by_date[date(2026, 11, 27)]
        self.assertEqual("open", early.status)
        self.assertEqual("13:00", early.open.end.strftime("%H:%M"))
        self.assertEqual("17:00", early.postmarket.end.strftime("%H:%M"))
        regular = by_date[date(2026, 11, 3)]
        self.assertEqual(("09:30", "16:00"), (regular.open.start.strftime("%H:%M"), regular.open.end.strftime("%H:%M")))
        for month, year in ((0, 2026), (13, 2026), (11, 99), ("11", 2026)):
            with self.assertRaises(ValueError):
                offline_client().calendar(month, year)  # type: ignore[arg-type]

    def test_unknown_key_is_a_network_error_with_the_brief_message(self) -> None:
        receipts: List[mt.RequestReceipt] = []
        client = offline_client(on_request=receipts.append)
        with self.assertRaises(tc.TradierError) as caught:
            client.quotes(["SPY", "FOO"])
        err = caught.exception
        self.assertEqual("network", err.status)
        self.assertEqual("no offline fixture for /v1/markets/quotes?FOO,SPY", err.message)
        self.assertEqual("/v1/markets/quotes", err.path)
        self.assertIsNone(err.http_status)
        self.assertEqual(1, len(receipts))
        self.assertEqual(("network", NOW, None, 2), (receipts[0].status, receipts[0].receipt_utc,
                                                      receipts[0].http_status, receipts[0].symbols_count))
        with self.assertRaises(tc.TradierError) as caught:
            client.option_chain("SPY", date(2026, 11, 20))
        self.assertEqual("no offline fixture for /v1/markets/options/chains?SPY__2026-11-20", caught.exception.message)
        with self.assertRaises(tc.TradierError) as caught:
            client.calendar(12, 2026)
        self.assertEqual("no offline fixture for /v1/markets/calendar?2026-12", caught.exception.message)
        transport = offline(now=NOW)
        with self.assertRaises(tc.TradierError) as caught:
            transport.fetch("https://sandbox.tradier.com/v1/markets/clock/extra", {}, 1.0)
        self.assertEqual("network", caught.exception.status)

    def test_bad_symbol_sends_nothing(self) -> None:
        receipts: List[mt.RequestReceipt] = []
        client = offline_client(on_request=receipts.append)
        for bad in ("SPY WMT", "", "SPY,WMT", "a" * 33, 42, "SP\nY", "SPY\nWMT"):
            with self.assertRaises(tc.TradierError) as caught:
                client.quotes(["SPY", bad])  # type: ignore[list-item]
            self.assertEqual("bad_symbol", caught.exception.status, repr(bad))
        with self.assertRaises(tc.TradierError) as caught:
            client.option_chain("SPY WMT", date(2026, 11, 20))
        self.assertEqual("bad_symbol", caught.exception.status)
        with self.assertRaises(ValueError):
            client.quotes([])
        with self.assertRaises(ValueError):
            client.option_chain("SPY", datetime(2026, 11, 20, tzinfo=UTC))  # a datetime is not a date
        self.assertEqual([], receipts)

    def test_offline_transport_never_needs_a_token_and_ignores_headers(self) -> None:
        transport = offline(now=NOW)
        response = transport.fetch("https://sandbox.tradier.com/v1/markets/clock", {"Authorization": "x"}, 1.0)
        self.assertEqual((200, {}, NOW, 0), (response.status, response.headers, response.receipt_utc, response.elapsed_ms))
        self.assertEqual(read_bytes(REGULAR, "clock.json"), response.body)


class OfflineShapesTest(unittest.TestCase):
    """Vendor shapes the committed sets do not carry, written into a temporary fixture set."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-offline-")
        self.set_dir = os.path.join(self._tmp.name, "shapes")
        os.makedirs(self.set_dir)
        self.receipts: List[mt.RequestReceipt] = []
        self.client = tc.TradierClient(
            "sandbox", None, user_agent=UA, on_request=self.receipts.append,
            transport=tc.OfflineTransport(self._tmp.name, "shapes", now=NOW))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, document: Any) -> None:
        with open(os.path.join(self.set_dir, name), "w", encoding="utf-8") as handle:
            if isinstance(document, str):
                handle.write(document)
            else:
                json.dump(document, handle)

    def spy(self) -> Dict[str, Any]:
        return read_json(REGULAR, "quotes__SPY.json")["quotes"]["quote"]

    def test_quotes_null_string_and_missing_unmatched(self) -> None:
        self.write("quotes__SPY.json", {"quotes": "null"})
        result = self.client.quotes(["SPY"])
        self.assertEqual({}, result.quotes)
        self.assertEqual([], result.unmatched, "the vendor echoed nothing as unmatched")
        self.assertEqual(("SPY",), result.not_returned, "a symbol the vendor dropped is reported, never lost")
        self.write("quotes__WMT.json", {"quotes": None})
        self.assertEqual(("WMT",), self.client.quotes(["WMT"]).not_returned)
        self.write("quotes__NVDA.json", {"quotes": {"unmatched_symbols": {"symbol": ["NVDA"]}}})
        nvda = self.client.quotes(["NVDA"])
        self.assertEqual((["NVDA"], ()), (nvda.unmatched, nvda.not_returned))
        self.write("quotes__SPY,WMT.json", {"quotes": {"quote": self.spy(), "unmatched_symbols": {"symbol": "wmt"}}})
        both = self.client.quotes(["SPY", "WMT"])
        self.assertEqual((["SPY"], ["wmt"], ()), (sorted(both.quotes), both.unmatched, both.not_returned))

    def test_decode_statuses(self) -> None:
        bad_bid = self.spy()
        bad_bid["bid"] = "abc"
        cases = (
            ("quotes__A.json", {"quotes": {"quote": dict(bad_bid, symbol="A")}}, "bid"),
            ("quotes__B.json", "not json at all", "not JSON"),
            ("quotes__C.json", ["a", "list"], "not a JSON object"),
            ("quotes__D.json", {"other": 1}, "quotes"),
            ("quotes__E.json", {"quotes": {"quote": "text"}}, "expected a JSON object"),
            ("quotes__F.json", {"quotes": {"quote": {"symbol": "F", "bid": float("nan")}}}, "non-finite"),
            ("quotes__G.json", b"\xff\xfe".decode("latin-1"), "not JSON"),
            ("quotes__H.json", {"quotes": {"quote": {"symbol": "H", "option_type": "straddle"}}}, "option_type"),
            ("quotes__I.json", {"quotes": {"quote": {"symbol": "I", "greeks": [1, 2]}}}, "greeks"),
            ("quotes__J.json", {"quotes": {"quote": {"symbol": "J", "bid_date": "yesterday"}}}, "bid_date"),
        )
        for name, document, needle in cases:
            self.write(name, document)
            symbol = name[len("quotes__"):-len(".json")]
            before = len(self.receipts)
            with self.assertRaises(tc.TradierError) as caught:
                self.client.quotes([symbol])
            err = caught.exception
            self.assertEqual("decode", err.status, name)
            self.assertIn(needle, err.message, name)
            self.assertEqual(200, err.http_status, name)
            self.assertEqual(before + 1, len(self.receipts), name)
            self.assertEqual("decode", self.receipts[-1].status, name)
            self.assertEqual(200, self.receipts[-1].http_status, name)

    def test_decode_message_is_capped_at_200_printable_characters(self) -> None:
        self.write("quotes__LONG.json", "<html>" + "x" * 1000 + "\n\t</html>")
        with self.assertRaises(tc.TradierError) as caught:
            self.client.quotes(["LONG"])
        message = caught.exception.message
        self.assertTrue(message.startswith("HTTP 200 body is not JSON"), message)
        excerpt = message.rsplit("): ", 1)[1]
        self.assertLessEqual(len(excerpt), 200 + len("..."))
        self.assertNotIn("\n", message)
        self.assertNotIn("\t", message)

    def test_other_endpoint_shapes(self) -> None:
        self.write("expirations__X.json", {"expirations": {"date": "2026-11-20"}})
        self.assertEqual([date(2026, 11, 20)], self.client.option_expirations("X").dates)
        self.write("expirations__Y.json", {"expirations": None})
        self.assertEqual([], self.client.option_expirations("Y").dates)
        self.write("expirations__Z.json", {"expirations": {"expiration": [{"date": "2026-12-18", "contract_size": 100}]}})
        self.assertEqual([date(2026, 12, 18)], self.client.option_expirations("Z").dates)
        self.write("strikes__X__2026-11-20.json", {"strikes": {"strike": 100.5}})
        self.assertEqual([Decimal("100.5")], self.client.option_strikes("X", date(2026, 11, 20)).strikes)
        self.write("history__X__2026-11-02__2026-11-02.json", {"history": None})
        self.assertEqual([], self.client.history("X", date(2026, 11, 2), date(2026, 11, 2)).bars)
        self.write("chains__X__2026-11-20.json", {"options": {"option": dict(self.spy(), symbol="X261120C00100000", type="option", option_type="call")}})
        self.assertEqual(1, len(self.client.option_chain("X", date(2026, 11, 20)).options))
        self.write("chains__Y__2026-11-20.json", {"options": "null"})
        self.assertEqual([], self.client.option_chain("Y", date(2026, 11, 20)).options)
        self.write("calendar__2026-12.json", {"calendar": {"month": 12, "year": 2026, "days": None}})
        self.assertEqual([], self.client.calendar(12, 2026).days)
        self.write("calendar__2027-01.json", {"calendar": {"month": 1, "year": 2027, "days": {"day": {"date": "2027-01-04", "status": "open", "description": "Market is open"}}}})
        self.assertEqual(1, len(self.client.calendar(1, 2027).days))
        self.write("clock.json", {"clock": {"date": "2026-11-03", "state": "open"}})
        result = self.client.clock()
        self.assertEqual(("open", None, None), (result.state, result.timestamp_utc, result.description))
        for name, document in (("expirations__BAD.json", {"expirations": {"date": ["not-a-date"]}}),
                               ("strikes__BAD__2026-11-20.json", {"strikes": {"strike": ["abc"]}}),
                               ("history__BAD__2026-11-02__2026-11-02.json", {"history": {"day": {"open": 1}}}),
                               ("calendar__2027-02.json", {"calendar": {"days": {"day": [{"date": "2027-02-01", "status": "half"}]}}}),
                               ("chains__BAD__2026-11-20.json", {"options": {"option": [{"symbol": "", "bid": 1}]}})):
            self.write(name, document)
        for call in (lambda: self.client.option_expirations("BAD"),
                     lambda: self.client.option_strikes("BAD", date(2026, 11, 20)),
                     lambda: self.client.history("BAD", date(2026, 11, 2), date(2026, 11, 2)),
                     lambda: self.client.calendar(2, 2027),
                     lambda: self.client.option_chain("BAD", date(2026, 11, 20))):
            with self.assertRaises(tc.TradierError) as caught:
                call()
            self.assertEqual("decode", caught.exception.status)

    def test_batching_over_max_batch(self) -> None:
        symbols = ["%s%d" % (letter, digit) for letter in "ABCDEF" for digit in range(10)][:tc.MAX_BATCH + 7]
        first, second = symbols[:tc.MAX_BATCH], symbols[tc.MAX_BATCH:]
        for batch in (first, second):
            quotes = [dict(self.spy(), symbol=symbol) for symbol in batch]
            self.write("quotes__%s.json" % ",".join(sorted(batch)), {"quotes": {"quote": quotes}})
        result = self.client.quotes(symbols)
        self.assertEqual(len(symbols), len(result.quotes))
        self.assertEqual(2, len(result.receipts))
        self.assertEqual([tc.MAX_BATCH, 7], [receipt.symbols_count for receipt in result.receipts])
        self.assertEqual(result.receipts[-1], result.receipt)
        self.assertEqual(2, len(self.receipts))
        self.assertEqual([], result.unmatched)

    def test_on_request_exceptions_propagate(self) -> None:
        def explode(receipt: mt.RequestReceipt) -> None:
            raise RuntimeError("ledger full")

        self.write("quotes__SPY.json", {"quotes": {"quote": self.spy()}})
        client = tc.TradierClient("sandbox", None, user_agent=UA, on_request=explode,
                                  transport=tc.OfflineTransport(self._tmp.name, "shapes", now=NOW))
        with self.assertRaises(RuntimeError):
            client.quotes(["SPY"])

    def test_default_now_needs_a_readable_clock(self) -> None:
        with self.assertRaises(ValueError):
            tc.OfflineTransport(self._tmp.name, "shapes")
        self.write("clock.json", {"clock": {"date": "2026-11-03", "state": "open", "timestamp": 1793735400}})
        self.assertEqual(NOW, tc.OfflineTransport(self._tmp.name, "shapes").receipt_utc)


# --------------------------------------------------------------------------------------------
# Loopback fake server
# --------------------------------------------------------------------------------------------

Responder = Callable[[str, Dict[str, str], Dict[str, str]], Tuple[int, List[Tuple[str, str]], bytes]]


def fixture_body(path: str, query: Dict[str, str]) -> Optional[bytes]:
    """The ``regular`` fixture body for an allow-listed request, built independently of the client."""
    if path == "/v1/markets/quotes":
        key = ",".join(sorted(s.upper() for s in query.get("symbols", "").split(",") if s))
        name = "quotes__%s.json" % key
    elif path == "/v1/markets/options/chains":
        name = "chains__%s__%s.json" % (query.get("symbol", ""), query.get("expiration", ""))
    elif path == "/v1/markets/options/expirations":
        name = "expirations__%s.json" % query.get("symbol", "")
    elif path == "/v1/markets/options/strikes":
        name = "strikes__%s__%s.json" % (query.get("symbol", ""), query.get("expiration", ""))
    elif path == "/v1/markets/history":
        name = "history__%s__%s__%s.json" % (query.get("symbol", ""), query.get("start", ""), query.get("end", ""))
    elif path == "/v1/markets/clock":
        name = "clock.json"
    elif path == "/v1/markets/calendar":
        name = "calendar__%s-%s.json" % (query.get("year", ""), query.get("month", ""))
    else:
        return None
    full = os.path.join(REGULAR, name)
    if not os.path.isfile(full):
        return None
    return read_bytes(full)


class RecordingHandler(http.server.BaseHTTPRequestHandler):
    """Records every request; serves fixtures on GET; any other verb is a violation."""

    server_version = "FakeTradier/0"
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
                status, extra, body = 404, [], b'{"error": "not found"}'
            else:
                status, extra, body = 200, [("Content-Type", "application/json;charset=utf-8")], fixture
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


class FakeTradier(http.server.ThreadingHTTPServer):
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


class LoopbackTest(unittest.TestCase):
    server: FakeTradier
    thread: threading.Thread

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = FakeTradier()
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

    def make_client(self, **kwargs: Any) -> tc.TradierClient:
        kwargs.setdefault("user_agent", UA)
        kwargs.setdefault("on_request", self.receipts.append)
        return tc.TradierClient("loopback:%d" % self.server.port, env_token(), **kwargs)

    def requests(self) -> List[Dict[str, Any]]:
        with self.server.lock:
            return list(self.server.requests)

    def test_get_with_the_three_headers_and_no_body(self) -> None:
        result = self.client.quotes(["SPY"])
        self.assertEqual(Decimal("648.10"), result.quotes["SPY"].bid)
        seen = self.requests()
        self.assertEqual(1, len(seen))
        request = seen[0]
        self.assertEqual("GET", request["method"])
        self.assertEqual("/v1/markets/quotes?symbols=SPY", request["path"])
        self.assertEqual("Bearer " + SECRET, request["headers"]["authorization"])
        self.assertEqual("application/json", request["headers"]["accept"])
        self.assertEqual(UA, request["headers"]["user-agent"])
        self.assertNotIn("content-length", request["headers"])
        self.assertNotIn("content-type", request["headers"])
        receipt = result.receipt
        self.assertEqual(("ok", 200, "/v1/markets/quotes", 1), (receipt.status, receipt.http_status, receipt.path, receipt.symbols_count))
        self.assertEqual(UTC, receipt.receipt_utc.tzinfo)
        self.assertIsNotNone(receipt.elapsed_ms)
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
        quotes = self.client.quotes(["SPY", "WMT", "WMT261120C00105000"], greeks=True)
        chain = self.client.option_chain("WMT", date(2026, 11, 20))
        expirations = self.client.option_expirations("SPY")
        strikes = self.client.option_strikes("NVDA", date(2026, 11, 20))
        history = self.client.history("NVDA", date(2026, 10, 27), date(2026, 11, 2))
        clock = self.client.clock()
        calendar = self.client.calendar(11, 2026)
        self.assertEqual(3, len(quotes.quotes))
        self.assertEqual(10, len(chain.options))
        self.assertIn(date(2026, 12, 18), expirations.dates)
        self.assertIn(Decimal("150"), strikes.strikes)
        self.assertEqual(5, len(history.bars))
        self.assertEqual("open", clock.state)
        self.assertEqual(30, len(calendar.days))
        self.assertEqual(7, len(seen))
        self.assertEqual({"GET"}, {method for method, _url, _body in seen})
        self.assertTrue(all(body is None for _method, _url, body in seen))
        self.assertEqual(sorted(tc.ALLOWED_PATHS), sorted(urlsplit(url).path for _method, url, _body in seen))
        self.assertIn("greeks=true", seen[0][1])
        self.assertIn("includeAllRoots=true", seen[2][1])
        self.assertIn("interval=daily", seen[4][1])
        self.assertIn("month=11", seen[6][1])
        self.assertEqual(7, len(self.requests()))
        self.assertEqual({"GET"}, {request["method"] for request in self.requests()})
        self.assertEqual(["ok"] * 7, [receipt.status for receipt in self.receipts])
        self.assertEqual([3, 1, 1, 1, 1, 0, 0], [receipt.symbols_count for receipt in self.receipts])

    def test_rate_limit_headers(self) -> None:
        body = read_bytes(REGULAR, "clock.json")
        self.server.script["/v1/markets/clock"] = static(200, body, [
            ("X-Ratelimit-Allowed", "60"), ("X-Ratelimit-Used", "7"), ("X-Ratelimit-Available", "53"),
            ("X-Ratelimit-Expiry", "1793735460000")])
        receipt = self.client.clock().receipt
        self.assertEqual((60, 7, 53), (receipt.ratelimit_allowed, receipt.ratelimit_used, receipt.ratelimit_available))
        self.assertEqual(datetime(2026, 11, 3, 19, 51, 0, tzinfo=UTC), receipt.ratelimit_expiry_utc)
        self.server.script["/v1/markets/clock"] = static(200, body, [("X-Ratelimit-Allowed", "sixty"), ("X-Ratelimit-Expiry", "soon")])
        receipt = self.client.clock().receipt
        self.assertIsNone(receipt.ratelimit_allowed)
        self.assertIsNone(receipt.ratelimit_expiry_utc)

    def assert_error(self, status: str, http_status: Optional[int], call: Callable[[], Any]) -> tc.TradierError:
        before = len(self.receipts)
        with self.assertRaises(tc.TradierError) as caught:
            call()
        err = caught.exception
        self.assertEqual(status, err.status)
        self.assertEqual(http_status, err.http_status)
        self.assertEqual(before + 1, len(self.receipts))
        self.assertEqual(status, self.receipts[-1].status)
        self.assertEqual(http_status, self.receipts[-1].http_status)
        self.assertNotIn(SECRET, str(err))
        self.assertNotIn(SECRET, repr(err))
        self.assertNotIn("symbols=", err.message)
        self.assertNotIn("Authorization", err.message)
        return err

    def test_401_and_403_are_auth(self) -> None:
        self.server.script["/v1/markets/quotes"] = static(401, read_bytes(REGULAR, "errors", "invalid_token.txt"))
        err = self.assert_error("auth", 401, lambda: self.client.quotes(["SPY"]))
        self.assertEqual("auth: HTTP 401: Invalid Access Token", str(err))
        self.assertEqual("/v1/markets/quotes", err.path)
        self.server.script["/v1/markets/clock"] = static(403, b"")
        err = self.assert_error("auth", 403, self.client.clock)
        self.assertEqual("HTTP 403 (empty body)", err.message)

    def test_400_quota_violation_and_429_are_quota(self) -> None:
        body = read_bytes(REGULAR, "errors", "quota_violation.txt")
        self.server.script["/v1/markets/quotes"] = static(400, body)
        err = self.assert_error("quota", 400, lambda: self.client.quotes(["SPY"]))
        self.assertIn("Rate limit quota violation", err.message)
        self.server.script["/v1/markets/quotes"] = static(400, b'{"fault": {"faultstring": "QUOTA VIOLATION"}}')
        self.assert_error("quota", 400, lambda: self.client.quotes(["SPY"]))
        self.server.script["/v1/markets/quotes"] = static(429, b"slow down")
        self.assert_error("quota", 429, lambda: self.client.quotes(["SPY"]))

    def test_other_400_is_bad_request_and_5xx_or_404_is_http(self) -> None:
        self.server.script["/v1/markets/quotes"] = static(400, b'{"error": "symbols is required"}')
        err = self.assert_error("bad_request", 400, lambda: self.client.quotes(["SPY"]))
        self.assertIn("symbols is required", err.message)
        self.server.script["/v1/markets/quotes"] = static(500, b"<html>internal error</html>")
        err = self.assert_error("http", 500, lambda: self.client.quotes(["SPY"]))
        self.assertIn("internal error", err.message)
        self.server.script["/v1/markets/quotes"] = static(503, b"")
        self.assert_error("http", 503, lambda: self.client.quotes(["SPY"]))
        self.server.script["/v1/markets/quotes"] = static(204, b"")
        self.assert_error("http", 204, lambda: self.client.quotes(["SPY"]))
        self.server.script.clear()
        self.assert_error("http", 404, lambda: self.client.quotes(["NOFILE"]))

    def test_redirects_are_refused_without_a_second_request(self) -> None:
        target = "http://127.0.0.1:%d/v1/markets/clock" % self.server.port
        for code in (301, 302, 303, 307, 308):
            self.server.script["/v1/markets/quotes"] = static(code, b"", [("Location", target)])
            before = len(self.requests())
            err = self.assert_error("refused_redirect", code, lambda: self.client.quotes(["SPY"]))
            self.assertEqual(before + 1, len(self.requests()), "a redirect must not be followed (code %d)" % code)
            self.assertEqual("/v1/markets/quotes?symbols=SPY", self.requests()[-1]["path"])
            self.assertIn("redirect", err.message)
            self.assertNotIn(target, err.message)
        self.server.script["/v1/markets/quotes"] = static(302, b"")  # no Location header at all
        before = len(self.requests())
        self.assert_error("refused_redirect", 302, lambda: self.client.quotes(["SPY"]))
        self.assertEqual(before + 1, len(self.requests()))
        self.server.script["/v1/markets/quotes"] = static(304, b"")
        self.assert_error("refused_redirect", 304, lambda: self.client.quotes(["SPY"]))

    def test_non_json_200_is_decode(self) -> None:
        self.server.script["/v1/markets/quotes"] = static(200, b"<!DOCTYPE html><html>maintenance</html>")
        err = self.assert_error("decode", 200, lambda: self.client.quotes(["SPY"]))
        self.assertIn("maintenance", err.message)
        self.server.script["/v1/markets/quotes"] = static(200, b"\xff\xfe\x00")
        self.assert_error("decode", 200, lambda: self.client.quotes(["SPY"]))

    def test_timeout(self) -> None:
        self.server.delay_s = 0.6
        client = self.make_client(timeout_s=0.15)
        err = self.assert_error("timeout", None, lambda: client.quotes(["SPY"]))
        self.assertIn("0.1", err.message)
        self.assertIsNotNone(self.receipts[-1].elapsed_ms)
        self.server.delay_s = 0.0

    def test_connection_refused_is_network(self) -> None:
        client = tc.TradierClient("loopback:%d" % free_port(), env_token(), user_agent=UA,
                                  on_request=self.receipts.append)
        err = self.assert_error("network", None, client.clock)
        self.assertTrue(err.message)
        self.assertEqual("/v1/markets/clock", err.path)
        self.assertEqual(0, len(self.requests()))

    def test_token_echoed_by_the_server_is_redacted(self) -> None:
        def echo(path: str, query: Dict[str, str], headers: Dict[str, str]) -> Tuple[int, List[Tuple[str, str]], bytes]:
            text = "Quota Violation for %s (%s)" % (headers.get("authorization", ""), SECRET)
            return 400, [], text.encode("utf-8")

        self.server.script["/v1/markets/quotes"] = echo
        err = self.assert_error("quota", 400, lambda: self.client.quotes(["SPY"]))
        self.assertIn("[redacted]", err.message)
        self.assertNotIn(SECRET, err.message)
        self.assertNotIn(SECRET, str(err))
        self.assertNotIn(SECRET, repr(err))
        self.assertNotIn(SECRET, repr(err.args))
        receipt_json = json.dumps([dataclasses.asdict(receipt) for receipt in self.receipts], default=str)
        self.assertNotIn(SECRET, receipt_json)
        self.assertNotIn(SECRET, repr(self.client))
        self.assertNotIn(SECRET, repr(self.client.token))

    def test_token_echoed_in_a_200_body_is_refused_before_it_can_be_cached(self) -> None:
        # Brief non-negotiable 3: the token never reaches a cache file. A vendor that echoes the
        # Authorization header into a quote field would otherwise land in quotes.json through the
        # raw vendor dict the cache stores, so the client refuses the whole answer as ``decode``.
        template = read_json(REGULAR, "quotes__SPY.json")

        def echo(path: str, query: Dict[str, str], headers: Dict[str, str]) -> Tuple[int, List[Tuple[str, str]], bytes]:
            document = json.loads(json.dumps(template))
            document["quotes"]["quote"]["description"] = "echo %s" % headers.get("authorization", "")
            return 200, [("Content-Type", "application/json")], json.dumps(document).encode("utf-8")

        self.server.script["/v1/markets/quotes"] = echo
        err = self.assert_error("decode", 200, lambda: self.client.quotes(["SPY"]))
        self.assertIn("echoes the credential", err.message)
        self.assertNotIn(SECRET, err.message)
        self.assertEqual("decode", self.receipts[-1].status)
        # The same secret written as JSON escapes is invisible in the raw bytes and still refused
        # once the body is decoded.
        document = json.loads(json.dumps(template))
        document["quotes"]["quote"]["description"] = "MARKER"
        escaped = "".join("\\u%04x" % ord(ch) for ch in SECRET)
        body = json.dumps(document).replace("MARKER", escaped).encode("utf-8")
        self.assertNotIn(SECRET.encode("utf-8"), body)
        self.assertEqual(SECRET, json.loads(body)["quotes"]["quote"]["description"])
        self.server.script["/v1/markets/quotes"] = static(200, body)
        err = self.assert_error("decode", 200, lambda: self.client.quotes(["SPY"]))
        self.assertIn("echoes the credential", err.message)
        # An honest 200 from the same server is served as before.
        self.server.script.clear()
        self.assertEqual(Decimal("648.1"), self.client.quotes(["SPY"]).quotes["SPY"].bid)

    def test_batching_over_max_batch_sends_consecutive_requests(self) -> None:
        template = read_json(REGULAR, "quotes__SPY.json")["quotes"]["quote"]

        def synth(path: str, query: Dict[str, str], headers: Dict[str, str]) -> Tuple[int, List[Tuple[str, str]], bytes]:
            symbols = query["symbols"].split(",")
            quotes = [dict(template, symbol=symbol) for symbol in symbols]
            return 200, [], json.dumps({"quotes": {"quote": quotes}}).encode("utf-8")

        self.server.script["/v1/markets/quotes"] = synth
        symbols = ["B%03d" % index for index in range(120)]
        result = self.client.quotes(symbols)
        self.assertEqual(120, len(result.quotes))
        self.assertEqual(set(symbols), set(result.quotes))
        self.assertEqual(([], ()), (result.unmatched, result.not_returned))
        seen = self.requests()
        self.assertEqual(3, len(seen))
        sent: List[str] = []
        for request in seen:
            batch = parse_qs(urlsplit(request["path"]).query)["symbols"][0].split(",")
            self.assertLessEqual(len(batch), tc.MAX_BATCH)
            sent.extend(batch)
        self.assertEqual(symbols, sent, "batches are consecutive and keep the request order")
        self.assertEqual([50, 50, 20], [receipt.symbols_count for receipt in result.receipts])
        self.assertEqual(3, len(self.receipts))

    def test_failure_in_a_later_batch_raises_after_recording_the_earlier_receipts(self) -> None:
        template = read_json(REGULAR, "quotes__SPY.json")["quotes"]["quote"]
        calls: List[int] = []

        def flaky(path: str, query: Dict[str, str], headers: Dict[str, str]) -> Tuple[int, List[Tuple[str, str]], bytes]:
            calls.append(1)
            if len(calls) == 2:
                return 400, [], read_bytes(REGULAR, "errors", "quota_violation.txt")
            quotes = [dict(template, symbol=symbol) for symbol in query["symbols"].split(",")]
            return 200, [], json.dumps({"quotes": {"quote": quotes}}).encode("utf-8")

        self.server.script["/v1/markets/quotes"] = flaky
        with self.assertRaises(tc.TradierError) as caught:
            self.client.quotes(["C%03d" % index for index in range(60)])
        self.assertEqual("quota", caught.exception.status)
        self.assertEqual(["ok", "quota"], [receipt.status for receipt in self.receipts])
        self.assertEqual(2, len(self.requests()))

    def test_symbols_are_normalised_and_the_query_is_encoded(self) -> None:
        self.client.quotes(["spy", " wmt ", "SPY"])
        self.assertEqual("/v1/markets/quotes?symbols=SPY%2CWMT", self.requests()[-1]["path"])
        self.assertEqual(2, self.receipts[-1].symbols_count)
        self.client.option_expirations("SPY", include_all_roots=False)
        self.assertEqual("/v1/markets/options/expirations?symbol=SPY", self.requests()[-1]["path"])
        with self.assertRaises(tc.TradierError) as caught:
            self.client.calendar(3, 2026)  # no March fixture: the fake server answers 404
        self.assertEqual(("http", 404), (caught.exception.status, caught.exception.http_status))
        self.assertEqual("/v1/markets/calendar?month=03&year=2026", self.requests()[-1]["path"])

    def test_bad_symbol_and_refused_path_send_nothing(self) -> None:
        with self.assertRaises(tc.TradierError) as caught:
            self.client.quotes(["SPY", "SPY WMT"])
        self.assertEqual("bad_symbol", caught.exception.status)
        with self.assertRaises(tc.TradierError) as caught:
            self.client.get_json("/v1/user/profile", {})
        self.assertEqual("refused_path", caught.exception.status)
        self.assertEqual([], self.requests())
        self.assertEqual([], self.receipts)

    def test_get_json_is_the_single_request_primitive(self) -> None:
        document, receipt = self.client.get_json("/v1/markets/clock", {})
        self.assertEqual("open", document["clock"]["state"])
        self.assertEqual(("ok", 200, 0), (receipt.status, receipt.http_status, receipt.symbols_count))
        self.assertEqual([receipt], self.receipts)
        with self.assertRaises(TypeError):
            self.client.get_json("/v1/markets/quotes", {"symbols": 5})  # type: ignore[dict-item]

    def test_transport_error_carries_a_receipt_time_for_the_ledger(self) -> None:
        now = datetime(2026, 11, 3, 19, 50, 0, tzinfo=UTC)
        err = tc.TransportError("network", "simulated", receipt_utc=now, elapsed_ms=3)
        self.assertIsInstance(err, tc.TradierError)
        self.assertEqual((now, 3, "network: simulated"), (err.receipt_utc, err.elapsed_ms, str(err)))


# --------------------------------------------------------------------------------------------
# The AST gate over the finished client and this test module
# --------------------------------------------------------------------------------------------


class NoWritePathGateTest(unittest.TestCase):
    def test_client_is_the_registered_network_module(self) -> None:
        self.assertEqual("scripts/aegis_desk/tradier_client.py", check_no_write_paths.NETWORK_CLIENT)
        self.assertTrue(os.path.isfile(os.path.join(ROOT, check_no_write_paths.NETWORK_CLIENT)))

    def test_client_and_this_module_have_no_findings(self) -> None:
        for rel in ("scripts/aegis_desk/tradier_client.py", "tests/engine/test_tradier_client.py"):
            with open(os.path.join(ROOT, rel), encoding="utf-8") as handle:
                source = handle.read()
            findings = check_no_write_paths.ModuleGate(rel, source).run()
            self.assertEqual([], [finding.render() for finding in findings], rel)

    def test_ast_gate_passes_over_the_whole_tree(self) -> None:
        findings, scanned = check_no_write_paths.scan_python(ROOT)
        self.assertEqual([], [finding.render() for finding in findings])
        self.assertGreater(scanned, 40)

    def test_client_source_names_only_the_literal_get(self) -> None:
        with open(os.path.join(ROOT, "scripts", "aegis_desk", "tradier_client.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertIn('method="GET"', source)
        for verb in ('"POST"', '"PUT"', '"PATCH"', '"DELETE"'):
            self.assertNotIn(verb, source)
        self.assertNotIn("data=", source)
        # The same facts read from the AST, the way the gate reads them: every Request() call
        # carries the literal method, no attribute that decides a request's verb, body or target
        # is ever assigned, and nothing subclasses Request (the redirect handler is the one
        # urllib class the client derives from).
        tree = ast.parse(source)
        request_calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                         and ast.unparse(node.func) == "urllib.request.Request"]
        self.assertEqual(1, len(request_calls))
        for call in request_calls:
            methods = [kw.value.value for kw in call.keywords if kw.arg == "method"]
            self.assertEqual(["GET"], methods)
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


if __name__ == "__main__":
    unittest.main()
