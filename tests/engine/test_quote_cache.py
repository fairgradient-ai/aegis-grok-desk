"""Quote cache (``aegis_desk.quote_cache``): TTL serving, batching, the per-minute ledger and guards.

The client is a small in-test fake that exposes the brief's ``quotes`` / ``option_chain`` return
shapes, calls the ``on_request`` hook for every attempt (as the real client does) and raises an
error shaped like ``AlpacaError`` (``.status``), so this module never depends on the network
client (only the offline plumbing tests build the real one). Most tests run the guard with a 60/min budget and a
headroom of 5 (``CacheTestCase.cfg``) so the window arithmetic stays small; the defaults are pinned separately. Everything on disk is checked byte-for-byte against the documented envelopes.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import json
import os
import shutil
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence
from unittest import mock

from aegis_desk import alpaca_client, desk_config, desk_paths, market_clock, quote_cache, state
from aegis_desk.market_types import Quote, RequestReceipt

UTC = timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
NOW = datetime(2026, 11, 3, 19, 50, 0, tzinfo=UTC)
STAMP_MS = 1793734272000  # 2026-11-03T19:31:12Z
QUOTES_PATH = "/v2/stocks/quotes/latest"
CHAINS_PATH = "/v1beta1/options/snapshots/WMT"
EXPIRATIONS_PATH = "/v2/options/contracts"
WMT_EXPIRATIONS = ["2026-11-06", "2026-11-13", "2026-11-20", "2026-12-18", "2027-01-15"]
EXPIRY = date(2026, 11, 20)


def raw_quote(symbol: str, bid: float, ask: float, stamp_ms: int = STAMP_MS, **extra: Any) -> Dict[str, Any]:
    """A normalised quote record (the ``Quote`` field names; JSON numbers, epoch milliseconds)."""
    record: Dict[str, Any] = {
        "symbol": symbol, "description": symbol, "exch": "P", "type": "stock", "last": round((bid + ask) / 2, 2),
        "change": 0.1, "volume": 1000, "open": bid, "high": ask, "low": bid, "close": None, "bid": bid, "ask": ask,
        "change_percentage": 0.01, "average_volume": 2000, "last_volume": 100, "trade_date": stamp_ms,
        "prevclose": bid, "week_52_high": ask, "week_52_low": bid, "bidsize": 3, "bidexch": "P",
        "bid_date": stamp_ms, "asksize": 2, "askexch": "P", "ask_date": stamp_ms, "root_symbols": symbol,
    }
    record.update(extra)
    return record


def raw_option(symbol: str, strike: float, right: str, bid: float, ask: float) -> Dict[str, Any]:
    return raw_quote(symbol, bid, ask, type="option", underlying="WMT", strike=strike, open_interest=10,
                     contract_size=100, expiration_date="2026-11-20", expiration_type="standard",
                     option_type=right, root_symbol="WMT", greeks=None)


BOOK: Dict[str, Dict[str, Any]] = {
    "SPY": raw_quote("SPY", 648.10, 648.14),
    "WMT": raw_quote("WMT", 104.20, 104.26),
    "NVDA": raw_quote("NVDA", 148.90, 148.95),
    "WMT261120C00105000": raw_option("WMT261120C00105000", 105.0, "call", 3.35, 3.50),
}
CHAIN_WMT = [raw_option("WMT261120C00100000", 100.0, "call", 6.15, 6.35),
             raw_option("WMT261120P00100000", 100.0, "put", 1.75, 1.85),
             raw_option("WMT261120C00105000", 105.0, "call", 3.35, 3.50)]
HTTP_STATUS = {"quota": 429, "auth": 401, "subscription": 403, "http": 500, "network": None, "timeout": None, "decode": 200}


class ClientError(Exception):
    """Shaped like ``alpaca_client.AlpacaError``: ``status``, ``http_status``, ``path``, ``message``."""

    def __init__(self, status: str, http_status: Optional[int], path: str) -> None:
        self.status = status
        self.http_status = http_status
        self.path = path
        self.message = "fake %s failure" % status
        super().__init__("%s: %s" % (status, self.message))


@dataclasses.dataclass
class FakeQuotesResult:
    quotes: Dict[str, Quote]
    unmatched: List[str]
    receipt: RequestReceipt


@dataclasses.dataclass
class FakeChainResult:
    options: List[Quote]
    receipt: RequestReceipt


@dataclasses.dataclass
class FakeExpirationsResult:
    dates: List[date]
    receipt: RequestReceipt


class FakeClient:
    """``quotes`` and ``option_chain`` with the brief's return types; ``errors`` is consumed one
    status per call (``None`` = success); ``drop`` names symbols the vendor silently omits.

    Receipts are stamped ``now + tick * attempt``: the default ``tick`` of zero is the offline
    transport (every receipt at ``now``), a positive ``tick`` models the live transport, whose
    wall-clock stamps fall after the ``now`` a tool read at start. A status in ``NO_RECEIPT`` is
    raised before any attempt, exactly as the real client refuses a bad symbol or a path outside
    the allow-list: no receipt, no hook call.
    """

    NO_RECEIPT = ("bad_symbol", "refused_path", "refused_host")

    def __init__(self, book: Optional[Dict[str, Dict[str, Any]]] = None, chains: Optional[Dict] = None,
                 errors: Sequence[Optional[str]] = (), on_request=None, now: datetime = NOW,
                 drop: Sequence[str] = (), raw_less: Sequence[str] = (), tick: timedelta = timedelta(0),
                 expirations: Optional[Dict[str, List[str]]] = None) -> None:
        self.book = dict(BOOK if book is None else book)
        self.chains = dict(chains or {})
        self.expirations = dict(expirations or {})
        self.errors = list(errors)
        self.on_request = on_request
        self.now = now
        self.drop = set(drop)
        self.raw_less = set(raw_less)
        self.tick = tick
        self.attempts = 0
        self.calls: List[tuple] = []
        self.starts: List[date] = []

    def _attempt(self, path: str, count: int) -> RequestReceipt:
        status = self.errors.pop(0) if self.errors else None
        if status in self.NO_RECEIPT:
            raise ClientError(status, None, path)
        self.attempts += 1
        http_status = 200 if status is None else HTTP_STATUS.get(status)
        receipt = RequestReceipt(path, count, self.now + self.tick * self.attempts, status or "ok",
                                 http_status=http_status, ratelimit_allowed=60, ratelimit_used=1, ratelimit_available=59)
        if self.on_request is not None:
            self.on_request(receipt)
        if status is not None:
            raise ClientError(status, http_status, path)
        return receipt

    def quotes(self, symbols: Sequence[str]) -> FakeQuotesResult:
        symbols = list(symbols)
        self.calls.append(("quotes", symbols))
        receipt = self._attempt(QUOTES_PATH, len(symbols))
        quotes: Dict[str, Quote] = {}
        unmatched: List[str] = []
        for symbol in symbols:
            key = symbol.upper()
            if key in self.drop:
                continue
            if key in self.raw_less:
                quotes[key] = Quote(key, bid=Decimal("1.00"), ask=Decimal("1.10"))
                continue
            raw = self.book.get(key)
            if raw is None:
                unmatched.append(key)
            else:
                quotes[key] = Quote.from_record(raw)
        return FakeQuotesResult(quotes, unmatched, receipt)

    def option_chain(self, symbol: str, expiration: date) -> FakeChainResult:
        self.calls.append(("chain", symbol, expiration))
        receipt = self._attempt(CHAINS_PATH, 1)
        raws = self.chains.get((symbol.upper(), expiration), [])
        return FakeChainResult([Quote.from_record(raw) for raw in raws], receipt)

    def option_expirations(self, symbol: str, start: date, *,
                           window_days: int = alpaca_client.EXPIRATIONS_WINDOW_DAYS) -> FakeExpirationsResult:
        self.calls.append(("expirations", symbol))
        self.starts.append(start)
        receipt = self._attempt(EXPIRATIONS_PATH, 1)
        listed = self.expirations.get(symbol.upper(), [])
        return FakeExpirationsResult([date.fromisoformat(day) for day in listed], receipt)


def receipt_at(moment: datetime, status: str = "ok", path: str = QUOTES_PATH) -> RequestReceipt:
    return RequestReceipt(path, 1, moment, status, http_status=200 if status == "ok" else 400,
                          ratelimit_available=50)


class CacheTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-cache-")
        self.root = os.path.realpath(self._tmp.name)
        self.paths = desk_paths.for_root(self.root)
        self.cfg = quote_cache.CacheConfig(ttl_min=5, minute_budget=60, minute_headroom=5)
        self.hook = quote_cache.ledger_hook(self.paths)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def client(self, **kwargs: Any) -> FakeClient:
        return FakeClient(on_request=self.hook, **kwargs)

    def get(self, symbols: Sequence[str], client: FakeClient, now: datetime = NOW, cfg=None, **kwargs: Any):
        return quote_cache.get_quotes(symbols, now=now, cfg=cfg or self.cfg, client=client, paths=self.paths, **kwargs)

    def chain(self, client: FakeClient, symbol: str = "WMT", expiration: date = EXPIRY, now: datetime = NOW,
              cfg=None, **kwargs: Any):
        return quote_cache.get_chain(symbol, expiration, now=now, cfg=cfg or self.cfg, client=client,
                                     paths=self.paths, **kwargs)

    def expirations(self, client: FakeClient, symbol: str = "WMT", now: datetime = NOW, cfg=None, **kwargs: Any):
        return quote_cache.get_expirations(symbol, now=now, cfg=cfg or self.cfg, client=client, paths=self.paths,
                                           **kwargs)

    def read_cache(self) -> Dict[str, Any]:
        with open(quote_cache.quotes_path(self.paths), encoding="utf-8") as handle:
            return json.load(handle)

    def ledger_lines(self) -> List[Dict[str, Any]]:
        path = quote_cache.ledger_path(self.paths)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]

    def fill_ledger(self, count: int, moment: datetime) -> None:
        for _ in range(count):
            self.hook(receipt_at(moment))


class CacheConfigTest(unittest.TestCase):
    def test_from_desk_uses_quote_ttl_and_the_defaults(self) -> None:
        cfg = desk_config.load_desk_config(os.path.join(FIXTURE_DESK, "desk.toml"))
        cache = quote_cache.CacheConfig.from_desk(cfg)
        self.assertEqual(quote_cache.CacheConfig(ttl_min=5, per_run_max_requests=40, per_run_max_chains=4,
                                                 minute_budget=200, minute_headroom=20), cache)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cache.ttl_min = 1  # type: ignore[misc]

    def test_defaults(self) -> None:
        cache = quote_cache.CacheConfig(ttl_min=7)
        self.assertEqual((7, 40, 4, 200, 20), (cache.ttl_min, cache.per_run_max_requests, cache.per_run_max_chains,
                                               cache.minute_budget, cache.minute_headroom))
        self.assertEqual(50, quote_cache.MAX_BATCH)

    def test_the_budget_follows_the_vendor(self) -> None:
        self.assertEqual(alpaca_client.RATE_LIMIT_PER_MIN, quote_cache.MINUTE_BUDGET)
        self.assertEqual(alpaca_client.MAX_BATCH, quote_cache.MAX_BATCH)
        self.assertEqual(alpaca_client.SYMBOL_RE.pattern, quote_cache.SYMBOL_RE.pattern)
        # the headroom covers the most one call can send past the guard: two paginated listings (a chain)
        self.assertGreaterEqual(quote_cache.MINUTE_HEADROOM, 2 * alpaca_client.MAX_PAGES)
        self.assertLess(quote_cache.MINUTE_HEADROOM, quote_cache.MINUTE_BUDGET // 4)
        # one run never spends the minute on its own, and every chain of the cap fits in the request cap
        self.assertLess(quote_cache.PER_RUN_MAX_REQUESTS, quote_cache.MINUTE_BUDGET - quote_cache.MINUTE_HEADROOM)
        self.assertGreaterEqual(quote_cache.PER_RUN_MAX_REQUESTS, 2 * quote_cache.PER_RUN_MAX_CHAINS + 8)


class GetQuotesTest(CacheTestCase):
    def test_first_run_fetches_and_persists(self) -> None:
        client = self.client()
        result = self.get(["SPY", "WMT"], client)
        self.assertEqual([("quotes", ["SPY", "WMT"])], client.calls)
        self.assertEqual(["SPY", "WMT"], list(result.quotes))
        self.assertEqual(Decimal("648.1"), result.quotes["SPY"].bid)
        self.assertEqual(Decimal("104.26"), result.quotes["WMT"].ask)
        self.assertEqual({"SPY": "fetch", "WMT": "fetch"}, result.served_from)
        self.assertEqual({}, result.missing)
        self.assertEqual(1, result.requests_made)
        self.assertEqual(NOW, result.receipt_utc)
        self.assertEqual({"SPY": NOW, "WMT": NOW}, result.fetched_utc)
        self.assertEqual({}, result.stale)
        self.assertEqual([], result.warnings)
        document = self.read_cache()
        self.assertEqual(1, document["schema"])
        self.assertEqual({"SPY", "WMT"}, set(document["quotes"]))
        self.assertEqual(BOOK["SPY"], document["quotes"]["SPY"]["quote"], "the raw vendor dict is what is stored")
        self.assertEqual("2026-11-03T19:50:00+00:00", document["quotes"]["SPY"]["fetched_utc"])
        lines = self.ledger_lines()
        self.assertEqual(1, len(lines))
        self.assertEqual({"utc": "2026-11-03T19:50:00+00:00", "path": QUOTES_PATH, "symbols": 2, "status": "ok",
                          "http_status": 200, "ratelimit_available": 59}, lines[0])

    def test_library_creates_the_cache_directory_itself(self) -> None:
        self.assertFalse(os.path.isdir(self.paths.cache_dir))
        self.get(["SPY"], self.client())
        self.assertTrue(os.path.isfile(quote_cache.quotes_path(self.paths)))
        self.assertTrue(os.path.isfile(quote_cache.ledger_path(self.paths)))
        self.assertTrue(os.path.exists(self.paths.lock_path), "the write happened under the desk lock")

    def test_second_run_within_ttl_serves_from_cache(self) -> None:
        self.get(["SPY", "WMT"], self.client())
        client = self.client()
        result = self.get(["SPY", "WMT"], client, now=NOW + timedelta(minutes=4, seconds=59))
        self.assertEqual([], client.calls)
        self.assertEqual({"SPY": "cache", "WMT": "cache"}, result.served_from)
        self.assertEqual(0, result.requests_made)
        self.assertIsNone(result.receipt_utc)
        self.assertEqual(Decimal("648.1"), result.quotes["SPY"].bid)
        self.assertEqual(BOOK["SPY"], result.quotes["SPY"].raw)
        self.assertEqual({"SPY": NOW, "WMT": NOW}, result.fetched_utc)
        self.assertEqual(1, len(self.ledger_lines()), "a cache hit is not a request")

    def test_ttl_boundary_is_inclusive(self) -> None:
        self.get(["SPY"], self.client())
        at_ttl = self.client()
        self.assertEqual({"SPY": "cache"}, self.get(["SPY"], at_ttl, now=NOW + timedelta(minutes=5)).served_from)
        self.assertEqual([], at_ttl.calls)
        past_ttl = self.client()
        result = self.get(["SPY"], past_ttl, now=NOW + timedelta(minutes=5, seconds=1))
        self.assertEqual({"SPY": "fetch"}, result.served_from)
        self.assertEqual(1, len(past_ttl.calls))
        self.assertEqual("2026-11-03T19:55:01+00:00", self.read_cache()["quotes"]["SPY"]["fetched_utc"])

    def test_force_refetches_inside_the_ttl(self) -> None:
        self.get(["SPY"], self.client())
        client = self.client()
        result = self.get(["SPY"], client, force=True)
        self.assertEqual({"SPY": "fetch"}, result.served_from)
        self.assertEqual(1, len(client.calls))

    def test_partial_cache_hit_fetches_only_the_rest(self) -> None:
        self.get(["SPY"], self.client())
        client = self.client()
        result = self.get(["SPY", "WMT", "NVDA"], client)
        self.assertEqual([("quotes", ["WMT", "NVDA"])], client.calls)
        self.assertEqual({"SPY": "cache", "WMT": "fetch", "NVDA": "fetch"}, result.served_from)
        self.assertEqual(["SPY", "WMT", "NVDA"], list(result.quotes), "request order is kept")
        self.assertEqual({"SPY", "WMT", "NVDA"}, set(self.read_cache()["quotes"]), "the store merges")

    def test_unmatched_symbol(self) -> None:
        result = self.get(["SPY", "SPX"], self.client())
        self.assertEqual({"SPX": "unmatched"}, result.missing)
        self.assertEqual(["SPY"], list(result.quotes))
        self.assertNotIn("SPX", self.read_cache()["quotes"])
        self.assertEqual(1, result.requests_made)

    def test_symbol_silently_dropped_by_the_vendor_is_reported(self) -> None:
        result = self.get(["SPY", "WMT"], self.client(drop=["WMT"]))
        self.assertEqual({"WMT": "fetch_failed:not_returned"}, result.missing)
        self.assertTrue(any("WMT" in w for w in result.warnings), result.warnings)

    def test_quota_error_guards_the_run_and_keeps_stale_copies_out(self) -> None:
        self.get(["SPY"], self.client())
        client = self.client(errors=["quota"])
        result = self.get(["SPY", "WMT"], client, now=NOW + timedelta(minutes=10))
        self.assertEqual({"SPY": "quota_guard", "WMT": "quota_guard"}, result.missing)
        self.assertEqual({}, result.quotes)
        self.assertEqual(["SPY"], list(result.stale), "the expired copy is offered for display only")
        self.assertEqual(Decimal("648.1"), result.stale["SPY"].bid)
        self.assertEqual(1, result.requests_made)
        self.assertIsNone(result.receipt_utc, "no response was received; the attempt is in the ledger")
        self.assertTrue(any("quota" in w for w in result.warnings), result.warnings)
        lines = self.ledger_lines()
        self.assertEqual(("quota", 429), (lines[-1]["status"], lines[-1]["http_status"]))
        self.assertEqual("2026-11-03T19:50:00+00:00", self.read_cache()["quotes"]["SPY"]["fetched_utc"],
                         "a failed fetch never rewrites the cache")

    def test_other_client_errors_mark_the_batch(self) -> None:
        for status in ("network", "timeout", "http", "decode", "bad_request"):
            with self.subTest(status=status):
                result = self.get(["SPY", "WMT"], self.client(errors=[status]))
                self.assertEqual({"SPY": "fetch_failed:" + status, "WMT": "fetch_failed:" + status}, result.missing)
                self.assertEqual({}, result.quotes)

    def test_unexpected_exceptions_propagate(self) -> None:
        class Broken(FakeClient):
            def quotes(self, symbols):
                raise TypeError("a bug, not a vendor error")

        with self.assertRaises(TypeError):
            self.get(["SPY"], Broken(on_request=self.hook))

    def test_batching_over_max_batch(self) -> None:
        symbols = ["S%03d" % i for i in range(120)]
        book = {s: raw_quote(s, 10.0, 10.1) for s in symbols}
        client = self.client(book=book)
        result = self.get(symbols, client)
        self.assertEqual([50, 50, 20], [len(call[1]) for call in client.calls])
        self.assertEqual(3, result.requests_made)
        self.assertEqual(120, len(result.quotes))
        self.assertEqual({}, result.missing)
        self.assertEqual(3, len(self.ledger_lines()))

    def test_per_run_cap_marks_the_batches_it_refuses(self) -> None:
        symbols = ["S%03d" % i for i in range(120)]
        book = {s: raw_quote(s, 10.0, 10.1) for s in symbols}
        client = self.client(book=book)
        result = self.get(symbols, client, cfg=quote_cache.CacheConfig(ttl_min=5, per_run_max_requests=2))
        self.assertEqual(2, len(client.calls))
        self.assertEqual(2, result.requests_made)
        self.assertEqual(100, len(result.quotes))
        self.assertEqual({s: "per_run_cap" for s in symbols[100:]}, result.missing)
        self.assertTrue(any("per-run" in w for w in result.warnings), result.warnings)

    def test_run_counters_are_shared_across_calls(self) -> None:
        run = quote_cache.RunCounters()
        cfg = quote_cache.CacheConfig(ttl_min=5, per_run_max_requests=1)
        first = self.get(["SPY"], self.client(), cfg=cfg, run=run)
        self.assertEqual({"SPY": "fetch"}, first.served_from)
        client = self.client()
        second = self.get(["WMT"], client, cfg=cfg, run=run)
        self.assertEqual({"WMT": "per_run_cap"}, second.missing)
        self.assertEqual([], client.calls)
        self.assertEqual((1, 0), (run.requests, run.chains))
        third = self.get(["SPY"], self.client(), cfg=cfg, run=run)
        self.assertEqual({"SPY": "cache"}, third.served_from, "the cache still serves under the cap")

    def test_quota_error_stops_the_remaining_batches(self) -> None:
        symbols = ["S%03d" % i for i in range(120)]
        book = {s: raw_quote(s, 10.0, 10.1) for s in symbols}
        client = self.client(book=book, errors=["quota"])
        result = self.get(symbols, client)
        self.assertEqual(1, len(client.calls))
        self.assertEqual({s: "quota_guard" for s in symbols}, result.missing)

    def test_auth_error_stops_the_remaining_batches(self) -> None:
        symbols = ["S%03d" % i for i in range(120)]
        book = {s: raw_quote(s, 10.0, 10.1) for s in symbols}
        client = self.client(book=book, errors=["auth"])
        result = self.get(symbols, client)
        self.assertEqual(1, len(client.calls), "a rejected credential is not retried on the next batch")
        self.assertEqual({s: "fetch_failed:auth" for s in symbols}, result.missing)
        self.assertEqual(1, result.requests_made)

    def test_transient_error_does_not_stop_the_next_batch(self) -> None:
        symbols = ["S%03d" % i for i in range(120)]
        book = {s: raw_quote(s, 10.0, 10.1) for s in symbols}
        client = self.client(book=book, errors=["network", None, None])
        result = self.get(symbols, client)
        self.assertEqual(3, len(client.calls))
        self.assertEqual({s: "fetch_failed:network" for s in symbols[:50]}, result.missing)
        self.assertEqual(70, len(result.quotes))

    def test_budget_guard_reads_the_desk_wide_ledger(self) -> None:
        self.fill_ledger(55, NOW - timedelta(seconds=10))
        self.assertEqual(55, quote_cache.requests_in_window(self.paths, NOW))
        self.assertEqual(0, quote_cache.budget_left(self.paths, NOW, self.cfg))
        client = self.client()
        result = self.get(["SPY", "WMT"], client)
        self.assertEqual([], client.calls)
        self.assertEqual({"SPY": "quota_guard", "WMT": "quota_guard"}, result.missing)
        self.assertEqual(0, result.requests_made)
        self.assertTrue(any("budget" in w for w in result.warnings), result.warnings)

    def test_budget_is_re_read_before_every_batch(self) -> None:
        self.fill_ledger(53, NOW - timedelta(seconds=10))  # 60 - 5 - 53 = 2 requests left
        symbols = ["S%03d" % i for i in range(120)]
        book = {s: raw_quote(s, 10.0, 10.1) for s in symbols}
        client = self.client(book=book)
        result = self.get(symbols, client)
        self.assertEqual(2, len(client.calls))
        self.assertEqual({s: "quota_guard" for s in symbols[100:]}, result.missing)

    def test_old_ledger_lines_do_not_count(self) -> None:
        self.fill_ledger(55, NOW - timedelta(seconds=61))
        self.assertEqual(0, quote_cache.requests_in_window(self.paths, NOW))
        self.assertEqual(55, quote_cache.budget_left(self.paths, NOW, self.cfg))
        client = self.client()
        self.assertEqual({"SPY": "fetch"}, self.get(["SPY"], client).served_from)


class RunGuardsTest(CacheTestCase):
    """The run-wide guards: the budget counts this run's own receipts, a quota (or credential)
    error stops quotes and chains alike for the run, a malformed symbol is reported alone, and a
    refusal before anything is sent is not a request."""

    def symbols(self, count: int):
        symbols = ["S%03d" % i for i in range(count)]
        return symbols, {s: raw_quote(s, 10.0, 10.1) for s in symbols}

    def test_budget_counts_the_runs_own_receipts_stamped_after_now(self) -> None:
        self.fill_ledger(54, NOW - timedelta(seconds=10))  # 60 - 5 - 54: exactly one request left
        run = quote_cache.RunCounters()
        hook = quote_cache.ledger_hook(self.paths, run)
        symbols, book = self.symbols(150)
        client = FakeClient(book=book, on_request=hook, tick=timedelta(seconds=1))  # live: stamped after now
        result = self.get(symbols, client, run=run)
        self.assertEqual(1, len(client.calls), "the run's own receipt counts although the window cannot see it")
        self.assertEqual(1, result.requests_made)
        self.assertEqual({s: "quota_guard" for s in symbols[50:]}, result.missing)
        self.assertEqual(50, len(result.quotes))
        self.assertEqual(54, quote_cache.requests_in_window(self.paths, NOW), "the stamp lies after now")
        self.assertEqual(55, quote_cache.requests_counted(self.paths, NOW, run))
        self.assertEqual(0, quote_cache.budget_left(self.paths, NOW, self.cfg, run))
        self.assertEqual([NOW + timedelta(seconds=1)], run.receipt_stamps)
        chain_client = FakeClient(chains={("WMT", EXPIRY): CHAIN_WMT}, on_request=hook, tick=timedelta(seconds=1))
        chain = self.chain(chain_client, run=run)
        self.assertEqual(([], "quota_guard"), (chain_client.calls, chain.missing_reason))

    def test_receipts_stamped_at_now_are_not_counted_twice(self) -> None:
        self.fill_ledger(53, NOW - timedelta(seconds=10))  # two requests left
        run = quote_cache.RunCounters()
        hook = quote_cache.ledger_hook(self.paths, run)
        symbols, book = self.symbols(120)
        client = FakeClient(book=book, on_request=hook)  # offline: every receipt is stamped now
        result = self.get(symbols, client, run=run)
        self.assertEqual(2, len(client.calls))
        self.assertEqual({s: "quota_guard" for s in symbols[100:]}, result.missing)
        self.assertEqual(55, quote_cache.requests_in_window(self.paths, NOW))
        self.assertEqual(55, quote_cache.requests_counted(self.paths, NOW, run))
        self.assertEqual(0, quote_cache.budget_left(self.paths, NOW, self.cfg, run))

    def test_an_unwired_hook_keeps_the_ledger_only_rule(self) -> None:
        run = quote_cache.RunCounters()
        self.get(["SPY"], self.client(), run=run)
        self.assertEqual([], run.receipt_stamps)
        self.assertEqual(1, quote_cache.requests_counted(self.paths, NOW, run))
        self.assertEqual(1, quote_cache.requests_counted(self.paths, NOW))
        self.assertEqual(54, quote_cache.budget_left(self.paths, NOW, self.cfg))

    def test_quota_error_stops_quotes_and_chains_for_the_whole_run(self) -> None:
        self.get(["SPY"], self.client())  # a cached copy, served under the stop
        run = quote_cache.RunCounters()
        symbols, book = self.symbols(120)
        first = self.get(symbols, self.client(book=book, errors=["quota"]), run=run)
        self.assertEqual({s: "quota_guard" for s in symbols}, first.missing)
        self.assertEqual("quota_guard", run.stop_reason)
        chain_client = self.client(chains={("WMT", EXPIRY): CHAIN_WMT})
        chain = self.chain(chain_client, run=run)
        self.assertEqual(([], "quota_guard", 0), (chain_client.calls, chain.missing_reason, chain.requests_made))
        self.assertTrue(any("stopped" in w for w in chain.warnings), chain.warnings)
        later = self.client()
        second = self.get(["SPY", "WMT"], later, run=run)
        self.assertEqual([], later.calls, "no request reaches a vendor that answered quota in this run")
        self.assertEqual({"WMT": "quota_guard"}, second.missing)
        self.assertEqual({"SPY": "cache"}, second.served_from, "the cache still serves under the stop")
        self.assertEqual(0, second.requests_made)
        self.assertTrue(any("stopped" in w for w in second.warnings), second.warnings)
        self.assertEqual(2, len(self.ledger_lines()), "the cached fetch and the quota attempt, nothing after")

    def test_credential_error_stops_the_run_too(self) -> None:
        run = quote_cache.RunCounters()
        self.get(["SPY"], self.client(errors=["auth"]), run=run)
        self.assertEqual("fetch_failed:auth", run.stop_reason)
        client = self.client()
        self.assertEqual({"WMT": "fetch_failed:auth"}, self.get(["WMT"], client, run=run).missing)
        self.assertEqual([], client.calls)
        chain = self.chain(self.client(chains={("WMT", EXPIRY): CHAIN_WMT}), run=run)
        self.assertEqual("fetch_failed:auth", chain.missing_reason)

    def test_a_subscription_refusal_stops_the_run(self) -> None:
        # Alpaca's "subscription does not permit ..." answer recurs on every later request of the run
        self.assertIn("subscription", quote_cache.STOP_STATUSES)
        self.assertIn("no_credential", quote_cache.STOP_STATUSES)
        self.assertIn("credential_file_mode", quote_cache.STOP_STATUSES)
        self.assertNotIn("incomplete", quote_cache.STOP_STATUSES)
        run = quote_cache.RunCounters()
        result = self.get(["SPY"], self.client(errors=["subscription"]), run=run)
        self.assertEqual({"SPY": "fetch_failed:subscription"}, result.missing)
        self.assertEqual("fetch_failed:subscription", run.stop_reason)
        client = self.client()
        self.assertEqual({"WMT": "fetch_failed:subscription"}, self.get(["WMT"], client, run=run).missing)
        self.assertEqual([], client.calls)

    def test_transient_errors_do_not_stop_the_run(self) -> None:
        run = quote_cache.RunCounters()
        self.get(["SPY"], self.client(errors=["network"]), run=run)
        self.assertIsNone(run.stop_reason)
        self.assertEqual({"WMT": "fetch"}, self.get(["WMT"], self.client(), run=run).served_from)

    def test_bad_symbol_is_reported_alone_and_nothing_is_sent_for_it(self) -> None:
        client = self.client()
        result = self.get(["SPY", "bad symbol", "WMT"], client)
        self.assertEqual([("quotes", ["SPY", "WMT"])], client.calls, "the typo does not sink its batch")
        self.assertEqual({"BAD SYMBOL": "fetch_failed:bad_symbol"}, result.missing)
        self.assertEqual(["SPY", "WMT"], list(result.quotes))
        self.assertEqual(1, result.requests_made)
        self.assertEqual(1, len(self.ledger_lines()))
        self.assertTrue(any("BAD SYMBOL" in w and "nothing sent" in w for w in result.warnings), result.warnings)
        only_bad = self.client()
        result = self.get(["S P Y"], only_bad)
        self.assertEqual(([], 0, {"S P Y": "fetch_failed:bad_symbol"}), (only_bad.calls, result.requests_made, result.missing))
        self.assertEqual(1, len(self.ledger_lines()), "no ledger line for a request that was never made")
        self.assertEqual(r"^[A-Za-z0-9.$^:_-]{1,32}$", quote_cache.SYMBOL_RE.pattern)

    def test_bad_chain_symbol_sends_nothing(self) -> None:
        client = self.client()
        chain = self.chain(client, symbol="W MT")
        self.assertEqual(([], "fetch_failed:bad_symbol", 0), (client.calls, chain.missing_reason, chain.requests_made))
        self.assertEqual([], self.ledger_lines())

    def test_a_refusal_before_sending_is_not_a_request(self) -> None:
        for status in ("bad_symbol", "refused_path", "refused_host"):
            with self.subTest(status=status):
                run = quote_cache.RunCounters()
                result = self.get(["SPY"], self.client(errors=[status]), run=run)
                self.assertEqual({"SPY": "fetch_failed:" + status}, result.missing)
                self.assertEqual((0, 0), (result.requests_made, run.requests))
        self.assertEqual([], self.ledger_lines())
        run = quote_cache.RunCounters()
        chain = self.chain(self.client(errors=["bad_symbol"]), run=run)
        self.assertEqual(("fetch_failed:bad_symbol", 0, 0, 0),
                         (chain.missing_reason, chain.requests_made, run.requests, run.chains))
        sent = quote_cache.RunCounters()
        failed = self.chain(self.client(errors=["network"]), run=sent)
        self.assertEqual((1, 1, 1), (failed.requests_made, sent.requests, sent.chains), "a sent request counts")


class RequestCountTest(CacheTestCase):
    """One client call can send several requests (equities then options; a chain's contract listing then its snapshot
    pages): every request the transport saw counts toward the per-run cap and the minute budget."""

    def multi(self, per_call: int, **kwargs: Any) -> FakeClient:
        class Multi(FakeClient):
            def _attempt(self, path: str, count: int) -> RequestReceipt:
                receipt = super()._attempt(path, count)
                for _ in range(per_call - 1):
                    receipt = super()._attempt(path, count)
                return receipt

        return Multi(**kwargs)

    def test_a_wired_hook_counts_every_receipt_of_a_call(self) -> None:
        run = quote_cache.RunCounters()
        hook = quote_cache.ledger_hook(self.paths, run)
        result = self.get(["SPY", "WMT261120C00105000"], self.multi(2, on_request=hook), run=run)
        self.assertEqual((2, 2), (result.requests_made, run.requests))
        chain = self.chain(self.multi(3, on_request=hook, chains={("WMT", EXPIRY): CHAIN_WMT}), run=run)
        self.assertEqual((3, 5, 1), (chain.requests_made, run.requests, run.chains))
        self.assertEqual(5, len(self.ledger_lines()))
        self.assertEqual(5, quote_cache.requests_counted(self.paths, NOW, run))

    def test_without_a_wired_hook_the_result_receipts_count(self) -> None:
        @dataclasses.dataclass
        class Paged:
            options: List[Quote]
            receipt: RequestReceipt
            receipts: tuple

        class Client(FakeClient):
            def option_chain(self, symbol: str, expiration: date) -> Any:
                plain = super().option_chain(symbol, expiration)
                return Paged(plain.options, plain.receipt, (plain.receipt,) * 4)

        run = quote_cache.RunCounters()
        chain = self.chain(Client(on_request=self.hook, chains={("WMT", EXPIRY): CHAIN_WMT}), run=run)
        self.assertEqual((4, 4, 1), (chain.requests_made, run.requests, run.chains))

    def test_a_failure_after_some_pages_counts_the_pages_sent(self) -> None:
        run = quote_cache.RunCounters()
        hook = quote_cache.ledger_hook(self.paths, run)
        client = self.multi(3, on_request=hook, errors=[None, None, "network"])
        chain = self.chain(client, run=run)
        self.assertEqual(("fetch_failed:network", 3, 3, 1), (chain.missing_reason, chain.requests_made, run.requests,
                                                             run.chains))

    def test_the_per_run_cap_is_checked_before_each_call(self) -> None:
        run = quote_cache.RunCounters()
        hook = quote_cache.ledger_hook(self.paths, run)
        cfg = quote_cache.CacheConfig(ttl_min=5, per_run_max_requests=4)
        chains = {("WMT", EXPIRY): CHAIN_WMT, ("WMT", date(2026, 12, 18)): CHAIN_WMT}
        first = self.chain(self.multi(2, on_request=hook, chains=chains), cfg=cfg, run=run)
        second = self.chain(self.multi(2, on_request=hook, chains=chains), expiration=date(2026, 12, 18), cfg=cfg,
                            run=run)
        third = self.chain(self.multi(2, on_request=hook, chains=chains), cfg=cfg, run=run, force=True)
        self.assertEqual(("fetch", "fetch", 4), (first.served_from, second.served_from, run.requests))
        self.assertEqual("cache", third.served_from, "over the cap: the forced refetch falls back to the cached copy")
        self.assertTrue(any("per-run cap reached" in w for w in third.warnings), third.warnings)

    def test_a_chain_starts_only_when_its_minimum_cost_fits_under_the_cap(self) -> None:
        # A chain costs at least CHAIN_MIN_REQUESTS (the contract listing, then the snapshots): at 39 of 40 requests
        # it is refused with nothing sent; at 38 it starts (pagination may still carry one call past the cap).
        self.assertEqual(2, quote_cache.CHAIN_MIN_REQUESTS)
        cfg = quote_cache.CacheConfig(ttl_min=5, per_run_max_requests=40)
        run = quote_cache.RunCounters(requests=39)
        client = self.multi(2, chains={("WMT", EXPIRY): CHAIN_WMT})
        chain = self.chain(client, cfg=cfg, run=run)
        self.assertEqual(("per_run_cap", 0, 39, 0), (chain.missing_reason, chain.requests_made, run.requests,
                                                     run.chains))
        self.assertEqual([], client.calls)
        self.assertTrue(any("per-run cap reached" in w for w in chain.warnings), chain.warnings)
        run = quote_cache.RunCounters(requests=38)
        chain = self.chain(self.multi(2, on_request=quote_cache.ledger_hook(self.paths, run),
                                      chains={("WMT", EXPIRY): CHAIN_WMT}), cfg=cfg, run=run)
        self.assertEqual(("fetch", 40, 1), (chain.served_from, run.requests, run.chains))


class ForceTest(CacheTestCase):
    """``force`` asks for a fresher copy; it never throws a copy that is still valid away."""

    def test_force_on_a_non_trading_day_serves_the_cached_copy(self) -> None:
        self.get(["SPY"], self.client(), now=NOW - timedelta(days=2))
        client = self.client()
        result = self.get(["SPY"], client, force=True, allow_fetch=False)
        self.assertEqual(([], {"SPY": "cache"}, {}), (client.calls, result.served_from, result.missing))
        self.assertEqual(Decimal("648.1"), result.quotes["SPY"].bid)

    def test_failed_forced_refetch_keeps_a_copy_inside_the_ttl(self) -> None:
        self.get(["SPY"], self.client())
        client = self.client(errors=["network"])
        result = self.get(["SPY"], client, force=True, now=NOW + timedelta(minutes=1))
        self.assertEqual(1, len(client.calls))
        self.assertEqual(({"SPY": "cache"}, {}, {}), (result.served_from, result.missing, result.stale))
        self.assertEqual(Decimal("648.1"), result.quotes["SPY"].bid)
        self.assertEqual(NOW, result.fetched_utc["SPY"])
        self.assertEqual(1, result.requests_made)
        self.assertTrue(any("forced refetch" in w and "fetch_failed:network" in w for w in result.warnings),
                        result.warnings)

    def test_failed_forced_refetch_of_an_expired_copy_is_stale(self) -> None:
        self.get(["SPY"], self.client())
        result = self.get(["SPY"], self.client(errors=["network"]), force=True, now=NOW + timedelta(minutes=10))
        self.assertEqual(({}, {"SPY": "fetch_failed:network"}, ["SPY"]), (result.quotes, result.missing, list(result.stale)))

    def test_forced_refetch_that_answers_unmatched_drops_the_copy(self) -> None:
        self.get(["SPY"], self.client())
        result = self.get(["SPY"], self.client(book={}), force=True)  # the vendor no longer knows the symbol
        self.assertEqual(({}, {"SPY": "unmatched"}), (result.quotes, result.missing))

    def test_forced_refetch_under_the_budget_guard_keeps_the_copy(self) -> None:
        self.get(["SPY"], self.client())
        self.fill_ledger(55, NOW - timedelta(seconds=5))
        client = self.client()
        result = self.get(["SPY", "WMT"], client, force=True)
        self.assertEqual([], client.calls)
        self.assertEqual({"SPY": "cache"}, result.served_from)
        self.assertEqual({"WMT": "quota_guard"}, result.missing)

    def test_forced_chain_refetch_failure_keeps_a_copy_inside_the_ttl(self) -> None:
        chains = {("WMT", EXPIRY): CHAIN_WMT}
        self.chain(self.client(chains=chains))
        failed = self.chain(self.client(errors=["network"]), force=True, now=NOW + timedelta(minutes=1))
        self.assertEqual(("cache", None, 3, 1, NOW), (failed.served_from, failed.missing_reason, len(failed.options),
                                                      failed.requests_made, failed.fetched_utc))
        self.assertTrue(any("forced refetch" in w for w in failed.warnings), failed.warnings)
        expired = self.chain(self.client(errors=["network"]), force=True, now=NOW + timedelta(minutes=10))
        self.assertEqual((None, "fetch_failed:network", []), (expired.served_from, expired.missing_reason, expired.options))
        client = self.client(chains=chains)
        closed = self.chain(client, force=True, allow_fetch=False)
        self.assertEqual(("cache", []), (closed.served_from, client.calls))


class LedgerTest(CacheTestCase):
    def test_window_boundaries(self) -> None:
        self.hook(receipt_at(NOW - timedelta(seconds=60)))   # exactly 60 s ago: outside (now - 60 s, now]
        self.hook(receipt_at(NOW - timedelta(seconds=59)))   # inside
        self.hook(receipt_at(NOW))                            # inside (inclusive at now)
        self.hook(receipt_at(NOW + timedelta(seconds=1)))    # a future stamp (replay): not counted
        self.hook(receipt_at(NOW - timedelta(seconds=30), status="quota"))  # failures count too
        self.assertEqual(3, quote_cache.requests_in_window(self.paths, NOW))
        self.assertEqual(4, quote_cache.requests_in_window(self.paths, NOW, seconds=61))
        self.assertEqual(0, quote_cache.requests_in_window(self.paths, NOW - timedelta(hours=1)))

    def test_missing_ledger_is_zero(self) -> None:
        self.assertEqual(0, quote_cache.requests_in_window(self.paths, NOW))
        self.assertEqual(55, quote_cache.budget_left(self.paths, NOW, self.cfg))

    def test_malformed_and_truncated_lines_are_skipped(self) -> None:
        self.hook(receipt_at(NOW))
        path = quote_cache.ledger_path(self.paths)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("not json\n")
            handle.write(json.dumps({"path": QUOTES_PATH, "status": "ok"}) + "\n")  # no utc
            handle.write(json.dumps({"utc": "garbage", "path": QUOTES_PATH}) + "\n")
            handle.write('{"utc": "2026-11-03T19:50:00+00:00", "pa')  # truncated last line
        self.assertEqual(1, quote_cache.requests_in_window(self.paths, NOW))

    def test_only_the_tail_is_read(self) -> None:
        self.fill_ledger(3, NOW)
        with open(quote_cache.ledger_path(self.paths), "ab") as handle:
            handle.write(b"x" * (quote_cache.LEDGER_TAIL_BYTES * 2) + b"\n")
        self.fill_ledger(2, NOW)
        self.assertEqual(2, quote_cache.requests_in_window(self.paths, NOW))

    def test_ledger_record_shape(self) -> None:
        receipt = RequestReceipt(QUOTES_PATH, 3, NOW, "ok", http_status=200, ratelimit_allowed=60,
                                 ratelimit_used=2, ratelimit_available=58, ratelimit_expiry_utc=NOW, elapsed_ms=120)
        self.assertEqual({"utc": NOW, "path": QUOTES_PATH, "symbols": 3, "status": "ok", "http_status": 200,
                          "ratelimit_available": 58}, quote_cache.ledger_record(receipt))
        bare = RequestReceipt(CHAINS_PATH, 1, NOW, "network")
        self.assertEqual({"utc": NOW, "path": CHAINS_PATH, "symbols": 1, "status": "network", "http_status": None,
                          "ratelimit_available": None}, quote_cache.ledger_record(bare))
        self.hook(bare)
        self.assertEqual(["utc", "path", "symbols", "status", "http_status", "ratelimit_available"],
                         sorted(self.ledger_lines()[0], key=["utc", "path", "symbols", "status", "http_status",
                                                              "ratelimit_available"].index))

    def test_paths(self) -> None:
        self.assertEqual(os.path.join(self.paths.cache_dir, "quotes.json"), quote_cache.quotes_path(self.paths))
        self.assertEqual(os.path.join(self.paths.cache_dir, "ledger.jsonl"), quote_cache.ledger_path(self.paths))
        self.assertEqual(os.path.join(self.paths.cache_dir, "chains", "WMT__2026-11-20.json"),
                         quote_cache.chain_path(self.paths, "wmt", EXPIRY))


class CacheFileRobustnessTest(CacheTestCase):
    def test_corrupt_cache_file_is_quarantined_and_refetched(self) -> None:
        os.makedirs(self.paths.cache_dir)
        with open(quote_cache.quotes_path(self.paths), "w", encoding="utf-8") as handle:
            handle.write("{not json")
        client = self.client()
        result = self.get(["SPY"], client)
        self.assertEqual({"SPY": "fetch"}, result.served_from)
        self.assertTrue(any("quarantined" in w for w in result.warnings), result.warnings)
        names = os.listdir(self.paths.cache_dir)
        self.assertTrue(any(name.startswith("quotes.json.corrupt-") for name in names), names)
        self.assertEqual(1, self.read_cache()["schema"])

    def test_unexpected_shape_is_ignored_and_overwritten(self) -> None:
        state.atomic_write_json(quote_cache.quotes_path(self.paths), {"schema": 2, "quotes": {"SPY": {}}})
        result = self.get(["SPY"], self.client())
        self.assertEqual({"SPY": "fetch"}, result.served_from)
        self.assertTrue(any("shape" in w for w in result.warnings), result.warnings)
        document = self.read_cache()
        self.assertEqual(1, document["schema"])
        self.assertEqual(BOOK["SPY"], document["quotes"]["SPY"]["quote"])

    def test_garbled_entry_is_refetched_with_a_warning(self) -> None:
        self.get(["SPY", "WMT"], self.client())
        document = self.read_cache()
        document["quotes"]["SPY"]["quote"]["bid"] = "abc"
        document["quotes"]["WMT"]["fetched_utc"] = "yesterday"
        state.atomic_write_json(quote_cache.quotes_path(self.paths), document)
        client = self.client()
        result = self.get(["SPY", "WMT"], client)
        self.assertEqual([("quotes", ["SPY", "WMT"])], client.calls)
        self.assertEqual(2, len([w for w in result.warnings if "SPY" in w or "WMT" in w]), result.warnings)
        self.assertEqual(BOOK["SPY"], self.read_cache()["quotes"]["SPY"]["quote"])

    def test_entry_from_the_future_is_not_trusted(self) -> None:
        self.get(["SPY"], self.client(), now=NOW + timedelta(minutes=10))
        client = self.client()
        result = self.get(["SPY"], client, now=NOW)
        self.assertEqual({"SPY": "fetch"}, result.served_from)
        self.assertEqual(1, len(client.calls))

    def test_quote_without_a_vendor_record_is_served_but_not_cached(self) -> None:
        result = self.get(["ZZZ"], self.client(raw_less=["ZZZ"]))
        self.assertEqual(Decimal("1.00"), result.quotes["ZZZ"].bid)
        self.assertEqual({"ZZZ": "fetch"}, result.served_from)
        self.assertTrue(any("ZZZ" in w and "not cached" in w for w in result.warnings), result.warnings)
        self.assertFalse(os.path.exists(quote_cache.quotes_path(self.paths)))

    def test_lock_busy_keeps_the_quotes_and_warns(self) -> None:
        with mock.patch.object(quote_cache, "LOCK_TIMEOUT_S", 0.05):
            with state.locked(self.paths.lock_path):
                result = self.get(["SPY"], self.client())
        self.assertEqual({"SPY": "fetch"}, result.served_from)
        self.assertEqual(Decimal("648.1"), result.quotes["SPY"].bid)
        self.assertTrue(any("lock" in w for w in result.warnings), result.warnings)
        self.assertFalse(os.path.exists(quote_cache.quotes_path(self.paths)))


class CacheOnlyModeTest(CacheTestCase):
    def test_non_trading_day_serves_any_cached_copy_and_never_fetches(self) -> None:
        self.get(["SPY"], self.client(), now=NOW - timedelta(days=2))
        client = self.client()
        result = self.get(["SPY", "WMT"], client, allow_fetch=False)
        self.assertEqual([], client.calls)
        self.assertEqual({"SPY": "cache"}, result.served_from)
        self.assertEqual({"WMT": "market_closed"}, result.missing)
        self.assertEqual(0, result.requests_made)
        self.assertTrue(any("SPY" in w and "non-trading day" in w for w in result.warnings), result.warnings)
        self.assertEqual(NOW - timedelta(days=2), result.fetched_utc["SPY"])

    def test_fresh_copy_in_cache_only_mode_has_no_warning(self) -> None:
        self.get(["SPY"], self.client())
        result = self.get(["SPY"], self.client(), allow_fetch=False)
        self.assertEqual({"SPY": "cache"}, result.served_from)
        self.assertEqual([], result.warnings)


class InputTest(CacheTestCase):
    def test_symbols_are_upper_cased_stripped_and_deduplicated(self) -> None:
        client = self.client()
        result = self.get(["spy", " SPY", "wmt "], client)
        self.assertEqual([("quotes", ["SPY", "WMT"])], client.calls)
        self.assertEqual(["SPY", "WMT"], list(result.quotes))

    def test_blank_symbol_and_naive_now_are_programming_errors(self) -> None:
        with self.assertRaises(ValueError):
            self.get(["SPY", ""], self.client())
        with self.assertRaises(ValueError):
            self.get(["SPY"], self.client(), now=datetime(2026, 11, 3, 19, 50))
        with self.assertRaises(ValueError):
            self.get("SPY", self.client())  # type: ignore[arg-type]

    def test_empty_request_makes_no_call(self) -> None:
        client = self.client()
        result = self.get([], client)
        self.assertEqual(({}, {}, 0), (result.quotes, result.missing, result.requests_made))
        self.assertEqual([], client.calls)

    def test_result_is_frozen(self) -> None:
        result = self.get(["SPY"], self.client())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.requests_made = 9  # type: ignore[misc]


class GetChainTest(CacheTestCase):
    def chains(self) -> Dict:
        return {("WMT", EXPIRY): CHAIN_WMT}

    def test_fetch_and_persist(self) -> None:
        client = self.client(chains=self.chains())
        result = self.chain(client)
        self.assertEqual([("chain", "WMT", EXPIRY)], client.calls)
        self.assertEqual(("WMT", EXPIRY, "fetch", None, 1, NOW), (result.symbol, result.expiration, result.served_from,
                                                                   result.missing_reason, result.requests_made,
                                                                   result.receipt_utc))
        self.assertEqual(["WMT261120C00100000", "WMT261120P00100000", "WMT261120C00105000"],
                         [q.symbol for q in result.options])
        self.assertEqual(Decimal("105"), result.options[2].strike)
        self.assertEqual(NOW, result.fetched_utc)
        path = quote_cache.chain_path(self.paths, "WMT", EXPIRY)
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual({"schema": 1, "symbol": "WMT", "expiration": "2026-11-20",
                          "fetched_utc": "2026-11-03T19:50:00+00:00", "options": CHAIN_WMT}, document)
        self.assertEqual([CHAINS_PATH], [line["path"] for line in self.ledger_lines()])

    def test_served_from_cache_within_ttl_and_refetched_after(self) -> None:
        self.chain(self.client(chains=self.chains()))
        client = self.client()
        result = self.chain(client, now=NOW + timedelta(minutes=5))
        self.assertEqual([], client.calls)
        self.assertEqual("cache", result.served_from)
        self.assertEqual(3, len(result.options))
        self.assertEqual(NOW, result.fetched_utc)
        later = self.client(chains=self.chains())
        self.assertEqual("fetch", self.chain(later, now=NOW + timedelta(minutes=5, seconds=1)).served_from)
        forced = self.client(chains=self.chains())
        self.assertEqual("fetch", self.chain(forced, force=True).served_from)

    def test_empty_chain_is_a_warning_not_an_error(self) -> None:
        result = self.chain(self.client(), symbol="NVDA")
        self.assertEqual(([], "fetch", None), (result.options, result.served_from, result.missing_reason))
        self.assertTrue(any("empty" in w for w in result.warnings), result.warnings)
        self.assertTrue(os.path.isfile(quote_cache.chain_path(self.paths, "NVDA", EXPIRY)))

    def test_per_run_chain_cap_and_request_counting(self) -> None:
        run = quote_cache.RunCounters()
        cfg = quote_cache.CacheConfig(ttl_min=5, per_run_max_chains=1)
        first = self.chain(self.client(chains=self.chains()), cfg=cfg, run=run)
        self.assertEqual("fetch", first.served_from)
        self.assertEqual((1, 1), (run.requests, run.chains))
        client = self.client()
        second = self.chain(client, symbol="NVDA", cfg=cfg, run=run)
        self.assertEqual([], client.calls)
        self.assertEqual((None, "per_run_cap", 0), (second.served_from, second.missing_reason, second.requests_made))
        cached = self.chain(self.client(), cfg=cfg, run=run)
        self.assertEqual("cache", cached.served_from, "the cache still serves under the cap")

    def test_chain_counts_against_the_request_cap(self) -> None:
        run = quote_cache.RunCounters()
        cfg = quote_cache.CacheConfig(ttl_min=5, per_run_max_requests=1)
        self.get(["SPY"], self.client(), cfg=cfg, run=run)
        result = self.chain(self.client(chains=self.chains()), cfg=cfg, run=run)
        self.assertEqual("per_run_cap", result.missing_reason)

    def test_budget_guard(self) -> None:
        self.fill_ledger(55, NOW - timedelta(seconds=5))
        client = self.client(chains=self.chains())
        result = self.chain(client)
        self.assertEqual([], client.calls)
        self.assertEqual("quota_guard", result.missing_reason)
        self.assertEqual([], result.options)

    def test_errors(self) -> None:
        quota = self.chain(self.client(errors=["quota"]))
        self.assertEqual(("quota_guard", [], 1), (quota.missing_reason, quota.options, quota.requests_made))
        network = self.chain(self.client(errors=["network"]))
        self.assertEqual("fetch_failed:network", network.missing_reason)
        self.assertTrue(any("network" in w for w in network.warnings), network.warnings)
        self.assertFalse(os.path.exists(quote_cache.chain_path(self.paths, "WMT", EXPIRY)))

    def test_cache_only_mode(self) -> None:
        client = self.client(chains=self.chains())
        absent = self.chain(client, allow_fetch=False)
        self.assertEqual(("market_closed", []), (absent.missing_reason, client.calls))
        self.chain(self.client(chains=self.chains()), now=NOW - timedelta(days=3))
        old = self.chain(self.client(), allow_fetch=False)
        self.assertEqual("cache", old.served_from)
        self.assertEqual(3, len(old.options))
        self.assertTrue(any("non-trading day" in w for w in old.warnings), old.warnings)

    def test_corrupt_chain_file_is_quarantined_and_refetched(self) -> None:
        path = quote_cache.chain_path(self.paths, "WMT", EXPIRY)
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("[")
        result = self.chain(self.client(chains=self.chains()))
        self.assertEqual("fetch", result.served_from)
        self.assertTrue(any("quarantined" in w for w in result.warnings), result.warnings)

    def test_mismatched_chain_document_is_refetched(self) -> None:
        path = quote_cache.chain_path(self.paths, "WMT", EXPIRY)
        state.atomic_write_json(path, {"schema": 1, "symbol": "NVDA", "expiration": "2026-11-20",
                                       "fetched_utc": "2026-11-03T19:50:00+00:00", "options": []})
        result = self.chain(self.client(chains=self.chains()))
        self.assertEqual("fetch", result.served_from)
        self.assertEqual(3, len(result.options))

    def test_expiration_must_be_a_date(self) -> None:
        with self.assertRaises(ValueError):
            self.chain(self.client(), expiration="2026-11-20")  # type: ignore[arg-type]


class GetExpirationsTest(CacheTestCase):
    """``get_expirations`` (PR 3b, designer Q's D3 item 2): the listed expirations of one underlying, cached at
    ``data/cache/expirations/<SYMBOL>.json`` and served without a refetch on the ET date it was fetched (a list does not
    change within a session), with ``get_chain``'s budget guard, stop and no-send rules and warning words; it counts
    against ``per_run_max_requests`` and never against the chain cap."""

    def listed(self) -> Dict[str, List[str]]:
        return {"WMT": list(WMT_EXPIRATIONS)}

    def test_result_shape_and_path(self) -> None:
        fields = [item.name for item in dataclasses.fields(quote_cache.ExpirationsCacheResult)]
        self.assertEqual(["symbol", "dates", "served_from", "missing_reason", "requests_made", "receipt_utc", "warnings",
                          "fetched_utc"], fields)
        self.assertEqual("expirations", quote_cache.EXPIRATIONS_SUBDIR)
        self.assertEqual(os.path.join(self.root, "data", "cache", "expirations", "WMT.json"),
                         quote_cache.expirations_path(self.paths, " wmt "))
        self.assertEqual(os.path.join(self.paths.cache_dir, "expirations", "SPY.json"),
                         quote_cache.expirations_path(self.paths, "SPY"))
        for name in ("EXPIRATIONS_SUBDIR", "ExpirationsCacheResult", "expirations_path", "get_expirations"):
            self.assertIn(name, quote_cache.__all__)
        result = self.expirations(self.client(expirations=self.listed()))
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.requests_made = 9  # type: ignore[misc]

    def test_fetch_and_persist(self) -> None:
        run = quote_cache.RunCounters()
        client = self.client(expirations=self.listed())
        result = self.expirations(client, symbol=" wmt ", run=run)
        self.assertEqual([("expirations", "WMT")], client.calls)
        self.assertEqual([date(2026, 11, 3)], client.starts, "the listing starts on the ET date of now")
        self.assertEqual(("WMT", "fetch", None, 1, NOW, NOW), (result.symbol, result.served_from, result.missing_reason,
                                                               result.requests_made, result.receipt_utc, result.fetched_utc))
        self.assertEqual([date.fromisoformat(day) for day in WMT_EXPIRATIONS], result.dates)
        self.assertEqual([], result.warnings)
        self.assertEqual((1, 0), (run.requests, run.chains), "a request, never a chain")
        with open(quote_cache.expirations_path(self.paths, "WMT"), encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual({"schema": 1, "symbol": "WMT", "fetched_utc": "2026-11-03T19:50:00+00:00",
                          "dates": WMT_EXPIRATIONS}, document)
        self.assertEqual([{"utc": "2026-11-03T19:50:00+00:00", "path": EXPIRATIONS_PATH, "symbols": 1, "status": "ok",
                           "http_status": 200, "ratelimit_available": 59}], self.ledger_lines())

    def test_served_without_a_refetch_on_the_same_et_date(self) -> None:
        self.expirations(self.client(expirations=self.listed()))
        last_minute = datetime(2026, 11, 4, 4, 59, 59, tzinfo=UTC)  # 23:59:59 ET on 2026-11-03
        self.assertEqual(date(2026, 11, 3), market_clock.to_et(last_minute).date())
        for moment in (NOW, NOW + timedelta(hours=2), last_minute):
            with self.subTest(moment=moment):
                client = self.client()
                result = self.expirations(client, now=moment)
                self.assertEqual(([], "cache", 0, NOW), (client.calls, result.served_from, result.requests_made,
                                                         result.fetched_utc))
                self.assertEqual(5, len(result.dates))
                self.assertIsNone(result.receipt_utc)
        next_day = datetime(2026, 11, 4, 5, 0, 0, tzinfo=UTC)  # 00:00 ET on 2026-11-04: a new ET date
        client = self.client(expirations={"WMT": WMT_EXPIRATIONS[1:]})
        result = self.expirations(client, now=next_day)
        self.assertEqual(([("expirations", "WMT")], "fetch", 4), (client.calls, result.served_from, len(result.dates)))
        self.assertEqual("2026-11-04T05:00:00+00:00", self.read_expirations("WMT")["fetched_utc"])

    def test_the_quote_ttl_does_not_apply(self) -> None:
        self.expirations(self.client(expirations=self.listed()))
        client = self.client()
        later = NOW + timedelta(minutes=self.cfg.ttl_min * 20)
        self.assertEqual("cache", self.expirations(client, now=later).served_from)
        self.assertEqual([], client.calls)

    def test_a_copy_stamped_in_the_future_is_not_trusted(self) -> None:
        self.expirations(self.client(expirations=self.listed()), now=NOW + timedelta(minutes=1))
        client = self.client(expirations=self.listed())
        self.assertEqual("fetch", self.expirations(client).served_from)
        self.assertEqual(1, len(client.calls))

    def test_force_refetches_and_keeps_the_same_day_copy_when_it_cannot(self) -> None:
        self.expirations(self.client(expirations=self.listed()))
        forced = self.client(expirations={"WMT": WMT_EXPIRATIONS[:2]})
        result = self.expirations(forced, force=True)
        self.assertEqual(("fetch", 2), (result.served_from, len(result.dates)))
        failing = self.client(errors=["network"])
        kept = self.expirations(failing, force=True)
        self.assertEqual(("cache", None, 1, 2), (kept.served_from, kept.missing_reason, kept.requests_made,
                                                 len(kept.dates)))
        self.assertTrue(any("forced refetch did not happen (fetch_failed:network)" in w for w in kept.warnings),
                        kept.warnings)

    def test_cache_only_mode_serves_any_copy(self) -> None:
        client = self.client(expirations=self.listed())
        absent = self.expirations(client, allow_fetch=False)
        self.assertEqual((None, "market_closed", [], 0), (absent.served_from, absent.missing_reason, absent.dates,
                                                          absent.requests_made))
        self.assertEqual([], client.calls)
        self.expirations(self.client(expirations=self.listed()), now=NOW - timedelta(days=3))
        old = self.expirations(self.client(), allow_fetch=False)
        self.assertEqual(("cache", 5), (old.served_from, len(old.dates)))
        self.assertTrue(any("non-trading day" in w for w in old.warnings), old.warnings)
        forced = self.client(expirations=self.listed())
        self.assertEqual("cache", self.expirations(forced, allow_fetch=False, force=True).served_from)
        self.assertEqual([], forced.calls, "force never fetches on a non-trading day")

    def test_counts_against_the_request_cap_not_the_chain_cap(self) -> None:
        no_chains = quote_cache.CacheConfig(ttl_min=5, per_run_max_chains=0)
        run = quote_cache.RunCounters()
        self.assertEqual("fetch", self.expirations(self.client(expirations=self.listed()), cfg=no_chains,
                                                   run=run).served_from)
        self.assertEqual((1, 0), (run.requests, run.chains))
        one = quote_cache.CacheConfig(ttl_min=5, per_run_max_requests=1)
        capped = quote_cache.RunCounters()
        self.get(["SPY"], self.client(), cfg=one, run=capped)
        client = self.client(expirations={"NVDA": ["2026-12-18"]})
        result = self.expirations(client, symbol="NVDA", cfg=one, run=capped)
        self.assertEqual(([], None, "per_run_cap", 0), (client.calls, result.served_from, result.missing_reason,
                                                        result.requests_made))
        self.assertTrue(any("per-run" in w and "cap" in w for w in result.warnings), result.warnings)
        self.assertEqual("cache", self.expirations(self.client(), cfg=one, run=capped).served_from,
                         "the cache still serves under the cap")
        chain_run = quote_cache.RunCounters()
        two = quote_cache.CacheConfig(ttl_min=5, per_run_max_requests=1)
        self.expirations(self.client(expirations={"SPY": ["2026-12-18"]}), symbol="SPY", cfg=two, run=chain_run)
        chain = self.chain(self.client(chains={("WMT", EXPIRY): CHAIN_WMT}), cfg=two, run=chain_run)
        self.assertEqual("per_run_cap", chain.missing_reason, "the expirations request counts for the chain's cap")

    def test_budget_guard(self) -> None:
        self.fill_ledger(55, NOW - timedelta(seconds=5))
        client = self.client(expirations=self.listed())
        result = self.expirations(client)
        self.assertEqual(([], "quota_guard", [], 0), (client.calls, result.missing_reason, result.dates,
                                                      result.requests_made))
        self.assertTrue(any("budget exhausted" in w for w in result.warnings), result.warnings)

    def test_the_budget_counts_the_runs_own_receipts(self) -> None:
        self.fill_ledger(54, NOW - timedelta(seconds=10))  # 60 - 5 - 54: exactly one request left
        run = quote_cache.RunCounters()
        hook = quote_cache.ledger_hook(self.paths, run)
        client = FakeClient(expirations={"WMT": WMT_EXPIRATIONS, "NVDA": ["2026-12-18"]}, on_request=hook,
                            tick=timedelta(seconds=1))  # live: the receipt is stamped after now
        first = self.expirations(client, run=run)
        second = self.expirations(client, symbol="NVDA", run=run)
        self.assertEqual(("fetch", None, "quota_guard"), (first.served_from, second.served_from, second.missing_reason))
        self.assertEqual([("expirations", "WMT")], client.calls)
        self.assertEqual([NOW + timedelta(seconds=1)], run.receipt_stamps)

    def test_errors_stop_rules_and_the_ledger(self) -> None:
        quota_run = quote_cache.RunCounters()
        quota = self.expirations(self.client(errors=["quota"]), run=quota_run)
        self.assertEqual(("quota_guard", [], 1, "quota_guard"), (quota.missing_reason, quota.dates,
                                                                quota.requests_made, quota_run.stop_reason))
        later = self.client(chains={("WMT", EXPIRY): CHAIN_WMT}, expirations=self.listed())
        self.assertEqual("quota_guard", self.chain(later, run=quota_run).missing_reason)
        self.assertEqual({"SPY": "quota_guard"}, self.get(["SPY"], later, run=quota_run).missing)
        stopped = self.expirations(later, symbol="NVDA", run=quota_run)
        self.assertEqual(("quota_guard", 0), (stopped.missing_reason, stopped.requests_made))
        self.assertTrue(any("stopped" in w for w in stopped.warnings), stopped.warnings)
        self.assertEqual([], later.calls)
        auth_run = quote_cache.RunCounters()
        auth = self.expirations(self.client(errors=["auth"]), symbol="SPY", run=auth_run)
        self.assertEqual(("fetch_failed:auth", "fetch_failed:auth"), (auth.missing_reason, auth_run.stop_reason))
        network_run = quote_cache.RunCounters()
        network = self.expirations(self.client(errors=["network"]), symbol="NVDA", run=network_run)
        self.assertEqual(("fetch_failed:network", None, 1, 1), (network.missing_reason, network_run.stop_reason,
                                                                network.requests_made, network_run.requests))
        self.assertTrue(any("expirations fetch failed for NVDA" in w and "network" in w for w in network.warnings),
                        network.warnings)
        self.assertFalse(os.path.exists(quote_cache.expirations_path(self.paths, "NVDA")))
        self.assertEqual(3, len([line for line in self.ledger_lines() if line["path"] == EXPIRATIONS_PATH]),
                         "every attempt that was sent is a ledger line, failures too")

    def test_a_refusal_before_sending_is_not_a_request(self) -> None:
        for status, stops in (("bad_symbol", False), ("refused_path", True), ("refused_host", True)):
            with self.subTest(status=status):
                run = quote_cache.RunCounters()
                result = self.expirations(self.client(errors=[status]), run=run)
                self.assertEqual(("fetch_failed:" + status, 0, 0), (result.missing_reason, result.requests_made,
                                                                    run.requests))
                self.assertEqual("fetch_failed:" + status if stops else None, run.stop_reason,
                                 "a refused path or host would recur: the run stops")
        self.assertEqual([], self.ledger_lines())
        client = self.client()
        bad = self.expirations(client, symbol="W MT")
        self.assertEqual(([], "fetch_failed:bad_symbol", 0), (client.calls, bad.missing_reason, bad.requests_made))
        self.assertTrue(any("nothing sent" in w for w in bad.warnings), bad.warnings)

    def test_an_empty_list_is_neither_served_nor_cached(self) -> None:
        # review L3-5: an empty answer is no data; cached for the ET date it would make every later run evaluate
        # nothing as if complete. It is reported missing (``empty_list``) and never stored.
        result = self.expirations(self.client(), symbol="AAPL")
        self.assertEqual(([], None, quote_cache.REASON_EMPTY_LIST, 1),
                         (result.dates, result.served_from, result.missing_reason, result.requests_made))
        self.assertEqual("empty_list", quote_cache.REASON_EMPTY_LIST)
        self.assertTrue(any("empty" in w for w in result.warnings), result.warnings)
        self.assertFalse(os.path.exists(quote_cache.expirations_path(self.paths, "AAPL")))
        again = self.expirations(self.client(), symbol="AAPL")
        self.assertEqual((None, quote_cache.REASON_EMPTY_LIST, 1),
                         (again.served_from, again.missing_reason, again.requests_made))

    def test_an_empty_cached_copy_is_never_served(self) -> None:
        path = quote_cache.expirations_path(self.paths, "WMT")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"schema": 1, "symbol": "WMT", "fetched_utc": "2026-11-03T19:50:00Z", "dates": []}, handle)
        result = self.expirations(self.client(expirations=self.listed()), symbol="WMT")
        self.assertEqual(("fetch", 1), (result.served_from, result.requests_made))
        self.assertTrue(result.dates)
        self.assertTrue(any("empty" in w for w in result.warnings), result.warnings)

    def test_unusable_cache_files_are_refetched(self) -> None:
        path = quote_cache.expirations_path(self.paths, "WMT")
        os.makedirs(os.path.dirname(path))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("[")
        corrupt = self.expirations(self.client(expirations=self.listed()))
        self.assertEqual("fetch", corrupt.served_from)
        self.assertTrue(any("quarantined" in w for w in corrupt.warnings), corrupt.warnings)
        for document in ({"schema": 1, "symbol": "NVDA", "fetched_utc": "2026-11-03T19:50:00+00:00", "dates": []},
                         {"schema": 1, "symbol": "WMT", "fetched_utc": "later", "dates": []},
                         {"schema": 1, "symbol": "WMT", "fetched_utc": "2026-11-03T19:50:00+00:00", "dates": "x"},
                         {"schema": 1, "symbol": "WMT", "fetched_utc": "2026-11-03T19:50:00+00:00",
                          "dates": ["2026-13-01"]},
                         {"schema": 1, "symbol": "WMT", "fetched_utc": "2026-11-03T19:50:00+00:00", "dates": [20261218]},
                         {"schema": 1, "symbol": "WMT", "fetched_utc": "2026-11-03T19:50:00+00:00", "dates": ["20261218"]},
                         {"schema": 2, "symbol": "WMT", "fetched_utc": "2026-11-03T19:50:00+00:00", "dates": []}):
            with self.subTest(document=document):
                state.atomic_write_json(path, document)
                result = self.expirations(self.client(expirations=self.listed()))
                self.assertEqual(("fetch", 5), (result.served_from, len(result.dates)))
                self.assertTrue(any("expirations cache WMT" in w for w in result.warnings), result.warnings)

    def test_unexpected_exceptions_and_arguments(self) -> None:
        class Broken(FakeClient):
            def option_expirations(self, symbol: str, start: date, **kwargs: Any) -> FakeExpirationsResult:
                raise RuntimeError("a bug, not a client error")

        with self.assertRaises(RuntimeError):
            self.expirations(Broken(on_request=self.hook))
        for symbol in ("", "  ", None, 5):
            with self.subTest(symbol=symbol):
                with self.assertRaises(ValueError):
                    self.expirations(self.client(), symbol=symbol)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.expirations(self.client(), now=datetime(2026, 11, 3, 19, 50))

    def test_the_lock_busy_case_serves_and_warns(self) -> None:
        with mock.patch.object(quote_cache.state, "locked", side_effect=state.LockBusy(self.paths.lock_path, 0.1)):
            result = self.expirations(self.client(expirations=self.listed()))
        self.assertEqual(("fetch", 5), (result.served_from, len(result.dates)))
        self.assertTrue(any("expirations cache not written" in w for w in result.warnings), result.warnings)
        self.assertFalse(os.path.exists(quote_cache.expirations_path(self.paths, "WMT")))

    def test_quotes_and_chains_ignore_the_expirations_cache(self) -> None:
        self.expirations(self.client(expirations=self.listed()))
        self.assertFalse(os.path.exists(quote_cache.quotes_path(self.paths)))
        self.assertFalse(os.path.exists(os.path.join(self.paths.cache_dir, quote_cache.CHAINS_SUBDIR)))

    def read_expirations(self, symbol: str) -> Dict[str, Any]:
        with open(quote_cache.expirations_path(self.paths, symbol), encoding="utf-8") as handle:
            return json.load(handle)


class OfflineExpirationsTest(unittest.TestCase):
    """Offline, ``get_expirations`` reads the recorded contract listing ``contracts__<SYMBOL>__<start>__<start+400>.json``
    of the fixture set (``start`` the ET date of ``now``) through the existing transport (``open_market_data`` on a copy of ``tests/fixtures/desk``; nothing under ``tests/fixtures`` is
    written)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-expirations-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def recorded(self, fixture_set: str, symbol: str, now: datetime) -> List[date]:
        start = market_clock.to_et(now).date()
        name = "contracts__%s__%s__%s.json" % (symbol, start.isoformat(),
                                               (start + timedelta(days=alpaca_client.EXPIRATIONS_WINDOW_DAYS)).isoformat())
        path = os.path.join(FIXTURE_DESK, "fixtures", "alpaca", fixture_set, name)
        with open(path, encoding="utf-8") as handle:
            listed = json.load(handle)["option_contracts"]
        return sorted({date.fromisoformat(contract["expiration_date"]) for contract in listed})

    def market(self, fixture_set: str, now: datetime) -> quote_cache.MarketData:
        return quote_cache.open_market_data(self.root, now=now, offline=True, fixture_set=fixture_set, timeout_s=10.0,
                                            user_agent="aegis-desk-tests/0")

    def test_the_recorded_lists_are_served_through_the_transport(self) -> None:
        for fixture_set, now in (("regular", NOW), ("expiry_day", datetime(2026, 11, 20, 19, 50, 0, tzinfo=UTC))):
            for symbol in ("WMT", "NVDA", "SPY"):
                with self.subTest(fixture_set=fixture_set, symbol=symbol):
                    market = self.market(fixture_set, now)
                    self.assertIsInstance(market.client, alpaca_client.AlpacaClient)
                    result = quote_cache.get_expirations(symbol, now=now, cfg=market.cache, client=market.client,
                                                         paths=market.paths, run=market.run)
                    self.assertEqual(self.recorded(fixture_set, symbol, now), result.dates)
                    self.assertTrue(result.dates)
                    self.assertEqual(("fetch", 1, 1, 0), (result.served_from, result.requests_made, market.run.requests,
                                                         market.run.chains))
                    shutil.rmtree(os.path.join(market.paths.cache_dir, quote_cache.EXPIRATIONS_SUBDIR))
        ledger = os.path.join(self.root, "data", "cache", "ledger.jsonl")
        with open(ledger, encoding="utf-8") as handle:
            paths = [json.loads(line)["path"] for line in handle.read().splitlines() if line]
        self.assertEqual([EXPIRATIONS_PATH] * 6, paths)

    def test_a_symbol_without_a_recorded_file_is_a_failed_request(self) -> None:
        market = self.market("regular", NOW)
        result = quote_cache.get_expirations("AAPL", now=NOW, cfg=market.cache, client=market.client,
                                             paths=market.paths, run=market.run)
        self.assertEqual(([], None, "fetch_failed:network", 1), (result.dates, result.served_from,
                                                                 result.missing_reason, result.requests_made))
        self.assertIsNone(market.run.stop_reason, "a missing fixture is a transient network failure")
        self.assertTrue(any("no offline fixture" in w for w in result.warnings), result.warnings)


class ToolPlumbingTest(unittest.TestCase):
    """The parts both tools share, exercised without the network client."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-plumb-")
        self.root = os.path.realpath(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write_desk(self, host: str) -> str:
        with open(os.path.join(FIXTURE_DESK, "desk.toml"), encoding="utf-8") as handle:
            text = handle.read()
        assert 'host = "paper"' in text
        with open(os.path.join(self.root, "desk.toml"), "w", encoding="utf-8") as handle:
            handle.write(text.replace('host = "paper"', 'host = "%s"' % host, 1))
        return self.root

    def test_a_live_host_never_loads(self) -> None:
        self.write_desk("live")
        with self.assertRaises(desk_config.DeskConfigError) as ctx:
            quote_cache.open_market_data(self.root, now=NOW, offline=True, fixture_set="regular", timeout_s=10.0,
                                         user_agent="test")
        self.assertIn('host must be "paper"', str(ctx.exception))
        self.assertFalse(os.path.exists(os.path.join(self.root, "state")), "a refused desk is never written to")

    def test_build_client_refuses_any_host_but_paper_with_the_exact_message(self) -> None:
        cfg = desk_config.load_desk_config(os.path.join(FIXTURE_DESK, "desk.toml"))
        paths = desk_paths.for_root(self.root)
        for host in ("live", "production", "loopback:1"):
            with self.subTest(host=host):
                bad = dataclasses.replace(cfg, market_data=dataclasses.replace(cfg.market_data, host=host))
                for offline in (True, False):
                    with self.assertRaises(quote_cache.MarketDataRefused) as ctx:
                        quote_cache.build_client(bad, paths, offline=offline, fixture_set="regular", timeout_s=10.0,
                                                 now=NOW, user_agent="test")
                    self.assertEqual("live_host", ctx.exception.status)
                    self.assertEqual(quote_cache.LIVE_REFUSED_MESSAGE, str(ctx.exception))
        self.assertEqual('only Alpaca paper market data is supported: the desk holds a paper key pair and never points '
                         'at a live account; set host = "paper"', quote_cache.LIVE_REFUSED_MESSAGE)
        self.assertFalse(os.path.exists(os.path.join(self.root, "data")), "nothing is written for a refused host")

    def test_no_credential_message_text(self) -> None:
        self.assertEqual("no market-data key: add the Alpaca paper key ID and secret through the secret card "
                         "(or the acknowledged credentials file)", quote_cache.NO_CREDENTIAL_MESSAGE)

    def test_online_without_a_key_pair_is_no_credential(self) -> None:
        cfg = desk_config.load_desk_config(os.path.join(FIXTURE_DESK, "desk.toml"))
        missing = os.path.join(self.root, "absent.key")
        cfg = dataclasses.replace(cfg, market_data=dataclasses.replace(cfg.market_data, credentials_file=missing))
        paths = desk_paths.for_root(self.root)
        scrubbed = {name: value for name, value in os.environ.items()
                    if name not in (cfg.market_data.key_env, cfg.market_data.secret_env)}
        with mock.patch.dict(os.environ, scrubbed, clear=True):
            with self.assertRaises(quote_cache.MarketDataRefused) as ctx:
                quote_cache.build_client(cfg, paths, offline=False, fixture_set="regular", timeout_s=10.0, now=NOW,
                                         user_agent="test")
            self.assertEqual(("no_credential", quote_cache.NO_CREDENTIAL_MESSAGE),
                             (ctx.exception.status, str(ctx.exception)))
            with mock.patch.dict(os.environ, {cfg.market_data.key_env: "PKTESTKEYID0000000000"}):
                with self.assertRaises(quote_cache.MarketDataRefused) as ctx:
                    quote_cache.build_client(cfg, paths, offline=False, fixture_set="regular", timeout_s=10.0,
                                             now=NOW, user_agent="test")
                self.assertEqual("no_credential", ctx.exception.status)
                self.assertNotIn("PKTESTKEYID", str(ctx.exception))
            with mock.patch.dict(os.environ, {cfg.market_data.key_env: "PKTESTKEYID0000000000",
                                              cfg.market_data.secret_env: "testsecret0000000000000000000000000000"}):
                client = quote_cache.build_client(cfg, paths, offline=False, fixture_set="regular", timeout_s=10.0,
                                                  now=NOW, user_agent="test")
                self.assertIsInstance(client, alpaca_client.AlpacaClient)
                self.assertEqual(("paper", "indicative", "iex"), (client.host_kind, client.options_feed,
                                                                  client.stock_feed))
                self.assertNotIn("testsecret", repr(client))

    def test_bad_desk_root_and_bad_config_raise_their_own_errors(self) -> None:
        with self.assertRaises(desk_paths.DeskPathsError):
            quote_cache.open_market_data(os.path.join(self.root, "nope"), now=NOW, offline=True, fixture_set=None,
                                         timeout_s=10.0, user_agent="test")
        with self.assertRaises(desk_config.DeskConfigError):
            quote_cache.open_market_data(self.root, now=NOW, offline=True, fixture_set=None, timeout_s=10.0,
                                         user_agent="test")

    def test_format_money(self) -> None:
        self.assertEqual("648.10", quote_cache.format_money(Decimal("648.1")))
        self.assertEqual("3.425", quote_cache.format_money(Decimal("3.425")))
        self.assertEqual("0.00", quote_cache.format_money(Decimal("0")))
        self.assertEqual("105.00", quote_cache.format_money(Decimal("105")))
        self.assertEqual("-", quote_cache.format_money(None))

    def test_footer_and_source_label(self) -> None:
        cfg = desk_config.load_desk_config(os.path.join(FIXTURE_DESK, "desk.toml"))
        stamp = datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC)
        self.assertEqual("Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of 19:31 UTC",
                         quote_cache.footer_line(cfg, stamp))
        self.assertEqual("alpaca:paper", quote_cache.source_label(cfg))
        opra = dataclasses.replace(cfg, market_data=dataclasses.replace(cfg.market_data, options_feed="opra",
                                                                        feed_delay_min=0))
        self.assertEqual("Alpaca paper (IEX stocks, OPRA options), delayed 0 min, as of 19:31 UTC",
                         quote_cache.footer_line(opra, stamp))

    def test_age_minutes(self) -> None:
        self.assertEqual(18, quote_cache.age_minutes(NOW, datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC)))
        self.assertEqual(0, quote_cache.age_minutes(NOW, NOW + timedelta(seconds=30)))
        self.assertIsNone(quote_cache.age_minutes(NOW, None))


if __name__ == "__main__":
    unittest.main()
