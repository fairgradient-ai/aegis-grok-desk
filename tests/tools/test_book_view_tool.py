"""``scripts/book.py --view`` and ``aegis_desk.book_view`` against a copied fixture desk root: the book view is one
markdown message (a title, the whole book, one block per open group, the sources and the fixed footer) with no
table, no OCC symbol and no account name the owner typed, linted in strict mode; the uncovered shares of an odd-lot
covered call explain its unbounded max profit; ``--json --view`` carries the message, its emitted document and the
lint; a view that fails its lint is withheld (exit 1)."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from typing import Any, Dict, List, Sequence, Tuple
from unittest import mock

from aegis_desk import alert_lint, book_view, summaries

import book as tool  # the tool module scripts/book.py

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
PREV_EOD_NOW = "2026-11-02T21:30:00Z"
FOOTER_1931 = alert_lint.footer_line(datetime.datetime(2026, 11, 3, 19, 31, tzinfo=datetime.timezone.utc))
OCC_SYMBOL = __import__("re").compile(r"\b[A-Z]{1,6}[0-9]{6}[CP][0-9]{8}\b")

REGULAR_VIEW = [
    "### YOUR BOOK 2026-11-03",
    "*3 open positions | Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 "
    "UTC*",
    "",
    "**The whole book**",
    "- **Unrealized** +$4,097.00 at the mids (+$4,034.00 at liquidation)",
    "- **Today** unavailable: no snapshot for 2026-11-02 (the desk's first day?)",
    "- **Realized** +$425.00 this year ($0.00 today, $0.00 this month)",
    "- **Net delta** 278.84 share-equivalents",
    "",
    "#### WMT covered call",
    "*AG-20261020-01 | account 1 of desk.toml (margin)*",
    "- **P/L** +$1,145.00 at the mids (+$1,124.00 at liquidation) on a $19,016.00 net cost",
    "- **Payoff** max profit $1,984.00 | max loss $19,016.00 | breakeven $95.08",
    "- **200 WMT shares** cost $98.50, now $104.23 | P/L +$1,146.00 | today unavailable",
    "  - *quote grade A | liquidation $104.20*",
    "- **2 short WMT 105 calls, 2026-11-20** cost $3.42, now $3.425 | P/L -$1.00 | today unavailable",
    "  - *quote grade B | liquidation $3.50 | IV 41.32 % | 17 calendar days (13 trading days) left*",
    "",
    "#### NVDA covered call",
    "*AG-20261022-02 | account 1 of desk.toml (margin)*",
    "- **P/L** +$1,515.00 at the mids (+$1,490.00 at liquidation) on a $27,630.00 net cost",
    "- **Payoff** max profit $2,370.00 | max loss $27,630.00 | breakeven $138.15",
    "- **200 NVDA shares** cost $141.20, now $148.925 | P/L +$1,545.00 | today unavailable",
    "  - *quote grade A | liquidation $148.90*",
    "- **2 short NVDA 150 calls, 2026-11-20** cost $3.05, now $3.20 | P/L -$30.00 | today unavailable",
    "  - *quote grade C | liquidation $3.30 | IV 27.80 % | 17 calendar days (13 trading days) left*",
    "",
    "#### SPY protective put",
    "*AG-20261028-03 | account 2 of desk.toml (ira)*",
    "- **P/L** +$1,437.00 at the mids (+$1,420.00 at liquidation) on a $64,370.00 net cost",
    "- **Payoff** max profit unbounded | max loss $2,370.00 | breakeven $643.70",
    "- **100 SPY shares** cost $632.40, now $648.12 | P/L +$1,572.00 | today unavailable",
    "  - *quote grade A | liquidation $648.10*",
    "- **1 long SPY 620 put, 2026-12-18** cost $11.30, now $9.95 | P/L -$135.00 | today unavailable",
    "  - *quote grade B | liquidation $9.80 | IV 24.68 % | 45 calendar days (32 trading days) left*",
    "",
    "*session open; as of 14:31:12 ET; freshness bound 25 min*",
    "",
    "deterministic rules you adopted · posted by an AI assistant · not personalized investment advice · data "
    "delayed 15 min, as of 19:31 UTC",
]


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = tool.main(list(argv))
    return code, buffer.getvalue()


class BookViewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-book-view-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, *extra: str, fixture_set: str = "regular", now: str = REGULAR_NOW) -> List[str]:
        return ["--offline", "--desk-root", self.root, "--now", now, "--fixture-set", fixture_set, "--view"] + list(extra)

    def rewrite(self, name: str, old: str, new: str) -> None:
        path = os.path.join(self.root, name)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))

    def test_the_whole_view(self) -> None:
        code, out = run_tool(self.argv())
        self.assertEqual(0, code, out)
        self.assertEqual("\n".join(REGULAR_VIEW) + "\n", out)

    def test_one_bubble_no_table_no_occ_symbol_no_account_name(self) -> None:
        code, out = run_tool(self.argv())
        self.assertEqual(0, code, out)
        self.assertFalse([line for line in out.splitlines() if line.startswith("|")], "no markdown table")
        self.assertIsNone(OCC_SYMBOL.search(out))
        for name in ("Brokerage (margin)", "Roth IRA"):
            self.assertNotIn(name, out, "the account name the owner typed is never printed")
        self.assertNotIn("```", out, "the view is posted as it is, never in a code block")

    def test_day_pl_from_the_previous_snapshot(self) -> None:
        code, _ = run_tool(["--offline", "--desk-root", self.root, "--now", PREV_EOD_NOW, "--fixture-set", "pre_open",
                            "--snapshot", "--json"])
        self.assertEqual(0, code)
        code, out = run_tool(self.argv())
        self.assertEqual(0, code, out)
        lines = out.splitlines()
        self.assertEqual("- **Today** +$400.00", lines[5])
        self.assertIn("- **2 short WMT 105 calls, 2026-11-20** cost $3.42, now $3.425 | P/L -$1.00 | today -$50.00",
                      lines)

    def test_an_odd_lot_names_the_shares_its_calls_leave_uncovered(self) -> None:
        # the user's case: a collar or covered call on 321.6301 shares with 3 contracts reads "max profit unbounded";
        # the view says which shares carry no call instead of leaving the owner (or a model) to subtract
        self.rewrite("positions.csv", "equity,WMT,,200,", "equity,WMT,,250.5,")
        code, out = run_tool(self.argv())
        self.assertIn(code, (0, 20), out)
        self.assertIn("max profit unbounded (50.5 of the 250.5 shares carry no call)", out)
        self.assertIn("- **250.5 WMT shares** cost $98.50", out)

    def test_json_carries_the_message_its_emitted_document_and_the_lint(self) -> None:
        code, out = run_tool(self.argv("--json"))
        self.assertEqual(0, code)
        body = json.loads(out)
        payload = body["payload"]
        self.assertEqual({"message", "emitted", "lint", "exit_code"}, set(payload))
        self.assertEqual("\n".join(REGULAR_VIEW), payload["message"])
        self.assertTrue(payload["lint"]["ok"], payload["lint"])
        relay = alert_lint.load_emitted(payload["emitted"])
        self.assertTrue(relay.check(payload["message"]).ok, "a relay re-lints the message against its document")
        self.assertEqual(0, payload["exit_code"])

    def test_an_unavailable_book_is_exit_20_and_still_a_linted_view(self) -> None:
        code, out = run_tool(self.argv(fixture_set="stale"))
        self.assertEqual(20, code, out)
        self.assertIn("| data unavailable", out)
        self.assertTrue(alert_lint.FOOTER_RE.match(out.rstrip("\n").splitlines()[-1]))

    def test_a_view_that_fails_its_lint_is_withheld(self) -> None:
        failing = book_view.BookView("x", {}, alert_lint.LintResult(False, ["p"], [alert_lint.Finding(
            "number_not_emitted", 1, 1, "p")], 0))
        with mock.patch.object(book_view, "render", return_value=failing):
            code, out = run_tool(self.argv())
            self.assertEqual(1, code)
            self.assertEqual("WITHHELD: the book view failed the alert lint (number_not_emitted); book.py without "
                             "--view prints the book\n", out)
            code, out = run_tool(self.argv("--json"))
            self.assertEqual(1, code)
            self.assertEqual("lint_failed", json.loads(out)["error"]["code"])

    def test_the_plain_book_is_unchanged(self) -> None:
        code, out = run_tool(["--offline", "--desk-root", self.root, "--now", REGULAR_NOW, "--fixture-set", "regular"])
        self.assertEqual(0, code)
        self.assertTrue(out.startswith("AG-20261020-01 covered_call WMT | Brokerage (margin) | net cost 19016.00"), out)


class LibraryTest(unittest.TestCase):
    """``aegis_desk.book_view`` on hand-built documents: the uncovered shares, quarantined groups, other warnings."""

    def doc(self) -> Dict[str, Any]:
        legs = [
            {"row_number": 2, "group_id": "AG-20261113-01", "status": "open", "occ": None, "instrument_type": "equity",
             "symbol": "NVDA", "underlying": "NVDA", "right": None, "strike": None, "expiry": None,
             "open_quantity": Decimal("321.6301"), "multiplier": 1, "entry_price_per_share": Decimal("150.93"),
             "mark_mid": Decimal("162.18"), "mark_liq": Decimal("162.17"), "quote_grade": "A",
             "unrealized_pl_mid": Decimal("3618.34"), "unrealized_pl_liq": Decimal("3615.12"), "day_pl": None,
             "iv": None, "dte_calendar": None, "dte_trading": None, "as_of": "2026-11-13T20:29:00+00:00"},
            {"row_number": 5, "group_id": "AG-20261113-01", "status": "open", "occ": "NVDA270521C00175000",
             "instrument_type": "option", "symbol": "NVDA270521C00175000", "underlying": "NVDA", "right": "CALL",
             "strike": Decimal("175"), "expiry": "2027-05-21", "open_quantity": Decimal("-3"), "multiplier": 100,
             "entry_price_per_share": Decimal("17.02"), "mark_mid": Decimal("18.475"), "mark_liq": Decimal("18.72"),
             "quote_grade": "B", "unrealized_pl_mid": Decimal("-436.50"), "unrealized_pl_liq": Decimal("-510.00"),
             "day_pl": Decimal("-436.50"), "iv": 0.477, "dte_calendar": 189, "dte_trading": 129,
             "as_of": "2026-11-13T20:29:00+00:00"},
        ]
        group = {"group_id": "AG-20261113-01", "trade_id": "AG-20261113-01", "account": "Schwab Individual",
                 "underlying": "NVDA", "structure": "collar", "net_cost": Decimal("48252.630993"),
                 "max_profit": None, "max_loss": Decimal("3252.63"), "breakevens": [Decimal("150.0252")],
                 "unbounded_up": True, "unbounded_down": False, "unrealized_pl_liq": Decimal("2682.12"),
                 "unrealized_pl_mid": Decimal("2799.34"), "data_quality": "ok", "legs": [2, 5]}
        return {"generated_at": "2026-11-13T20:44:00+00:00", "legs": legs, "groups": [group],
                "sources": [{"name": "alpaca:paper", "delay_min": 15}],
                "portfolio": {"unrealized_pl_liq": Decimal("8978.70"), "unrealized_pl_mid": Decimal("10006.47"),
                              "day_pl": None, "realized_pl_today": Decimal("0.00"), "realized_pl_mtd": Decimal("0.00"),
                              "realized_pl_ytd": Decimal("0.00"), "net_delta": 261.44, "open_groups": 1,
                              "quarantined_groups": []},
                "warnings": ["day P/L unavailable: no snapshot for 2026-11-12 (the desk's first day?)"]}

    def render(self, document: Dict[str, Any]) -> book_view.BookView:
        view = book_view.render(document, et_date=datetime.date(2026, 11, 13), accounts=[],
                                session_line="session open; as of 15:29:46 ET; freshness bound 25 min")
        self.assertTrue(view.ok, view.lint.problems)
        return view

    def test_the_collar_of_the_screenshot(self) -> None:
        view = self.render(self.doc())
        lines = view.text.split("\n")
        self.assertEqual(["### YOUR BOOK 2026-11-13",
                          "*1 open position | Alpaca paper (IEX stocks, indicative options: approximate), 15-min "
                          "delayed, as of 20:29 UTC*"], lines[:2])
        self.assertEqual("- **Unrealized** +$10,006.47 at the mids (+$8,978.70 at liquidation)", lines[4])
        self.assertEqual(["#### NVDA collar", "*AG-20261113-01 | the account in row 2 of positions.csv*",
                          "- **P/L** +$2,799.34 at the mids (+$2,682.12 at liquidation) on a $48,252.63 net cost",
                          "- **Payoff** max profit unbounded (21.6301 of the 321.6301 shares carry no call) | max loss "
                          "$3,252.63 | breakeven $150.0252",
                          "- **321.6301 NVDA shares** cost $150.93, now $162.18 | P/L +$3,618.34 | today unavailable",
                          "  - *quote grade A | liquidation $162.17*",
                          "- **3 short NVDA 175 calls, 2027-05-21** cost $17.02, now $18.475 | P/L -$436.50 | today "
                          "-$436.50",
                          "  - *quote grade B | liquidation $18.72 | IV 47.70 % | 189 calendar days (129 trading days) "
                          "left*"], lines[9:17])
        self.assertNotIn("Schwab", view.text)
        self.assertEqual(Decimal("21.6301"), book_view.uncovered_shares(self.doc()["groups"][0], self.doc()["legs"]))

    def test_covered_shares_print_no_count(self) -> None:
        document = self.doc()
        document["legs"][0]["open_quantity"] = Decimal("300")
        self.assertIsNone(book_view.uncovered_shares(document["groups"][0], document["legs"]))
        self.assertIn("max profit unbounded | max loss", self.render(document).text)
        document["legs"] = document["legs"][:1]
        self.assertIsNone(book_view.uncovered_shares(document["groups"][0], document["legs"]), "no short call")

    def test_quarantined_groups_and_other_warnings(self) -> None:
        document = self.doc()
        document["portfolio"]["quarantined_groups"] = [{"group_id": "AG-20261113-09",
                                                        "reasons": ["account mismatch: Schwab Individual, IRA"]}]
        document["warnings"].append("SPY: quote grade E (one side stale)")
        view = self.render(document)
        self.assertIn("#### AG-20261113-09 quarantined\n- account mismatch (positions_check.py shows the rows)", view.text)
        self.assertIn("- 1 more warning (`book.py` without `--view` prints it)", view.text)
        self.assertNotIn("Schwab", view.text)

    def test_a_closed_group_prints_nothing(self) -> None:
        document = self.doc()
        for leg in document["legs"]:
            leg["status"] = "closed"
        view = self.render(document)
        self.assertEqual("*0 open positions", view.text.split("\n")[1][:17])
        self.assertNotIn("NVDA collar", view.text)

    def test_the_composer_helpers(self) -> None:
        self.assertEqual("**day P/L**", summaries.bold_label("day P/L"))


if __name__ == "__main__":
    unittest.main()
