"""``scripts/alert_lint.py`` run in-process: exit 0 for every passing fixture, 1 for every red-team fixture (with the
lint's finding codes in the ``--json`` payload), 1 for an unreadable message or emitted file, 2 for usage errors;
``--schema`` describes the payload; the output never repeats a forbidden phrase it reports (journeys screen tool
output for order-shaped text); the shared flags of a journey (``--offline --desk-root --now``) are accepted and
nothing under the desk root is written."""

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

from aegis_desk import alert_lint as library

import alert_lint as tool  # the tool scripts/alert_lint.py, not the library aegis_desk.alert_lint
from tests.engine.test_alert_lint import (PASS_FIXTURES, STRUCTURING_PASS_FIXTURES, fail_fixture_table,
                                          structuring_fail_fixture_table)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
ALERTS = os.path.join(ROOT, "tests", "fixtures", "alerts")
NOW = "2026-11-20T19:50:00Z"


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = tool.main(list(argv))
    return code, buffer.getvalue()


def run_json(argv: Sequence[str]) -> Tuple[int, Dict[str, Any]]:
    code, out = run_tool(list(argv) + ["--json"])
    return code, json.loads(out)


def fixture_argv(name: str) -> List[str]:
    return ["--message-file", os.path.join(ALERTS, name + ".txt"), "--emitted-file",
            os.path.join(ALERTS, name + ".emitted.json")]


class ToolTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="aegis-alert-lint-tool-")
        self.tmp = os.path.realpath(self._tmp)

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def write(self, name: str, data: bytes) -> str:
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(data)
        return path

    def assert_no_forbidden_text(self, out: str) -> None:
        for pattern in library.FORBIDDEN_PATTERNS:
            self.assertIsNone(pattern.search(out), (pattern.pattern, out))

    def test_schema(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual("alert_lint", body["tool"])
        self.assertEqual("none", body["network_access"])
        payload = body["envelope"]["payload"]
        self.assertEqual({"ok", "problems", "findings", "numbers_checked", "message_file", "emitted_file", "rule_ids"},
                         set(payload))
        for code_name in library.CODES:
            self.assertIn(code_name, payload["findings"][0]["code"])
        self.assertIn("1", body["exit_codes"])

    def test_footer_snapshot_flag(self) -> None:
        stamp = datetime.datetime(2026, 11, 3, 19, 31, 12, tzinfo=datetime.timezone.utc)
        text = "SPY 648.12 mid, as of 19:31 UTC\n" + library.snapshot_footer_line(stamp)
        message_file = self.write("snapshot.txt", text.encode("utf-8"))
        emitted_file = self.write("snapshot.emitted.json", json.dumps(["648.12"]).encode("utf-8"))
        argv = ["--message-file", message_file, "--emitted-file", emitted_file]
        self.assertEqual(0, run_tool(argv + ["--footer", "snapshot"])[0])
        code, body = run_json(argv)
        self.assertEqual(1, code)
        self.assertIn("footer_missing", [item["code"] for item in body["payload"]["findings"]],
                      "without --footer snapshot the rules footer is required")
        declared = self.write("declared.emitted.json", json.dumps({"emitted_numbers": ["648.12"],
                                                                   "footer": "snapshot"}).encode("utf-8"))
        self.assertEqual(0, run_tool(["--message-file", message_file, "--emitted-file", declared])[0],
                         "a document that declares the snapshot footer is linted with it")
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as raised:
            run_tool(argv + ["--footer", "market"])
        self.assertEqual(2, raised.exception.code, "an unknown footer kind is a usage error")

    def test_footer_structuring_flag(self) -> None:
        footer = [action for action in tool.build_parser("0")._actions if "--footer" in action.option_strings][0]
        self.assertEqual(library.FOOTER_KINDS, tuple(footer.choices))
        self.assertEqual("the footer the last line must be: rules (every alert, sheet and summary), snapshot "
                         "(opening_bell.py's rule-free market snapshot only) or structuring (the Structuring Desk's "
                         "tickets, roll analyses and proposals only); default: the emitted document's footer key, else "
                         "rules", footer.help)
        stamp = datetime.datetime(2026, 11, 3, 19, 31, 12, tzinfo=datetime.timezone.utc)
        leg = "leg 1 of 1: closes your short 2 x WMT 2026-11-20 105 call | ask 3.50"
        message_file = self.write("structuring.txt", (leg + "\n" + library.structuring_footer_line(stamp)).encode("utf-8"))
        emitted_file = self.write("structuring.emitted.json", json.dumps(["1", "2", "105", "3.50"]).encode("utf-8"))
        argv = ["--message-file", message_file, "--emitted-file", emitted_file]
        self.assertEqual(0, run_tool(argv + ["--footer", "structuring"])[0])
        code, body = run_json(argv)
        self.assertEqual(1, code)
        self.assertIn("footer_missing", [item["code"] for item in body["payload"]["findings"]],
                      "without --footer structuring the rules footer is required")
        self.assertEqual(1, run_tool(argv + ["--footer", "snapshot"])[0])
        declared = self.write("declared.emitted.json", json.dumps({"emitted_numbers": ["1", "2", "105", "3.50"],
                                                                   "footer": "structuring"}).encode("utf-8"))
        self.assertEqual(0, run_tool(["--message-file", message_file, "--emitted-file", declared])[0],
                         "a document that declares the structuring footer is linted with it")
        self.assertEqual(1, run_tool(["--message-file", message_file, "--emitted-file", declared, "--footer", "rules"])[0],
                         "--footer overrides the document's key")
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)

    def test_structuring_fixtures_exit_0_and_1(self) -> None:
        for name in STRUCTURING_PASS_FIXTURES:
            with self.subTest(name=name):
                code, body = run_json(fixture_argv(name))
                self.assertEqual(0, code, body)
                self.assertEqual((True, []), (body["payload"]["ok"], body["payload"]["problems"]))
                self.assertEqual([], body["payload"]["rule_ids"])
        for name, (_base, _changes, _document, codes) in structuring_fail_fixture_table().items():
            with self.subTest(name=name):
                code, body = run_json(fixture_argv(name))
                self.assertEqual(1, code)
                self.assertEqual(sorted(set(codes)), sorted({finding["code"] for finding in body["payload"]["findings"]}))
                self.assert_no_forbidden_text(json.dumps(body))
                code, out = run_tool(fixture_argv(name))
                for entry in library.STRUCTURING_FORBIDDEN_LANGUAGE:
                    self.assertIsNone(entry.pattern.search(out), (entry.code, out))

    def test_passing_fixtures_exit_0(self) -> None:
        for name in PASS_FIXTURES:
            with self.subTest(name=name):
                code, body = run_json(fixture_argv(name))
                self.assertEqual(0, code, body)
                self.assertEqual("ok", body["status"])
                payload = body["payload"]
                self.assertEqual((True, [], []), (payload["ok"], payload["problems"], payload["findings"]))
                self.assertGreater(payload["numbers_checked"], 0)
                self.assertEqual(os.path.join(ALERTS, name + ".txt"), payload["message_file"])
                self.assertIsInstance(payload["rule_ids"], list)
                self.assertIsNone(body["source"])

    def test_red_team_fixtures_exit_1_with_their_codes(self) -> None:
        for name, (_base, _changes, _emitted, codes) in fail_fixture_table().items():
            with self.subTest(name=name):
                code, body = run_json(fixture_argv(name))
                self.assertEqual(1, code)
                self.assertEqual("error", body["status"])
                payload = body["payload"]
                self.assertFalse(payload["ok"])
                self.assertEqual(sorted(set(codes)), sorted({finding["code"] for finding in payload["findings"]}))
                self.assertEqual([finding["message"] for finding in payload["findings"]], payload["problems"])
                self.assert_no_forbidden_text(json.dumps(body))

    def test_prose(self) -> None:
        code, out = run_tool(fixture_argv("pass_exit_signal"))
        self.assertEqual(0, code)
        self.assertRegex(out, r"^ok: the message passes the alert lint \(\d+ numbers checked\)\n$")
        for name in fail_fixture_table():
            with self.subTest(name=name):
                code, out = run_tool(fixture_argv(name))
                self.assertEqual(1, code)
                lines = out.splitlines()
                self.assertRegex(lines[0], r"^alert lint: \d+ problems?; the message must not be posted$")
                self.assertEqual(len(lines) - 1, int(lines[0].split()[2]))
                self.assert_no_forbidden_text(out)

    def test_journey_flags_are_accepted_and_nothing_is_written(self) -> None:
        desk = os.path.join(self.tmp, "desk")
        shutil.copytree(FIXTURE_DESK, desk)
        before = sorted(os.walk(desk))
        code, body = run_json(fixture_argv("pass_exit_signal") + ["--offline", "--desk-root", desk, "--now", NOW])
        self.assertEqual(0, code, body)
        self.assertEqual("2026-11-20T19:50:00+00:00", body["generated_at"])
        self.assertEqual(before, sorted(os.walk(desk)))

    def test_relative_paths_resolve_against_the_working_directory(self) -> None:
        previous = os.getcwd()
        os.chdir(ROOT)
        try:
            code, body = run_json(["--message-file", "tests/fixtures/alerts/pass_watch.txt", "--emitted-file",
                                   "tests/fixtures/alerts/pass_watch.emitted.json"])
        finally:
            os.chdir(previous)
        self.assertEqual(0, code, body)
        self.assertEqual("tests/fixtures/alerts/pass_watch.txt", body["payload"]["message_file"])

    def test_the_strict_document_refuses_invented_identifiers_and_a_moved_footer(self) -> None:
        with open(os.path.join(ALERTS, "pass_exit_signal.txt"), encoding="utf-8") as handle:
            text = handle.read()
        emitted = os.path.join(ALERTS, "pass_exit_signal.emitted.json")
        for old, new, code_name in (("sha 3f9a01bc", "sha 00000110", "identifier_not_emitted"),
                                    ("as of 19:31 UTC\n", "as of 19:49 UTC\n", "footer_missing")):
            with self.subTest(change=new):
                self.assertEqual(1, text.count(old))
                path = self.write("mutated.txt", text.replace(old, new).encode("utf-8"))
                code, body = run_json(["--message-file", path, "--emitted-file", emitted])
                self.assertEqual(1, code, body)
                self.assertIn(code_name, [finding["code"] for finding in body["payload"]["findings"]])

    def test_a_plain_list_emitted_file(self) -> None:
        with open(os.path.join(ALERTS, "pass_pre_close.emitted.json"), encoding="utf-8") as handle:
            numbers = json.load(handle)["emitted_numbers"]
        emitted = self.write("list.json", json.dumps(numbers).encode("utf-8"))
        code, body = run_json(["--message-file", os.path.join(ALERTS, "pass_pre_close.txt"), "--emitted-file", emitted])
        self.assertEqual(0, code, body)
        self.assertIsNone(body["payload"]["rule_ids"])

    def test_unreadable_inputs_exit_1(self) -> None:
        message = os.path.join(ALERTS, "pass_watch.txt")
        emitted = os.path.join(ALERTS, "pass_watch.emitted.json")
        cases = [
            (["--message-file", os.path.join(self.tmp, "absent.txt"), "--emitted-file", emitted], "message_unreadable"),
            (["--message-file", self.write("latin1.txt", b"caf\xe9\n"), "--emitted-file", emitted], "message_unreadable"),
            (["--message-file", message, "--emitted-file", os.path.join(self.tmp, "absent.json")], "emitted_unreadable"),
            (["--message-file", message, "--emitted-file", self.write("bad.json", b"{not json")], "emitted_invalid"),
            (["--message-file", message, "--emitted-file", self.write("num.json", b"[1, 2]")], "emitted_invalid"),
            (["--message-file", message, "--emitted-file", self.write("obj.json", b'{"numbers": []}')], "emitted_invalid"),
            (["--message-file", message, "--emitted-file", self.write("bin.json", b"\xff\xfe")], "emitted_invalid"),
        ]
        for argv, error_code in cases:
            with self.subTest(error=error_code, argv=argv[1]):
                code, body = run_json(argv)
                self.assertEqual(1, code)
                self.assertEqual(("error", error_code), (body["status"], body["error"]["code"]))
                code, out = run_tool(argv)
                self.assertEqual(1, code)
                self.assertTrue(out.startswith("error: "), out)

    def test_usage_errors_exit_2(self) -> None:
        message = os.path.join(ALERTS, "pass_watch.txt")
        for argv in ([], ["--message-file", message], ["--emitted-file", message], ["--bogus"],
                     ["--message-file", message, "--emitted-file", message, "--offline"]):
            with self.subTest(argv=argv):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    with self.assertRaises(SystemExit) as raised:
                        tool.main(list(argv) + ["--json"])
                self.assertEqual(2, raised.exception.code)
                self.assertEqual("usage", json.loads(buffer.getvalue())["status"])
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                tool.main([])
        self.assertEqual(2, raised.exception.code)


if __name__ == "__main__":
    unittest.main()
