"""``scripts/heartbeat.py`` run in-process (``main(argv)``) against a copied fixture desk root.

The fixture desk deliberately holds no runtime directories, so the write path proves the tool
creates ``state/heartbeat/`` through ``desk_paths.ensure_runtime_dirs`` on its copy. Every run
passes ``--now`` so the recorded instants, the lateness and the ages are exact.
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

from aegis_desk import contract, desk_paths
from aegis_desk import heartbeat as library

import heartbeat as tool  # the tool module scripts/heartbeat.py, not the library

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
NOW = "2026-11-03T19:50:00Z"  # 14:50:00 EST
STARTED = "2026-11-03T15:12:30Z"  # 10:12:30 EST


def run_tool(argv: Sequence[str]) -> Tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = tool.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def run_json(argv: Sequence[str]) -> Tuple[int, Dict[str, Any]]:
    code, out, _ = run_tool(argv)
    return code, json.loads(out)


def usage_exit(argv: Sequence[str]) -> Tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            tool.main(list(argv))
        except SystemExit as exc:
            return int(exc.code), out.getvalue(), err.getvalue()
    raise AssertionError("expected a usage exit for %r" % (list(argv),))


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-heartbeat-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, *extra: str, now: str = NOW, json_mode: bool = True) -> List[str]:
        argv = list(extra) + ["--offline", "--desk-root", self.root, "--now", now]
        if json_mode:
            argv.append("--json")
        return argv

    def history(self) -> List[Dict[str, Any]]:
        path = library.history_path(self.paths)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]


class SchemaAndUsageTest(DeskCopy):
    def test_schema(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual("heartbeat", body["tool"])
        self.assertEqual("read-only", body["mode"])
        self.assertEqual("none", body["network_access"])
        self.assertIn("heartbeat", body["envelope"]["payload"])
        self.assertIn("heartbeats", body["envelope"]["payload"])
        self.assertEqual({"0", "1", "2", "10", "20"}, set(body["exit_codes"]))

    def test_usage_errors_exit_2_with_an_envelope_in_json_mode(self) -> None:
        cases = [
            self.argv(),                                                   # neither --routine nor --list
            self.argv("--routine", "book-check", "--list"),                # both
            self.argv("--routine", "Book-check"),                          # bad id
            self.argv("--routine", "book_check"),
            self.argv("--routine", "book-check", "--scheduled-et", "10:05:00"),
            self.argv("--routine", "book-check", "--scheduled-et", "24:00"),
            self.argv("--routine", "book-check", "--exit-code", "256"),
            self.argv("--routine", "book-check", "--exit-code", "-1"),
            self.argv("--routine", "book-check", "--exit-code", "zero"),
            self.argv("--routine", "book-check", "--status", "quiet"),
            self.argv("--routine", "book-check", "--status", "hold"),
            self.argv("--routine", "book-check", "--exit-code", "20", "--status", "ok"),
            self.argv("--routine", "book-check", "--exit-code", "0", "--status", "fired"),
            self.argv("--routine", "book-check", "--started-utc", "2026-11-03T15:12:30"),
            self.argv("--routine", "book-check", "--started-utc", "2026-11-03T19:50:01Z"),  # after --now
            self.argv("--routine", "book-check", "--message-file", os.path.join(self.root, "missing.txt")),
            self.argv("--list", "--exit-code", "0"),
            self.argv("--list", "--scheduled-et", "10:05"),
            self.argv("--list", "--message-file", os.path.join(self.root, "desk.toml")),
            ["--routine", "book-check", "--json", "--offline"],                 # --offline without --desk-root
            self.argv("--routine", "book-check", now="2026-11-03T19:50:00+01:00"),
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                code, out, err = usage_exit(argv)
                self.assertEqual(2, code)
                body = json.loads(out)
                self.assertEqual("usage", body["status"])
                self.assertEqual("invalid_arguments", body["error"]["code"])
                self.assertEqual("heartbeat", body["tool"])
                self.assertEqual("", err)
        self.assertEqual([], self.history())
        self.assertFalse(os.path.exists(self.paths.state_dir))

    def test_usage_errors_in_prose_mode_go_to_stderr(self) -> None:
        code, out, err = usage_exit(self.argv("--routine", "9lives", json_mode=False))
        self.assertEqual(2, code)
        self.assertEqual("", out)
        self.assertIn("usage: heartbeat", err)
        self.assertIn("--routine", err)


class WriteTest(DeskCopy):
    def test_journey_write(self) -> None:
        argv = self.argv("--routine", "book-check", "--exit-code", "0", "--status", "ok", "--scheduled-et", "10:05")
        code, body = run_json(argv)
        self.assertEqual(0, code)
        self.assertEqual("heartbeat", body["tool"])
        self.assertEqual("ok", body["status"])
        self.assertEqual("read-only", body["mode"])
        self.assertEqual("2026-11-03T19:50:00+00:00", body["generated_at"])
        self.assertIsNone(body["source"])
        self.assertIsNone(body["delay_min"])
        payload = body["payload"]
        record = payload["heartbeat"]
        self.assertEqual({
            "schema": 1, "routine_id": "book-check", "run_utc": "2026-11-03T19:50:00+00:00",
            "started_utc": "2026-11-03T19:50:00+00:00", "finished_utc": "2026-11-03T19:50:00+00:00",
            "exit_code": 0, "status": "ok", "message_sha256": None, "scheduled_et": "10:05",
            "lateness_min": 285, "lateness_reason": "measured", "tool_version": body["tool_version"],
        }, record)
        self.assertEqual("state/heartbeat/book-check.json", payload["path"])
        self.assertEqual("state/heartbeat/history.jsonl", payload["history"])
        stored, status = library.read_heartbeat(self.paths, "book-check")
        self.assertEqual(("ok", record), (status, stored))
        self.assertEqual([record], self.history())
        # The runtime directories were created on the copy; the fixture tree itself holds none.
        for rel in desk_paths.RUNTIME_DIRS:
            self.assertTrue(os.path.isdir(os.path.join(self.root, *rel.split("/"))), rel)
        self.assertFalse(os.path.isdir(os.path.join(FIXTURE_DESK, "state")))

    def test_prose_line(self) -> None:
        code, out, err = run_tool(self.argv("--routine", "book-check", "--exit-code", "0", "--status", "ok",
                                            "--scheduled-et", "10:05", json_mode=False))
        self.assertEqual(0, code)
        self.assertEqual("heartbeat book-check written at 19:50:00 UTC (lateness 285 min)\n", out)
        self.assertEqual("", err)
        code, out, _ = run_tool(self.argv("--routine", "eod", json_mode=False))
        self.assertEqual(0, code)
        self.assertEqual("heartbeat eod written at 19:50:00 UTC (lateness n/a: no schedule)\n", out)
        code, out, _ = run_tool(self.argv("--routine", "preopen", "--scheduled-et", "15:30", json_mode=False))
        self.assertEqual(0, code)
        self.assertEqual("heartbeat preopen written at 19:50:00 UTC (lateness 0 min, before schedule)\n", out)

    def test_started_utc_sets_the_lateness_and_finished_is_now(self) -> None:
        code, body = run_json(self.argv("--routine", "book-check", "--scheduled-et", "10:05", "--started-utc", STARTED))
        self.assertEqual(0, code)
        record = body["payload"]["heartbeat"]
        self.assertEqual("2026-11-03T15:12:30+00:00", record["started_utc"])
        self.assertEqual("2026-11-03T19:50:00+00:00", record["finished_utc"])
        self.assertEqual(7, record["lateness_min"])
        self.assertEqual("measured", record["lateness_reason"])

    def test_status_and_exit_code_derive_from_each_other(self) -> None:
        code, body = run_json(self.argv("--routine", "a-fired", "--exit-code", "10"))
        self.assertEqual(0, code)
        self.assertEqual((10, "fired"), (body["payload"]["heartbeat"]["exit_code"], body["payload"]["heartbeat"]["status"]))
        code, body = run_json(self.argv("--routine", "b-unavailable", "--status", "unavailable"))
        self.assertEqual(0, code)
        self.assertEqual((20, "unavailable"), (body["payload"]["heartbeat"]["exit_code"], body["payload"]["heartbeat"]["status"]))
        code, body = run_json(self.argv("--routine", "c-default"))
        self.assertEqual(0, code)
        self.assertEqual((0, "ok"), (body["payload"]["heartbeat"]["exit_code"], body["payload"]["heartbeat"]["status"]))
        code, body = run_json(self.argv("--routine", "d-crash", "--exit-code", "137"))
        self.assertEqual(0, code)
        self.assertEqual((137, "error"), (body["payload"]["heartbeat"]["exit_code"], body["payload"]["heartbeat"]["status"]))
        code, body = run_json(self.argv("--routine", "e-error", "--exit-code", "137", "--status", "error"))
        self.assertEqual(0, code)
        self.assertEqual(5, len(self.history()))

    def test_message_file_is_hashed_never_copied(self) -> None:
        message = os.path.join(self.root, "message.txt")
        text = "EXIT SIGNAL AG-20261118-02 | WMT covered call | your rule TP50 fired\n"
        with open(message, "w", encoding="utf-8") as handle:
            handle.write(text)
        code, body = run_json(self.argv("--routine", "book-check", "--message-file", message))
        self.assertEqual(0, code)
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        self.assertEqual(digest, body["payload"]["heartbeat"]["message_sha256"])
        with open(library.heartbeat_path(self.paths, "book-check"), encoding="utf-8") as handle:
            stored = handle.read()
        self.assertIn(digest, stored)
        self.assertNotIn("EXIT SIGNAL", stored)

    def test_write_failure_is_exit_1_with_an_error_envelope(self) -> None:
        os.makedirs(self.paths.state_dir)
        with open(self.paths.heartbeat_dir, "w", encoding="utf-8") as handle:
            handle.write("a file where the heartbeat directory belongs\n")
        code, body = run_json(self.argv("--routine", "book-check"))
        self.assertEqual(1, code)
        self.assertEqual("error", body["status"])
        self.assertEqual("write_failed", body["error"]["code"])
        self.assertIn("heartbeat", body["error"]["message"])
        code, out, _ = run_tool(self.argv("--routine", "book-check", json_mode=False))
        self.assertEqual(1, code)
        self.assertTrue(out.startswith("error: "), out)

    def test_desk_root_must_be_a_directory(self) -> None:
        code, out, err = usage_exit(["--routine", "book-check", "--json", "--offline", "--desk-root",
                                     os.path.join(self.root, "nope"), "--now", NOW])
        self.assertEqual(contract.EXIT_USAGE, code)
        self.assertEqual("invalid_arguments", json.loads(out)["error"]["code"])


class ListTest(DeskCopy):
    def test_empty_list_is_exit_0(self) -> None:
        code, body = run_json(self.argv("--list"))
        self.assertEqual(0, code)
        self.assertEqual({"heartbeats": [], "count": 0, "directory": "state/heartbeat"}, body["payload"])
        self.assertEqual("ok", body["status"])
        # Listing never creates anything.
        self.assertFalse(os.path.exists(self.paths.state_dir))
        code, out, _ = run_tool(self.argv("--list", json_mode=False))
        self.assertEqual(0, code)
        self.assertEqual("no heartbeats recorded under state/heartbeat\n", out)

    def test_list_after_writes_sorted_with_ages(self) -> None:
        run_json(self.argv("--routine", "preopen", "--scheduled-et", "07:30", now="2026-11-03T12:41:00Z"))
        run_json(self.argv("--routine", "book-check", "--exit-code", "10", "--scheduled-et", "10:05", now="2026-11-03T15:12:30Z"))
        with open(library.heartbeat_path(self.paths, "eod"), "w", encoding="utf-8") as handle:
            handle.write("garbage")
        code, body = run_json(self.argv("--list", now="2026-11-03T19:50:00Z"))
        self.assertEqual(0, code)
        payload = body["payload"]
        self.assertEqual(3, payload["count"])
        self.assertEqual(["book-check", "eod", "preopen"], [item["routine_id"] for item in payload["heartbeats"]])
        book, eod, preopen = payload["heartbeats"]
        self.assertEqual("ok", book["read_status"])
        self.assertEqual(277, book["age_min"])  # 15:12:30Z -> 19:50:00Z = 4 h 37 min 30 s
        self.assertEqual(7, book["lateness_min"])
        self.assertEqual(10, book["exit_code"])
        self.assertEqual("fired", book["status"])
        self.assertEqual({"routine_id": "eod", "read_status": "corrupt", "age_min": None}, eod)
        self.assertEqual(429, preopen["age_min"])  # 12:41Z -> 19:50Z
        self.assertEqual(11, preopen["lateness_min"])  # 07:41 ET against 07:30
        # The corrupt file was quarantined by that read (the next read says missing), so plant it again
        # for the prose listing and check the quarantine copy is there.
        self.assertFalse(os.path.exists(library.heartbeat_path(self.paths, "eod")))
        self.assertTrue(any(".corrupt-" in name for name in os.listdir(self.paths.heartbeat_dir)))
        with open(library.heartbeat_path(self.paths, "eod"), "w", encoding="utf-8") as handle:
            handle.write("garbage")
        code, out, _ = run_tool(self.argv("--list", now="2026-11-03T19:50:00Z", json_mode=False))
        self.assertEqual(0, code)
        lines = out.splitlines()
        self.assertEqual(3, len(lines))
        self.assertEqual("heartbeat book-check: fired (exit 10), run 2026-11-03T15:12:30+00:00, age 277 min, lateness 7 min", lines[0])
        self.assertEqual("heartbeat eod: corrupt record (quarantined)", lines[1])
        self.assertEqual("heartbeat preopen: ok (exit 0), run 2026-11-03T12:41:00+00:00, age 429 min, lateness 11 min", lines[2])


if __name__ == "__main__":
    unittest.main()
