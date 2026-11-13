"""``scripts/exit_ticket.py``: a ticket for a position you hold (PR 3b; designer Q's D11-D13, designer C's D3, D6, D7
and D15 under the addendum S1, S2, S5-S11).

Every run is in-process (``main(argv)``) on a copy of ``tests/fixtures/desk`` that holds the fixture criteria
(``tests/fixtures/init/expected_structuring.toml``) and whose configuration ``ack.py config --owner-tapped`` acknowledged
at 19:49 UTC. Pinned here: designer Q's D16 ticket goldens under S17 with their full text (WMT CLOSE, ROLL with the
2027-01-15 115 call as the replacement, UNWIND_STOCK, LET_ASSIGN, MONETIZE_PUT not applicable; the NVDA ROLL with the
2027-01-15 160 call, a TICKET with two FAILS_CRITERION lines and exit 0; SPY MONETIZE_PUT, UNWIND_STOCK, ROLL with the
2027-03-19 610 put, LET_ASSIGN not applicable), the expiry-day WMT CLOSE at grade E, the order of C D3.6 (CONFIG CHANGED first, then the
criteria file, then the approval-level check, then market data), every usage error, replacement_not_listed,
market_closed, the ticket files (only on exit 0, idempotent, never overwritten), the journal record of every printed
text, the WITHHELD line, the request counts, ``--now`` and that every request is an allow-listed GET.
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
from typing import Any, Dict, List, Sequence, Tuple
from unittest import mock
from urllib.parse import urlsplit

from aegis_desk import (alert_lint, alpaca_client, contract, desk_paths, market_clock, quote_cache, structuring_text,
                        tickets)

import ack as ack_tool
import exit_check as exit_check_tool
import exit_ticket

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
CRITERIA_FILE = os.path.join(ROOT, "tests", "fixtures", "init", "expected_structuring.toml")
REGULAR_NOW = "2026-11-03T19:50:00Z"
ACK_NOW = "2026-11-03T19:49:00Z"
AFTER_CLOSE_NOW = "2026-11-03T21:25:00Z"
EXPIRY_DAY_NOW = "2026-11-20T19:50:00Z"
HOLIDAY_NOW = "2026-11-26T15:00:00Z"
WMT, NVDA, SPY = "AG-20261020-01", "AG-20261022-02", "AG-20261028-03"
FOOTER = ("computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for "
          "positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of ")
TAIL = ("you supply the limit; the desk prints none",
        "valid until 20:20 UTC: after that its quotes are too old, and a new ticket needs a new request",
        "after a fill, tell Aegis Position Control the fill; it proposes the positions.csv lines",
        "if you decide to act, you type it into your brokerage account yourself and you supply the limit",
        FOOTER + "19:31 UTC")
MODEL = "prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style options is not modelled"
APPROVAL = ("approval: the highest step needs level %d by the desk's convention, within your self-attested level 2 "
            "(desk.toml); your broker's own approval for this account decides")
MULTI = "entered as one multi-leg order, some brokers class a roll as a spread, which can need a higher level"
# an unwind is shares and options together, not a roll (reviews L1-5, L2-6, L4-5)
MULTI_UNWIND = ("entered as one multi-leg order, some brokers class shares and options together as a combination, "
                "which can need a higher level")
QUOTE = "quote grade %s, Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC"
WMT_HEAD = "TICKET AG-20261020-01 | WMT covered call | %s (action %s) | account 1 of desk.toml (margin) | computed 19:50 UTC, valid until 20:20 UTC (30 min)"
SPY_HEAD = "TICKET AG-20261028-03 | SPY protective put | %s (action %s) | account 2 of desk.toml (ira) | computed 19:50 UTC, valid until 20:20 UTC (30 min)"
WMT_CALL_LEG = "leg 1 of %d: closes your short 2 x WMT 2026-11-20 105 call | bid 3.35, ask 3.50, mid 3.425 | " + QUOTE % "B"
SPY_PUT_LEG = "leg %d of %d: closes your long 1 x SPY 2026-12-18 620 put | bid 9.80, ask 10.10, mid 9.95 | " + QUOTE % "B"

GOLDEN_TEXTS: Dict[str, Tuple[Tuple[str, ...], int, Tuple[str, ...]]] = {
    "wmt_close": ((WMT, "CLOSE"), 0, (
        WMT_HEAD % ("the option legs closed", "CLOSE"),
        WMT_CALL_LEG % 1,
        "leg order: after leg 1 you hold 200 WMT shares and no option (level 0 by the desk's convention)",
        APPROVAL % 0,
        "cash: -700.00 at the conservative prices (mid -685.00); the most the steps need at once: 700.00; the desk does "
        "not see your cash balance",
        "realized on the closing leg: -16.00 at the conservative side (mid -1.00)") + TAIL),
    "wmt_roll": ((WMT, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "115"), 0, (
        WMT_HEAD % ("a roll of the short call", "ROLL"),
        WMT_CALL_LEG % 2,
        "leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55, ask 3.65, mid 3.60 | " + QUOTE % "B",
        "leg order: leg 1 first; after leg 1 you hold 200 WMT shares and no option (level 0 by the desk's convention); "
        "after leg 2 you hold 200 WMT shares and 2 short calls (level 1)",
        "in the other order, after leg 2 you would hold 2 uncovered calls (level 4)",
        MULTI, APPROVAL % 1,
        "cash: +10.00 at the conservative prices (mid +35.00); the most the steps need at once: 700.00; the desk does "
        "not see your cash balance",
        "realized on the closing leg: -16.00 at the conservative side (mid -1.00)",
        "accepted ex_dividend: the ex-dividend date 2026-11-24 (cum 2026-11-23) falls inside the new call's life (by "
        "#assign-ok)",
        "after both legs: group delta 134.07, max loss 19006.00, max profit 3994.00, breakeven 95.03",
        MODEL) + TAIL),
    "wmt_unwind": ((WMT, "UNWIND_STOCK"), 0, (
        WMT_HEAD % ("the shares and every option leg closed", "UNWIND_STOCK"),
        WMT_CALL_LEG % 2,
        "leg 2 of 2: closes 200 of your long WMT shares | bid 104.20, ask 104.26, mid 104.23 | " + QUOTE % "A",
        "leg order: leg 1 first; after leg 1 you hold 200 WMT shares and no option (level 0 by the desk's convention); "
        "after leg 2 you hold no WMT shares and no option (level 0)",
        "in the other order, after leg 2 you would hold 2 uncovered calls (level 4)",
        MULTI_UNWIND, APPROVAL % 0,
        "cash: +20140.00 at the conservative prices (mid +20161.00); the most the steps need at once: 700.00; the desk "
        "does not see your cash balance",
        "realized on the closing legs: +1124.00 at the conservative side (mid +1145.00)") + TAIL),
    "wmt_let_assign": ((WMT, "LET_ASSIGN"), 0, (
        WMT_HEAD % ("the short option left to expiry", "LET_ASSIGN"),
        "no leg to enter: if the 105 call is at least 0.01 in the money at expiry, OCC exercises it by exception; your "
        "broker's cutoff is 16:30 ET (desk.toml)",
        "the 2026-11-20 105 call is out of the money now: the spot mid 104.23 is below its strike",
        # the short leg can be closed only in the session: its close, not the broker's exercise cutoff (L1-2, L2-5)
        "if you decide otherwise, you act by Fri 2026-11-20 16:00 ET (21:00 UTC): a short option left to expiry can be "
        "closed only while the session trades",
        "if assigned: 200 WMT shares are called away at the strike, cash +21000.00; realized on the call and the shares "
        "+1984.00",
        "the ex-dividend date 2026-11-24 falls after the call's last trading day 2026-11-20",
        "valid until 20:20 UTC: after that its quotes are too old, and a new ticket needs a new request",
        "after expiry, the post-expiry check of Aegis Position Control proposes the positions.csv lines",
        FOOTER + "19:31 UTC")),
    "wmt_monetize": ((WMT, "MONETIZE_PUT"), 0, (
        "NOT_APPLICABLE | AG-20261020-01 MONETIZE_PUT ticket | AG-20261020-01 is a covered call: the put of a protective "
        "put or a collar is what MONETIZE_PUT closes",
        FOOTER + "19:50 UTC")),
    "nvda_roll": ((NVDA, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "160"), 0, (
        "TICKET AG-20261022-02 | NVDA covered call | a roll of the short call (action ROLL) | account 1 of desk.toml "
        "(margin) | computed 19:50 UTC, valid until 20:20 UTC (30 min)",
        "leg 1 of 2: closes your short 2 x NVDA 2026-11-20 150 call | bid 3.10, ask 3.30, mid 3.20 | " + QUOTE % "C",
        "leg 2 of 2: opens a new short 2 x NVDA 2027-01-15 160 call | bid 3.25, ask 3.35, mid 3.30 | " + QUOTE % "B",
        "leg order: leg 1 first; after leg 1 you hold 200 NVDA shares and no option (level 0 by the desk's convention); "
        "after leg 2 you hold 200 NVDA shares and 2 short calls (level 1)",
        "in the other order, after leg 2 you would hold 2 uncovered calls (level 4)",
        MULTI, APPROVAL % 1,
        "cash: -10.00 at the conservative prices (mid +20.00); the most the steps need at once: 660.00; the desk does "
        "not see your cash balance",
        "realized on the closing leg: -50.00 at the conservative side (mid -30.00)",
        "FAILS_CRITERION credit_only: a net debit of 0.05",
        "FAILS_CRITERION ex_dividend: the ex-dividend date 2026-12-03 (cum 2026-12-02) falls inside the new call's life",
        "after both legs: group delta 137.35, max loss 27640.00, max profit 4360.00, breakeven 138.20",
        MODEL) + TAIL),
    "spy_monetize": ((SPY, "MONETIZE_PUT"), 0, (
        SPY_HEAD % ("the long put closed for its value", "MONETIZE_PUT"),
        SPY_PUT_LEG % (1, 1),
        "leg order: after leg 1 you hold 100 SPY shares and no option (level 0 by the desk's convention)",
        APPROVAL % 0,
        "cash: +980.00 at the conservative prices (mid +995.00); the most the steps need at once: 0.00; the desk does "
        "not see your cash balance",
        "realized on the closing leg: -150.00 at the conservative side (mid -135.00)",
        "the 2026-12-18 620 put is out of the money: the spot mid 648.12 is above its strike, so its bid 9.80 is all "
        "time value",
        "after the leg, your 100 SPY shares carry no put: their downside is unhedged") + TAIL),
    "spy_unwind": ((SPY, "UNWIND_STOCK"), 0, (
        SPY_HEAD % ("the shares and every option leg closed", "UNWIND_STOCK"),
        "leg 1 of 2: closes 100 of your long SPY shares | bid 648.10, ask 648.14, mid 648.12 | " + QUOTE % "A",
        SPY_PUT_LEG % (2, 2),
        "leg order: leg 1 first; after leg 1 you hold no SPY shares and 1 long put (level 0 by the desk's convention); "
        "after leg 2 you hold no SPY shares and no option (level 0)",
        MULTI_UNWIND, APPROVAL % 0,
        "cash: +65790.00 at the conservative prices (mid +65807.00); the most the steps need at once: 0.00; the desk "
        "does not see your cash balance",
        "realized on the closing legs: +1420.00 at the conservative side (mid +1437.00)") + TAIL),
    "spy_roll": ((SPY, "ROLL", "--new-expiration", "2027-03-19", "--new-strike", "610"), 0, (
        SPY_HEAD % ("a roll of the long put", "ROLL"),
        "leg 1 of 2: opens a new long 1 x SPY 2027-03-19 610 put | bid 22.90, ask 23.10, mid 23.00 | " + QUOTE % "A",
        SPY_PUT_LEG % (2, 2),
        "leg order: leg 1 first; after leg 1 you hold 100 SPY shares and 2 long puts (level 2 by the desk's "
        "convention); after leg 2 you hold 100 SPY shares and 1 long put (level 0)",
        MULTI, APPROVAL % 2,
        "cash: -1330.00 at the conservative prices (mid -1305.00); the most the steps need at once: 2310.00; the desk "
        "does not see your cash balance",
        "realized on the closing leg: -150.00 at the conservative side (mid -135.00)",
        "after both legs: group delta 69.69, max loss 4700.00, max profit unbounded, breakeven 657.00",
        MODEL) + TAIL),
    "spy_let_assign": ((SPY, "LET_ASSIGN"), 0, (
        "NOT_APPLICABLE | AG-20261028-03 LET_ASSIGN ticket | AG-20261028-03 holds no short option: only a short option "
        "can be left to assignment",
        FOOTER + "19:50 UTC")),
}

EXPIRY_DAY_CLOSE: Tuple[str, ...] = (
    WMT_HEAD % ("the option legs closed", "CLOSE"),
    "leg 1 of 1: closes your short 2 x WMT 2026-11-20 105 call | bid 0.12, ask 0.16, mid 0.14 | " + QUOTE % "E"
    + " | quote grade E: the conservative side stands",
    "leg order: after leg 1 you hold 200 WMT shares and no option (level 0 by the desk's convention)",
    APPROVAL % 0,
    "cash: -32.00 at the conservative prices (mid -28.00); the most the steps need at once: 32.00; the desk does not "
    "see your cash balance",
    "realized on the closing leg: +652.00 at the conservative side (mid +656.00)") + TAIL


def call(main: Any, argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            result = main(list(argv))
        except SystemExit as exc:  # argparse usage errors exit through the parser
            result = exc.code
    return result, buffer.getvalue()


def sha256_file(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class DeskCase(unittest.TestCase):
    """A copy of the fixture desk with the fixture criteria, acknowledged at 19:49 UTC (``acknowledge=False`` skips the
    acknowledgement, ``criteria=False`` the criteria file)."""

    acknowledge = True
    criteria = True

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-exit-ticket-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)
        if self.criteria:
            shutil.copyfile(CRITERIA_FILE, self.paths.structuring_toml)
        if self.acknowledge:
            self.ack()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def ack(self, now: str = ACK_NOW) -> None:
        code, out = call(ack_tool.main, ["config", "--owner-tapped", "--offline", "--desk-root", self.root, "--now", now,
                                         "--json"])
        self.assertEqual(0, code, out)

    def screen(self, out: str) -> None:
        self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(out)], out)
        self.assertNotIn("Brokerage (margin)", "" if out.lstrip().startswith("{") else out)

    def argv(self, trade: str, action: str, *extra: str, now: str = REGULAR_NOW, fixture_set: str = "regular",
             json_mode: bool = True) -> List[str]:
        argv = ["--trade", trade, "--action", action] + list(extra) + ["--offline", "--desk-root", self.root, "--now", now,
                                                                       "--fixture-set", fixture_set]
        return argv + (["--json"] if json_mode else [])

    def run_json(self, trade: str, action: str, *extra: str, **kwargs: Any) -> Tuple[int, Dict[str, Any]]:
        code, out = call(exit_ticket.main, self.argv(trade, action, *extra, **kwargs))
        self.screen(out)
        return code, json.loads(out)

    def run_text(self, trade: str, action: str, *extra: str, **kwargs: Any) -> Tuple[int, str]:
        code, out = call(exit_ticket.main, self.argv(trade, action, *extra, json_mode=False, **kwargs))
        self.screen(out)
        return code, out

    def journal(self) -> List[Dict[str, Any]]:
        try:
            with open(self.paths.structuring_journal, encoding="utf-8") as handle:
                return [json.loads(line) for line in handle if line.strip()]
        except FileNotFoundError:
            return []

    def ticket_files(self) -> List[str]:
        try:
            return sorted(os.listdir(self.paths.tickets_dir))
        except FileNotFoundError:
            return []


# ------------------------------------------------------------------------------------------------
# --schema
# ------------------------------------------------------------------------------------------------


class SchemaTest(unittest.TestCase):
    def test_schema_describes_every_payload_key(self) -> None:
        code, out = call(exit_ticket.main, ["--schema"])
        self.assertEqual(0, code)
        body = json.loads(out)
        self.assertEqual("exit_ticket", body["tool"])
        self.assertEqual(list(exit_ticket.PAYLOAD_KEYS), sorted(body["envelope"]["payload"], key=exit_ticket.PAYLOAD_KEYS.index))
        self.assertEqual(set(exit_ticket.PAYLOAD_KEYS), set(body["envelope"]["payload"]))
        self.assertIn("GET", body["network_access"])
        self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(out)])
        self.assertEqual(tickets.TICKET_KEYS, exit_ticket.PAYLOAD_KEYS[:len(tickets.TICKET_KEYS)])

    def test_the_flags(self) -> None:
        parser = exit_ticket.build_parser("0.1.0")
        flags = {option for action in parser._actions for option in action.option_strings}
        for flag in ("--trade", "--action", "--new-expiration", "--new-strike", "--fixture-set", "--json", "--schema",
                     "--timeout", "--desk-root", "--offline", "--now"):
            self.assertIn(flag, flags)
        self.assertFalse([flag for flag in flags if "candidate" in flag or "--to" == flag])


# ------------------------------------------------------------------------------------------------
# Designer Q's D16 ticket goldens (S17), full text
# ------------------------------------------------------------------------------------------------


class GoldenTextTest(DeskCase):
    def test_every_golden_text(self) -> None:
        for name, (args, exit_code, lines) in GOLDEN_TEXTS.items():
            with self.subTest(name=name):
                code, out = self.run_text(*args)
                self.assertEqual(exit_code, code, out)
                self.assertEqual("\n".join(lines) + "\n", out)
                self.assertIsNone(structuring_text.OCC_RE.search(out))

    def test_the_json_carries_the_same_text_and_its_lint(self) -> None:
        for name in ("wmt_roll", "nvda_roll", "spy_monetize", "wmt_let_assign", "wmt_monetize"):
            args, exit_code, lines = GOLDEN_TEXTS[name]
            with self.subTest(name=name):
                code, body = self.run_json(*args)
                self.assertEqual(exit_code, code)
                payload = body["payload"]
                self.assertEqual(set(exit_ticket.PAYLOAD_KEYS), set(payload))
                self.assertEqual("\n".join(lines), payload["message"])
                self.assertEqual("ok", body["status"])
                self.assertEqual({"ok": True, "problems": [], "numbers_checked": payload["lint"]["numbers_checked"]},
                                 payload["lint"])
                self.assertEqual("structuring", payload["emitted"]["footer"])
                self.assertEqual([], payload["emitted"]["rule_ids"])
                result = alert_lint.check(payload["message"], payload["emitted"]["emitted_numbers"], rule_ids=[],
                                          identifiers=payload["emitted"]["identifiers"],
                                          clocks=payload["emitted"]["clocks"],
                                          as_of=contract.parse_utc_instant(payload["emitted"]["as_of"]),
                                          footer="structuring")
                self.assertTrue(result.ok, result.problems)

    def test_the_wmt_roll_payload(self) -> None:
        code, body = self.run_json(*GOLDEN_TEXTS["wmt_roll"][0])
        payload = body["payload"]
        self.assertEqual((0, "TICKET", "TK-AG-20261020-01-ROLL-20261103T195000Z", "covered_call", "margin"),
                         (code, payload["verdict"], payload["ticket_id"], payload["structure"], payload["account_type"]))
        self.assertEqual(["close_short", "open_short"], [leg["intent"] for leg in payload["legs"]])
        self.assertEqual(["WMT261120C00105000", "WMT270115C00115000"], [leg["contract"] for leg in payload["legs"]])
        self.assertEqual([("ask", "3.50", "-700.00", "-685.00"), ("bid", "3.55", "710.00", "720.00")],
                         [(leg["conservative_side"], leg["conservative"], leg["cash_conservative"], leg["cash_mid"])
                          for leg in payload["legs"]])
        self.assertEqual(["0.10", "0.10"], [leg["tick"] for leg in payload["legs"]])  # standard class at 3.00 and up
        self.assertEqual({"net_conservative": "10.00", "net_mid": "35.00", "peak_needed": "700.00",
                          "prefix": ["-700.00", "10.00"]}, payload["cash"])
        self.assertEqual([{"row_number": 4, "conservative": "-16.00", "mid": "-1.00"}], payload["realized"])
        self.assertEqual((True, 1, 2, None), (payload["approval"]["ok"], payload["approval"]["required_level"],
                                              payload["approval"]["owner_level"], payload["approval"]["exposure_step"]))
        self.assertEqual([0, 1], [step["level"] for step in payload["steps"]])
        replacement = payload["replacement"]
        self.assertEqual(("replacement_call", "roll", "WMT270115C00115000", "ELIGIBLE", "0.0467", "10.00"),
                         (replacement["kind"], replacement["section"], replacement["candidate"]["id"],
                          replacement["candidate"]["status"], replacement["candidate"]["score"],
                          replacement["candidate"]["net_credit_total"]))
        self.assertEqual("extrinsic_per_day", replacement["objective"]["id"])
        self.assertIsNotNone(payload["model_note"])
        self.assertEqual({"made": 4, "chains": 1, "per_run_max_requests": 40, "per_run_max_chains": 4,
                          "budget_left": 176}, payload["requests"])
        self.assertEqual("5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c",
                         payload["criteria"]["sha256"])
        self.assertEqual("2026-11-03T20:20:00+00:00", payload["valid_until"])
        self.assertTrue(payload["valid_full"])
        self.assertEqual("open", payload["session"]["session"])

    def test_the_wmt_let_assign_payload(self) -> None:
        _code, body = self.run_json(*GOLDEN_TEXTS["wmt_let_assign"][0])
        (leg,) = body["payload"]["let_assign"]
        self.assertEqual(("WMT261120C00105000", False, "104.23", "2026-11-20T21:00:00+00:00", "21000.00", "1984.00"),
                         (leg["contract"], leg["itm"], leg["spot_mid"], leg["act_by_utc"], leg["cash_if_assigned"],
                          leg["realized_if_assigned"]))
        self.assertEqual([], body["payload"]["legs"])
        self.assertIsNone(body["payload"]["cash"])

    def test_the_expiry_day_close_at_grade_e(self) -> None:
        code, out = self.run_text(WMT, "CLOSE", now=EXPIRY_DAY_NOW, fixture_set="expiry_day")
        self.assertEqual(0, code, out)
        self.assertEqual("\n".join(EXPIRY_DAY_CLOSE) + "\n", out)

    def test_the_expiry_day_roll(self) -> None:
        code, body = self.run_json(WMT, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "115",
                                   now=EXPIRY_DAY_NOW, fixture_set="expiry_day")
        self.assertEqual((0, "TICKET"), (code, body["payload"]["verdict"]))
        self.assertIn("FAILS_CRITERION earnings: the listed earnings date 2026-11-17 has passed, so the next one is "
                      "unknown and counts as inside the new call's life", body["payload"]["message"])
        self.assertIn("bid 0.12, ask 0.16, mid 0.14 | quote grade E, Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC | "
                      "quote grade E: the conservative side stands", body["payload"]["message"])


# ------------------------------------------------------------------------------------------------
# Files and the journal
# ------------------------------------------------------------------------------------------------


class FilesTest(DeskCase):
    def test_a_ticket_writes_its_pair_once_and_a_rerun_is_a_no_op(self) -> None:
        code, body = self.run_json(WMT, "CLOSE")
        stem = "TK-AG-20261020-01-CLOSE-20261103T195000Z"
        self.assertEqual(0, code)
        self.assertEqual({"json": "tickets/%s.json" % stem, "md": "tickets/%s.md" % stem, "written": True},
                         body["payload"]["files"])
        self.assertEqual([stem + ".json", stem + ".md"], self.ticket_files())
        with open(os.path.join(self.paths.tickets_dir, stem + ".md"), encoding="utf-8") as handle:
            self.assertEqual(structuring_text.report_markdown("Ticket %s" % stem, body["payload"]["message"]), handle.read())
        with open(os.path.join(self.paths.tickets_dir, stem + ".json"), encoding="utf-8") as handle:
            document = json.load(handle)
        # the standard envelope, as roll_analysis.py and propose.py write their reports, without the run's own keys
        self.assertEqual((contract.SCHEMA_VERSION, "exit_ticket", "ok", "2026-11-03T19:50:00+00:00", body["source"],
                          body["delay_min"]),
                         (document["schema_version"], document["tool"], document["status"], document["generated_at"],
                          document["source"], document["delay_min"]))
        self.assertEqual((stem, body["payload"]["message"]), (document["payload"]["ticket_id"],
                                                              document["payload"]["message"]))
        self.assertEqual(sorted(key for key in exit_ticket.PAYLOAD_KEYS if key not in exit_ticket.FILE_EXCLUDED),
                         sorted(document["payload"]))
        for key in ("requests", "warnings", "files", "journal", "exit_code"):
            self.assertNotIn(key, document["payload"])
        before = {name: sha256_file(os.path.join(self.paths.tickets_dir, name)) for name in self.ticket_files()}
        code, body = self.run_json(WMT, "CLOSE")
        self.assertEqual((0, False), (code, body["payload"]["files"]["written"]))
        self.assertEqual(0, body["payload"]["requests"]["made"])  # served from the quote cache, same bytes
        self.assertEqual(before, {name: sha256_file(os.path.join(self.paths.tickets_dir, name))
                                  for name in self.ticket_files()})
        self.assertEqual(2, len(self.journal()))

    def test_a_conflicting_file_is_never_overwritten(self) -> None:
        stem = "TK-AG-20261020-01-CLOSE-20261103T195000Z"
        os.makedirs(self.paths.tickets_dir)
        with open(os.path.join(self.paths.tickets_dir, stem + ".json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        code, body = self.run_json(WMT, "CLOSE")
        self.assertEqual((1, "error", "ticket_exists"), (code, body["status"], body["error"]["code"]))
        self.assertEqual([stem + ".json"], self.ticket_files())
        self.assertEqual([], self.journal())
        code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(1, code)
        self.assertTrue(out.startswith("error: "), out)

    def test_files_only_on_a_ticket(self) -> None:
        for args in ((WMT, "MONETIZE_PUT"), (WMT, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "117")):
            with self.subTest(args=args):
                code, body = self.run_json(*args)
                self.assertIsNone(body["payload"]["files"])
        self.assertEqual([], self.ticket_files())
        code, _body = self.run_json(WMT, "CLOSE", now=AFTER_CLOSE_NOW, fixture_set="after_close")
        self.assertEqual(20, code)
        self.assertEqual([], self.ticket_files())

    def test_the_journal_records_every_printed_text(self) -> None:
        code, out = self.run_text(NVDA, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "160")
        self.assertEqual(0, code)
        self.run_json(WMT, "MONETIZE_PUT")
        self.run_json(WMT, "CLOSE", now=AFTER_CLOSE_NOW, fixture_set="after_close")
        records = self.journal()
        self.assertEqual(3, len(records))
        self.assertEqual(set(structuring_text.JOURNAL_KEYS), set(records[0]))
        first = records[0]
        self.assertEqual(("exit_ticket", "ROLL", "covered_call", NVDA, "TICKET", "2026-11-03T19:50:00+00:00"),
                         (first["tool"], first["action"], first["structure"], first["trade_id"], first["verdict"],
                          first["utc"]))
        self.assertEqual(hashlib.sha256(out[:-1].encode("utf-8")).hexdigest(), first["text_sha256"])
        self.assertEqual("5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c", first["criteria_sha256"])
        self.assertEqual(sha256_file(self.paths.desk_toml), first["desk_toml_sha256"])
        self.assertEqual(sha256_file(self.paths.positions_csv), first["positions_csv_sha256"])
        self.assertEqual(("NOT_APPLICABLE", None), (records[1]["verdict"], records[1]["refused_code"]))
        self.assertEqual(("UNAVAILABLE", "market_closed"), (records[2]["verdict"], records[2]["refused_code"]))
        self.assertEqual("journal/structuring.jsonl", json.loads(call(exit_ticket.main, self.argv(WMT, "CLOSE"))[1])
                         ["payload"]["journal"])
        # the desk's other journals are never touched
        self.assertFalse(os.path.exists(os.path.join(self.root, "journal", "signals.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "journal", "alerts.jsonl")))


# ------------------------------------------------------------------------------------------------
# Usage, errors and the answers that print no ticket
# ------------------------------------------------------------------------------------------------


class UsageTest(DeskCase):
    def usage(self, argv: List[str]) -> Dict[str, Any]:
        code, out = call(exit_ticket.main, argv + ["--json"])
        self.assertEqual(2, code, out)
        body = json.loads(out)
        self.assertEqual(("usage", "invalid_arguments"), (body["status"], body["error"]["code"]))
        self.screen(out)
        return body

    def test_each_usage_error(self) -> None:
        base = ["--offline", "--desk-root", self.root, "--now", REGULAR_NOW]
        cases = [
            ["--action", "CLOSE"] + base,
            ["--trade", WMT] + base,
            ["--trade", "WMT-1", "--action", "CLOSE"] + base,
            ["--trade", WMT, "--action", "EXPIRE"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-strike", "115"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-expiration", "2027-01-15"] + base,
            ["--trade", WMT, "--action", "CLOSE", "--new-expiration", "2027-01-15", "--new-strike", "115"] + base,
            ["--trade", WMT, "--action", "CLOSE", "--new-strike", "115"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-expiration", "2027-1-15", "--new-strike", "115"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-expiration", "2027-02-30", "--new-strike", "115"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "0"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "-5"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "abc"] + base,
            ["--trade", WMT, "--action", "CLOSE", "--fixture-set", "regular", "--desk-root", self.root],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                self.usage(argv)
        self.assertEqual([], self.journal())

    def test_the_replacement_must_expire_after_the_held_leg(self) -> None:
        for day in ("2026-11-20", "2026-11-13"):
            with self.subTest(day=day):
                body = self.usage(["--trade", WMT, "--action", "ROLL", "--new-expiration", day, "--new-strike", "110",
                                   "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
                self.assertIn("must be later than the held leg's expiration 2026-11-20", body["error"]["message"])
        self.assertEqual([], self.journal())
        self.assertEqual([], self.ticket_files())

    def test_unknown_trade_and_unusable_files(self) -> None:
        code, body = self.run_json("AG-20990101-01", "CLOSE")
        self.assertEqual((1, "unknown_trade"), (code, body["error"]["code"]))
        with open(self.paths.structuring_toml, "a", encoding="utf-8") as handle:
            handle.write("surprise = 1\n")
        self.ack(now="2026-11-03T19:49:30Z")
        code, body = self.run_json(WMT, "CLOSE")
        self.assertEqual((1, "structuring_invalid"), (code, body["error"]["code"]))
        self.assertEqual([], self.journal())


class QuietTest(DeskCase):
    def test_replacement_not_listed(self) -> None:
        code, body = self.run_json(WMT, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "117")
        payload = body["payload"]
        self.assertEqual((20, "unavailable", "UNAVAILABLE", "replacement_not_listed", 4),
                         (code, body["status"], payload["verdict"], payload["cause"], payload["requests"]["made"]))
        self.assertEqual("UNAVAILABLE | AG-20261020-01 ROLL ticket | the chain does not list that contract\n" + FOOTER
                         + "19:50 UTC", payload["message"])

    def test_market_closed(self) -> None:
        for fixture_set, now, clock in (("after_close", AFTER_CLOSE_NOW, "21:25"), ("holiday", HOLIDAY_NOW, "15:00")):
            with self.subTest(fixture_set=fixture_set):
                if fixture_set == "holiday":
                    self.ack(now="2026-11-26T14:59:00Z")
                code, out = self.run_text(WMT, "CLOSE", now=now, fixture_set=fixture_set)
                self.assertEqual(20, code)
                self.assertEqual("UNAVAILABLE | AG-20261020-01 CLOSE ticket | a ticket needs quotes live enough to act "
                                 "on: the session is not open\n" + FOOTER + clock + " UTC\n", out)
                code, body = self.run_json(WMT, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "115", now=now,
                                           fixture_set=fixture_set)
                self.assertEqual((20, "market_closed", 0), (code, body["payload"]["cause"],
                                                            body["payload"]["requests"]["made"]))

    def test_refused_at_a_lower_acknowledged_level(self) -> None:
        with open(self.paths.desk_toml, encoding="utf-8") as handle:
            text = handle.read()
        old = "approval_level = 2 "
        self.assertEqual(1, text.count(old))
        with open(self.paths.desk_toml, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, "approval_level = 1 "))
        code, body = self.run_json(SPY, "ROLL", "--new-expiration", "2027-03-19", "--new-strike", "610")
        self.assertEqual((20, "config_unacknowledged"), (code, body["payload"]["cause"]))  # the edit is not acknowledged
        self.ack(now="2026-11-03T19:49:30Z")
        code, out = self.run_text(SPY, "ROLL", "--new-expiration", "2027-03-19", "--new-strike", "610")
        self.assertEqual(20, code)
        self.assertEqual("REFUSED AG-20261028-03 | SPY protective put | opening 1 new long put needs level 2 by the desk's "
                         "convention; desk.toml records your self-attested level 1 | the desk shows nothing above your "
                         "level; your broker's own approval decides what you can enter\n" + FOOTER + "19:50 UTC\n", out)
        code, body = self.run_json(SPY, "ROLL", "--new-expiration", "2027-03-19", "--new-strike", "610")
        payload = body["payload"]
        self.assertEqual(("REFUSED", "approval_level", 0, None), (payload["verdict"], payload["refused_code"],
                                                                 payload["requests"]["made"], payload["files"]))
        self.assertEqual((False, 2, 1), (payload["approval"]["ok"], payload["approval"]["required_level"],
                                         payload["approval"]["owner_level"]))
        code, _out = self.run_text(SPY, "MONETIZE_PUT")
        self.assertEqual(0, code)  # a closing ticket needs no level
        self.assertEqual("approval_level", self.journal()[-2]["refused_code"])

    def test_config_unacknowledged_comes_first(self) -> None:
        with open(self.paths.structuring_toml, "a", encoding="utf-8") as handle:
            handle.write("# an edit\n")
        new = sha256_file(self.paths.structuring_toml)
        code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(20, code)
        self.assertEqual("UNAVAILABLE | CONFIG CHANGED: structuring.toml: sha 5155427c -> sha %s | the desk names no "
                         "candidate and prints no ticket while desk.toml, positions.csv or structuring.toml differ from "
                         "what you acknowledged | acknowledge with ack.py config --owner-tapped\n" % new[:8]
                         + FOOTER + "19:50 UTC\n", out)
        code, body = self.run_json(WMT, "CLOSE")
        payload = body["payload"]
        self.assertEqual(("UNAVAILABLE", "config_unacknowledged", None), (payload["verdict"], payload["cause"],
                                                                          payload["requests"]))
        self.assertEqual("desk.toml, positions.csv or structuring.toml differ from what you acknowledged",
                         payload["reason"])
        self.assertEqual([{"name": "structuring_toml", "old": "5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c",
                           "new": new}], payload["config_changed"])
        self.assertEqual("config_unacknowledged", self.journal()[-1]["refused_code"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "data", "cache", "ledger.jsonl")))

    def test_a_rulebook_change_alone_prints_first_and_does_not_block(self) -> None:
        rulebook = exit_check_tool.adopted_target(self.paths)
        self.assertEqual(os.path.join(self.root, "rules", "exit_rules-1.0.0.toml"), rulebook)
        old = sha256_file(rulebook)
        with open(rulebook, "a", encoding="utf-8") as handle:
            handle.write("\n")
        code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(0, code, out)
        lines = out.split("\n")
        self.assertEqual("CONFIG CHANGED: adopted rulebook: sha %s -> sha %s | the structuring text below reads the files "
                         "as they are now; acknowledge with ack.py config --owner-tapped" % (old[:8], sha256_file(rulebook)[:8]),
                         lines[0])
        self.assertEqual(GOLDEN_TEXTS["wmt_close"][2][0], lines[1])


class UnacknowledgedDeskTest(DeskCase):
    acknowledge = False

    def test_a_desk_never_acknowledged(self) -> None:
        code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(20, code)
        self.assertTrue(out.startswith("UNAVAILABLE | CONFIG CHANGED: desk.toml: not acknowledged -> sha %s | positions.csv: "
                                       "not acknowledged -> sha %s | adopted rulebook: not acknowledged"
                                       % (sha256_file(self.paths.desk_toml)[:8], sha256_file(self.paths.positions_csv)[:8])),
                        out)
        self.assertIn("structuring.toml: not acknowledged -> sha 5155427c", out)


class CriteriaMissingTest(DeskCase):
    criteria = False

    def test_criteria_missing(self) -> None:
        code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(20, code)
        self.assertEqual("UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your "
                         "criteria (SETUP step 9)\n" + FOOTER + "19:50 UTC\n", out)
        code, body = self.run_json(WMT, "CLOSE")
        self.assertEqual(("criteria_missing", None, None), (body["payload"]["cause"], body["payload"]["criteria"],
                                                            self.journal()[-1]["criteria_sha256"]))
        self.assertEqual("no structuring.toml: the Structuring Desk shows nothing until you set your criteria",
                         body["payload"]["reason"])

    def test_a_rulebook_change_prints_before_the_missing_criteria(self) -> None:
        rulebook = exit_check_tool.adopted_target(self.paths)
        old = sha256_file(rulebook)
        with open(rulebook, "a", encoding="utf-8") as handle:
            handle.write("\n")
        code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(20, code)
        self.assertEqual("CONFIG CHANGED: adopted rulebook: sha %s -> sha %s | the structuring text below reads the files "
                         "as they are now; acknowledge with ack.py config --owner-tapped\n" % (old[:8], sha256_file(rulebook)[:8])
                         + "UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your "
                         "criteria (SETUP step 9)\n" + FOOTER + "19:50 UTC\n", out)


class MarketDataRefusedTest(DeskCase):
    """Market data the desk cannot use as configured is exit 20 UNAVAILABLE, as in every landed market tool
    (``quote_cache.MarketDataRefused``: "Tools map it to exit 20"), and so is an expired holiday table."""

    def test_refused_market_data_and_an_expired_holiday_table_are_unavailable(self) -> None:
        for error, code in ((quote_cache.MarketDataRefused(quote_cache.REFUSED_NO_CREDENTIAL, quote_cache.NO_CREDENTIAL_MESSAGE),
                             "no_credential"),
                            (market_clock.TableExpiredError("the table ends 2027-12-31"), "holiday_table_expired")):
            with self.subTest(code=code):
                with mock.patch.object(exit_ticket.quote_cache, "open_market_data", side_effect=error):
                    exit_code, body = self.run_json(WMT, "CLOSE")
                self.assertEqual((20, "unavailable", code), (exit_code, body["status"], body["error"]["code"]))
                with mock.patch.object(exit_ticket.quote_cache, "open_market_data", side_effect=error):
                    exit_code, out = self.run_text(WMT, "CLOSE")
                self.assertEqual(20, exit_code)
                self.assertTrue(out.startswith("UNAVAILABLE: "), out)
        self.assertEqual([], self.ticket_files())


# ------------------------------------------------------------------------------------------------
# The self-lint, the requests, --now and the network
# ------------------------------------------------------------------------------------------------


class WithheldTest(DeskCase):
    def test_a_text_that_fails_the_lint_is_withheld(self) -> None:
        original = tickets.ticket_lines

        def bad(w: Any, ticket: Any, changes: Any = ()) -> List[str]:
            return original(w, ticket, changes) + ["close it now, you should"]

        with mock.patch.object(tickets, "ticket_lines", side_effect=bad):
            code, out = self.run_text(WMT, "CLOSE")
            json_code, raw = call(exit_ticket.main, self.argv(WMT, "CLOSE"))  # the JSON shows the text that failed
        body = json.loads(raw)
        self.assertEqual((1, 1), (code, json_code))
        self.assertEqual("WITHHELD | the text of exit_ticket.py failed the alert lint (forbidden_language, "
                         "imperative_start) and is withheld; the JSON of exit_ticket.py --json shows it\n" + FOOTER
                         + "19:31 UTC\n", out)
        payload = body["payload"]
        self.assertEqual(("error", False, 1), (body["status"], payload["lint"]["ok"], payload["exit_code"]))
        self.assertIn("close it now, you should", payload["withheld"]["text"])
        self.assertEqual(out[:-1], payload["message"])
        self.assertEqual(["forbidden_language", "imperative_start"], payload["withheld"]["codes"])
        self.assertEqual(([], []), (self.ticket_files(), self.journal()))


class RequestsTest(DeskCase):
    def test_request_counts(self) -> None:
        # a quote batch with an option leg is two requests (equities, then options); a chain is two more
        cases = [((WMT, "CLOSE"), 2), ((WMT, "LET_ASSIGN"), 2), ((SPY, "MONETIZE_PUT"), 2), ((SPY, "UNWIND_STOCK"), 2),
                 ((NVDA, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "160"), 4), ((WMT, "MONETIZE_PUT"), 0)]
        for args, made in cases:
            with self.subTest(args=args):
                self.tearDown()
                self.setUp()  # a fresh acknowledged copy: an empty quote cache and ledger
                _code, body = self.run_json(*args)
                self.assertEqual(made, body["payload"]["requests"]["made"])

    def test_now_is_honoured(self) -> None:
        code, body = self.run_json(WMT, "CLOSE", now="2026-11-03T19:55:07Z")
        payload = body["payload"]
        self.assertEqual(("2026-11-03T19:55:07+00:00", "2026-11-03T19:55:07+00:00", "TK-AG-20261020-01-CLOSE-20261103T195507Z",
                          "2026-11-03T20:25:07+00:00"),
                         (body["generated_at"], payload["computed_utc"], payload["ticket_id"], payload["valid_until"]))
        self.assertEqual("2026-11-03T19:55:07+00:00", self.journal()[-1]["utc"])
        self.assertIn("computed 19:55 UTC, valid until 20:25 UTC (30 min)", payload["message"])

    def test_every_request_is_an_allow_listed_get(self) -> None:
        original = alpaca_client.OfflineTransport.fetch
        seen: List[str] = []

        def recording(transport: Any, url: str, headers: Any, timeout_s: float) -> Any:
            seen.append(url)
            return original(transport, url, headers, timeout_s)

        with mock.patch.object(alpaca_client.OfflineTransport, "fetch", autospec=True, side_effect=recording), \
                mock.patch("http.client.HTTPConnection.request", autospec=True) as request:
            code, body = self.run_json(WMT, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "115")
        self.assertEqual(0, code)
        self.assertEqual(0, request.call_count)
        paths = ["/v2/stocks/quotes/latest", "/v1beta1/options/quotes/latest", "/v2/options/contracts",
                 "/v1beta1/options/snapshots/WMT"]
        self.assertEqual(paths, [urlsplit(url).path for url in seen])
        self.assertTrue(all(alpaca_client.host_for_path(urlsplit(url).path) == urlsplit(url).hostname for url in seen))
        with open(os.path.join(self.root, "data", "cache", "ledger.jsonl"), encoding="utf-8") as handle:
            ledger = [json.loads(line) for line in handle]
        self.assertEqual(paths, [record["path"] for record in ledger])


QUARANTINED_ROW = ("option,WMT,WMT261218C00110000,-2,,,,Brokerage (margin),AG-20261030-05,2026-10-30,-400.00,,2.50,open,"
                   ",,,,")
WMT_CALL_ROW = ("option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,open,,,,,"
                "covered call #assign-ok")


class FixerTest(DeskCase):
    """The PR 3b review, through the tool: L2-1/L3-1 (repro A), L3-2, L3-3, L3-6 and BD4."""

    acknowledge = False

    def edit(self, relative: str, old: str, new: str) -> None:
        path = os.path.join(self.root, relative)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(1, text.count(old), old)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new))

    def test_a_quarantined_group_fails_closed(self) -> None:
        self.edit("positions.csv", WMT_CALL_ROW, WMT_CALL_ROW + "\n" + QUARANTINED_ROW)
        self.ack()
        for action in ("UNWIND_STOCK", "CLOSE"):
            with self.subTest(action=action):
                code, body = self.run_json(WMT, action)
                payload = body["payload"]
                self.assertEqual((20, "UNAVAILABLE", "positions_quarantined", 0),
                                 (code, payload["verdict"], payload["cause"], payload["requests"]["made"]))
                self.assertNotIn("after leg", payload["message"])
        self.assertEqual([], self.ticket_files())
        self.assertEqual(["positions_quarantined", "positions_quarantined"], [r["refused_code"] for r in self.journal()])

    def test_an_account_named_with_a_desk_word_prints_the_ticket(self) -> None:
        for relative in ("desk.toml", "positions.csv"):
            path = os.path.join(self.root, relative)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text.replace("Roth IRA", "options"))
        self.ack()
        code, out = self.run_text(WMT, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "115")
        self.assertEqual(0, code, out)
        self.assertTrue(out.startswith("TICKET AG-20261020-01 | WMT covered call"), out)

    def test_two_tickets_in_one_second_are_one_ticket(self) -> None:
        self.ack()
        first_code, first = self.run_json(WMT, "CLOSE", now="2026-11-03T19:50:00.100000Z")
        second_code, second = self.run_json(WMT, "CLOSE", now="2026-11-03T19:50:00.900000Z")
        self.assertEqual((0, 0), (first_code, second_code))
        self.assertEqual((True, False), (first["payload"]["files"]["written"], second["payload"]["files"]["written"]))
        self.assertEqual("2026-11-03T19:50:00+00:00", second["payload"]["computed_utc"])

    def test_refused_market_data_is_journaled(self) -> None:
        self.ack()
        error = quote_cache.MarketDataRefused("no_credential", quote_cache.NO_CREDENTIAL_MESSAGE)
        with mock.patch.object(exit_ticket.quote_cache, "open_market_data", side_effect=error):
            code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(20, code)
        (record,) = self.journal()
        self.assertEqual(("UNAVAILABLE", "no_credential", "CLOSE", hashlib.sha256(out[:-1].encode("utf-8")).hexdigest()),
                         (record["verdict"], record["refused_code"], record["action"], record["text_sha256"]))

    def test_an_unwind_of_fractional_shares(self) -> None:
        self.edit("positions.csv", "equity,WMT,,200,", "equity,WMT,,200.5,")
        self.ack()
        code, out = self.run_text(WMT, "UNWIND_STOCK")
        self.assertEqual(0, code, out)
        self.assertIn("leg 2 of 2: closes 200.5 of your long WMT shares", out)


class VerifierTest(DeskCase):
    """The PR 3b verifier: the JSON of a fractional unwind (BD4) matches its --schema."""

    acknowledge = False

    def test_a_fractional_unwind_payload_matches_the_schema(self) -> None:
        path = os.path.join(self.root, "positions.csv")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(1, text.count("equity,WMT,,200,"))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace("equity,WMT,,200,", "equity,WMT,,200.5,"))
        self.ack()
        code, body = self.run_json(WMT, "UNWIND_STOCK")
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual([2, "200.5"], [leg["quantity"] for leg in payload["legs"]])
        self.assertEqual([("200.5", 0), (0, 0)], [(step["after"]["shares"], step["after"]["short_calls"])
                                                  for step in payload["steps"]])
        self.assertIs(int, type(payload["steps"][-1]["after"]["shares"]))
        schema = exit_ticket.OUTPUT
        self.assertIn("decimal string", schema["legs"][0]["quantity"])
        self.assertIn("decimal string", schema["steps"][0]["after"]["shares"])


if __name__ == "__main__":
    unittest.main()
