"""Vendored TOML reader: fixtures, construct-by-construct checks and the tomllib cross-check.

Fixtures live under ``tests/fixtures/toml/``: ``good/`` holds desk-shaped documents that must
parse (and agree with ``tomllib`` wherever it exists), ``bad/`` holds one defect per file with
its expectation in the first comment lines (``# expect: <message fragment>``,
``# expect-line: <line | none>``, and ``# tomllib: accepts`` for the documented divergences,
``inf``/``nan``). The cross-check runs on Python 3.11 and later and is skipped, not failed,
elsewhere; the structural checks run everywhere.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import datetime
import os
import random
import re
import tempfile
import unittest
from decimal import Decimal
from typing import Any, Dict, List, Tuple

from aegis_desk import toml_compat
from aegis_desk._vendor import toml_reader
from aegis_desk._vendor.toml_reader import TomlError, load, loads

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "fixtures", "toml")
GOOD = os.path.join(FIXTURES, "good")
BAD = os.path.join(FIXTURES, "bad")
UTC = datetime.timezone.utc
HAVE_TOMLLIB = toml_compat.tomllib_module() is not None


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def expectations(text: str) -> Tuple[str, Any, bool]:
    expect = re.search(r"^# expect: (.+)$", text, re.M)
    line = re.search(r"^# expect-line: (\S+)$", text, re.M)
    if not expect or not line:
        raise AssertionError("bad fixture needs '# expect:' and '# expect-line:' header lines")
    expected_line = None if line.group(1) == "none" else int(line.group(1))
    return expect.group(1).rstrip(), expected_line, "# tomllib: accepts" in text


def walk(value: Any):
    yield value
    if isinstance(value, dict):
        for child in value.values():
            yield from walk(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk(child)


class GoodFixtureTest(unittest.TestCase):
    def fixture_names(self) -> List[str]:
        names = sorted(name for name in os.listdir(GOOD) if name.endswith(".toml"))
        self.assertGreaterEqual(len(names), 6)
        return names

    def test_every_good_fixture_parses_without_floats(self) -> None:
        for name in self.fixture_names():
            document = load(os.path.join(GOOD, name))
            self.assertIsInstance(document, dict, name)
            self.assertTrue(document, name)
            for value in walk(document):
                self.assertNotIsInstance(value, float, "%s: a float leaked out of the reader" % name)

    @unittest.skipUnless(HAVE_TOMLLIB, "tomllib (Python 3.11+) is the cross-check oracle")
    def test_every_good_fixture_agrees_with_tomllib(self) -> None:
        for name in self.fixture_names():
            text = read_bytes(os.path.join(GOOD, name)).decode("utf-8")
            toml_compat.cross_check(text)  # raises CrossCheckMismatch on any difference

    def test_desk_toml_types(self) -> None:
        desk = load(os.path.join(GOOD, "desk.toml"))
        self.assertEqual("America/New_York", desk["desk"]["timezone"])
        self.assertEqual(datetime.time(16, 30), desk["owner"]["broker_exercise_cutoff_et"])
        self.assertEqual(2, desk["owner"]["approval_level"])
        self.assertIsInstance(desk["owner"]["approval_level"], int)
        self.assertEqual(Decimal("250000.00"), desk["owner"]["declared_equity"])
        self.assertEqual("250000.00", str(desk["owner"]["declared_equity"]))
        self.assertEqual(Decimal("1.5"), desk["owner"]["attention_thresholds"]["pl_day_pct"])
        self.assertEqual(["occ-characteristics-and-risks-2024", "ai-role-disclosure"],
                         desk["owner"]["disclosures_acknowledged"])
        self.assertEqual([{"name": "Brokerage (margin)", "type": "margin"}, {"name": "Roth IRA", "type": "ira"}],
                         desk["owner"]["accounts"])
        self.assertEqual(15, desk["market_data"]["feed_delay_min"])
        self.assertEqual(Decimal("0.25"), desk["market_data"]["pin_band"])
        self.assertEqual(Decimal("0.0412"), desk["rates"]["risk_free_rate"])
        self.assertEqual(datetime.date(2026, 10, 30), desk["rates"]["as_of"])
        self.assertIs(False, desk["levels"]["order_shape_enabled"])

    def test_exit_rules_array_of_tables_with_subtables(self) -> None:
        rules = load(os.path.join(GOOD, "exit_rules-1.0.0.toml"))
        self.assertEqual("1.0.0", rules["version"])
        ids = [rule["id"] for rule in rules["rule"]]
        self.assertEqual(["TP50", "LOSSX", "DTE21"], ids)
        tp50, lossx, dte21 = rules["rule"]
        self.assertEqual({"pct": 50, "dte_min": 21, "dte_unit": "trading"}, tp50["thresholds"])
        self.assertEqual({"observations": 2, "min_gap_min": 10, "window_min": 90}, tp50["confirmation"])
        self.assertEqual(Decimal("3.0"), lossx["thresholds"]["multiple"])
        self.assertIs(True, lossx["owner_optional"])
        self.assertEqual(1440, dte21["rearm"]["cooldown_min"])
        self.assertNotIn("confirmation", dte21)

    def test_calendar_datetimes_are_aware_utc(self) -> None:
        calendar = load(os.path.join(GOOD, "calendar.toml"))
        wmt = calendar["symbols"]["WMT"]
        self.assertEqual(datetime.date(2026, 11, 18), wmt["earnings"]["date"])
        checked = wmt["earnings"]["checked_utc"]
        self.assertEqual(datetime.datetime(2026, 11, 1, 13, 5, tzinfo=UTC), checked)
        self.assertEqual(datetime.timedelta(0), checked.utcoffset())
        self.assertEqual(Decimal("0.235"), wmt["ex_dividend"]["amount"])
        spy = calendar["symbols"]["SPY"]["ex_dividend"]["checked_utc"]
        self.assertEqual(250000, spy.microsecond)
        self.assertEqual([datetime.date(2026, 11, 26), datetime.date(2026, 12, 25), datetime.date(2027, 1, 1),
                          datetime.date(2027, 1, 18)], calendar["nyse"]["holidays"])
        self.assertEqual(datetime.time(13, 0), calendar["nyse"]["early_closes"][0]["close"])
        self.assertEqual(Decimal("6712.84"), calendar["settlement_values"]["SPX"][0]["value"])

    def test_manifest_shape(self) -> None:
        manifest = load(os.path.join(GOOD, "manifest.toml"))
        self.assertEqual(1, manifest["schema_version"])
        self.assertEqual({"runs_per_week": 61, "additive": True}, manifest["profiles"]["active"])
        self.assertEqual(["preopen", "book-check"], [r["id"] for r in manifest["routine"]])
        self.assertEqual({"routine": "preopen", "min_gap_min": 20}, manifest["routine"][1]["depends_on"])

    def test_kitchen_sink_values(self) -> None:
        doc = load(os.path.join(GOOD, "kitchen_sink.toml"))
        self.assertEqual("Desk \u00e9 \U0001F4C8 tab\t quote\" backslash\\ bell\b ff\f cr\r nl\n", doc["title"])
        self.assertEqual('C:\\path\\no "escape" here', doc["literal"])
        self.assertEqual("", doc["empty_basic"])
        self.assertEqual("", doc["empty_literal"])
        self.assertEqual("Roses are red\n  Violets are blue", doc["multi_basic"])
        self.assertEqual("The quick brown fox jumps.", doc["multi_backslash"])
        self.assertEqual('has ""two"" quotes and ends with two""', doc["multi_quotes"])
        self.assertEqual("raw \\n stays, 'single' quotes too\n", doc["multi_literal"])
        self.assertEqual("ends with two''", doc["multi_literal_quotes"])
        self.assertEqual(1000000, doc["int_dec"])
        self.assertEqual(-17, doc["int_neg"])
        self.assertEqual(42, doc["int_plus"])
        self.assertEqual(0, doc["int_zero"])
        self.assertEqual(0xDEADBEEF, doc["int_hex"])
        self.assertEqual(0o755, doc["int_oct"])
        self.assertEqual(0b10101010, doc["int_bin"])
        for key in ("int_dec", "int_neg", "int_plus", "int_zero", "int_hex", "int_oct", "int_bin"):
            self.assertIs(int, type(doc[key]), key)
        self.assertEqual(Decimal("3.42"), doc["flt_plain"])
        self.assertEqual(Decimal("-0.5"), doc["flt_neg"])
        self.assertEqual(Decimal("6.626e-34"), doc["flt_exp"])
        self.assertEqual(Decimal("1E5"), doc["flt_cap_exp"])
        self.assertEqual(Decimal("6.02e23"), doc["flt_both"])
        self.assertEqual(Decimal("224617.445991"), doc["flt_under"])
        for key in ("flt_plain", "flt_neg", "flt_exp", "flt_cap_exp", "flt_both", "flt_under"):
            self.assertIs(Decimal, type(doc[key]), key)
        self.assertIs(True, doc["bool_t"])
        self.assertIs(False, doc["bool_f"])
        self.assertEqual(datetime.datetime(1979, 5, 27, 7, 32, tzinfo=UTC), doc["odt_z"])
        self.assertEqual(doc["odt_z"], doc["odt_lower"])
        minus7 = datetime.timezone(datetime.timedelta(hours=-7))
        self.assertEqual(datetime.datetime(1979, 5, 27, 0, 32, 0, 999999, tzinfo=minus7), doc["odt_offset"])
        self.assertEqual(datetime.timedelta(hours=-7), doc["odt_offset"].utcoffset())
        plus530 = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
        self.assertEqual(datetime.datetime(1979, 5, 27, 7, 32, 0, 123456, tzinfo=plus530), doc["odt_space"])
        self.assertEqual(datetime.datetime(1979, 5, 27, 7, 32), doc["ldt"])
        self.assertIsNone(doc["ldt"].tzinfo)
        self.assertEqual(500000, doc["ldt_frac"].microsecond)
        self.assertEqual(datetime.date(1979, 5, 27), doc["ld"])
        self.assertIs(datetime.date, type(doc["ld"]))
        self.assertEqual(datetime.time(7, 32), doc["lt"])
        self.assertEqual(datetime.time(0, 32, 0, 999999), doc["lt_frac"])
        self.assertEqual([1, 2, 3], doc["arr_ints"])
        self.assertEqual(["a", "b", "c"], doc["arr_multi"])
        self.assertEqual([[1, 2], ["x", "y"], [[True], [False]]], doc["arr_nested"])
        self.assertEqual([1, "two", Decimal("3.0"), datetime.date(1979, 5, 27), {"k": "v"}], doc["arr_mixed"])
        self.assertEqual([], doc["arr_empty"])
        self.assertEqual({}, doc["inline_empty"])
        self.assertEqual({"name": "x", "dims": {"w": 1, "h": 2}, "dot": {"ted": {"key": 3}}, "list": [1, 2]},
                         doc["inline"])
        self.assertEqual(1, doc["quoted key"])
        self.assertEqual(2, doc["literal key"])
        self.assertEqual("empty quoted key", doc[""])
        self.assertEqual(3, doc['with "escape"'])
        self.assertEqual({"b": {"c": 4}}, doc["a"])
        self.assertEqual({"google.com": True}, doc["site"])
        self.assertEqual("numeric bare key", doc["1234"])
        self.assertEqual(5, doc["dashed-key_name"])
        self.assertEqual({"k": "v", "sub": {"k": "sub", "deeper": {"k": "deeper"}}}, doc["table"])
        self.assertEqual([{"name": "first", "detail": {"n": 1}}, {},
                          {"name": "third", "detail": {"n": 3}, "items": [{"sku": 1}, {"sku": 2}]}], doc["aot"])
        self.assertEqual({"apple": {"color": "red", "taste": {"sweet": True}, "texture": {"smooth": True}}},
                         doc["fruit"])
        self.assertEqual({"y": {"z": {"w": {"deep": True}}}, "q": 1}, doc["x"])
        self.assertEqual(["y", "q"], list(doc["x"]))
        self.assertEqual({"literal part": {"v": 1}}, doc["quoted table"])
        self.assertEqual({"header": {"v": 2}}, doc["spaced"])


class BadFixtureTest(unittest.TestCase):
    def fixture_names(self) -> List[str]:
        names = sorted(name for name in os.listdir(BAD) if name.endswith(".toml"))
        self.assertGreaterEqual(len(names), 40)
        return names

    def test_every_bad_fixture_is_rejected_with_its_message_and_line(self) -> None:
        for name in self.fixture_names():
            path = os.path.join(BAD, name)
            header = read_bytes(path).decode("utf-8", errors="replace")
            expected, expected_line, _ = expectations(header)
            with self.assertRaises(TomlError, msg=name) as ctx:
                load(path)
            error = ctx.exception
            self.assertIn(expected, error.message, name)
            self.assertEqual(expected_line, error.line, "%s: %s" % (name, error))
            self.assertEqual(path, error.source, name)
            self.assertTrue(str(error).startswith(path + ": "), str(error))
            if expected_line is not None:
                self.assertIsNotNone(error.col, name)
                self.assertIn("line %d, column %d: " % (expected_line, error.col), str(error))
            self.assertIsInstance(error, ValueError)

    @unittest.skipUnless(HAVE_TOMLLIB, "tomllib (Python 3.11+) is the cross-check oracle")
    def test_bad_fixtures_rejection_parity_with_tomllib(self) -> None:
        for name in self.fixture_names():
            raw = read_bytes(os.path.join(BAD, name))
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                continue  # tomllib.loads takes str; the UTF-8 defect is a load()-level check
            _, _, tomllib_accepts = expectations(text)
            ours, theirs = toml_compat.rejection_parity(text)
            self.assertIsNotNone(ours, name)
            if tomllib_accepts:
                self.assertIsNone(theirs, "%s: documented divergence no longer diverges" % name)
            else:
                self.assertIsNotNone(theirs, "%s: tomllib accepts what the vendored reader rejects" % name)

    def test_bad_fixtures_each_hold_a_distinct_defect(self) -> None:
        messages = set()
        for name in self.fixture_names():
            try:
                load(os.path.join(BAD, name))
            except TomlError as exc:
                messages.add((exc.line, exc.message))
        self.assertEqual(len(self.fixture_names()), len(messages))


class StringTest(unittest.TestCase):
    def test_escapes(self) -> None:
        self.assertEqual({"s": "\b\t\n\f\r\"\\ \u00e9 \U0001F600"},
                         loads(r'''s = "\b\t\n\f\r\"\\ \u00e9 \U0001F600"'''))

    def test_multiline_first_newline_trimmed_only_once(self) -> None:
        self.assertEqual({"s": "\nx"}, loads('s = """\n\nx"""'))
        self.assertEqual({"s": "x\n"}, loads('s = """x\n"""'))
        self.assertEqual({"s": "\nx\n"}, loads("s = '''\n\nx\n'''"))

    def test_multiline_line_ending_backslash(self) -> None:
        self.assertEqual({"s": "ab"}, loads('s = """a\\\n   \n\t  b"""'))
        self.assertEqual({"s": "ab"}, loads('s = """a\\   \n   b"""'))
        with self.assertRaisesRegex(TomlError, "backslash"):
            loads('s = """a\\ b"""')

    def test_multiline_closing_quotes(self) -> None:
        self.assertEqual({"s": 'x"'}, loads('s = """x""""'))
        self.assertEqual({"s": 'x""'}, loads('s = """x"""""'))
        self.assertEqual({"s": "x'"}, loads("s = '''x''''"))
        self.assertEqual({"s": "x''"}, loads("s = '''x'''''"))
        self.assertEqual({"s": '"a" ""b""'}, loads('s = """"a" ""b"\""""'))

    def test_crlf_normalised(self) -> None:
        self.assertEqual({"a": 1, "b": "x\ny"}, loads('a = 1\r\nb = """x\r\ny"""\r\n'))

    def test_literal_strings_take_no_escapes(self) -> None:
        self.assertEqual({"s": "\\n \\u0041"}, loads("s = '\\n \\u0041'"))
        self.assertEqual({"s": "\\n"}, loads("s = '''\\n'''"))

    def test_control_characters_rejected_everywhere(self) -> None:
        for text in ('s = "a\x01b"', "s = 'a\x01b'", 's = """a\x01b"""', "s = '''a\x01b'''", "# c\x01", "a = 1 \x01"):
            with self.assertRaises(TomlError, msg=repr(text)):
                loads(text)
        self.assertEqual({"s": "a\tb"}, loads('s = "a\tb"'))  # tab is allowed


class NumberTest(unittest.TestCase):
    def test_decimal_exactness(self) -> None:
        doc = loads("a = 0.1\nb = 0.2\nc = 3.42\nd = 1e-3\ne = -0.0\nf = 100.000")
        self.assertEqual(Decimal("0.3"), doc["a"] + doc["b"])
        self.assertEqual("3.42", str(doc["c"]))
        self.assertEqual(Decimal("0.001"), doc["d"])
        self.assertEqual(Decimal("-0.0"), doc["e"])
        self.assertEqual("100.000", str(doc["f"]))
        self.assertEqual(-3, doc["f"].as_tuple().exponent)

    def test_integer_forms(self) -> None:
        doc = loads("a = +0\nb = -0\nc = 1_2_3\nd = 0xff\ne = 0o17\nf = 0b11\ng = 9_223_372_036_854_775_807\nh = 170141183460469231731687303715884105727")
        self.assertEqual([0, 0, 123, 255, 15, 3, 9223372036854775807, 2 ** 127 - 1], list(doc.values()))

    def test_number_followed_by_junk_is_an_error(self) -> None:
        for text in ("a = 1x", "a = 1_", "a = _1", "a = 0x", "a = 0b2", "a = 1.2.3", "a = 1e5e5", "a = +-1", "a = 00", "a = -01"):
            with self.assertRaises(TomlError, msg=repr(text)):
                loads(text)

    def test_non_finite_rejected(self) -> None:
        for text in ("a = inf", "a = +inf", "a = -inf", "a = nan", "a = +nan", "a = -nan"):
            with self.assertRaisesRegex(TomlError, "non-finite", msg=text):
                loads(text)


class DateTimeTest(unittest.TestCase):
    def test_forms_and_types(self) -> None:
        doc = loads("a = 2026-11-02T14:31:05Z\nb = 2026-11-02 14:31:05-05:00\nc = 2026-11-02T14:31:05\nd = 2026-11-02\ne = 14:31:05\nf = 2026-11-02T14:31:05.0000005Z")
        self.assertEqual(datetime.datetime(2026, 11, 2, 14, 31, 5, tzinfo=UTC), doc["a"])
        self.assertEqual(datetime.timedelta(hours=-5), doc["b"].utcoffset())
        self.assertEqual(datetime.datetime(2026, 11, 2, 19, 31, 5, tzinfo=UTC), doc["b"])
        self.assertIsNone(doc["c"].tzinfo)
        self.assertIs(datetime.date, type(doc["d"]))
        self.assertIs(datetime.time, type(doc["e"]))
        self.assertEqual(0, doc["f"].microsecond)  # truncated, not rounded

    def test_invalid_components(self) -> None:
        for text in ("a = 2026-02-30", "a = 2026-00-10", "a = 2026-11-02T24:00:00Z", "a = 2026-11-02T10:60:00Z",
                     "a = 2026-11-02T10:00:60Z", "a = 2026-11-02T10:00:00+10:60", "a = 10:00", "a = 2026-11-02T10:00Z"):
            with self.assertRaises(TomlError, msg=text):
                loads(text)


class StructureTest(unittest.TestCase):
    def test_empty_and_comment_only_documents(self) -> None:
        self.assertEqual({}, loads(""))
        self.assertEqual({}, loads("# just a comment"))
        self.assertEqual({}, loads("\n\n   # c\n\n"))
        self.assertEqual({"a": 1}, loads("a = 1"))  # no trailing newline

    def test_array_of_tables_subtables_bind_to_latest_element(self) -> None:
        doc = loads("[[a]]\n[a.b]\nx = 1\n[[a]]\n[a.b]\nx = 2\n[a.c]\ny = 3")
        self.assertEqual({"a": [{"b": {"x": 1}}, {"b": {"x": 2}, "c": {"y": 3}}]}, doc)

    def test_super_table_after_sub_table_allowed_once(self) -> None:
        self.assertEqual({"a": {"b": {"c": 1}, "d": 2}}, loads("[a.b]\nc = 1\n[a]\nd = 2"))
        with self.assertRaisesRegex(TomlError, "defined twice"):
            loads("[a.b]\nc = 1\n[a]\nd = 2\n[a]\ne = 3")

    def test_dotted_keys_extend_within_a_section(self) -> None:
        self.assertEqual({"t": {"a": {"b": 1, "c": 2}, "d": 3}}, loads("[t]\na.b = 1\na.c = 2\nd = 3"))
        self.assertEqual({"a": {"b": 1}, "c": {"d": 2}}, loads("a.b = 1\nc.d = 2"))

    def test_inline_table_rules(self) -> None:
        self.assertEqual({"t": {"a": {"b": 1, "c": 2}}}, loads("t = { a.b = 1, a.c = 2 }"))
        with self.assertRaisesRegex(TomlError, "duplicate key"):
            loads("t = { a = 1, a = 2 }")
        with self.assertRaisesRegex(TomlError, "cannot be extended"):
            loads("t = { a = { b = 1 }, a.c = 2 }")
        with self.assertRaisesRegex(TomlError, "already holds a value"):
            loads("t = { a = 1, a.c = 2 }")
        # An inline table is closed: tomllib reports the frozen namespace before any duplicate.
        with self.assertRaisesRegex(TomlError, "cannot be extended"):
            loads("t = { a = 1 }\nt.a = 2")
        with self.assertRaisesRegex(TomlError, "cannot be extended"):
            loads("t = { a = 1 }\nt.b = 2")
        with self.assertRaisesRegex(TomlError, "cannot be extended"):
            loads("t = [1]\nt.b = 2")

    def test_root_keys_after_a_header_belong_to_that_table(self) -> None:
        self.assertEqual({"a": {"x": 1}, "b": {"y": 2}}, loads("[a]\nx = 1\n[b]\ny = 2"))

    def test_key_order_is_document_order(self) -> None:
        doc = loads("z = 1\n[m]\nq = 1\na = 2\n[b]\n[a]")
        self.assertEqual(["z", "m", "b", "a"], list(doc))
        self.assertEqual(["q", "a"], list(doc["m"]))

    def test_whitespace_and_comments_tolerated(self) -> None:
        doc = loads("\t a \t = \t 1 \t # c\n[ t . u ]   # c\n  v=2#c\n")
        self.assertEqual({"a": 1, "t": {"u": {"v": 2}}}, doc)

    def test_duplicate_detection_is_case_sensitive(self) -> None:
        self.assertEqual({"a": 1, "A": 2}, loads("a = 1\nA = 2"))

    def test_arrays_may_mix_types_and_span_lines(self) -> None:
        self.assertEqual({"a": [1, "x", [2], {"k": 3}, Decimal("1.5"), True]},
                         loads("a = [\n  1,\n  # c\n  'x', [2],\n  {k = 3}, 1.5, true\n]"))


class ErrorApiTest(unittest.TestCase):
    def test_error_attributes_and_rendering(self) -> None:
        with self.assertRaises(TomlError) as ctx:
            loads("a = 1\nb = \n")
        error = ctx.exception
        self.assertEqual(2, error.line)
        self.assertEqual(5, error.col)
        self.assertIsNone(error.source)
        self.assertEqual("line 2, column 5: " + error.message, str(error))
        self.assertIsInstance(error, ValueError)

    def test_loads_rejects_non_text(self) -> None:
        with self.assertRaises(TypeError):
            loads(b"a = 1")  # type: ignore[arg-type]

    def test_load_bom_and_file_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "bom.toml")
            with open(path, "wb") as handle:
                handle.write(b"\xef\xbb\xbfa = 1\n")
            self.assertEqual({"a": 1}, load(path))
            bad = os.path.join(directory, "bad.toml")
            with open(bad, "wb") as handle:
                handle.write(b"a = \xff\n")
            with self.assertRaises(TomlError) as ctx:
                load(bad)
            self.assertIn("not valid UTF-8", ctx.exception.message)
            self.assertEqual(bad, ctx.exception.source)
            self.assertIsNone(ctx.exception.line)
            self.assertTrue(str(ctx.exception).startswith(bad + ": "))
            with self.assertRaises(FileNotFoundError):
                load(os.path.join(directory, "missing.toml"))

    def test_module_exports(self) -> None:
        self.assertEqual(["TomlError", "load", "loads"], sorted(toml_reader.__all__))
        self.assertIs(toml_compat.loads("a = 1")["a"], 1)
        self.assertIs(toml_compat.TomlError, TomlError)


class TomlCompatTest(unittest.TestCase):
    def test_structural_differences(self) -> None:
        diff = toml_compat.structural_differences
        self.assertEqual([], diff({"a": [1, Decimal("1.5")]}, {"a": [1, Decimal("1.5")]}))
        self.assertTrue(diff({"a": True}, {"a": 1}))
        self.assertTrue(diff({"a": Decimal("1.0")}, {"a": Decimal("1")}))
        self.assertTrue(diff({"a": 1}, {"b": 1}))
        self.assertTrue(diff({"a": 1, "b": 2}, {"b": 2, "a": 1}))  # order matters
        self.assertTrue(diff([1], [1, 2]))
        self.assertTrue(diff({"a": 1}, [1]))
        naive = datetime.datetime(2026, 1, 1)
        self.assertTrue(diff({"a": naive}, {"a": naive.replace(tzinfo=UTC)}))
        plus1 = datetime.timezone(datetime.timedelta(hours=1))
        same_instant = datetime.datetime(2026, 1, 1, 1, tzinfo=plus1)
        self.assertTrue(diff({"a": datetime.datetime(2026, 1, 1, tzinfo=UTC)}, {"a": same_instant}))

    def test_cross_check_without_tomllib_returns_vendored_parse(self) -> None:
        original = toml_compat.tomllib_module
        toml_compat.tomllib_module = lambda: None  # type: ignore[assignment]
        try:
            self.assertEqual({"a": Decimal("1.5")}, toml_compat.cross_check("a = 1.5"))
            ours, theirs = toml_compat.rejection_parity("a = 1\na = 2")
            self.assertEqual("line 2, column 1: duplicate key a", ours)
            self.assertIsNone(theirs)
        finally:
            toml_compat.tomllib_module = original  # type: ignore[assignment]

    @unittest.skipUnless(HAVE_TOMLLIB, "tomllib (Python 3.11+) is the cross-check oracle")
    def test_cross_check_mismatch_is_reported(self) -> None:
        module = toml_compat.tomllib_module()
        self.assertTrue(hasattr(module, "TOMLDecodeError"))
        original = toml_reader.loads
        toml_reader.loads = lambda text: {"a": Decimal("2")}  # type: ignore[assignment]
        try:
            with self.assertRaises(toml_compat.CrossCheckMismatch) as ctx:
                toml_compat.cross_check("a = 1")
            self.assertEqual(1, len(ctx.exception.differences))
            self.assertIn("$.a", ctx.exception.differences[0])
        finally:
            toml_reader.loads = original  # type: ignore[assignment]


class SeededCrossCheckTest(unittest.TestCase):
    """Random documents rendered to TOML text: both readers must agree on every one."""

    @staticmethod
    def _render_scalar(rng: random.Random) -> Tuple[str, Any]:
        kind = rng.choice(("int", "dec", "str", "bool", "date", "datetime", "time"))
        if kind == "int":
            value = rng.randint(-10 ** 12, 10 ** 12)
            return str(value), value
        if kind == "dec":
            text = "%s%d.%0*d" % (rng.choice(("", "-")), rng.randint(0, 99999), rng.randint(1, 6), rng.randint(0, 999999))
            text = text[: text.index(".") + 1 + rng.randint(1, 6)]
            if rng.random() < 0.3:
                text += "e%s%d" % (rng.choice(("", "+", "-")), rng.randint(0, 30))
            return text, Decimal(text)
        if kind == "str":
            alphabet = "abc XYZ 09 \u00e9\u4e2d\U0001F4C8 '-_#=[]{}"
            raw = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
            raw += rng.choice(("", "\t", "\\", '"', "\n"))
            escaped = raw.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\t", "\\t")
            return '"%s"' % escaped, raw
        if kind == "bool":
            value = rng.random() < 0.5
            return ("true" if value else "false"), value
        day = datetime.date(2020, 1, 1) + datetime.timedelta(days=rng.randint(0, 3000))
        if kind == "date":
            return day.isoformat(), day
        clock = datetime.time(rng.randint(0, 23), rng.randint(0, 59), rng.randint(0, 59), rng.choice((0, 500000, 123456)))
        if kind == "time":
            return clock.isoformat(), clock
        offset = rng.choice((None, 0, 60, -300, 330))
        moment = datetime.datetime.combine(day, clock)
        if offset is None:
            return moment.isoformat(), moment
        tz = datetime.timezone(datetime.timedelta(minutes=offset))
        moment = moment.replace(tzinfo=tz)
        text = moment.isoformat()
        if offset == 0:
            text = text.replace("+00:00", "Z")
        return text, moment

    def test_random_documents_agree(self) -> None:
        rng = random.Random(20261102)
        for _ in range(150):
            lines: List[str] = []
            expected: Dict[str, Any] = {}
            for index in range(rng.randint(1, 8)):
                key = "k%d" % index
                if rng.random() < 0.25:
                    items = [self._render_scalar(rng) for _ in range(rng.randint(0, 4))]
                    lines.append("%s = [%s]" % (key, ", ".join(text for text, _ in items)))
                    expected[key] = [value for _, value in items]
                else:
                    text, value = self._render_scalar(rng)
                    lines.append("%s = %s" % (key, text))
                    expected[key] = value
            section = "s%d" % rng.randint(0, 9)
            lines.append("[%s]" % section)
            text, value = self._render_scalar(rng)
            lines.append("v = %s" % text)
            expected[section] = {"v": value}
            document = "\n".join(lines) + "\n"
            parsed = toml_compat.cross_check(document)  # raises CrossCheckMismatch under tomllib
            self.assertEqual([], toml_compat.structural_differences(parsed, expected), document)


if __name__ == "__main__":
    unittest.main()
