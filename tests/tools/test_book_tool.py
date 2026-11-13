"""``scripts/book.py`` run in-process against a copied fixture desk root: the exit codes (0 every open group ok,
20 any group unavailable or market data refused, 1 configuration or file errors), the ``--json`` payload (the book
dict with decimal strings), ``--snapshot`` (the artifact of record under ``data/book/``) feeding the next day's
day P/L, ``--report`` and the prose table with the fixed footer."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Sequence, Tuple
from unittest import mock

from aegis_desk import book as library
from aegis_desk import quote_cache

import book as tool  # the tool module scripts/book.py, not the library aegis_desk.book

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
PREV_EOD_NOW = "2026-11-02T21:30:00Z"
HOLIDAY_NOW = "2026-11-26T15:00:00Z"
CLOSURE_ENTRY = '\n[[closures]]\ndate = %s\nkind = "full"\nsource = "owner-entered (test)"\n'
CLOSED_TODAY_LINE = "NYSE closed (calendar closure): no quote fetch; nothing is usable for rules today"
ORDER_SHAPED = re.compile(r"\b(?:buy|sell|sold|bought|place|submit|limit order|market order|good till|time in force)\b",
                          re.IGNORECASE)


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
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-book-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, fixture_set: str = "regular", now: str = REGULAR_NOW, json_mode: bool = True,
             extra: Sequence[str] = ()) -> List[str]:
        argv = ["--offline", "--desk-root", self.root, "--now", now, "--fixture-set", fixture_set] + list(extra)
        if json_mode:
            argv.append("--json")
        return argv

    def ledger(self) -> List[Dict[str, Any]]:
        path = os.path.join(self.root, "data", "cache", "ledger.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]

    def rewrite(self, name: str, old: str, new: str) -> None:
        path = os.path.join(self.root, name)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))


class AlwaysOnTest(DeskCopy):
    def test_schema(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual("book", body["tool"])
        payload = body["envelope"]["payload"]
        for key in library.BOOK_KEYS:
            self.assertIn(key, payload)
        self.assertIn("20", body["exit_codes"])

    def test_schema_describes_exactly_the_payload_keys(self) -> None:
        # The --schema leg, group, underlying and portfolio objects carry exactly the keys the --json payload
        # carries (docs/TOOLS.md is generated from --schema): no drift in either direction.
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        described = body["envelope"]["payload"]
        self.assertEqual(set(library.BOOK_KEYS), set(described))
        self.assertEqual(set(library.LEG_KEYS), set(described["legs"][0]))
        self.assertIn("open_quantity", described["legs"][0])
        self.assertIn("remaining_cost_basis", described["legs"][0])
        self.assertIn("fresh_bid", described["legs"][0])
        self.assertIn("fresh_ask", described["legs"][0])
        self.assertEqual(set(library.GROUP_KEYS), set(described["groups"][0]))
        self.assertEqual(set(library.UNDERLYING_KEYS), set(described["underlyings"]["SYMBOL"]))
        self.assertEqual(set(library.PORTFOLIO_KEYS), set(described["portfolio"]))
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        payload = body["payload"]
        for leg in payload["legs"]:
            self.assertEqual(set(described["legs"][0]), set(leg), leg["row_number"])
        for group in payload["groups"]:
            self.assertEqual(set(described["groups"][0]), set(group), group["group_id"])
        for symbol, entry in payload["underlyings"].items():
            self.assertEqual(set(described["underlyings"]["SYMBOL"]), set(entry), symbol)
        self.assertEqual(set(described["portfolio"]), set(payload["portfolio"]))
        call = [leg for leg in payload["legs"] if leg["occ"] == "WMT261120C00105000"][0]
        self.assertEqual(("-2", "-684.00"), (call["open_quantity"], call["remaining_cost_basis"]), "decimal strings")
        self.assertEqual(("3.35", "3.5"), (call["fresh_bid"], call["fresh_ask"]), "decimal strings")

    def test_usage_errors(self) -> None:
        for argv in (["--json", "--offline"],
                     ["--json", "--desk-root", self.root, "--fixture-set", "regular"],
                     ["--json", "--offline", "--desk-root", self.root, "--bogus"],
                     ["--json", "--offline", "--desk-root", self.root, "--now", "2026-11-03"]):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(argv)
                self.assertEqual(2, ctx.exception.code)


class PlumbingTest(DeskCopy):
    def test_a_live_host_is_refused_at_load(self) -> None:
        self.rewrite("desk.toml", 'host = "paper"', 'host = "live"')
        code, body = run_json(["--json", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
        self.assertEqual((1, "error", "config_invalid"), (code, body["status"], body["error"]["code"]))

    def test_refused_market_data_is_exit_20(self) -> None:
        refusal = quote_cache.MarketDataRefused(quote_cache.REFUSED_NO_CREDENTIAL, quote_cache.NO_CREDENTIAL_MESSAGE)
        with mock.patch.object(quote_cache, "build_client", side_effect=refusal):
            code, body = run_json(["--json", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
        self.assertEqual(20, code)
        self.assertEqual(("unavailable", "no_credential"), (body["status"], body["error"]["code"]))

    def test_invalid_config_is_exit_1(self) -> None:
        self.rewrite("desk.toml", "grace_min = 5", "grace_min = 31")
        code, body = run_json(["--json", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
        self.assertEqual((1, "error", "config_invalid"), (code, body["status"], body["error"]["code"]))

    def test_positions_file_error_is_exit_1(self) -> None:
        self.rewrite("positions.csv", "cost_basis_total,", "cost_basis_totl,")
        code, body = run_json(self.argv())
        self.assertEqual((1, "error", "positions_invalid"), (code, body["status"], body["error"]["code"]))
        self.assertIn("did you mean", body["error"]["message"])
        self.assertEqual([], self.ledger())

    def test_missing_positions_file_is_exit_1(self) -> None:
        os.remove(os.path.join(self.root, "positions.csv"))
        code, body = run_json(self.argv())
        self.assertEqual((1, "error", "positions_missing"), (code, body["status"], body["error"]["code"]))
        code, text = run_tool(self.argv(json_mode=False))
        self.assertEqual(1, code)
        self.assertTrue(text.startswith("error: "), text)

    def test_bad_calendar_is_exit_1(self) -> None:
        with open(os.path.join(self.root, "research", "calendar.toml"), "a", encoding="utf-8") as handle:
            handle.write("\nunknown_key = 1\n")
        code, body = run_json(self.argv())
        self.assertEqual((1, "error", "calendar_invalid"), (code, body["status"], body["error"]["code"]))

    def test_file_in_place_of_a_runtime_directory_is_io_error(self) -> None:
        with open(os.path.join(self.root, "state"), "w", encoding="utf-8") as handle:
            handle.write("not a directory\n")
        code, body = run_json(self.argv())
        self.assertEqual((1, "error", "io_error"), (code, body["status"], body["error"]["code"]))


class JourneysTest(DeskCopy):
    def test_regular_book_is_exit_0_with_the_goldens(self) -> None:
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertEqual(("ok", "book", "alpaca:paper", 15, "2026-11-03T19:50:00+00:00"),
                         (body["status"], body["tool"], body["source"], body["delay_min"], body["generated_at"]))
        payload = body["payload"]
        self.assertEqual(set(library.BOOK_KEYS), set(payload))
        portfolio = payload["portfolio"]
        self.assertEqual(("4097.00", "4034.00", None, "0.00", "0.00", "425.00"),
                         (portfolio["unrealized_pl_mid"], portfolio["unrealized_pl_liq"], portfolio["day_pl"],
                          portfolio["realized_pl_today"], portfolio["realized_pl_mtd"], portfolio["realized_pl_ytd"]))
        self.assertEqual({"WMT": "8.61", "NVDA": "12.17", "SPY": "26.32"}, portfolio["concentration"]["by_underlying"])
        self.assertEqual(["1146.00", "-1.00", "1545.00", "-30.00", "1572.00", "-135.00", None],
                         [leg["unrealized_pl_mid"] for leg in payload["legs"]])
        self.assertEqual(["1140.00", "-16.00", "1540.00", "-50.00", "1570.00", "-150.00", None],
                         [leg["unrealized_pl_liq"] for leg in payload["legs"]])
        wmt = payload["groups"][0]
        self.assertEqual(("AG-20261020-01", "covered_call", "19016.00", "1984.00", "19016.00", ["95.08"], "ok"),
                         (wmt["group_id"], wmt["structure"], wmt["net_cost"], wmt["max_profit"], wmt["max_loss"],
                          wmt["breakevens"], wmt["data_quality"]))
        self.assertEqual(("open", "as of 14:31:12 ET"), (payload["session"], payload["as_of_label"]))
        self.assertIn("day P/L unavailable: no snapshot for 2026-11-02 (the desk's first day?)", payload["warnings"])
        self.assertEqual(2, len(self.ledger()), "one quotes batch for the whole book: equities, then options")
        self.assertEqual("ok", payload["legs"][1]["iv_status"])
        self.assertIsInstance(payload["legs"][1]["iv"], float)

    def test_stale_set_is_exit_20_and_still_prints_the_book(self) -> None:
        code, body = run_json(self.argv(fixture_set="stale"))
        self.assertEqual(20, code)
        self.assertEqual("unavailable", body["status"])
        payload = body["payload"]
        self.assertEqual(set(library.BOOK_KEYS), set(payload))
        self.assertIsNone(payload["portfolio"]["unrealized_pl_mid"])
        self.assertEqual(["unavailable"] * 3, [group["data_quality"] for group in payload["groups"][:3]])
        self.assertTrue(all(leg["mark_source"] == "unavailable" for leg in payload["legs"]))

    def test_snapshot_then_next_day_reports_day_pl(self) -> None:
        code, body = run_json(self.argv(fixture_set="pre_open", now=PREV_EOD_NOW, extra=["--snapshot"]))
        self.assertEqual(0, code, body)
        path = os.path.join(self.root, "data", "book", "2026-11-02.json")
        self.assertTrue(os.path.isfile(path), "the snapshot of record")
        with open(path, encoding="utf-8") as handle:
            snapshot = json.load(handle)
        self.assertEqual(set(library.BOOK_KEYS), set(snapshot))
        self.assertEqual(("2026-11-02T21:30:00+00:00", "closed"), (snapshot["generated_at"], snapshot["session"]))
        self.assertEqual(body["payload"], snapshot, "the file holds exactly the printed book")
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual("400.00", payload["portfolio"]["day_pl"])
        self.assertEqual(["84.00", "-50.00", "264.00", "-70.00", "222.00", "-50.00", None],
                         [leg["day_pl"] for leg in payload["legs"]])
        self.assertEqual(("103.81", "0.40"), (payload["underlyings"]["WMT"]["prev_eod_mid"],
                                              payload["underlyings"]["WMT"]["move_pct_from_prev_eod"]))
        self.assertEqual([], [w for w in payload["warnings"] if "day P/L" in w])
        self.assertFalse(os.path.exists(os.path.join(self.root, "data", "book", "2026-11-03.json")),
                         "no --snapshot on the second run")

    def test_regular_legs_carry_their_fresh_sides_and_the_extrinsic_goldens(self) -> None:
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        legs = body["payload"]["legs"]
        self.assertEqual([("104.2", "104.26"), ("3.35", "3.5"), ("148.9", "148.95"), ("3.1", "3.3"), ("648.1", "648.14"),
                          ("9.8", "10.1"), (None, None)], [(leg["fresh_bid"], leg["fresh_ask"]) for leg in legs],
                         "every open leg's fresh sides, the stock and ETF legs included; the closed row has none")
        self.assertEqual([None, "3.35", None, "3.1", None, "9.8", None], [leg["extrinsic_bid"] for leg in legs],
                         "every regular leg is A-D and aligned: the extrinsic goldens keep their values")

    def test_a_snapshot_without_the_fresh_sides_still_gives_the_day_pl(self) -> None:
        # A snapshot of record written before the book kept fresh_bid / fresh_ask is still read: the next day's
        # day P/L is the same golden as from a snapshot of today's shape.
        code, body = run_json(self.argv(fixture_set="pre_open", now=PREV_EOD_NOW, extra=["--snapshot"]))
        self.assertEqual(0, code, body)
        path = os.path.join(self.root, "data", "book", "2026-11-02.json")
        with open(path, encoding="utf-8") as handle:
            snapshot = json.load(handle)
        self.assertTrue(all({"fresh_bid", "fresh_ask"} <= set(leg) for leg in snapshot["legs"]))
        for leg in snapshot["legs"]:
            del leg["fresh_bid"]
            del leg["fresh_ask"]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(snapshot, handle)
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual("400.00", payload["portfolio"]["day_pl"])
        self.assertEqual(["84.00", "-50.00", "264.00", "-70.00", "222.00", "-50.00", None],
                         [leg["day_pl"] for leg in payload["legs"]])
        self.assertEqual([], [w for w in payload["warnings"] if "day P/L" in w])

    def test_snapshot_is_written_even_when_the_book_is_unavailable(self) -> None:
        code, _ = run_json(self.argv(fixture_set="stale", extra=["--snapshot"]))
        self.assertEqual(20, code)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "data", "book", "2026-11-03.json")))

    def test_report_is_written(self) -> None:
        code, text = run_tool(self.argv(json_mode=False, extra=["--report", "--snapshot"]))
        self.assertEqual(0, code, text)
        report = os.path.join(self.root, "reports", "book.md")
        self.assertTrue(os.path.isfile(report))
        with open(report, encoding="utf-8") as handle:
            content = handle.read()
        for needle in ("AG-20261020-01", "covered_call", "4097.00", "Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min"):
            self.assertIn(needle, content)
        self.assertIn("reports/book.md", text)
        self.assertIn("data/book/2026-11-03.json", text)

    def test_prose_table_and_footer(self) -> None:
        code, text = run_tool(self.argv(json_mode=False))
        self.assertEqual(0, code, text)
        lines = text.splitlines()
        self.assertEqual("Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of 19:31 UTC", lines[-1])
        self.assertIn("as of 14:31:12 ET", lines[-2])
        joined = "\n".join(lines)
        for needle in ("AG-20261020-01 covered_call WMT", "net cost 19016.00", "max profit 1984.00", "max loss 19016.00",
                       "BE 95.08", "WMT261120C00105000 -2", "mark 3.425", "unrealized mid 4097.00", "liq 4034.00",
                       "YTD 425.00", "DTE 17/13", "day P/L unavailable"):
            self.assertIn(needle, joined)
        group_lines = [line for line in lines if line.startswith("AG-")]
        self.assertEqual(["AG-20261020-01", "AG-20261022-02", "AG-20261028-03", "AG-20261001-04"],
                         [line.split()[0] for line in group_lines])
        self.assertIsNone(ORDER_SHAPED.search(text), text)

    def test_holiday_prints_nyse_closed_and_fetches_nothing(self) -> None:
        code, text = run_tool(self.argv(fixture_set="holiday", now=HOLIDAY_NOW, json_mode=False))
        self.assertEqual(20, code)
        self.assertIn("NYSE closed", text)
        self.assertIn("NYSE closed (Thanksgiving Day): no quote fetch; nothing is usable for rules today", text.splitlines())
        self.assertEqual([], self.ledger())
        code, body = run_json(self.argv(fixture_set="holiday", now=HOLIDAY_NOW))
        self.assertEqual((20, "unavailable", "holiday"), (code, body["status"], body["payload"]["session"]))

    def test_json_output_never_carries_order_shaped_text(self) -> None:
        code, text = run_tool(self.argv())
        self.assertEqual(0, code)
        self.assertIsNone(ORDER_SHAPED.search(text), text)

    def test_fixture_set_defaults_to_regular(self) -> None:
        code, body = run_json(["--json", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
        self.assertEqual(0, code)
        self.assertEqual("4097.00", body["payload"]["portfolio"]["unrealized_pl_mid"])


class InjectedClosureTest(DeskCopy):
    """An owner-injected full closure in the copy's research/calendar.toml: the book follows the calendar like
    every other market tool. On today's ET date that is a closed day (exit 20, the closed-day line, --json
    unavailable, no fetch) and --snapshot still writes the day's record of what was known; on another date
    only the trading-day counts move."""

    def inject_closure(self, day: str) -> None:
        with open(os.path.join(self.root, "research", "calendar.toml"), "a", encoding="utf-8") as handle:
            handle.write(CLOSURE_ENTRY % day)

    def test_a_closure_on_today_is_a_closed_day_in_prose_and_json(self) -> None:
        self.inject_closure("2026-11-03")
        code, text = run_tool(self.argv(json_mode=False))
        self.assertEqual(20, code, text)
        lines = text.splitlines()
        self.assertEqual(CLOSED_TODAY_LINE, lines[-2])
        self.assertEqual("Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of 19:50 UTC", lines[-1], "no quote stamp: the run's own instant")
        self.assertEqual([], self.ledger(), "no quote fetch")
        self.assertIsNone(ORDER_SHAPED.search(text), text)
        code, body = run_json(self.argv())
        self.assertEqual((20, "unavailable"), (code, body["status"]))
        payload = body["payload"]
        self.assertEqual(("holiday", "NYSE closed"), (payload["session"], payload["as_of_label"]))
        self.assertEqual(["unavailable", "unavailable", "unavailable", "ok"],
                         [group["data_quality"] for group in payload["groups"]])
        self.assertIsNone(payload["portfolio"]["unrealized_pl_mid"], "never a zero")
        call = [leg for leg in payload["legs"] if leg["occ"] == "WMT261120C00105000"][0]
        self.assertEqual((17, 13, "F", "missing", "unavailable"),
                         (call["dte_calendar"], call["dte_trading"], call["quote_grade"], call["quote_status"],
                          call["mark_source"]), "the ET date is unchanged; the leg is unavailable, not valued")
        self.assertEqual([], self.ledger())

    def test_snapshot_still_records_the_closed_day(self) -> None:
        self.inject_closure("2026-11-03")
        code, text = run_tool(self.argv(json_mode=False, extra=["--snapshot"]))
        self.assertEqual(20, code, text)
        self.assertIn("snapshot written: data/book/2026-11-03.json", text.splitlines())
        path = os.path.join(self.root, "data", "book", "2026-11-03.json")
        self.assertTrue(os.path.isfile(path), "the record of what was known")
        with open(path, encoding="utf-8") as handle:
            snapshot = json.load(handle)
        self.assertEqual(("holiday", "NYSE closed", "2026-11-03T19:50:00+00:00"),
                         (snapshot["session"], snapshot["as_of_label"], snapshot["generated_at"]))
        self.assertEqual(set(library.BOOK_KEYS), set(snapshot))
        self.assertEqual([], self.ledger())

    def test_a_closure_on_another_date_leaves_the_open_day_alone(self) -> None:
        self.inject_closure("2026-11-10")
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual(("open", "as of 14:31:12 ET", "4097.00"),
                         (payload["session"], payload["as_of_label"], payload["portfolio"]["unrealized_pl_mid"]))
        self.assertEqual([12, 12, 31], [leg["dte_trading"] for leg in payload["legs"]
                                        if leg["occ"] is not None and leg["status"] == "open"],
                         "one trading day off every open leg that spans the closure")
        self.assertEqual(2, len(self.ledger()))


if __name__ == "__main__":
    unittest.main()
