"""Heartbeat library: one JSON record per routine run, an append-only history, lateness arithmetic.

Every test works in a temporary desk root built with ``desk_paths.for_root``; the instants are
fixed (no wall clock) so lateness, ages and the on-disk records are exact. 2026-11-01 ended
daylight time, so 2026-11-03 is EST (UTC-5) and 2026-10-30 is EDT (UTC-4).
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import hashlib
import json
import os
import re
import tempfile
import unittest

from aegis_desk import contract, desk_paths, heartbeat, state

UTC = datetime.timezone.utc
NOW = datetime.datetime(2026, 11, 3, 19, 50, 0, tzinfo=UTC)  # 14:50:00 EST
STARTED = datetime.datetime(2026, 11, 3, 15, 12, 30, tzinfo=UTC)  # 10:12:30 EST, 7.5 min after a 10:05 slot
FINISHED = STARTED + datetime.timedelta(seconds=90)
SHA = hashlib.sha256(b"EXIT SIGNAL AG-20261118-02 | WMT covered call").hexdigest()
TOKEN_RE = re.compile(r"(?i)(?:bearer|token)[^A-Za-z0-9]{0,8}[A-Za-z0-9]{20,}")


class HeartbeatRootTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-heartbeat-")
        self.root = os.path.realpath(self._tmp.name)
        self.paths = desk_paths.for_root(self.root)
        desk_paths.ensure_runtime_dirs(self.paths)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, routine_id: str = "book-check", **overrides):
        kwargs = dict(now=NOW, exit_code=0, status="ok", started_utc=STARTED, finished_utc=FINISHED,
                      message_sha256=SHA, scheduled_et="10:05", tool_version="0.1.0")
        kwargs.update(overrides)
        return heartbeat.write_heartbeat(self.paths, routine_id, **kwargs)

    def history(self):
        path = heartbeat.history_path(self.paths)
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]


class ConstantsTest(unittest.TestCase):
    def test_routine_id_pattern_is_the_brief(self) -> None:
        self.assertEqual(r"^[a-z][a-z0-9-]{1,40}$", heartbeat.ROUTINE_ID_RE.pattern)
        for good in ("book-check", "eod", "preopen", "final-call", "a1", "x" + "y" * 40):
            self.assertEqual(good, heartbeat.validate_routine_id(good), good)
        for bad in ("", "a", "Book-check", "book_check", "9lives", "-eod", "x" + "y" * 41, "../eod", "eod.json",
                    "book check", None, 7):
            with self.assertRaises(heartbeat.HeartbeatError, msg=repr(bad)):
                heartbeat.validate_routine_id(bad)  # type: ignore[arg-type]

    def test_status_vocabulary_is_the_contracts(self) -> None:
        self.assertEqual(contract.STATUSES, heartbeat.STATUSES)
        self.assertEqual(("ok", "error", "usage", "fired", "unavailable"), heartbeat.TOOL_STATUSES)
        self.assertEqual(1, heartbeat.SCHEMA_VERSION)
        self.assertEqual("history.jsonl", heartbeat.HISTORY_FILE)

    def test_default_status_mirrors_the_exit_code_table(self) -> None:
        self.assertEqual("ok", heartbeat.default_status(0))
        self.assertEqual("error", heartbeat.default_status(1))
        self.assertEqual("usage", heartbeat.default_status(2))
        self.assertEqual("fired", heartbeat.default_status(10))
        self.assertEqual("unavailable", heartbeat.default_status(20))
        # An exit code outside the table is a tool failure by the contract's own definition.
        for code in (3, 127, 137, 255):
            self.assertEqual("error", heartbeat.default_status(code), code)

    def test_status_matches_exit(self) -> None:
        self.assertTrue(heartbeat.status_matches_exit("ok", 0))
        self.assertTrue(heartbeat.status_matches_exit("quiet", 0))
        self.assertTrue(heartbeat.status_matches_exit("fired", 10))
        self.assertTrue(heartbeat.status_matches_exit("unavailable", 20))
        self.assertTrue(heartbeat.status_matches_exit("error", 137))
        self.assertFalse(heartbeat.status_matches_exit("ok", 1))
        self.assertFalse(heartbeat.status_matches_exit("ok", 20))
        self.assertFalse(heartbeat.status_matches_exit("fired", 137))
        self.assertFalse(heartbeat.status_matches_exit("hold", 0))

    def test_sha256_hex(self) -> None:
        self.assertEqual("e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", heartbeat.sha256_hex(b""))
        self.assertEqual(SHA, heartbeat.sha256_hex(b"EXIT SIGNAL AG-20261118-02 | WMT covered call"))
        with self.assertRaises(TypeError):
            heartbeat.sha256_hex("text")  # type: ignore[arg-type]


class LatenessTest(unittest.TestCase):
    def test_measured_in_whole_minutes_on_the_runs_et_date(self) -> None:
        self.assertEqual((7, "measured"), heartbeat.lateness("10:05", STARTED))  # 7 min 30 s -> 7
        exact = datetime.datetime(2026, 11, 3, 15, 5, 0, tzinfo=UTC)
        self.assertEqual((0, "measured"), heartbeat.lateness("10:05", exact))
        self.assertEqual((0, "measured"), heartbeat.lateness("10:05", exact + datetime.timedelta(seconds=59)))
        self.assertEqual((1, "measured"), heartbeat.lateness("10:05", exact + datetime.timedelta(seconds=60)))
        self.assertEqual((285, "measured"), heartbeat.lateness("10:05", NOW))  # the journey's value

    def test_before_schedule_is_zero_with_a_reason_never_negative(self) -> None:
        early = datetime.datetime(2026, 11, 3, 15, 0, 0, tzinfo=UTC)  # 10:00 ET
        self.assertEqual((0, "before_schedule"), heartbeat.lateness("10:05", early))
        self.assertEqual((0, "before_schedule"), heartbeat.lateness("23:59", early))

    def test_no_schedule(self) -> None:
        self.assertEqual((None, "no_schedule"), heartbeat.lateness(None, STARTED))

    def test_daylight_time_offset(self) -> None:
        # 2026-10-30 is EDT (UTC-4): 14:12Z is 10:12 ET, 7 min after a 10:05 slot; under EST it would read 67.
        edt = datetime.datetime(2026, 10, 30, 14, 12, 0, tzinfo=UTC)
        self.assertEqual((7, "measured"), heartbeat.lateness("10:05", edt))
        est = datetime.datetime(2026, 11, 3, 14, 12, 0, tzinfo=UTC)  # 09:12 EST: before the slot
        self.assertEqual((0, "before_schedule"), heartbeat.lateness("10:05", est))
        # Another zone's aware datetime is the same instant.
        plus_one = edt.astimezone(datetime.timezone(datetime.timedelta(hours=1)))
        self.assertEqual((7, "measured"), heartbeat.lateness("10:05", plus_one))

    def test_input_validation(self) -> None:
        for bad in ("24:00", "9:05", "10:5", "10:05:00", "10:60", "1005", "", " 10:05", "10h05", 1005, 10.5):
            with self.assertRaises(heartbeat.HeartbeatError, msg=repr(bad)):
                heartbeat.lateness(bad, STARTED)  # type: ignore[arg-type]
        with self.assertRaises(heartbeat.HeartbeatError):
            heartbeat.lateness("10:05", STARTED.replace(tzinfo=None))
        with self.assertRaises(heartbeat.HeartbeatError):
            heartbeat.lateness("10:05", "2026-11-03T15:12:30Z")  # type: ignore[arg-type]
        self.assertEqual("measured", heartbeat.LATENESS_MEASURED)
        self.assertEqual("before_schedule", heartbeat.LATENESS_BEFORE_SCHEDULE)
        self.assertEqual("no_schedule", heartbeat.LATENESS_NO_SCHEDULE)


class WriteTest(HeartbeatRootTest):
    def test_write_creates_the_record_and_appends_history(self) -> None:
        record = self.write()
        self.assertIsInstance(record, heartbeat.Heartbeat)
        expected = {
            "schema": 1,
            "routine_id": "book-check",
            "run_utc": "2026-11-03T19:50:00+00:00",
            "started_utc": "2026-11-03T15:12:30+00:00",
            "finished_utc": "2026-11-03T15:14:00+00:00",
            "exit_code": 0,
            "status": "ok",
            "message_sha256": SHA,
            "scheduled_et": "10:05",
            "lateness_min": 7,
            "lateness_reason": "measured",
            "tool_version": "0.1.0",
        }
        self.assertEqual(expected, record.as_dict())
        path = heartbeat.heartbeat_path(self.paths, "book-check")
        self.assertEqual(os.path.join(self.root, "state", "heartbeat", "book-check.json"), path)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(expected, json.loads(text))
        self.assertEqual(state.json_dumps(expected) + "\n", text)  # the desk JSON convention, sorted keys
        self.assertEqual(os.path.join(self.root, "state", "heartbeat", "history.jsonl"), heartbeat.history_path(self.paths))
        self.assertEqual([expected], self.history())
        self.assertEqual(7, record.lateness_min)
        self.assertEqual(NOW, record.run_utc)
        self.assertEqual(STARTED, record.started_utc)

    def test_second_write_replaces_the_file_and_extends_the_history(self) -> None:
        first = self.write()
        later = NOW + datetime.timedelta(hours=2)
        second = self.write(now=later, exit_code=10, status="fired", started_utc=later - datetime.timedelta(minutes=3),
                            finished_utc=later, scheduled_et="14:05", message_sha256=None)
        record, status = heartbeat.read_heartbeat(self.paths, "book-check")
        self.assertEqual("ok", status)
        self.assertEqual(second.as_dict(), record)
        self.assertNotEqual(first.as_dict(), record)
        self.assertEqual([first.as_dict(), second.as_dict()], self.history())
        self.assertEqual(10, record["exit_code"])
        self.assertEqual("fired", record["status"])
        self.assertIsNone(record["message_sha256"])
        # 16:47 ET started against a 14:05 slot: 162 minutes late.
        self.assertEqual(162, record["lateness_min"])

    def test_without_schedule_lateness_is_none_with_an_explicit_reason(self) -> None:
        record = self.write(scheduled_et=None)
        self.assertIsNone(record.lateness_min)
        self.assertEqual("no_schedule", record.lateness_reason)
        stored, _ = heartbeat.read_heartbeat(self.paths, "book-check")
        self.assertIsNone(stored["lateness_min"])
        self.assertIsNone(stored["scheduled_et"])
        self.assertEqual("no_schedule", stored["lateness_reason"])

    def test_before_schedule_is_floored_at_zero(self) -> None:
        early = datetime.datetime(2026, 11, 3, 14, 58, 0, tzinfo=UTC)  # 09:58 ET
        record = self.write(started_utc=early, finished_utc=early + datetime.timedelta(seconds=30))
        self.assertEqual(0, record.lateness_min)
        self.assertEqual("before_schedule", record.lateness_reason)

    def test_other_zones_are_normalised_to_utc(self) -> None:
        plus_two = datetime.timezone(datetime.timedelta(hours=2))
        record = self.write(now=NOW.astimezone(plus_two), started_utc=STARTED.astimezone(plus_two),
                            finished_utc=FINISHED.astimezone(plus_two))
        self.assertEqual("2026-11-03T19:50:00+00:00", record.as_dict()["run_utc"])
        self.assertEqual(7, record.lateness_min)

    def test_every_table_status_round_trips_with_its_exit_code(self) -> None:
        for code, status in ((0, "ok"), (0, "quiet"), (1, "error"), (2, "usage"), (10, "fired"), (20, "unavailable"),
                             (137, "error")):
            record = self.write("routine-%d-%s" % (code, status), exit_code=code, status=status)
            self.assertEqual((code, status), (record.exit_code, record.status))

    def test_rejects_inconsistent_or_invalid_inputs(self) -> None:
        cases = [
            dict(routine_id="Book-check"),
            dict(routine_id="book_check"),
            dict(status="hold"),
            dict(status="quiet", exit_code=1),
            dict(exit_code=20, status="ok"),
            dict(exit_code=0, status="error"),
            dict(exit_code=137, status="fired"),
            dict(exit_code=-1, status="error"),
            dict(exit_code=256, status="error"),
            dict(exit_code=True, status="error"),
            dict(exit_code="0"),
            dict(message_sha256=SHA[:-1]),
            dict(message_sha256=SHA.upper()),
            dict(message_sha256=SHA + "0"),
            dict(message_sha256=""),
            dict(message_sha256=12345),
            dict(scheduled_et="24:00"),
            dict(scheduled_et="10:05:00"),
            dict(scheduled_et=""),
            dict(now=NOW.replace(tzinfo=None)),
            dict(started_utc=STARTED.replace(tzinfo=None)),
            dict(finished_utc=FINISHED.replace(tzinfo=None)),
            dict(finished_utc=STARTED - datetime.timedelta(seconds=1)),
            dict(tool_version=""),
            dict(tool_version=None),
            dict(tool_version=1),
        ]
        for overrides in cases:
            routine_id = overrides.pop("routine_id", "book-check")
            with self.assertRaises(heartbeat.HeartbeatError, msg=repr(overrides)):
                self.write(routine_id, **overrides)
        self.assertFalse(os.path.exists(heartbeat.heartbeat_path(self.paths, "book-check")))
        self.assertFalse(os.path.exists(heartbeat.history_path(self.paths)))
        with self.assertRaises(heartbeat.HeartbeatError):
            heartbeat.write_heartbeat(self.root, "book-check", now=NOW, exit_code=0, status="ok",  # type: ignore[arg-type]
                                      started_utc=STARTED, finished_utc=FINISHED, message_sha256=None,
                                      scheduled_et=None, tool_version="0.1.0")

    def test_finished_equal_to_started_is_allowed(self) -> None:
        record = self.write(finished_utc=STARTED)
        self.assertEqual(record.started_utc, record.finished_utc)

    def test_heartbeat_error_is_a_value_error(self) -> None:
        self.assertTrue(issubclass(heartbeat.HeartbeatError, ValueError))

    def test_record_carries_only_the_message_hash_and_no_secret_shapes(self) -> None:
        self.write()
        with open(heartbeat.heartbeat_path(self.paths, "book-check"), encoding="utf-8") as handle:
            text = handle.read()
        self.assertNotIn("EXIT SIGNAL", text)
        self.assertNotIn("Bearer", text)
        self.assertIsNone(TOKEN_RE.search(text.replace(SHA, "")), text)

    def test_heartbeat_directory_is_created_when_missing(self) -> None:
        os.rmdir(self.paths.heartbeat_dir)
        self.write()
        self.assertTrue(os.path.isfile(heartbeat.heartbeat_path(self.paths, "book-check")))
        self.assertTrue(os.path.isfile(heartbeat.history_path(self.paths)))


class ReadTest(HeartbeatRootTest):
    def test_missing(self) -> None:
        self.assertEqual((None, "missing"), heartbeat.read_heartbeat(self.paths, "book-check"))
        os.rmdir(self.paths.heartbeat_dir)
        self.assertEqual((None, "missing"), heartbeat.read_heartbeat(self.paths, "book-check"))

    def test_ok_returns_the_stored_object(self) -> None:
        written = self.write()
        record, status = heartbeat.read_heartbeat(self.paths, "book-check")
        self.assertEqual("ok", status)
        self.assertEqual(written.as_dict(), record)

    def test_undecodable_file_is_corrupt_and_quarantined(self) -> None:
        path = heartbeat.heartbeat_path(self.paths, "book-check")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        self.assertEqual((None, "corrupt"), heartbeat.read_heartbeat(self.paths, "book-check"))
        self.assertFalse(os.path.exists(path))
        quarantined = [name for name in os.listdir(self.paths.heartbeat_dir) if name.startswith("book-check.json.corrupt-")]
        self.assertEqual(1, len(quarantined), os.listdir(self.paths.heartbeat_dir))
        self.assertEqual((None, "missing"), heartbeat.read_heartbeat(self.paths, "book-check"))

    def test_well_formed_json_that_is_not_this_routines_record_is_corrupt(self) -> None:
        for document in ([1, 2, 3], {"routine_id": "eod", "schema": 1}, {"routine_id": "book-check", "schema": 2},
                         {"routine_id": "book-check", "schema": 1, "run_utc": "yesterday", "exit_code": 0, "status": "ok"},
                         {"routine_id": "book-check", "schema": 1, "run_utc": "2026-11-03T19:50:00+00:00",
                          "exit_code": "0", "status": "ok"},
                         {"routine_id": "book-check", "schema": 1, "run_utc": "2026-11-03T19:50:00+00:00",
                          "exit_code": 0, "status": "hold"}):
            path = heartbeat.heartbeat_path(self.paths, "book-check")
            state.atomic_write_json(path, document)
            self.assertEqual((None, "corrupt"), heartbeat.read_heartbeat(self.paths, "book-check"), document)
            self.assertFalse(os.path.exists(path))
        self.assertEqual(6, len([n for n in os.listdir(self.paths.heartbeat_dir) if ".corrupt-" in n]))

    def test_bad_routine_id_never_reaches_the_filesystem(self) -> None:
        with self.assertRaises(heartbeat.HeartbeatError):
            heartbeat.read_heartbeat(self.paths, "../desk")


class AgeTest(unittest.TestCase):
    def test_age_is_floored_whole_minutes_from_run_utc(self) -> None:
        record = {"routine_id": "eod", "run_utc": "2026-11-03T19:50:00+00:00"}
        self.assertEqual(7, heartbeat.heartbeat_age_min(record, NOW + datetime.timedelta(minutes=7, seconds=30)))
        self.assertEqual(0, heartbeat.heartbeat_age_min(record, NOW))
        self.assertEqual(0, heartbeat.heartbeat_age_min(record, NOW + datetime.timedelta(seconds=59)))
        self.assertEqual(1440, heartbeat.heartbeat_age_min(record, NOW + datetime.timedelta(days=1)))
        # A record stamped after "now" reads negative: clock skew is shown, never hidden.
        self.assertEqual(-1, heartbeat.heartbeat_age_min(record, NOW - datetime.timedelta(seconds=30)))

    def test_age_accepts_a_z_suffix_and_rejects_garbage(self) -> None:
        self.assertEqual(5, heartbeat.heartbeat_age_min({"run_utc": "2026-11-03T19:50:00Z"}, NOW + datetime.timedelta(minutes=5)))
        for record in ({}, {"run_utc": None}, {"run_utc": "2026-11-03T19:50:00"}, {"run_utc": "soon"}, {"run_utc": 1793735400},
                       "not a record"):
            with self.assertRaises(heartbeat.HeartbeatError, msg=repr(record)):
                heartbeat.heartbeat_age_min(record, NOW)  # type: ignore[arg-type]
        with self.assertRaises(heartbeat.HeartbeatError):
            heartbeat.heartbeat_age_min({"run_utc": "2026-11-03T19:50:00+00:00"}, NOW.replace(tzinfo=None))


class ListTest(HeartbeatRootTest):
    def test_empty_and_missing_directory(self) -> None:
        self.assertEqual([], heartbeat.list_heartbeats(self.paths))
        os.rmdir(self.paths.heartbeat_dir)
        self.assertEqual([], heartbeat.list_heartbeats(self.paths))

    def test_sorted_by_routine_id_with_corrupt_entries_named(self) -> None:
        self.write("preopen", scheduled_et="07:30")
        self.write("book-check")
        self.write("eod", scheduled_et=None)
        with open(heartbeat.heartbeat_path(self.paths, "final-call"), "w", encoding="utf-8") as handle:
            handle.write("garbage")
        with open(os.path.join(self.paths.heartbeat_dir, "README.txt"), "w", encoding="utf-8") as handle:
            handle.write("not a heartbeat\n")
        with open(os.path.join(self.paths.heartbeat_dir, "Notes.json"), "w", encoding="utf-8") as handle:
            handle.write("{}")
        listed = heartbeat.list_heartbeats(self.paths)
        self.assertEqual(["book-check", "eod", "final-call", "preopen"], [item["routine_id"] for item in listed])
        self.assertEqual(["ok", "ok", "corrupt", "ok"], [item["read_status"] for item in listed])
        self.assertEqual({"routine_id": "final-call", "read_status": "corrupt"}, listed[2])
        book = listed[0]
        self.assertEqual(7, book["lateness_min"])
        self.assertEqual(SHA, book["message_sha256"])
        stored, _ = heartbeat.read_heartbeat(self.paths, "book-check")
        self.assertEqual(dict(stored, read_status="ok"), book)
        # history.jsonl is the ledger, never a heartbeat; the garbage file was quarantined by the read.
        self.assertTrue(os.path.isfile(heartbeat.history_path(self.paths)))
        self.assertFalse(os.path.exists(heartbeat.heartbeat_path(self.paths, "final-call")))
        self.assertEqual(3, len(self.history()))


if __name__ == "__main__":
    unittest.main()
