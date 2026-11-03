"""``scripts/assignment_watch.py`` run in-process against a copied fixture desk root: the dividend
(extrinsic-versus-dividend) and pin tiers with act-by times, the exit codes 0 / 10 / 20 and the
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

import assignment_watch as tool

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
T1_NOW = "2026-11-19T19:50:00Z"
EXPIRY_NOW = "2026-11-20T19:50:00Z"
POST_EXPIRY_NOW = "2026-11-21T14:30:00Z"
HOLIDAY_NOW = "2026-11-26T15:00:00Z"
FOOTER_T1 = "Tradier sandbox, delayed 15 min, as of 19:31 UTC"
# The fixture calendar's WMT ex-dividend (Tue 2026-11-24) has its last cum-dividend day on Mon 2026-11-23, after the
# Nov-20 call's last trading day: the fixture sets list no dividend tier for the WMT call. The tests that need one
# move the COPY's ex-dividend to Mon 2026-11-23 (cum day Fri 2026-11-20 = the last trading day), which grades the call
# with the boundary note.
WMT_NOTED = ("NOTED AG-20261020-01 WMT261120C00105000 short 2: ex-dividend 2026-11-23 in 2 trading days (confirmed), "
             "amount 0.2350; extrinsic (bid) 0.09 = bid 1.52 - max(0, 106.43 - 105); in the money; HIGH downgraded to "
             "NOTED: #assign-ok (owner accepted assignment); act by Fri 2026-11-20 16:30 ET (21:30 UTC) [next trading day]")
WMT_NOTED_EXPIRY_DAY = ("NOTED AG-20261020-01 WMT261120C00105000 short 2: ex-dividend 2026-11-23 in 1 trading day (confirmed), "
                        "amount 0.2350; extrinsic (bid) 0.12 = bid 0.12 - max(0, 104.93 - 105); out of the money; HIGH downgraded "
                        "to NOTED: #assign-ok (owner accepted assignment); act by Fri 2026-11-20 16:30 ET (21:30 UTC) [today]")
WMT_PIN_WATCH = ("PIN WATCH AG-20261020-01 WMT261120C00105000 short 2: 1.34 % from the strike (mid 106.43 vs 105), in the "
                 "money; last trading day Fri 2026-11-20; act by Fri 2026-11-20 16:30 ET (21:30 UTC) [next trading day]")
NVDA_PIN_HIGH = ("PIN HIGH AG-20261022-02 NVDA261120C00150000 short 2: 0.52 % from the strike (mid 149.225 vs 150), out of "
                 "the money; last trading day Fri 2026-11-20; act by Fri 2026-11-20 16:30 ET (21:30 UTC) [next trading day]")
BOUNDARY_NOTE = ("ex-dividend 2026-11-23 is the first trading day after the last trading day 2026-11-20: a dividend-capture "
                 "exercise would come at expiration")
GRADE_E_NOTE = "option quote grade E (wide: spread 28.57% > 25%): the bid-side arithmetic stands"
ITM_LINE_FRIDAY = "  in the money: automatic exercise or assignment expected at the close of Fri 2026-11-20"


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
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-assignment-watch-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.positions_path = os.path.join(self.root, "positions.csv")
        self.positions_sha = sha256_of(self.positions_path)

    def tearDown(self) -> None:
        self.assertEqual(self.positions_sha, sha256_of(self.positions_path), "the tool never writes positions.csv")
        self._tmp.cleanup()

    def argv(self, fixture_set: str = None, now: str = REGULAR_NOW, json_mode: bool = True,
             extra: Sequence[str] = ()) -> List[str]:
        argv = ["--offline", "--desk-root", self.root, "--now", now] + list(extra)
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

    def drop_positions_lines(self, needle: str) -> None:
        with open(self.positions_path, encoding="utf-8") as handle:
            lines = handle.read().splitlines(True)
        kept = [line for line in lines if needle not in line]
        self.assertLess(len(kept), len(lines))
        with open(self.positions_path, "w", encoding="utf-8") as handle:
            handle.write("".join(kept))
        self.positions_sha = sha256_of(self.positions_path)

    def leg(self, body: Dict[str, Any], symbol: str) -> Dict[str, Any]:
        found = [leg for leg in body["payload"]["legs"] if leg["symbol"] == symbol]
        self.assertEqual(1, len(found), body["payload"]["legs"])
        return found[0]


class AlwaysOnTest(DeskCopy):
    def test_schema(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual("assignment_watch", body["tool"])
        payload = body["envelope"]["payload"]
        for key in ("verdict", "legs", "quote_check", "quarantined", "expired"):
            self.assertIn(key, payload)
        self.assertIn("quotes", body["network_access"])

    def test_schema_leg_matches_the_json_leg_key_for_key(self) -> None:
        # Recon finding 6: the --schema leg object (what docs/TOOLS.md is generated from) carries exactly the
        # keys of a --json leg, its dividend block included (option_grade is reported on every graded leg).
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        described = body["envelope"]["payload"]["legs"][0]
        self.assertIn("option_grade", described["dividend"])
        self.move_wmt_ex_dividend_to_monday()  # the fixture geometry lists no dividend block for the WMT call
        code, body = run_json(self.argv("t1", now=T1_NOW))
        self.assertEqual(10, code, body)
        wmt = self.leg(body, "WMT261120C00105000")
        self.assertEqual(set(described), set(wmt))
        self.assertEqual(set(described["dividend"]), set(wmt["dividend"]))
        self.assertEqual(set(described["pin"]), set(wmt["pin"]))
        self.assertIn("option_grade", wmt["dividend"])
        for leg in body["payload"]["legs"]:
            self.assertEqual(set(described), set(leg), leg["symbol"])

    def test_usage_errors(self) -> None:
        for argv in (["--json", "--desk-root", self.root, "--fixture-set", "regular"],
                     ["--json", "--offline"],
                     ["--json", "--offline", "--desk-root", self.root, "--week"],
                     ["--json", "--offline", "--desk-root", self.root, "--now", "2026-11-03 19:50"]):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(argv)
                self.assertEqual(2, ctx.exception.code)


class JourneysTest(DeskCopy):
    def test_regular_day_is_quiet(self) -> None:
        code, body = run_json(self.argv("regular"))
        self.assertEqual(0, code, body)
        self.assertEqual(("ok", "tradier:sandbox", 15), (body["status"], body["source"], body["delay_min"]))
        payload = body["payload"]
        self.assertEqual(("quiet", 0, "2026-11-03", "2026-11-04"),
                         (payload["verdict"], payload["exit_code"], payload["today"], payload["next_trading_day"]))
        self.assertEqual(["WMT261120C00105000", "NVDA261120C00150000", "SPY261218P00620000"],
                         [leg["symbol"] for leg in payload["legs"]])
        self.assertTrue(all(leg["pin"] is None and leg["dividend"] is None for leg in payload["legs"]))
        self.assertEqual(["NVDA", "NVDA261120C00150000", "SPY", "SPY261218P00620000", "WMT", "WMT261120C00105000"],
                         payload["quote_check"]["symbols"])
        self.assertEqual((1, {}), (payload["quote_check"]["requests_made"], payload["quote_check"]["missing"]))
        self.assertEqual(1, len(self.ledger()), "one batch for the whole book")
        self.assertEqual("open", payload["session"]["session"])
        self.assertEqual("regular", payload["fixture_set"])
        code, text = run_tool(self.argv("regular", json_mode=False))
        self.assertEqual(0, code)
        lines = text.splitlines()
        self.assertIn("nothing within the windows: no ex-dividend inside 5 trading days with a short call alive on the last "
                      "cum-dividend day, no contract on its last or next-to-last trading day", lines)
        self.assertEqual("Tradier sandbox, delayed 15 min, as of 19:31 UTC", lines[-1])

    def test_t1_fires_on_the_nvda_pin_and_lists_no_dividend_tier_for_the_wmt_call(self) -> None:
        code, body = run_json(self.argv("t1", now=T1_NOW))
        self.assertEqual(10, code, body)
        self.assertEqual("fired", body["status"])
        payload = body["payload"]
        self.assertEqual(("fired", 10, "2026-11-19", "2026-11-20"),
                         (payload["verdict"], payload["exit_code"], payload["today"], payload["next_trading_day"]))
        wmt = self.leg(body, "WMT261120C00105000")
        self.assertIsNone(wmt["dividend"], "the WMT ex-dividend 2026-11-24 (cum day Mon 2026-11-23) falls after the call's "
                                           "last trading day Fri 2026-11-20: nothing to list, no HIGH for a risk that cannot occur")
        self.assertEqual(("PIN WATCH", "1.34", True), (wmt["pin"]["tier"], wmt["pin"]["distance_pct"], wmt["pin"]["itm"]))
        self.assertFalse(wmt["fired"])
        self.assertEqual([], wmt["unavailable_reasons"])
        self.assertEqual((1, 0, 0, 3), (payload["counts"]["fired"], payload["counts"]["noted"], payload["counts"]["unavailable"],
                                        payload["counts"]["open_option_legs"]))
        self.assertEqual(("2026-11-20T21:30:00+00:00", "2026-11-20 16:30 ET", "21:30 UTC", False),
                         (wmt["act_by_utc"], wmt["act_by_et"], wmt["act_by_utc_text"], wmt["act_by_passed"]))
        nvda = self.leg(body, "NVDA261120C00150000")
        self.assertEqual(("PIN HIGH", "0.52", "149.225", "150", False, True),
                         (nvda["pin"]["tier"], nvda["pin"]["distance_pct"], nvda["pin"]["spot_mid"], nvda["pin"]["strike"],
                          nvda["pin"]["itm"], nvda["fired"]))
        self.assertIsNone(nvda["dividend"])
        self.assertEqual("pm", nvda["settlement"])
        self.assertEqual(1, nvda["dte_trading"])
        self.assertIsNone(self.leg(body, "SPY261218P00620000")["pin"])

    def test_t1_prose_lines_and_footer(self) -> None:
        code, text = run_tool(self.argv("t1", now=T1_NOW, json_mode=False))
        self.assertEqual(10, code, text)
        lines = text.splitlines()
        self.assertIn(WMT_PIN_WATCH, lines)
        self.assertIn(ITM_LINE_FRIDAY, lines)
        self.assertIn(NVDA_PIN_HIGH, lines)
        self.assertNotIn("ex-dividend", text, "no dividend line: the WMT call expires before its ex-dividend's cum day")
        self.assertNotIn("NOTED", text)
        self.assertEqual(FOOTER_T1, lines[-1])
        self.assertTrue(any(line.startswith("assignment watch Thu 2026-11-19") for line in lines), lines[0])
        self.assertTrue(any("FIRED (1 fired, 0 noted, 0 unavailable, 3 open option legs)" in line for line in lines), lines[0])
        self.assertNotIn("UNAVAILABLE", text)

    def test_t1_with_the_ex_dividend_on_the_first_trading_day_after_expiry_notes_the_wmt_call(self) -> None:
        # Ex-dividend Mon 2026-11-23: the cum day is Fri 2026-11-20, the call's last trading day, so a dividend-capture
        # exercise would come at expiration; extrinsic 0.09 < 0.2350 is HIGH, #assign-ok makes it NOTED, with the note.
        self.move_wmt_ex_dividend_to_monday()
        code, body = run_json(self.argv("t1", now=T1_NOW))
        self.assertEqual(10, code, body)
        wmt = self.leg(body, "WMT261120C00105000")
        dividend = wmt["dividend"]
        self.assertEqual(("NOTED", "HIGH", "2026-11-23", 2, "confirmed", "0.2350"),
                         (dividend["tier"], dividend["downgraded_from"], dividend["ex_date"], dividend["trading_days_to_ex"],
                          dividend["dividend_status"], dividend["amount"]))
        self.assertEqual(("1.52", "106.43", "1.43", "0.09", True),
                         (dividend["bid"], dividend["spot_mid"], dividend["intrinsic"], dividend["extrinsic_bid"], dividend["itm"]))
        self.assertEqual(([BOUNDARY_NOTE], False, False), (dividend["notes"], dividend["fired"], dividend["unavailable"]))
        self.assertEqual(("PIN WATCH", False), (wmt["pin"]["tier"], wmt["fired"]))
        self.assertEqual((1, 1, 0), (body["payload"]["counts"]["fired"], body["payload"]["counts"]["noted"],
                                     body["payload"]["counts"]["unavailable"]))
        self.assertTrue(self.leg(body, "NVDA261120C00150000")["fired"])
        code, text = run_tool(self.argv("t1", now=T1_NOW, json_mode=False))
        self.assertEqual(10, code, text)
        lines = text.splitlines()
        self.assertIn(WMT_NOTED, lines)
        self.assertEqual(lines.index(WMT_NOTED) + 1, lines.index(WMT_PIN_WATCH), "the pin line follows the dividend line")
        self.assertIn(ITM_LINE_FRIDAY, lines)
        self.assertIn("  note: " + BOUNDARY_NOTE, lines)
        self.assertIn(NVDA_PIN_HIGH, lines)
        self.assertTrue(any("FIRED (1 fired, 1 noted, 0 unavailable, 3 open option legs)" in line for line in lines), lines[0])

    def test_expiry_day_pin_high_and_in_the_money_line(self) -> None:
        code, body = run_json(self.argv("expiry_day", now=EXPIRY_NOW))
        self.assertEqual(10, code, body)
        wmt = self.leg(body, "WMT261120C00105000")
        self.assertEqual(("PIN HIGH", "0.07", "104.93", False, True), (wmt["pin"]["tier"], wmt["pin"]["distance_pct"],
                                                                      wmt["pin"]["spot_mid"], wmt["pin"]["itm"], wmt["fired"]))
        self.assertIsNone(wmt["dividend"], "the cum day Mon 2026-11-23 is after the last trading day Fri 2026-11-20 (today)")
        nvda = self.leg(body, "NVDA261120C00150000")
        self.assertEqual(("PIN WATCH", "2.17", True, "in the money: automatic exercise or assignment expected at the close"),
                         (nvda["pin"]["tier"], nvda["pin"]["distance_pct"], nvda["pin"]["itm"], nvda["pin"]["line"]))
        self.assertEqual((True, True, 0), (nvda["due_today"], nvda["pin"]["on_last_trading_day"], nvda["dte_trading"]))
        code, text = run_tool(self.argv("expiry_day", now=EXPIRY_NOW, json_mode=False))
        self.assertEqual(10, code)
        lines = text.splitlines()
        self.assertIn("  in the money: automatic exercise or assignment expected at the close", lines)
        self.assertFalse(any(line.startswith("  note:") for line in lines), "no dividend tier, so no dividend notes")
        self.assertNotIn("ex-dividend", text)
        self.assertTrue(any(line.startswith("PIN HIGH AG-20261020-01 WMT261120C00105000 short 2: 0.07 %") for line in lines))
        self.assertTrue(any("act by Fri 2026-11-20 16:30 ET (21:30 UTC) [today]" in line for line in lines))

    def test_expiry_day_with_the_ex_dividend_on_the_next_trading_day_notes_the_grade_e_call(self) -> None:
        # Ex-dividend Mon 2026-11-23 seen on Fri 2026-11-20: one trading day out, the cum day is today (the last trading
        # day). The WMT call quote 0.12/0.16 is graded E (wide) and the rule is bid-side: extrinsic 0.12 < 0.2350 is
        # HIGH, #assign-ok makes it NOTED; the leg carries the boundary note and the grade-E note.
        self.move_wmt_ex_dividend_to_monday()
        code, body = run_json(self.argv("expiry_day", now=EXPIRY_NOW))
        self.assertEqual(10, code, body)
        wmt = self.leg(body, "WMT261120C00105000")
        dividend = wmt["dividend"]
        self.assertEqual(("NOTED", "HIGH", "2026-11-23", 1, "0.12", "104.93", "0", "0.12", False, "E"),
                         (dividend["tier"], dividend["downgraded_from"], dividend["ex_date"], dividend["trading_days_to_ex"],
                          dividend["bid"], dividend["spot_mid"], dividend["intrinsic"], dividend["extrinsic_bid"], dividend["itm"],
                          dividend["option_grade"]))
        self.assertEqual([BOUNDARY_NOTE, GRADE_E_NOTE], dividend["notes"])
        self.assertEqual(("PIN HIGH", True, True), (wmt["pin"]["tier"], wmt["due_today"], wmt["fired"]))
        code, text = run_tool(self.argv("expiry_day", now=EXPIRY_NOW, json_mode=False))
        self.assertEqual(10, code, text)
        lines = text.splitlines()
        self.assertIn(WMT_NOTED_EXPIRY_DAY, lines)
        self.assertIn("  note: " + BOUNDARY_NOTE, lines)
        self.assertIn("  note: " + GRADE_E_NOTE, lines)
        self.assertTrue(any(line.startswith("PIN HIGH AG-20261020-01 WMT261120C00105000 short 2: 0.07 %") for line in lines))

    def test_saturday_after_expiry_leaves_expired_legs_to_the_reconciliation(self) -> None:
        code, body = run_json(self.argv("post_expiry", now=POST_EXPIRY_NOW))
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual(["SPY261218P00620000"], [leg["symbol"] for leg in payload["legs"]])
        self.assertEqual([4, 6], [row["row_number"] for row in payload["expired"]])
        self.assertTrue(any("csv_recon.py" in warning for warning in payload["warnings"]))
        self.assertEqual("weekend", payload["session"]["session"])
        self.assertEqual([], self.ledger(), "no fetch on a weekend")
        self.assertEqual(["NVDA", "SPY", "SPY261218P00620000", "WMT"], payload["quote_check"]["symbols"])

    def test_thanksgiving_is_quiet(self) -> None:
        code, body = run_json(self.argv("holiday", now=HOLIDAY_NOW))
        self.assertEqual(0, code, body)
        self.assertEqual("holiday", body["payload"]["session"]["session"])
        self.assertEqual("quiet", body["payload"]["verdict"])

    def test_stale_quotes_on_t1_are_unavailable(self) -> None:
        self.move_wmt_ex_dividend_to_monday()  # so the WMT call carries a dividend tier that can report UNAVAILABLE
        code, body = run_json(self.argv("stale", now=T1_NOW))
        self.assertEqual(20, code, body)
        self.assertEqual("unavailable", body["status"])
        nvda = self.leg(body, "NVDA261120C00150000")
        self.assertEqual("UNAVAILABLE", nvda["pin"]["tier"])
        self.assertIn("NVDA quote unavailable (F stale", nvda["pin"]["reason"])
        wmt = self.leg(body, "WMT261120C00105000")
        self.assertEqual(("UNAVAILABLE", "0.2350", "2026-11-23", [BOUNDARY_NOTE], "UNAVAILABLE"),
                         (wmt["dividend"]["tier"], wmt["dividend"]["amount"], wmt["dividend"]["ex_date"], wmt["dividend"]["notes"],
                          wmt["pin"]["tier"]), "the dividend facts are kept on an UNAVAILABLE verdict")
        code, text = run_tool(self.argv("stale", now=T1_NOW, json_mode=False))
        self.assertEqual(20, code)
        self.assertIn("UNAVAILABLE", text)
        self.assertNotIn("HOLD", text)

    def test_no_calendar_is_unavailable_when_nothing_fires(self) -> None:
        os.remove(os.path.join(self.root, "research", "calendar.toml"))
        self.drop_positions_lines("NVDA")
        code, body = run_json(self.argv("t1", now=T1_NOW))
        self.assertEqual(20, code, body)
        wmt = self.leg(body, "WMT261120C00105000")
        self.assertEqual("UNAVAILABLE", wmt["dividend"]["tier"])
        self.assertIn("no research/calendar.toml", wmt["dividend"]["reason"])
        self.assertIn("unknown dividend is never zero", wmt["dividend"]["reason"])
        self.assertEqual("unavailable", body["payload"]["verdict"])

    def test_no_calendar_with_a_pin_high_still_fires(self) -> None:
        os.remove(os.path.join(self.root, "research", "calendar.toml"))
        code, body = run_json(self.argv("t1", now=T1_NOW))
        self.assertEqual(10, code, body)
        self.assertEqual("UNAVAILABLE", self.leg(body, "NVDA261120C00150000")["dividend"]["tier"])
        self.assertEqual("PIN HIGH", self.leg(body, "NVDA261120C00150000")["pin"]["tier"])

    def test_quarantined_group_is_unavailable(self) -> None:
        self.rewrite("positions.csv", "-684.00,,3.42", "684.00,,3.42")
        code, body = run_json(self.argv("regular"))
        self.assertEqual(20, code, body)
        self.assertIn("AG-20261020-01", body["payload"]["quarantined"])
        self.assertEqual(["NVDA261120C00150000", "SPY261218P00620000"], [leg["symbol"] for leg in body["payload"]["legs"]])
        code, text = run_tool(self.argv("regular", json_mode=False))
        self.assertIn("quarantined AG-20261020-01:", text)


class ErrorMappingTest(DeskCopy):
    def test_positions_missing_is_exit_1(self) -> None:
        os.remove(self.positions_path)
        code, body = run_json(self.argv("regular"))
        self.assertEqual((1, "error", "positions_missing"), (code, body["status"], body["error"]["code"]))
        with open(self.positions_path, "w", encoding="utf-8") as handle:
            handle.write("")
        self.positions_sha = sha256_of(self.positions_path)

    def test_positions_file_error_is_exit_1(self) -> None:
        self.rewrite("positions.csv", "cost_basis_total,cost_basis_per_unit", "cost_basis_total,cost_basis_totl")
        code, body = run_json(self.argv("regular"))
        self.assertEqual((1, "error", "positions_invalid"), (code, body["status"], body["error"]["code"]))
        self.assertIn("did you mean", body["error"]["message"])

    def test_calendar_parse_error_is_exit_1(self) -> None:
        self.rewrite("research/calendar.toml", "schema_version = 1", "schema_version = 2")
        code, body = run_json(self.argv("regular"))
        self.assertEqual((1, "error", "calendar_invalid"), (code, body["status"], body["error"]["code"]))

    def test_production_host_is_refused_with_exit_20(self) -> None:
        self.rewrite("desk.toml", 'host = "sandbox"', 'host = "production"')
        code, body = run_json(self.argv("regular"))
        self.assertEqual((20, "unavailable", "production_host"), (code, body["status"], body["error"]["code"]))

    def test_invalid_config_is_exit_1(self) -> None:
        self.rewrite("desk.toml", "grace_min = 5", "grace_min = 31")
        code, body = run_json(self.argv("regular"))
        self.assertEqual((1, "error", "config_invalid"), (code, body["status"], body["error"]["code"]))

    def test_file_in_place_of_a_runtime_directory_is_io_error(self) -> None:
        with open(os.path.join(self.root, "state"), "w", encoding="utf-8") as handle:
            handle.write("not a directory\n")
        code, body = run_json(self.argv("regular"))
        self.assertEqual((1, "error", "io_error"), (code, body["status"], body["error"]["code"]))


if __name__ == "__main__":
    unittest.main()
