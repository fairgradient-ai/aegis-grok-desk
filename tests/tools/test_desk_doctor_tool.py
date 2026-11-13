"""``scripts/desk_doctor.py`` run in-process against a copied fixture desk root (PR 2d decision D2-12): the three
modes and the checks each runs, the verdict and exit codes (0 PASS, 20 FAIL with the checks in a success envelope,
1 when the doctor itself fails, 2 usage), ``state/doctor.json`` (full and quick modes, only with a ``desk.toml``), the
test seams (``CHECKOUT_RUN``, ``ENVIRON``, ``WORKSPACE_EXISTS``, ``LOCAL_OFFSET_S``, ``PAPER_CLIENT``) and the one
paper-key request, against the loopback fake Alpaca server. Nothing under ``tests/fixtures/`` is ever written."""

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
import threading
import unittest
from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest import mock

from aegis_desk import alert_lint, alpaca_client, desk_paths, doctor, heartbeat, state, version

import check_grok_template
import desk_doctor as tool
from tests.engine.test_alpaca_client import FakeAlpaca, static

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR = os.path.join(FIXTURE_DESK, "fixtures", "alpaca", "regular")
REGULAR_NOW = "2026-11-03T19:50:00Z"
KEY_ID = "PKFAKE-doctor-key-0000"
SECRET = "fake-paper-secret-0000-1111-2222-3333"
HEAD = "971186c676d34b0a5d4e35c691d7888632132b8a"
TEMPLATE_OK = {"ok": True, "errors": [], "skips": [], "status": "unpublished", "release": "0.1.0"}
QUICK_IDS = ["interpreter", "holiday_table", "key_source", "desk_files", "market_status"]
FULL_IDS = ["release_pin", "interpreter", "holiday_table", "key_source", "paper_key", "machine_role", "broker_env",
            "desk_files", "market_status", "connector_readback", "heartbeats"]
DESK_FILES_PASS = ("desk.toml loads (sha f08a18dc); positions.csv parses: 7 rows in 4 groups, none quarantined; "
                   "rules 1.0.0, sha cc09859c; research/calendar.toml loads")
MISSED_AT_REGULAR = ("preopen 07:30 ET: no heartbeat 45 min after the slot; book-check 10:05 ET: no heartbeat 45 min "
                     "after the slot; book-check 12:05 ET: no heartbeat 45 min after the slot; book-check 14:05 ET: no "
                     "heartbeat 45 min after the slot")


class Answer:
    def __init__(self, returncode: int = 0, stdout: bytes = b"", stderr: bytes = b"") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def git_answers(tag: Optional[str], status: bytes = b"") -> Any:
    """A ``CHECKOUT_RUN`` seam answering the pin check's four literal argument lists."""
    def run(root: str, args: List[str]) -> Answer:
        if args == ["rev-parse", "--is-inside-work-tree"]:
            return Answer(0, b"true\n")
        if args == ["rev-parse", "--verify", "--quiet", "HEAD^{commit}"]:
            return Answer(0, (HEAD + "\n").encode())
        if args[:3] == ["rev-parse", "--verify", "--quiet"] and args[3].startswith("refs/tags/v"):
            return Answer(0, (tag + "\n").encode()) if tag else Answer(1)
        if args == ["--no-optional-locks", "status", "--porcelain", "--untracked-files=all"]:
            return Answer(0, status)
        raise AssertionError("unexpected git arguments %r" % (args,))
    return run


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = tool.main(list(argv))
    return code, buffer.getvalue()


def order_shaped(text: str) -> List[str]:
    return [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(text)]


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-desk-doctor-")
        self.base = os.path.realpath(self._tmp.name)
        self.root = os.path.join(self.base, "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)
        for name, value in (("CHECKOUT_RUN", git_answers(None)), ("ENVIRON", {"HOME": "/home/owner", "LANG": "C.UTF-8"}),
                            ("WORKSPACE_EXISTS", True), ("LOCAL_OFFSET_S", 0), ("PAPER_CLIENT", self.no_client)):
            patcher = mock.patch.object(tool, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # the template check's seam (the gate lets nothing but a call reach the wrapper module itself)
        self.template = mock.Mock(return_value=dict(TEMPLATE_OK))
        template = mock.patch.object(tool, "TEMPLATE_RUN", self.template)
        template.start()
        self.addCleanup(template.stop)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    @staticmethod
    def no_client(credential: Any, timeout_s: float, user_agent: str) -> Any:
        raise AssertionError("this run must send no request")

    def argv(self, *flags: str, now: str = REGULAR_NOW, offline: bool = True, json_mode: bool = True,
             root: Optional[str] = None) -> List[str]:
        argv = list(flags) + ["--desk-root", root or self.root, "--now", now]
        if offline:
            argv.append("--offline")
        if json_mode:
            argv.append("--json")
        return argv

    def run_json(self, *flags: str, **kwargs: Any) -> Tuple[int, Dict[str, Any]]:
        code, out = run_tool(self.argv(*flags, **kwargs))
        self.assertEqual([], order_shaped(out))
        return code, json.loads(out)

    def checks(self, body: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
        return {item["id"]: item for item in body["payload"]["checks"]}

    def record(self) -> Dict[str, Any]:
        document, status = state.read_json(os.path.join(self.root, "state", "doctor.json"))
        self.assertEqual("ok", status)
        return document

    def files(self, root: Optional[str] = None) -> List[str]:
        found = []
        base = root or self.root
        for folder, _dirs, names in os.walk(base):
            found.extend(os.path.relpath(os.path.join(folder, name), base) for name in names)
        return sorted(found)

    def rewrite(self, rel: str, old: str, new: str) -> None:
        path = os.path.join(self.root, rel)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))


class SchemaAndUsageTest(DeskCopy):
    def test_schema(self) -> None:
        code, out = run_tool(["--schema"])
        self.assertEqual(0, code)
        body = json.loads(out)
        self.assertEqual("desk_doctor", body["tool"])
        payload = body["envelope"]["payload"]
        for key in ("mode", "verdict", "checks", "counts", "warned", "failed", "machine_role", "release", "record",
                    "written"):
            self.assertIn(key, payload)
        self.assertIn("https://paper-api.alpaca.markets/v2/clock", body["network_access"])

    def test_usage_errors(self) -> None:
        for argv in (["--quick", "--selftest", "--json"], ["--deep", "--json"], ["--quick", "--offline", "--json"]):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(argv)
                self.assertEqual(2, ctx.exception.code)


class QuickModeTest(DeskCopy):
    def test_quick_offline_on_the_fixture_root_passes_and_records(self) -> None:
        before = self.files()
        code, body = self.run_json("--quick")
        self.assertEqual((0, "ok", "desk_doctor"), (code, body["status"], body["tool"]))
        payload = body["payload"]
        self.assertEqual(("quick", "PASS", QUICK_IDS), (payload["mode"], payload["verdict"],
                                                        [item["id"] for item in payload["checks"]]))
        self.assertEqual([2, 3, 4, 8, 9], [item["number"] for item in payload["checks"]])
        checks = self.checks(body)
        self.assertEqual(("WARN", "not checked offline (offline runs load no credential)"),
                         (checks["key_source"]["status"], checks["key_source"]["reason"]))
        self.assertEqual(("PASS", DESK_FILES_PASS), (checks["desk_files"]["status"], checks["desk_files"]["reason"]))
        self.assertEqual(("PASS", "holiday table covers 2026-2027 (expires 2027-12-31)"),
                         (checks["holiday_table"]["status"], checks["holiday_table"]["reason"]))
        self.assertEqual("session open at 14:50 ET; America/New_York offset -05:00; local offset +00:00",
                         checks["market_status"]["reason"])
        self.assertEqual(({"pass": 4, "warn": 1, "fail": 0}, ["key_source"], [], "shared", ["state/doctor.json"],
                          "state/doctor.json"),
                         (payload["counts"], payload["warned"], payload["failed"], payload["machine_role"],
                          payload["written"], payload["record"]))
        self.assertEqual({"schema": 1, "run_utc": "2026-11-03T19:50:00+00:00", "mode": "quick", "verdict": "PASS",
                          "checks": payload["checks"], "machine_role": "shared", "tool_version": version.release_version()},
                         self.record())
        self.assertEqual(sorted(before + ["state/doctor.json"]), self.files(), "the doctor writes its record only")

    def test_human_output_lists_every_check_and_the_verdict(self) -> None:
        code, text = run_tool(self.argv("--quick", json_mode=False))
        self.assertEqual(0, code)
        self.assertEqual([], order_shaped(text))
        lines = text.splitlines()
        self.assertEqual("desk doctor, quick check at 2026-11-03 19:50 UTC (release %s)" % version.release_version(), lines[0])
        self.assertEqual("[WARN] 4 key_source: not checked offline (offline runs load no credential)", lines[3])
        self.assertEqual("[PASS] 8 desk_files: " + DESK_FILES_PASS, lines[4])
        self.assertEqual("verdict: PASS (5 checks; warnings: key_source; failures: none); state/doctor.json written",
                         lines[-1])
        self.assertEqual(7, len(lines))

    def test_without_desk_toml_the_quick_check_fails_and_records_nothing(self) -> None:
        os.remove(self.paths.desk_toml)
        before = self.files()
        code, body = self.run_json("--quick")
        self.assertEqual((20, "unavailable", "FAIL"), (code, body["status"], body["payload"]["verdict"]))
        checks = self.checks(body)
        self.assertEqual("FAIL", checks["desk_files"]["status"])
        self.assertIn("cannot read desk.toml", checks["desk_files"]["reason"])
        self.assertEqual(([], None, ["desk_files"]), (body["payload"]["written"], body["payload"]["record"],
                                                      body["payload"]["failed"]))
        self.assertEqual(before, self.files())
        code, text = run_tool(self.argv("--quick", json_mode=False))
        self.assertEqual(20, code)
        self.assertTrue(text.splitlines()[-1].endswith("state/doctor.json not written (no desk.toml at the desk root)"))

    def test_an_expiring_holiday_table_fails(self) -> None:
        code, body = self.run_json("--quick", now="2027-11-15T15:00:00Z")
        self.assertEqual((20, "FAIL"), (code, body["payload"]["verdict"]))
        self.assertEqual(("FAIL", "holiday table expires 2027-12-31: update the release"),
                         (self.checks(body)["holiday_table"]["status"], self.checks(body)["holiday_table"]["reason"]))
        self.assertEqual("FAIL", self.record()["verdict"])

    def test_a_foreign_local_offset_warns(self) -> None:
        with mock.patch.object(tool, "LOCAL_OFFSET_S", 3600):
            code, body = self.run_json("--quick")
        self.assertEqual((0, "PASS"), (code, body["payload"]["verdict"]))
        self.assertEqual(("WARN", "local timezone offset +01:00 is neither UTC nor America/New_York (-05:00); session open "
                                  "at 14:50 ET"),
                         (self.checks(body)["market_status"]["status"], self.checks(body)["market_status"]["reason"]))

    def test_a_record_that_cannot_be_written_is_exit_1(self) -> None:
        os.makedirs(os.path.join(self.root, "state", "doctor.json"))
        code, body = self.run_json("--quick")
        self.assertEqual((1, "error", "io_error"), (code, body["status"], body["error"]["code"]))


class SelftestModeTest(DeskCopy):
    def test_selftest_reads_and_writes_no_desk_file(self) -> None:
        empty = os.path.join(self.base, "empty")
        os.makedirs(empty)
        code, body = self.run_json("--selftest", root=empty)
        self.assertEqual((0, "ok", "selftest", "PASS"), (code, body["status"], body["payload"]["mode"],
                                                          body["payload"]["verdict"]))
        self.assertEqual(["interpreter", "selftest"], [item["id"] for item in body["payload"]["checks"]])
        self.assertEqual(("PASS", "engine selftest: 5 of 5 sub-checks passed (bsm golden, iv round trip, DST transitions, "
                                  "TOML reader, alert lint)"),
                         (self.checks(body)["selftest"]["status"], self.checks(body)["selftest"]["reason"]))
        self.assertEqual(([], None), (body["payload"]["written"], body["payload"]["record"]))
        self.assertEqual([], self.files(empty))
        before = self.files()
        code, _body = self.run_json("--selftest")
        self.assertEqual(0, code)
        self.assertEqual(before, self.files(), "the selftest never writes state/doctor.json")

    def test_a_broken_engine_fails_the_selftest(self) -> None:
        with mock.patch.object(doctor.toml_compat, "loads", side_effect=ValueError("broken reader")):
            code, body = self.run_json("--selftest")
        self.assertEqual((20, "unavailable"), (code, body["status"]))
        self.assertEqual(("FAIL", "engine selftest: TOML reader"),
                         (self.checks(body)["selftest"]["status"], self.checks(body)["selftest"]["reason"]))


class FullModeTest(DeskCopy):
    def test_full_offline_on_a_development_checkout(self) -> None:
        code, body = self.run_json()
        self.assertEqual((0, "full", "PASS"), (code, body["payload"]["mode"], body["payload"]["verdict"]))
        self.assertEqual(FULL_IDS, [item["id"] for item in body["payload"]["checks"]])
        self.assertEqual(list(range(1, 12)), [item["number"] for item in body["payload"]["checks"]])
        checks = self.checks(body)
        expected = {
            "release_pin": ("WARN", "development checkout (no tag)"),
            "key_source": ("WARN", "not checked offline (offline runs load no credential)"),
            "paper_key": ("WARN", "not verified offline"),
            "machine_role": ("PASS", "machine role: shared"),
            "broker_env": ("PASS", "no broker-looking environment variable name (2 names checked)"),
            "desk_files": ("PASS", DESK_FILES_PASS),
            "connector_readback": ("WARN", "re-confirm connected apps and MCP servers (state/desk_state.json records no "
                                           "connector_readback_utc)"),
            "heartbeats": ("WARN", MISSED_AT_REGULAR),
        }
        for check_id, pair in expected.items():
            self.assertEqual(pair, (checks[check_id]["status"], checks[check_id]["reason"]), check_id)
        self.assertEqual(["release_pin", "key_source", "paper_key", "connector_readback", "heartbeats"],
                         body["payload"]["warned"])
        self.assertEqual("full", self.record()["mode"])
        self.template.assert_called_once_with(version.repository_root())

    def test_a_pinned_checkout_with_heartbeats_and_a_recent_readback_passes_clean(self) -> None:
        state.atomic_write_json(self.paths.desk_state_json, {"connector_readback_utc": "2026-10-20T15:00:00Z"})
        for routine_id, slot, run in (("preopen", "07:30", "2026-11-03T12:31Z"), ("book-check", "10:05", "2026-11-03T15:06Z"),
                                      ("book-check", "12:05", "2026-11-03T17:06Z"), ("book-check", "14:05", "2026-11-03T19:06Z")):
            moment = datetime.datetime.strptime(run, "%Y-%m-%dT%H:%MZ").replace(tzinfo=datetime.timezone.utc)
            heartbeat.write_heartbeat(self.paths, routine_id, now=moment, exit_code=0, status="ok", started_utc=moment,
                                      finished_utc=moment, message_sha256=None, scheduled_et=slot, tool_version="0.1.0")
        with mock.patch.object(tool, "CHECKOUT_RUN", git_answers(HEAD)):
            code, body = self.run_json()
        self.assertEqual((0, "PASS"), (code, body["payload"]["verdict"]))
        checks = self.checks(body)
        self.assertEqual(("PASS", "release v%s at 971186c6, clean" % version.release_version()),
                         (checks["release_pin"]["status"], checks["release_pin"]["reason"]))
        self.assertEqual(("PASS", "connected apps and MCP servers confirmed 2026-10-20 (14 days ago)"),
                         (checks["connector_readback"]["status"], checks["connector_readback"]["reason"]))
        self.assertEqual(("PASS", "4 of 4 due slots ran today (profile lean); latest heartbeats: book-check 44 min ago, "
                                  "preopen 439 min ago"),
                         (checks["heartbeats"]["status"], checks["heartbeats"]["reason"]))
        self.assertEqual(["key_source", "paper_key"], body["payload"]["warned"])

    def test_a_dirty_checkout_fails_with_exit_20(self) -> None:
        with mock.patch.object(tool, "CHECKOUT_RUN", git_answers(HEAD, b" M scripts/routine.py\n")):
            code, body = self.run_json()
        self.assertEqual((20, "unavailable", "FAIL", ["release_pin"]),
                         (code, body["status"], body["payload"]["verdict"], body["payload"]["failed"]))
        self.assertEqual("the checkout has 1 uncommitted or untracked path(s)", self.checks(body)["release_pin"]["reason"])
        self.assertEqual(("FAIL", "full"), (self.record()["verdict"], self.record()["mode"]))

    def test_a_failing_template_check_fails_the_pin(self) -> None:
        self.template.return_value = {"ok": False, "errors": ["template/grok-bot.json: name must be 'Aegis Desk Lead'"]}
        code, body = self.run_json()
        self.assertEqual(20, code)
        self.assertEqual(("FAIL", "development checkout (no tag); template/grok-bot.json: name must be 'Aegis Desk Lead'"),
                         (self.checks(body)["release_pin"]["status"], self.checks(body)["release_pin"]["reason"]))

    def test_broker_names_and_the_machine_role_come_from_the_environment_seams(self) -> None:
        with mock.patch.object(tool, "ENVIRON", {"SCHWAB_APP_KEY": "x", "AEGIS_MACHINE_ROLE": "owner_private"}), \
                mock.patch.object(tool, "WORKSPACE_EXISTS", False):
            code, body = self.run_json()
        self.assertEqual((20, ["broker_env"], "owner_private"),
                         (code, body["payload"]["failed"], body["payload"]["machine_role"]))
        checks = self.checks(body)
        self.assertEqual(("FAIL", "broker-looking environment variable(s): SCHWAB_APP_KEY"),
                         (checks["broker_env"]["status"], checks["broker_env"]["reason"]))
        self.assertEqual("machine role: owner_private", checks["machine_role"]["reason"])
        self.assertEqual("owner_private", self.record()["machine_role"])

    def test_the_real_pin_check_reads_this_checkout(self) -> None:
        with mock.patch.object(tool, "CHECKOUT_RUN", None):
            code, body = self.run_json()
        expected = check_grok_template.checkout_state(version.repository_root(), version.release_version())["reason"]
        self.assertEqual(expected, self.checks(body)["release_pin"]["reason"])
        self.assertIn(code, (0, 20))


class PaperKeyModeTest(DeskCopy):
    server: FakeAlpaca
    thread: threading.Thread

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = FakeAlpaca()
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        super().setUp()
        with self.server.lock:
            del self.server.requests[:]
            del self.server.violations[:]
        self.server.script.clear()
        environ = {key: value for key, value in os.environ.items()
                   if key not in ("APCA_API_KEY_ID", "APCA_API_SECRET_KEY")}
        environ.update({"APCA_API_KEY_ID": KEY_ID, "APCA_API_SECRET_KEY": SECRET})
        patcher = mock.patch.dict(os.environ, environ, clear=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        client = mock.patch.object(tool, "PAPER_CLIENT", self.loopback)
        client.start()
        self.addCleanup(client.stop)

    def tearDown(self) -> None:
        self.server.script.clear()
        self.assertEqual([], self.server.violations, "a non-GET request reached the fake server")
        super().tearDown()

    def loopback(self, credential: Any, timeout_s: float, user_agent: str) -> Any:
        return alpaca_client.AlpacaClient("loopback:%d" % self.server.port, credential, timeout_s=timeout_s,
                                          user_agent=user_agent)

    def requests(self) -> List[Dict[str, Any]]:
        with self.server.lock:
            return list(self.server.requests)

    def test_one_clock_request_on_the_paper_host_verifies_the_paper_key(self) -> None:
        with open(os.path.join(REGULAR, "clock.json"), "rb") as handle:
            self.server.script[alpaca_client.PATH_CLOCK] = static(200, handle.read())
        code, body = self.run_json(offline=False)
        checks = self.checks(body)
        self.assertEqual(("PASS", "key pair from environment variables APCA_API_KEY_ID and APCA_API_SECRET_KEY"),
                         (checks["key_source"]["status"], checks["key_source"]["reason"]))
        self.assertEqual(("PASS", "paper key verified (paper clock answered: open)"),
                         (checks["paper_key"]["status"], checks["paper_key"]["reason"]))
        seen = self.requests()
        self.assertEqual([("GET", "/v2/clock")], [(item["method"], item["path"]) for item in seen])
        self.assertEqual("aegis-desk/%s desk_doctor (read-only market data)" % version.release_version(),
                         seen[0]["headers"]["user-agent"])
        self.assertEqual((KEY_ID, SECRET), (seen[0]["headers"]["apca-api-key-id"],
                                            seen[0]["headers"]["apca-api-secret-key"]))
        self.assertEqual(0, code)
        self.assertNotIn(SECRET, json.dumps(body))
        self.assertNotIn(KEY_ID, json.dumps(body))

    def test_a_rejected_key_fails_as_not_a_paper_key(self) -> None:
        with open(os.path.join(REGULAR, "errors", "unauthorized.txt"), "rb") as handle:
            self.server.script[alpaca_client.PATH_CLOCK] = static(401, handle.read())
        code, body = self.run_json(offline=False)
        self.assertEqual((20, ["paper_key"]), (code, body["payload"]["failed"]))
        self.assertEqual("not a paper key: remove it and add a paper key", self.checks(body)["paper_key"]["reason"])
        self.assertEqual(1, len(self.requests()))

    def test_a_live_alpaca_base_url_fails_check_7(self) -> None:
        with open(os.path.join(REGULAR, "clock.json"), "rb") as handle:
            self.server.script[alpaca_client.PATH_CLOCK] = static(200, handle.read())
        environ = {"HOME": "/home/owner", "APCA_API_BASE_URL": "https://api.alpaca.markets",
                   "APCA_API_KEY_ID": "x", "APCA_API_SECRET_KEY": "y"}
        with mock.patch.object(tool, "ENVIRON", environ):
            code, body = self.run_json(offline=False)
        self.assertEqual((20, ["broker_env"]), (code, body["payload"]["failed"]))
        self.assertEqual("APCA_API_BASE_URL points at the live trading host api.alpaca.markets",
                         self.checks(body)["broker_env"]["reason"])

    def test_a_live_host_fails_without_a_request(self) -> None:
        self.rewrite("desk.toml", 'host = "paper"', 'host = "live"')
        code, body = self.run_json("--quick", offline=False)
        self.assertEqual((20, ["desk_files"]), (code, body["payload"]["failed"]),
                         "the quick doctor (the preopen step) fails a live host too")
        self.assertIn('host must be "paper" (got "live")', self.checks(body)["desk_files"]["reason"])
        self.assertEqual([], self.requests())
        code, body = self.run_json(offline=False)
        self.assertEqual((20, ["desk_files"]), (code, body["payload"]["failed"]))
        self.assertEqual(("WARN", "not verified: desk.toml cannot be used (see desk_files)"),
                         (self.checks(body)["paper_key"]["status"], self.checks(body)["paper_key"]["reason"]))
        self.assertEqual([], self.requests())

    def test_no_key_pair_fails_the_key_check_and_sends_nothing(self) -> None:
        missing = os.path.join(self.base, "missing.key")
        self.rewrite("desk.toml", 'key_env = "APCA_API_KEY_ID"', 'key_env = "AEGIS_DOCTOR_UNSET_KEY_ID"\n'
                                                                'credentials_file = "%s"' % missing)
        self.rewrite("desk.toml", 'secret_env = "APCA_API_SECRET_KEY"', 'secret_env = "AEGIS_DOCTOR_UNSET_SECRET"')
        code, body = self.run_json("--quick", offline=False)
        self.assertEqual((20, ["key_source"]), (code, body["payload"]["failed"]))
        reason = self.checks(body)["key_source"]["reason"]
        for name in ("AEGIS_DOCTOR_UNSET_KEY_ID", "AEGIS_DOCTOR_UNSET_SECRET", missing):
            self.assertIn(name, reason)
        self.assertNotIn(SECRET, reason)
        self.assertEqual([], self.requests())


if __name__ == "__main__":
    unittest.main()
