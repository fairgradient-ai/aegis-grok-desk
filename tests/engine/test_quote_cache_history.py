"""``quote_cache.get_history`` (PR 2b, F2): the daily-bar cache under ``data/cache/history/``.

The happy paths run the real GET-only client over an ``OfflineTransport`` whose fixture set is
written under a temporary desk root (the client's ``bars__<SYMBOL>__<START>__<END>.json``
naming, Alpaca's ``/v2/stocks/bars`` shape); the guard and error paths use a small fake client shaped like
``AlpacaError``, because
the offline transport can only answer 200 or "no fixture". The existing cache tests
(``tests/engine/test_quote_cache.py``) stay untouched: nothing here changes ``get_quotes`` or
``get_chain``.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import json
import os
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from aegis_desk import alpaca_client, desk_paths, quote_cache, state
from aegis_desk.market_types import Bar, RequestReceipt

UTC = timezone.utc
UA = "aegis-desk-tests/0"
HISTORY_PATH = "/v2/stocks/bars"
SET = "hist"
START = date(2026, 11, 20)
END = date(2026, 11, 23)
NOW = datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC)  # Tuesday 07:00 ET: the recon instant, the range is closed
MONDAY = datetime(2026, 11, 23, 20, 0, 0, tzinfo=UTC)  # Monday 15:00 ET: the range's last day is still trading
SATURDAY = datetime(2026, 11, 21, 14, 30, 0, tzinfo=UTC)  # the post-expiry routine: a non-trading day

BAR_FRI = {"date": "2026-11-20", "open": 104.6, "high": 105.2, "low": 104.3, "close": 104.86, "volume": 9100000}
BAR_MON = {"date": "2026-11-23", "open": 105.3, "high": 105.9, "low": 104.95, "close": 105.62, "volume": 8400000}
TWO_BARS = [BAR_FRI, BAR_MON]


def alpaca_bars(symbol: str, records: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Alpaca's ``/v2/stocks/bars`` answer for ``records`` (each bar stamped at midnight ET of its date: 05:00 UTC in
    November)."""
    bars = [{"t": "%sT05:00:00Z" % record["date"], "o": record["open"], "h": record["high"], "l": record["low"],
             "c": record["close"], "v": record["volume"], "n": 1000, "vw": record["close"]} for record in records]
    return {"bars": {symbol: bars} if bars else {}, "next_page_token": None}


FIXTURES = {
    "bars__WMT__2026-11-20__2026-11-23.json": alpaca_bars("WMT", TWO_BARS),
    "bars__WMT__2026-11-20__2026-11-20.json": alpaca_bars("WMT", [BAR_FRI]),
    "bars__AAPL__2026-11-20__2026-11-20.json": alpaca_bars("AAPL", []),
}
HTTP_STATUS = {"quota": 429, "auth": 401, "http": 500, "network": None, "timeout": None, "decode": 200}


class ClientError(Exception):
    """Shaped like ``alpaca_client.AlpacaError``: ``status``, ``http_status``, ``path``, ``message``."""

    def __init__(self, status: str, http_status: Optional[int], path: str) -> None:
        self.status = status
        self.http_status = http_status
        self.path = path
        self.message = "fake %s failure" % status
        super().__init__("%s: %s" % (status, self.message))


@dataclasses.dataclass
class FakeHistoryResult:
    bars: List[Bar]
    receipt: RequestReceipt
    raw: Dict[str, Any]


class FakeHistoryClient:
    """``history(symbol, start, end, interval=)`` with the client's return shape; ``errors`` is
    consumed one status per call (``None`` = success); a status in ``NO_RECEIPT`` is raised before
    any attempt (no receipt, no hook call), as the real client refuses a bad symbol."""

    NO_RECEIPT = ("bad_symbol", "refused_path", "refused_host")

    def __init__(self, bars: Optional[Sequence[Dict[str, Any]]] = None, errors: Sequence[Optional[str]] = (),
                 on_request=None, now: datetime = NOW, raw_less: bool = False) -> None:
        self.bars = list(TWO_BARS if bars is None else bars)
        self.errors = list(errors)
        self.on_request = on_request
        self.now = now
        self.raw_less = raw_less
        self.calls: List[tuple] = []

    def history(self, symbol: str, start: date, end: date, *, interval: str = "daily") -> FakeHistoryResult:
        self.calls.append((symbol, start, end, interval))
        status = self.errors.pop(0) if self.errors else None
        if status in self.NO_RECEIPT:
            raise ClientError(status, None, HISTORY_PATH)
        http_status = 200 if status is None else HTTP_STATUS.get(status)
        receipt = RequestReceipt(HISTORY_PATH, 1, self.now, status or "ok", http_status=http_status,
                                 ratelimit_allowed=60, ratelimit_used=1, ratelimit_available=59)
        if self.on_request is not None:
            self.on_request(receipt)
        if status is not None:
            raise ClientError(status, http_status, HISTORY_PATH)
        if self.raw_less:
            bars = [Bar(date=date.fromisoformat(raw["date"]), close=Decimal(str(raw["close"]))) for raw in self.bars]
        else:
            bars = [Bar.from_record(dict(raw)) for raw in self.bars]
        return FakeHistoryResult(bars, receipt, alpaca_bars(symbol, self.bars))


def receipt_at(moment: datetime) -> RequestReceipt:
    return RequestReceipt("/v2/stocks/quotes/latest", 1, moment, "ok", http_status=200, ratelimit_available=50)


class HistoryTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-history-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        os.makedirs(self.root)
        self.paths = desk_paths.for_root(self.root)
        desk_paths.ensure_runtime_dirs(self.paths)
        self.set_dir = os.path.join(self.paths.alpaca_fixtures_dir, SET)
        os.makedirs(self.set_dir)
        for name, document in FIXTURES.items():
            with open(os.path.join(self.set_dir, name), "w", encoding="utf-8") as handle:
                json.dump(document, handle, indent=2)
        self.cfg = quote_cache.CacheConfig(ttl_min=5)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def offline_client(self, now: datetime = NOW, run: Optional[quote_cache.RunCounters] = None) -> Any:
        transport = alpaca_client.OfflineTransport(self.paths.alpaca_fixtures_dir, SET, now=now)
        return alpaca_client.AlpacaClient("paper", None, user_agent=UA, transport=transport,
                                          on_request=quote_cache.ledger_hook(self.paths, run))

    def fake_client(self, run: Optional[quote_cache.RunCounters] = None, **kwargs: Any) -> FakeHistoryClient:
        return FakeHistoryClient(on_request=quote_cache.ledger_hook(self.paths, run), **kwargs)

    def history(self, client: Any, symbol: str = "WMT", start: date = START, end: date = END,
                now: datetime = NOW, cfg: Optional[quote_cache.CacheConfig] = None,
                run: Optional[quote_cache.RunCounters] = None) -> quote_cache.HistoryCacheResult:
        return quote_cache.get_history(symbol, start, end, now=now, cfg=cfg or self.cfg, client=client,
                                       paths=self.paths, run=run)

    def remove_fixture(self, name: str) -> None:
        os.remove(os.path.join(self.set_dir, name))

    def cache_file(self, symbol: str = "WMT", start: date = START, end: date = END) -> Dict[str, Any]:
        with open(quote_cache.history_path(self.paths, symbol, start, end), encoding="utf-8") as handle:
            return json.load(handle)

    def ledger_lines(self) -> List[Dict[str, Any]]:
        path = quote_cache.ledger_path(self.paths)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]


class PathAndResultTest(HistoryTestCase):
    def test_history_path_naming(self) -> None:
        expected = os.path.join(self.paths.cache_dir, "history", "WMT__2026-11-20__2026-11-23.json")
        self.assertEqual(expected, quote_cache.history_path(self.paths, "wmt", START, END))
        self.assertEqual("history", quote_cache.HISTORY_SUBDIR)
        self.assertEqual("no_bars", quote_cache.REASON_NO_BARS)
        for name in ("HistoryCacheResult", "get_history", "history_path", "HISTORY_SUBDIR", "REASON_NO_BARS"):
            self.assertIn(name, quote_cache.__all__)

    def test_result_is_a_frozen_dataclass_with_the_addendum_fields(self) -> None:
        names = [f.name for f in dataclasses.fields(quote_cache.HistoryCacheResult)]
        self.assertEqual(["symbol", "start", "end", "bars", "served_from", "missing_reason", "requests_made",
                          "receipt_utc", "warnings", "fetched_utc"], names)
        result = self.history(self.offline_client())
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.symbol = "X"  # type: ignore[misc]


class FetchAndCacheTest(HistoryTestCase):
    def test_first_call_fetches_decodes_and_persists(self) -> None:
        run = quote_cache.RunCounters()
        result = self.history(self.offline_client(run=run), run=run)
        self.assertEqual(("WMT", START, END), (result.symbol, result.start, result.end))
        self.assertEqual("fetch", result.served_from)
        self.assertIsNone(result.missing_reason)
        self.assertEqual(1, result.requests_made)
        self.assertEqual(NOW, result.receipt_utc)
        self.assertEqual(NOW, result.fetched_utc)
        self.assertEqual([], result.warnings)
        self.assertEqual([date(2026, 11, 20), date(2026, 11, 23)], [bar.date for bar in result.bars])
        self.assertEqual(Decimal("104.86"), result.bars[0].close)
        self.assertEqual(Decimal("105.3"), result.bars[1].open, "Decimal from the JSON text")
        self.assertEqual(8400000, result.bars[1].volume)
        self.assertEqual(BAR_FRI, result.bars[0].raw)
        self.assertEqual(1, run.requests)
        self.assertEqual(0, run.chains, "history is not a chain")
        self.assertIsNone(run.stop_reason)
        self.assertEqual([NOW], run.receipt_stamps)
        document = self.cache_file()
        self.assertEqual({"schema", "symbol", "start", "end", "fetched_utc", "bars"}, set(document))
        self.assertEqual((1, "WMT", "2026-11-20", "2026-11-23", "2026-11-24T12:00:00+00:00"),
                         (document["schema"], document["symbol"], document["start"], document["end"], document["fetched_utc"]))
        self.assertEqual([BAR_FRI, BAR_MON], document["bars"], "the normalised records are what is stored")
        lines = self.ledger_lines()
        self.assertEqual(1, len(lines))
        self.assertEqual({"utc": "2026-11-24T12:00:00+00:00", "path": HISTORY_PATH, "symbols": 1, "status": "ok",
                          "http_status": 200, "ratelimit_available": None}, lines[0])

    def test_symbol_is_normalised(self) -> None:
        result = self.history(self.offline_client(), symbol=" wmt ")
        self.assertEqual("WMT", result.symbol)
        self.assertEqual("fetch", result.served_from)

    def test_second_call_is_served_from_the_cache_without_the_transport(self) -> None:
        self.history(self.offline_client())
        self.remove_fixture("bars__WMT__2026-11-20__2026-11-23.json")
        result = self.history(self.offline_client(), now=NOW + timedelta(minutes=3))
        self.assertEqual("cache", result.served_from)
        self.assertEqual(0, result.requests_made)
        self.assertIsNone(result.receipt_utc)
        self.assertEqual(NOW, result.fetched_utc)
        self.assertEqual([Decimal("104.86"), Decimal("105.62")], [bar.close for bar in result.bars])
        self.assertEqual(BAR_MON, result.bars[1].raw, "a reload goes through Bar.from_record")
        self.assertEqual([], result.warnings)
        self.assertEqual(1, len(self.ledger_lines()))

    def test_closed_range_fetched_after_its_last_day_is_served_without_any_ttl(self) -> None:
        self.history(self.offline_client())
        self.remove_fixture("bars__WMT__2026-11-20__2026-11-23.json")
        much_later = NOW + timedelta(days=40)
        result = self.history(self.offline_client(now=much_later), now=much_later)
        self.assertEqual("cache", result.served_from)
        self.assertEqual(2, len(result.bars))
        self.assertEqual([], result.warnings, "closed days never change: no staleness warning")

    def test_range_still_open_follows_the_quote_ttl(self) -> None:
        run = quote_cache.RunCounters()
        first = self.history(self.offline_client(now=MONDAY, run=run), now=MONDAY, run=run)
        self.assertEqual("fetch", first.served_from)
        inside = MONDAY + timedelta(minutes=5)
        self.assertEqual("cache", self.history(self.offline_client(now=inside), now=inside).served_from)
        self.remove_fixture("bars__WMT__2026-11-20__2026-11-23.json")
        expired = MONDAY + timedelta(minutes=5, seconds=1)
        result = self.history(self.offline_client(now=expired), now=expired)
        self.assertEqual("fetch_failed:network", result.missing_reason, "the copy expired and the refetch failed")
        self.assertIsNone(result.served_from)
        self.assertEqual([], result.bars, "an expired copy is not served")
        self.assertEqual(1, result.requests_made)
        self.assertTrue(any("no offline fixture" in warning for warning in result.warnings), result.warnings)

    def test_copy_fetched_on_the_ranges_last_day_is_not_final(self) -> None:
        """Fetched Monday 15:00 ET the Monday bar is still forming: the copy is final only once it
        was fetched on a later ET date than ``end`` (the safe reading of "closed days never change")."""
        self.history(self.offline_client(now=MONDAY), now=MONDAY)
        self.remove_fixture("bars__WMT__2026-11-20__2026-11-23.json")
        result = self.history(self.offline_client(), now=NOW)
        self.assertEqual("fetch_failed:network", result.missing_reason)
        self.assertEqual(0, len(result.bars))

    def test_cached_copy_stamped_in_the_future_is_not_trusted(self) -> None:
        self.history(self.offline_client(now=MONDAY), now=MONDAY)
        earlier = MONDAY - timedelta(minutes=1)
        result = self.history(self.offline_client(now=earlier), now=earlier)
        self.assertEqual("fetch", result.served_from)

    def test_single_bar_object_shape(self) -> None:
        result = self.history(self.offline_client(now=SATURDAY), start=START, end=START, now=SATURDAY)
        self.assertEqual("fetch", result.served_from, "history is fetched on a non-trading day too")
        self.assertEqual([date(2026, 11, 20)], [bar.date for bar in result.bars])
        self.assertEqual(Decimal("104.86"), result.bars[0].close)
        self.assertEqual([BAR_FRI], self.cache_file(end=START)["bars"])

    def test_empty_answer_is_no_bars_and_cached_with_the_ttl_rule(self) -> None:
        run = quote_cache.RunCounters()
        result = self.history(self.offline_client(run=run), symbol="AAPL", start=START, end=START, run=run)
        self.assertEqual([], result.bars)
        self.assertEqual("no_bars", result.missing_reason)
        self.assertEqual("fetch", result.served_from)
        self.assertEqual(1, result.requests_made)
        self.assertEqual(NOW, result.fetched_utc)
        self.assertTrue(any("no bars" in warning for warning in result.warnings), result.warnings)
        self.assertEqual([], self.cache_file("AAPL", START, START)["bars"])
        self.remove_fixture("bars__AAPL__2026-11-20__2026-11-20.json")
        inside = NOW + timedelta(minutes=4)
        again = self.history(self.offline_client(now=inside), symbol="AAPL", start=START, end=START, now=inside)
        self.assertEqual(("cache", "no_bars", []), (again.served_from, again.missing_reason, again.bars))
        self.assertEqual(0, again.requests_made)
        expired = NOW + timedelta(minutes=6)
        later = self.history(self.offline_client(now=expired), symbol="AAPL", start=START, end=START, now=expired)
        self.assertEqual("fetch_failed:network", later.missing_reason, "an empty past answer never becomes final")

    def test_missing_fixture_is_a_network_failure_that_counts_as_a_request(self) -> None:
        run = quote_cache.RunCounters()
        result = self.history(self.offline_client(run=run), symbol="MSFT", run=run)
        self.assertEqual("fetch_failed:network", result.missing_reason)
        self.assertEqual(([], None, 1, None, None), (result.bars, result.served_from, result.requests_made,
                                                    result.receipt_utc, result.fetched_utc))
        self.assertEqual(1, run.requests)
        self.assertIsNone(run.stop_reason, "a network error does not stop the run")
        lines = self.ledger_lines()
        self.assertEqual(1, len(lines))
        self.assertEqual(("network", None, 1), (lines[0]["status"], lines[0]["http_status"], lines[0]["symbols"]))
        self.assertFalse(os.path.exists(quote_cache.history_path(self.paths, "MSFT", START, END)))

    def test_corrupt_cache_file_is_quarantined_and_refetched(self) -> None:
        self.history(self.offline_client())
        path = quote_cache.history_path(self.paths, "WMT", START, END)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        result = self.history(self.offline_client())
        self.assertEqual("fetch", result.served_from)
        self.assertTrue(any("corrupt" in warning for warning in result.warnings), result.warnings)
        self.assertEqual([BAR_FRI, BAR_MON], self.cache_file()["bars"])
        quarantined = [name for name in os.listdir(os.path.dirname(path)) if ".corrupt-" in name]
        self.assertEqual(1, len(quarantined))

    def test_cache_file_with_unexpected_contents_is_refetched(self) -> None:
        self.history(self.offline_client())
        path = quote_cache.history_path(self.paths, "WMT", START, END)
        document = self.cache_file()
        for broken in ({**document, "symbol": "NVDA"}, {**document, "end": "2026-11-24"}, {**document, "bars": "x"},
                       {**document, "fetched_utc": "yesterday"}, {**document, "schema": 2}, {**document, "bars": [{"close": 1}]}):
            state.atomic_write_json(path, broken)
            result = self.history(self.offline_client())
            self.assertEqual("fetch", result.served_from, broken)
            self.assertTrue(result.warnings, broken)
        self.assertEqual([BAR_FRI, BAR_MON], self.cache_file()["bars"])

    def test_bars_without_vendor_records_are_served_but_not_cached(self) -> None:
        result = self.history(self.fake_client(raw_less=True))
        self.assertEqual("fetch", result.served_from)
        self.assertEqual(2, len(result.bars))
        self.assertTrue(any("not cached" in warning for warning in result.warnings), result.warnings)
        self.assertFalse(os.path.exists(quote_cache.history_path(self.paths, "WMT", START, END)))


class GuardTest(HistoryTestCase):
    def test_per_run_request_cap(self) -> None:
        run = quote_cache.RunCounters(requests=1)
        client = self.fake_client(run)
        result = self.history(client, cfg=quote_cache.CacheConfig(ttl_min=5, per_run_max_requests=1), run=run)
        self.assertEqual("per_run_cap", result.missing_reason)
        self.assertEqual(([], None, 0), (result.bars, result.served_from, result.requests_made))
        self.assertEqual([], client.calls)
        self.assertTrue(any("per-run" in warning for warning in result.warnings), result.warnings)
        self.assertEqual([], self.ledger_lines())

    def test_chain_cap_does_not_apply(self) -> None:
        run = quote_cache.RunCounters(chains=4)
        result = self.history(self.fake_client(run), cfg=quote_cache.CacheConfig(ttl_min=5, per_run_max_chains=4), run=run)
        self.assertEqual("fetch", result.served_from)
        self.assertEqual(4, run.chains)
        self.assertEqual(1, run.requests)

    def test_budget_guard(self) -> None:
        hook = quote_cache.ledger_hook(self.paths)
        full = quote_cache.MINUTE_BUDGET - quote_cache.MINUTE_HEADROOM  # 180 of Alpaca's 200 a minute
        for _ in range(full):
            hook(receipt_at(NOW - timedelta(seconds=30)))
        client = self.fake_client()
        result = self.history(client)
        self.assertEqual("quota_guard", result.missing_reason)
        self.assertEqual([], client.calls)
        self.assertTrue(any("budget" in warning for warning in result.warnings), result.warnings)
        self.assertEqual(full, len(self.ledger_lines()))
        aged = NOW + timedelta(seconds=31)
        self.assertEqual("fetch", self.history(self.fake_client(now=aged), now=aged).served_from)

    def test_run_stop_reason_blocks_the_fetch(self) -> None:
        run = quote_cache.RunCounters(stop_reason="quota_guard")
        client = self.fake_client(run)
        result = self.history(client, run=run)
        self.assertEqual("quota_guard", result.missing_reason)
        self.assertEqual([], client.calls)
        self.assertTrue(any("stopped" in warning for warning in result.warnings), result.warnings)

    def test_cached_copy_is_served_before_any_guard(self) -> None:
        self.history(self.fake_client())
        run = quote_cache.RunCounters(stop_reason="quota_guard", requests=99)
        result = self.history(self.fake_client(run), run=run)
        self.assertEqual("cache", result.served_from)
        self.assertEqual(2, len(result.bars))

    def test_quota_error_stops_the_run(self) -> None:
        run = quote_cache.RunCounters()
        client = self.fake_client(run, errors=["quota"])
        result = self.history(client, run=run)
        self.assertEqual("quota_guard", result.missing_reason)
        self.assertEqual(1, result.requests_made)
        self.assertEqual(("quota_guard", 1), (run.stop_reason, run.requests))
        self.assertEqual("quota", self.ledger_lines()[0]["status"])
        second = self.history(self.fake_client(run), symbol="NVDA", run=run)
        self.assertEqual("quota_guard", second.missing_reason)
        self.assertEqual(1, len(self.ledger_lines()), "nothing more is sent on this run")

    def test_credential_errors_stop_the_run_and_other_errors_do_not(self) -> None:
        for status in ("auth", "subscription", "no_credential", "credential_file_mode", "refused_redirect"):
            run = quote_cache.RunCounters()
            result = self.history(self.fake_client(run, errors=[status]), run=run)
            self.assertEqual("fetch_failed:" + status, result.missing_reason)
            self.assertEqual("fetch_failed:" + status, run.stop_reason)
        for status in ("network", "timeout", "http", "decode", "bad_request", "incomplete"):
            run = quote_cache.RunCounters()
            result = self.history(self.fake_client(run, errors=[status]), run=run)
            self.assertEqual("fetch_failed:" + status, result.missing_reason)
            self.assertIsNone(run.stop_reason)
            self.assertEqual((1, 1), (result.requests_made, run.requests))

    def test_refusals_before_sending_are_not_requests(self) -> None:
        for status in quote_cache.NO_SEND_STATUSES:
            run = quote_cache.RunCounters()
            result = self.history(self.fake_client(run, errors=[status]), run=run)
            self.assertEqual("fetch_failed:" + status, result.missing_reason)
            self.assertEqual((0, 0), (result.requests_made, run.requests))
        self.assertEqual([], self.ledger_lines())

    def test_unknown_exceptions_propagate(self) -> None:
        class Broken:
            def history(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("bug")

        with self.assertRaises(RuntimeError):
            self.history(Broken())

    def test_run_counters_default_when_omitted(self) -> None:
        result = quote_cache.get_history("WMT", START, END, now=NOW, cfg=self.cfg, client=self.fake_client(), paths=self.paths)
        self.assertEqual("fetch", result.served_from)


class ValidationTest(HistoryTestCase):
    def test_bad_symbol_pattern_sends_nothing(self) -> None:
        client = self.fake_client()
        result = self.history(client, symbol="WM T")
        self.assertEqual("fetch_failed:bad_symbol", result.missing_reason)
        self.assertEqual("WM T", result.symbol)
        self.assertEqual(([], None, 0), (result.bars, result.served_from, result.requests_made))
        self.assertEqual([], client.calls)
        self.assertTrue(result.warnings)

    def test_arguments(self) -> None:
        with self.assertRaises(ValueError):
            self.history(self.fake_client(), start=END, end=START)
        with self.assertRaises(ValueError):
            self.history(self.fake_client(), start=datetime(2026, 11, 20, tzinfo=UTC))  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.history(self.fake_client(), end="2026-11-23")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            self.history(self.fake_client(), symbol="")
        with self.assertRaises(ValueError):
            self.history(self.fake_client(), now=datetime(2026, 11, 24, 12, 0))
        self.assertEqual("fetch", self.history(self.fake_client(), start=START, end=START).served_from)


class ExistingBehaviourTest(HistoryTestCase):
    def test_quotes_and_chains_ignore_the_history_cache(self) -> None:
        self.history(self.offline_client())
        self.assertFalse(os.path.exists(quote_cache.quotes_path(self.paths)))
        self.assertTrue(os.path.isdir(os.path.join(self.paths.cache_dir, "history")))
        self.assertFalse(os.path.isdir(os.path.join(self.paths.cache_dir, "chains")))


if __name__ == "__main__":
    unittest.main()
