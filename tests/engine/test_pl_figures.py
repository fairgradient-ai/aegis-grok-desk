"""``aegis_desk.pl_figures``: the totals, the per-group day P/L and the percentages the desk views print, in Decimal,
from a book document (live Decimals or a snapshot's decimal strings): golden values, the never-partial group day
P/L, credit groups without a percentage, a start value at or below zero, the unavailable reason in words."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import unittest
from decimal import Decimal
from typing import Any, Dict, List

from aegis_desk import pl_figures

D = Decimal


def leg(row: int, group: str, quantity: str, multiplier: int, mark: str, day: Any) -> Dict[str, Any]:
    return {"row_number": row, "group_id": group, "status": "open", "open_quantity": D(quantity),
            "multiplier": multiplier, "mark_mid": D(mark), "day_pl": None if day is None else D(day)}


def document() -> Dict[str, Any]:
    """The fixture desk's 2026-11-03 book, its WMT and NVDA covered calls and SPY protective put."""
    legs: List[Dict[str, Any]] = [
        leg(3, "AG-20261020-01", "200", 1, "104.23", "84.00"), leg(4, "AG-20261020-01", "-2", 100, "3.425", "-50.00"),
        leg(5, "AG-20261022-02", "200", 1, "148.925", "264.00"), leg(6, "AG-20261022-02", "-2", 100, "3.20", "-70.00"),
        leg(7, "AG-20261028-03", "100", 1, "648.12", "222.00"), leg(8, "AG-20261028-03", "1", 100, "9.95", "-50.00")]
    groups = [
        {"group_id": "AG-20261020-01", "net_cost": D("19016.00"), "unrealized_pl_mid": D("1145.00"),
         "data_quality": "ok", "legs": [3, 4], "warnings": []},
        {"group_id": "AG-20261022-02", "net_cost": D("27630.00"), "unrealized_pl_mid": D("1515.00"),
         "data_quality": "ok", "legs": [5, 6], "warnings": []},
        {"group_id": "AG-20261028-03", "net_cost": D("64370.00"), "unrealized_pl_mid": D("1437.00"),
         "data_quality": "ok", "legs": [7, 8], "warnings": []}]
    return {"legs": legs, "groups": groups,
            "portfolio": {"unrealized_pl_mid": D("4097.00"), "unrealized_pl_liq": D("4034.00"), "day_pl": D("400.00"),
                          "realized_pl_ytd": D("425.00")}}


class BookFiguresTest(unittest.TestCase):
    def test_the_fixture_book(self) -> None:
        figures = pl_figures.book_figures(document())
        self.assertEqual((D("4522.00"), D("4459.00")), (figures.total_pl_mid, figures.total_pl_liq),
                         "the open positions plus what was closed this year")
        self.assertEqual(D("111016.00"), figures.open_cost)
        self.assertEqual(D("3.69"), figures.total_pct, "4097.00 / 111016.00 x 100")
        self.assertEqual(D("0.35"), figures.day_pct, "400.00 over the start values 20127 + 28951 + 65635")
        wmt = figures.groups["AG-20261020-01"]
        self.assertEqual((D("19016.00"), D("6.02"), D("34.00"), D("20127.00"), D("0.17")),
                         (wmt.cost, wmt.pl_pct, wmt.day_pl, wmt.start_value, wmt.day_pct))
        self.assertIsNone(wmt.reason)

    def test_a_snapshot_of_decimal_strings_reads_the_same(self) -> None:
        raw = document()
        for item in raw["legs"] + raw["groups"]:
            for key, value in list(item.items()):
                if isinstance(value, Decimal):
                    item[key] = str(value)
        raw["portfolio"] = {key: str(value) for key, value in raw["portfolio"].items()}
        self.assertEqual(pl_figures.book_figures(document()), pl_figures.book_figures(raw))

    def test_a_group_day_pl_is_never_partial(self) -> None:
        raw = document()
        raw["legs"][1]["day_pl"] = None
        figures = pl_figures.book_figures(raw)
        wmt = figures.groups["AG-20261020-01"]
        self.assertEqual((None, None, None), (wmt.day_pl, wmt.day_pct, wmt.start_value))
        self.assertEqual(D("6.02"), wmt.pl_pct, "the P/L % needs no day P/L")

    def test_an_unavailable_group_has_no_figures_but_its_reason(self) -> None:
        raw = document()
        raw["groups"][1].update(data_quality="unavailable", unrealized_pl_mid=None,
                                warnings=["row 6 NVDA261120C00150000: no usable mark (E fresh)",
                                          "row 5 NVDA: no usable mark (- missing)", "some other warning"])
        nvda = pl_figures.book_figures(raw).groups["AG-20261022-02"]
        self.assertEqual((None, None), (nvda.pl_pct, nvda.day_pl))
        self.assertEqual("row 6 had no usable quote (grade E, fresh); row 5 had no usable quote (missing)", nvda.reason)
        self.assertNotIn("NVDA261120C00150000", nvda.reason, "never an OCC symbol")

    def test_a_credit_group_and_a_start_value_at_or_below_zero_have_no_percentage(self) -> None:
        raw = document()
        raw["groups"][0]["net_cost"] = D("-480.00")
        raw["legs"][0].update(open_quantity=D("0"))
        wmt = pl_figures.book_figures(raw).groups["AG-20261020-01"]
        self.assertEqual((None, None), (wmt.cost, wmt.pl_pct))
        self.assertIsNone(wmt.day_pct, "a start value at or below zero gives no percentage")
        self.assertIsNotNone(wmt.day_pl)

    def test_a_missing_total_input_gives_none_and_signs(self) -> None:
        raw = document()
        raw["portfolio"]["unrealized_pl_mid"] = None
        raw["portfolio"]["day_pl"] = None
        figures = pl_figures.book_figures(raw)
        self.assertEqual((None, None, None), (figures.total_pl_mid, figures.total_pct, figures.day_pct))
        self.assertEqual([1, -1, 0, None], [pl_figures.sign_of(value) for value in (D("1"), D("-0.01"), D("0"), None)])
        self.assertIsNone(pl_figures.decimal_of(1.5), "a float is never money")
        self.assertIsNone(pl_figures.decimal_of("not a number"))


if __name__ == "__main__":
    unittest.main()
