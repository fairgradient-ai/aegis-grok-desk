"""``scripts/roll_analysis.py``: the replacement candidates for the short call of a ``covered_call`` group, on the owner's
request only (PR 3b, the addendum S1-S17 over designer Q's D3-D16 and designer C's D3-D7).

Every run is in-process (``main(argv)``) on a copy of ``tests/fixtures/desk`` (nothing under ``tests/fixtures/`` is ever
written) with the offline flags and a fixed ``--now``; the copy gets its ``structuring.toml`` the way a journey does:
``desk_init.py --answers tests/fixtures/init/structuring-answers.json`` and then ``ack.py config --owner-tapped``. The
goldens are designer Q's D16 under S17, recomputed with the landed engine (``aegis_desk.structuring`` and
``aegis_desk.structuring_text``) and pinned as full texts: the WMT roll (BEST), the NVDA roll (NONE_QUALIFIES), the
protective put group (NOT_APPLICABLE) and ``[roll] enabled = false``; the fail-closed answers in the order of designer
C's D3.6 (``config_unacknowledged`` before ``criteria_missing``, an invalid file before the approval-level check, that
check before any market data); the stale, after-close and holiday sets; the request counts and ``budget_left``; the
report files (written only on exit 0, idempotent, never overwritten); the journal record; ``--now`` (no wall clock); and
every request a GET through the offline transport.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import datetime
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest import mock

import ack as ack_tool
import desk_init as desk_init_tool
import roll_analysis as tool  # the tool scripts/roll_analysis.py
from aegis_desk import alert_lint, contract, desk_paths, quote_cache, state, structuring, structuring_text, tradier_client

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
ANSWERS = os.path.join(ROOT, "tests", "fixtures", "init", "structuring-answers.json")
CRITERIA_SHA = "5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c"
REGULAR_NOW = "2026-11-03T19:50:00Z"
ACK_NOW = "2026-11-03T19:49:00Z"
AFTER_CLOSE_NOW = "2026-11-03T21:25:00Z"
HOLIDAY_NOW = "2026-11-26T15:00:00Z"
EXPIRY_DAY_NOW = "2026-11-20T19:50:00Z"
WMT = "AG-20261020-01"
NVDA = "AG-20261022-02"
SPY = "AG-20261028-03"
UTC = datetime.timezone.utc


def footer(clock: str) -> str:
    return alert_lint.structuring_footer_line(contract.parse_utc_instant("2026-11-03T%s:00Z" % clock))


FOOTER_1931 = ("computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion "
               "for positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 "
               "min, as of 19:31 UTC")
WMT_TEXT = [
    "STRUCTURING | AG-20261020-01 roll analysis | WMT covered call | criteria structuring.toml (sha 5155427c) | "
    "Tradier sandbox, 15-min delayed, as of 19:31 UTC",
    "replacement candidates for the short 2026-11-20 105 call: 1 passed your criteria, 19 failed",
    "BEST CANDIDATE 1 of 20 by your objective extrinsic_per_day (the bid's time value per calendar day, in percent of "
    "the share price): 0.0467 | the highest among the 1 candidate that passed every criterion",
    "the score ranks the candidates only on the objective you chose; it is not a forecast",
    "leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | ask 3.50 | quote grade B, 18 min old",
    "leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55 | quote grade B, 18 min old",
    "reasons: 73 calendar days inside your window 40 to 75; absolute delta 0.33 inside your band 0.15 to 0.35; bid 3.55 "
    "at or above your minimum 0.50; spread 2.78 % within your maximum 25 %; a net credit of 0.05 (10.00 in all) passes "
    "credit_only; strike 115 above your stock cost 98.50",
    "accepted ex_dividend: the ex-dividend date 2026-11-24 (cum 2026-11-23) falls inside the new call's life (by "
    "#assign-ok)",
    "after both legs: group delta 134.07 (now 101.71), max loss 19006.00 (now 19016.00), max profit 3994.00 (now "
    "1984.00), breakeven 95.03 (now 95.08)",
    "candidate 2: closes the 2026-11-20 105 call and opens the 2026-12-18 112.5 call | net credit -0.70 | "
    "extrinsic_per_day 0.0597 | FAILS_CRITERION credit_only: a net debit of 0.70",
    "candidate 3: closes the 2026-11-20 105 call and opens the 2026-12-18 115 call | net credit -1.40 | "
    "extrinsic_per_day 0.0448 | FAILS_CRITERION credit_only: a net debit of 1.40",
    "failed your criteria: 19 (abs_delta 15, credit_only 4, strike_floor 4)",
    "a ticket for one of these: ask the Aegis Structuring Desk for a ROLL ticket with its expiration and strike",
    "prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not "
    "modelled",
    FOOTER_1931,
]
NVDA_EX = "FAILS_CRITERION ex_dividend: the ex-dividend date 2026-12-03 (cum 2026-12-02) falls inside the new call's life"
NVDA_TEXT = [
    "STRUCTURING | AG-20261022-02 roll analysis | NVDA covered call | criteria structuring.toml (sha 5155427c) | "
    "Tradier sandbox, 15-min delayed, as of 19:31 UTC",
    "replacement candidates for the short 2026-11-20 150 call: 0 passed your criteria, 18 failed, 2 not priced",
    "no candidate passed every criterion: the desk names no best candidate",
    "the net credits close the held 2026-11-20 150 call | ask 3.30 | quote grade C, 18 min old",
    "candidate 1: closes the 2026-11-20 150 call and opens the 2027-01-15 160 call | net credit -0.05 | "
    "extrinsic_per_day 0.0299 | FAILS_CRITERION credit_only: a net debit of 0.05 | " + NVDA_EX,
    "candidate 2: closes the 2026-11-20 150 call and opens the 2026-12-18 160 call | net credit -1.45 | "
    "extrinsic_per_day 0.0276 | FAILS_CRITERION credit_only: a net debit of 1.45 | " + NVDA_EX,
    "candidate 3: closes the 2026-11-20 150 call and opens the 2027-01-15 165 call | net credit -1.35 | "
    "extrinsic_per_day 0.0179 | FAILS_CRITERION credit_only: a net debit of 1.35 | " + NVDA_EX,
    "failed your criteria: 18 (abs_delta 14, min_credit 1, credit_only 6, strike_floor 6, ex_dividend 18)",
    "a ticket for one of these: ask the Aegis Structuring Desk for a ROLL ticket with its expiration and strike",
    "prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not "
    "modelled",
    FOOTER_1931,
]
SPY_NOT_APPLICABLE = [
    "NOT_APPLICABLE | AG-20261028-03 roll analysis | AG-20261028-03 is a protective put: the roll analysis covers the "
    "short call of a covered call; a ROLL ticket prices the replacement of its long put that you name; the roll "
    "analysis of a long put, a short put or a collar is planned for Stage 2 (not in this release)",
    footer("19:50"),
]
DISABLED_TEXT = ["NOT_APPLICABLE | AG-20261020-01 roll analysis | the roll section is disabled in your structuring.toml",
                 footer("19:50")]
CRITERIA_MISSING_TEXT = [
    "UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your criteria (SETUP step 9)",
    footer("19:50"),
]
REFUSED_TEXT = [
    "REFUSED AG-20261020-01 | WMT covered call | opening 2 new short calls, covered by 200 free shares needs level 1 by "
    "the desk's convention; desk.toml records your self-attested level 0 | the desk shows nothing above your level; "
    "your broker's own approval decides what you can enter",
    footer("19:50"),
]
UNACKNOWLEDGED_TAIL = ("the desk names no candidate and prints no ticket while desk.toml, positions.csv or structuring.toml "
                       "differ from what you acknowledged | acknowledge with ack.py config --owner-tapped")
STALE_TEXT = ["UNAVAILABLE | AG-20261020-01 roll analysis | no usable quote of the underlying", footer("19:50")]
AFTER_CLOSE_TEXT = ["UNAVAILABLE | AG-20261020-01 roll analysis | an option chain in your window was not served",
                    footer("21:25")]
HOLIDAY_TEXT = ["UNAVAILABLE | AG-20261020-01 roll analysis | the market is closed today",
                alert_lint.structuring_footer_line(contract.parse_utc_instant(HOLIDAY_NOW))]
JOURNAL_KEYS = ("utc", "tool", "action", "structure", "trade_id", "verdict", "criteria_sha256", "desk_toml_sha256",
                "positions_csv_sha256", "best_candidate", "candidates_passed", "candidates_failed", "refused_code",
                "text_sha256")


def call(main: Any, argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


def order_shaped(text: str) -> List[str]:
    return [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(text)]


def sha256_file(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class DeskCopy(unittest.TestCase):
    """A copy of the fixture desk; ``prepare()`` adds structuring.toml and acknowledges every file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-roll-analysis-tool-")
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)

    # -- set-up steps, as a journey runs them -------------------------------------------------

    def init_structuring(self) -> None:
        code, out = call(desk_init_tool.main, ["--answers", ANSWERS, "--desk-root", self.root, "--json"])
        self.assertEqual(0, code, out)
        self.assertEqual(CRITERIA_SHA, sha256_file(self.paths.structuring_toml))

    def ack(self, now: str = ACK_NOW) -> None:
        code, out = call(ack_tool.main, ["config", "--owner-tapped", "--offline", "--desk-root", self.root, "--now", now,
                                         "--json"])
        self.assertEqual(0, code, out)

    def prepare(self) -> None:
        self.init_structuring()
        self.ack()

    def edit(self, relative: str, old: str, new: str) -> None:
        path = os.path.join(self.root, relative)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(1, text.count(old), old)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new))

    # -- runs ---------------------------------------------------------------------------------

    def argv(self, trade: str = WMT, now: str = REGULAR_NOW, fixture_set: Optional[str] = None,
             json_mode: bool = True) -> List[str]:
        argv = ["--trade", trade, "--offline", "--desk-root", self.root, "--now", now]
        if fixture_set is not None:
            argv += ["--fixture-set", fixture_set]
        return argv + (["--json"] if json_mode else [])

    def run_json(self, trade: str = WMT, now: str = REGULAR_NOW, fixture_set: Optional[str] = None,
                 expected: int = 0) -> Dict[str, Any]:
        code, out = call(tool.main, self.argv(trade, now, fixture_set))
        self.assertEqual([], order_shaped(out))
        body = json.loads(out)
        self.assertEqual(expected, code, out[:3000])
        return body

    def run_text(self, trade: str = WMT, now: str = REGULAR_NOW, fixture_set: Optional[str] = None,
                 expected: int = 0) -> List[str]:
        code, out = call(tool.main, self.argv(trade, now, fixture_set, json_mode=False))
        self.assertEqual([], order_shaped(out))
        self.assertEqual(expected, code, out)
        self.assertTrue(out.endswith("\n"))
        return out[:-1].split("\n")

    def journal(self) -> List[Dict[str, Any]]:
        records, status = state.read_jsonl(self.paths.structuring_journal)
        return records if status == "ok" else []

    def reports(self) -> List[str]:
        try:
            return sorted(os.listdir(self.paths.rolls_dir))
        except FileNotFoundError:
            return []

    def ledger(self) -> List[Dict[str, Any]]:
        records, status = state.read_jsonl(os.path.join(self.paths.cache_dir, "ledger.jsonl"))
        return records if status == "ok" else []


# ------------------------------------------------------------------------------------------------
# The contract: --schema, usage
# ------------------------------------------------------------------------------------------------


class SchemaTest(unittest.TestCase):
    def test_schema_describes_every_payload_key_without_desk_or_network(self) -> None:
        code, out = call(tool.main, ["--schema"])
        self.assertEqual(0, code)
        self.assertEqual([], order_shaped(out))
        body = json.loads(out)
        self.assertEqual("roll_analysis", body["tool"])
        self.assertEqual("read-only", body["mode"])
        payload = body["envelope"]["payload"]
        self.assertEqual(set(tool.PAYLOAD_KEYS), set(payload))
        self.assertEqual(tuple(structuring.ANSWER_KEYS) + ("approval", "config_changed", "message", "emitted", "lint",
                                                          "withheld", "files", "journal"), tool.PAYLOAD_KEYS)
        self.assertIn("/v1/markets/options/expirations", body["network_access"])
        self.assertIn("/v1/markets/options/chains", body["network_access"])
        self.assertEqual({"0", "1", "2", "10", "20"}, set(body["exit_codes"]))

    def test_the_flags(self) -> None:
        parser = tool.build_parser("0.1.0")
        flags = {option for action in parser._actions for option in action.option_strings}
        self.assertTrue({"--trade", "--fixture-set", "--json", "--schema", "--timeout", "--desk-root", "--offline",
                         "--now"} <= flags)
        for forbidden in ("--to", "--candidate", "--symbol", "--target"):
            self.assertNotIn(forbidden, flags)


class UsageTest(DeskCopy):
    def usage(self, argv: Sequence[str]) -> Dict[str, Any]:
        code, out = call(tool.main, list(argv) + ["--json"])
        self.assertEqual(2, code, out)
        body = json.loads(out)
        self.assertEqual(("usage", "invalid_arguments"), (body["status"], body["error"]["code"]))
        return body

    def test_usage_errors_exit_2(self) -> None:
        self.usage(["--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
        self.usage(["--trade", "AG-1", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
        self.usage(["--trade", "wmt", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
        self.usage(["--trade", WMT, "--desk-root", self.root, "--fixture-set", "regular", "--now", REGULAR_NOW])
        self.usage(["--trade", WMT, "--offline", "--desk-root", self.root, "--now", "2026-11-03 19:50"])
        self.usage(["--trade", WMT, "--offline", "--desk-root", self.root, "--to", "WMT270115C00115000"])
        self.assertEqual([], self.journal())

    def test_an_unknown_trade_is_exit_1(self) -> None:
        self.prepare()
        body = self.run_json("AG-20991231-01", expected=1)
        self.assertEqual(("error", "unknown_trade"), (body["status"], body["error"]["code"]))
        self.assertEqual([], self.journal())
        self.assertEqual([], self.reports())


# ------------------------------------------------------------------------------------------------
# The goldens (designer Q's D16 under S17)
# ------------------------------------------------------------------------------------------------


class GoldenTest(DeskCopy):
    def setUp(self) -> None:
        super().setUp()
        self.prepare()

    def test_wmt_roll_is_best_with_the_full_text(self) -> None:
        self.assertEqual(WMT_TEXT, self.run_text())

    def test_wmt_roll_payload(self) -> None:
        body = self.run_json()
        self.assertEqual(("ok", "roll_analysis", "tradier:sandbox", 15),
                         (body["status"], body["tool"], body["source"], body["delay_min"]))
        payload = body["payload"]
        self.assertEqual(set(tool.PAYLOAD_KEYS), set(payload))
        self.assertEqual(("BEST", None, "WMT270115C00115000", "covered_call", "roll", "replacement_call"),
                         (payload["verdict"], payload["cause"], payload["best"], payload["structure"],
                          payload["section"], payload["kind"]))
        self.assertEqual(("AG-20261020-01", "WMT", "Brokerage (margin)", "margin"),
                         (payload["group_id"], payload["underlying"], payload["account"], payload["account_type"]))
        self.assertEqual((1, 19, 20), (payload["passed"], payload["failed"], len(payload["candidates"])))
        best = payload["candidates"][0]
        self.assertEqual((1, "ELIGIBLE", "0.0467", "0.05", "10.00", 2),
                         (best["rank"], best["status"], best["score"], best["net_credit"], best["net_credit_total"],
                          best["contracts"]))
        self.assertEqual({"group_delta": 134.07142892292973, "delta_change": 32.3649197421928, "max_loss": "19006.00",
                          "max_profit": "3994.00", "breakeven": "95.03", "breakevens": ["95.03"], "unbounded_up": False,
                          "realized_on_close": "-16.00"}, payload["after"])
        self.assertEqual([{"by": "#assign-ok", "code": "crosses_ex_dividend", "key": "ex_dividend",
                           "threshold": {"last_trading_day": "2027-01-15"},
                           "value": {"amount": "0.2350", "cum_day": "2026-11-23", "ex_date": "2026-11-24",
                                     "status": "confirmed"}}], best["accepted"])
        self.assertEqual(("3.50", "ask", "B", 18), (payload["held"]["close_price"], payload["held"]["close_side"],
                                                    payload["held"]["grade"], payload["held"]["quote_age_min"]))
        self.assertEqual({"made": 4, "chains": 2, "per_run_max_requests": 20, "per_run_max_chains": 4,
                          "budget_left": 51}, payload["requests"])
        self.assertEqual("extrinsic_per_day", payload["objective"]["id"])
        self.assertEqual(CRITERIA_SHA, payload["criteria"]["sha256"])
        self.assertEqual({"listed_in_window": ["2026-12-18", "2027-01-15"], "evaluated": ["2026-12-18", "2027-01-15"],
                          "not_evaluated": []}, payload["expirations"])
        self.assertEqual("\n".join(WMT_TEXT), payload["message"])
        self.assertEqual({"ok": True, "problems": []}, {k: payload["lint"][k] for k in ("ok", "problems")})
        self.assertIsNone(payload["withheld"])
        self.assertEqual(("structuring", "2026-11-03T19:31:12+00:00", []),
                         (payload["emitted"]["footer"], payload["emitted"]["as_of"], payload["emitted"]["rule_ids"]))
        self.assertEqual({"ok": True, "owner_level": 2, "required_level": 1, "refusal_step": None, "exposure_step": None},
                         {k: payload["approval"][k] for k in ("ok", "owner_level", "required_level", "refusal_step",
                                                             "exposure_step")})
        self.assertEqual([[1, "close_option", 0], [2, "open_covered_call", 1]],
                         [[step["index"], step["kind"], step["level"]] for step in payload["approval"]["steps"]])
        self.assertEqual([], payload["config_changed"])
        self.assertEqual("journal/structuring.jsonl", payload["journal"])
        self.assertEqual({"json": "reports/rolls/AG-20261020-01-20261103T195000Z.json",
                          "md": "reports/rolls/AG-20261020-01-20261103T195000Z.md", "written": True}, payload["files"])
        # the emitted document re-lints the printed text strictly, as a relay would
        emitted = payload["emitted"]
        result = alert_lint.check(payload["message"], emitted["emitted_numbers"], rule_ids=[],
                                  identifiers=emitted["identifiers"], clocks=emitted["clocks"],
                                  as_of=contract.parse_utc_instant("2026-11-03T19:31:12Z"), footer="structuring")
        self.assertTrue(result.ok, result.problems)

    def test_the_prose_is_the_payload_message(self) -> None:
        text = self.run_text()
        body = self.run_json()
        self.assertEqual("\n".join(text), body["payload"]["message"])

    def test_nvda_roll_is_none_qualifies_with_the_full_text(self) -> None:
        self.assertEqual(NVDA_TEXT, self.run_text(NVDA))
        payload = self.run_json(NVDA)["payload"]
        self.assertEqual(("NONE_QUALIFIES", None, 0, 18, 2), (payload["verdict"], payload["best"], payload["passed"],
                                                              payload["failed"], payload["unpriced"]))
        self.assertIsNone(payload["after"])
        top = payload["candidates"][0]
        self.assertEqual(("NVDA270115C00160000", "FAILS_CRITERION", "0.0299", "-0.05", "-10.00"),
                         (top["id"], top["status"], top["score"], top["net_credit"], top["net_credit_total"]))
        self.assertEqual(["credit_only", "ex_dividend"], [reason["key"] for reason in top["reasons"]])
        self.assertEqual({"group_delta": 137.35, "max_loss": "27640.00", "max_profit": "4360.00", "breakeven": "138.20"},
                         {"group_delta": round(top["after"]["group_delta"], 2), "max_loss": top["after"]["max_loss"],
                          "max_profit": top["after"]["max_profit"], "breakeven": top["after"]["breakeven"]})
        self.assertEqual(["NVDA261218C00180000", "NVDA270115C00180000"],
                         [c["id"] for c in payload["candidates"] if c["status"] == "UNPRICED"])

    def test_a_protective_put_group_is_not_applicable_without_a_request(self) -> None:
        self.assertEqual(SPY_NOT_APPLICABLE, self.run_text(SPY))
        payload = self.run_json(SPY)["payload"]
        self.assertEqual(("NOT_APPLICABLE", "structure", []), (payload["verdict"], payload["cause"],
                                                               payload["candidates"]))
        self.assertEqual(0, payload["requests"]["made"])
        self.assertIsNone(payload["approval"])
        self.assertEqual([], self.ledger())
        # BD6: only BEST and NONE_QUALIFIES (a complete evaluation) write a report; the journal record stays
        self.assertEqual([], self.reports())
        self.assertIsNone(payload["files"])
        self.assertEqual(2, len(self.journal()))

    def test_roll_disabled_is_not_applicable(self) -> None:
        self.edit("structuring.toml", "[roll]\nenabled = true", "[roll]\nenabled = false")
        self.ack()
        self.assertEqual(DISABLED_TEXT, self.run_text())
        payload = self.run_json()["payload"]
        self.assertEqual(("NOT_APPLICABLE", "disabled", 0), (payload["verdict"], payload["cause"],
                                                             payload["requests"]["made"]))

    def test_the_owner_typed_account_name_is_never_printed(self) -> None:
        for trade in (WMT, NVDA, SPY):
            text = "\n".join(self.run_text(trade))
            self.assertNotIn("Brokerage", text)
            self.assertNotIn("Roth", text)
            self.assertIsNone(structuring_text.OCC_RE.search(text))
            self.assertNotIn("FAILS_RULE", text)

    def test_expiry_day_the_held_call_closes_at_its_grade_e_ask(self) -> None:
        text = self.run_text(now=EXPIRY_DAY_NOW, fixture_set="expiry_day")
        self.assertIn("the net credits close the held 2026-11-20 105 call | ask 0.16 | quote grade E, 18 min old | "
                      "quote grade E: the conservative side stands", text)
        payload = self.run_json(now=EXPIRY_DAY_NOW, fixture_set="expiry_day")["payload"]
        self.assertEqual(("NONE_QUALIFIES", ["2027-01-15"]), (payload["verdict"], payload["expirations"]["evaluated"]))
        self.edit("positions.csv", "covered call #assign-ok", "covered call #assign-ok #earnings-ok")
        self.ack()
        # a minute later: the report of 19:50 stands as it was (a report file is never overwritten)
        payload = self.run_json(now="2026-11-20T19:51:00Z", fixture_set="expiry_day")["payload"]
        self.assertEqual(("BEST", "WMT270115C00115000", "0.0502", "2.79"),
                         (payload["verdict"], payload["best"], payload["candidates"][0]["score"],
                          payload["candidates"][0]["net_credit"]))


# ------------------------------------------------------------------------------------------------
# Failing closed, in the order of designer C's D3.6
# ------------------------------------------------------------------------------------------------


class FailClosedTest(DeskCopy):
    def test_criteria_missing(self) -> None:
        self.ack()
        self.assertEqual(CRITERIA_MISSING_TEXT, self.run_text(expected=20))
        body = self.run_json(expected=20)
        payload = body["payload"]
        self.assertEqual(("unavailable", "UNAVAILABLE", "criteria_missing"),
                         (body["status"], payload["verdict"], payload["cause"]))
        self.assertIsNone(payload["criteria"])
        self.assertIsNone(payload["files"])
        self.assertEqual([], self.reports())
        self.assertEqual([], self.ledger(), "no market data without criteria")
        records = self.journal()
        self.assertEqual(2, len(records))
        self.assertEqual((None, "criteria_missing", "UNAVAILABLE"),
                         (records[0]["criteria_sha256"], records[0]["refused_code"], records[0]["verdict"]))

    def test_positions_csv_edited_after_the_acknowledgement(self) -> None:
        self.prepare()
        before = sha256_file(self.paths.positions_csv)
        self.edit("positions.csv", "core holding #horizon:2027-03", "core holding #horizon:2027-04")
        after = sha256_file(self.paths.positions_csv)
        expected = ["UNAVAILABLE | CONFIG CHANGED: positions.csv: sha %s -> sha %s | %s" % (before[:8], after[:8],
                                                                                        UNACKNOWLEDGED_TAIL),
                    footer("19:50")]
        self.assertEqual(expected, self.run_text(expected=20))
        payload = self.run_json(expected=20)["payload"]
        self.assertEqual(("UNAVAILABLE", "config_unacknowledged"), (payload["verdict"], payload["cause"]))
        self.assertEqual([{"name": "positions_csv", "old": before, "new": after}], payload["config_changed"])
        self.assertEqual([], self.ledger())
        self.assertEqual([], self.reports())
        self.assertEqual("config_unacknowledged", self.journal()[-1]["refused_code"])
        self.assertEqual(after, self.journal()[-1]["positions_csv_sha256"])

    def test_an_unacknowledged_structuring_toml_comes_before_its_contents(self) -> None:
        self.ack()
        self.init_structuring()
        expected = ["UNAVAILABLE | CONFIG CHANGED: structuring.toml: not acknowledged -> sha 5155427c | %s"
                    % UNACKNOWLEDGED_TAIL, footer("19:50")]
        self.assertEqual(expected, self.run_text(expected=20))

    def test_structuring_invalid_is_exit_1(self) -> None:
        self.init_structuring()
        self.edit("structuring.toml", "max_candidates_shown = 3", "max_candidates_shown = 11")
        self.ack()
        body = self.run_json(expected=1)
        self.assertEqual(("error", "structuring_invalid"), (body["status"], body["error"]["code"]))
        self.assertIn("structuring.toml", body["error"]["message"])
        self.assertEqual([], self.journal(), "exit 1 prints no structuring text")
        self.assertEqual([], self.ledger())

    def test_desk_files_that_cannot_be_used_are_exit_1(self) -> None:
        self.prepare()
        with open(self.paths.calendar_toml, "a", encoding="utf-8") as handle:
            handle.write("\n[symbols.WMT\n")
        body = self.run_json(expected=1)
        self.assertEqual(("error", "calendar_invalid"), (body["status"], body["error"]["code"]))
        shutil.copy(os.path.join(FIXTURE_DESK, "research", "calendar.toml"), self.paths.calendar_toml)
        self.edit("positions.csv", "instrument_type,symbol", "instrument_kind,symbol")
        self.ack()
        body = self.run_json(expected=1)
        self.assertEqual(("error", "positions_invalid"), (body["status"], body["error"]["code"]))
        shutil.copy(os.path.join(FIXTURE_DESK, "positions.csv"), self.paths.positions_csv)
        self.edit("desk.toml", "approval_level = 2 ", "approval_level = 9 ")
        self.ack()
        body = self.run_json(expected=1)
        self.assertEqual(("error", "config_invalid"), (body["status"], body["error"]["code"]))
        self.assertEqual([], self.journal())
        self.assertEqual([], self.ledger())

    def test_refused_at_a_lower_acknowledged_approval_level_before_any_request(self) -> None:
        self.init_structuring()
        self.edit("desk.toml", "approval_level = 2 ", "approval_level = 0 ")
        self.ack()
        self.assertEqual(REFUSED_TEXT, self.run_text(expected=20))
        payload = self.run_json(expected=20)["payload"]
        self.assertEqual(("REFUSED", "approval_level", [], None), (payload["verdict"], payload["cause"],
                                                                   payload["candidates"], payload["best"]))
        self.assertEqual({"ok": False, "owner_level": 0, "required_level": 1, "refusal_step": 2},
                         {k: payload["approval"][k] for k in ("ok", "owner_level", "required_level", "refusal_step")})
        self.assertEqual(0, payload["requests"]["made"])
        self.assertEqual([], self.ledger(), "the approval-level check comes before any market data")
        self.assertEqual([], self.reports())
        self.assertEqual(("REFUSED", "approval_level", None),
                         (self.journal()[-1]["verdict"], self.journal()[-1]["refused_code"],
                          self.journal()[-1]["best_candidate"]))

    def test_an_unacknowledged_rulebook_alone_prints_its_line_first_and_continues(self) -> None:
        self.prepare()
        os.remove(self.paths.adopted_file)
        text = self.run_text()
        self.assertEqual("CONFIG CHANGED: adopted rulebook: sha cc09859c -> missing | the structuring text below reads "
                         "the files as they are now; acknowledge with ack.py config --owner-tapped", text[0])
        self.assertEqual(WMT_TEXT, text[1:])
        payload = self.run_json()["payload"]
        self.assertEqual(["rulebook"], [change["name"] for change in payload["config_changed"]])
        self.assertEqual("BEST", payload["verdict"])


# ------------------------------------------------------------------------------------------------
# Market data: other sets, request counts, GET only
# ------------------------------------------------------------------------------------------------


class MarketDataTest(DeskCopy):
    def setUp(self) -> None:
        super().setUp()
        self.prepare()

    def test_the_stale_set_is_unavailable_after_one_request(self) -> None:
        self.assertEqual(STALE_TEXT, self.run_text(fixture_set="stale", expected=20))
        payload = self.run_json(fixture_set="stale", expected=20)["payload"]
        self.assertEqual(("UNAVAILABLE", "spot_unusable", []), (payload["verdict"], payload["cause"],
                                                                payload["candidates"]))
        self.assertEqual(1, len(self.ledger()), "one quotes request, then it stops (the second run is served cached)")
        self.assertEqual([], self.reports())
        self.assertEqual("spot_unusable", self.journal()[-1]["refused_code"])

    def test_the_stale_set_counts_one_request(self) -> None:
        payload = self.run_json(fixture_set="stale", expected=20)["payload"]
        self.assertEqual((1, 0), (payload["requests"]["made"], payload["requests"]["chains"]))

    def test_after_the_close_the_chain_is_not_in_the_set(self) -> None:
        self.assertEqual(AFTER_CLOSE_TEXT, self.run_text(now=AFTER_CLOSE_NOW, fixture_set="after_close", expected=20))
        self.assertEqual(3, len(self.ledger()), "quotes, expirations and the failed chain")
        payload = self.run_json(now=AFTER_CLOSE_NOW, fixture_set="after_close", expected=20)["payload"]
        self.assertEqual(("UNAVAILABLE", "chain_not_served"), (payload["verdict"], payload["cause"]))
        self.assertEqual([{"expiration": "2026-12-18", "reason": "fetch_failed:network"},
                          {"expiration": "2027-01-15", "reason": "not requested: an earlier expiration was not served"}],
                         payload["expirations"]["not_evaluated"])

    def test_a_holiday_makes_no_request(self) -> None:
        self.assertEqual(HOLIDAY_TEXT, self.run_text(now=HOLIDAY_NOW, fixture_set="holiday", expected=20))
        payload = self.run_json(now=HOLIDAY_NOW, fixture_set="holiday", expected=20)["payload"]
        self.assertEqual(("UNAVAILABLE", "market_closed", 0), (payload["verdict"], payload["cause"],
                                                               payload["requests"]["made"]))
        self.assertEqual([], self.ledger())

    def test_request_counts_and_budget_left(self) -> None:
        first = self.run_json()["payload"]["requests"]
        self.assertEqual({"made": 4, "chains": 2, "per_run_max_requests": 20, "per_run_max_chains": 4,
                          "budget_left": 51}, first)
        second = self.run_json(NVDA)["payload"]["requests"]
        self.assertEqual((4, 2, 47), (second["made"], second["chains"], second["budget_left"]))
        third = self.run_json()["payload"]["requests"]
        self.assertEqual((0, 0, 47), (third["made"], third["chains"], third["budget_left"]),
                         "a rerun inside the TTL is served from the cache")
        self.assertEqual(8, len(self.ledger()))

    def test_every_request_is_a_get_through_the_read_only_transport(self) -> None:
        seen: List[str] = []
        original = tradier_client.OfflineTransport.fetch

        def recording(transport: Any, url: str, headers: Dict[str, str], timeout_s: float) -> Any:
            seen.append(url)
            return original(transport, url, headers, timeout_s)

        with mock.patch.object(tradier_client.OfflineTransport, "fetch", autospec=True, side_effect=recording), \
                mock.patch("http.client.HTTPConnection.request", side_effect=AssertionError("no socket offline")):
            payload = self.run_json()["payload"]
        paths = [url.split("?")[0].split("sandbox.tradier.com", 1)[-1] for url in seen]
        self.assertEqual(["/v1/markets/quotes", "/v1/markets/options/expirations", "/v1/markets/options/chains",
                          "/v1/markets/options/chains"], paths)
        self.assertTrue(set(paths) <= set(tradier_client.ALLOWED_PATHS))
        self.assertEqual(payload["requests"]["made"], len(seen))
        self.assertEqual(paths, [record["path"] for record in self.ledger()])

    def test_now_is_honoured_and_no_wall_clock_is_read(self) -> None:
        with mock.patch("aegis_desk.state.utc_now", side_effect=AssertionError("the wall clock was read")):
            body = self.run_json()
        self.assertEqual("2026-11-03T19:50:00+00:00", body["generated_at"])
        self.assertEqual("2026-11-03T19:50:00+00:00", self.journal()[-1]["utc"])


# ------------------------------------------------------------------------------------------------
# The record: report files (S9) and the journal (designer C's D7.5)
# ------------------------------------------------------------------------------------------------


class RecordTest(DeskCopy):
    def setUp(self) -> None:
        super().setUp()
        self.prepare()

    def test_the_report_files(self) -> None:
        text = self.run_text()
        self.assertEqual(["AG-20261020-01-20261103T195000Z.json", "AG-20261020-01-20261103T195000Z.md"], self.reports())
        stem = os.path.join(self.paths.rolls_dir, "AG-20261020-01-20261103T195000Z")
        with open(stem + ".md", encoding="utf-8") as handle:
            self.assertEqual("# AG-20261020-01 roll analysis (20261103T195000Z)\n\n```text\n%s\n```\n" % "\n".join(text),
                             handle.read())
        with open(stem + ".json", encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(("ok", "roll_analysis", "2026-11-03T19:50:00+00:00"),
                         (report["status"], report["tool"], report["generated_at"]))
        self.assertEqual(set(tool.PAYLOAD_KEYS) - set(tool.REPORT_OMITTED_KEYS), set(report["payload"]))
        self.assertEqual(("requests", "files", "journal"), tool.REPORT_OMITTED_KEYS)
        self.assertEqual(("BEST", "WMT270115C00115000", "\n".join(text)),
                         (report["payload"]["verdict"], report["payload"]["best"], report["payload"]["message"]))

    def test_a_rerun_is_idempotent_and_files_are_never_overwritten(self) -> None:
        first = self.run_json()["payload"]
        self.assertTrue(first["files"]["written"])
        stem = os.path.join(self.paths.rolls_dir, "AG-20261020-01-20261103T195000Z")
        with open(stem + ".json", "rb") as handle:
            data = handle.read()
        second = self.run_json()["payload"]
        self.assertEqual(0, second["requests"]["made"])
        self.assertEqual(dict(first["files"], written=False), second["files"])
        with open(stem + ".json", "rb") as handle:
            self.assertEqual(data, handle.read(), "the cached rerun writes the same bytes: a no-op")
        self.assertEqual(2, len(self.journal()))
        with open(stem + ".json", "wb") as handle:
            handle.write(data.replace(b"WMT270115C00115000", b"WMT270115C00120000", 1))
        body = self.run_json(expected=1)
        self.assertEqual(("error", "ticket_exists"), (body["status"], body["error"]["code"]))
        self.assertEqual(2, len(self.journal()), "an exit 1 appends no record")

    def test_files_only_on_exit_0(self) -> None:
        self.run_json(fixture_set="stale", expected=20)
        self.run_json(now=HOLIDAY_NOW, fixture_set="holiday", expected=20)
        self.assertEqual([], self.reports())
        self.assertEqual(2, len(self.journal()), "an exit 20 printed a text: it is journaled")

    def test_the_journal_record(self) -> None:
        text = "\n".join(self.run_text())
        records = self.journal()
        self.assertEqual(1, len(records))
        record = records[0]
        self.assertEqual(set(JOURNAL_KEYS), set(record))
        self.assertEqual(tuple(structuring_text.JOURNAL_KEYS), JOURNAL_KEYS)
        self.assertEqual({
            "utc": "2026-11-03T19:50:00+00:00", "tool": "roll_analysis", "action": None, "structure": None,
            "trade_id": WMT, "verdict": "BEST", "criteria_sha256": CRITERIA_SHA,
            "desk_toml_sha256": sha256_file(self.paths.desk_toml),
            "positions_csv_sha256": sha256_file(self.paths.positions_csv), "best_candidate": "WMT270115C00115000",
            "candidates_passed": 1, "candidates_failed": 19, "refused_code": None,
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}, record)

    def test_a_text_that_fails_the_lint_is_withheld(self) -> None:
        with mock.patch.object(structuring_text, "roll_analysis_lines",
                               side_effect=lambda w, answer: ["STRUCTURING | the desk wrote 7.77 here"]):
            code, out = call(tool.main, self.argv(json_mode=False))
            body_code, body_out = call(tool.main, self.argv())
        self.assertEqual(1, code)
        lines = out[:-1].split("\n")
        self.assertEqual(2, len(lines))
        self.assertTrue(lines[0].startswith("WITHHELD | the text of roll_analysis.py failed the alert lint ("), lines[0])
        self.assertIn("number_not_emitted", lines[0])
        self.assertTrue(lines[1].startswith("computed by the desk's tools from your criteria"), lines[1])
        self.assertEqual(1, body_code)
        body = json.loads(body_out)
        self.assertEqual("error", body["status"])
        payload = body["payload"]
        self.assertIn("number_not_emitted", payload["withheld"]["codes"])
        self.assertTrue(payload["withheld"]["text"].startswith("STRUCTURING | the desk wrote 7.77 here\n"))
        self.assertFalse(payload["lint"]["ok"])
        self.assertEqual(out[:-1], payload["message"])
        self.assertIsNone(payload["files"])
        self.assertEqual([], self.reports())
        self.assertEqual([], self.journal())


QUARANTINED_ROW = ("option,WMT,WMT261218C00110000,-2,,,,Brokerage (margin),AG-20261030-05,2026-10-30,-400.00,,2.50,open,"
                   ",,,,")
WMT_CALL_ROW = ("option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,open,,,,,"
                "covered call #assign-ok")


class FixerTest(DeskCopy):
    """The PR 3b review, through the tool: BD5, BD6, L1-4, L2-1/L3-1, L3-2, L3-3, L3-6, L4-1 and BD4."""

    def setUp(self) -> None:
        super().setUp()
        self.init_structuring()

    def test_one_source_of_the_disabled_words(self) -> None:
        self.assertFalse(hasattr(tool, "DISABLED_WORDS"), "BD5: the library's words are the only source")

    def test_a_quarantined_group_fails_closed(self) -> None:
        self.edit("positions.csv", WMT_CALL_ROW, WMT_CALL_ROW + "\n" + QUARANTINED_ROW)
        self.ack()
        body = self.run_json(expected=20)
        payload = body["payload"]
        self.assertEqual(("UNAVAILABLE", "positions_quarantined", 0), (payload["verdict"], payload["cause"],
                                                                      payload["requests"]["made"]))
        self.assertEqual(["UNAVAILABLE | AG-20261020-01 roll analysis | positions.csv has a quarantined group "
                          "(positions_check.py shows why): the desk cannot count the account's coverage", footer("19:50")],
                         self.run_text(expected=20))
        self.assertEqual([], self.reports())
        self.assertEqual(["positions_quarantined", "positions_quarantined"], [r["refused_code"] for r in self.journal()])

    def test_every_candidate_unpriced_is_unavailable_without_a_report(self) -> None:
        for name in ("chains__WMT__2026-12-18.json", "chains__WMT__2027-01-15.json"):
            path = os.path.join(self.root, "fixtures", "tradier", "regular", name)
            with open(path, encoding="utf-8") as handle:
                document = json.load(handle)
            for entry in document["options"]["option"]:
                entry["bid_date"] -= 120000
                entry["ask_date"] -= 120000
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(document, handle)
        self.ack()
        lines = self.run_text(expected=20)
        self.assertEqual("UNAVAILABLE | AG-20261020-01 roll analysis | no candidate in your window could be priced: the "
                         "underlying's quote is stamped too far from the option's", lines[0])
        self.assertEqual([], self.reports())

    def test_a_window_without_a_listed_monthly_is_not_applicable_without_a_report(self) -> None:
        self.edit("structuring.toml", "[roll]\nenabled = true\ndte = [40, 75]", "[roll]\nenabled = true\ndte = [80, 100]")
        self.ack()
        self.assertEqual(["NOT_APPLICABLE | AG-20261020-01 roll analysis | no listed standard monthly expiration lies "
                          "inside your roll window of 80 to 100 calendar days", footer("19:50")], self.run_text())
        payload = self.run_json()["payload"]
        self.assertEqual(("NOT_APPLICABLE", "no_expiration_in_window", None), (payload["verdict"], payload["cause"],
                                                                               payload["files"]))
        self.assertEqual([], self.reports())

    def test_accounts_named_with_the_desk_words_print_the_analysis(self) -> None:
        for name in ("options", "brokerage"):
            with self.subTest(name=name):
                for relative in ("desk.toml", "positions.csv"):
                    path = os.path.join(self.root, relative)
                    with open(path, encoding="utf-8") as handle:
                        text = handle.read()
                    with open(path, "w", encoding="utf-8") as handle:
                        handle.write(text.replace("Roth IRA", name))
                self.ack()
                lines = self.run_text()
                self.assertTrue(lines[0].startswith("STRUCTURING | AG-20261020-01 roll analysis"), lines[0])
                for relative in ("desk.toml", "positions.csv"):
                    shutil.copyfile(os.path.join(FIXTURE_DESK, relative), os.path.join(self.root, relative))

    def test_two_runs_in_one_second_write_one_report(self) -> None:
        self.ack()
        first = self.run_json(now="2026-11-03T19:50:00.100000Z")
        second = self.run_json(now="2026-11-03T19:50:00.900000Z")
        self.assertEqual((True, False), (first["payload"]["files"]["written"], second["payload"]["files"]["written"]))
        self.assertEqual("2026-11-03T19:50:00+00:00", second["generated_at"])
        self.assertEqual(2, len(self.journal()))

    def test_refused_market_data_is_journaled(self) -> None:
        self.ack()
        error = quote_cache.MarketDataRefused("no_token", "no market-data token: add the Tradier sandbox token")
        with mock.patch.object(tool.quote_cache, "open_market_data", side_effect=error):
            code, out = call(tool.main, self.argv(json_mode=False))
        self.assertEqual((20, "UNAVAILABLE: no market-data token: add the Tradier sandbox token\n"), (code, out))
        (record,) = self.journal()
        self.assertEqual(("UNAVAILABLE", "no_token", CRITERIA_SHA, hashlib.sha256(out[:-1].encode("utf-8")).hexdigest()),
                         (record["verdict"], record["refused_code"], record["criteria_sha256"], record["text_sha256"]))

    def test_a_replacement_below_the_stock_cost_reads_below(self) -> None:
        self.edit("positions.csv", "AG-20261020-01,2026-10-20,,98.50,", "AG-20261020-01,2026-10-20,,120.00,")
        self.ack()
        lines = self.run_text()
        (reasons,) = [line for line in lines if line.startswith("reasons: ")]
        self.assertTrue(reasons.endswith("; strike 115 below your stock cost 120.00 and not below the held strike 105"),
                        reasons)

    def test_fractional_shares_are_analysed(self) -> None:
        self.edit("positions.csv", "equity,WMT,,200,", "equity,WMT,,200.5,")
        self.ack()
        payload = self.run_json()["payload"]
        self.assertEqual(("BEST", "WMT270115C00115000", "200.5"), (payload["verdict"], payload["best"], payload["shares"]))


if __name__ == "__main__":
    unittest.main()
