"""``scripts/quote_qc.py`` run in-process against a copied fixture desk root (see the cache tool
tests for the skip rule while Builder A's client and Builder C's ``--now`` flag are pending)."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Sequence, Tuple

from aegis_desk import contract

import quote_qc as tool  # the tool module scripts/quote_qc.py, not the library

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
CLIENT_MODULE = os.path.join(ROOT, "scripts", "aegis_desk", "tradier_client.py")
REGULAR_NOW = "2026-11-03T19:50:00Z"
AFTER_CLOSE_NOW = "2026-11-03T21:25:00Z"
PRE_OPEN_NOW = "2026-11-03T13:00:00Z"
HOLIDAY_NOW = "2026-11-26T15:00:00Z"
CLOSURE_ENTRY = '\n[[closures]]\ndate = %s\nkind = "%s"\nsource = "owner-entered (test)"\n'
CLOSED_TODAY_SESSION = {"session": "holiday", "trading_day": False, "close_utc": None, "minutes_to_close": None,
                        "holiday_name": "calendar closure", "as_of_utc": "2026-11-03T19:50:00+00:00",
                        "et_date": "2026-11-03", "open_utc": None, "product": "equity", "table_status": "ok"}
SIX_SYMBOLS = ("NVDA", "NVDA261120C00150000", "SPY", "SPY261218P00620000", "WMT", "WMT261120C00105000")


def closed_day_missing(symbol: str) -> Dict[str, Any]:
    """The verdict on a symbol with no cached copy on a closed day: nothing was fetched, nothing is usable."""
    return {"symbol": symbol, "grade": "F", "status": "missing", "label": "NYSE closed", "age_min_bid": None,
            "age_min_ask": None, "spread_pct": None, "mark_mid": None, "mark_liq_long": None, "mark_liq_short": None,
            "usable_for_rules": False, "reasons": ["cache: market_closed"], "bid": None, "ask": None, "as_of": None,
            "served_from": None}


def now_flag_present() -> bool:
    probe = contract.ReadOnlyParser("probe", "0")
    _, extras = probe.parse_known_args(["--now", REGULAR_NOW])
    return not extras


NOW_READY = now_flag_present()
NOW_SKIP = "waits for the shared --now flag of ReadOnlyParser (Builder C)"
READY = os.path.isfile(CLIENT_MODULE) and NOW_READY
SKIP = "waits for scripts/aegis_desk/tradier_client.py (Builder A) and the shared --now flag (Builder C)"


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = tool.main(list(argv))
    return code, buffer.getvalue()


def run_json(argv: Sequence[str]) -> Tuple[int, Dict[str, Any]]:
    code, out = run_tool(argv)
    return code, json.loads(out)


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-qc-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, *symbols: str, fixture_set: str = None, now: str = REGULAR_NOW, json_mode: bool = True,
             extra: Sequence[str] = ()) -> List[str]:
        argv = list(symbols) + ["--offline", "--desk-root", self.root, "--now", now] + list(extra)
        if fixture_set is not None:
            argv += ["--fixture-set", fixture_set]
        if json_mode:
            argv.append("--json")
        return argv

    def ledger(self) -> List[Dict[str, Any]]:
        path = os.path.join(self.root, "data", "cache", "ledger.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]

    def rewrite_desk_toml(self, old: str, new: str) -> None:
        path = os.path.join(self.root, "desk.toml")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))


class AlwaysOnTest(DeskCopy):
    def test_schema(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual("quote_qc", body["tool"])
        self.assertIn("symbols", body["envelope"]["payload"])
        self.assertIn("bound_min", body["envelope"]["payload"])

    def test_usage_errors(self) -> None:
        for argv in (["--json", "--offline", "--desk-root", self.root],
                     ["SPY", "--json", "--desk-root", self.root, "--fixture-set", "regular"],
                     ["SPY", "--json", "--offline"],
                     ["SPY", "BAD SYMBOL", "--json", "--offline", "--desk-root", self.root]):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(argv)
                self.assertEqual(2, ctx.exception.code)


@unittest.skipUnless(NOW_READY, NOW_SKIP)
class PlumbingTest(DeskCopy):
    """Config and host checks: no client is built, but ``main`` resolves ``args.now``."""

    def test_production_host_is_refused_with_exit_20(self) -> None:
        self.rewrite_desk_toml('host = "sandbox"', 'host = "production"')
        code, body = run_json(["SPY", "--json", "--offline", "--desk-root", self.root])
        self.assertEqual(20, code)
        self.assertEqual(("unavailable", "production_host"), (body["status"], body["error"]["code"]))
        self.assertEqual("production market data is not supported before the market-data proxy (Stage 3); "
                         "use the sandbox host", body["error"]["message"])

    def test_invalid_config_is_exit_1(self) -> None:
        self.rewrite_desk_toml('grace_min = 5', 'grace_min = 31')
        code, body = run_json(["SPY", "--json", "--offline", "--desk-root", self.root])
        self.assertEqual((1, "error", "config_invalid"), (code, body["status"], body["error"]["code"]))


@unittest.skipUnless(READY, SKIP)
class GradingJourneysTest(DeskCopy):
    def test_regular_set_grades_a(self) -> None:
        code, body = run_json(self.argv("SPY", "WMT"))
        self.assertEqual(0, code, body)
        self.assertEqual(("ok", "tradier:sandbox", 15), (body["status"], body["source"], body["delay_min"]))
        payload = body["payload"]
        self.assertEqual(25, payload["bound_min"])
        self.assertEqual("open", payload["session"]["session"])
        self.assertEqual(["SPY", "WMT"], [s["symbol"] for s in payload["symbols"]])
        spy = payload["symbols"][0]
        self.assertEqual(("A", "fresh", "as of 14:31:12 ET", True), (spy["grade"], spy["status"], spy["label"],
                                                                    spy["usable_for_rules"]))
        self.assertEqual(("648.1", "648.14", "648.12", "648.1", "648.14", "0.01"),
                         (spy["bid"], spy["ask"], spy["mark_mid"], spy["mark_liq_long"], spy["mark_liq_short"],
                          spy["spread_pct"]))
        self.assertEqual((18, 18), (spy["age_min_bid"], spy["age_min_ask"]))
        self.assertEqual("fetch", spy["served_from"])
        self.assertEqual([], spy["reasons"])

    def test_option_grades_from_the_six_symbol_book(self) -> None:
        code, body = run_json(self.argv("NVDA", "NVDA261120C00150000", "SPY", "SPY261218P00620000", "WMT",
                                        "WMT261120C00105000"))
        self.assertEqual(0, code, body)
        symbols = body["payload"]["symbols"]
        self.assertEqual(["A", "C", "A", "B", "A", "B"], [s["grade"] for s in symbols])
        self.assertEqual(["0.03", "6.25", "0.01", "3.02", "0.06", "4.38"], [s["spread_pct"] for s in symbols])
        # Decimal from the vendor digits: (3.1 + 3.3) / 2 renders as 3.2, never a padded 3.20.
        self.assertEqual(["148.925", "3.2", "648.12", "9.95", "104.23", "3.425"], [s["mark_mid"] for s in symbols])
        self.assertEqual(1, len(self.ledger()), "six symbols are one request")

    def test_prose_lines_and_footer(self) -> None:
        code, text = run_tool(self.argv("SPY", "WMT", "WMT261120C00105000", json_mode=False))
        self.assertEqual(0, code, text)
        lines = text.splitlines()
        self.assertIn("SPY A fresh 648.10/648.14 0.01% 648.12 648.10 648.14 as of 14:31:12 ET", lines)
        self.assertIn("WMT A fresh 104.20/104.26 0.06% 104.23 104.20 104.26 as of 14:31:12 ET", lines)
        self.assertIn("WMT261120C00105000 B fresh 3.35/3.50 4.38% 3.425 3.35 3.50 as of 14:31:12 ET", lines)
        self.assertIn("session open; freshness bound 25 min", lines)
        self.assertEqual("Tradier sandbox, delayed 15 min, as of 19:31 UTC", lines[-1])

    def test_stale_set_is_exit_20(self) -> None:
        code, body = run_json(self.argv("SPY", fixture_set="stale"))
        self.assertEqual(20, code)
        self.assertEqual("unavailable", body["status"])
        spy = body["payload"]["symbols"][0]
        self.assertEqual(("F", "stale", False, 60), (spy["grade"], spy["status"], spy["usable_for_rules"],
                                                      spy["age_min_bid"]))
        self.assertIsNone(spy["mark_mid"])
        self.assertEqual("648.1", spy["bid"], "the stale quote is still displayed")
        code, text = run_tool(self.argv("SPY", fixture_set="stale", json_mode=False))
        self.assertEqual(20, code)
        self.assertIn("SPY F stale 648.10/648.14 0.01% - - - as of 13:50:00 ET", text.splitlines())

    def test_after_close_label(self) -> None:
        code, body = run_json(self.argv("SPY", fixture_set="after_close", now=AFTER_CLOSE_NOW))
        self.assertEqual(0, code, body)
        spy = body["payload"]["symbols"][0]
        self.assertEqual(("fresh", "as of close 16:00 ET", "A"), (spy["status"], spy["label"], spy["grade"]))
        self.assertEqual("closed", body["payload"]["session"]["session"])

    def test_pre_open_label(self) -> None:
        code, body = run_json(self.argv("SPY", fixture_set="pre_open", now=PRE_OPEN_NOW))
        self.assertEqual(0, code, body)
        spy = body["payload"]["symbols"][0]
        self.assertEqual(("fresh", "as of prior close 2026-11-02 16:00 ET", "645.88"),
                         (spy["status"], spy["label"], spy["bid"]))
        self.assertEqual("pre", body["payload"]["session"]["session"])

    def test_holiday_is_exit_20_without_a_fetch(self) -> None:
        code, body = run_json(self.argv("SPY", fixture_set="holiday", now=HOLIDAY_NOW))
        self.assertEqual(20, code)
        spy = body["payload"]["symbols"][0]
        self.assertEqual(("F", "missing", "NYSE closed"), (spy["grade"], spy["status"], spy["label"]))
        self.assertIn("cache: market_closed", spy["reasons"])
        self.assertEqual("holiday", body["payload"]["session"]["session"])
        self.assertEqual([], self.ledger())
        code, text = run_tool(self.argv("SPY", fixture_set="holiday", now=HOLIDAY_NOW, json_mode=False))
        self.assertEqual(20, code)
        self.assertIn("NYSE closed", text)

    def test_cached_copy_on_a_holiday_is_graded_closed_day(self) -> None:
        run_json(self.argv("SPY"))
        code, body = run_json(self.argv("SPY", fixture_set="holiday", now=HOLIDAY_NOW))
        self.assertEqual(20, code)
        spy = body["payload"]["symbols"][0]
        self.assertEqual(("F", "closed_day", "NYSE closed", ["market closed"]),
                         (spy["grade"], spy["status"], spy["label"], spy["reasons"]))

    def test_unmatched_symbol_is_missing(self) -> None:
        code, body = run_json(self.argv("SPY", "SPX"))
        self.assertEqual(20, code)
        spx = body["payload"]["symbols"][1]
        self.assertEqual(("SPX", "F", "missing"), (spx["symbol"], spx["grade"], spx["status"]))
        self.assertEqual(["cache: unmatched"], spx["reasons"])
        self.assertEqual("A", body["payload"]["symbols"][0]["grade"])

    def test_fixture_set_defaults_to_regular_in_the_payload(self) -> None:
        code, body = run_json(self.argv("SPY"))
        self.assertEqual((0, "regular"), (code, body["payload"]["fixture_set"]))

    def test_file_in_place_of_a_runtime_directory_is_io_error(self) -> None:
        with open(os.path.join(self.root, "state"), "w", encoding="utf-8") as handle:
            handle.write("not a directory\n")
        code, body = run_json(self.argv("SPY"))
        self.assertEqual((1, "error", "io_error"), (code, body["status"], body["error"]["code"]))
        self.assertIn("runtime directories", body["error"]["message"])

    def test_reads_through_the_cache_so_nothing_is_fetched_twice(self) -> None:
        import quote_cache as cache_tool
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            cache_tool.main(self.argv("SPY", "WMT"))
        code, body = run_json(self.argv("SPY", "WMT"))
        self.assertEqual(0, code)
        self.assertEqual(["cache", "cache"], [s["served_from"] for s in body["payload"]["symbols"]])
        self.assertEqual(1, len(self.ledger()))


class CalendarClosureTest(DeskCopy):
    """The session and the prior close follow research/calendar.toml's full closures, as in the six
    book-of-record tools: an owner-injected closure on today's ET date is a closed day (nothing fetched, grade F
    ``NYSE closed``, exit 20); a closure on the previous trading day moves the prior close that pre-open quotes
    are graded and labelled against; a closure on another date, an early close (never injected) or no calendar
    file leave the regular grades unchanged; a calendar that cannot be read is exit 1 ``calendar_invalid`` and
    nothing is fetched."""

    def add_to_calendar(self, text: str, root: str = "") -> None:
        with open(os.path.join(root or self.root, "research", "calendar.toml"), "a", encoding="utf-8") as handle:
            handle.write(text)

    def copy_desk(self, label: str) -> str:
        root = os.path.join(os.path.realpath(self._tmp.name), label)
        shutil.copytree(FIXTURE_DESK, root)
        return root

    def run_on(self, root: str, *symbols: str) -> Tuple[int, Dict[str, Any]]:
        return run_json(list(symbols) + ["--offline", "--desk-root", root, "--now", REGULAR_NOW,
                                         "--fixture-set", "regular", "--json"])

    def test_a_full_closure_today_is_a_closed_day_without_a_fetch(self) -> None:
        self.add_to_calendar(CLOSURE_ENTRY % ("2026-11-03", "full"))
        code, body = run_json(self.argv("SPY", "WMT", fixture_set="regular"))
        self.assertEqual((20, "unavailable"), (code, body["status"]))
        self.assertEqual({"symbols": [closed_day_missing("SPY"), closed_day_missing("WMT")], "bound_min": 25,
                          "session": CLOSED_TODAY_SESSION, "missing": {"SPY": "market_closed", "WMT": "market_closed"},
                          "warnings": [], "fixture_set": "regular"}, body["payload"])
        self.assertEqual([], self.ledger(), "nothing is fetched on a closed day")
        code, text = run_tool(self.argv("SPY", "WMT", fixture_set="regular", json_mode=False))
        self.assertEqual(20, code, text)
        self.assertEqual(["SPY F missing -/- - - - - NYSE closed", "WMT F missing -/- - - - - NYSE closed",
                          "NYSE closed (calendar closure): no quote fetch; nothing is usable for rules today",
                          "Tradier sandbox, delayed 15 min, as of 19:50 UTC"], text.splitlines())
        self.assertEqual([], self.ledger())

    def test_a_cached_copy_on_a_closure_day_is_graded_closed_day(self) -> None:
        code, _body = run_json(self.argv("SPY", fixture_set="regular"))
        self.assertEqual((0, 1), (code, len(self.ledger())))
        self.add_to_calendar(CLOSURE_ENTRY % ("2026-11-03", "full"))
        code, body = run_json(self.argv("SPY", fixture_set="regular", now="2026-11-03T20:30:00Z"))
        self.assertEqual((20, "unavailable"), (code, body["status"]))
        spy = body["payload"]["symbols"][0]
        self.assertEqual(("F", "closed_day", "NYSE closed", ["market closed"], False, "cache", "648.1", "648.14"),
                         (spy["grade"], spy["status"], spy["label"], spy["reasons"], spy["usable_for_rules"],
                          spy["served_from"], spy["bid"], spy["ask"]))
        self.assertEqual((None, None, None), (spy["mark_mid"], spy["mark_liq_long"], spy["mark_liq_short"]))
        self.assertEqual(1, len(self.ledger()), "the expired copy is not refetched on a closed day")
        code, text = run_tool(self.argv("SPY", fixture_set="regular", now="2026-11-03T20:30:00Z", json_mode=False))
        self.assertEqual(20, code, text)
        self.assertEqual(["SPY F closed_day 648.10/648.14 0.01% - - - NYSE closed",
                          "warning: SPY served from a cache copy 40 min old (TTL 5 min): no fetch on a non-trading day",
                          "NYSE closed (calendar closure): no quote fetch; nothing is usable for rules today",
                          "Tradier sandbox, delayed 15 min, as of 19:31 UTC"], text.splitlines())
        self.assertEqual(1, len(self.ledger()))

    def test_a_closure_on_the_previous_trading_day_moves_the_prior_close(self) -> None:
        # Monday 2026-11-02 closed by the owner: Tuesday's pre-open is judged against Friday's close (16:00 ET, still
        # daylight time: 20:00 UTC), and the recorded print of Monday 15:59:55 ET is not that close's quote.
        self.add_to_calendar(CLOSURE_ENTRY % ("2026-11-02", "full"))
        code, body = run_json(self.argv("SPY", fixture_set="pre_open", now=PRE_OPEN_NOW))
        self.assertEqual((20, "unavailable", "pre"), (code, body["status"], body["payload"]["session"]["session"]))
        spy = body["payload"]["symbols"][0]
        self.assertEqual(("F", "stale", "as of prior close 2026-10-30 16:00 ET", False),
                         (spy["grade"], spy["status"], spy["label"], spy["usable_for_rules"]))
        self.assertEqual(["bid stamped 4379 min after the prior close: not the closing quote",
                          "ask stamped 4379 min after the prior close: not the closing quote"], spy["reasons"])
        self.assertEqual((None, None, None), (spy["mark_mid"], spy["mark_liq_long"], spy["mark_liq_short"]))
        self.assertEqual(1, len(self.ledger()), "a pre-open run on a trading day still fetches")

    def test_the_moved_prior_close_also_labels_an_expired_copy_that_could_not_be_refreshed(self) -> None:
        code, first = run_json(self.argv("SPY", fixture_set="pre_open", now="2026-11-03T12:00:00Z"))
        self.assertEqual((0, "fetch"), (code, first["payload"]["symbols"][0]["served_from"]))
        self.add_to_calendar(CLOSURE_ENTRY % ("2026-11-02", "full"))
        # SPY's copy has expired by 13:00 UTC and the QQQ,SPY batch has no recorded answer: SPY is shown from its copy.
        code, body = run_json(self.argv("SPY", "QQQ", fixture_set="pre_open", now=PRE_OPEN_NOW))
        self.assertEqual(20, code, body)
        spy, qqq = body["payload"]["symbols"]
        self.assertEqual(("F", "missing", "as of prior close 2026-10-30 16:00 ET", ["cache: fetch_failed:network"],
                          "645.88", None), (spy["grade"], spy["status"], spy["label"], spy["reasons"], spy["bid"],
                                            spy["served_from"]))
        self.assertEqual(("F", "missing", "no quote"), (qqq["grade"], qqq["status"], qqq["label"]))

    def test_a_closure_elsewhere_an_early_close_or_no_calendar_leave_the_regular_grades_unchanged(self) -> None:
        code, baseline = self.run_on(self.root, *SIX_SYMBOLS)
        self.assertEqual((0, "open", ["A", "C", "A", "B", "A", "B"]),
                         (code, baseline["payload"]["session"]["session"],
                          [s["grade"] for s in baseline["payload"]["symbols"]]))
        for label, entry in (("closure-on-2026-11-10", CLOSURE_ENTRY % ("2026-11-10", "full")),
                             ("early-close-today", CLOSURE_ENTRY % ("2026-11-03", "early_close")),
                             ("no-calendar-file", None)):
            with self.subTest(variant=label):
                root = self.copy_desk(label)
                if entry is None:
                    os.remove(os.path.join(root, "research", "calendar.toml"))
                else:
                    self.add_to_calendar(entry, root)
                self.assertEqual((0, baseline), self.run_on(root, *SIX_SYMBOLS))

    def test_a_calendar_that_cannot_be_read_is_exit_1_calendar_invalid(self) -> None:
        for label, text, reason in (("toml-syntax", "\nnot = [toml\n", "invalid TOML"),
                                    ("unknown-key", "\nunknown_key = 1\n", "unknown key 'unknown_key'")):
            with self.subTest(calendar=label):
                root = self.copy_desk(label)
                self.add_to_calendar(text, root)
                code, body = self.run_on(root, "SPY", "WMT")
                self.assertEqual((1, "error"), (code, body["status"]), body)
                self.assertEqual("calendar_invalid", body["error"]["code"])
                message = body["error"]["message"]
                self.assertTrue(message.startswith(os.path.join(root, "research", "calendar.toml") + ": "), message)
                self.assertIn(reason, message)
                self.assertNotIn("payload", body)
                code, text_out = run_tool(["SPY", "--offline", "--desk-root", root, "--now", REGULAR_NOW])
                self.assertEqual((1, "error: " + message), (code, text_out.strip()))
                self.assertFalse(os.path.exists(os.path.join(root, "data", "cache", "ledger.jsonl")), "nothing is fetched")


if __name__ == "__main__":
    unittest.main()
