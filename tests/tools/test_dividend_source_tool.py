"""``scripts/dividend_source.py`` (roadmap 2.11, the owner's answer to open question 1): the held symbols' declared
cash dividends from Alpaca's corporate actions, compared with ``research/calendar.toml`` and proposed as
``desk_init.py`` answers; then ``desk_init.py``'s ``sourced_dividends`` update on the owner's tap."""

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
from typing import Any, Dict, Sequence, Tuple

import desk_init
import dividend_source as tool  # the tool scripts/dividend_source.py
from aegis_desk import alert_lint, calendar_events

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
FIXTURE = "corporate_actions__NVDA,SPY,WMT__2026-10-24__2027-04-02.json"
TEXT = [
    "SOURCED DIVIDENDS | ex-dates 2026-11-03 to 2027-02-01 | Alpaca corporate actions (cash_dividend), as of "
    "2026-11-03T19:50:00Z",
    "NVDA | AGREES | ex-date 2026-12-03, 0.01 per share | calendar: 2026-12-03 0.01 confirmed (owner-entered (fixture))",
    "SPY | DIFFERS | ex-date 2026-12-18, 1.9021 per share | calendar: 2026-12-18 1.85 estimated (owner-entered "
    "(fixture))",
    "WMT | AGREES | ex-date 2026-11-24, 0.235 per share | calendar: 2026-11-24 0.2350 confirmed (owner-entered "
    "(fixture))",
    "proposed for research/calendar.toml, written only on your tap: SPY",
    "to record them: desk_init.py --answers <the answers above, saved as a file> --replace --owner-tapped",
    "ignored: 1 record(s) without an ex-date",
]


def run(module: Any, argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = module.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-dividends-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.calendar = os.path.join(self.root, "research", "calendar.toml")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def offline(self, *extra: str, json_mode: bool = False) -> Tuple[int, str]:
        code, out = run(tool, ["--offline", "--desk-root", self.root, "--now", REGULAR_NOW] + list(extra)
                        + (["--json"] if json_mode else []))
        self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(out)])
        return code, out

    def answers_file(self, answers: Dict[str, Any]) -> str:
        path = os.path.join(self._tmp.name, "answers.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(dict(answers, schema_version=1), handle)
        return path

    def read_calendar(self) -> str:
        with open(self.calendar, encoding="utf-8") as handle:
            return handle.read()

    def init(self, answers: Dict[str, Any], *flags: str) -> Tuple[int, str]:
        return run(desk_init, ["--desk-root", self.root, "--answers", self.answers_file(answers),
                               "--now", "2026-11-03T20:00:00Z", "--json"] + list(flags))


class SchemaTest(DeskCopy):
    def test_schema_and_usage(self) -> None:
        code, out = run(tool, ["--schema"])
        body = json.loads(out)
        self.assertEqual((0, "dividend_source"), (code, body["tool"]))
        self.assertIn("https://data.alpaca.markets/v1/corporate-actions (types=cash_dividend)", body["network_access"])
        self.assertEqual(sorted(tool.OUTPUT), sorted(body["envelope"]["payload"]))
        for argv in (["--days", "0"], ["--days", "401"], ["--fixture-set", "regular"]):
            self.assertEqual(2, run(tool, argv + ["--desk-root", self.root, "--json"])[0], argv)


class ProposalTest(DeskCopy):
    def test_the_regular_goldens_and_nothing_written(self) -> None:
        before = self.read_calendar()
        code, out = self.offline()
        self.assertEqual((10, TEXT), (code, out.splitlines()), "a proposal is exit 10: the routine posts it")
        self.assertEqual(before, self.read_calendar())

    def test_json_answers(self) -> None:
        code, out = self.offline(json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual({"sourced_dividends": {"SPY": {"date": "2026-12-18", "amount": "1.9021",
                                                        "checked_utc": "2026-11-03T19:50:00Z"}}}, payload["answers"])
        self.assertEqual(["AGREES", "DIFFERS", "AGREES"], [row["state"] for row in payload["symbols"]])
        self.assertEqual((10, "proposed", 1, None), (code, payload["verdict"], payload["ignored_without_ex_date"],
                                                     payload["reason"]))
        self.assertEqual("\n".join(TEXT), payload["message"])

    def test_a_symbol_without_a_declaration_is_none_found_and_nothing_is_proposed_for_it(self) -> None:
        path = os.path.join(self.root, "fixtures", "alpaca", "regular", FIXTURE)
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        document["corporate_actions"]["cash_dividends"] = [item for item in document["corporate_actions"]["cash_dividends"]
                                                           if item["symbol"] != "NVDA"]
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
        code, out = self.offline(json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual(10, code, "SPY is still proposed")
        self.assertEqual(("NONE FOUND", None), (payload["symbols"][0]["state"], payload["symbols"][0]["ex_date"]))
        self.assertEqual(["SPY"], sorted(payload["answers"]["sourced_dividends"]), "absence is no proof of none")
        code, out = self.offline()
        self.assertIn("NVDA | NONE FOUND in the window (nothing proposed; calendar: 2026-12-03 0.01 confirmed "
                      "(owner-entered (fixture)))", out.splitlines())

    def test_not_served_is_unavailable_and_nothing_is_sourced(self) -> None:
        os.remove(os.path.join(self.root, "fixtures", "alpaca", "regular", FIXTURE))
        code, out = self.offline()
        self.assertEqual(20, code)
        self.assertTrue(out.startswith("UNAVAILABLE | sourced dividends | Alpaca corporate actions not served"), out)
        self.assertIn("keep stating dividends", out)


class TapTest(DeskCopy):
    def test_the_owners_tap_writes_the_proposal_and_keeps_everything_else(self) -> None:
        _code, out = self.offline(json_mode=True)
        answers = json.loads(out)["payload"]["answers"]
        before = self.read_calendar()
        code, out = self.init(answers)
        self.assertEqual((1, "calendar_exists"), (code, json.loads(out)["error"]["code"]), "no tap, no write")
        self.assertEqual(before, self.read_calendar())
        code, out = self.init(answers, "--replace", "--owner-tapped")
        self.assertEqual(0, code, out)
        after = self.read_calendar()
        changed = [(old, new) for old, new in zip(before.splitlines(), after.splitlines()) if old != new]
        self.assertEqual(1, len(changed), "one line changes: SPY's ex_dividend")
        self.assertEqual('ex_dividend = { date = 2026-12-18, amount = 1.9021, yield = 0.0115, yield_source = '
                         '"owner-entered (fixture)", status = "confirmed", source = "Alpaca corporate actions '
                         '(cash_dividend)", checked_utc = 2026-11-03T19:50:00Z }', changed[0][1])
        cal = calendar_events.parse_calendar_text(after)
        self.assertEqual(("owner-entered (fixture)", cal.closures), (cal.sourced_yield("SPY").source,
                                                                     calendar_events.parse_calendar_text(before).closures))
        code, out = self.offline()
        self.assertIn("nothing to propose: the calendar agrees with every declaration found", out.splitlines())
        self.assertEqual(0, code, "nothing to propose is quiet")

    def test_a_new_symbol_gets_its_own_table(self) -> None:
        code, out = self.init({"sourced_dividends": {"KO": {"date": "2026-11-28", "amount": "0.51",
                                                             "checked_utc": "2026-11-03T19:50:00Z"}}},
                              "--replace", "--owner-tapped")
        self.assertEqual(0, code, out)
        text = self.read_calendar()
        self.assertTrue(text.endswith('\n[symbols.KO]\nex_dividend = { date = 2026-11-28, amount = 0.51, status = '
                                      '"confirmed", source = "Alpaca corporate actions (cash_dividend)", checked_utc = '
                                      '2026-11-03T19:50:00Z }\n'), text[-300:])
        self.assertIsNone(calendar_events.parse_calendar_text(text).sourced_yield("KO").value, "no yield is invented")

    def test_refusals(self) -> None:
        entry = {"date": "2026-12-18", "amount": "1.9021", "checked_utc": "2026-11-03T19:50:00Z"}
        cases = [({"sourced_dividends": {"SPY": dict(entry, amount=1.9)}}, "answers_invalid"),
                 ({"sourced_dividends": {"SPY": dict(entry, checked_utc="yesterday")}}, "answers_invalid"),
                 ({"sourced_dividends": {"SPY": dict(entry, status="estimated")}}, "answers_invalid"),
                 ({"sourced_dividends": {"SPY": entry}, "earnings": {"SPY": {"status": "none", "source": "x"}}},
                  "answers_invalid")]
        for answers, error in cases:
            with self.subTest(answers=answers):
                code, out = self.init(answers, "--replace", "--owner-tapped")
                self.assertEqual((1, error), (code, json.loads(out)["error"]["code"]))
        with open(self.calendar, encoding="utf-8") as handle:
            text = handle.read()
        lines = text.split("\n")
        spy = next(i for i, line in enumerate(lines) if line.startswith("ex_dividend") and "2026-12-18" in line)
        lines[spy] = ""
        lines.append('[symbols.SPY.ex_dividend]\ndate = 2026-12-18\namount = 1.85\nyield = 0.0115\nstatus = "estimated"\n'
                     'source = "owner-entered"\nchecked_utc = 2026-11-01T12:00:00Z\n')
        with open(self.calendar, "w", encoding="utf-8") as handle:
            handle.write("\n".join(lines))
        calendar_events.load_calendar(self.calendar)  # the owner's layout loads
        code, out = self.init({"sourced_dividends": {"SPY": entry}}, "--replace", "--owner-tapped")
        self.assertEqual((1, "calendar_layout"), (code, json.loads(out)["error"]["code"]), "never rewritten by guess")
        os.remove(self.calendar)
        code, out = self.init({"sourced_dividends": {"SPY": entry}}, "--replace", "--owner-tapped")
        self.assertEqual((1, "calendar_missing"), (code, json.loads(out)["error"]["code"]))


if __name__ == "__main__":
    unittest.main()
