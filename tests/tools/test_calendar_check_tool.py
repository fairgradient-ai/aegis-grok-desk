"""``scripts/calendar_check.py`` run in-process against a copied fixture desk root: the freshness
lint of ``research/calendar.toml`` against the held symbols and the short-call underlyings. The
tool reads the desk and writes nothing; nothing under ``tests/fixtures/`` is ever written."""

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
from typing import Any, Dict, List, Sequence, Tuple

import calendar_check as tool

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
LATER_NOW = "2026-11-25T12:00:00Z"


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
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-calendar-check-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.calendar_path = os.path.join(self.root, "research", "calendar.toml")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, now: str = REGULAR_NOW, json_mode: bool = True, extra: Sequence[str] = ()) -> List[str]:
        argv = ["--offline", "--desk-root", self.root, "--now", now] + list(extra)
        if json_mode:
            argv.append("--json")
        return argv

    def rewrite(self, relative: str, old: str, new: str) -> None:
        path = os.path.join(self.root, relative)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))

    def tree(self) -> List[str]:
        found = []
        for dirpath, _, filenames in os.walk(self.root):
            for name in filenames:
                found.append(os.path.relpath(os.path.join(dirpath, name), self.root))
        return sorted(found)


class AlwaysOnTest(DeskCopy):
    def test_schema(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual(("calendar_check", "none"), (body["tool"], body["network_access"]))
        payload = body["envelope"]["payload"]
        for key in ("verdict", "findings", "held_symbols", "short_call_symbols", "max_age_days"):
            self.assertIn(key, payload)

    def test_usage_errors(self) -> None:
        for argv in (["--json", "--offline"],
                     ["--json", "--desk-root", self.root, "--max-age-days", "-1"],
                     ["--json", "--desk-root", self.root, "--max-age-days", "x"],
                     ["--json", "--desk-root", self.root, "--max-age-days", "1.5"]):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(argv)
                self.assertEqual(2, ctx.exception.code)


class LintTest(DeskCopy):
    def test_main_root_is_clean_and_nothing_is_written(self) -> None:
        before = self.tree()
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertEqual(("ok", None, None), (body["status"], body["source"], body["delay_min"]))
        payload = body["payload"]
        self.assertEqual(("clean", 0, 7, [], "2026-11-03"),
                         (payload["verdict"], payload["exit_code"], payload["max_age_days"], payload["findings"], payload["today"]))
        self.assertEqual((["NVDA", "SPY", "WMT"], ["NVDA", "WMT"], ["NVDA", "SPY", "WMT"]),
                         (payload["held_symbols"], payload["short_call_symbols"], payload["listed_symbols"]))
        self.assertEqual({"STALE": 0, "MISSING": 0, "UNKNOWN_DIVIDEND": 0, "PAST": 0}, payload["counts"])
        self.assertEqual(64, len(payload["calendar_sha256"]))
        self.assertEqual(before, self.tree(), "the lint writes nothing, not even runtime directories")
        code, text = run_tool(self.argv(json_mode=False))
        self.assertEqual(0, code)
        lines = text.splitlines()
        self.assertEqual("CLEAN: research/calendar.toml lists NVDA, SPY, WMT; held NVDA, SPY, WMT (short calls NVDA, WMT); "
                         "0 findings within 7 days as of 2026-11-03", lines[-1])

    def test_max_age_one_day_is_stale(self) -> None:
        code, body = run_json(self.argv(extra=["--max-age-days", "1"]))
        self.assertEqual(20, code, body)
        self.assertEqual("unavailable", body["status"])
        payload = body["payload"]
        self.assertEqual(("unavailable", 20, 1), (payload["verdict"], payload["exit_code"], payload["max_age_days"]))
        self.assertEqual(["STALE"] * 6, [finding["kind"] for finding in payload["findings"]])
        self.assertEqual(["NVDA", "NVDA", "SPY", "SPY", "WMT", "WMT"], [finding["symbol"] for finding in payload["findings"]])
        self.assertEqual(6, payload["counts"]["STALE"])
        code, text = run_tool(self.argv(json_mode=False, extra=["--max-age-days", "1"]))
        self.assertEqual(20, code)
        lines = text.splitlines()
        self.assertTrue(any(line.startswith("STALE WMT: WMT ex_dividend checked 2026-11-01T12:00:00+00:00 is 2 days old (limit 1 day)")
                            for line in lines), lines)
        self.assertTrue(lines[-1].startswith("UNAVAILABLE: 6 findings (6 STALE)"), lines[-1])

    def test_past_events_later_in_november(self) -> None:
        code, body = run_json(self.argv(now=LATER_NOW))
        self.assertEqual(20, code, body)
        findings = body["payload"]["findings"]
        past = sorted((finding["symbol"], finding["message"].split(" ")[1], finding["message"].split(" ")[2])
                      for finding in findings if finding["kind"] == "PAST")
        self.assertEqual([("NVDA", "earnings", "2026-11-19"), ("WMT", "earnings", "2026-11-17"), ("WMT", "ex_dividend", "2026-11-24")],
                         past)
        self.assertEqual(3, body["payload"]["counts"]["STALE"], "the three entries not already PAST are 24 days old")

    def test_missing_calendar_is_exit_20(self) -> None:
        os.remove(self.calendar_path)
        code, body = run_json(self.argv())
        self.assertEqual((20, "unavailable", "calendar_missing"), (code, body["status"], body["error"]["code"]))
        self.assertIn("no research/calendar.toml", body["error"]["message"])
        code, text = run_tool(self.argv(json_mode=False))
        self.assertEqual(20, code)
        self.assertIn("UNAVAILABLE: no research/calendar.toml", text)

    def test_parse_error_is_exit_1(self) -> None:
        with open(self.calendar_path, "w", encoding="utf-8") as handle:
            handle.write("schema_version = 1\n[symbols.WMT]\nearnings = { date = 2026-11-17, status = \"confirmed\" }\n")
        code, body = run_json(self.argv())
        self.assertEqual((1, "error", "calendar_invalid"), (code, body["status"], body["error"]["code"]))
        self.assertIn("missing key 'source'", body["error"]["message"])
        with open(self.calendar_path, "w", encoding="utf-8") as handle:
            handle.write("not = [toml\n")
        code, body = run_json(self.argv())
        self.assertEqual((1, "calendar_invalid"), (code, body["error"]["code"]))

    def test_unknown_dividend_for_a_short_call_underlying(self) -> None:
        self.rewrite("research/calendar.toml",
                     'ex_dividend = { date = 2026-12-03, amount = 0.01, status = "confirmed", source = "owner-entered (fixture)", '
                     'checked_utc = 2026-11-01T12:00:00Z }',
                     'ex_dividend = { status = "unknown", source = "owner-entered (fixture)", checked_utc = 2026-11-01T12:00:00Z }')
        code, body = run_json(self.argv())
        self.assertEqual(20, code, body)
        self.assertEqual([("UNKNOWN_DIVIDEND", "NVDA")], [(f["kind"], f["symbol"]) for f in body["payload"]["findings"]])

    def test_spy_without_a_short_call_needs_no_dividend(self) -> None:
        self.rewrite("research/calendar.toml",
                     'ex_dividend = { date = 2026-12-18, amount = 1.85, status = "estimated", source = "owner-entered (fixture)", '
                     'checked_utc = 2026-11-01T12:00:00Z }',
                     'ex_dividend = { status = "unknown", source = "owner-entered (fixture)", checked_utc = 2026-11-01T12:00:00Z }')
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)

    def test_held_symbol_absent_from_the_file_is_missing(self) -> None:
        self.rewrite("research/calendar.toml", "[symbols.NVDA]", "[symbols.NVDX]")
        code, body = run_json(self.argv())
        self.assertEqual(20, code, body)
        self.assertEqual([("MISSING", "NVDA")], [(f["kind"], f["symbol"]) for f in body["payload"]["findings"]])
        self.assertIn("short call", body["payload"]["findings"][0]["message"])

    def test_calendar_load_warnings_are_printed(self) -> None:
        with open(self.calendar_path, "a", encoding="utf-8") as handle:
            handle.write('\n[[closures]]\ndate = 2026-12-01\nkind = "early_close"\nsource = "test"\n')
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertTrue(any("2026-12-01" in warning for warning in body["payload"]["warnings"]))
        code, text = run_tool(self.argv(json_mode=False))
        self.assertIn("warning: closure 2026-12-01 early_close", text)

    def test_positions_file_error_leaves_held_symbols_unknown(self) -> None:
        self.rewrite("positions.csv", "cost_basis_total,cost_basis_per_unit", "cost_basis_total,cost_basis_totl")
        code, body = run_json(self.argv())
        self.assertEqual(20, code, body)
        payload = body["payload"]
        self.assertEqual(([], "unavailable"), (payload["held_symbols"], payload["verdict"]))
        self.assertTrue(any("held symbols unknown" in warning for warning in payload["warnings"]))

    def test_quarantined_group_is_a_warning(self) -> None:
        self.rewrite("positions.csv", "-684.00,,3.42", "684.00,,3.42")
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertEqual(["NVDA", "SPY"], body["payload"]["held_symbols"])
        self.assertTrue(any("AG-20261020-01" in warning for warning in body["payload"]["warnings"]))

    def test_positions_missing_is_exit_1(self) -> None:
        os.remove(os.path.join(self.root, "positions.csv"))
        code, body = run_json(self.argv())
        self.assertEqual((1, "error", "positions_missing"), (code, body["status"], body["error"]["code"]))

    def test_desk_root_without_desk_toml_still_lints(self) -> None:
        os.remove(os.path.join(self.root, "desk.toml"))
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)


if __name__ == "__main__":
    unittest.main()
