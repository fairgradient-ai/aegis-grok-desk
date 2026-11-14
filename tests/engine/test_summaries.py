"""``aegis_desk.summaries``: the Morning Desk Sheet and the EOD Desk Brief composed from the desk's own records
(PR 2d decisions D1-13 to D1-15, addendum overrides O3, O5, O12, O15): every line's exact text, the UNAVAILABLE
lines of a missing, late, unreadable or malformed record, the number discipline (every number a string a formatter
emitted, nothing summed), the strict lint and the footer's instant."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import unittest
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence

from aegis_desk import alert_lint, contract, routines, signals, summaries

UTC = datetime.timezone.utc
DAY = datetime.date(2026, 11, 3)
MORNING_NOW = contract.parse_utc_instant("2026-11-03T12:35:00Z")
BRIEF_NOW = contract.parse_utc_instant("2026-11-03T22:12:00Z")
WMT, NVDA, SPY = "AG-20261020-01", "AG-20261022-02", "AG-20261028-03"
FOOTER_1231 = alert_lint.footer_line(contract.parse_utc_instant("2026-11-03T12:31:00Z"))
FOOTER_1931 = alert_lint.footer_line(contract.parse_utc_instant("2026-11-03T19:31:12Z"))
# the oldest data instant of the default morning records: last night's levels sheet (book as of 20:59:55)
FOOTER_2059_PREVIOUS = alert_lint.footer_line(contract.parse_utc_instant("2026-11-02T20:59:55Z"))
RECON_PATH = "data/positions/2026-11-03.recon.json"
DOCKET_PATH = "reports/expiry/2026-11-03.week.json"
LEVELS_PATH = "levels/2026-11-02.json"
DOCTOR_PATH = "state/doctor.json"
BOOK_PATH = "data/book/2026-11-03.json"


def order_shaped(text: str) -> List[str]:
    return [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(text)]


def doctor_record(mode: str = "quick", verdict: str = "PASS", run_utc: str = "2026-11-03T12:31:00+00:00",
                  statuses: Sequence[Any] = (("interpreter", "PASS"), ("key_source", "WARN"))) -> Dict[str, Any]:
    checks = [{"id": name, "number": index, "status": status, "reason": "a reason the sheet never prints"}
              for index, (name, status) in enumerate(statuses, 1)]
    return {"schema": 1, "run_utc": run_utc, "mode": mode, "verdict": verdict, "checks": checks,
            "machine_role": "shared", "tool_version": "0.1.0"}


def recon_record(verdict: str = "CLEAN", edits: int = 0, unavailable: int = 0, et_date: str = "2026-11-03",
                 generated_at: str = "2026-11-03T12:31:00+00:00", as_of: Optional[str] = None) -> Dict[str, Any]:
    """``as_of`` (the instant of the record's data, csv_recon's payload.as_of) defaults to ``generated_at``."""
    return {"generated_at": generated_at, "status": "ok", "tool": "csv_recon",
            "payload": {"verdict": verdict, "et_date": et_date, "counts": {"edits": edits, "unavailable": unavailable},
                        "edits": [{"csv_line": "a cell the owner typed: never printed"}],
                        "as_of": generated_at if as_of is None else as_of}}


def docket_leg(trade_id: Optional[str], underlying: str, structure: str, last_trading_day: str, dte: int,
               act_by_text: str, symbol: str, side: str, contracts: str, group_id: str = "ROW-4") -> Dict[str, Any]:
    return {"trade_id": trade_id, "group_id": group_id, "underlying": underlying, "structure": structure,
            "symbol": symbol, "side": side, "contracts": contracts,
            "last_trading_day": last_trading_day, "dte_trading": dte, "act_by_text": act_by_text,
            "act_by_passed": False}


def docket_record(legs: Sequence[Dict[str, Any]] = (), today: str = "2026-11-03", unavailable: int = 0,
                  generated_at: str = "2026-11-03T12:31:00+00:00", as_of: Optional[str] = None) -> Dict[str, Any]:
    return {"generated_at": generated_at, "status": "ok", "tool": "expiry_checklist",
            "payload": {"mode": "week", "today": today, "legs": list(legs),
                        "counts": {"listed": len(legs), "unavailable": unavailable},
                        "as_of": generated_at if as_of is None else as_of}}


def levels_record(et_date: str = "2026-11-02",
                  groups: Sequence[Any] = ((WMT, 3, 1), (NVDA, 5, 1), (SPY, 3, 0)),
                  as_of: str = "2026-11-02T20:59:55+00:00") -> Dict[str, Any]:
    """``as_of`` is the levels' book.as_of (its data's instant; the D1-16 staging has 2026-11-02T20:59:55)."""
    return {"et_date": et_date, "book": {"generated_at": "2026-11-02T21:30:00+00:00", "as_of": as_of},
            "groups": [{"group_id": trade_id or "ROW-1", "trade_id": trade_id, "price_levels": price,
                        "resting_eligible": resting} for trade_id, price, resting in groups]}


def made(label: str, path: str, raw: Any, expected: datetime.date = DAY, status: str = "ok",
         older: Optional[datetime.date] = None) -> summaries.Artifact:
    return summaries.make_artifact(label, path, raw, status, expected, older)


def open_signal(signal_id: str, trade_id: str, structure: str, verdict: str, plan: Optional[str], posts: int,
                posted_at: str = "2026-11-03T19:50:00Z", snoozed: Optional[str] = None) -> signals.OpenSignal:
    first = contract.parse_utc_instant(posted_at)
    return signals.OpenSignal(signal_id=signal_id, trade_id=trade_id, structure=structure, verdict=verdict, plan=plan,
                              posted_at=first, posts=posts, last_posted_at=first if posts else None,
                              ack_state=signals.ACK_STATE_SNOOZED if snoozed is not None else signals.ACK_STATE_OPEN,
                              snoozed_until=snoozed, record={})


def morning(**changes: Any) -> summaries.MorningInputs:
    values: Dict[str, Any] = {
        "et_date": DAY, "now": MORNING_NOW,
        "doctor": made(summaries.LABEL_DOCTOR, DOCTOR_PATH, doctor_record()),
        "recon": made(summaries.LABEL_RECON, RECON_PATH, recon_record()),
        "docket": made(summaries.LABEL_DOCKET, DOCKET_PATH, docket_record()),
        "levels": made(summaries.LABEL_LEVELS, LEVELS_PATH, levels_record(), datetime.date(2026, 11, 2)),
        "open_signals": [], "delay_min": 15,
    }
    values.update(changes)
    return summaries.MorningInputs(**values)


def book_leg(row: int, group_id: str, status: str = "open", day_pl: Any = Decimal("0.00"),
             as_of: str = "2026-11-03T19:31:12+00:00") -> Dict[str, Any]:
    return {"row_number": row, "group_id": group_id, "status": status, "day_pl": day_pl,
            "as_of": None if status != "open" else contract.parse_utc_instant(as_of.replace("+00:00", "Z"))}


def book_document(**portfolio: Any) -> Dict[str, Any]:
    values: Dict[str, Any] = {
        "realized_pl_today": Decimal("0.00"), "realized_pl_mtd": Decimal("0.00"), "realized_pl_ytd": Decimal("425.00"),
        "unrealized_pl_liq": Decimal("4034.00"), "unrealized_pl_mid": Decimal("4097.00"), "day_pl": Decimal("400.00"),
        "open_groups": 3, "quarantined_groups": []}
    values.update(portfolio)
    groups = [
        {"group_id": WMT, "trade_id": WMT, "underlying": "WMT", "structure": "covered_call", "legs": [3, 4],
         "unrealized_pl_liq": Decimal("1124.00"), "unrealized_pl_mid": Decimal("1145.00"), "data_quality": "ok"},
        {"group_id": NVDA, "trade_id": NVDA, "underlying": "NVDA", "structure": "covered_call", "legs": [5, 6],
         "unrealized_pl_liq": Decimal("1490.00"), "unrealized_pl_mid": Decimal("1515.00"), "data_quality": "ok"},
        {"group_id": SPY, "trade_id": SPY, "underlying": "SPY", "structure": "protective_put", "legs": [7, 8],
         "unrealized_pl_liq": Decimal("1420.00"), "unrealized_pl_mid": Decimal("1437.00"), "data_quality": "ok"},
        {"group_id": "AG-20261001-04", "trade_id": "AG-20261001-04", "underlying": "SPY",
         "structure": "cash_secured_put", "legs": [9], "unrealized_pl_liq": None, "unrealized_pl_mid": None,
         "data_quality": "ok"},
    ]
    legs = [book_leg(3, WMT, day_pl=Decimal("84.00")), book_leg(4, WMT, day_pl=Decimal("-50.00")),
            book_leg(5, NVDA, day_pl=Decimal("264.00")), book_leg(6, NVDA, day_pl=Decimal("-70.00")),
            book_leg(7, SPY, day_pl=Decimal("222.00")), book_leg(8, SPY, day_pl=Decimal("-50.00")),
            book_leg(9, "AG-20261001-04", status="closed", day_pl=None)]
    return {"generated_at": contract.parse_utc_instant("2026-11-03T21:35:00Z"), "legs": legs, "groups": groups,
            "portfolio": values, "warnings": [], "sources": [{"name": "alpaca:paper", "delay_min": 15}]}


def book_artifact(document: Optional[Dict[str, Any]] = None) -> summaries.Artifact:
    document = book_document() if document is None else document
    return summaries.Artifact(summaries.LABEL_BOOK, BOOK_PATH, summaries.ARTIFACT_OK, document=document,
                              instant=summaries.book_instant(document), present=True)


def slot(routine_id: str, scheduled_et: str, ran: bool = False, lateness: Optional[int] = None) -> routines.SlotAudit:
    when = routines.slot_instant(DAY, scheduled_et)
    return routines.SlotAudit(routine_id, scheduled_et, when, 1 if ran else 0, when if ran else None,
                              lateness if ran else None, 0 if ran else None, 0 if ran else None, "ok" if ran else None)


def audit(expected: Sequence[routines.SlotAudit], missed: Sequence[routines.SlotAudit] = ()) -> routines.DayAudit:
    ran = tuple(item for item in expected if item.ran)
    late = tuple(item for item in ran if item.lateness_min is not None and item.lateness_min > routines.LATE_AFTER_MIN)
    return routines.DayAudit(tuple(expected), ran, late, tuple(missed))


def brief(**changes: Any) -> summaries.BriefInputs:
    values: Dict[str, Any] = {
        "et_date": DAY, "now": BRIEF_NOW, "book": book_artifact(),
        "recon": made(summaries.LABEL_RECON, RECON_PATH, recon_record(generated_at="2026-11-03T21:36:00+00:00")),
        "docket": made(summaries.LABEL_DOCKET, DOCKET_PATH, docket_record()),
        "signal_records": [], "open_signals": [], "delay_min": 15,
        "audit": audit([slot("preopen", "07:30", True, 3), slot("book-check", "10:05", True, 31),
                        slot("book-check", "12:05"), slot("book-check", "14:05", True, 0)],
                       missed=[slot("book-check", "12:05")]),
        "config_changes": [], "calendar_findings": 0,
    }
    values.update(changes)
    return summaries.BriefInputs(**values)


class LintedTest(unittest.TestCase):
    def assertLinted(self, summary: summaries.Summary) -> None:
        result = summary.lint()
        self.assertTrue(result.ok, result.problems)
        self.assertTrue(alert_lint.load_emitted(summary.emitted_document()).check(summary.text).ok)
        self.assertEqual(list(alert_lint.EMITTED_DOCUMENT_KEYS), sorted(summary.emitted_document(),
                                                                         key=alert_lint.EMITTED_DOCUMENT_KEYS.index))
        self.assertEqual([], order_shaped(summary.text))
        for forbidden in ("EXIT SIGNAL,", "EXIT_SIGNAL", "your plan: close,", "pri" + "ce:", "@"):
            if forbidden == "EXIT SIGNAL,":
                self.assertNotIn("| EXIT SIGNAL,", summary.text)
            else:
                self.assertNotIn(forbidden, summary.text)


class MorningSheetTest(LintedTest):
    def test_the_whole_sheet(self) -> None:
        summary = summaries.morning_sheet(morning())
        self.assertEqual([
            "### MORNING DESK SHEET 2026-11-03",
            "",
            "**Desk status**",
            "- **Doctor** PASS, 1 warning: key_source (quick check at 12:31 UTC)",
            "- **positions.csv** CLEAN, 0 proposed edits (`csv_recon.py --diff` lists them)",
            "- **Open signals** 0",
            "- **Expiry docket** 0 contracts due",
            "- **Last night's levels**",
            "  - AG-20261020-01 1 resting-eligible of 3 price levels",
            "  - AG-20261022-02 1 resting-eligible of 5 price levels",
            "  - AG-20261028-03 0 resting-eligible of 3 price levels",
            "",
            "*read from state/doctor.json, data/positions/2026-11-03.recon.json, "
            "reports/expiry/2026-11-03.week.json, levels/2026-11-02.json*",
            "",
            FOOTER_2059_PREVIOUS,
        ], summary.text.split("\n"))
        self.assertEqual(summaries.MODE_MORNING, summary.mode)
        self.assertEqual(0, summary.exit_code)
        self.assertEqual([], summary.unavailable)
        self.assertEqual(contract.parse_utc_instant("2026-11-02T20:59:55Z"), summary.as_of)
        self.assertLinted(summary)

    def test_every_unavailable_form(self) -> None:
        inputs = morning(
            doctor=made(summaries.LABEL_DOCTOR, DOCTOR_PATH, None, status="missing"),
            recon=made(summaries.LABEL_RECON, RECON_PATH, None, status="missing", older=datetime.date(2026, 11, 2)),
            docket=made(summaries.LABEL_DOCKET, DOCKET_PATH, None, status="corrupt"),
            levels=made(summaries.LABEL_LEVELS, LEVELS_PATH, {"et_date": "2026-11-02"}, datetime.date(2026, 11, 2)))
        summary = summaries.morning_sheet(inputs)
        lines = summary.text.split("\n")
        self.assertEqual([
            "### MORNING DESK SHEET 2026-11-03",
            "",
            "**Desk status**",
            "- UNAVAILABLE: doctor result state/doctor.json missing",
            "- UNAVAILABLE: reconciliation is from 2026-11-02 (upstream late)",
            "- **Open signals** 0",
            "- UNAVAILABLE: expiry docket reports/expiry/2026-11-03.week.json cannot be read (moved aside)",
            "- UNAVAILABLE: levels sheet levels/2026-11-02.json cannot be read (not the record the desk writes)",
            "",
        ], lines[:-1])
        self.assertEqual(alert_lint.footer_line(MORNING_NOW), lines[-1], "no record read: the footer is the run's")
        self.assertEqual(20, summary.exit_code)
        self.assertEqual([summaries.LABEL_DOCTOR, summaries.LABEL_RECON, summaries.LABEL_DOCKET,
                          summaries.LABEL_LEVELS], [item.label for item in summary.unavailable])
        self.assertLinted(summary)

    def test_an_internal_date_that_differs_is_upstream_late(self) -> None:
        cases = (
            (summaries.LABEL_DOCTOR, DOCTOR_PATH, doctor_record(run_utc="2026-11-02T21:00:00+00:00"), DAY,
             "UNAVAILABLE: doctor result is from 2026-11-02 (upstream late)"),
            (summaries.LABEL_RECON, RECON_PATH, recon_record(et_date="2026-11-02"), DAY,
             "UNAVAILABLE: reconciliation is from 2026-11-02 (upstream late)"),
            (summaries.LABEL_DOCKET, DOCKET_PATH, docket_record(today="2026-10-30"), DAY,
             "UNAVAILABLE: expiry docket is from 2026-10-30 (upstream late)"),
            (summaries.LABEL_LEVELS, LEVELS_PATH, levels_record(et_date="2026-10-30"), datetime.date(2026, 11, 2),
             "UNAVAILABLE: levels sheet is from 2026-10-30 (upstream late)"),
        )
        for label, path, raw, expected, line in cases:
            item = made(label, path, raw, expected)
            self.assertEqual(summaries.ARTIFACT_LATE, item.status, label)
            self.assertTrue(item.present, label)
            self.assertEqual(line, item.line(), label)

    def test_a_record_of_another_shape_is_invalid(self) -> None:
        cases = (
            (summaries.LABEL_DOCTOR, DOCTOR_PATH, dict(doctor_record(), mode="selftest")),
            (summaries.LABEL_DOCTOR, DOCTOR_PATH, dict(doctor_record(), run_utc="yesterday")),
            (summaries.LABEL_RECON, RECON_PATH, {"payload": {"verdict": "CLEAN"}}),
            (summaries.LABEL_RECON, RECON_PATH, dict(recon_record(), payload=dict(recon_record()["payload"],
                                                                               verdict="GREAT"))),
            (summaries.LABEL_DOCKET, DOCKET_PATH, {"generated_at": "2026-11-03T12:31:00+00:00", "payload": []}),
            (summaries.LABEL_LEVELS, LEVELS_PATH, {"et_date": "2026-11-02", "groups": [{"group_id": "x"}]}),
            (summaries.LABEL_BOOK, BOOK_PATH, {"generated_at": "2026-11-03T21:35:00+00:00"}),
            (summaries.LABEL_RECON, RECON_PATH, ["not", "an", "object"]),
        )
        for label, path, raw in cases:
            item = made(label, path, raw)
            self.assertEqual(summaries.ARTIFACT_INVALID, item.status, (label, raw))
            self.assertIn("cannot be read (not the record the desk writes)", item.line())

    def test_the_doctor_line_names_warnings_and_failures_never_reasons(self) -> None:
        cases = (
            (doctor_record(statuses=(("interpreter", "PASS"),)), "PASS, 0 warnings"),
            (doctor_record(mode="full", statuses=(("release_pin", "WARN"), ("connector_readback", "WARN"))),
             "PASS, 2 warnings: release_pin, connector_readback"),
            (doctor_record(verdict="FAIL", statuses=(("key_source", "FAIL"), ("heartbeats", "WARN"))),
             "FAIL, 1 warning: heartbeats; 1 failure: key_source"),
        )
        for raw, tail in cases:
            summary = summaries.morning_sheet(morning(doctor=made(summaries.LABEL_DOCTOR, DOCTOR_PATH, raw)))
            line = summary.lines[3]
            self.assertEqual("- **Doctor** %s (%s check at 12:31 UTC)" % (tail, raw["mode"]), line)
            self.assertNotIn("reason", summary.text)
            self.assertEqual(0, summary.exit_code, "a doctor FAIL is the doctor's own exit 20, not an UNAVAILABLE")
            self.assertLinted(summary)

    def test_the_recon_line_reads_only_the_verdict_and_the_counts(self) -> None:
        cases = ((recon_record("EDITS_PENDING", edits=1),
                  "EDITS_PENDING, 1 proposed edit (`csv_recon.py --diff` lists it)"),
                 (recon_record("UNAVAILABLE", edits=2, unavailable=3),
                  "UNAVAILABLE, 2 proposed edits (`csv_recon.py --diff` lists them), 3 unavailable items "
                  "(`csv_recon.py` shows them)"))
        for raw, tail in cases:
            summary = summaries.morning_sheet(morning(recon=made(summaries.LABEL_RECON, RECON_PATH, raw)))
            self.assertEqual("- **positions.csv** " + tail, summary.lines[4])
            self.assertNotIn("the owner typed", summary.text)
            self.assertLinted(summary)

    def test_the_docket_lines_and_the_early_close_line(self) -> None:
        today = datetime.date(2026, 11, 27)
        act = "act by Fri 2026-11-27 13:00 ET (18:00 UTC) [today]; the session close: a short option is closed only while the market trades"
        legs = [docket_leg(WMT, "WMT", "covered_call", "2026-11-27", 0, act, "WMT261127C00105000", "short", "2"),
                docket_leg(None, "NVDA", "short_call", "2026-11-27", 0, act, "NVDA261127C00150000", "short", "2",
                           group_id="ROW-6"),
                docket_leg(SPY, "SPY", "protective_put", "2026-12-04", 5,
                           "act by Fri 2026-12-04 16:30 ET (21:30 UTC)", "SPY261204P00620000", "long", "1")]
        docket = made(summaries.LABEL_DOCKET, "reports/expiry/2026-11-27.week.json",
                      docket_record(legs, today="2026-11-27", unavailable=1,
                                    generated_at="2026-11-27T12:31:00+00:00"), today)
        summary = summaries.morning_sheet(morning(
            et_date=today, now=contract.parse_utc_instant("2026-11-27T12:35:00Z"), docket=docket,
            doctor=made(summaries.LABEL_DOCTOR, DOCTOR_PATH, doctor_record(run_utc="2026-11-27T12:31:00+00:00"), today),
            recon=made(summaries.LABEL_RECON, "data/positions/2026-11-27.recon.json",
                       recon_record(et_date="2026-11-27", generated_at="2026-11-27T12:31:00+00:00"), today),
            levels=made(summaries.LABEL_LEVELS, "levels/2026-11-25.json", levels_record("2026-11-25"),
                        datetime.date(2026, 11, 25)),
            early_close_et=datetime.time(13, 0)))
        at = summary.lines.index("- **Expiry docket** 3 contracts due, 1 unavailable (`expiry_checklist.py --week` "
                                 "shows why)")
        self.assertEqual([
            "  - AG-20261020-01 WMT261127C00105000 short 2 | WMT covered call | last trading day 2026-11-27 (0 trading "
            "days) | " + act,
            "  - ROW-6 NVDA261127C00150000 short 2 | NVDA short call | last trading day 2026-11-27 (0 trading days) | "
            + act,
            "  - AG-20261028-03 SPY261204P00620000 long 1 | SPY protective put | last trading day 2026-12-04 (5 trading "
            "days) | act by Fri 2026-12-04 16:30 ET (21:30 UTC)",
            "- **Early close** today at 13:00 ET: 2 contracts on their last trading day; " + act,
        ], summary.lines[at + 1:at + 5])
        self.assertLinted(summary)

    def test_each_docket_line_names_its_contract_and_side(self) -> None:
        # Review L1-1 (FAI-689): the two legs of a vertical act by different times (a short leg by the session close,
        # a long one by the broker cutoff), so each docket line names its own contract, side and size, as the
        # checklist's lines do. The act-by texts are what expiry_checklist.py --week prints for a SPY 600/590 put debit
        # vertical on its last trading day (fixture copy, 2026-11-20 12:31 UTC).
        today = datetime.date(2026, 11, 20)
        trade = "AG-20261101-05"
        long_act = "act by Fri 2026-11-20 16:30 ET (21:30 UTC) [today]"
        short_act = ("act by Fri 2026-11-20 16:00 ET (21:00 UTC) [today]; the session close: a short option is closed "
                     "only while the market trades")
        legs = [docket_leg(trade, "SPY", "put_debit_vertical", "2026-11-20", 0, long_act, "SPY261120P00600000", "long",
                           "1"),
                docket_leg(trade, "SPY", "put_debit_vertical", "2026-11-20", 0, short_act, "SPY261120P00590000", "short",
                           "1")]
        docket = made(summaries.LABEL_DOCKET, "reports/expiry/2026-11-20.week.json",
                      docket_record(legs, today="2026-11-20", generated_at="2026-11-20T12:31:00+00:00"), today)
        summary = summaries.morning_sheet(morning(
            et_date=today, now=contract.parse_utc_instant("2026-11-20T12:35:00Z"), docket=docket,
            doctor=made(summaries.LABEL_DOCTOR, DOCTOR_PATH, doctor_record(run_utc="2026-11-20T12:31:00+00:00"), today),
            recon=made(summaries.LABEL_RECON, "data/positions/2026-11-20.recon.json",
                       recon_record(et_date="2026-11-20", generated_at="2026-11-20T12:31:00+00:00"), today),
            levels=made(summaries.LABEL_LEVELS, "levels/2026-11-19.json", levels_record("2026-11-19"),
                        datetime.date(2026, 11, 19))))
        at = summary.lines.index("- **Expiry docket** 2 contracts due")
        self.assertEqual([
            "  - AG-20261101-05 SPY261120P00600000 long 1 | SPY put debit vertical | last trading day 2026-11-20 "
            "(0 trading days) | " + long_act,
            "  - AG-20261101-05 SPY261120P00590000 short 1 | SPY put debit vertical | last trading day 2026-11-20 "
            "(0 trading days) | " + short_act,
        ], summary.lines[at + 1:at + 3])
        self.assertLinted(summary)
        evening = summaries.eod_brief(brief(docket=docket))
        self.assertIn(summary.lines[at + 1], evening.lines, "the EOD brief prints the same docket lines")
        self.assertIn(summary.lines[at + 2], evening.lines)

    def test_a_docket_leg_without_its_contract_side_or_size_is_invalid(self) -> None:
        good = docket_leg(WMT, "WMT", "covered_call", "2026-11-20", 0, "act by Fri 2026-11-20 16:00 ET (21:00 UTC)",
                          "WMT261120C00105000", "short", "2")
        self.assertEqual(summaries.ARTIFACT_OK, made(summaries.LABEL_DOCKET, DOCKET_PATH, docket_record([good])).status)
        for key, value in (("symbol", None), ("symbol", ""), ("side", None), ("side", "flat"), ("contracts", None),
                           ("contracts", "two"), ("contracts", 2)):
            leg = dict(good)
            if value is None:
                del leg[key]
            else:
                leg[key] = value
            item = made(summaries.LABEL_DOCKET, DOCKET_PATH, docket_record([leg]))
            self.assertEqual(summaries.ARTIFACT_INVALID, item.status, (key, value))

    def test_the_early_close_line_without_contracts_or_without_a_docket(self) -> None:
        one = morning(early_close_et=datetime.time(13, 0))
        self.assertIn("- **Early close** today at 13:00 ET: no contract on its last trading day",
                      summaries.morning_sheet(one).lines)
        gone = morning(early_close_et=datetime.time(13, 0),
                       docket=made(summaries.LABEL_DOCKET, DOCKET_PATH, None, status="missing"))
        summary = summaries.morning_sheet(gone)
        self.assertIn("- **Early close** today at 13:00 ET: the expiry docket is unavailable, so the contracts on their "
                      "last trading day are unknown", summary.lines)
        self.assertLinted(summary)
        self.assertFalse([line for line in summaries.morning_sheet(morning()).lines if "early close" in line])

    def test_the_levels_line_of_a_group_without_a_trade_id_and_of_no_group(self) -> None:
        raw = levels_record(groups=((None, 1, 1), (WMT, 0, 0)))
        summary = summaries.morning_sheet(morning(levels=made(summaries.LABEL_LEVELS, LEVELS_PATH, raw,
                                                               datetime.date(2026, 11, 2))))
        at = summary.lines.index("- **Last night's levels**")
        self.assertEqual(["  - ROW-1 1 resting-eligible of 1 price level",
                          "  - AG-20261020-01 0 resting-eligible of 0 price levels"], summary.lines[at + 1:at + 3])
        none = summaries.morning_sheet(morning(levels=made(summaries.LABEL_LEVELS, LEVELS_PATH, levels_record(groups=()),
                                                            datetime.date(2026, 11, 2))))
        self.assertIn("- **Last night's levels** no open group", none.lines)
        self.assertLinted(summary)
        self.assertLinted(none)


class OpenSignalLinesTest(LintedTest):
    def test_every_ladder_position(self) -> None:
        items = [
            open_signal("SIG-20261103-001", WMT, "covered_call", "EXIT_SIGNAL", "close", 1),
            open_signal("SIG-20261103-002", NVDA, "covered_call", "EXIT_SIGNAL", "roll_review", 2),
            open_signal("SIG-20261103-003", SPY, "protective_put", "EXIT_SIGNAL", "hold_review", 4),
            open_signal("SIG-20261103-004", "portfolio", "portfolio", "WATCH", "hold_review", 1),
            open_signal("SIG-20261103-005", "ROW-9", "cash_secured_put", "UNAVAILABLE", None, 3),
            open_signal("SIG-20261103-006", "AG-20261028-07", "short_call", "WATCH", "close", 2,
                        snoozed="2026-11-04T14:00:00+00:00"),
        ]
        summary = summaries.morning_sheet(morning(open_signals=items))
        self.assertEqual([
            "- **Open signals** 6",
            "  - SIG-20261103-001 | AG-20261020-01 covered call | EXIT SIGNAL | your plan: close | first posted "
            "2026-11-03 19:50 UTC | posted 1 of 4 times; next re-post 1 run after the first post | not acknowledged",
            "  - SIG-20261103-002 | AG-20261022-02 covered call | EXIT SIGNAL | your plan: review a roll | first "
            "posted 2026-11-03 19:50 UTC | posted 2 of 4 times; next re-post 3 runs after the first post | not "
            "acknowledged",
            "  - SIG-20261103-003 | AG-20261028-03 protective put | EXIT SIGNAL | your plan: review and decide | "
            "first posted 2026-11-03 19:50 UTC | posted 4 of 4 times; the re-post ladder is done | not acknowledged",
            "  - SIG-20261103-004 | portfolio all open groups | WATCH | your plan: review and decide | first posted "
            "2026-11-03 19:50 UTC | posted 1 time; no re-post ladder for WATCH | not acknowledged",
            "  - SIG-20261103-005 | ROW-9 cash-secured put | UNAVAILABLE | first posted 2026-11-03 19:50 UTC | posted "
            "3 times; re-posted on every run until acknowledged | not acknowledged",
            "  - SIG-20261103-006 | AG-20261028-07 short call | WATCH | your plan: close | first posted 2026-11-03 19:50 "
            "UTC | posted 2 times; no re-post ladder for WATCH | snoozed until 2026-11-04 14:00 UTC",
        ], summary.lines[summary.lines.index("- **Open signals** 6"):][:7])
        self.assertLinted(summary)
        self.assertEqual(0, summary.exit_code, "an UNAVAILABLE signal is a journal record, not an UNAVAILABLE line")


class EodBriefTest(LintedTest):
    def test_the_whole_brief(self) -> None:
        items = [open_signal("SIG-20261103-001", WMT, "covered_call", "EXIT_SIGNAL", "close", 1)]
        records = [{"signal_id": "SIG-20261103-001", "verdict": "EXIT_SIGNAL", "posted_at": "2026-11-03T19:50:00+00:00"},
                   {"signal_id": "SIG-20261103-002", "verdict": "WATCH", "posted_at": "2026-11-03T20:35:00+00:00"},
                   {"signal_id": "SIG-20261102-001", "verdict": "WATCH", "posted_at": "2026-11-02T20:35:00+00:00"},
                   {"signal_id": "SIG-20261103-003", "verdict": "UNAVAILABLE", "posted_at": "2026-11-04T04:30:00+00:00"}]
        summary = summaries.eod_brief(brief(
            open_signals=items, signal_records=records,
            recon=made(summaries.LABEL_RECON, RECON_PATH, recon_record("EDITS_PENDING", edits=2,
                                                                       generated_at="2026-11-03T21:36:00+00:00")),
            config_changes=[{"name": "desk_toml", "old": None, "new": "a" * 64},
                            {"name": "rulebook", "old": "b" * 64, "new": "c" * 64}],
            calendar_findings=1))
        self.assertEqual([
            "### EOD DESK BRIEF 2026-11-03",
            "",
            "**P/L**",
            "- \U0001F7E2 **Total** +$4,522.00 | +$4,459.00 at liquidation",
            "  - *open positions +$4,097.00, closed this year +$425.00 (today $0.00, this month $0.00)*",
            "- \U0001F7E2 **Today** +$400.00",
            "",
            "**By trade**",
            "- \U0001F7E2 **WMT covered call** +$1,145.00 | today +$34.00",
            "  - *AG-20261020-01 | since entry | +$1,124.00 at liquidation*",
            "- \U0001F7E2 **NVDA covered call** +$1,515.00 | today +$194.00",
            "  - *AG-20261022-02 | since entry | +$1,490.00 at liquidation*",
            "- \U0001F7E2 **SPY protective put** +$1,437.00 | today +$172.00",
            "  - *AG-20261028-03 | since entry | +$1,420.00 at liquidation*",
            "",
            "**Desk status**",
            "- **Signals today** 3 (1 EXIT SIGNAL, 1 WATCH, 1 UNAVAILABLE)",
            "- **Open signals** 1",
            "  - SIG-20261103-001 | AG-20261020-01 covered call | EXIT SIGNAL | your plan: close | first posted "
            "2026-11-03 19:50 UTC | posted 1 of 4 times; next re-post 1 run after the first post | not acknowledged",
            "- **positions.csv** EDITS_PENDING, 2 proposed edits (`csv_recon.py --diff` lists them)",
            "- **Expiry docket** 0 contracts due",
            "- **Routines** 3 of 4 ran today",
            "  - late: book-check 10:05 ET (31 min late)",
            "  - missed: book-check 12:05 ET",
            "",
            "**To do**",
            "- SIG-20261103-001 not acknowledged (`ack.py show SIG-20261103-001`)",
            "- positions.csv: 2 proposed edits pending (`csv_recon.py --diff` lists them)",
            "- CONFIG CHANGED pending: desk.toml, adopted rulebook (acknowledge with ack.py config --owner-tapped)",
            "- research/calendar.toml: 1 finding (`calendar_check.py` lists it)",
            "",
            "*read from data/book/2026-11-03.json, data/positions/2026-11-03.recon.json, "
            "reports/expiry/2026-11-03.week.json*",
            "",
            "deterministic rules you adopted · posted by an AI assistant · not personalized investment advice · "
            "data delayed 15 min, as of 19:31 UTC",
        ], summary.text.split("\n"))
        self.assertEqual(summaries.MODE_BRIEF, summary.mode)
        self.assertEqual(0, summary.exit_code)
        self.assertEqual(contract.parse_utc_instant("2026-11-03T19:31:12Z"), summary.as_of,
                         "the footer is the book's newest quote as-of (alerts.book_as_of)")
        self.assertLinted(summary)

    def test_totals_come_from_pl_figures_and_a_group_day_is_never_partial(self) -> None:
        document = book_document(unrealized_pl_liq=Decimal("1.23"), unrealized_pl_mid=Decimal("4.56"),
                                 day_pl=Decimal("7.89"), realized_pl_ytd=Decimal("10.11"))
        summary = summaries.eod_brief(brief(book=book_artifact(document)))
        self.assertEqual([
            "- \U0001F7E2 **Total** +$14.67 | +$11.34 at liquidation",
            "  - *open positions +$4.56, closed this year +$10.11 (today $0.00, this month $0.00)*",
            "- \U0001F7E2 **Today** +$7.89",
        ], summary.lines[3:6])
        self.assertIn("**WMT covered call** +$1,145.00 | today +$34.00", summary.text,
                      "pl_figures adds the WMT group's two complete leg day P/Ls (84.00 - 50.00), in Decimal")
        document["legs"][1]["day_pl"] = None
        partial = summaries.eod_brief(brief(book=book_artifact(document)))
        self.assertIn("**WMT covered call** +$1,145.00 | today unavailable", partial.text)
        self.assertNotIn("$84.00", partial.text, "one leg's day P/L is never shown as the group's")
        self.assertLinted(summary)

    def test_a_null_prints_unavailable_and_a_null_day_prints_the_books_warning(self) -> None:
        document = book_document(unrealized_pl_liq=None, unrealized_pl_mid=None, day_pl=None)
        document["warnings"] = ["NVDA excluded from the portfolio totals: x",
                                "day P/L unavailable: no snapshot for 2026-11-02 (the desk's first day?)"]
        document["groups"][1].update(unrealized_pl_liq=None, unrealized_pl_mid=None, data_quality="unavailable")
        document["legs"][3]["day_pl"] = None
        summary = summaries.eod_brief(brief(book=book_artifact(document)))
        self.assertEqual([
            "**P/L**",
            "- \U0001F7E1 **Total** unavailable",
            "  - *open positions unavailable, closed this year +$425.00 (today $0.00, this month $0.00)*",
            "- \U0001F7E1 **Today** unavailable: no snapshot for 2026-11-02 (the desk's first day?)",
        ], summary.lines[2:6])
        self.assertEqual([
            "- \U0001F7E1 **NVDA covered call** unavailable: no usable quote",
            "  - *AG-20261022-02*",
        ],
                         summary.lines[10:12])
        document["legs"][1]["day_pl"] = None
        self.assertTrue(summaries.eod_brief(brief(book=book_artifact(document))).lines[8].endswith("| today unavailable"),
                        "a group with an open leg that has no day P/L prints no partial sum")
        document["warnings"] = []
        self.assertEqual("- \U0001F7E1 **Today** unavailable",
                         summaries.eod_brief(brief(book=book_artifact(document))).lines[5])
        self.assertLinted(summary)

    def test_one_line_per_open_group_quarantined_groups_in_their_own_words(self) -> None:
        document = book_document()
        document["groups"].append({"group_id": "ROW-10", "trade_id": None, "underlying": "TSLA", "structure": "short_call",
                                   "legs": [10], "unrealized_pl_liq": Decimal("-12.00"),
                                   "unrealized_pl_mid": Decimal("-10.00"), "data_quality": "ok"})
        document["legs"].append(book_leg(10, "ROW-10", day_pl=Decimal("3.00")))
        document["groups"].append({"group_id": "AG-20261030-05", "trade_id": "AG-20261030-05", "underlying": "AAPL",
                                   "structure": "custom", "legs": [11], "unrealized_pl_liq": None,
                                   "unrealized_pl_mid": None, "data_quality": "unavailable"})
        document["legs"].append(book_leg(11, "AG-20261030-05", day_pl=None))
        document["portfolio"]["quarantined_groups"] = [
            {"group_id": "AG-20261030-05", "reasons": ["account mismatch: rows 11 and 12 name different accounts"]},
            {"group_id": "ROW-12", "reasons": ["row 12 status: 'opne' is not a status"]}]
        summary = summaries.eod_brief(brief(book=book_artifact(document)))
        self.assertEqual([
            "- \U0001F7E2 **Today** +$400.00",
            "",
            "**By trade**",
        ], summary.lines[5:8])
        self.assertNotIn("AG-20261001-04", summary.text, "a group whose rows are all closed has no line")
        self.assertNotIn("opne", summary.text)
        self.assertLinted(summary)

    def test_missing_book_and_recon_and_an_absent_docket(self) -> None:
        summary = summaries.eod_brief(brief(
            book=made(summaries.LABEL_BOOK, BOOK_PATH, None, status="missing"),
            recon=made(summaries.LABEL_RECON, RECON_PATH, None, status="missing"),
            docket=made(summaries.LABEL_DOCKET, DOCKET_PATH, None, status="missing", older=datetime.date(2026, 10, 30)),
            audit=audit([])))
        self.assertEqual([
            "### EOD DESK BRIEF 2026-11-03",
            "",
            "**P/L**",
            "- UNAVAILABLE: book snapshot data/book/2026-11-03.json missing",
            "",
            "**Desk status**",
            "- **Signals today** 0 (0 EXIT SIGNAL, 0 WATCH, 0 UNAVAILABLE)",
            "- **Open signals** 0",
            "- UNAVAILABLE: reconciliation data/positions/2026-11-03.recon.json missing",
            "- **Expiry docket** none recorded today (reports/expiry/2026-11-03.week.json missing)",
            "- **Routines** 0 of 0 ran today, none late or missed",
            "",
            "**To do** nothing pending",
            "",
            "deterministic rules you adopted · posted by an AI assistant · not personalized investment advice · "
            "data delayed 15 min, as of 22:12 UTC",
        ], summary.text.split("\n"))
        self.assertEqual(20, summary.exit_code)
        self.assertEqual([summaries.LABEL_BOOK, summaries.LABEL_RECON], [item.label for item in summary.unavailable])
        self.assertLinted(summary)

    def test_a_present_docket_that_cannot_be_used_is_unavailable_in_the_brief(self) -> None:
        late = made(summaries.LABEL_DOCKET, DOCKET_PATH, docket_record(today="2026-11-02"))
        summary = summaries.eod_brief(brief(docket=late))
        self.assertIn("- UNAVAILABLE: expiry docket is from 2026-11-02 (upstream late)", summary.lines)
        self.assertEqual(20, summary.exit_code)

    def test_the_routines_line_names_pending_slots_and_a_manifest_problem(self) -> None:
        pending = audit([slot("preopen", "07:30", True, 0), slot("eod", "16:35")])
        summary = summaries.eod_brief(brief(audit=pending))
        at = summary.lines.index("- **Routines** 1 of 2 ran today, none late or missed")
        self.assertEqual(["  - pending: eod 16:35 ET", ""], summary.lines[at + 1:at + 3])
        problem = summaries.Artifact(summaries.LABEL_MANIFEST, "routines/manifest.toml", summaries.ARTIFACT_INVALID,
                                     why="routine_audit.py --budget shows why")
        broken = summaries.eod_brief(brief(audit=None, audit_problem=problem))
        self.assertIn("- UNAVAILABLE: routine manifest routines/manifest.toml cannot be read (routine_audit.py --budget "
                      "shows why)", broken.lines)
        self.assertEqual(20, broken.exit_code)
        self.assertLinted(summary)
        self.assertLinted(broken)

    def test_partial_journal_notes(self) -> None:
        note = summaries.PARTIAL_JOURNAL_LINE % "journal/signals.jsonl"
        self.assertEqual("journal/signals.jsonl has lines that do not parse: they were skipped (the file is kept as it is)",
                         note)
        summary = summaries.eod_brief(brief(notes=[note]))
        at = summary.lines.index("- " + note)
        self.assertEqual("", summary.lines[at + 1], "the last item of the desk status, before the to-do list")
        self.assertLinted(summary)
        morning_summary = summaries.morning_sheet(morning(notes=[note]))
        self.assertIn("- " + note, morning_summary.lines)


class ReviewFixTest(LintedTest):
    """The footer at the records' data instants, the totals' exclusions, the day's incident files."""

    STAGED_AS_OF = "2026-11-02T20:59:55+00:00"

    def test_the_morning_footer_is_a_data_instant_never_a_run_instant(self) -> None:
        staged = morning(
            recon=made(summaries.LABEL_RECON, RECON_PATH, recon_record(as_of=self.STAGED_AS_OF)),
            docket=made(summaries.LABEL_DOCKET, DOCKET_PATH, docket_record(as_of=self.STAGED_AS_OF)),
            levels=made(summaries.LABEL_LEVELS, LEVELS_PATH, levels_record(as_of=self.STAGED_AS_OF),
                        datetime.date(2026, 11, 2)))
        summary = summaries.morning_sheet(staged)
        self.assertEqual(contract.parse_utc_instant("2026-11-02T20:59:55Z"), summary.as_of)
        self.assertEqual(alert_lint.footer_line(summary.as_of), summary.text.split("\n")[-1])
        self.assertTrue(summary.text.endswith("as of 20:59 UTC"))
        self.assertLinted(summary)
        # the doctor's run instant is not market data: with every other record missing the footer is the run's
        bare = summaries.morning_sheet(morning(
            recon=made(summaries.LABEL_RECON, RECON_PATH, None, status="missing"),
            docket=made(summaries.LABEL_DOCKET, DOCKET_PATH, None, status="missing"),
            levels=made(summaries.LABEL_LEVELS, LEVELS_PATH, None, datetime.date(2026, 11, 2), status="missing")))
        self.assertEqual(MORNING_NOW, bare.as_of)

    def test_the_footer_is_the_oldest_data_instant(self) -> None:
        # the sheet never claims fresher data than its stalest record (integration decision 3)
        staged = morning(
            recon=made(summaries.LABEL_RECON, RECON_PATH, recon_record(as_of="2026-11-02T20:59:55+00:00")),
            docket=made(summaries.LABEL_DOCKET, DOCKET_PATH, docket_record(as_of="2026-11-02T21:10:00+00:00")),
            levels=made(summaries.LABEL_LEVELS, LEVELS_PATH, levels_record(as_of="2026-11-02T20:58:30+00:00"),
                        datetime.date(2026, 11, 2)))
        summary = summaries.morning_sheet(staged)
        self.assertEqual(contract.parse_utc_instant("2026-11-02T20:58:30Z"), summary.as_of)
        self.assertTrue(summary.text.endswith("as of 20:58 UTC"), summary.text.split("\n")[-1])
        self.assertLinted(summary)
        no_book = summaries.eod_brief(brief(
            book=made(summaries.LABEL_BOOK, BOOK_PATH, None, status="missing"),
            recon=made(summaries.LABEL_RECON, RECON_PATH, recon_record(as_of="2026-11-03T20:59:50+00:00")),
            docket=made(summaries.LABEL_DOCKET, DOCKET_PATH, docket_record(as_of="2026-11-03T21:05:00+00:00"))))
        self.assertEqual(contract.parse_utc_instant("2026-11-03T20:59:50Z"), no_book.as_of)

    def test_the_brief_footer_without_a_snapshot_is_a_data_instant(self) -> None:
        summary = summaries.eod_brief(brief(
            book=made(summaries.LABEL_BOOK, BOOK_PATH, None, status="missing"),
            recon=made(summaries.LABEL_RECON, RECON_PATH, recon_record(generated_at="2026-11-03T21:36:00+00:00",
                                                                       as_of="2026-11-03T20:59:50+00:00")),
            docket=made(summaries.LABEL_DOCKET, DOCKET_PATH, None, status="missing")))
        self.assertEqual(contract.parse_utc_instant("2026-11-03T20:59:50Z"), summary.as_of,
                         "the record's data instant, not its generated_at")

    def test_the_pl_line_names_the_groups_its_totals_leave_out(self) -> None:
        document = book_document(unrealized_pl_liq=Decimal("2910.00"), unrealized_pl_mid=Decimal("2952.00"),
                                 day_pl=Decimal("222.00"))
        document["groups"][0].update(unrealized_pl_liq=None, unrealized_pl_mid=None, data_quality="unavailable")
        document["warnings"] = [
            "AG-20261020-01 excluded from the portfolio totals: account mismatch: Brokerage (margin), Roth IRA",
            "AG-20261022-02 excluded from the day P/L total: NVDA261120C00150000 (row 6) not in the previous snapshot "
            "2026-11-02",
            "NVDA excluded from the portfolio totals: not a group of this snapshot, never named"]
        summary = summaries.eod_brief(brief(book=book_artifact(document)))
        self.assertEqual([
            "- \U0001F7E2 **Total** +$3,377.00 | +$3,335.00 at liquidation",
            "  - *open positions +$2,952.00, closed this year +$425.00 (today $0.00, this month $0.00) | totals "
            "without AG-20261020-01 (data unavailable)*",
            "- \U0001F7E2 **Today** +$222.00",
            "  - *the day total also without AG-20261022-02 (an open leg has no day P/L)*",
        ], summary.lines[3:7])
        self.assertNotIn("Roth IRA", summary.text, "a reason may carry a cell the owner typed: only ids are printed")
        self.assertLinted(summary)
        document["warnings"] = document["warnings"][:1]
        document["portfolio"]["day_pl"] = None
        lines = summaries.eod_brief(brief(book=book_artifact(document))).lines
        self.assertEqual([
            "  - *open positions +$2,952.00, closed this year +$425.00 (today $0.00, this month $0.00) | totals "
            "without AG-20261020-01 (data unavailable)*",
            "- \U0001F7E1 **Today** unavailable",
            "",
        ], lines[4:7])

    def test_the_brief_names_the_day_s_incident_files(self) -> None:
        incidents = [("book-check", "trigger_levels"), ("final-call", "expiry_checklist-final-call withheld"),
                     (None, "dirty checkout"), (None, None)]
        summary = summaries.eod_brief(brief(incidents=incidents))
        self.assertIn("- **Incidents today** 4 (book-check trigger_levels, final-call expiry_checklist-final-call "
                      "withheld, dirty checkout, 1 other file); details in `incidents/`", summary.lines)
        self.assertEqual(["**To do**", "- `incidents/`: 4 files today"], summary.lines[-4:-2])
        self.assertEqual(0, summary.exit_code, "an incident is named, it is not an unavailable record")
        self.assertLinted(summary)
        quiet = summaries.eod_brief(brief())
        self.assertFalse(any("incidents today" in line for line in quiet.lines))
        self.assertNotIn("incidents/", quiet.text)


class FooterInstantTest(unittest.TestCase):
    def test_book_then_oldest_record_then_the_run(self) -> None:
        self.assertEqual(contract.parse_utc_instant("2026-11-03T19:31:12Z"), summaries.eod_brief(brief()).as_of)
        no_book = summaries.eod_brief(brief(book=made(summaries.LABEL_BOOK, BOOK_PATH, None, status="missing")))
        self.assertEqual(contract.parse_utc_instant("2026-11-03T12:31:00Z"), no_book.as_of, "the older docket")
        self.assertEqual(contract.parse_utc_instant("2026-11-02T20:59:55Z"), summaries.morning_sheet(morning()).as_of,
                         "last night's levels are the oldest data the morning sheet shows")


if __name__ == "__main__":
    unittest.main()
