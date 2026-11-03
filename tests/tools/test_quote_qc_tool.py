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


if __name__ == "__main__":
    unittest.main()
