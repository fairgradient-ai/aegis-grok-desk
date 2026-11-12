"""``structuring.toml`` joins the acknowledged configuration (PR 3b decision S8, designer C's D3 items 1-5 and 8).

The three duplicated name tuples (``ack.HASH_NAMES``, ``book_check.HASH_NAMES``, ``exit_check.CONFIG_NAMES``) stay
equal and end with ``structuring_toml``; the labels print ``structuring.toml``; ``ack.py config`` writes its hash (null
while the file is missing) and names it in its line only when the file exists; ``book_check`` and ``exit_check`` list
an unacknowledged or edited structuring.toml under CONFIG CHANGED, while a missing file with no acknowledged hash is
no change (every landed golden on ``tests/fixtures/desk``, which holds no structuring.toml, stays as it was); and the
doctor's check 8 adds a structuring part only when the file exists. Every run is in-process on a copy of the fixture
desk root at fixed instants.
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

from aegis_desk import alert_lint, alerts, desk_paths, doctor, state, structuring_config

import ack as ack_tool
import book_check as book_check_tool
import exit_check as exit_check_tool

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
EXPECTED_STRUCTURING = os.path.join(ROOT, "tests", "fixtures", "init", "expected_structuring.toml")
STRUCTURING_SHA = "5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c"
REGULAR_NOW = "2026-11-03T19:50:00Z"
ACK_NOW = "2026-11-03T19:49:00Z"
DOCTOR_PASS = ("desk.toml loads (sha 0af433cf); positions.csv parses: 7 rows in 4 groups, none quarantined; "
               "rules 1.0.0, sha cc09859c; research/calendar.toml loads")


def call(main: Any, argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = main(list(argv))
    return code, buffer.getvalue()


def sha256_file(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class NamesTest(unittest.TestCase):
    def test_the_three_name_tuples_are_equal_and_end_with_structuring_toml(self) -> None:
        names = ("desk_toml", "positions_csv", "rulebook", "structuring_toml")
        self.assertEqual(names, ack_tool.HASH_NAMES)
        self.assertEqual(ack_tool.HASH_NAMES, book_check_tool.HASH_NAMES)
        self.assertEqual(ack_tool.HASH_NAMES, exit_check_tool.CONFIG_NAMES)

    def test_the_labels(self) -> None:
        self.assertEqual("structuring.toml", alerts.CONFIG_FILE_LABELS["structuring_toml"])
        self.assertEqual("structuring.toml", exit_check_tool.CONFIG_LABELS["structuring_toml"])
        self.assertEqual({"desk_toml": "desk.toml", "positions_csv": "positions.csv", "rulebook": "adopted rulebook",
                          "structuring_toml": "structuring.toml"}, exit_check_tool.CONFIG_LABELS)
        for name in exit_check_tool.CONFIG_NAMES:
            self.assertEqual(exit_check_tool.CONFIG_LABELS[name], alerts.CONFIG_FILE_LABELS[name], name)

    def test_the_schemas_name_the_four_files(self) -> None:
        for main in (ack_tool.main, book_check_tool.main, exit_check_tool.main):
            code, out = call(main, ["--schema"])
            self.assertEqual(0, code)
            self.assertIn("structuring_toml", out)


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-structuring-ack-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)
        self.assertFalse(os.path.exists(self.paths.structuring_toml), "the fixture desk holds no structuring.toml")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def screen(self, out: str) -> None:
        self.assertEqual([], [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(out)], out)

    def add_structuring(self, text: str = "") -> str:
        shutil.copy(EXPECTED_STRUCTURING, self.paths.structuring_toml)
        if text:
            with open(self.paths.structuring_toml, "a", encoding="utf-8") as handle:
                handle.write(text)
        return sha256_file(self.paths.structuring_toml)

    def ack_config(self, now: str = ACK_NOW, json_mode: bool = True) -> Tuple[int, str]:
        argv = ["config", "--owner-tapped", "--offline", "--desk-root", self.root, "--now", now]
        code, out = call(ack_tool.main, argv + (["--json"] if json_mode else []))
        self.screen(out)
        return code, out

    def acknowledged(self) -> Dict[str, Any]:
        document, status = state.read_json(self.paths.desk_state_json)
        self.assertEqual("ok", status)
        return document["acknowledged_hashes"]

    def book_check(self) -> Dict[str, Any]:
        code, out = call(book_check_tool.main, ["--offline", "--desk-root", self.root, "--now", REGULAR_NOW,
                                                "--fixture-set", "regular", "--json"])
        self.screen(out)
        body = json.loads(out)
        self.assertIn(code, (0, 10), body)
        return body["payload"]

    def exit_check(self) -> Dict[str, Any]:
        code, out = call(exit_check_tool.main, ["--offline", "--desk-root", self.root, "--now", REGULAR_NOW,
                                                "--fixture-set", "regular", "--json"])
        self.screen(out)
        body = json.loads(out)
        self.assertIn(code, (0, 10), body)
        return body["payload"]


class AckConfigTest(DeskCopy):
    def test_a_missing_file_is_acknowledged_as_null_and_the_line_is_unchanged(self) -> None:
        code, out = self.ack_config()
        self.assertEqual(0, code, out)
        hashes = json.loads(out)["payload"]["acknowledged_hashes"]
        self.assertEqual(list(ack_tool.HASH_NAMES), list(hashes))
        self.assertIsNone(hashes["structuring_toml"])
        self.assertIsNone(self.acknowledged()["structuring_toml"])
        code, text = self.ack_config(json_mode=False)
        self.assertEqual("configuration acknowledged at 2026-11-03 19:49 UTC: desk.toml sha %s, positions.csv sha %s, "
                         "adopted rulebook 1.0.0 sha cc09859c\n" % (sha256_file(self.paths.desk_toml)[:8],
                                                                     sha256_file(self.paths.positions_csv)[:8]), text)

    def test_an_existing_file_is_hashed_and_named_last(self) -> None:
        sha = self.add_structuring()
        self.assertEqual(STRUCTURING_SHA, sha)
        code, out = self.ack_config()
        self.assertEqual(0, code, out)
        payload = json.loads(out)["payload"]
        self.assertEqual(sha, payload["acknowledged_hashes"]["structuring_toml"])
        self.assertEqual(sha, payload["record"]["hashes"]["structuring_toml"])
        self.assertEqual(sha, self.acknowledged()["structuring_toml"])
        code, text = self.ack_config(json_mode=False)
        self.assertTrue(text.endswith(", adopted rulebook 1.0.0 sha cc09859c, structuring.toml sha 5155427c\n"), text)

    def test_current_hashes(self) -> None:
        hashes, version = ack_tool.current_hashes(self.paths)
        self.assertEqual(("1.0.0", None), (version, hashes["structuring_toml"]))
        self.add_structuring()
        hashes, _version = ack_tool.current_hashes(self.paths)
        self.assertEqual(STRUCTURING_SHA, hashes["structuring_toml"])
        self.assertEqual(hashes, {name: exit_check_tool.config_hashes(self.paths)[name] for name in hashes})


class ConfigChangedTest(DeskCopy):
    def test_no_file_and_no_acknowledged_key_is_no_change(self) -> None:
        # An acknowledgement written before PR 3b has no structuring_toml key: None == None is no change.
        self.assertEqual(0, self.ack_config()[0])
        document, _status = state.read_json(self.paths.desk_state_json)
        document["acknowledged_hashes"].pop("structuring_toml")
        state.atomic_write_json(self.paths.desk_state_json, document)
        self.assertEqual([], self.book_check()["config_changed"])
        self.assertEqual([], self.exit_check()["config_changed"])
        self.assertEqual([], exit_check_tool.config_changes(self.paths))

    def test_an_unacknowledged_file_posts_config_changed(self) -> None:
        self.assertEqual(0, self.ack_config()[0])
        sha = self.add_structuring()
        expected = [{"name": "structuring_toml", "old": None, "new": sha}]
        payload = self.book_check()
        self.assertEqual(expected, payload["config_changed"])
        self.assertEqual(("config_changed", "config_changed"), (payload["parts"][0]["kind"], payload["parts"][0]["reason"]))
        self.assertIn("CONFIG CHANGED | structuring.toml: not acknowledged -> sha 5155427c | ", payload["message"])
        self.assertTrue(payload["lint"]["ok"], payload["lint"])
        self.assertEqual(expected, self.exit_check()["config_changed"])
        self.assertEqual(expected, exit_check_tool.config_changes(self.paths))
        self.assertEqual("CONFIG CHANGED: structuring.toml: not acknowledged -> sha 5155427c | %s"
                         % exit_check_tool.ACKNOWLEDGE_HINT, exit_check_tool.config_changed_line(expected))

    def test_acknowledging_clears_it_and_an_edit_brings_it_back(self) -> None:
        sha = self.add_structuring()
        self.assertEqual(0, self.ack_config()[0])
        self.assertEqual([], self.book_check()["config_changed"])
        self.assertEqual([], self.exit_check()["config_changed"])
        edited = self.add_structuring("# a hand edit\n")
        self.assertNotEqual(sha, edited)
        expected = [{"name": "structuring_toml", "old": sha, "new": edited}]
        payload = self.book_check()
        self.assertEqual(expected, payload["config_changed"])
        self.assertIn("CONFIG CHANGED | structuring.toml: sha 5155427c -> sha %s | " % edited[:8], payload["message"])
        self.assertEqual(expected, self.exit_check()["config_changed"])

    def test_a_removed_file_after_its_acknowledgement_is_listed_as_missing(self) -> None:
        sha = self.add_structuring()
        self.assertEqual(0, self.ack_config()[0])
        os.remove(self.paths.structuring_toml)
        expected = [{"name": "structuring_toml", "old": sha, "new": None}]
        payload = self.book_check()
        self.assertEqual(expected, payload["config_changed"])
        self.assertIn("CONFIG CHANGED | structuring.toml: sha 5155427c -> missing | ", payload["message"])
        self.assertEqual(expected, exit_check_tool.config_changes(self.paths))

    def test_the_other_files_keep_their_order_before_it(self) -> None:
        self.add_structuring()
        names = [change["name"] for change in self.book_check()["config_changed"]]
        self.assertEqual(["desk_toml", "positions_csv", "rulebook", "structuring_toml"], names)
        self.assertEqual(names, [change["name"] for change in exit_check_tool.config_changes(self.paths)])


class DoctorTest(DeskCopy):
    def run_check(self) -> Any:
        item, _cfg, _cal = doctor.desk_files_check(self.paths, __import__("datetime").date(2026, 11, 3))
        return item

    def test_a_missing_file_adds_nothing(self) -> None:
        item = self.run_check()
        self.assertEqual(("PASS", DOCTOR_PASS), (item.status, item.reason))

    def test_a_valid_file_adds_a_pass_part(self) -> None:
        self.add_structuring()
        item = self.run_check()
        self.assertEqual(("PASS", DOCTOR_PASS + "; structuring.toml loads (sha 5155427c)"), (item.status, item.reason))

    def test_an_invalid_file_fails_with_the_reader_error(self) -> None:
        self.add_structuring()
        with open(self.paths.structuring_toml, encoding="utf-8") as handle:
            text = handle.read()
        with open(self.paths.structuring_toml, "w", encoding="utf-8") as handle:
            handle.write(text.replace("max_candidates_shown = 3", "max_candidates_shown = 11"))
        item = self.run_check()
        self.assertEqual("FAIL", item.status)
        self.assertTrue(item.reason.startswith(DOCTOR_PASS + "; "), item.reason)
        self.assertIn("%s: [general] max_candidates_shown must be between 1 and 10 (got 11)"
                      % self.paths.structuring_toml, item.reason)


if __name__ == "__main__":
    unittest.main()
