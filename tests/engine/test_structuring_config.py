"""Strict ``structuring.toml`` reader (``aegis_desk.structuring_config``, PR 3b decision S3 over designer C's D1).

The owner's structuring criteria: one merged schema (every section required, unknown keys refused, booleans never
numbers, the disclosure acknowledged by its id), the documented defaults loading with typed values, every bound
watched in both directions on a one-line mutation of a valid document, the error naming the file, the section and
the key, and the SHA-256 taken over the file's bytes (a UTF-8 byte-order mark included).
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import dataclasses
import hashlib
import os
import re
import tempfile
import unittest
from decimal import Decimal
from typing import List

from aegis_desk import alert_lint, structuring_config as sc

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXPECTED_INIT = os.path.join(ROOT, "tests", "fixtures", "init", "expected_structuring.toml")

BASE = '''schema_version = 1

[disclosure]
acknowledged = "structuring-suggestions-1"

[general]
max_spread_pct = 25
max_candidates_shown = 3

[covered_call]
enabled = true
dte = [40, 75]
abs_delta = [0.15, 0.35]
min_credit = 0.50
strike_floor = "cost_basis"
earnings = "exclude"
ex_dividend = "exclude"
objective = "extrinsic_per_day"

[roll]
enabled = true
dte = [40, 75]
abs_delta = [0.15, 0.35]
min_credit = 0.50
credit_only = true
strike_floor = "cost_basis"
earnings = "exclude"
ex_dividend = "exclude"
objective = "extrinsic_per_day"

[protective_put]
enabled = true
dte = [120, 210]
floor_pct = [3, 10]
stress_drop_pct = 20
objective = "stress_cost_per_day"

[collar]
enabled = true
dte = [120, 210]
floor_pct = [3, 10]
cap_pct = [2, 10]
max_net_debit = 0.00
strike_floor = "cost_basis"
earnings = "exclude"
ex_dividend = "exclude"
objective = "cap_within_budget"
'''

DISCLOSURE_TEXT = (
    "The Aegis Structuring Desk answers only when you ask. For a position you hold in positions.csv, its tools list "
    "candidates from delayed Alpaca paper quotes (indicative option quotes, which are approximate): closing or "
    "unwinding it, a replacement for a short option you are closing or that is expiring, or a covered call, protective "
    "put or collar on shares you hold. They score the "
    "candidates by the objective and criteria in this file and name the highest-scoring one with its reasons. That "
    "is a personalized suggestion computed by deterministic tools and posted by an AI assistant; it is not an order, "
    "not a forecast and not a judgment that a trade suits you. The desk knows only your positions, these criteria and "
    "the approval level you stated; it does not see your cash, your other holdings, your objectives or your taxes. It "
    "never places anything: you decide, you supply the limit and you act in your brokerage account. Options involve "
    "substantial risk; you confirm that you received the OCC booklet Characteristics and Risks of Standardized "
    "Options.")

DTE_SECTIONS = ("covered_call", "roll", "protective_put", "collar")
SHORT_CALL_SECTIONS = ("covered_call", "roll")
EVENT_SECTIONS = ("covered_call", "roll", "collar")
FLOOR_SECTIONS = ("protective_put", "collar")


def section_text(name: str, base: str = BASE) -> str:
    """The ``[name]`` block of ``base`` (its header to the next blank line)."""
    start = base.index("[%s]\n" % name)
    end = base.find("\n\n", start)
    return base[start:] if end < 0 else base[start:end + 1]


def in_section(name: str, old: str, new: str, base: str = BASE) -> str:
    """``base`` with ``old`` replaced by ``new`` inside the ``[name]`` block only (exactly once)."""
    block = section_text(name, base)
    if block.count(old) != 1:
        raise AssertionError("%r must occur once in [%s]" % (old, name))
    return base.replace(block, block.replace(old, new, 1), 1)


class StructuringConfigTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-structuring-cfg-")
        self.dir = self._tmp.name

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, text: str, name: str = "structuring.toml", encoding: str = "utf-8") -> str:
        path = os.path.join(self.dir, name)
        with open(path, "w", encoding=encoding, newline="\n") as handle:
            handle.write(text)
        return path

    def load_text(self, text: str) -> "sc.StructuringConfig":
        return sc.load_structuring_config(self.write(text))

    def assert_rejected(self, text: str, *needles: str) -> "sc.StructuringConfigError":
        path = self.write(text)
        with self.assertRaises(sc.StructuringConfigError) as ctx:
            sc.load_structuring_config(path)
        message = str(ctx.exception)
        self.assertEqual("%s: %s" % (path, ctx.exception.reason), message)
        self.assertEqual(path, ctx.exception.path)
        self.assertNotIn("\n", message)  # one line
        for needle in needles:
            self.assertIn(needle, message, message)
        self.assertIsInstance(ctx.exception, ValueError)
        with self.assertRaises(sc.StructuringConfigError):
            sc.parse_structuring_toml_text(text)
        return ctx.exception

    def assert_loads(self, text: str) -> "sc.StructuringConfig":
        try:
            return self.load_text(text)
        except sc.StructuringConfigError as exc:  # pragma: no cover - the failure message is the diagnosis
            self.fail("expected the document to load: %s" % exc)


class ConstantsTest(unittest.TestCase):
    def test_schema_disclosure_and_sections(self) -> None:
        self.assertEqual(1, sc.SCHEMA_VERSION)
        self.assertEqual("structuring-suggestions-1", sc.DISCLOSURE_ID)
        self.assertEqual(DISCLOSURE_TEXT, sc.DISCLOSURE_TEXT)
        self.assertEqual(("disclosure", "general", "covered_call", "roll", "protective_put", "collar"), sc.SECTIONS)
        self.assertEqual({
            "disclosure": ("acknowledged",),
            "general": ("max_spread_pct", "max_candidates_shown"),
            "covered_call": ("enabled", "dte", "abs_delta", "min_credit", "strike_floor", "earnings", "ex_dividend",
                             "objective"),
            "roll": ("enabled", "dte", "abs_delta", "min_credit", "credit_only", "strike_floor", "earnings",
                     "ex_dividend", "objective"),
            "protective_put": ("enabled", "dte", "floor_pct", "stress_drop_pct", "objective"),
            "collar": ("enabled", "dte", "floor_pct", "cap_pct", "max_net_debit", "strike_floor", "earnings",
                       "ex_dividend", "objective"),
        }, sc.SECTION_KEYS)
        self.assertEqual(list(sc.SECTIONS), list(sc.SECTION_KEYS))
        self.assertEqual(("exclude", "allow"), sc.EVENT_POLICIES)
        self.assertEqual(("cost_basis", "none"), sc.STRIKE_FLOORS)
        self.assertEqual("disabled in your structuring.toml", sc.DISABLED_REASON)
        self.assertEqual("structuring.toml", sc.FILE_NAME)

    def test_bounds(self) -> None:
        self.assertEqual((7, 400, 90), (sc.DTE_MIN, sc.DTE_MAX, sc.DTE_SPAN_MAX))
        self.assertEqual((Decimal("0.05"), Decimal("0.60")), sc.ABS_DELTA_RANGE)
        self.assertEqual(Decimal("0.01"), sc.MIN_CREDIT_MIN)
        self.assertEqual(Decimal("25"), sc.MAX_SPREAD_PCT_MAX)
        self.assertEqual((1, 10), sc.MAX_CANDIDATES_RANGE)
        self.assertEqual((Decimal("0"), Decimal("50")), sc.FLOOR_PCT_RANGE)
        self.assertEqual((Decimal("0"), Decimal("100")), sc.CAP_PCT_RANGE)
        self.assertEqual(Decimal("90"), sc.STRESS_DROP_PCT_MAX)
        self.assertEqual(Decimal("0"), sc.MAX_NET_DEBIT_MIN)
        self.assertEqual(2, sc.DECIMAL_PLACES)

    def test_objectives_one_id_per_section(self) -> None:
        self.assertEqual({"covered_call": ("extrinsic_per_day",), "roll": ("extrinsic_per_day",),
                          "protective_put": ("stress_cost_per_day",), "collar": ("cap_within_budget",)},
                         sc.OBJECTIVES)
        self.assertEqual(("extrinsic_per_day", "stress_cost_per_day", "cap_within_budget"), sc.OBJECTIVE_IDS)
        self.assertEqual(("extrinsic_per_day", "stress_cost_per_day", "cap_within_budget"),
                         (sc.OBJECTIVE_EXTRINSIC_PER_DAY, sc.OBJECTIVE_STRESS_COST_PER_DAY,
                          sc.OBJECTIVE_CAP_WITHIN_BUDGET))
        for section, ids in sc.OBJECTIVES.items():
            self.assertEqual(1, len(ids), section)
            self.assertEqual(ids[0], sc.objective_for(section).id)
        self.assertEqual(set(sc.OBJECTIVE_IDS), set(sc.OBJECTIVE_DEFS))

    def test_objective_words_formulas_and_directions(self) -> None:
        extrinsic = sc.OBJECTIVE_DEFS["extrinsic_per_day"]
        self.assertEqual("the bid's time value per calendar day, in percent of the share price", extrinsic.words)
        self.assertEqual(sc.DIRECTION_HIGHER, extrinsic.direction)
        self.assertEqual("E = bid - max(0, S - K); score = E / S / T_c x 100, quantized 0.0001 half-even",
                         extrinsic.formula)
        stress = sc.OBJECTIVE_DEFS["stress_cost_per_day"]
        self.assertEqual("the put's ask per calendar day per 100 of loss it offsets at your stress drop", stress.words)
        self.assertEqual(sc.DIRECTION_LOWER, stress.direction)
        self.assertIn("S_x = S x (1 - x / 100)", stress.formula)
        self.assertIn("score = ask / T_c / (K - S_x) x 100", stress.formula)
        cap = sc.OBJECTIVE_DEFS["cap_within_budget"]
        self.assertEqual("the cap's distance above the share price, in percent, among pairs within your net-debit "
                         "budget", cap.words)
        self.assertEqual(sc.DIRECTION_HIGHER, cap.direction)
        self.assertIn("U = (Kc - S) / S x 100", cap.formula)
        self.assertEqual(("higher", "lower"), (sc.DIRECTION_HIGHER, sc.DIRECTION_LOWER))
        for objective in sc.OBJECTIVE_DEFS.values():
            self.assertTrue(objective.unit)
            self.assertEqual(objective, sc.OBJECTIVE_DEFS[objective.id])

    def test_no_claim_word_and_no_order_shape_in_any_constant(self) -> None:
        texts: List[str] = [sc.DISCLOSURE_TEXT, sc.DISABLED_REASON, sc.UNKNOWN_EVENT_RULE]
        for objective in sc.OBJECTIVE_DEFS.values():
            texts += [objective.id, objective.words, objective.formula, objective.unit]
            # C D6: objective words never use return, yield, profit or income.
            for word in ("return", "yield", "profit", "income"):
                self.assertNotIn(word, (objective.id + " " + objective.words + " " + objective.unit).lower())
        names = list(sc.SECTIONS) + list(sc.OBJECTIVE_IDS) + [key for keys in sc.SECTION_KEYS.values() for key in keys]
        for text in texts + names:
            self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(text)], text)
        # S2 and C D1: no section, key or objective name holds target, return, yield, recommend or a roll ... to form.
        joined = " ".join(names)
        for pattern in (r"target", r"return", r"yield", r"recommend", r"roll\w*[\s_]+to\b", r"roll_to"):
            self.assertIsNone(re.search(pattern, joined), pattern)

    def test_unknown_events_rule_is_stated(self) -> None:
        self.assertIn("counts as inside the window", sc.UNKNOWN_EVENT_RULE)
        self.assertIn("never treated as none", sc.UNKNOWN_EVENT_RULE)


class LoadsValidDocumentsTest(StructuringConfigTestCase):
    def test_the_documented_defaults_load_with_typed_values(self) -> None:
        path = self.write(BASE)
        cfg = sc.load_structuring_config(path)
        self.assertEqual(path, cfg.path)
        with open(path, "rb") as handle:
            self.assertEqual(hashlib.sha256(handle.read()).hexdigest(), cfg.sha256)
        self.assertEqual(64, len(cfg.sha256))
        self.assertEqual(1, cfg.schema_version)
        self.assertEqual("structuring-suggestions-1", cfg.disclosure.acknowledged)
        self.assertEqual(Decimal("25"), cfg.general.max_spread_pct)
        self.assertEqual(3, cfg.general.max_candidates_shown)
        call = cfg.covered_call
        self.assertIs(True, call.enabled)
        self.assertEqual((40, 75), call.dte)
        self.assertTrue(all(type(day) is int for day in call.dte))
        self.assertEqual((Decimal("0.15"), Decimal("0.35")), call.abs_delta)
        self.assertEqual("0.50", str(call.min_credit))
        self.assertEqual(("cost_basis", "exclude", "exclude", "extrinsic_per_day"),
                         (call.strike_floor, call.earnings, call.ex_dividend, call.objective))
        roll = cfg.roll
        self.assertEqual((True, (40, 75), (Decimal("0.15"), Decimal("0.35")), Decimal("0.50"), True),
                         (roll.enabled, roll.dte, roll.abs_delta, roll.min_credit, roll.credit_only))
        self.assertEqual(("cost_basis", "exclude", "exclude", "extrinsic_per_day"),
                         (roll.strike_floor, roll.earnings, roll.ex_dividend, roll.objective))
        put = cfg.protective_put
        self.assertEqual((True, (120, 210), (Decimal(3), Decimal(10)), Decimal(20), "stress_cost_per_day"),
                         (put.enabled, put.dte, put.floor_pct, put.stress_drop_pct, put.objective))
        self.assertTrue(all(isinstance(value, Decimal) for value in put.floor_pct + (put.stress_drop_pct,)))
        collar = cfg.collar
        self.assertEqual((True, (120, 210), (Decimal(3), Decimal(10)), (Decimal(2), Decimal(10)), "0.00"),
                         (collar.enabled, collar.dte, collar.floor_pct, collar.cap_pct, str(collar.max_net_debit)))
        self.assertEqual(("cost_basis", "exclude", "exclude", "cap_within_budget"),
                         (collar.strike_floor, collar.earnings, collar.ex_dividend, collar.objective))

    def test_section_lookup_objective_and_enabled(self) -> None:
        cfg = self.load_text(BASE)
        for name in sc.SECTIONS:
            self.assertIs(getattr(cfg, name), cfg.section(name))
        with self.assertRaises(KeyError):
            cfg.section("vertical")
        self.assertEqual(sc.OBJECTIVE_DEFS["extrinsic_per_day"], cfg.objective("roll"))
        self.assertEqual(sc.OBJECTIVE_DEFS["cap_within_budget"], cfg.objective("collar"))
        self.assertTrue(cfg.enabled("collar"))
        cfg = self.load_text(in_section("collar", "enabled = true", "enabled = false"))
        self.assertFalse(cfg.enabled("collar"))
        self.assertTrue(cfg.enabled("covered_call"))

    def test_the_init_fixture_loads_as_the_base_document(self) -> None:
        rendered = sc.load_structuring_config(EXPECTED_INIT)
        base = self.load_text(BASE)
        self.assertEqual(dataclasses.replace(base, sha256="", path=""),
                         dataclasses.replace(rendered, sha256="", path=""))

    def test_parse_text_hashes_the_utf8_text(self) -> None:
        cfg = sc.parse_structuring_toml_text(BASE)
        self.assertEqual(hashlib.sha256(BASE.encode("utf-8")).hexdigest(), cfg.sha256)
        self.assertEqual("<text>", cfg.path)
        self.assertEqual("x.toml", sc.parse_structuring_toml_text(BASE, path="x.toml").path)
        with self.assertRaises(sc.StructuringConfigError) as ctx:
            sc.parse_structuring_toml_text(BASE.encode("utf-8"))  # type: ignore[arg-type]
        self.assertIn("must be a str", str(ctx.exception))

    def test_a_byte_order_mark_is_read_and_hashed_with_the_bytes(self) -> None:
        path = self.write(BASE, encoding="utf-8-sig")
        with open(path, "rb") as handle:
            data = handle.read()
        self.assertTrue(data.startswith(b"\xef\xbb\xbf"))
        cfg = sc.load_structuring_config(path)
        self.assertEqual(hashlib.sha256(data).hexdigest(), cfg.sha256)
        self.assertNotEqual(hashlib.sha256(BASE.encode("utf-8")).hexdigest(), cfg.sha256)
        self.assertEqual(3, cfg.general.max_candidates_shown)

    def test_comments_and_the_header_lines_change_only_the_hash(self) -> None:
        commented = "# a header line\n# a second header line\n" + BASE.replace("dte = [40, 75]", "dte = [40, 75]  # days", 1)
        first, second = self.load_text(BASE), self.load_text(commented)
        self.assertNotEqual(first.sha256, second.sha256)
        self.assertEqual(dataclasses.replace(first, sha256="", path=""), dataclasses.replace(second, sha256="", path=""))

    def test_integers_are_accepted_where_a_decimal_is_allowed(self) -> None:
        cfg = self.load_text(in_section("covered_call", "min_credit = 0.50", "min_credit = 1")
                             .replace("max_net_debit = 0.00", "max_net_debit = 0"))
        self.assertEqual(Decimal(1), cfg.covered_call.min_credit)
        self.assertIsInstance(cfg.covered_call.min_credit, Decimal)
        self.assertEqual(Decimal(0), cfg.collar.max_net_debit)
        cfg = self.load_text(in_section("protective_put", "floor_pct = [3, 10]", "floor_pct = [3.50, 10.25]"))
        self.assertEqual((Decimal("3.50"), Decimal("10.25")), cfg.protective_put.floor_pct)

    def test_a_disabled_section_is_still_read_in_full(self) -> None:
        text = in_section("roll", "enabled = true", "enabled = false")
        self.assertFalse(self.load_text(text).roll.enabled)
        self.assert_rejected(in_section("roll", "credit_only = true\n", "", base=text), "[roll]", "credit_only")
        self.assert_rejected(in_section("roll", "dte = [40, 75]", "dte = [1, 2]", base=text), "[roll]", "dte")

    def test_if_present(self) -> None:
        self.assertIsNone(sc.load_structuring_config_if_present(os.path.join(self.dir, "missing.toml")))
        cfg = sc.load_structuring_config_if_present(self.write(BASE))
        self.assertIsNotNone(cfg)
        assert cfg is not None
        self.assertEqual(3, cfg.general.max_candidates_shown)
        with self.assertRaises(sc.StructuringConfigError):
            sc.load_structuring_config_if_present(self.write(BASE.replace("schema_version = 1", "schema_version = 2")))

    def test_frozen(self) -> None:
        cfg = self.load_text(BASE)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cfg.sha256 = "x"  # type: ignore[misc]
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cfg.covered_call.enabled = False  # type: ignore[misc]

    def test_as_dict_echoes_every_value_in_the_file_order(self) -> None:
        cfg = self.load_text(BASE)
        echo = cfg.as_dict()
        self.assertEqual(["schema_version"] + list(sc.SECTIONS) + ["sha256"], list(echo))
        for name in sc.SECTIONS:
            self.assertEqual(list(sc.SECTION_KEYS[name]), list(echo[name]), name)
        self.assertEqual([40, 75], echo["covered_call"]["dte"])
        self.assertEqual([Decimal("0.15"), Decimal("0.35")], echo["roll"]["abs_delta"])
        self.assertEqual(Decimal("0.00"), echo["collar"]["max_net_debit"])
        self.assertEqual(cfg.sha256, echo["sha256"])
        self.assertIs(True, echo["roll"]["credit_only"])


class FileErrorsTest(StructuringConfigTestCase):
    def test_unreadable_and_non_utf8_files(self) -> None:
        with self.assertRaises(sc.StructuringConfigError) as ctx:
            sc.load_structuring_config(self.dir)  # a directory
        self.assertIn("cannot read structuring.toml", str(ctx.exception))
        path = os.path.join(self.dir, "bad.toml")
        with open(path, "wb") as handle:
            handle.write(b"schema_version = 1\n# \xff\n")
        with self.assertRaises(sc.StructuringConfigError) as ctx:
            sc.load_structuring_config(path)
        self.assertIn("not valid UTF-8", str(ctx.exception))

    def test_invalid_toml_names_the_line(self) -> None:
        self.assert_rejected(BASE.replace("dte = [40, 75]", "dte = [40, 75", 1), "invalid TOML", "line")


class StructureRulesTest(StructuringConfigTestCase):
    def test_schema_version_must_be_the_integer_one(self) -> None:
        for value in ("2", "0", '"1"', "true", "1.0"):
            self.assert_rejected(BASE.replace("schema_version = 1", "schema_version = %s" % value),
                                 "schema_version must be the integer 1")
        self.assert_rejected(BASE.replace("schema_version = 1\n", ""), "missing key 'schema_version'")

    def test_every_section_is_required(self) -> None:
        for name in sc.SECTIONS:
            with self.subTest(section=name):
                self.assert_rejected(BASE.replace(section_text(name), ""), "missing section [%s]" % name)

    def test_unknown_sections_and_top_level_keys(self) -> None:
        self.assert_rejected(BASE + "\n[vertical]\nenabled = true\n", "unknown section [vertical]")
        self.assert_rejected("dte_unit = \"calendar\"\n" + BASE, "unknown top-level key 'dte_unit'")
        self.assert_rejected(BASE.replace("[general]\n", "[general.extra]\nx = 1\n\n[general]\n"),
                             "unknown key 'extra' in section [general]")

    def test_a_section_must_be_a_table(self) -> None:
        text = BASE.replace(section_text("general"), "").replace("schema_version = 1\n", "schema_version = 1\ngeneral = 3\n")
        self.assert_rejected(text, "section [general] must be a table")

    def test_unknown_keys_in_every_section_name_the_key_and_the_section(self) -> None:
        dropped = {"general": "min_quote_grade = \"C\"", "covered_call": "max_debit_pct_of_spot = 3",
                   "roll": "min_net_credit = 0.05", "protective_put": "abs_delta = [0.20, 0.40]",
                   "collar": "put_abs_delta = [0.20, 0.40]", "disclosure": "text = \"x\""}
        for name, line in dropped.items():
            key = line.split(" ")[0]
            with self.subTest(section=name):
                header = "[%s]\n" % name
                self.assert_rejected(BASE.replace(header, header + line + "\n", 1),
                                     "unknown key '%s' in section [%s]" % (key, name))
        # credit_only belongs to [roll] only.
        self.assert_rejected(in_section("covered_call", "enabled = true", "enabled = true\ncredit_only = true"),
                             "unknown key 'credit_only' in section [covered_call]")
        self.assert_rejected(in_section("collar", "enabled = true", "enabled = true\nstress_drop_pct = 20"),
                             "unknown key 'stress_drop_pct' in section [collar]")

    def test_every_key_is_required(self) -> None:
        for name, keys in sc.SECTION_KEYS.items():
            for key in keys:
                with self.subTest(section=name, key=key):
                    block = section_text(name)
                    line = next(line for line in block.splitlines() if line.startswith(key + " = "))
                    self.assert_rejected(in_section(name, line + "\n", ""),
                                         "missing key '%s' in section [%s]" % (key, name))

    def test_the_disclosure_must_be_acknowledged_by_its_id(self) -> None:
        reason = ("the structuring disclosure structuring-suggestions-1 is not acknowledged in this file: run the "
                  "structuring interview again")
        for value in ('"structuring-suggestions-2"', '"STRUCTURING-SUGGESTIONS-1"', '""', "true", "1",
                      '" structuring-suggestions-1"'):
            with self.subTest(value=value):
                error = self.assert_rejected(BASE.replace('acknowledged = "structuring-suggestions-1"',
                                                          "acknowledged = %s" % value))
                self.assertEqual(reason, error.reason)


class GeneralTest(StructuringConfigTestCase):
    def test_max_spread_pct_lies_in_zero_exclusive_to_twenty_five(self) -> None:
        for good in ("25", "0.01", "12.5", "25.00"):
            self.assertEqual(Decimal(good), self.assert_loads(BASE.replace("max_spread_pct = 25",
                                                                           "max_spread_pct = %s" % good)).general.max_spread_pct)
        for bad in ("0", "0.00", "-1", "25.01", "26", "100"):
            self.assert_rejected(BASE.replace("max_spread_pct = 25", "max_spread_pct = %s" % bad),
                                 "[general] max_spread_pct", "(0, 25]")
        self.assert_rejected(BASE.replace("max_spread_pct = 25", "max_spread_pct = 12.505"),
                             "[general] max_spread_pct", "at most 2 decimal places")

    def test_max_candidates_shown_is_an_integer_from_one_to_ten(self) -> None:
        for good in (1, 10):
            self.assertEqual(good, self.assert_loads(BASE.replace("max_candidates_shown = 3",
                                                                  "max_candidates_shown = %d" % good)).general.max_candidates_shown)
        for bad in ("0", "11", "-3"):
            self.assert_rejected(BASE.replace("max_candidates_shown = 3", "max_candidates_shown = %s" % bad),
                                 "[general] max_candidates_shown", "between 1 and 10")
        for bad in ("3.0", '"3"', "true"):
            self.assert_rejected(BASE.replace("max_candidates_shown = 3", "max_candidates_shown = %s" % bad),
                                 "[general] max_candidates_shown must be an integer")


class BooleansAreNeverNumbersTest(StructuringConfigTestCase):
    def test_a_boolean_where_a_number_is_expected(self) -> None:
        cases = [
            BASE.replace("max_spread_pct = 25", "max_spread_pct = true"),
            in_section("covered_call", "min_credit = 0.50", "min_credit = true"),
            in_section("covered_call", "dte = [40, 75]", "dte = [true, 75]"),
            in_section("roll", "abs_delta = [0.15, 0.35]", "abs_delta = [0.15, false]"),
            in_section("protective_put", "stress_drop_pct = 20", "stress_drop_pct = true"),
            in_section("protective_put", "floor_pct = [3, 10]", "floor_pct = [false, 10]"),
            in_section("collar", "cap_pct = [2, 10]", "cap_pct = [2, true]"),
            in_section("collar", "max_net_debit = 0.00", "max_net_debit = false"),
        ]
        for text in cases:
            error = self.assert_rejected(text)
            self.assertRegex(error.reason, r"must be (?:a number|numbers|integers|an integer)", error.reason)

    def test_a_number_where_a_boolean_is_expected(self) -> None:
        for name in DTE_SECTIONS:
            for value in ("1", "0", '"true"', "1.0"):
                self.assert_rejected(in_section(name, "enabled = true", "enabled = %s" % value),
                                     "[%s] enabled must be a boolean" % name)
        for value in ("1", '"yes"'):
            self.assert_rejected(in_section("roll", "credit_only = true", "credit_only = %s" % value),
                                 "[roll] credit_only must be a boolean")

    def test_a_string_is_never_a_number_and_infinity_is_refused(self) -> None:
        self.assert_rejected(BASE.replace("max_spread_pct = 25", 'max_spread_pct = "25"'),
                             "[general] max_spread_pct must be a number")
        for value in ("inf", "nan", "-inf"):  # the vendored reader refuses non-finite numbers itself
            self.assert_rejected(BASE.replace("max_spread_pct = 25", "max_spread_pct = %s" % value),
                                 "invalid TOML", "non-finite number")


class DteWindowTest(StructuringConfigTestCase):
    def test_bounds_in_every_section(self) -> None:
        for name in DTE_SECTIONS:
            line = next(line for line in section_text(name).splitlines() if line.startswith("dte = "))
            with self.subTest(section=name):
                for good, expected in (("[7, 8]", (7, 8)), ("[310, 400]", (310, 400)), ("[40, 130]", (40, 130))):
                    cfg = self.assert_loads(in_section(name, line, "dte = %s" % good))
                    self.assertEqual(expected, cfg.section(name).dte)
                for bad in ("[6, 40]", "[0, 40]", "[-7, 40]", "[40, 40]", "[75, 40]", "[350, 401]", "[40, 131]",
                            "[7, 400]"):
                    self.assert_rejected(in_section(name, line, "dte = %s" % bad), "[%s] dte" % name,
                                         "7 <= lo < hi <= 400 and hi - lo <= 90")

    def test_shape_and_integers(self) -> None:
        for bad, needle in (("[40]", "two"), ("[40, 75, 90]", "two"), ("40", "two"), ('"40-75"', "two"),
                            ("[40.0, 75]", "integers"), ("[40, 75.5]", "integers"), ('["40", 75]', "integers"),
                            ("[true, 75]", "integers")):
            with self.subTest(value=bad):
                self.assert_rejected(in_section("covered_call", "dte = [40, 75]", "dte = %s" % bad),
                                     "[covered_call] dte", needle)


class AbsDeltaTest(StructuringConfigTestCase):
    def test_bounds_in_both_short_call_sections(self) -> None:
        for name in SHORT_CALL_SECTIONS:
            with self.subTest(section=name):
                for good in ("[0.05, 0.60]", "[0.05, 0.06]", "[0.59, 0.60]", "[0.150, 0.35]"):
                    self.assert_loads(in_section(name, "abs_delta = [0.15, 0.35]", "abs_delta = %s" % good))
                for bad in ("[0.04, 0.35]", "[0.15, 0.61]", "[0.35, 0.15]", "[0.15, 0.15]", "[0, 1]", "[-0.15, 0.35]"):
                    self.assert_rejected(in_section(name, "abs_delta = [0.15, 0.35]", "abs_delta = %s" % bad),
                                         "[%s] abs_delta" % name, "0.05 <= lo < hi <= 0.60")
                self.assert_rejected(in_section(name, "abs_delta = [0.15, 0.35]", "abs_delta = [0.155, 0.35]"),
                                     "[%s] abs_delta" % name, "at most 2 decimal places")
                self.assert_rejected(in_section(name, "abs_delta = [0.15, 0.35]", "abs_delta = [0.15]"),
                                     "[%s] abs_delta" % name, "two")
                self.assert_rejected(in_section(name, "abs_delta = [0.15, 0.35]", 'abs_delta = ["0.15", 0.35]'),
                                     "[%s] abs_delta" % name, "numbers")


class MinCreditTest(StructuringConfigTestCase):
    def test_at_least_one_cent_per_share(self) -> None:
        for name in SHORT_CALL_SECTIONS:
            with self.subTest(section=name):
                for good in ("0.01", "1", "12.50"):
                    self.assert_loads(in_section(name, "min_credit = 0.50", "min_credit = %s" % good))
                for bad in ("0.00", "0", "-0.50", "0.009"):
                    error = self.assert_rejected(in_section(name, "min_credit = 0.50", "min_credit = %s" % bad),
                                                 "[%s] min_credit" % name)
                    self.assertTrue(">= 0.01" in error.reason or "at most 2 decimal places" in error.reason, error.reason)
                self.assert_rejected(in_section(name, "min_credit = 0.50", "min_credit = 0.505"),
                                     "[%s] min_credit" % name, "at most 2 decimal places")


class ChoicesTest(StructuringConfigTestCase):
    def test_strike_floor_and_event_policies(self) -> None:
        for name in EVENT_SECTIONS:
            with self.subTest(section=name):
                self.assert_loads(in_section(name, 'strike_floor = "cost_basis"', 'strike_floor = "none"'))
                self.assert_loads(in_section(name, 'earnings = "exclude"', 'earnings = "allow"'))
                self.assert_loads(in_section(name, 'ex_dividend = "exclude"', 'ex_dividend = "allow"'))
                for key, old, bad in (("strike_floor", '"cost_basis"', '"cost"'), ("strike_floor", '"cost_basis"', "false"),
                                      ("earnings", '"exclude"', '"skip"'), ("earnings", '"exclude"', "true"),
                                      ("ex_dividend", '"exclude"', '"Exclude"'), ("ex_dividend", '"exclude"', '"none"')):
                    self.assert_rejected(in_section(name, "%s = %s" % (key, old), "%s = %s" % (key, bad)),
                                         "[%s] %s must be one of" % (name, key))

    def test_each_section_takes_only_its_own_objective(self) -> None:
        for name, (objective,) in sc.OBJECTIVES.items():
            line = 'objective = "%s"' % objective
            for other in sc.OBJECTIVE_IDS + ("credit_per_day", "return_if_unchanged_per_day", ""):
                if other == objective:
                    continue
                with self.subTest(section=name, objective=other):
                    self.assert_rejected(in_section(name, line, 'objective = "%s"' % other),
                                         "[%s] objective must be %s" % (name, objective))
            self.assert_rejected(in_section(name, line, "objective = 1"), "[%s] objective must be %s" % (name, objective))


class ProtectionBandsTest(StructuringConfigTestCase):
    def test_floor_pct_bounds(self) -> None:
        for name in FLOOR_SECTIONS:
            with self.subTest(section=name):
                for good in ("[0, 50]", "[0, 0.01]", "[3.25, 10]"):
                    text = in_section(name, "floor_pct = [3, 10]", "floor_pct = %s" % good)
                    if name == "protective_put":
                        text = in_section(name, "stress_drop_pct = 20", "stress_drop_pct = 60", base=text)
                    self.assert_loads(text)
                for bad in ("[-1, 10]", "[3, 51]", "[10, 3]", "[3, 3]", "[-0.01, 10]", "[3, 50.01]"):
                    self.assert_rejected(in_section(name, "floor_pct = [3, 10]", "floor_pct = %s" % bad),
                                         "[%s] floor_pct" % name, "0 <= lo < hi <= 50")
                self.assert_rejected(in_section(name, "floor_pct = [3, 10]", "floor_pct = [3.125, 10]"),
                                     "[%s] floor_pct" % name, "at most 2 decimal places")

    def test_stress_drop_lies_above_the_floor_band_and_at_most_ninety(self) -> None:
        for good in ("10.01", "90", "45.5"):
            cfg = self.assert_loads(in_section("protective_put", "stress_drop_pct = 20", "stress_drop_pct = %s" % good))
            self.assertEqual(Decimal(good), cfg.protective_put.stress_drop_pct)
        for bad in ("10", "9.99", "3", "0", "90.01", "100"):
            self.assert_rejected(in_section("protective_put", "stress_drop_pct = 20", "stress_drop_pct = %s" % bad),
                                 "[protective_put] stress_drop_pct", "10 < x <= 90")
        text = in_section("protective_put", "floor_pct = [3, 10]", "floor_pct = [3, 25]")
        self.assert_rejected(text, "[protective_put] stress_drop_pct", "25 < x <= 90")
        self.assert_rejected(in_section("protective_put", "stress_drop_pct = 20", "stress_drop_pct = 20.005"),
                             "[protective_put] stress_drop_pct", "at most 2 decimal places")

    def test_cap_pct_bounds(self) -> None:
        for good in ("[0.01, 100]", "[2, 3]", "[99.99, 100]"):
            self.assert_loads(in_section("collar", "cap_pct = [2, 10]", "cap_pct = %s" % good))
        for bad in ("[0, 10]", "[-1, 10]", "[2, 100.01]", "[10, 2]", "[2, 2]"):
            self.assert_rejected(in_section("collar", "cap_pct = [2, 10]", "cap_pct = %s" % bad),
                                 "[collar] cap_pct", "0 < lo < hi <= 100")
        self.assert_rejected(in_section("collar", "cap_pct = [2, 10]", "cap_pct = [2.001, 10]"),
                             "[collar] cap_pct", "at most 2 decimal places")

    def test_max_net_debit_is_at_least_zero_in_cents(self) -> None:
        for good in ("0.00", "0", "5", "1.25"):
            self.assertEqual(Decimal(good), self.assert_loads(
                in_section("collar", "max_net_debit = 0.00", "max_net_debit = %s" % good)).collar.max_net_debit)
        for bad, needle in (("-0.01", ">= 0"), ("-5", ">= 0"), ("0.005", "at most 2 decimal places")):
            self.assert_rejected(in_section("collar", "max_net_debit = 0.00", "max_net_debit = %s" % bad),
                                 "[collar] max_net_debit", needle)


if __name__ == "__main__":
    unittest.main()
