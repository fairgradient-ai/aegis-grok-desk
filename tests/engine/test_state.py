"""State layer: advisory locking, atomic writes, journal appends, corrupt-file quarantine, JSON.

Every test works in a temporary directory. The lock-contention tests hold the lock through a
second file handle in the same process: ``flock`` locks belong to the open file description,
so a second ``locked()`` on the same path contends exactly as a second process would, which
keeps the timing deterministic (no subprocess, no sleeps beyond the timeout under test).
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import datetime
import json
import os
import re
import stat
import tempfile
import threading
import time
import unittest
from decimal import Decimal

from aegis_desk import state

UTC = datetime.timezone.utc


class TemporaryDirectoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-state-")
        self.root = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def path(self, *parts: str) -> str:
        return os.path.join(self.root, *parts)

    def listing(self, *parts: str):
        return sorted(os.listdir(self.path(*parts)))


class JsonConventionTest(unittest.TestCase):
    def test_decimal_as_string_and_datetime_as_utc(self) -> None:
        moment = datetime.datetime(2026, 11, 2, 9, 31, 5, tzinfo=datetime.timezone(datetime.timedelta(hours=-5)))
        text = state.json_dumps({"money": Decimal("3.42"), "big": Decimal("250000.00"), "when": moment,
                                 "day": datetime.date(2026, 11, 2), "clock": datetime.time(16, 0),
                                 "greek": 0.5, "n": 3, "flag": True, "none": None, "tags": {"b", "a"}})
        parsed = json.loads(text)
        self.assertEqual("3.42", parsed["money"])
        self.assertEqual("250000.00", parsed["big"])
        self.assertEqual("2026-11-02T14:31:05+00:00", parsed["when"])
        self.assertEqual(moment, datetime.datetime.fromisoformat(parsed["when"]))
        self.assertEqual("2026-11-02", parsed["day"])
        self.assertEqual("16:00:00", parsed["clock"])
        self.assertEqual(0.5, parsed["greek"])
        self.assertEqual(["a", "b"], parsed["tags"])
        self.assertEqual(sorted(parsed), list(parsed))  # sorted keys
        self.assertIn("\n  ", text)  # two-space indent

    def test_microseconds_kept_and_fromisoformat_round_trip(self) -> None:
        moment = datetime.datetime(2026, 11, 2, 14, 31, 5, 123456, tzinfo=UTC)
        rendered = state.format_utc(moment)
        self.assertEqual("2026-11-02T14:31:05.123456+00:00", rendered)
        self.assertEqual(moment, datetime.datetime.fromisoformat(rendered))

    def test_refusals(self) -> None:
        with self.assertRaises(TypeError):
            state.json_dumps({"when": datetime.datetime(2026, 11, 2, 14, 31)})  # naive: zone unknown
        with self.assertRaises(ValueError):
            state.json_dumps({"x": float("nan")})
        with self.assertRaises(ValueError):
            state.json_dumps({"x": float("inf")})
        with self.assertRaises(ValueError):
            state.json_dumps({"x": Decimal("NaN")})
        with self.assertRaises(ValueError):
            state.json_dumps({"x": Decimal("Infinity")})
        with self.assertRaises(TypeError):
            state.json_dumps({"x": object()})
        with self.assertRaises(TypeError):
            state.format_utc("2026-11-02")  # type: ignore[arg-type]

    def test_dataclasses_serialise_as_objects(self) -> None:
        @dataclasses.dataclass(frozen=True)
        class Leg:
            occ: str
            strike: Decimal
            qty: int

        parsed = json.loads(state.json_dumps({"leg": Leg("AAPL  240119C00150000", Decimal("150.000"), -1)}))
        self.assertEqual({"occ": "AAPL  240119C00150000", "strike": "150.000", "qty": -1}, parsed["leg"])

    def test_jsonl_line_has_no_newline_and_is_compact(self) -> None:
        line = state.json_dumps_line({"text": "two\nlines", "d": Decimal("1.5"), "z": 1, "a": 2})
        self.assertNotIn("\n", line)
        self.assertEqual('{"a":2,"d":"1.5","text":"two\\nlines","z":1}', line)

    def test_utc_now_is_aware_utc(self) -> None:
        now = state.utc_now()
        self.assertEqual(datetime.timedelta(0), now.utcoffset())
        self.assertLess(abs((now - datetime.datetime.now(UTC)).total_seconds()), 5)


class LockTest(TemporaryDirectoryTest):
    def test_lock_acquire_release_and_reacquire(self) -> None:
        lock = self.path("state", ".lock")
        with state.locked(lock, timeout_s=1.0):
            self.assertTrue(os.path.exists(lock))
        with state.locked(lock, timeout_s=0):
            pass
        self.assertEqual([".lock"], self.listing("state"))

    def test_busy_lock_times_out(self) -> None:
        lock = self.path(".lock")
        with state.locked(lock, timeout_s=1.0):
            started = time.monotonic()
            with self.assertRaises(state.LockBusy) as ctx:
                with state.locked(lock, timeout_s=0.3, poll_s=0.02):
                    self.fail("the lock must not be acquired while it is held")
            elapsed = time.monotonic() - started
            self.assertGreaterEqual(elapsed, 0.3)
            self.assertLess(elapsed, 5.0)
            self.assertEqual(lock, ctx.exception.lock_path)
            self.assertEqual(0.3, ctx.exception.timeout_s)
            self.assertIn("busy", str(ctx.exception))
            self.assertIsInstance(ctx.exception, RuntimeError)
        with state.locked(lock, timeout_s=0):
            pass  # released on exit

    def test_zero_timeout_is_a_single_attempt(self) -> None:
        lock = self.path(".lock")
        with state.locked(lock):
            started = time.monotonic()
            with self.assertRaises(state.LockBusy):
                with state.locked(lock, timeout_s=0):
                    pass
            self.assertLess(time.monotonic() - started, 0.5)

    def test_lock_released_after_exception(self) -> None:
        lock = self.path(".lock")
        with self.assertRaises(KeyError):
            with state.locked(lock):
                raise KeyError("inside")
        with state.locked(lock, timeout_s=0):
            pass

    def test_waiter_acquires_once_holder_releases(self) -> None:
        lock = self.path(".lock")
        acquired = []

        def waiter() -> None:
            with state.locked(lock, timeout_s=5.0, poll_s=0.01):
                acquired.append(time.monotonic())

        with state.locked(lock):
            thread = threading.Thread(target=waiter)
            thread.start()
            time.sleep(0.15)
            self.assertEqual([], acquired)
            released = time.monotonic()
        thread.join(timeout=5.0)
        self.assertEqual(1, len(acquired))
        self.assertGreaterEqual(acquired[0], released - 0.01)

    def test_invalid_parameters(self) -> None:
        with self.assertRaises(ValueError):
            with state.locked(self.path(".lock"), timeout_s=-1):
                pass
        with self.assertRaises(ValueError):
            with state.locked(self.path(".lock"), poll_s=0):
                pass

    def test_lock_unsupported_without_fcntl(self) -> None:
        original = state.fcntl
        state.fcntl = None
        try:
            with self.assertRaises(state.LockUnsupported):
                with state.locked(self.path(".lock")):
                    pass
        finally:
            state.fcntl = original


class AtomicWriteTest(TemporaryDirectoryTest):
    def test_text_written_and_replaced_without_leftovers(self) -> None:
        target = self.path("state", "desk_state.json")
        state.atomic_write_text(target, "one\n")
        state.atomic_write_text(target, "two\n")
        with open(target, encoding="utf-8") as handle:
            self.assertEqual("two\n", handle.read())
        self.assertEqual(["desk_state.json"], self.listing("state"))

    def test_json_round_trip_with_decimals_and_datetimes(self) -> None:
        target = self.path("book.json")
        moment = datetime.datetime(2026, 11, 2, 14, 31, 5, tzinfo=UTC)
        state.atomic_write_json(target, {"pl": Decimal("-12.50"), "generated_at": moment, "greeks": {"delta": 0.42}})
        with open(target, encoding="utf-8") as handle:
            raw = handle.read()
        self.assertTrue(raw.endswith("}\n"))
        obj, status = state.read_json(target)
        self.assertEqual(state.STATUS_OK, status)
        self.assertEqual({"pl": "-12.50", "generated_at": "2026-11-02T14:31:05+00:00", "greeks": {"delta": 0.42}}, obj)
        self.assertEqual(Decimal("-12.50"), Decimal(obj["pl"]))

    def test_bytes_and_type_errors(self) -> None:
        target = self.path("blob.bin")
        state.atomic_write_bytes(target, b"\x00\x01")
        with open(target, "rb") as handle:
            self.assertEqual(b"\x00\x01", handle.read())
        with self.assertRaises(TypeError):
            state.atomic_write_text(target, b"bytes")  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            state.atomic_write_bytes(target, "text")  # type: ignore[arg-type]
        with open(target, "rb") as handle:
            self.assertEqual(b"\x00\x01", handle.read())  # a refused write leaves the file alone
        self.assertEqual(["blob.bin"], self.listing())

    def test_existing_mode_preserved_and_new_file_mode_masked(self) -> None:
        target = self.path("perm.json")
        state.atomic_write_text(target, "a")
        mask = os.umask(0o022)
        os.umask(mask)
        self.assertEqual(0o644 & ~mask, stat.S_IMODE(os.stat(target).st_mode))
        os.chmod(target, 0o600)
        state.atomic_write_text(target, "b")
        self.assertEqual(0o600, stat.S_IMODE(os.stat(target).st_mode))

    def test_failed_serialisation_leaves_target_and_directory_untouched(self) -> None:
        target = self.path("state.json")
        state.atomic_write_json(target, {"ok": 1})
        with self.assertRaises(TypeError):
            state.atomic_write_json(target, {"bad": object()})
        self.assertEqual(({"ok": 1}, state.STATUS_OK), state.read_json(target))
        self.assertEqual(["state.json"], self.listing())

    def test_write_into_missing_directory_creates_it(self) -> None:
        target = self.path("a", "b", "c.json")
        state.atomic_write_json(target, [1])
        self.assertEqual(([1], state.STATUS_OK), state.read_json(target))


class AppendJsonlTest(TemporaryDirectoryTest):
    def test_append_creates_and_extends(self) -> None:
        journal = self.path("journal", "signals.jsonl")
        state.append_jsonl(journal, {"signal_id": 1, "value": Decimal("0.5")})
        state.append_jsonl(journal, {"signal_id": 2, "posted_at": datetime.datetime(2026, 11, 2, tzinfo=UTC)})
        with open(journal, encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        self.assertEqual("", lines[-1])
        self.assertEqual(3, len(lines))
        self.assertEqual({"signal_id": 1, "value": "0.5"}, json.loads(lines[0]))
        self.assertEqual({"signal_id": 2, "posted_at": "2026-11-02T00:00:00+00:00"}, json.loads(lines[1]))
        records, status = state.read_jsonl(journal)
        self.assertEqual(state.STATUS_OK, status)
        self.assertEqual(2, len(records))

    def test_append_refuses_unserialisable_without_touching_file(self) -> None:
        journal = self.path("j.jsonl")
        state.append_jsonl(journal, {"a": 1})
        with self.assertRaises(TypeError):
            state.append_jsonl(journal, {"a": object()})
        self.assertEqual(([{"a": 1}], state.STATUS_OK), state.read_jsonl(journal))

    def test_read_jsonl_drops_incomplete_last_line_and_quarantines_bad_lines(self) -> None:
        journal = self.path("j.jsonl")
        state.append_jsonl(journal, {"a": 1})
        with open(journal, "ab") as handle:
            handle.write(b'{"a": 2')  # crash between write and newline
        self.assertEqual(([{"a": 1}], state.STATUS_OK), state.read_jsonl(journal))
        with open(journal, "ab") as handle:
            handle.write(b"\n")  # now a complete, malformed record
        records, status = state.read_jsonl(journal)
        self.assertEqual(([], state.STATUS_CORRUPT), (records, status))
        self.assertEqual(1, len([n for n in self.listing() if n.startswith("j.jsonl.corrupt-")]))
        self.assertEqual(([], state.STATUS_MISSING), state.read_jsonl(journal))


class ReadJsonTest(TemporaryDirectoryTest):
    def test_missing(self) -> None:
        self.assertEqual((None, state.STATUS_MISSING), state.read_json(self.path("none.json")))

    def assert_quarantined(self, target: str, payload: bytes) -> str:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        before = set(os.listdir(os.path.dirname(target)))
        with open(target, "wb") as handle:
            handle.write(payload)
        obj, status = state.read_json(target)
        self.assertIsNone(obj)
        self.assertEqual(state.STATUS_CORRUPT, status)
        self.assertFalse(os.path.exists(target))
        names = sorted(set(os.listdir(os.path.dirname(target))) - before)
        self.assertEqual(1, len(names), names)
        self.assertTrue(names[0].startswith(os.path.basename(target) + ".corrupt-"), names[0])
        self.assertRegex(names[0], r"\.corrupt-\d{8}T\d{6}\.\d{6}Z$")
        with open(os.path.join(os.path.dirname(target), names[0]), "rb") as handle:
            self.assertEqual(payload, handle.read())  # the evidence is kept byte for byte
        self.assertEqual((None, state.STATUS_MISSING), state.read_json(target))
        return names[0]

    def test_invalid_json_quarantined(self) -> None:
        self.assert_quarantined(self.path("s", "desk_state.json"), b'{"half": ')

    def test_invalid_utf8_quarantined(self) -> None:
        self.assert_quarantined(self.path("desk_state.json"), b"\xff\xfe\x00")

    def test_empty_file_quarantined(self) -> None:
        self.assert_quarantined(self.path("desk_state.json"), b"")

    def test_two_quarantines_keep_distinct_names(self) -> None:
        target = self.path("desk_state.json")
        first = self.assert_quarantined(target, b"x")
        second = self.assert_quarantined(target, b"y")
        self.assertNotEqual(first, second)

    def test_quarantine_path_helper(self) -> None:
        moment = datetime.datetime(2026, 11, 2, 14, 31, 5, 250000, tzinfo=UTC)
        self.assertEqual("/d/state.json.corrupt-20261102T143105.250000Z", state.quarantine_path("/d/state.json", moment))
        est = datetime.timezone(datetime.timedelta(hours=-5))
        self.assertEqual("20261102T143105.000000Z", state.quarantine_stamp(moment.astimezone(est).replace(microsecond=0)))
        self.assertTrue(re.fullmatch(r"\d{8}T\d{6}\.\d{6}Z", state.quarantine_stamp()))

    def test_unreadable_file_propagates_oserror(self) -> None:
        if os.geteuid() == 0:
            self.skipTest("root ignores file permission bits")
        target = self.path("locked.json")
        state.atomic_write_json(target, {"a": 1})
        os.chmod(target, 0)
        try:
            with self.assertRaises(PermissionError):
                state.read_json(target)
        finally:
            os.chmod(target, 0o600)

    def test_statuses_are_distinct_words(self) -> None:
        self.assertEqual({"ok", "missing", "corrupt"}, {state.STATUS_OK, state.STATUS_MISSING, state.STATUS_CORRUPT})


if __name__ == "__main__":
    unittest.main()
