"""``scripts/propose.py``: a covered call, a protective put or a collar on a shares-only group, on the owner's request
only (PR 3b, the addendum S1-S17 over designer Q's D10-D16 and designer C's D3-D7).

Every run is in-process (``main(argv)``) on a copy of ``tests/fixtures/desk`` with the offline flags and a fixed
``--now``; the copy gets its ``structuring.toml`` as a journey does (``desk_init.py --answers
tests/fixtures/init/structuring-answers.json``, then ``ack.py config --owner-tapped``). The main fixture holds no
shares-only group, so every proposal there is NOTHING_TO_PROPOSE (designer Q's D10); the goldens of designer Q's D16
under S17 run on test-time copies of ``positions.csv`` without row 4 (the WMT call) or row 8 (the SPY put), with and
without the tags, pinned as full texts: the WMT covered call (NONE_QUALIFIES without ``#earnings-ok #assign-ok``, BEST
with them), the SPY protective put (BEST) and the SPY collar (NONE_QUALIFIES without ``#assign-ok``, BEST with it).
Then the approval-level REFUSED before any request, ``enabled = false``, no free shares, the fail-closed answers, the
stale and holiday sets, the request counts, the report files (exit 0 only, idempotent), the journal record, ``--now``
and every request a GET.
"""

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
from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest import mock

import ack as ack_tool
import desk_init as desk_init_tool
import propose as tool  # the tool scripts/propose.py
from aegis_desk import alert_lint, contract, desk_paths, state, structuring, structuring_text, tradier_client

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
ANSWERS = os.path.join(ROOT, "tests", "fixtures", "init", "structuring-answers.json")
CRITERIA_SHA = "5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c"
REGULAR_NOW = "2026-11-03T19:50:00Z"
ACK_NOW = "2026-11-03T19:49:00Z"
HOLIDAY_NOW = "2026-11-26T15:00:00Z"
WMT = "AG-20261020-01"
NVDA = "AG-20261022-02"
SPY = "AG-20261028-03"
WMT_CALL_ROW = "WMT261120C00105000"
SPY_PUT_ROW = "SPY261218P00620000"
WMT_SHARES_NOTE = ("92.00,core holding #horizon:2027-03", "92.00,core holding #horizon:2027-03 #earnings-ok #assign-ok")
SPY_SHARES_ROW = ("Roth IRA,AG-20261028-03,2026-10-28,63240.00,,,open,,,,,",
                  "Roth IRA,AG-20261028-03,2026-10-28,63240.00,,,open,,,,,#assign-ok")


def footer(clock: str) -> str:
    return alert_lint.structuring_footer_line(contract.parse_utc_instant("2026-11-03T%s:00Z" % clock))


FOOTER_1931 = footer("19:31")
SOURCE = "Tradier sandbox, 15-min delayed, as of 19:31 UTC"
MODEL = ("prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is "
         "not modelled")
LIMIT = "you supply the limit; the desk prints none"
ACT = "if you decide to act, you type it into your brokerage account yourself and you supply the limit"
CAVEAT = "the score ranks the candidates only on the objective you chose; it is not a forecast"
WMT_HEADER = ("PROPOSAL WMT covered call | on 200 WMT shares you hold in account 1 of desk.toml (margin) | criteria "
              "structuring.toml (sha 5155427c) | " + SOURCE)
SPY_PP_HEADER = ("PROPOSAL SPY protective put | on 100 SPY shares you hold in account 2 of desk.toml (ira) | criteria "
                 "structuring.toml (sha 5155427c) | " + SOURCE)
SPY_COLLAR_HEADER = ("PROPOSAL SPY collar | on 100 SPY shares you hold in account 2 of desk.toml (ira) | criteria "
                     "structuring.toml (sha 5155427c) | " + SOURCE)
WMT_EVENTS = ("FAILS_CRITERION earnings: earnings on 2026-11-17 (before the open) fall inside the call's life | "
              "FAILS_CRITERION ex_dividend: the ex-dividend date 2026-11-24 (cum 2026-11-23) falls inside the call's life")
WMT_NONE_TEXT = [
    WMT_HEADER,
    "candidates for a covered call on the 200 WMT shares: 0 passed your criteria, 20 failed",
    "no candidate passed every criterion: the desk names no best candidate",
    "candidate 1: the 2026-12-18 112.5 call | bid 2.80 | extrinsic_per_day 0.0597 | " + WMT_EVENTS,
    "candidate 2: the 2027-01-15 115 call | bid 3.55 | extrinsic_per_day 0.0467 | " + WMT_EVENTS,
    "candidate 3: the 2026-12-18 115 call | bid 2.10 | extrinsic_per_day 0.0448 | " + WMT_EVENTS,
    "failed your criteria: 20 (abs_delta 15, strike_floor 4, earnings 20, ex_dividend 20)",
    MODEL, LIMIT, ACT, FOOTER_1931,
]
WMT_BEST_TEXT = [
    WMT_HEADER,
    "candidates for a covered call on the 200 WMT shares: 5 passed your criteria, 15 failed",
    "BEST CANDIDATE 1 of 20 by your objective extrinsic_per_day (the bid's time value per calendar day, in percent of "
    "the share price): 0.0597 | the highest among the 5 candidates that passed every criterion",
    CAVEAT,
    "leg 1 of 1: opens a new short 2 x WMT 2026-12-18 112.5 call | bid 2.80, ask 2.90, mid 2.85 | quote grade B, " + SOURCE,
    "reasons: 45 calendar days inside your window 40 to 75; absolute delta 0.32 inside your band 0.15 to 0.35; bid 2.80 "
    "at or above your minimum 0.50; spread 3.51 % within your maximum 25 %; strike 112.5 above your stock cost 98.50",
    "accepted earnings: earnings on 2026-11-17 (before the open) fall inside the call's life (by #earnings-ok)",
    "accepted ex_dividend: the ex-dividend date 2026-11-24 (cum 2026-11-23) falls inside the call's life (by "
    "#assign-ok)",
    "after the leg: group delta 135.05 (now 200.00), max loss 19140.00 (now 19700.00), max profit 3360.00 (now "
    "unbounded), breakeven 95.70 (now 98.50)",
    "leg order: after leg 1 you hold 200 WMT shares and 2 short calls (level 1 by the desk's convention)",
    "approval: the highest step needs level 1 by the desk's convention, within your self-attested level 2 (desk.toml); "
    "your broker's own approval for this account decides",
    "candidate 2: the 2027-01-15 115 call | bid 3.55 | extrinsic_per_day 0.0467",
    "candidate 3: the 2026-12-18 115 call | bid 2.10 | extrinsic_per_day 0.0448",
    "failed your criteria: 15 (abs_delta 15, strike_floor 4)",
    MODEL, LIMIT, ACT, FOOTER_1931,
]
SPY_PP_TEXT = [
    SPY_PP_HEADER,
    "candidates for a protective put on the 100 SPY shares: 5 passed your criteria, 7 failed",
    "BEST CANDIDATE 1 of 12 by your objective stress_cost_per_day (the put's ask per calendar day per 100 of loss it "
    "offsets at your stress drop): 0.1856 | the lowest among the 5 candidates that passed every criterion",
    CAVEAT,
    "leg 1 of 1: opens a new long 1 x SPY 2027-03-19 610 put | bid 22.90, ask 23.10, mid 23.00 | quote grade A, " + SOURCE,
    "reasons: 136 calendar days inside your window 120 to 210; floor 5.88 % inside your band 3 to 10; it covers 91.504 "
    "per share (9150.40 in all) at your stress drop of 20 % (stressed price 518.496); spread 0.87 % within your maximum "
    "25 %",
    "after the leg: group delta 69.69 (now 100.00), max loss 4550.00 (now 63240.00), max profit unbounded (now "
    "unbounded), breakeven 655.50 (now 632.40)",
    "leg order: after leg 1 you hold 100 SPY shares and 1 long put (level 2 by the desk's convention)",
    "approval: the highest step needs level 2 by the desk's convention, within your self-attested level 2 (desk.toml); "
    "your broker's own approval for this account decides",
    "candidate 2: the 2027-03-19 620 put | ask 25.65 | stress_cost_per_day 0.1858",
    "candidate 3: the 2027-03-19 625 put | ask 27.05 | stress_cost_per_day 0.1868",
    "failed your criteria: 7 (floor_pct 7)",
    MODEL, LIMIT, ACT, FOOTER_1931,
]
SPY_EX = ("FAILS_CRITERION ex_dividend: the ex-dividend date 2026-12-18 (estimated, cum 2026-12-17) falls inside the "
          "call's life | FAILS_CRITERION ex_dividend: a second ex-date may fall before the call's last trading day; the "
          "calendar holds one")
OUTSIDE = "not paired, outside their bands: 7 puts outside your floor band and 10 calls outside your cap band"
SPY_COLLAR_NONE_TEXT = [
    SPY_COLLAR_HEADER,
    "candidates for a collar on the 100 SPY shares: 0 passed your criteria, 10 failed",
    "no candidate passed every criterion: the desk names no best candidate",
    "candidate 1: the 2027-03-19 620 put and the 2027-03-19 680 call | net credit 0.75 | cap_within_budget 4.92 | "
    + SPY_EX,
    "candidate 2: the 2027-03-19 610 put and the 2027-03-19 680 call | net credit 3.30 | cap_within_budget 4.92 | "
    + SPY_EX,
    "candidate 3: the 2027-03-19 600 put and the 2027-03-19 680 call | net credit 5.60 | cap_within_budget 4.92 | "
    + SPY_EX,
    "failed your criteria: 10 (max_net_debit 6, ex_dividend 10)",
    OUTSIDE, MODEL, LIMIT, ACT, FOOTER_1931,
]
SPY_COLLAR_BEST_TEXT = [
    SPY_COLLAR_HEADER,
    "candidates for a collar on the 100 SPY shares: 4 passed your criteria, 6 failed",
    "BEST CANDIDATE 1 of 10 by your objective cap_within_budget (the cap's distance above the share price, in percent, "
    "among pairs within your net-debit budget): 4.92 | the highest among the 4 candidates that passed every criterion",
    CAVEAT,
    "tie on cap_within_budget broken by the put strike (higher first)",
    "leg 1 of 2: opens a new long 1 x SPY 2027-03-19 620 put | bid 25.45, ask 25.65, mid 25.55 | quote grade A, " + SOURCE,
    "leg 2 of 2: opens a new short 1 x SPY 2027-03-19 680 call | bid 26.40, ask 26.60, mid 26.50 | quote grade A, "
    + SOURCE,
    "reasons: 136 calendar days inside your window 120 to 210; floor 4.34 % inside your band 3 to 10; cap 4.92 % inside "
    "your band 2 to 10; a net credit of 0.75 within your net-debit budget 0.00; spreads 0.78 % and 0.75 % within your "
    "maximum 25 %; call strike 680 above your stock cost 632.40",
    "accepted ex_dividend: the ex-dividend date 2026-12-18 (estimated, cum 2026-12-17) falls inside the call's life (by "
    "#assign-ok)",
    "accepted ex_dividend: a second ex-date may fall before the call's last trading day; the calendar holds one (by "
    "#assign-ok)",
    "after both legs: group delta 24.14 (now 100.00), max loss 1165.00 (now 63240.00), max profit 4835.00 (now "
    "unbounded), breakeven 631.65 (now 632.40)",
    "leg order: leg 1 first; after leg 1 you hold 100 SPY shares and 1 long put (level 2 by the desk's convention); "
    "after leg 2 you hold 100 SPY shares, 1 short call and 1 long put (level 1)",
    "entered as one multi-leg order, some brokers class a collar as a spread, which can need a higher level",
    "approval: the highest step needs level 2 by the desk's convention, within your self-attested level 2 (desk.toml); "
    "your broker's own approval for this account decides",
    "candidate 2: the 2027-03-19 610 put and the 2027-03-19 680 call | net credit 3.30 | cap_within_budget 4.92",
    "candidate 3: the 2027-03-19 600 put and the 2027-03-19 680 call | net credit 5.60 | cap_within_budget 4.92",
    "failed your criteria: 6 (max_net_debit 6)",
    OUTSIDE, MODEL, LIMIT, ACT, FOOTER_1931,
]
PP_NOTHING_TEXT = ["NOTHING_TO_PROPOSE | AG-20261028-03 protective put proposal | the 100 SPY shares already carry 1 "
                   "long put", footer("19:50")]
CC_NOTHING_TEXT = ["NOTHING_TO_PROPOSE | AG-20261020-01 covered call proposal | AG-20261020-01 is a covered call: ask "
                   "for a roll analysis", footer("19:50")]
NO_FREE_SHARES_TEXT = ["NOTHING_TO_PROPOSE | AG-20261020-01 covered call proposal | no free shares: 200 WMT shares are "
                       "covered by 2 short calls; the desk proposes no covered call", footer("19:50")]
COLLAR_REFUSED_TEXT = [
    "REFUSED AG-20261028-03 | SPY collar | opening 1 new long put needs level 2 by the desk's convention; desk.toml "
    "records your self-attested level 1 | the desk shows nothing above your level; your broker's own approval decides "
    "what you can enter",
    footer("19:50"),
]
CC_REFUSED_TEXT = [
    "REFUSED AG-20261020-01 | WMT covered call | opening 2 new short calls, covered by 200 free shares needs level 1 by "
    "the desk's convention; desk.toml records your self-attested level 0 | the desk shows nothing above your level; "
    "your broker's own approval decides what you can enter",
    footer("19:50"),
]
COLLAR_DISABLED_TEXT = ["NOT_APPLICABLE | AG-20261028-03 collar proposal | the collar section is disabled in your "
                        "structuring.toml", footer("19:50")]
CRITERIA_MISSING_TEXT = [
    "UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your criteria (SETUP step 9)",
    footer("19:50"),
]
UNACKNOWLEDGED_TAIL = ("the desk names no candidate and prints no ticket while desk.toml, positions.csv or structuring.toml "
                       "differ from what you acknowledged | acknowledge with ack.py config --owner-tapped")


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
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-propose-tool-")
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)

    def init_structuring(self) -> None:
        code, out = call(desk_init_tool.main, ["--answers", ANSWERS, "--desk-root", self.root, "--json"])
        self.assertEqual(0, code, out)

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

    def drop_row(self, marker: str) -> None:
        with open(self.paths.positions_csv, encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        kept = [line for line in lines if marker not in line]
        self.assertEqual(len(lines) - 1, len(kept))
        with open(self.paths.positions_csv, "w", encoding="utf-8") as handle:
            handle.write("\n".join(kept))

    def argv(self, trade: str, structure: str, now: str = REGULAR_NOW, fixture_set: Optional[str] = None,
             json_mode: bool = True) -> List[str]:
        argv = ["--trade", trade, "--structure", structure, "--offline", "--desk-root", self.root, "--now", now]
        if fixture_set is not None:
            argv += ["--fixture-set", fixture_set]
        return argv + (["--json"] if json_mode else [])

    def run_json(self, trade: str, structure: str, now: str = REGULAR_NOW, fixture_set: Optional[str] = None,
                 expected: int = 0) -> Dict[str, Any]:
        code, out = call(tool.main, self.argv(trade, structure, now, fixture_set))
        self.assertEqual([], order_shaped(out))
        body = json.loads(out)
        self.assertEqual(expected, code, out[:3000])
        return body

    def run_text(self, trade: str, structure: str, now: str = REGULAR_NOW, fixture_set: Optional[str] = None,
                 expected: int = 0) -> List[str]:
        code, out = call(tool.main, self.argv(trade, structure, now, fixture_set, json_mode=False))
        self.assertEqual([], order_shaped(out))
        self.assertEqual(expected, code, out)
        self.assertTrue(out.endswith("\n"))
        return out[:-1].split("\n")

    def journal(self) -> List[Dict[str, Any]]:
        records, status = state.read_jsonl(self.paths.structuring_journal)
        return records if status == "ok" else []

    def reports(self) -> List[str]:
        try:
            return sorted(os.listdir(self.paths.proposals_dir))
        except FileNotFoundError:
            return []

    def ledger(self) -> List[Dict[str, Any]]:
        records, status = state.read_jsonl(os.path.join(self.paths.cache_dir, "ledger.jsonl"))
        return records if status == "ok" else []

    # the shares-only copies of designer Q's D10 (row 4 or row 8 removed), with or without the tags
    def wmt_shares_only(self, tags: bool = False) -> None:
        self.drop_row(WMT_CALL_ROW)
        if tags:
            self.edit("positions.csv", *WMT_SHARES_NOTE)
        self.prepare()

    def spy_shares_only(self, tags: bool = False) -> None:
        self.drop_row(SPY_PUT_ROW)
        if tags:
            self.edit("positions.csv", *SPY_SHARES_ROW)
        self.prepare()


class SchemaTest(unittest.TestCase):
    def test_schema_describes_every_payload_key_without_desk_or_network(self) -> None:
        code, out = call(tool.main, ["--schema"])
        self.assertEqual(0, code)
        self.assertEqual([], order_shaped(out))
        body = json.loads(out)
        self.assertEqual(("propose", "read-only"), (body["tool"], body["mode"]))
        payload = body["envelope"]["payload"]
        self.assertEqual(set(tool.PAYLOAD_KEYS), set(payload))
        self.assertEqual(tuple(structuring.ANSWER_KEYS) + ("approval", "config_changed", "message", "emitted", "lint",
                                                          "withheld", "files", "journal"), tool.PAYLOAD_KEYS)
        self.assertEqual({"0", "1", "2", "10", "20"}, set(body["exit_codes"]))

    def test_the_flags(self) -> None:
        parser = tool.build_parser("0.1.0")
        actions = {option: action for action in parser._actions for option in action.option_strings}
        self.assertTrue({"--trade", "--structure", "--fixture-set", "--json", "--schema", "--timeout", "--desk-root",
                         "--offline", "--now"} <= set(actions))
        self.assertEqual(["covered_call", "protective_put", "collar"], list(actions["--structure"].choices))
        for forbidden in ("--symbol", "--to", "--candidate", "--target"):
            self.assertNotIn(forbidden, actions)


class UsageTest(DeskCopy):
    def usage(self, argv: Sequence[str]) -> None:
        code, out = call(tool.main, list(argv) + ["--json"])
        self.assertEqual(2, code, out)
        body = json.loads(out)
        self.assertEqual(("usage", "invalid_arguments"), (body["status"], body["error"]["code"]))

    def test_usage_errors_exit_2(self) -> None:
        common = ["--offline", "--desk-root", self.root, "--now", REGULAR_NOW]
        self.usage(["--structure", "collar"] + common)
        self.usage(["--trade", SPY] + common)
        self.usage(["--trade", SPY, "--structure", "vertical"] + common)
        self.usage(["--trade", SPY, "--structure", "cash_secured_put"] + common)
        self.usage(["--trade", "SPY", "--structure", "collar"] + common)
        self.usage(["--trade", SPY, "--structure", "collar", "--symbol", "SPY"] + common)
        self.usage(["--trade", SPY, "--structure", "collar", "--desk-root", self.root, "--fixture-set", "regular"])
        self.assertEqual([], self.journal())


class MainFixtureTest(DeskCopy):
    """The main fixture holds no shares-only group: every proposal is NOTHING_TO_PROPOSE, with no request."""

    def setUp(self) -> None:
        super().setUp()
        self.prepare()

    def test_the_protective_put_group(self) -> None:
        self.assertEqual(PP_NOTHING_TEXT, self.run_text(SPY, "protective_put"))
        payload = self.run_json(SPY, "protective_put")["payload"]
        self.assertEqual(("NOTHING_TO_PROPOSE", "not_shares_only", "protective_put", 0),
                         (payload["verdict"], payload["cause"], payload["structure_requested"],
                          payload["requests"]["made"]))
        self.assertIsNone(payload["approval"])
        self.assertEqual([], self.ledger())

    def test_the_covered_call_group(self) -> None:
        self.assertEqual(CC_NOTHING_TEXT, self.run_text(WMT, "covered_call"))
        payload = self.run_json(WMT, "covered_call")["payload"]
        self.assertEqual(("NOTHING_TO_PROPOSE", 0), (payload["verdict"], payload["requests"]["made"]))
        self.assertEqual(2, len(self.reports()), "a complete answer (exit 0) is a report")

    def test_a_collar_on_a_covered_call_group(self) -> None:
        payload = self.run_json(NVDA, "collar")["payload"]
        self.assertEqual(("NOTHING_TO_PROPOSE", "not_shares_only"), (payload["verdict"], payload["cause"]))


class GoldenTest(DeskCopy):
    def test_wmt_covered_call_without_the_tags_is_none_qualifies(self) -> None:
        self.wmt_shares_only()
        self.assertEqual(WMT_NONE_TEXT, self.run_text(WMT, "covered_call"))
        payload = self.run_json(WMT, "covered_call")["payload"]
        self.assertEqual(("NONE_QUALIFIES", None, 0, 20, "long_stock"),
                         (payload["verdict"], payload["best"], payload["passed"], payload["failed"],
                          payload["structure"]))

    def test_wmt_covered_call_with_the_tags_is_best(self) -> None:
        self.wmt_shares_only(tags=True)
        body = self.run_json(WMT, "covered_call")
        self.assertEqual(WMT_BEST_TEXT, self.run_text(WMT, "covered_call"))
        payload = body["payload"]
        self.assertEqual(("ok", "BEST", "WMT261218C00112500", "covered_call", "covered_call", 2, "200"),
                         (body["status"], payload["verdict"], payload["best"], payload["structure_requested"],
                          payload["section"], payload["contracts"], payload["shares"]))
        best = payload["candidates"][0]
        self.assertEqual(("0.0597", "560.00", "ELIGIBLE"), (best["score"], best["credit_total"], best["status"]))
        leg = best["legs"][0]
        self.assertEqual(("2.80", "2.90", "2.85", "B", "3.51", "0.32", 45, 32),
                         (leg["bid"], leg["ask"], leg["mid"], leg["grade"], leg["spread_pct"], leg["abs_delta"],
                          leg["dte_calendar"], leg["dte_trading"]))
        self.assertEqual({"max_loss": "19140.00", "max_profit": "3360.00", "breakeven": "95.70", "group_delta": 135.05},
                         {"max_loss": payload["after"]["max_loss"], "max_profit": payload["after"]["max_profit"],
                          "breakeven": payload["after"]["breakeven"],
                          "group_delta": round(payload["after"]["group_delta"], 2)})
        self.assertEqual([["#earnings-ok", "crosses_earnings"], ["#assign-ok", "crosses_ex_dividend"]],
                         [[label["by"], label["code"]] for label in best["accepted"]])
        self.assertEqual({"stage": "best_candidate", "ok": True, "owner_level": 2, "required_level": 1},
                         {k: payload["approval"][k] for k in ("stage", "ok", "owner_level", "required_level")})
        self.assertEqual({"made": 4, "chains": 2, "per_run_max_requests": 20, "per_run_max_chains": 4,
                          "budget_left": 51}, payload["requests"])
        self.assertEqual("\n".join(WMT_BEST_TEXT), payload["message"])
        self.assertTrue(payload["lint"]["ok"])

    def test_spy_protective_put_is_best(self) -> None:
        self.spy_shares_only()
        payload = self.run_json(SPY, "protective_put")["payload"]
        self.assertEqual(SPY_PP_TEXT, self.run_text(SPY, "protective_put"))
        self.assertEqual(("BEST", "SPY270319P00610000", 1), (payload["verdict"], payload["best"], payload["contracts"]))
        self.assertEqual({"drop_pct": "20", "spot": "518.496"}, payload["stress"])
        best = payload["candidates"][0]
        self.assertEqual(("0.1856", "5.88", "91.504", "9150.40", "2310.00"),
                         (best["score"], best["floor_pct"], best["covered_per_share"], best["covered_total"],
                          best["cost_total"]))
        self.assertEqual(("SPY270319P00620000", "ELIGIBLE", "0.1858"),
                         (payload["candidates"][1]["id"], payload["candidates"][1]["status"],
                          payload["candidates"][1]["score"]))
        self.assertEqual(("4550.00", "655.50"), (payload["after"]["max_loss"], payload["after"]["breakeven"]))
        self.assertEqual((3, 1), (payload["requests"]["made"], payload["requests"]["chains"]))

    def test_spy_collar_without_the_tag_is_none_qualifies(self) -> None:
        self.spy_shares_only()
        self.assertEqual(SPY_COLLAR_NONE_TEXT, self.run_text(SPY, "collar"))

    def test_spy_collar_with_assign_ok_is_best(self) -> None:
        self.spy_shares_only(tags=True)
        self.assertEqual(SPY_COLLAR_BEST_TEXT, self.run_text(SPY, "collar"))
        payload = self.run_json(SPY, "collar")["payload"]
        self.assertEqual(("BEST", "SPY270319P00620000+SPY270319C00680000", "put_strike"),
                         (payload["verdict"], payload["best"], payload["tie_break"]))
        best = payload["candidates"][0]
        self.assertEqual(("-0.75", "-75.00", "4.34", "4.92"),
                         (best["net_debit"], best["net_debit_total"], best["floor_pct"], best["cap_pct"]))
        self.assertEqual([["crosses_ex_dividend", "#assign-ok"], ["dividend_unknown", "#assign-ok"]],
                         [[label["code"], label["by"]] for label in best["accepted"]])
        self.assertEqual(("1165.00", "4835.00", "631.65"),
                         (payload["after"]["max_loss"], payload["after"]["max_profit"], payload["after"]["breakeven"]))
        self.assertEqual({"puts": 7, "calls": 10}, payload["outside_bands"])
        self.assertEqual([1, 2], [step["index"] for step in payload["approval"]["steps"]])
        self.assertEqual([2, 1], [step["level"] for step in payload["approval"]["steps"]])

    def test_the_owner_typed_account_name_is_never_printed(self) -> None:
        self.spy_shares_only(tags=True)
        for structure in ("protective_put", "collar"):
            text = "\n".join(self.run_text(SPY, structure))
            self.assertNotIn("Roth", text)
            self.assertIsNone(structuring_text.OCC_RE.search(text))


class ApplicabilityTest(DeskCopy):
    def test_refused_at_a_lower_acknowledged_level_before_any_request(self) -> None:
        self.drop_row(SPY_PUT_ROW)
        self.edit("desk.toml", "approval_level = 2 ", "approval_level = 1 ")
        self.prepare()
        self.assertEqual(COLLAR_REFUSED_TEXT, self.run_text(SPY, "collar", expected=20))
        payload = self.run_json(SPY, "collar", expected=20)["payload"]
        self.assertEqual(("REFUSED", "approval_level", [], None, 0),
                         (payload["verdict"], payload["cause"], payload["candidates"], payload["best"],
                          payload["requests"]["made"]))
        self.assertEqual({"stage": "before_market_data", "ok": False, "owner_level": 1, "required_level": 2,
                          "refusal_step": 1},
                         {k: payload["approval"][k] for k in ("stage", "ok", "owner_level", "required_level",
                                                             "refusal_step")})
        self.assertEqual([], self.ledger())
        self.assertEqual([], self.reports())
        self.assertEqual(("REFUSED", "approval_level", "collar"),
                         (self.journal()[-1]["verdict"], self.journal()[-1]["refused_code"],
                          self.journal()[-1]["structure"]))
        # a covered call needs level 1 only: it passes the check and goes on to the market data (the regular set holds
        # no 2027-01-15 SPY chain, designer Q's D2, so the in-window chain is not served)
        payload = self.run_json(SPY, "covered_call", expected=20)["payload"]
        self.assertEqual(("UNAVAILABLE", "chain_not_served", True, 1),
                         (payload["verdict"], payload["cause"], payload["approval"]["ok"],
                          payload["approval"]["required_level"]))
        self.assertGreater(payload["requests"]["made"], 0)

    def test_a_covered_call_at_level_0(self) -> None:
        self.drop_row(WMT_CALL_ROW)
        self.edit("desk.toml", "approval_level = 2 ", "approval_level = 0 ")
        self.prepare()
        self.assertEqual(CC_REFUSED_TEXT, self.run_text(WMT, "covered_call", expected=20))
        self.assertEqual([], self.ledger())

    def test_a_disabled_section_is_not_applicable(self) -> None:
        self.drop_row(SPY_PUT_ROW)
        self.init_structuring()
        self.edit("structuring.toml", "[collar]\nenabled = true", "[collar]\nenabled = false")
        self.ack()
        self.assertEqual(COLLAR_DISABLED_TEXT, self.run_text(SPY, "collar"))
        payload = self.run_json(SPY, "collar")["payload"]
        self.assertEqual(("NOT_APPLICABLE", "disabled", 0), (payload["verdict"], payload["cause"],
                                                             payload["requests"]["made"]))

    def test_no_free_shares(self) -> None:
        # the WMT call moved to a trade of its own: the 200 shares are a shares-only group, but the account's short
        # calls on WMT already rely on them (coverage is per account, whatever the group)
        self.edit("positions.csv", "Brokerage (margin),AG-20261020-01,2026-10-20,-684.00",
                  "Brokerage (margin),AG-20261020-02,2026-10-20,-684.00")
        self.prepare()
        self.assertEqual(NO_FREE_SHARES_TEXT, self.run_text(WMT, "covered_call"))
        payload = self.run_json(WMT, "covered_call")["payload"]
        self.assertEqual(("NOTHING_TO_PROPOSE", "no_free_shares", 0), (payload["verdict"], payload["cause"],
                                                                       payload["requests"]["made"]))


class FailClosedTest(DeskCopy):
    def test_criteria_missing(self) -> None:
        self.drop_row(SPY_PUT_ROW)
        self.ack()
        self.assertEqual(CRITERIA_MISSING_TEXT, self.run_text(SPY, "protective_put", expected=20))
        payload = self.run_json(SPY, "protective_put", expected=20)["payload"]
        self.assertEqual(("UNAVAILABLE", "criteria_missing", "protective_put"),
                         (payload["verdict"], payload["cause"], payload["structure_requested"]))
        self.assertEqual([], self.ledger())
        self.assertEqual([], self.reports())
        self.assertEqual(["criteria_missing", "criteria_missing"], [r["refused_code"] for r in self.journal()])

    def test_positions_csv_edited_after_the_acknowledgement(self) -> None:
        self.spy_shares_only()
        before = sha256_file(self.paths.positions_csv)
        self.edit("positions.csv", *SPY_SHARES_ROW)
        after = sha256_file(self.paths.positions_csv)
        expected = ["UNAVAILABLE | CONFIG CHANGED: positions.csv: sha %s -> sha %s | %s" % (before[:8], after[:8],
                                                                                        UNACKNOWLEDGED_TAIL),
                    footer("19:50")]
        self.assertEqual(expected, self.run_text(SPY, "collar", expected=20))
        payload = self.run_json(SPY, "collar", expected=20)["payload"]
        self.assertEqual(("UNAVAILABLE", "config_unacknowledged"), (payload["verdict"], payload["cause"]))
        self.assertEqual([], self.ledger())

    def test_structuring_invalid_is_exit_1(self) -> None:
        self.drop_row(SPY_PUT_ROW)
        self.init_structuring()
        self.edit("structuring.toml", "stress_drop_pct = 20", "stress_drop_pct = 5")
        self.ack()
        body = self.run_json(SPY, "protective_put", expected=1)
        self.assertEqual(("error", "structuring_invalid"), (body["status"], body["error"]["code"]))
        self.assertEqual([], self.journal())

    def test_an_unknown_trade_is_exit_1(self) -> None:
        self.prepare()
        body = self.run_json("AG-20991231-01", "collar", expected=1)
        self.assertEqual(("error", "unknown_trade"), (body["status"], body["error"]["code"]))
        self.assertEqual([], self.journal())


class MarketDataTest(DeskCopy):
    def test_the_stale_set_is_unavailable_after_one_request(self) -> None:
        self.spy_shares_only()
        lines = self.run_text(SPY, "protective_put", fixture_set="stale", expected=20)
        self.assertEqual(["UNAVAILABLE | AG-20261028-03 protective put proposal | no usable quote of the underlying",
                          footer("19:50")], lines)
        self.assertEqual(1, len(self.ledger()))
        self.assertEqual([], self.reports())

    def test_a_holiday_makes_no_request(self) -> None:
        self.spy_shares_only()
        lines = self.run_text(SPY, "collar", now=HOLIDAY_NOW, fixture_set="holiday", expected=20)
        self.assertEqual(["UNAVAILABLE | AG-20261028-03 collar proposal | the market is closed today",
                          alert_lint.structuring_footer_line(contract.parse_utc_instant(HOLIDAY_NOW))], lines)
        self.assertEqual([], self.ledger())

    def test_every_request_is_a_get_through_the_read_only_transport(self) -> None:
        self.spy_shares_only(tags=True)
        seen: List[str] = []
        original = tradier_client.OfflineTransport.fetch

        def recording(transport: Any, url: str, headers: Dict[str, str], timeout_s: float) -> Any:
            seen.append(url)
            return original(transport, url, headers, timeout_s)

        with mock.patch.object(tradier_client.OfflineTransport, "fetch", autospec=True, side_effect=recording), \
                mock.patch("http.client.HTTPConnection.request", side_effect=AssertionError("no socket offline")):
            payload = self.run_json(SPY, "collar")["payload"]
        paths = [url.split("?")[0].split("sandbox.tradier.com", 1)[-1] for url in seen]
        self.assertEqual(["/v1/markets/quotes", "/v1/markets/options/expirations", "/v1/markets/options/chains"], paths)
        self.assertTrue(set(paths) <= set(tradier_client.ALLOWED_PATHS))
        self.assertEqual({"made": 3, "chains": 1, "per_run_max_requests": 20, "per_run_max_chains": 4,
                          "budget_left": 52}, payload["requests"])
        self.assertEqual(paths, [record["path"] for record in self.ledger()])

    def test_now_is_honoured_and_no_wall_clock_is_read(self) -> None:
        self.spy_shares_only()
        with mock.patch("aegis_desk.state.utc_now", side_effect=AssertionError("the wall clock was read")):
            body = self.run_json(SPY, "protective_put")
        self.assertEqual("2026-11-03T19:50:00+00:00", body["generated_at"])


class RecordTest(DeskCopy):
    def setUp(self) -> None:
        super().setUp()
        self.spy_shares_only()

    def test_the_report_files_and_the_journal_record(self) -> None:
        text = self.run_text(SPY, "protective_put")
        stem = "AG-20261028-03-protective_put-20261103T195000Z"
        self.assertEqual([stem + ".json", stem + ".md"], self.reports())
        with open(os.path.join(self.paths.proposals_dir, stem + ".md"), encoding="utf-8") as handle:
            self.assertEqual("# AG-20261028-03 protective put proposal (20261103T195000Z)\n\n```text\n%s\n```\n"
                             % "\n".join(text), handle.read())
        with open(os.path.join(self.paths.proposals_dir, stem + ".json"), encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(set(tool.PAYLOAD_KEYS) - set(tool.REPORT_OMITTED_KEYS), set(report["payload"]))
        self.assertEqual(("ok", "SPY270319P00610000"), (report["status"], report["payload"]["best"]))
        record = self.journal()[0]
        self.assertEqual({
            "utc": "2026-11-03T19:50:00+00:00", "tool": "propose", "action": None, "structure": "protective_put",
            "trade_id": SPY, "verdict": "BEST", "criteria_sha256": CRITERIA_SHA,
            "desk_toml_sha256": sha256_file(self.paths.desk_toml),
            "positions_csv_sha256": sha256_file(self.paths.positions_csv), "best_candidate": "SPY270319P00610000",
            "candidates_passed": 5, "candidates_failed": 7, "refused_code": None,
            "text_sha256": hashlib.sha256("\n".join(text).encode("utf-8")).hexdigest()}, record)

    def test_a_rerun_is_idempotent_and_files_are_never_overwritten(self) -> None:
        first = self.run_json(SPY, "collar")["payload"]
        self.assertEqual({"json": "reports/proposals/AG-20261028-03-collar-20261103T195000Z.json",
                          "md": "reports/proposals/AG-20261028-03-collar-20261103T195000Z.md", "written": True},
                         first["files"])
        second = self.run_json(SPY, "collar")["payload"]
        self.assertEqual((0, False), (second["requests"]["made"], second["files"]["written"]))
        path = os.path.join(self.root, first["files"]["md"])
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("an edit\n")
        body = self.run_json(SPY, "collar", expected=1)
        self.assertEqual(("error", "ticket_exists"), (body["status"], body["error"]["code"]))
        self.assertEqual(2, len(self.journal()))

    def test_a_text_that_fails_the_lint_is_withheld(self) -> None:
        with mock.patch.object(structuring_text, "proposal_lines",
                               side_effect=lambda w, answer, level: ["PROPOSAL | the desk wrote 7.77 here"]):
            code, out = call(tool.main, self.argv(SPY, "protective_put", json_mode=False))
            body_code, body_out = call(tool.main, self.argv(SPY, "protective_put"))
        self.assertEqual((1, 1), (code, body_code))
        self.assertTrue(out.startswith("WITHHELD | the text of propose.py failed the alert lint ("), out)
        payload = json.loads(body_out)["payload"]
        self.assertIn("number_not_emitted", payload["withheld"]["codes"])
        self.assertEqual(out[:-1], payload["message"])
        self.assertEqual([], self.reports())
        self.assertEqual([], self.journal())


if __name__ == "__main__":
    unittest.main()
