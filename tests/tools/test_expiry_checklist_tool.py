"""``scripts/expiry_checklist.py`` run in-process against a copied fixture desk root: the week docket,
the T-1 preview and the Final Call sheet with act-by times, the exit codes 0 / 10 / 20 and the
error mapping. Nothing under ``tests/fixtures/`` is ever written."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Sequence, Tuple

import expiry_checklist as tool

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
MONDAY_NOW = "2026-11-16T15:00:00Z"
T1_NOW = "2026-11-19T19:50:00Z"
EXPIRY_NOW = "2026-11-20T19:50:00Z"
POST_EXPIRY_NOW = "2026-11-21T14:30:00Z"
EARLY_CLOSE_NOW = "2026-11-27T17:20:00Z"  # 12:20 ET on the day after Thanksgiving: the 13:00 ET close
EARLY_CLOSE_ROW = ("option,WMT,WMT261127C00105000,-1,,,,Brokerage (margin),AG-20261102-09,2026-11-02,-100.00,,,open,,,,,"
                   "short call into the early close")
WMT_DOCKET = ("AG-20261020-01 WMT261120C00105000 short 2 | covered_call | pm-settled | last trading day Fri 2026-11-20 "
              "(1 trading day) | shares called away if assigned: 200 WMT at 105 (covered by the 200 shares held) | "
              "act by Fri 2026-11-20 16:30 ET (21:30 UTC) [next trading day]")
NVDA_DOCKET = ("AG-20261022-02 NVDA261120C00150000 short 2 | covered_call | pm-settled | last trading day Fri 2026-11-20 "
               "(1 trading day) | shares called away if assigned: 200 NVDA at 150 (covered by the 200 shares held) | "
               "act by Fri 2026-11-20 16:30 ET (21:30 UTC) [next trading day]")
# The fixture calendar's WMT ex-dividend (Tue 2026-11-24) has its last cum-dividend day on Mon 2026-11-23, after the
# Nov-20 call's last trading day: the fixture sets list no dividend tier for the WMT call. The tests that need one
# move the COPY's ex-dividend to Mon 2026-11-23 (cum day Fri 2026-11-20 = the last trading day): the call is graded
# with the boundary note. The checklist indents the tier lines by two spaces and their notes by four.
WMT_NOTED_T1 = ("  NOTED AG-20261020-01 WMT261120C00105000 short 2: ex-dividend 2026-11-23 in 2 trading days (confirmed), "
                "amount 0.2350; extrinsic (bid) 0.09 = bid 1.52 - max(0, 106.43 - 105); in the money; HIGH downgraded to "
                "NOTED: #assign-ok (owner accepted assignment); act by Fri 2026-11-20 16:30 ET (21:30 UTC) [next trading day]")
WMT_NOTED_EXPIRY_DAY = ("  NOTED AG-20261020-01 WMT261120C00105000 short 2: ex-dividend 2026-11-23 in 1 trading day (confirmed), "
                        "amount 0.2350; extrinsic (bid) 0.12 = bid 0.12 - max(0, 104.93 - 105); out of the money; HIGH downgraded "
                        "to NOTED: #assign-ok (owner accepted assignment); act by Fri 2026-11-20 16:30 ET (21:30 UTC) [today]")
BOUNDARY_NOTE = ("ex-dividend 2026-11-23 is the first trading day after the last trading day 2026-11-20: a dividend-capture "
                 "exercise would come at expiration")
GRADE_E_NOTE = "option quote grade E (wide: spread 28.57% > 25%): the bid-side arithmetic stands"


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = tool.main(list(argv))
    return code, buffer.getvalue()


def run_json(argv: Sequence[str]) -> Tuple[int, Dict[str, Any]]:
    code, out = run_tool(argv)
    return code, json.loads(out)


def sha256_of(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-expiry-checklist-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.positions_path = os.path.join(self.root, "positions.csv")
        self.positions_sha = sha256_of(self.positions_path)

    def tearDown(self) -> None:
        if os.path.exists(self.positions_path):
            self.assertEqual(self.positions_sha, sha256_of(self.positions_path), "the tool never writes positions.csv")
        self._tmp.cleanup()

    def argv(self, mode: str, fixture_set: str = None, now: str = REGULAR_NOW, json_mode: bool = True) -> List[str]:
        argv = [mode, "--offline", "--desk-root", self.root, "--now", now]
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

    def rewrite(self, relative: str, old: str, new: str) -> None:
        path = os.path.join(self.root, relative)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))
        if relative == "positions.csv":
            self.positions_sha = sha256_of(self.positions_path)

    def move_wmt_ex_dividend_to_monday(self) -> None:
        """The copy's calendar with the WMT ex-dividend on Mon 2026-11-23: its last cum-dividend day is Fri
        2026-11-20, the Nov-20 call's last trading day, so the call is graded (the boundary case)."""
        self.rewrite("research/calendar.toml", "date = 2026-11-24, amount = 0.2350", "date = 2026-11-23, amount = 0.2350")


class AlwaysOnTest(DeskCopy):
    def test_schema(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual("expiry_checklist", body["tool"])
        payload = body["envelope"]["payload"]
        for key in ("mode", "verdict", "legs", "quote_check", "quarantined", "expired", "week_start", "week_end"):
            self.assertIn(key, payload)

    def test_schema_leg_matches_the_json_leg_key_for_key(self) -> None:
        # Recon finding 6: the schema describes the leg object itself (not a prose string), key for key.
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        described = body["envelope"]["payload"]["legs"][0]
        self.assertIsInstance(described, dict)
        self.assertIn("option_grade", described["dividend"])
        self.move_wmt_ex_dividend_to_monday()  # the fixture geometry lists no dividend block for the WMT call
        code, body = run_json(self.argv("--week", "t1", now=T1_NOW))
        self.assertEqual(10, code, body)
        for leg in body["payload"]["legs"]:
            self.assertEqual(set(described), set(leg), leg["symbol"])
        wmt = body["payload"]["legs"][0]
        self.assertEqual(set(described["dividend"]), set(wmt["dividend"]))
        self.assertEqual(set(described["pin"]), set(wmt["pin"]))

    def test_usage_errors(self) -> None:
        for argv in (["--json", "--offline", "--desk-root", self.root],
                     ["--week", "--tomorrow", "--json", "--offline", "--desk-root", self.root],
                     ["--final-call", "--week", "--json", "--offline", "--desk-root", self.root],
                     ["--week", "--json", "--desk-root", self.root, "--fixture-set", "t1"],
                     ["--week", "--json", "--offline"]):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(argv)
                self.assertEqual(2, ctx.exception.code)


class JourneysTest(DeskCopy):
    def test_week_docket_at_t1(self) -> None:
        code, body = run_json(self.argv("--week", "t1", now=T1_NOW))
        self.assertEqual(10, code, body)
        self.assertEqual(("fired", "tradier:sandbox", 15), (body["status"], body["source"], body["delay_min"]))
        payload = body["payload"]
        self.assertEqual(("week", "fired", 10, "2026-11-19", "2026-11-16", "2026-11-22"),
                         (payload["mode"], payload["verdict"], payload["exit_code"], payload["today"], payload["week_start"],
                          payload["week_end"]))
        self.assertEqual(["WMT261120C00105000", "NVDA261120C00150000"], [leg["symbol"] for leg in payload["legs"]])
        wmt, nvda = payload["legs"]
        self.assertEqual(("pm", "2026-11-20", 1, "2", "short", "AG-20261020-01", "covered_call"),
                         (wmt["settlement"], wmt["last_trading_day"], wmt["dte_trading"], wmt["contracts"], wmt["side"],
                          wmt["trade_id"], wmt["structure"]))
        self.assertEqual("shares called away if assigned: 200 WMT at 105 (covered by the 200 shares held)", wmt["implication"])
        self.assertEqual("shares called away if assigned: 200 NVDA at 150 (covered by the 200 shares held)", nvda["implication"])
        self.assertEqual(("2026-11-20T21:30:00+00:00", "2026-11-20 16:30 ET"), (nvda["act_by_utc"], nvda["act_by_et"]))
        self.assertIsNone(wmt["dividend"], "the WMT ex-dividend 2026-11-24 (cum day Mon 2026-11-23) falls after the call's last "
                                           "trading day Fri 2026-11-20: no dividend tier is listed")
        self.assertEqual(("PIN WATCH", "1.34", "PIN HIGH"), (wmt["pin"]["tier"], wmt["pin"]["distance_pct"], nvda["pin"]["tier"]))
        self.assertEqual((False, True, [], []), (wmt["fired"], nvda["fired"], wmt["unavailable_reasons"], nvda["unavailable_reasons"]))
        self.assertEqual(3, payload["counts"]["open_option_legs"])
        self.assertEqual(1, payload["quote_check"]["requests_made"])
        self.assertEqual(1, len(self.ledger()))
        code, text = run_tool(self.argv("--week", "t1", now=T1_NOW, json_mode=False))
        self.assertEqual(10, code, text)
        lines = text.splitlines()
        self.assertEqual("EXPIRY WEEK 2026-11-16..2026-11-22: 2 contracts due (today Thu 2026-11-19)", lines[0])
        self.assertIn(WMT_DOCKET, lines)
        self.assertIn(NVDA_DOCKET, lines)
        self.assertTrue(any(line.startswith("  PIN WATCH AG-20261020-01 WMT261120C00105000 short 2: 1.34 %") for line in lines))
        self.assertIn("    in the money: automatic exercise or assignment expected at the close of Fri 2026-11-20", lines)
        self.assertTrue(any(line.startswith("  PIN HIGH AG-20261022-02 NVDA261120C00150000") for line in lines))
        self.assertNotIn("ex-dividend", text, "no dividend line on the fixture geometry")
        self.assertNotIn("NOTED", text)
        self.assertEqual("Tradier sandbox, delayed 15 min, as of 19:31 UTC", lines[-1])

    def test_week_docket_at_t1_with_the_ex_dividend_on_the_first_trading_day_after_expiry(self) -> None:
        # Ex-dividend Mon 2026-11-23: the cum day Fri 2026-11-20 is the WMT call's last trading day, so it is graded
        # (extrinsic 0.09 < 0.2350: HIGH, NOTED by #assign-ok) and carries the boundary note.
        self.move_wmt_ex_dividend_to_monday()
        code, body = run_json(self.argv("--week", "t1", now=T1_NOW))
        self.assertEqual(10, code, body)
        wmt, nvda = body["payload"]["legs"]
        self.assertEqual(("NOTED", "HIGH", "2026-11-23", 2, "0.09", [BOUNDARY_NOTE], False),
                         (wmt["dividend"]["tier"], wmt["dividend"]["downgraded_from"], wmt["dividend"]["ex_date"],
                          wmt["dividend"]["trading_days_to_ex"], wmt["dividend"]["extrinsic_bid"], wmt["dividend"]["notes"],
                          wmt["fired"]))
        self.assertEqual(("PIN WATCH", "PIN HIGH"), (wmt["pin"]["tier"], nvda["pin"]["tier"]))
        code, text = run_tool(self.argv("--week", "t1", now=T1_NOW, json_mode=False))
        self.assertEqual(10, code, text)
        lines = text.splitlines()
        self.assertIn(WMT_DOCKET, lines)
        self.assertIn(WMT_NOTED_T1, lines)
        self.assertIn("    note: " + BOUNDARY_NOTE, lines)
        self.assertIn("    in the money: automatic exercise or assignment expected at the close of Fri 2026-11-20", lines)

    def test_t1_preview(self) -> None:
        code, body = run_json(self.argv("--tomorrow", "t1", now=T1_NOW))
        self.assertEqual(10, code, body)
        payload = body["payload"]
        self.assertEqual(("tomorrow", "2026-11-20"), (payload["mode"], payload["next_trading_day"]))
        self.assertEqual(["WMT261120C00105000", "NVDA261120C00150000"], [leg["symbol"] for leg in payload["legs"]])
        self.assertTrue(all(leg["due_tomorrow"] for leg in payload["legs"]))
        code, text = run_tool(self.argv("--tomorrow", "t1", now=T1_NOW, json_mode=False))
        self.assertEqual(10, code)
        self.assertEqual("T-1 PREVIEW for Fri 2026-11-20: 2 contracts on their last trading day (today Thu 2026-11-19)",
                         text.splitlines()[0])
        self.assertIn("act by Fri 2026-11-20 16:30 ET (21:30 UTC) [next trading day]", text)

    def test_final_call_on_t1_has_nothing_due(self) -> None:
        code, body = run_json(self.argv("--final-call", "t1", now=T1_NOW))
        self.assertEqual(0, code, body)
        self.assertEqual(("quiet", []), (body["payload"]["verdict"], body["payload"]["legs"]))
        code, text = run_tool(self.argv("--final-call", "t1", now=T1_NOW, json_mode=False))
        self.assertEqual(0, code)
        self.assertIn("FINAL CALL Thu 2026-11-19: nothing due today", text.splitlines()[0])

    def test_final_call_on_expiry_day(self) -> None:
        code, body = run_json(self.argv("--final-call", "expiry_day", now=EXPIRY_NOW))
        self.assertEqual(10, code, body)
        payload = body["payload"]
        self.assertEqual(("final-call", "fired"), (payload["mode"], payload["verdict"]))
        wmt, nvda = payload["legs"]
        self.assertEqual(("PIN HIGH", "0.07", True), (wmt["pin"]["tier"], wmt["pin"]["distance_pct"], wmt["due_today"]))
        self.assertEqual((None, None), (wmt["dividend"], nvda["dividend"]), "both ex-dividends fall after the calls' last trading day")
        self.assertEqual(("PIN WATCH", "2.17", "in the money: automatic exercise or assignment expected at the close"),
                         (nvda["pin"]["tier"], nvda["pin"]["distance_pct"], nvda["pin"]["line"]))
        self.assertEqual(("2026-11-20T21:30:00+00:00", "2026-11-20 16:30 ET", "21:30 UTC"),
                         (nvda["act_by_utc"], nvda["act_by_et"], nvda["act_by_utc_text"]))
        code, text = run_tool(self.argv("--final-call", "expiry_day", now=EXPIRY_NOW, json_mode=False))
        self.assertEqual(10, code, text)
        lines = text.splitlines()
        self.assertEqual("FINAL CALL Fri 2026-11-20: 2 contracts on their last trading day; act by 16:30 ET (21:30 UTC)", lines[0])
        self.assertTrue(any(line.startswith("  PIN HIGH AG-20261020-01 WMT261120C00105000 short 2: 0.07 %") for line in lines))
        self.assertTrue(any(line.startswith("  PIN WATCH AG-20261022-02 NVDA261120C00150000 short 2: 2.17 %") for line in lines))
        self.assertIn("    in the money: automatic exercise or assignment expected at the close", lines)
        self.assertTrue(any("| last trading day Fri 2026-11-20 (today) |" in line for line in lines))
        self.assertTrue(any("act by Fri 2026-11-20 16:30 ET (21:30 UTC) [today]" in line for line in lines))
        self.assertEqual("Tradier sandbox, delayed 15 min, as of 19:31 UTC", lines[-1])

    def test_final_call_on_expiry_day_with_the_ex_dividend_on_the_next_trading_day(self) -> None:
        # Ex-dividend Mon 2026-11-23 seen on Fri 2026-11-20: the cum day is today, the last trading day; the grade-E
        # quote (wide) still has a usable bid: extrinsic 0.12 < 0.2350 is HIGH, NOTED by #assign-ok, two notes.
        self.move_wmt_ex_dividend_to_monday()
        code, body = run_json(self.argv("--final-call", "expiry_day", now=EXPIRY_NOW))
        self.assertEqual(10, code, body)
        wmt, nvda = body["payload"]["legs"]
        self.assertEqual(("NOTED", "HIGH", "2026-11-23", 1, "0.12", "E", [BOUNDARY_NOTE, GRADE_E_NOTE]),
                         (wmt["dividend"]["tier"], wmt["dividend"]["downgraded_from"], wmt["dividend"]["ex_date"],
                          wmt["dividend"]["trading_days_to_ex"], wmt["dividend"]["extrinsic_bid"], wmt["dividend"]["option_grade"],
                          wmt["dividend"]["notes"]))
        self.assertEqual(("PIN HIGH", True), (wmt["pin"]["tier"], wmt["fired"]))
        self.assertIsNone(nvda["dividend"], "the NVDA ex-dividend 2026-12-03 is beyond the Nov-20 call's life")
        code, text = run_tool(self.argv("--final-call", "expiry_day", now=EXPIRY_NOW, json_mode=False))
        self.assertEqual(10, code, text)
        lines = text.splitlines()
        self.assertIn(WMT_NOTED_EXPIRY_DAY, lines)
        self.assertIn("    note: " + BOUNDARY_NOTE, lines)
        self.assertIn("    note: " + GRADE_E_NOTE, lines)
        self.assertTrue(any(line.startswith("  PIN HIGH AG-20261020-01 WMT261120C00105000 short 2: 0.07 %") for line in lines))

    def test_final_call_on_an_early_close_day_acts_by_the_close(self) -> None:
        # Recon finding 2: on 2026-11-27 the session ends at 13:00 ET; the act-by is the earlier of the
        # configured 16:30 ET broker cutoff and the close, marked so the owner confirms the broker's own time.
        with open(self.positions_path, "a", encoding="utf-8") as handle:
            handle.write(EARLY_CLOSE_ROW + "\n")
        self.positions_sha = sha256_of(self.positions_path)
        code, body = run_json(self.argv("--final-call", "regular", now=EARLY_CLOSE_NOW))
        payload = body["payload"]
        self.assertEqual(("final-call", "early_close", "2026-11-27T18:00:00+00:00"),
                         (payload["mode"], payload["session"]["session"], payload["session"]["close_utc"]))
        self.assertEqual(["WMT261127C00105000"], [leg["symbol"] for leg in payload["legs"]])
        leg = payload["legs"][0]
        self.assertEqual(("2026-11-27T18:00:00+00:00", "2026-11-27 13:00 ET", "18:00 UTC", False, True),
                         (leg["act_by_utc"], leg["act_by_et"], leg["act_by_utc_text"], leg["act_by_passed"],
                          leg["act_by_early_close"]))
        self.assertEqual("act by Fri 2026-11-27 13:00 ET (18:00 UTC) [today]; early close 13:00 ET: confirm your broker's "
                         "cutoff", leg["act_by_text"])
        self.assertIn(code, (10, 20), "listed (the regular set's quotes are stale on 2026-11-27, so UNAVAILABLE is fine)")
        code, text = run_tool(self.argv("--final-call", "regular", now=EARLY_CLOSE_NOW, json_mode=False))
        lines = text.splitlines()
        self.assertEqual("FINAL CALL Fri 2026-11-27: 1 contract on its last trading day; act by 13:00 ET (18:00 UTC); "
                         "early close 13:00 ET: confirm your broker's cutoff", lines[0])
        self.assertNotIn("16:30 ET", text, "no instruction time after the session has closed")
        self.assertTrue(any("act by Fri 2026-11-27 13:00 ET (18:00 UTC) [today]; early close" in line for line in lines), text)

    def test_regular_week_and_the_day_after_expiry_are_quiet(self) -> None:
        code, body = run_json(self.argv("--week", "regular"))
        self.assertEqual(0, code, body)
        self.assertEqual(("quiet", [], "2026-11-02", "2026-11-08"),
                         (body["payload"]["verdict"], body["payload"]["legs"], body["payload"]["week_start"], body["payload"]["week_end"]))
        code, text = run_tool(self.argv("--week", "regular", json_mode=False))
        self.assertEqual(0, code)
        self.assertEqual("EXPIRY WEEK 2026-11-02..2026-11-08: nothing due (today Tue 2026-11-03)", text.splitlines()[0])
        code, body = run_json(self.argv("--tomorrow", "expiry_day", now=EXPIRY_NOW))
        self.assertEqual(0, code, body)
        self.assertEqual("2026-11-23", body["payload"]["next_trading_day"])

    def test_monday_week_docket_lists_the_friday_contracts_without_tiers(self) -> None:
        code, body = run_json(self.argv("--week", "t1", now=MONDAY_NOW))
        self.assertEqual(10, code, body)
        legs = body["payload"]["legs"]
        self.assertEqual(["WMT261120C00105000", "NVDA261120C00150000"], [leg["symbol"] for leg in legs])
        self.assertEqual([4, 4], [leg["dte_trading"] for leg in legs])
        self.assertTrue(all(leg["pin"] is None and leg["dividend"] is None for leg in legs))
        self.assertTrue(all(leg["unavailable_reasons"] == [] for leg in legs))
        code, text = run_tool(self.argv("--week", "t1", now=MONDAY_NOW, json_mode=False))
        self.assertEqual(10, code)
        self.assertTrue(any("(4 trading days) |" in line and "act by Fri 2026-11-20 16:30 ET (21:30 UTC)" in line
                            for line in text.splitlines()))

    def test_stale_quotes_on_the_final_call_are_unavailable(self) -> None:
        code, body = run_json(self.argv("--final-call", "stale", now=EXPIRY_NOW))
        self.assertEqual(20, code, body)
        self.assertEqual(("unavailable", "unavailable"), (body["status"], body["payload"]["verdict"]))
        self.assertEqual(2, len(body["payload"]["legs"]), "the due items are still listed")
        self.assertTrue(all(leg["unavailable_reasons"] for leg in body["payload"]["legs"]))
        code, text = run_tool(self.argv("--final-call", "stale", now=EXPIRY_NOW, json_mode=False))
        self.assertEqual(20, code)
        self.assertIn("UNAVAILABLE", text)
        self.assertNotIn("HOLD", text)

    def test_saturday_week_docket_is_quiet_and_fetches_nothing(self) -> None:
        code, body = run_json(self.argv("--week", "post_expiry", now=POST_EXPIRY_NOW))
        self.assertEqual(0, code, body)
        self.assertEqual([4, 6], [row["row_number"] for row in body["payload"]["expired"]])
        self.assertEqual([], self.ledger())

    def test_quarantined_group_is_unavailable(self) -> None:
        self.rewrite("positions.csv", "-684.00,,3.42", "684.00,,3.42")
        code, body = run_json(self.argv("--final-call", "expiry_day", now=EXPIRY_NOW))
        self.assertEqual(20, code, body)
        self.assertIn("AG-20261020-01", body["payload"]["quarantined"])


class ErrorMappingTest(DeskCopy):
    def test_positions_missing_is_exit_1(self) -> None:
        os.remove(self.positions_path)
        code, body = run_json(self.argv("--week", "regular"))
        self.assertEqual((1, "error", "positions_missing"), (code, body["status"], body["error"]["code"]))

    def test_calendar_parse_error_is_exit_1(self) -> None:
        self.rewrite("research/calendar.toml", "schema_version = 1", "schema_version = 2")
        code, body = run_json(self.argv("--week", "regular"))
        self.assertEqual((1, "error", "calendar_invalid"), (code, body["status"], body["error"]["code"]))

    def test_production_host_is_refused_with_exit_20(self) -> None:
        self.rewrite("desk.toml", 'host = "sandbox"', 'host = "production"')
        code, body = run_json(self.argv("--final-call", "regular"))
        self.assertEqual((20, "unavailable", "production_host"), (code, body["status"], body["error"]["code"]))


if __name__ == "__main__":
    unittest.main()
