"""Command-line contract: flags, validation, exit-code table, envelopes, schema, floor check."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import datetime
import io
import json
import os
import tempfile
import unittest
from decimal import Decimal

from aegis_desk import contract, state

UTC = datetime.timezone.utc


def parser(**kwargs) -> contract.ReadOnlyParser:
    return contract.ReadOnlyParser("demo_tool", "0.1.0", description="demo", **kwargs)


class ExitCodeTableTest(unittest.TestCase):
    def test_named_constants(self) -> None:
        self.assertEqual((0, 1, 2, 10, 20), (contract.EXIT_QUIET, contract.EXIT_ERROR, contract.EXIT_USAGE,
                                              contract.EXIT_FIRED, contract.EXIT_UNAVAILABLE))
        self.assertEqual({0, 1, 2, 10, 20}, set(contract.EXIT_CODES))
        for code, text in contract.EXIT_CODES.items():
            self.assertTrue(text.startswith(contract.STATUS_FOR_EXIT[code] + ":"), text)

    def test_status_mapping_round_trips(self) -> None:
        for code, status in contract.STATUS_FOR_EXIT.items():
            self.assertEqual(code, contract.exit_code_for(status))
            self.assertEqual(status, contract.status_for(code))
        self.assertEqual(contract.EXIT_QUIET, contract.exit_code_for(contract.STATUS_OK))
        self.assertEqual(("ok", "quiet", "error", "usage", "fired", "unavailable"), contract.STATUSES)
        with self.assertRaises(ValueError):
            contract.exit_code_for("hold")
        with self.assertRaises(ValueError):
            contract.status_for(3)


class EnvelopeTest(unittest.TestCase):
    def test_envelope_shape(self) -> None:
        moment = datetime.datetime(2026, 11, 2, 14, 31, 5, tzinfo=UTC)
        body = contract.envelope("exit_check", "0.1.0", {"verdict": "EXIT_SIGNAL", "credit": Decimal("3.42")},
                                 source="tradier sandbox", delay_min=15, generated_at=moment, status="fired")
        self.assertEqual({"schema_version": 1, "tool": "exit_check", "tool_version": "0.1.0", "mode": "read-only",
                          "status": "fired", "generated_at": "2026-11-02T14:31:05+00:00", "source": "tradier sandbox",
                          "delay_min": 15, "payload": {"verdict": "EXIT_SIGNAL", "credit": Decimal("3.42")}}, body)
        rendered = json.loads(contract.json_dumps(body))
        self.assertEqual("3.42", rendered["payload"]["credit"])
        self.assertEqual("read-only", rendered["mode"])

    def test_envelope_defaults_and_validation(self) -> None:
        body = contract.envelope("t", "1", None)
        self.assertEqual("ok", body["status"])
        self.assertIsNone(body["source"])
        self.assertIsNone(body["delay_min"])
        self.assertIsNone(body["payload"])
        stamp = datetime.datetime.fromisoformat(body["generated_at"])
        self.assertEqual(datetime.timedelta(0), stamp.utcoffset())
        self.assertEqual("2026-11-02T14:31:05+00:00", contract.envelope("t", "1", {}, generated_at="2026-11-02T14:31:05+00:00")["generated_at"])
        with self.assertRaises(ValueError):
            contract.envelope("t", "1", {}, status="hold")
        with self.assertRaises(ValueError):
            contract.envelope("t", "1", {}, delay_min=-1)
        with self.assertRaises(ValueError):
            contract.envelope("t", "1", {}, delay_min=True)
        with self.assertRaises(TypeError):
            contract.envelope("t", "1", {}, generated_at=datetime.datetime(2026, 11, 2))  # naive

    def test_error_envelope(self) -> None:
        body = contract.error_envelope("book", "0.1.0", "source_unavailable", "quote_cache older than 25 min",
                                       contract.STATUS_UNAVAILABLE, generated_at="2026-11-02T14:31:05+00:00")
        self.assertEqual({"schema_version": 1, "tool": "book", "tool_version": "0.1.0", "mode": "read-only",
                          "status": "unavailable", "generated_at": "2026-11-02T14:31:05+00:00",
                          "error": {"code": "source_unavailable", "message": "quote_cache older than 25 min"}}, body)
        self.assertNotIn("payload", body)
        for status in ("error", "usage", "unavailable"):
            contract.error_envelope("t", "1", "c", "m", status)
        for status in ("ok", "quiet", "fired", "bogus"):
            with self.assertRaises(ValueError):
                contract.error_envelope("t", "1", "c", "m", status)

    def test_schema_envelope(self) -> None:
        body = contract.schema_envelope("book", "0.1.0", {"legs": "array"}, network_access="GET /v1/markets/quotes",
                                        description="marks the book")
        self.assertEqual("read-only", body["mode"])
        self.assertEqual({"0", "1", "2", "10", "20"}, set(body["exit_codes"]))
        self.assertTrue(body["exit_codes"]["20"].startswith("unavailable:"))
        self.assertEqual({"legs": "array"}, body["envelope"]["payload"])
        self.assertEqual("book", body["error"]["tool"])
        self.assertEqual("GET /v1/markets/quotes", body["network_access"])
        self.assertEqual("marks the book", body["description"])
        json.loads(contract.json_dumps(body))

    def test_json_helpers_are_the_state_layer_convention(self) -> None:
        self.assertIs(state.json_dumps, contract.json_dumps)
        self.assertIs(state.utc_now, contract.utc_now)
        self.assertIs(state.format_utc, contract.format_utc)


class FloorCheckTest(unittest.TestCase):
    def test_too_old_prints_envelope_and_exits_1(self) -> None:
        stream = io.StringIO()
        with self.assertRaises(SystemExit) as ctx:
            contract.python_floor_check(version=(3, 8, 17), stream=stream, tool="opening_bell", tool_version="0.1.0")
        self.assertEqual(contract.EXIT_ERROR, ctx.exception.code)
        lines = stream.getvalue().splitlines()
        self.assertEqual(1, len(lines))
        body = json.loads(lines[0])
        self.assertEqual("python_too_old", body["error"]["code"])
        self.assertEqual("error", body["status"])
        self.assertEqual("read-only", body["mode"])
        self.assertEqual("opening_bell", body["tool"])
        self.assertIn("3.8.17", body["error"]["message"])
        self.assertIn("3.9", body["error"]["message"])

    def test_at_or_above_floor_is_silent(self) -> None:
        stream = io.StringIO()
        for version in ((3, 9, 0), (3, 9, 23), (3, 13, 1), (4, 0, 0)):
            contract.python_floor_check(version=version, stream=stream)
        contract.python_floor_check(stream=stream)  # the running interpreter is at least 3.9
        self.assertEqual("", stream.getvalue())
        with self.assertRaises(SystemExit):
            contract.python_floor_check(minimum=(3, 12), version=(3, 11, 9), stream=stream)
        self.assertEqual((3, 9), contract.PYTHON_FLOOR)


class ParserTest(unittest.TestCase):
    def test_defaults(self) -> None:
        args = parser().parse_args([])
        self.assertFalse(args.json)
        self.assertFalse(args.schema)
        self.assertFalse(args.offline)
        self.assertEqual(10.0, args.timeout)
        self.assertEqual(os.path.abspath(os.getcwd()), args.desk_root)
        self.assertFalse(args.desk_root_explicit)

    def test_explicit_desk_root_is_absolute_and_must_exist(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            args = parser().parse_args(["--desk-root", directory, "--offline", "--timeout", "2.5"])
            self.assertEqual(os.path.abspath(directory), args.desk_root)
            self.assertTrue(args.desk_root_explicit)
            self.assertTrue(args.offline)
            self.assertEqual(2.5, args.timeout)
            missing = os.path.join(directory, "nope")
            with self.assertRaises(SystemExit) as ctx:
                with contextlib.redirect_stderr(io.StringIO()):
                    parser().parse_args(["--desk-root", missing])
            self.assertEqual(contract.EXIT_USAGE, ctx.exception.code)
            args = parser(desk_root_must_exist=False).parse_args(["--desk-root", missing])
            self.assertEqual(os.path.abspath(missing), args.desk_root)

    def test_offline_requires_explicit_desk_root(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                parser().parse_args(["--offline"])
        self.assertEqual(contract.EXIT_USAGE, ctx.exception.code)
        self.assertIn("--offline runs must pass an explicit --desk-root", stderr.getvalue())
        self.assertEqual("", stdout.getvalue())

    def test_timeout_validation(self) -> None:
        for value in ("0", "-1", "nan", "inf", "-inf", "abc"):
            with self.assertRaises(SystemExit, msg=value) as ctx:
                with contextlib.redirect_stderr(io.StringIO()):
                    parser().parse_args(["--timeout", value])
            self.assertEqual(contract.EXIT_USAGE, ctx.exception.code)
        self.assertEqual(0.001, parser().parse_args(["--timeout", "0.001"]).timeout)

    def test_json_mode_errors_are_envelopes_on_stdout(self) -> None:
        for argv in (["--json", "--timeout", "0"], ["--json", "--bogus"], ["--schema", "--offline"], ["--json", "--timeout"]):
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.assertRaises(SystemExit, msg=argv) as ctx:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    parser().parse_args(argv)
            self.assertEqual(contract.EXIT_USAGE, ctx.exception.code, argv)
            body = json.loads(stdout.getvalue())
            self.assertEqual("invalid_arguments", body["error"]["code"], argv)
            self.assertEqual("usage", body["status"])
            self.assertEqual("read-only", body["mode"])
            self.assertEqual("demo_tool", body["tool"])
            self.assertEqual("0.1.0", body["tool_version"])
            self.assertTrue(body["error"]["message"])
            self.assertEqual("", stderr.getvalue(), argv)

    def test_prose_mode_errors_go_to_stderr(self) -> None:
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                parser().parse_args(["--bogus"])
        self.assertEqual(contract.EXIT_USAGE, ctx.exception.code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("usage: demo_tool", stderr.getvalue())

    def test_abbreviated_flags_are_rejected(self) -> None:
        # allow_abbrev is off: --js is not a quiet alias of --json. A Bot that abbreviates gets
        # the exact usage error (exit 2) instead of prose where it expected an envelope.
        for argv in (["--js"], ["--sch"], ["--time", "5"], ["--desk", "."]):
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.assertRaises(SystemExit, msg=argv) as ctx:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    parser().parse_args(argv)
            self.assertEqual(contract.EXIT_USAGE, ctx.exception.code, argv)
            self.assertIn("unrecognized arguments", stderr.getvalue(), argv)
            self.assertEqual("", stdout.getvalue(), argv)
        self.assertTrue(parser().parse_args(["--json"]).json)
        self.assertTrue(parser().parse_args(["--schema"]).schema)

    def test_tool_flags_coexist_with_the_shared_ones(self) -> None:
        p = parser()
        p.add_argument("--symbol", required=True)
        args = p.parse_args(["--symbol", "SPY", "--json"])
        self.assertEqual("SPY", args.symbol)
        self.assertTrue(args.json)
        self.assertTrue(p.machine_readable)

    def test_schema_method_and_help_text(self) -> None:
        p = parser()
        body = p.schema({"quote": "object"}, network_access="GET /v1/markets/quotes")
        self.assertEqual("demo_tool", body["tool"])
        self.assertEqual("demo", body["description"])
        self.assertEqual({"quote": "object"}, body["envelope"]["payload"])
        help_text = p.format_help()
        for flag in ("--json", "--schema", "--timeout", "--desk-root", "--offline"):
            self.assertIn(flag, help_text)
        self.assertIn("must pass it explicitly", help_text)


class NowFlagTest(unittest.TestCase):
    """``--now ISO-8601-UTC``: an aware UTC datetime in ``args.now``, ``None`` when absent."""

    def test_absent_is_none(self) -> None:
        self.assertIsNone(parser().parse_args([]).now)
        self.assertIsNone(parser().parse_args(["--json"]).now)

    def test_z_and_plus_zero_offset_are_the_same_instant(self) -> None:
        expected = datetime.datetime(2026, 11, 3, 19, 50, 0, tzinfo=UTC)
        for text in ("2026-11-03T19:50:00Z", "2026-11-03T19:50:00+00:00"):
            moment = parser().parse_args(["--now", text]).now
            self.assertEqual(expected, moment, text)
            self.assertEqual(datetime.timedelta(0), moment.utcoffset(), text)
            self.assertIs(UTC, moment.tzinfo, text)
        with tempfile.TemporaryDirectory() as directory:
            args = parser().parse_args(["--offline", "--desk-root", directory, "--now", "2026-11-03T19:50:00Z", "--json"])
            self.assertEqual(expected, args.now)
            self.assertTrue(args.offline and args.json)

    def test_fractional_seconds(self) -> None:
        self.assertEqual(500000, parser().parse_args(["--now", "2026-11-03T19:50:00.5Z"]).now.microsecond)
        self.assertEqual(123456, parser().parse_args(["--now", "2026-11-03T19:50:00.123456+00:00"]).now.microsecond)
        self.assertEqual(120000, parser().parse_args(["--now", "2026-11-03T19:50:00.12Z"]).now.microsecond)

    def test_anything_else_is_a_usage_error(self) -> None:
        bad = (
            "2026-11-03T19:50:00",        # naive
            "2026-11-03T19:50:00+01:00",  # another zone
            "2026-11-03T19:50:00-00:00",  # not the accepted spelling
            "2026-11-03T19:50:00z",       # lowercase
            "2026-11-03 19:50:00Z",       # space separator
            "2026-11-03T19:50Z",          # seconds required
            "2026-11-03",                 # a date
            "2026-13-03T19:50:00Z",       # month 13
            "2026-11-03T24:00:00Z",       # hour 24
            "2026-11-03T19:50:60Z",       # second 60
            "2026-11-03T19:50:00.1234567Z",
            "1793735400",                 # epoch seconds
            "now",
            "",
        )
        for text in bad:
            stdout, stderr = io.StringIO(), io.StringIO()
            with self.assertRaises(SystemExit, msg=text) as ctx:
                with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                    parser().parse_args(["--json", "--now", text])
            self.assertEqual(contract.EXIT_USAGE, ctx.exception.code, text)
            body = json.loads(stdout.getvalue())
            self.assertEqual("invalid_arguments", body["error"]["code"], text)
            self.assertEqual("usage", body["status"], text)
            self.assertIn("--now", body["error"]["message"], text)
            self.assertEqual("", stderr.getvalue(), text)
        stdout, stderr = io.StringIO(), io.StringIO()
        with self.assertRaises(SystemExit) as ctx:
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                parser().parse_args(["--now", "2026-11-03T19:50:00"])
        self.assertEqual(contract.EXIT_USAGE, ctx.exception.code)
        self.assertEqual("", stdout.getvalue())
        self.assertIn("--now", stderr.getvalue())

    def test_parse_utc_instant_helper(self) -> None:
        moment = contract.parse_utc_instant("2026-11-03T19:50:00Z")
        self.assertEqual(datetime.datetime(2026, 11, 3, 19, 50, tzinfo=UTC), moment)
        self.assertEqual(moment, contract.parse_utc_instant(" 2026-11-03T19:50:00+00:00 "))
        for text in ("2026-11-03T19:50:00", "2026-11-03T19:50:00+01:00", "2026-02-30T00:00:00Z", 1793735400, None):
            with self.assertRaises(ValueError, msg=repr(text)):
                contract.parse_utc_instant(text, "--at")  # type: ignore[arg-type]
        with self.assertRaises(ValueError) as ctx:
            contract.parse_utc_instant("2026-11-03", "--at")
        self.assertIn("--at", str(ctx.exception))
        self.assertIn("2026-11-03T19:50:00Z", str(ctx.exception))

    def test_now_is_in_the_help_and_the_flag_list(self) -> None:
        help_text = parser().format_help()
        self.assertIn("--now", help_text)
        self.assertIn("ISO-8601-UTC", help_text)
        self.assertIn("testing and replay", help_text)
        self.assertIn("parse_utc_instant", contract.__all__)


if __name__ == "__main__":
    unittest.main()
