"""``scripts/trigger_levels.py`` run in-process against copied fixture desk roots: the level sheet of the adopted
rules (PR 2d decisions D1-1 to D1-10), written under the desk lock to ``levels/<D>.md`` and ``levels/<D>.json`` (and
``plans/<group>.md`` with ``--plan``), linted strictly before anything prints, never touching the rule state; the
saved-snapshot source (``--from-snapshot``), the exit codes (0, 20 for an unavailable book, a missing snapshot,
refused market data or a busy lock, 1 for errors and a failing lint, 2 for usage, never 10), the JSON payload and
the goldens of the regular, expiry_day and LOSSX-variant books recomputed with the landed tools."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Sequence, Tuple
from unittest import mock

import book as book_tool
import book_check as book_check_tool
import trigger_levels as tool  # the tool scripts/trigger_levels.py
from aegis_desk import alert_lint, desk_paths, levels, state
from tests.engine.test_levels import ALERT, REGULAR_SHEET, WMT_EQUITY_ROW

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
EXPIRY_DAY_POST = "2026-11-20T19:50:00Z"
EXPIRY_DAY_NOW = "2026-11-20T19:55:00Z"
EXPIRY_NEXT_RUN = "2026-11-20T21:35:00Z"
WMT, NVDA, SPY = "AG-20261020-01", "AG-20261022-02", "AG-20261028-03"
LEVELS_ORDER_SHAPE = "[levels]\norder_shape_enabled = false\n"
TP80_REGULAR = REGULAR_SHEET[3]
TP80_CROSSED = ("TP80 fires at an option price of 0.65 (rounded down from 0.684 = credit 3.42 x (1 - 80 %)); threshold "
                "already crossed (rule fired)")
LOSSX = {
    "penny_all_classes": "LOSSX fires at an option price of 10.26 (= credit 3.42 x (1 + 2)); " + ALERT,
    "penny_classes": "LOSSX fires at an option price of 10.25 (rounded down from 10.26 = credit 3.42 x (1 + 2)); " + ALERT,
    None: "LOSSX fires at an option price of 10.20 (rounded down from 10.26 = credit 3.42 x (1 + 2)); " + ALERT,
}


def run_module(module: Any, argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = module.main(list(argv))
    return code, buffer.getvalue()


def order_shaped(text: str) -> List[str]:
    found = [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(text)]
    words = ("bu" + "y", "se" + "ll", "G" + "TC", "da" + "y or" + "der", "wor" + "king")
    return found + [word for word in words if re.search(r"\b%s\b" % word, text, re.IGNORECASE)]


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-trigger-levels-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, *extra: str, now: str = REGULAR_NOW, fixture_set: str = "regular", json_mode: bool = True) -> List[str]:
        argv = ["--offline", "--desk-root", self.root, "--now", now] + list(extra)
        if fixture_set:
            argv += ["--fixture-set", fixture_set]
        if json_mode:
            argv.append("--json")
        return argv

    def run_json(self, *extra: str, **options: Any) -> Tuple[int, Dict[str, Any]]:
        code, out = run_module(tool, self.argv(*extra, **options))
        self.assertEqual([], order_shaped(out))
        return code, json.loads(out)

    def run_text(self, *extra: str, **options: Any) -> Tuple[int, str]:
        code, out = run_module(tool, self.argv(*extra, json_mode=False, **options))
        self.assertEqual([], order_shaped(out))
        return code, out

    def rewrite(self, name: str, old: str, new: str) -> None:
        path = os.path.join(self.root, name)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(1, text.count(old), old)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))

    def read(self, rel: str) -> str:
        with open(os.path.join(self.root, *rel.split("/")), encoding="utf-8") as handle:
            return handle.read()

    def exists(self, rel: str) -> bool:
        return os.path.exists(os.path.join(self.root, *rel.split("/")))

    def sha(self, rel: str) -> str:
        with open(os.path.join(self.root, *rel.split("/")), "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()

    def snapshot(self, fixture_set: str = "regular", now: str = REGULAR_NOW) -> None:
        code, out = run_module(book_tool, ["--snapshot", "--offline", "--desk-root", self.root, "--now", now,
                                           "--fixture-set", fixture_set, "--json"])
        self.assertEqual(0, code, out)


class SchemaAndUsageTest(DeskCopy):
    def test_schema_describes_the_payload(self) -> None:
        code, out = run_module(tool, ["--schema"])
        self.assertEqual(0, code)
        body = json.loads(out)
        self.assertEqual("trigger_levels", body["tool"])
        described = body["envelope"]["payload"]
        code, body = self.run_json()
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual(set(described), set(payload))
        self.assertEqual(set(tool.PAYLOAD_KEYS), set(payload))
        self.assertEqual(set(described["groups"][0]), set(payload["groups"][0]))
        self.assertEqual(set(described["groups"][0]["lines"][0]), set(payload["groups"][0]["lines"][0]))
        self.assertEqual(set(described["sections"][0]), set(payload["sections"][0]))
        self.assertEqual(set(described["emitted"]), set(payload["emitted"]))
        flags = {flag for action in tool.build_parser("0.0.0")._actions for flag in action.option_strings}
        self.assertTrue({"--plan", "--from-snapshot", "--fixture-set"} <= flags, "routine.py passes --fixture-set")
        self.assertEqual([], order_shaped(out))

    def test_usage_errors(self) -> None:
        for argv in (["--json", "--desk-root", self.root, "--fixture-set", "regular"],
                     self.argv("--from-snapshot"),
                     self.argv("--plan", "AG-1", "--bogus"),
                     self.argv("--timeout", "0")):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_module(tool, argv)
                self.assertEqual(2, ctx.exception.code)


class RegularTest(DeskCopy):
    def test_the_sheet_the_files_and_the_payload(self) -> None:
        code, body = self.run_json()
        self.assertEqual((0, "ok"), (code, body["status"]))
        payload = body["payload"]
        text = "\n".join(REGULAR_SHEET)
        self.assertEqual(text, payload["message"])
        self.assertEqual(text, payload["text"])
        self.assertEqual({"ok": True, "problems": []}, {key: payload["lint"][key] for key in ("ok", "problems")})
        self.assertGreater(payload["lint"]["numbers_checked"], 40)
        self.assertEqual(set(alert_lint.EMITTED_DOCUMENT_KEYS), set(payload["emitted"]))
        self.assertTrue(alert_lint.load_emitted(payload["emitted"]).check(payload["message"]).ok)
        self.assertEqual(["levels/2026-11-03.md", "levels/2026-11-03.json"], payload["written"])
        self.assertEqual(text, self.read("levels/2026-11-03.md"))
        self.assertEqual(json.loads(json.dumps(payload)), json.loads(self.read("levels/2026-11-03.json")))
        self.assertEqual("2026-11-03", payload["et_date"])
        self.assertEqual({"origin": "quotes", "path": None, "generated_at": "2026-11-03T19:50:00+00:00",
                          "as_of": "2026-11-03T19:31:12+00:00"}, payload["book"])
        self.assertEqual({"version": "1.0.0",
                          "sha256": "cc09859c896e22f84402ce8b686d0f79ec920b503b647754232247668b8bc5fe"},
                         payload["rulebook"])
        counts = {group["group_id"]: (group["price_levels"], group["resting_eligible"]) for group in payload["groups"]}
        self.assertEqual({WMT: (3, 1), NVDA: (5, 1), SPY: (3, 0)}, counts)
        self.assertEqual((None, [], "tradier:sandbox", 15), (payload["plan"], payload["warnings"], body["source"],
                                                            body["delay_min"]))
        self.assertFalse(self.exists("state/rule_state.json"), "trigger_levels never writes the rule state")
        self.assertFalse(self.exists("plans/%s.md" % WMT))

    def test_human_mode_prints_the_message(self) -> None:
        code, out = self.run_text()
        self.assertEqual(0, code)
        self.assertEqual("\n".join(REGULAR_SHEET) + "\n", out)

    def test_the_rule_state_is_read_never_written(self) -> None:
        code, _ = run_module(book_check_tool, ["--post", "--next-run-utc", EXPIRY_NEXT_RUN, "--fixture-set", "expiry_day",
                                               "--json", "--offline", "--now", EXPIRY_DAY_POST, "--desk-root", self.root])
        self.assertEqual(10, code)
        before = self.sha("state/rule_state.json")
        code, body = self.run_json(now=EXPIRY_DAY_NOW, fixture_set="expiry_day")
        self.assertEqual((20, "unavailable"), (code, body["status"]), "the WMT group is unavailable (grade E)")
        self.assertEqual(before, self.sha("state/rule_state.json"))
        message = body["payload"]["message"]
        self.assertIn(TP80_CROSSED, message.split("\n"))
        self.assertTrue(body["payload"]["lint"]["ok"], body["payload"]["lint"])
        self.assertEqual(["levels/2026-11-20.md", "levels/2026-11-20.json"], body["payload"]["written"])
        self.assertIn("CAP fires at NVDA 150.01 or above (one cent above the strike 150); threshold already crossed (rule "
                      "fired)", message.split("\n"))
        self.assertIn("your rule ROLL30 fires on 2026-11-18 (30 calendar days before the last trading day 2026-12-18); "
                      "threshold already crossed (rule fired)", message.split("\n"))

    def test_without_the_recorded_fire_tp80_is_pending(self) -> None:
        code, body = self.run_json(now=EXPIRY_DAY_NOW, fixture_set="expiry_day")
        self.assertEqual(20, code)
        self.assertIn(TP80_CROSSED.replace("(rule fired)", "(pending confirmation: observation 1 of 2)"),
                      body["payload"]["message"].split("\n"))

    def test_order_shape_enabled_changes_no_text(self) -> None:
        _code, before = self.run_json()
        self.rewrite("desk.toml", "order_shape_enabled = false", "order_shape_enabled = true")
        code, after = self.run_json()
        self.assertEqual(0, code)
        self.assertEqual(before["payload"]["message"], after["payload"]["message"])
        self.assertEqual([levels.ORDER_SHAPE_WARNING], after["payload"]["warnings"])
        self.assertEqual([], order_shaped(after["payload"]["message"]))
        code, out = self.run_text()
        self.assertEqual("\n".join(REGULAR_SHEET) + "\nwarning: " + levels.ORDER_SHAPE_WARNING + "\n", out)

    def test_a_stale_book_is_unavailable_and_still_written(self) -> None:
        code, body = self.run_json(fixture_set="stale")
        self.assertEqual((20, "unavailable"), (code, body["status"]))
        self.assertTrue(body["payload"]["lint"]["ok"], body["payload"]["lint"])
        self.assertIn("the desk cannot read this rule now", body["payload"]["message"])
        self.assertEqual(["levels/2026-11-03.md", "levels/2026-11-03.json"], body["payload"]["written"])


class PlanCardTest(DeskCopy):
    def test_the_plan_card_is_written(self) -> None:
        code, body = self.run_json("--plan", WMT)
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual(["levels/2026-11-03.md", "levels/2026-11-03.json", "plans/%s.md" % WMT], payload["written"])
        card = self.read("plans/%s.md" % WMT)
        self.assertEqual(card, payload["plan"]["text"])
        self.assertEqual({"group_id": WMT, "path": "plans/%s.md" % WMT},
                         {key: payload["plan"][key] for key in ("group_id", "path")})
        self.assertTrue(payload["plan"]["lint"]["ok"])
        self.assertTrue(alert_lint.load_emitted(payload["plan"]["emitted"]).check(card).ok)
        self.assertTrue(card.startswith("PLAN CARD %s | WMT covered call | rules 1.0.0, sha cc09859c\n" % WMT), card)
        self.assertIn("dates that matter: last trading day 2026-11-20; 21 trading days before it 2026-10-22; earnings "
                      "2026-11-17 (confirmed, research/calendar.toml); ex-dividend 2026-11-24 (confirmed, $0.2350 per "
                      "share); your broker's exercise cutoff 16:30 ET on 2026-11-20", card)
        self.assertEqual("\n".join(REGULAR_SHEET), payload["message"], "the message stays the sheet")
        code, out = self.run_text("--plan", WMT)
        self.assertEqual("\n".join(REGULAR_SHEET) + "\n\n" + card + "\n", out)

    def test_an_unknown_group_is_an_error_and_writes_nothing(self) -> None:
        code, body = self.run_json("--plan", "AG-20261231-99")
        self.assertEqual((1, "unknown_group"), (code, body["error"]["code"]))
        self.assertFalse(self.exists("levels/2026-11-03.md"))
        self.assertFalse(self.exists("plans/AG-20261231-99.md"))


class SnapshotTest(DeskCopy):
    def test_from_the_snapshot_of_record(self) -> None:
        self.snapshot()
        code, body = self.run_json("--from-snapshot", fixture_set="")
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual("\n".join(REGULAR_SHEET), payload["message"])
        self.assertEqual({"origin": "file", "path": "data/book/2026-11-03.json"},
                         {key: payload["book"][key] for key in ("origin", "path")})
        self.assertEqual(["levels/2026-11-03.md", "levels/2026-11-03.json"], payload["written"])

    def test_a_missing_snapshot_is_unavailable_and_writes_nothing(self) -> None:
        code, body = self.run_json("--from-snapshot", fixture_set="")
        self.assertEqual((20, "unavailable", "snapshot_missing"), (code, body["status"], body["error"]["code"]))
        self.assertEqual("book snapshot data/book/2026-11-03.json missing", body["error"]["message"])
        code, out = self.run_text("--from-snapshot", fixture_set="")
        self.assertEqual((20, "UNAVAILABLE: book snapshot data/book/2026-11-03.json missing\n"), (code, out))
        self.assertFalse(self.exists("levels"))

    def test_a_busy_lock_prints_the_sheet_and_writes_nothing(self) -> None:
        self.snapshot()
        with mock.patch.object(tool, "LOCK_TIMEOUT_S", 0.0):
            with state.locked(self.paths.lock_path):
                code, body = self.run_json("--from-snapshot", fixture_set="")
        self.assertEqual((20, "unavailable"), (code, body["status"]))
        payload = body["payload"]
        self.assertEqual("\n".join(REGULAR_SHEET), payload["message"])
        self.assertEqual([], payload["written"])
        self.assertEqual(1, len(payload["warnings"]))
        self.assertTrue(payload["warnings"][0].startswith("levels not written: lock "), payload["warnings"])
        self.assertFalse(self.exists("levels/2026-11-03.md"))


class VariantTest(DeskCopy):
    """The LOSSX goldens on a copy whose positions.csv drops the WMT stock row (a naked short call), for each tick
    class the copy's desk.toml declares (the committed fixture desk.toml stays byte-identical)."""

    def test_lossx_for_each_class(self) -> None:
        self.rewrite("positions.csv", WMT_EQUITY_ROW, "")
        for key, golden in LOSSX.items():
            with self.subTest(key=key):
                shutil.copyfile(os.path.join(FIXTURE_DESK, "desk.toml"), os.path.join(self.root, "desk.toml"))
                if key is not None:
                    self.rewrite("desk.toml", LEVELS_ORDER_SHAPE, LEVELS_ORDER_SHAPE + '%s = ["WMT"]\n' % key)
                code, body = self.run_json()
                self.assertEqual(0, code, body)
                lines = body["payload"]["message"].split("\n")
                self.assertIn(golden, lines)
                words = {"penny_all_classes": "0.01 below 3.00, 0.01 at or above (penny class at every price)",
                         "penny_classes": "0.01 below 3.00, 0.05 at or above (penny class)",
                         None: "0.05 below 3.00, 0.10 at or above (standard class: desk.toml [levels] names no penny "
                               "class for WMT)"}[key]
                self.assertIn("## %s | WMT short call | option ticks %s" % (WMT, words), lines)
                self.assertTrue(body["payload"]["lint"]["ok"], body["payload"]["lint"])
                group = next(item for item in body["payload"]["groups"] if item["group_id"] == WMT)
                self.assertEqual(("short_call", {"penny_all_classes": "penny_all", "penny_classes": "penny",
                                                 None: "standard"}[key]), (group["structure"], group["tick_class"]))
                lossx = next(line for line in group["lines"] if line["rule_id"] == "LOSSX")
                self.assertEqual(("option_price", "alert_level", "ask", "10.26"), (lossx["kind"], lossx["status"],
                                                                                    lossx["close_side"], lossx["exact"]))


class ErrorTest(DeskCopy):
    def test_desk_and_rulebook_errors(self) -> None:
        os.remove(self.paths.adopted_file)
        code, body = self.run_json()
        self.assertEqual((1, "not_adopted"), (code, body["error"]["code"]))
        self.assertFalse(self.exists("levels"))

    def test_config_error(self) -> None:
        self.rewrite("desk.toml", LEVELS_ORDER_SHAPE, LEVELS_ORDER_SHAPE + 'penny_classes = ["wmt"]\n')
        code, body = self.run_json()
        self.assertEqual((1, "config_invalid"), (code, body["error"]["code"]))
        self.assertIn("penny_classes", body["error"]["message"])

    def test_refused_market_data_is_unavailable(self) -> None:
        self.rewrite("desk.toml", 'host = "sandbox"', 'host = "production"')
        code, body = self.run_json()
        self.assertEqual((20, "unavailable"), (code, body["status"]))
        self.assertFalse(self.exists("levels/2026-11-03.md"))

    def test_a_failing_lint_is_exit_1_and_writes_nothing(self) -> None:
        real = levels.build_sheet

        def tampered(*args: Any, **kwargs: Any) -> levels.Sheet:
            sheet = real(*args, **kwargs)
            sheet.text = sheet.text.replace("13 trading days", "34-DTE", 1)  # 34 was never emitted
            return sheet

        with mock.patch.object(tool.levels, "build_sheet", tampered):
            code, body = self.run_json()
        self.assertEqual((1, "lint_failed"), (code, body["error"]["code"]))
        self.assertIn("number_not_emitted", body["error"]["message"])
        self.assertFalse(self.exists("levels/2026-11-03.md"))
        with mock.patch.object(tool.levels, "build_sheet", tampered):
            code, out = self.run_text()
        self.assertEqual(1, code)
        self.assertTrue(out.startswith("error: the level sheet failed alert_lint and was not printed"), out)
        self.assertNotIn("34-DTE", out)


if __name__ == "__main__":
    unittest.main()
