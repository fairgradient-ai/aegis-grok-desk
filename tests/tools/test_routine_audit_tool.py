"""``scripts/routine_audit.py`` run in-process: ``--budget`` (regular runs per week per cadence profile, from the
manifest), ``--rules-text`` (the three Grok Bot Auto-review texts, rendered from ``routines/autoreview.toml`` with
the manifest's routine ids) and the day audit (expected, ran, late, missed and pending slots from
``state/heartbeat/history.jsonl`` on a tempfile copy of the fixture desk). The tool posts nothing and writes nothing:
every audit leaves the copied desk root byte for byte as it was. Exit 0, 20 when an audited day has a missed slot,
1 on errors, 2 on usage. 2026-11-03 is EST (UTC-5)."""

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
from typing import Any, Dict, List, Sequence, Tuple
from unittest import mock

from aegis_desk import alert_lint, desk_paths, heartbeat, market_clock, routines

import routine_audit as tool  # the tool scripts/routine_audit.py

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
TOO_LONG_AUTOREVIEW = os.path.join(ROOT, "tests", "fixtures", "gates", "bad", "autoreview_too_long", "routines",
                                   "autoreview.toml")
TUESDAY = datetime.date(2026, 11, 3)
BRIEF_NOW = "2026-11-03T22:10:00Z"  # 17:10 ET


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            code = tool.main(list(argv))
        except SystemExit as exc:  # parser.error: the usage envelope, then exit 2
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


def order_shaped(text: str) -> List[str]:
    return [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(text)]


def et(day: datetime.date, hhmm: str) -> datetime.datetime:
    hour, minute = (int(part) for part in hhmm.split(":"))
    return market_clock.from_et(datetime.datetime(day.year, day.month, day.day, hour, minute))


class ToolTest(unittest.TestCase):
    def run_json(self, *argv: str) -> Tuple[int, Dict[str, Any]]:
        code, out = run_tool(list(argv) + ["--json"])
        self.assertEqual([], order_shaped(out))
        return code, json.loads(out)

    def run_text(self, *argv: str) -> Tuple[int, str]:
        code, out = run_tool(list(argv))
        self.assertEqual([], order_shaped(out))
        return code, out


class SchemaAndUsageTest(ToolTest):
    def test_schema(self) -> None:
        code, document = self.run_json("--schema")
        self.assertEqual(0, code)
        self.assertEqual("routine_audit", document["tool"])
        self.assertEqual("none", document["network_access"])
        self.assertIn("budget", document["envelope"]["payload"])

    def test_usage_errors(self) -> None:
        for argv in (["--budget", "--rules-text"], ["--rules-text", "--week"], ["--rules-text", "--profile", "lean"],
                     ["--budget", "--date", "2026-11-03"], ["--budget", "--week"], ["--date", "11/03/2026"],
                     ["--profile", "turbo"]):
            code, document = self.run_json(*argv)
            self.assertEqual(2, code, argv)
            self.assertEqual("usage", document["status"], argv)
            self.assertEqual("invalid_arguments", document["error"]["code"], argv)


class BudgetTest(ToolTest):
    def test_budget_json(self) -> None:
        code, document = self.run_json("--budget")
        self.assertEqual(0, code)
        payload = document["payload"]
        self.assertEqual("budget", payload["mode"])
        self.assertEqual({"lean": 41, "active": 61}, payload["budget"])
        self.assertEqual("routines/manifest.toml", payload["manifest"]["path"])
        self.assertEqual(routines.load_manifest().sha256, payload["manifest"]["sha256"])
        lean = payload["profiles"][0]
        self.assertEqual(("lean", "about 8 runs per weekday plus Saturday", False, 41),
                         (lean["name"], lean["description"], lean["additive"], lean["runs_per_week"]))
        self.assertEqual({"id": "book-check", "owner": "exit-sentinel", "slots": ["10:05", "12:05", "14:05", "15:35"],
                          "days": ["Mon", "Tue", "Wed", "Thu", "Fri"], "runs_per_week": 20}, lean["routines"][1])
        self.assertEqual(["preopen", "book-check", "final-call", "eod", "eod-brief", "post-expiry"],
                         [item["id"] for item in lean["routines"]])
        self.assertEqual(sum(item["runs_per_week"] for item in lean["routines"]), lean["runs_per_week"])

    def test_budget_text(self) -> None:
        code, out = self.run_text("--budget")
        self.assertEqual(0, code)
        lines = out.splitlines()
        self.assertTrue(lines[0].startswith("routine budget (routines/manifest.toml, sha "), lines[0])
        self.assertIn("lean: 41 runs per week (about 8 runs per weekday plus Saturday)", lines)
        self.assertIn("  book-check (exit-sentinel): 10:05, 12:05, 14:05, 15:35 ET, Mon-Fri: 20", lines)
        self.assertIn("  post-expiry (position-control): 09:30 ET, Sat: 1", lines)
        self.assertIn("active: 61 runs per week (lean plus four paused book-check slots, unpaused only on the "
                      "conditions the owner chose)", lines)

    def test_budget_of_one_profile(self) -> None:
        code, document = self.run_json("--budget", "--profile", "active")
        self.assertEqual(0, code)
        self.assertEqual({"active": 61}, document["payload"]["budget"])
        self.assertEqual(["active"], [item["name"] for item in document["payload"]["profiles"]])

    def test_invalid_manifest_is_exit_1(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aegis-routine-audit-") as tmp:
            path = os.path.join(tmp, "manifest.toml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("schema_version = 2\n")
            with mock.patch.object(tool, "manifest_file", return_value=path):
                code, document = self.run_json("--budget")
                text_code, out = self.run_text("--budget")
        self.assertEqual(1, code)
        self.assertEqual("error", document["status"])
        self.assertEqual("manifest_invalid", document["error"]["code"])
        self.assertIn("schema_version must be 1 (found 2)", document["error"]["message"])
        self.assertEqual(1, text_code)
        self.assertTrue(out.startswith("error: "), out)


class RulesTextTest(ToolTest):
    def test_rules_text_json(self) -> None:
        code, document = self.run_json("--rules-text")
        self.assertEqual(0, code)
        payload = document["payload"]
        expected = routines.rules_text(routines.load_manifest(), routines.load_autoreview())
        self.assertEqual("rules_text", payload["mode"])
        self.assertEqual(expected, payload["rules"])
        self.assertEqual({"allow": 511, "ask_first_writes": 487, "ask_first_owner_authority": 333}, payload["lengths"])
        self.assertEqual(1000, payload["limit_chars"])
        self.assertEqual(["preopen", "book-check", "final-call", "eod", "eod-brief", "post-expiry"],
                         payload["routine_ids"])
        self.assertEqual("routines/autoreview.toml", payload["autoreview"]["path"])
        self.assertEqual("2d4c4c427a551b59e152f2ff89a8ab603154e0b753dad834ff5d91313665b2da",
                         payload["autoreview"]["sha256"])

    def test_rules_text_prints_each_rule_on_its_own_line(self) -> None:
        code, out = self.run_text("--rules-text")
        self.assertEqual(0, code)
        lines = out.splitlines()
        texts = routines.rules_text(routines.load_manifest(), routines.load_autoreview())
        for number, key in enumerate(routines.RULE_KEYS, 1):
            self.assertIn(texts[key], lines)
            self.assertIn("%d. %s (%d of 1000 characters)" % (number, key, len(texts[key])), lines)
        self.assertTrue(lines[0].startswith("Auto-review rules (routines/autoreview.toml, rendered for 6 routines)"))

    def test_too_long_rules_are_exit_1(self) -> None:
        with mock.patch.object(tool, "autoreview_file", return_value=TOO_LONG_AUTOREVIEW):
            code, document = self.run_json("--rules-text")
        self.assertEqual(1, code)
        self.assertEqual("rules_invalid", document["error"]["code"])
        self.assertIn("[allow_routines] renders to 1130 characters (limit 1000)", document["error"]["message"])

    def test_invalid_autoreview_is_exit_1(self) -> None:
        with tempfile.TemporaryDirectory(prefix="aegis-routine-audit-") as tmp:
            path = os.path.join(tmp, "autoreview.toml")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("schema_version = 1\nlimit_chars = 1000\n")
            with mock.patch.object(tool, "autoreview_file", return_value=path):
                code, document = self.run_json("--rules-text")
        self.assertEqual(1, code)
        self.assertEqual("autoreview_invalid", document["error"]["code"])
        self.assertIn("[allow_routines].text must be a non-empty string", document["error"]["message"])


class DayAuditTest(ToolTest):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-routine-audit-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def beat(self, routine_id: str, slot: str, started: datetime.datetime, exit_code: int = 0, status: str = "ok") -> None:
        desk_paths.ensure_runtime_dirs(self.paths)
        heartbeat.write_heartbeat(self.paths, routine_id, now=started, exit_code=exit_code, status=status,
                                  started_utc=started, finished_utc=started, message_sha256=None, scheduled_et=slot,
                                  tool_version="0.1.0")

    def tuesday(self, skip: Sequence[str] = ("book-check 15:35",)) -> None:
        for routine_id, slot, started, code, status in (
                ("preopen", "07:30", "07:42", 0, "ok"), ("book-check", "10:05", "10:36", 10, "fired"),
                ("book-check", "12:05", "12:15", 0, "ok"), ("book-check", "14:05", "14:25", 0, "ok"),
                ("final-call", "15:20", "15:30", 0, "quiet"), ("book-check", "15:35", "15:40", 0, "ok"),
                ("eod", "16:35", "16:50", 0, "ok"), ("eod-brief", "17:10", "17:12", 0, "ok")):
            if "%s %s" % (routine_id, slot) not in skip:
                self.beat(routine_id, slot, et(TUESDAY, started), code, status)

    def argv(self, now: str, *extra: str) -> List[str]:
        return ["--offline", "--desk-root", self.root, "--now", now] + list(extra)

    def snapshot(self) -> Dict[str, Tuple[int, int]]:
        found: Dict[str, Tuple[int, int]] = {}
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames.sort()
            for name in filenames:
                path = os.path.join(dirpath, name)
                stat = os.stat(path)
                found[os.path.relpath(path, self.root)] = (stat.st_size, stat.st_mtime_ns)
        return found

    def test_brief_time_audit_with_a_missed_slot_is_exit_20(self) -> None:
        self.tuesday(skip=("book-check 15:35", "eod-brief 17:10"))
        before = self.snapshot()
        code, document = self.run_json(*self.argv(BRIEF_NOW))
        self.assertEqual(before, self.snapshot())  # posts nothing, writes nothing
        self.assertEqual(20, code)
        self.assertEqual("unavailable", document["status"])
        payload = document["payload"]
        self.assertEqual(("day", "lean", 20), (payload["mode"], payload["profile"], payload["exit_code"]))
        day = payload["days"][0]
        self.assertEqual("2026-11-03", day["et_date"])
        self.assertTrue(day["trading_day"])
        self.assertEqual({"expected": 8, "ran": 6, "late": 1, "missed": 1, "pending": 1}, day["counts"])
        self.assertEqual(["book-check 15:35"], ["%s %s" % (s["routine_id"], s["scheduled_et"]) for s in day["audit"]["missed"]])
        self.assertEqual(["eod-brief 17:10"], ["%s %s" % (s["routine_id"], s["scheduled_et"]) for s in day["audit"]["pending"]])
        self.assertIsNone(day["timezone_offset_suspected"])
        self.assertEqual({"path": "state/heartbeat/history.jsonl", "status": "ok", "records": 6, "bad_lines": 0},
                         payload["history"])
        self.assertEqual([], payload["warnings"])

    def test_brief_time_audit_text(self) -> None:
        self.tuesday(skip=("book-check 15:35", "eod-brief 17:10"))
        code, out = self.run_text(*self.argv(BRIEF_NOW))
        self.assertEqual(20, code)
        lines = out.splitlines()
        self.assertEqual("routine audit Tue 2026-11-03 (profile lean, as of 22:10 UTC): 6 of 8 due slots ran; "
                         "late: book-check 10:05 ET (31 min late); missed: book-check 15:35 ET; "
                         "pending: eod-brief 17:10 ET", lines[0])
        self.assertIn("  preopen 07:30 ET: ran at 07:42 ET (12 min late), exit 0 ok", lines)
        self.assertIn("  book-check 10:05 ET: ran at 10:36 ET (31 min late), exit 10 fired", lines)
        self.assertIn("  final-call 15:20 ET: ran at 15:30 ET (10 min late), exit 0 quiet", lines)
        self.assertIn("  book-check 15:35 ET: missed (no heartbeat 45 min after the slot)", lines)
        self.assertIn("  eod-brief 17:10 ET: pending (no heartbeat yet)", lines)

    def test_a_full_day_is_exit_0(self) -> None:
        self.tuesday(skip=())
        code, document = self.run_json(*self.argv("2026-11-03T23:30:00Z"))
        self.assertEqual(0, code)
        self.assertEqual("ok", document["status"])
        self.assertEqual({"expected": 8, "ran": 8, "late": 1, "missed": 0, "pending": 0},
                         document["payload"]["days"][0]["counts"])

    def test_empty_history_names_the_missing_journal(self) -> None:
        code, document = self.run_json(*self.argv("2026-11-03T19:50:00Z"))
        self.assertEqual(20, code)
        day = document["payload"]["days"][0]
        self.assertEqual({"expected": 4, "ran": 0, "late": 0, "missed": 4, "pending": 0}, day["counts"])
        self.assertEqual("missing", document["payload"]["history"]["status"])
        self.assertEqual(["state/heartbeat/history.jsonl missing: no routine run is recorded"],
                         document["payload"]["warnings"])

    def test_a_torn_history_line_is_a_warning(self) -> None:
        self.tuesday(skip=())
        with open(heartbeat.history_path(self.paths), "a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        code, document = self.run_json(*self.argv("2026-11-03T23:30:00Z"))
        self.assertEqual(0, code)
        self.assertEqual("partial", document["payload"]["history"]["status"])
        self.assertEqual(1, document["payload"]["history"]["bad_lines"])
        self.assertEqual(["state/heartbeat/history.jsonl: 1 unreadable line(s) skipped"], document["payload"]["warnings"])

    def test_closed_day_expects_nothing(self) -> None:
        code, out = self.run_text(*self.argv("2026-11-26T20:00:00Z"))
        self.assertEqual(0, code)
        self.assertEqual("routine audit Thu 2026-11-26 (profile lean, as of 20:00 UTC): no slot of profile lean is due "
                         "(no session: Thanksgiving Day)", out.splitlines()[0])
        code, out = self.run_text(*self.argv("2026-11-22T20:00:00Z"))
        self.assertEqual(0, code)
        self.assertEqual("routine audit Sun 2026-11-22 (profile lean, as of 20:00 UTC): no slot of profile lean is due "
                         "(no session: weekend)", out.splitlines()[0])

    def test_past_date_and_week(self) -> None:
        code, document = self.run_json(*self.argv(BRIEF_NOW, "--date", "2026-11-02"))
        self.assertEqual(20, code)
        self.assertEqual({"expected": 8, "ran": 0, "late": 0, "missed": 8, "pending": 0},
                         document["payload"]["days"][0]["counts"])
        self.tuesday(skip=())
        code, document = self.run_json(*self.argv("2026-11-04T12:00:00Z", "--week"))
        self.assertEqual(20, code)
        self.assertEqual("week", document["payload"]["mode"])
        self.assertEqual(["2026-11-02", "2026-11-03", "2026-11-04"],
                         [day["et_date"] for day in document["payload"]["days"]])
        self.assertEqual([8, 0, 0], [day["counts"]["missed"] for day in document["payload"]["days"]])
        self.assertEqual([0, 8, 0], [day["counts"]["ran"] for day in document["payload"]["days"]])
        code, document = self.run_json(*self.argv(BRIEF_NOW, "--date", "2026-11-04"))
        self.assertEqual(2, code)
        self.assertIn("--date 2026-11-04 is after the ET date of the run instant (2026-11-03)", document["error"]["message"])

    def test_profile_override_and_config_errors(self) -> None:
        self.tuesday(skip=())
        code, document = self.run_json(*self.argv(BRIEF_NOW, "--profile", "active"))
        self.assertEqual(20, code)
        self.assertEqual("active", document["payload"]["profile"])
        self.assertEqual(12, document["payload"]["days"][0]["counts"]["expected"])
        os.remove(self.paths.desk_toml)
        code, document = self.run_json(*self.argv(BRIEF_NOW))
        self.assertEqual(1, code)
        self.assertEqual("config_missing", document["error"]["code"])
        code, document = self.run_json(*self.argv(BRIEF_NOW, "--profile", "lean"))
        self.assertEqual(0, code)
        with open(self.paths.calendar_toml, "w", encoding="utf-8") as handle:
            handle.write("schema_version = [\n")
        code, document = self.run_json(*self.argv(BRIEF_NOW, "--profile", "lean"))
        self.assertEqual(1, code)
        self.assertEqual("calendar_invalid", document["error"]["code"])

    def test_invalid_desk_toml_is_exit_1(self) -> None:
        with open(self.paths.desk_toml, "a", encoding="utf-8") as handle:
            handle.write("\n[desk2]\nx = 1\n")
        code, document = self.run_json(*self.argv(BRIEF_NOW))
        self.assertEqual(1, code)
        self.assertEqual("config_invalid", document["error"]["code"])

    def test_timezone_offset_suspected_is_a_warning(self) -> None:
        for routine_id, slot, started in (("preopen", "07:30", "08:32"), ("book-check", "10:05", "11:07"),
                                          ("book-check", "12:05", "13:07")):
            self.beat(routine_id, slot, et(TUESDAY, started))
        code, out = self.run_text(*self.argv("2026-11-03T18:30:00Z"))
        self.assertEqual(0, code)
        self.assertIn("timezone offset suspected: every run of Tue 2026-11-03 started about 60 min after its slot; "
                      "check the time zone of the routine schedule", out.splitlines())

    def test_a_run_before_its_slot(self) -> None:
        self.beat("preopen", "07:30", et(TUESDAY, "07:31"))
        self.beat("book-check", "10:05", et(TUESDAY, "10:00"))
        code, out = self.run_text(*self.argv("2026-11-03T15:02:00Z"))
        self.assertEqual(0, code)
        self.assertIn("  book-check 10:05 ET: ran at 10:00 ET (5 min before its slot), exit 0 ok", out.splitlines())


if __name__ == "__main__":
    unittest.main()
