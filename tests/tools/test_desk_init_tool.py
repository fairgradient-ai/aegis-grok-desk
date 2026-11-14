"""``scripts/desk_init.py`` run in-process on temporary desk roots: ``desk.toml`` rendered byte for byte from the
owner's interview answers (PR 2d decision D2-13, override O9), validated by a round trip through ``desk_config``
before anything is written, an existing file refused without ``--replace --owner-tapped``, the ex-dividend intake
into ``research/calendar.toml``, the exit codes (0 written; 1 invalid answers, refusal or round-trip failure; 2 usage)
and the ``--schema`` answers description."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import copy
import dataclasses
import datetime
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

import desk_init as tool  # the tool scripts/desk_init.py
from aegis_desk import alert_lint, calendar_events, desk_config, structuring_config

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ANSWERS = os.path.join(ROOT, "tests", "fixtures", "init", "answers.json")
EXPECTED = os.path.join(ROOT, "tests", "fixtures", "init", "expected_desk.toml")
FIXTURE_DESK_TOML = os.path.join(ROOT, "tests", "fixtures", "desk", "desk.toml")
EXPECTED_SHA = "8245fc1428ddc9590d39ec32332eeff8dba0f6173142936a6fbae84cea89c18e"
STRUCTURING_ANSWERS = os.path.join(ROOT, "tests", "fixtures", "init", "structuring-answers.json")
EXPECTED_STRUCTURING = os.path.join(ROOT, "tests", "fixtures", "init", "expected_structuring.toml")
EXPECTED_STRUCTURING_SHA = "5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c"
EXPECTED_STRUCTURING_BYTES = 1090
NEW_OBJECTIVES_ANSWERS = os.path.join(ROOT, "tests", "fixtures", "init", "structuring-answers-new-objectives.json")
OVERLAY_SKILL = os.path.join(ROOT, "skills", "aegis-overlay-proposals", "SKILL.md")
STRUCTURING_DOC = os.path.join(ROOT, "docs", "STRUCTURING.md")
UPDATING_DOC = os.path.join(ROOT, "docs", "UPDATING.md")
INTERVIEW_AGENT = os.path.join(ROOT, "agents", "structuring-desk.md")
NEW_OBJECTIVES = {"covered_call": "band_centre", "roll": "net_extrinsic_per_added_day",
                  "protective_put": "band_centre", "collar": "band_centre"}
STRUCTURING_HINT = ("acknowledge with ack.py config --owner-tapped: the structuring tools show nothing until "
                    "structuring.toml is acknowledged")
S3_VALUES = {
    "disclosure_acknowledged": "structuring-suggestions-1",
    "general": {"max_spread_pct": "25", "max_candidates_shown": 3},
    "covered_call": {"enabled": True, "dte": [40, 75], "abs_delta": ["0.15", "0.35"], "min_credit": "0.50",
                     "strike_floor": "cost_basis", "earnings": "exclude", "ex_dividend": "exclude",
                     "objective": "extrinsic_per_day"},
    "roll": {"enabled": True, "dte": [40, 75], "abs_delta": ["0.15", "0.35"], "min_credit": "0.50",
             "credit_only": True, "strike_floor": "cost_basis", "earnings": "exclude", "ex_dividend": "exclude",
             "objective": "extrinsic_per_day"},
    "protective_put": {"enabled": True, "dte": [120, 210], "floor_pct": ["3", "10"], "stress_drop_pct": "20",
                       "objective": "stress_cost_per_day"},
    "collar": {"enabled": True, "dte": [120, 210], "floor_pct": ["3", "10"], "cap_pct": ["2", "10"],
               "max_net_debit": "0.00", "strike_floor": "cost_basis", "earnings": "exclude", "ex_dividend": "exclude",
               "objective": "cap_within_budget"},
}
"""PR 3b decision S3: the documented defaults the interview shows are exactly the fixture answers."""
NOW = "2026-11-04T15:20:00Z"
HINT = "acknowledge with ack.py config --owner-tapped once positions.csv and the adopted rulebook are in place"


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = tool.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


def order_shaped(text: str) -> List[str]:
    return [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(text)]


def fixture_answers() -> Dict[str, Any]:
    with open(ANSWERS, encoding="utf-8") as handle:
        return json.load(handle)


def structuring_answers() -> Dict[str, Any]:
    with open(STRUCTURING_ANSWERS, encoding="utf-8") as handle:
        return json.load(handle)


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


class TempRoot(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-desk-init-tool-")
        self.base = os.path.realpath(self._tmp.name)
        self.root = os.path.join(self.base, "desk")
        self.addCleanup(self._tmp.cleanup)

    def answers_file(self, document: Any, name: str = "answers.json") -> str:
        path = os.path.join(self.base, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(document if isinstance(document, str) else json.dumps(document))
        return path

    def invoke(self, answers: Any = None, *extra: str, json_mode: bool = True) -> Tuple[int, str]:
        path = ANSWERS if answers is None else self.answers_file(answers)
        argv = ["--answers", path, "--desk-root", self.root, "--now", NOW] + list(extra)
        code, out = run_tool(argv + (["--json"] if json_mode else []))
        self.assertEqual([], order_shaped(out))
        return code, out

    def run_json(self, answers: Any = None, *extra: str) -> Tuple[int, Dict[str, Any]]:
        code, out = self.invoke(answers, *extra)
        return code, json.loads(out)

    def files(self) -> List[str]:
        found: List[str] = []
        if not os.path.isdir(self.root):
            return found
        for folder, _dirs, names in os.walk(self.root):
            found.extend(os.path.relpath(os.path.join(folder, name), self.root) for name in names)
        return sorted(found)


class SchemaAndUsageTest(TempRoot):
    def test_schema_documents_the_answers(self) -> None:
        code, out = run_tool(["--schema"])
        self.assertEqual(0, code)
        body = json.loads(out)
        self.assertEqual(("desk_init", "none"), (body["tool"], body["network_access"]))
        self.assertEqual(["answers"], sorted(body["input"]))
        answers = body["input"]["answers"]
        self.assertEqual(["desk", "earnings", "ex_dividends", "levels", "market_data", "owner", "rates",
                          "schema_version", "structuring"], sorted(answers))
        shape = answers["structuring"]
        self.assertEqual(sorted(tool.STRUCTURING_KEYS), sorted(shape))  # --schema prints sorted keys
        self.assertEqual(("disclosure_acknowledged", "general", "covered_call", "roll", "protective_put", "collar"),
                         tool.STRUCTURING_KEYS)
        for section in structuring_config.SECTIONS[1:]:
            self.assertEqual(sorted(structuring_config.SECTION_KEYS[section] + structuring_config.V2_KEYS.get(section, ())),
                             sorted(shape[section]), section)
            self.assertEqual(list(structuring_config.SECTION_KEYS[section]) + list(structuring_config.V2_KEYS.get(section, ())),
                             list(tool.STRUCTURING_SHAPE[section]))
        self.assertEqual(set(tool.STRUCTURING_KINDS),
                         {key for keys in structuring_config.SECTION_KEYS.values() for key in keys}
                         | {key for keys in structuring_config.V2_KEYS.values() for key in keys})
        self.assertEqual(sorted(tool.PAYLOAD_KEYS), sorted(body["envelope"]["payload"]))
        self.assertEqual([], order_shaped(out))

    def test_usage_errors(self) -> None:
        for argv in ([], ["--answers", ANSWERS, "--replace"], ["--answers", ANSWERS, "--owner-tapped"],
                     ["--answers", ANSWERS, "--bogus"]):
            code, out = run_tool(argv + ["--desk-root", self.root, "--json"])
            self.assertEqual(2, code, argv)
            self.assertEqual("invalid_arguments", json.loads(out)["error"]["code"], argv)
        self.assertEqual([], self.files())


class RenderTest(TempRoot):
    def test_the_fixture_answers_render_the_expected_bytes_in_a_new_root(self) -> None:
        self.assertFalse(os.path.exists(self.root))
        code, body = self.run_json()
        self.assertEqual((0, "ok"), (code, body["status"]))
        written = read_bytes(os.path.join(self.root, "desk.toml"))
        self.assertEqual(read_bytes(EXPECTED), written)
        self.assertEqual(1037, len(written))
        self.assertEqual(EXPECTED_SHA, hashlib.sha256(written).hexdigest())
        self.assertEqual(["desk.toml"], self.files(), "desk_init writes desk.toml only: no state, no runtime directory")
        payload = body["payload"]
        self.assertEqual(list(tool.PAYLOAD_KEYS), sorted(payload, key=tool.PAYLOAD_KEYS.index))
        self.assertEqual(["desk.toml"], payload["written"])
        self.assertEqual({"path": "desk.toml", "sha256": EXPECTED_SHA, "bytes": 1037,
                          "text": written.decode("utf-8")}, payload["desk_toml"])
        self.assertIsNone(payload["calendar_toml"])
        self.assertEqual(HINT, payload["acknowledge"])
        self.assertEqual([], payload["replaced"])

    def test_the_rendered_file_loads_as_the_fixture_desk(self) -> None:
        self.assertEqual(0, self.invoke()[0])
        rendered = desk_config.load_desk_config(os.path.join(self.root, "desk.toml"))
        fixture = desk_config.load_desk_config(FIXTURE_DESK_TOML)
        self.assertEqual(dataclasses.replace(fixture, sha256="", path=""),
                         dataclasses.replace(rendered, sha256="", path=""))
        expected = desk_config.load_desk_config(EXPECTED)
        self.assertEqual(dataclasses.replace(fixture, sha256="", path=""),
                         dataclasses.replace(expected, sha256="", path=""))

    def test_the_answers_fixture_holds_the_fixture_desks_values(self) -> None:
        answers = fixture_answers()
        fixture = desk_config.load_desk_config(FIXTURE_DESK_TOML)
        self.assertEqual((fixture.desk.engagement, fixture.desk.cadence_profile, fixture.desk.chatty_or_quiet),
                         (answers["desk"]["engagement"], answers["desk"]["cadence_profile"],
                          answers["desk"]["chatty_or_quiet"]))
        self.assertEqual(str(fixture.owner.declared_equity), answers["owner"]["declared_equity"])
        self.assertEqual(str(fixture.rates.risk_free_rate), answers["rates"]["risk_free_rate"])
        self.assertEqual([(account.name, account.type) for account in fixture.owner.accounts],
                         [(account["name"], account["type"]) for account in answers["owner"]["accounts"]])

    def test_human_output_names_the_file_its_hash_and_the_acknowledgement(self) -> None:
        code, out = self.invoke(json_mode=False)
        self.assertEqual(0, code)
        self.assertEqual(["wrote desk.toml (1037 bytes, sha256 %s)" % EXPECTED_SHA, HINT], out.rstrip("\n").split("\n"))

    def test_optional_keys_credentials_file_and_penny_classes(self) -> None:
        answers = fixture_answers()
        answers["market_data"]["credentials_file"] = "/workspace/.aegis-secrets/alpaca.key"
        answers["levels"] = {"penny_classes": ["SPY", "QQQ"], "penny_all_classes": ["SPXW"]}
        self.assertEqual(0, self.invoke(answers)[0])
        text = read_bytes(os.path.join(self.root, "desk.toml")).decode("utf-8")
        self.assertIn('[market_data]\nvendor = "alpaca"\nhost = "paper"\nkey_env = "APCA_API_KEY_ID"\n'
                      'secret_env = "APCA_API_SECRET_KEY"\ncredentials_file = "/workspace/.aegis-secrets/alpaca.key"\n'
                      'options_feed = "indicative"\nstock_feed = "iex"\nfeed_delay_min = 15\n', text)
        self.assertTrue(text.endswith('[levels]\norder_shape_enabled = false\npenny_classes = ["SPY", "QQQ"]\n'
                                      'penny_all_classes = ["SPXW"]\n'), text)
        cfg = desk_config.load_desk_config(os.path.join(self.root, "desk.toml"))
        self.assertEqual((("SPY", "QQQ"), ("SPXW",)), (cfg.levels.penny_classes, cfg.levels.penny_all_classes))
        self.assertEqual("/workspace/.aegis-secrets/alpaca.key", cfg.market_data.credentials_file)
        self.assertEqual(("APCA_API_KEY_ID", "APCA_API_SECRET_KEY"), (cfg.market_data.key_env, cfg.market_data.secret_env))


class RefusalTest(TempRoot):
    def test_an_existing_desk_toml_needs_replace_and_owner_tapped(self) -> None:
        self.assertEqual(0, self.invoke()[0])
        before = read_bytes(os.path.join(self.root, "desk.toml"))
        answers = fixture_answers()
        answers["desk"]["cadence_profile"] = "active"
        code, body = self.run_json(answers)
        self.assertEqual((1, "desk_toml_exists"), (code, body["error"]["code"]))
        self.assertEqual(before, read_bytes(os.path.join(self.root, "desk.toml")))
        code, body = self.run_json(answers, "--replace", "--owner-tapped")
        self.assertEqual(0, code, body)
        self.assertEqual(["desk.toml"], body["payload"]["replaced"])
        self.assertEqual("active", desk_config.load_desk_config(os.path.join(self.root, "desk.toml")).desk.cadence_profile)

    def test_live_is_refused(self) -> None:
        answers = fixture_answers()
        answers["market_data"]["host"] = "live"
        code, body = self.run_json(answers)
        self.assertEqual((1, "live_host"), (code, body["error"]["code"]))
        self.assertIn("paper", body["error"]["message"])
        self.assertEqual([], self.files())

    def test_a_key_given_where_its_variable_name_belongs_is_refused_without_echo(self) -> None:
        # The answers carry the NAMES of the two environment variables; a pasted key ID or secret
        # (no underscore, not the upper-case variable shape) is refused and never printed back.
        for field, value in (("key_env", "PKFAKE0000AAAA1111ZZ"), ("secret_env", "fakeSecretValue0000aaaa1111bbbb2222cccc"),
                             ("key_env", "pk-not-a-name"), ("secret_env", "APCA API SECRET")):
            answers = fixture_answers()
            answers["market_data"][field] = value
            code, out = self.invoke(answers)
            body = json.loads(out)
            self.assertEqual((1, "answers_invalid"), (code, body["error"]["code"]), (field, value))
            self.assertIn("market_data.%s" % field, body["error"]["message"])
            self.assertNotIn(value, out, "a value given where a variable NAME belongs is never echoed")
        answers = fixture_answers()
        answers["market_data"]["secret_env"] = "APCA_API_KEY_ID"
        code, body = self.run_json(answers)
        self.assertEqual((1, "answers_invalid"), (code, body["error"]["code"]))
        self.assertEqual([], self.files())
        # One rule for both readers: desk.toml's own loader refuses the same pasted values.
        self.assertIs(desk_config.ENV_NAME_RE, tool.ENV_VAR_NAME_RE)

    def test_invalid_answers_write_nothing(self) -> None:
        def changed(edit: Any) -> Dict[str, Any]:
            answers = copy.deepcopy(fixture_answers())
            edit(answers)
            return answers

        cases = [
            "{not json",
            ["a", "list"],
            changed(lambda a: a.update(schema_version=2)),
            changed(lambda a: a.update(extra={})),
            changed(lambda a: a["desk"].update(cadence_profile="busy")),
            changed(lambda a: a["desk"].update(timezone="UTC")),
            changed(lambda a: a["owner"].update(approval_level=7)),
            changed(lambda a: a["owner"].update(approval_level=True)),
            changed(lambda a: a["owner"].update(declared_equity=250000.0)),
            changed(lambda a: a["owner"].update(declared_equity="2.5e5")),
            changed(lambda a: a["owner"].update(broker_exercise_cutoff_et="4:30pm")),
            changed(lambda a: a["owner"].update(accounts=[])),
            changed(lambda a: a["owner"]["accounts"][0].update(type="roth")),
            changed(lambda a: a["owner"]["attention_thresholds"].pop("pl_trade_abs")),
            changed(lambda a: a["owner"].update(disclosures_acknowledged=["", "x"])),
            changed(lambda a: a["rates"].update(as_of="2026-13-01")),
            changed(lambda a: a["market_data"].update(credentials_file="")),
            changed(lambda a: a["market_data"].update(vendor="alpaca")),
            changed(lambda a: a["market_data"].update(options_feed="opra")),
            changed(lambda a: a["market_data"].update(token_env="APCA_API_KEY_ID")),
            changed(lambda a: a["market_data"].pop("secret_env")),
            changed(lambda a: a["market_data"].update(host="local")),
            changed(lambda a: a["market_data"].update(host="sandbox")),
            changed(lambda a: a.pop("rates")),
            changed(lambda a: a.update(levels={"order_shape_enabled": True})),
            {"schema_version": 1},
        ]
        for answers in cases:
            code, body = self.run_json(answers)
            self.assertEqual((1, "answers_invalid"), (code, body["error"]["code"]), answers)
        self.assertEqual([], self.files())
        code, out = run_tool(["--answers", os.path.join(self.base, "missing.json"), "--desk-root", self.root, "--json"])
        self.assertEqual((1, "answers_invalid"), (code, json.loads(out)["error"]["code"]))

    def test_a_desk_root_inside_the_checkout_is_refused(self) -> None:
        for root in (ROOT, os.path.join(ROOT, "tests", "fixtures", "init", "desk")):
            code, out = run_tool(["--answers", ANSWERS, "--desk-root", root, "--now", NOW, "--json"])
            self.assertEqual((1, "desk_root"), (code, json.loads(out)["error"]["code"]), root)
        self.assertFalse(os.path.exists(os.path.join(ROOT, "desk.toml")))
        self.assertFalse(os.path.exists(os.path.join(ROOT, "tests", "fixtures", "init", "desk")))

    def test_a_round_trip_failure_writes_nothing(self) -> None:
        answers = fixture_answers()
        answers["owner"]["declared_equity"] = "0"
        code, body = self.run_json(answers)
        self.assertEqual((1, "config_invalid"), (code, body["error"]["code"]))
        self.assertIn("declared_equity", body["error"]["message"])
        self.assertEqual([], self.files())


class DividendIntakeTest(TempRoot):
    INTAKE = {"WMT": {"date": "2026-11-24", "amount": "0.2350", "status": "confirmed"},
              "BRK.B": {"status": "none", "source": "owner: no dividend (Berkshire)"},
              "NVDA": {"date": "2026-12-03", "amount": "0.01", "status": "estimated"}}

    def test_the_intake_alone_renders_the_calendar(self) -> None:
        os.makedirs(self.root)
        code, body = self.run_json({"schema_version": 1, "ex_dividends": self.INTAKE})
        self.assertEqual(0, code, body)
        self.assertEqual(["research/calendar.toml"], self.files(), "answers without desk render no desk.toml")
        text = read_bytes(os.path.join(self.root, "research", "calendar.toml")).decode("utf-8")
        self.assertIn('[symbols."BRK.B"]\nex_dividend = { status = "none", source = "owner: no dividend (Berkshire)", '
                      'checked_utc = 2026-11-04T15:20:00Z }\n', text)
        self.assertIn('[symbols.WMT]\nex_dividend = { date = 2026-11-24, amount = 0.2350, status = "confirmed", '
                      'source = "owner-entered", checked_utc = 2026-11-04T15:20:00Z }\n', text)
        self.assertLess(text.index("[symbols.NVDA]"), text.index("[symbols.WMT]"), "symbols in sorted order")
        cal = calendar_events.load_calendar(os.path.join(self.root, "research", "calendar.toml"))
        self.assertEqual(["BRK.B", "NVDA", "WMT"], sorted(cal.symbols))
        self.assertEqual("0.2350", str(cal.symbols["WMT"].ex_dividend.amount))
        payload = body["payload"]
        self.assertEqual((["research/calendar.toml"], None, None), (payload["written"], payload["desk_toml"],
                                                                     payload["acknowledge"]))
        self.assertEqual(["BRK.B", "NVDA", "WMT"], payload["calendar_toml"]["symbols"])

    def test_an_existing_calendar_needs_replace_and_owner_tapped_and_nothing_is_half_written(self) -> None:
        os.makedirs(os.path.join(self.root, "research"))
        shutil.copy(os.path.join(ROOT, "tests", "fixtures", "desk", "research", "calendar.toml"),
                    os.path.join(self.root, "research", "calendar.toml"))
        before = read_bytes(os.path.join(self.root, "research", "calendar.toml"))
        answers = dict(fixture_answers(), ex_dividends=self.INTAKE)
        code, body = self.run_json(answers)
        self.assertEqual((1, "calendar_exists"), (code, body["error"]["code"]))
        self.assertEqual(["research/calendar.toml"], self.files(), "desk.toml is not written when the calendar is refused")
        self.assertEqual(before, read_bytes(os.path.join(self.root, "research", "calendar.toml")))
        code, body = self.run_json(answers, "--replace", "--owner-tapped")
        self.assertEqual(0, code, body)
        self.assertEqual(["desk.toml", "research/calendar.toml"], body["payload"]["written"])
        self.assertEqual(["research/calendar.toml"], body["payload"]["replaced"])

    def test_invalid_intakes(self) -> None:
        os.makedirs(self.root)
        for intake in ({"WMT": {"date": "2026-11-24", "amount": "0.2350"}},
                       {"WMT": {"date": "2026-11-24", "amount": "0.2350", "status": "confirmed", "source": "x"}},
                       {"WMT": {"status": "none"}},
                       {"WMT": {"date": "2026-11-24", "amount": 0.235, "status": "confirmed"}},
                       {"wmt lower": {"status": "none", "source": "x"}},
                       {"WMT": {"status": "unknown", "source": "x"}},
                       []):
            code, body = self.run_json({"schema_version": 1, "ex_dividends": intake})
            self.assertEqual((1, "answers_invalid"), (code, body["error"]["code"]), intake)
        code, body = self.run_json({"schema_version": 1, "ex_dividends": {"WMT": {"date": "2026-11-24", "amount": "0",
                                                                                 "status": "confirmed"}}})
        self.assertEqual((1, "calendar_invalid"), (code, body["error"]["code"]), "amount > 0 is the calendar's rule")
        self.assertEqual([], self.files())


COLLAR_NOTES = [
    '[collar] dte starts at 120 calendar days with earnings = "exclude": every call that long spans a quarterly '
    'earnings date, so a stock that reports earnings never passes; earnings = "allow" accepts it in the structuring '
    'tools only (your exit alerts are unchanged)',
    '[collar] dte starts at 120 calendar days with ex_dividend = "exclude": every call that long spans a quarterly '
    'ex-dividend date, so a stock that pays a quarterly dividend never passes; ex_dividend = "allow" accepts it in the '
    'structuring tools only (your exit alerts are unchanged)',
]
"""The documented defaults' collar window (120 to 210 days) with both events excluded: criteria no candidate on a
reporting, dividend-paying stock can pass (the owner's NVDA collar, 2026-11-13), said before the owner relies on them."""

class EarningsIntakeTest(TempRoot):
    """The owner's earnings dates go into research/calendar.toml with the dividends, so a structuring answer reads a
    known date instead of counting an unknown one as inside every call's life (the owner's NVDA collar, 2026-11-13)."""

    EARNINGS = {"NVDA": {"date": "2026-11-17", "time": "post", "status": "confirmed"},
                "SPY": {"status": "none", "source": "owner: an ETF reports no earnings"}}

    def test_earnings_and_dividends_render_one_calendar(self) -> None:
        os.makedirs(self.root)
        code, body = self.run_json({"schema_version": 1, "ex_dividends": DividendIntakeTest.INTAKE,
                                    "earnings": self.EARNINGS})
        self.assertEqual(0, code, body)
        text = read_bytes(os.path.join(self.root, "research", "calendar.toml")).decode("utf-8")
        self.assertIn('[symbols.NVDA]\nearnings = { date = 2026-11-17, time = "post", status = "confirmed", '
                      'source = "owner-entered", checked_utc = 2026-11-04T15:20:00Z }\nex_dividend = { date = '
                      '2026-12-03, amount = 0.01, status = "estimated", source = "owner-entered", checked_utc = '
                      '2026-11-04T15:20:00Z }\n', text)
        self.assertIn('[symbols.SPY]\nearnings = { status = "none", source = "owner: an ETF reports no earnings", '
                      'checked_utc = 2026-11-04T15:20:00Z }\n', text)
        cal = calendar_events.load_calendar(os.path.join(self.root, "research", "calendar.toml"))
        self.assertEqual(["BRK.B", "NVDA", "SPY", "WMT"], sorted(cal.symbols))
        self.assertEqual(datetime.date(2026, 11, 17), cal.symbols["NVDA"].earnings.date)
        self.assertIsNone(cal.symbols["WMT"].earnings)
        self.assertEqual(["BRK.B", "NVDA", "SPY", "WMT"], body["payload"]["calendar_toml"]["symbols"])

    def test_earnings_alone_render_the_calendar(self) -> None:
        os.makedirs(self.root)
        code, body = self.run_json({"schema_version": 1, "earnings": self.EARNINGS})
        self.assertEqual(0, code, body)
        self.assertEqual(["research/calendar.toml"], body["payload"]["written"])

    def test_invalid_earnings(self) -> None:
        os.makedirs(self.root)
        for earnings in ({"NVDA": {"date": "2026-11-17", "status": "confirmed"}},
                         {"NVDA": {"date": "2026-11-17", "time": "noon", "status": "confirmed"}},
                         {"NVDA": {"status": "none"}},
                         {"NVDA": {"status": "unknown", "source": "x"}},
                         {"nvda": {"status": "none", "source": "x"}},
                         {}):
            code, body = self.run_json({"schema_version": 1, "earnings": earnings})
            self.assertEqual((1, "answers_invalid"), (code, body["error"]["code"]), earnings)
        self.assertEqual([], self.files())


class StructuringIntakeTest(TempRoot):
    """PR 3b decision S3 and designer C's D2: the structuring intake renders structuring.toml byte for byte, validated
    by structuring_config before anything is written."""

    def test_the_fixture_answers_hold_exactly_the_documented_defaults(self) -> None:
        self.assertEqual({"schema_version": 1, "structuring": S3_VALUES}, structuring_answers())

    def test_the_intake_alone_renders_the_expected_bytes(self) -> None:
        self.assertFalse(os.path.exists(self.root))
        code, body = self.run_json(structuring_answers())
        self.assertEqual((0, "ok"), (code, body["status"]), body)
        written = read_bytes(os.path.join(self.root, "structuring.toml"))
        self.assertEqual(read_bytes(EXPECTED_STRUCTURING), written)
        self.assertEqual(EXPECTED_STRUCTURING_BYTES, len(written))
        self.assertEqual(EXPECTED_STRUCTURING_SHA, hashlib.sha256(written).hexdigest())
        self.assertEqual(["structuring.toml"], self.files(), "the intake writes structuring.toml only")
        payload = body["payload"]
        self.assertEqual(list(tool.PAYLOAD_KEYS), sorted(payload, key=tool.PAYLOAD_KEYS.index))
        self.assertEqual((["structuring.toml"], [], None, None), (payload["written"], payload["replaced"],
                                                                 payload["desk_toml"], payload["calendar_toml"]))
        self.assertEqual({"path": "structuring.toml", "sha256": EXPECTED_STRUCTURING_SHA,
                          "bytes": EXPECTED_STRUCTURING_BYTES, "text": written.decode("utf-8")},
                         payload["structuring_toml"])
        self.assertEqual(STRUCTURING_HINT, payload["acknowledge"])
        self.assertEqual(STRUCTURING_HINT, tool.ACKNOWLEDGE_STRUCTURING)

    def test_criteria_that_can_pass_carry_no_note(self) -> None:
        os.makedirs(self.root)
        values = copy.deepcopy(S3_VALUES)
        values["collar"]["earnings"] = values["collar"]["ex_dividend"] = "allow"
        code, body = self.run_json({"schema_version": 1, "structuring": values})
        self.assertEqual(0, code, body)
        self.assertEqual([], body["payload"]["notes"])
        values = copy.deepcopy(S3_VALUES)
        code, body = self.run_json({"schema_version": 1, "structuring": values}, "--replace", "--owner-tapped")
        self.assertEqual(COLLAR_NOTES, body["payload"]["notes"])

    def test_the_rendering_and_its_header_lines(self) -> None:
        text = tool.render_structuring_toml(S3_VALUES)
        self.assertEqual(read_bytes(EXPECTED_STRUCTURING).decode("utf-8"), text)
        lines = text.split("\n")
        self.assertEqual(list(tool.STRUCTURING_HEADER), lines[:2])
        self.assertEqual(("# structuring.toml: rendered by scripts/desk_init.py from the owner's structuring interview; a "
                          "hand edit changes its",
                          "# hash, and the structuring tools show nothing until the owner acknowledges it (ack.py config "
                          "--owner-tapped)."), tool.STRUCTURING_HEADER)
        self.assertEqual("schema_version = 1", lines[2])
        headers = [line for line in lines if line.startswith("[")]
        self.assertEqual(["[%s]" % name for name in structuring_config.SECTIONS], headers)
        self.assertTrue(text.endswith('objective = "cap_within_budget"\n'))

    def test_the_rendered_file_loads_with_the_documented_values(self) -> None:
        self.assertEqual(0, self.invoke(structuring_answers())[0])
        cfg = structuring_config.load_structuring_config(os.path.join(self.root, "structuring.toml"))
        self.assertEqual(EXPECTED_STRUCTURING_SHA, cfg.sha256)
        self.assertEqual(structuring_config.DISCLOSURE_ID, cfg.disclosure.acknowledged)
        self.assertEqual(((40, 75), (120, 210), "0.00", 3),
                         (cfg.covered_call.dte, cfg.collar.dte, str(cfg.collar.max_net_debit),
                          cfg.general.max_candidates_shown))

    def test_human_output(self) -> None:
        code, out = self.invoke(structuring_answers(), json_mode=False)
        self.assertEqual(0, code)
        self.assertEqual(["wrote structuring.toml (%d bytes, sha256 %s)" % (EXPECTED_STRUCTURING_BYTES,
                                                                           EXPECTED_STRUCTURING_SHA), STRUCTURING_HINT]
                         + ["note: " + note for note in COLLAR_NOTES],
                         out.rstrip("\n").split("\n"))

    def test_with_the_desk_answers_the_desk_hint_is_used(self) -> None:
        answers = dict(fixture_answers(), structuring=S3_VALUES)
        code, body = self.run_json(answers)
        self.assertEqual(0, code, body)
        self.assertEqual(["desk.toml", "structuring.toml"], body["payload"]["written"])
        self.assertEqual(HINT, body["payload"]["acknowledge"])
        self.assertEqual(read_bytes(EXPECTED), read_bytes(os.path.join(self.root, "desk.toml")))
        self.assertEqual(read_bytes(EXPECTED_STRUCTURING), read_bytes(os.path.join(self.root, "structuring.toml")))

    def test_an_existing_file_needs_replace_and_owner_tapped(self) -> None:
        self.assertEqual(0, self.invoke(structuring_answers())[0])
        before = read_bytes(os.path.join(self.root, "structuring.toml"))
        answers = structuring_answers()
        answers["structuring"]["general"]["max_candidates_shown"] = 5
        code, body = self.run_json(answers)
        self.assertEqual((1, "structuring_exists"), (code, body["error"]["code"]))
        self.assertIn("--replace --owner-tapped", body["error"]["message"])
        self.assertEqual(before, read_bytes(os.path.join(self.root, "structuring.toml")))
        both = dict(fixture_answers(), structuring=answers["structuring"])
        code, body = self.run_json(both)
        self.assertEqual((1, "structuring_exists"), (code, body["error"]["code"]))
        self.assertEqual(["structuring.toml"], self.files(), "desk.toml is not written when structuring.toml is refused")
        code, body = self.run_json(answers, "--replace", "--owner-tapped")
        self.assertEqual(0, code, body)
        self.assertEqual((["structuring.toml"], ["structuring.toml"]),
                         (body["payload"]["written"], body["payload"]["replaced"]))
        cfg = structuring_config.load_structuring_config(os.path.join(self.root, "structuring.toml"))
        self.assertEqual(5, cfg.general.max_candidates_shown)

    def test_invalid_shapes_are_answers_invalid_and_write_nothing(self) -> None:
        def changed(edit: Any) -> Dict[str, Any]:
            answers = copy.deepcopy(structuring_answers())
            edit(answers["structuring"])
            return answers

        cases = [
            {"schema_version": 1, "structuring": []},
            {"schema_version": 1, "structuring": {}},
            changed(lambda s: s.pop("disclosure_acknowledged")),
            changed(lambda s: s.update(disclosure={"acknowledged": "structuring-suggestions-1"})),
            changed(lambda s: s.pop("roll")),
            changed(lambda s: s["covered_call"].pop("min_credit")),
            changed(lambda s: s["covered_call"].update(dte_unit="calendar")),
            changed(lambda s: s["general"].update(max_spread_pct=25)),
            changed(lambda s: s["general"].update(max_spread_pct="2.5e1")),
            changed(lambda s: s["general"].update(max_candidates_shown="3")),
            changed(lambda s: s["general"].update(max_candidates_shown=True)),
            changed(lambda s: s["covered_call"].update(dte=[40, 75, 90])),
            changed(lambda s: s["covered_call"].update(dte=["40", "75"])),
            changed(lambda s: s["covered_call"].update(dte=[40.0, 75])),
            changed(lambda s: s["roll"].update(abs_delta=[0.15, 0.35])),
            changed(lambda s: s["roll"].update(abs_delta="0.15-0.35")),
            changed(lambda s: s["roll"].update(credit_only="true")),
            changed(lambda s: s["collar"].update(enabled=1)),
            changed(lambda s: s["collar"].update(strike_floor=None)),
            changed(lambda s: s["protective_put"].update(objective=3)),
            changed(lambda s: s.update(disclosure_acknowledged=True)),
        ]
        for answers in cases:
            code, body = self.run_json(answers)
            self.assertEqual((1, "answers_invalid"), (code, body["error"]["code"]), answers)
        self.assertEqual([], self.files())

    def test_values_the_reader_refuses_are_structuring_invalid_and_write_nothing(self) -> None:
        def changed(edit: Any) -> Dict[str, Any]:
            answers = copy.deepcopy(structuring_answers())
            edit(answers["structuring"])
            return answers

        cases = [
            (changed(lambda s: s.update(disclosure_acknowledged="structuring-suggestions-2")),
             "the structuring disclosure structuring-suggestions-1 is not acknowledged in this file"),
            (changed(lambda s: s["covered_call"].update(dte=[5, 40])), "[covered_call] dte"),
            (changed(lambda s: s["roll"].update(abs_delta=["0.35", "0.15"])), "[roll] abs_delta"),
            (changed(lambda s: s["collar"].update(objective="extrinsic_per_day")), "[collar] objective"),
            (changed(lambda s: s["covered_call"].update(earnings="skip")), "[covered_call] earnings"),
            (changed(lambda s: s["protective_put"].update(stress_drop_pct="10")), "[protective_put] stress_drop_pct"),
            (changed(lambda s: s["general"].update(max_candidates_shown=11)), "[general] max_candidates_shown"),
            (changed(lambda s: s["collar"].update(max_net_debit="-0.05")), "[collar] max_net_debit"),
            (changed(lambda s: s["general"].update(max_spread_pct="012")), "invalid TOML"),
        ]
        for answers, needle in cases:
            code, body = self.run_json(answers)
            self.assertEqual((1, "structuring_invalid"), (code, body["error"]["code"]), answers)
            self.assertIn("the rendered structuring.toml does not load: ", body["error"]["message"])
            self.assertIn(needle, body["error"]["message"])
        both = dict(fixture_answers(), structuring=cases[0][0]["structuring"])
        code, body = self.run_json(both)
        self.assertEqual((1, "structuring_invalid"), (code, body["error"]["code"]))
        self.assertEqual([], self.files(), "nothing is written before everything is valid")

    def test_with_the_dividend_intake(self) -> None:
        os.makedirs(self.root)
        answers = {"schema_version": 1, "ex_dividends": DividendIntakeTest.INTAKE, "structuring": S3_VALUES}
        code, body = self.run_json(answers)
        self.assertEqual(0, code, body)
        self.assertEqual(["research/calendar.toml", "structuring.toml"], body["payload"]["written"])
        self.assertEqual(STRUCTURING_HINT, body["payload"]["acknowledge"])
        self.assertEqual(["research/calendar.toml", "structuring.toml"], self.files())


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class ObjectiveChoiceTest(TempRoot):
    """The interview and the update ask the owner which objective ranks each section; desk_init writes the choice."""

    def test_the_new_objectives_fixture_differs_from_the_defaults_only_in_the_objectives(self) -> None:
        with open(NEW_OBJECTIVES_ANSWERS, encoding="utf-8") as handle:
            chosen = json.load(handle)
        expected = copy.deepcopy(S3_VALUES)
        for section, objective in NEW_OBJECTIVES.items():
            expected[section]["objective"] = objective
        self.assertEqual({"schema_version": 1, "structuring": expected}, chosen)

    def test_a_first_setup_writes_the_chosen_objectives(self) -> None:
        with open(NEW_OBJECTIVES_ANSWERS, encoding="utf-8") as handle:
            chosen = json.load(handle)
        code, body = self.run_json(chosen)
        self.assertEqual((0, "ok"), (code, body["status"]), body)
        cfg = structuring_config.load_structuring_config(os.path.join(self.root, "structuring.toml"))
        self.assertEqual(NEW_OBJECTIVES, {name: getattr(cfg, name).objective for name in NEW_OBJECTIVES})
        self.assertNotEqual(EXPECTED_STRUCTURING_SHA, cfg.sha256)

    def test_an_update_switch_rewrites_only_the_objective_lines(self) -> None:
        self.assertEqual(0, self.invoke(structuring_answers())[0])
        before = read_bytes(os.path.join(self.root, "structuring.toml")).decode("utf-8").split("\n")
        with open(NEW_OBJECTIVES_ANSWERS, encoding="utf-8") as handle:
            chosen = json.load(handle)
        code, body = self.run_json(chosen)
        self.assertEqual((1, "structuring_exists"), (code, body["error"]["code"]), "keeping is the default")
        code, body = self.run_json(chosen, "--replace", "--owner-tapped")
        self.assertEqual(0, code, body)
        after = read_bytes(os.path.join(self.root, "structuring.toml")).decode("utf-8").split("\n")
        changed = [(old, new) for old, new in zip(before, after) if old != new]
        self.assertEqual(len(before), len(after))
        self.assertEqual([('objective = "%s"' % structuring_config.OBJECTIVES[name][0], 'objective = "%s"' % chosen_id)
                          for name, chosen_id in NEW_OBJECTIVES.items()], changed)

    def test_a_roll_cannot_choose_band_centre(self) -> None:
        answers = structuring_answers()
        answers["structuring"]["roll"]["objective"] = "band_centre"
        code, body = self.run_json(answers)
        self.assertEqual((1, "structuring_invalid"), (code, body["error"]["code"]))
        self.assertIn("[roll] objective", body["error"]["message"])
        self.assertEqual([], self.files())

    def test_the_schema_lists_every_accepted_objective_default_first(self) -> None:
        for section, ids in structuring_config.OBJECTIVES.items():
            self.assertEqual(" | ".join(ids), tool.STRUCTURING_SHAPE[section]["objective"], section)

    def test_every_accepted_objective_is_documented_where_the_interview_reads_it(self) -> None:
        skill, doc = read_text(OVERLAY_SKILL), read_text(STRUCTURING_DOC)
        for ids in structuring_config.OBJECTIVES.values():
            for objective in ids:
                self.assertIn("| `%s`" % objective, skill, objective)
                self.assertIn("| `%s` |" % objective, doc, objective)
        agent = read_text(INTERVIEW_AGENT)
        self.assertIn("ask which objective ranks its candidates", agent)
        self.assertIn("with no choice, the default stays", agent)
        self.assertIn("Where they choose `band_centre`, ask whether they want their own centres", agent)
        updating = read_text(UPDATING_DOC)
        self.assertIn("When the changelog names a new objective for `structuring.toml`", updating)
        self.assertIn("Keeping\nis the default and changes nothing.", updating)



class SchemaTwoAnswersTest(TempRoot):
    """Step 3b: desk_init writes schema_version = 2, with the owner's centres and weights, only when an answer uses a
    version-2 key; every other file stays byte for byte as before."""

    def answers(self, **covered_call: Any) -> Dict[str, Any]:
        values = copy.deepcopy(S3_VALUES)
        values["covered_call"]["objective"] = "band_centre"
        values["covered_call"].update(covered_call)
        return {"schema_version": 1, "structuring": values}

    def test_without_version_two_keys_the_file_stays_version_one(self) -> None:
        text = tool.render_structuring_toml(self.answers()["structuring"])
        self.assertIn("\nschema_version = 1\n", text)
        self.assertNotIn("band_weights", text)

    def test_centres_and_weights_are_written_after_the_objective(self) -> None:
        code, body = self.run_json(self.answers(dte_centre="50", abs_delta_centre="0.30",
                                                band_weights={"quote": "2", "dte": "0.5"}))
        self.assertEqual((0, "ok"), (code, body["status"]), body)
        text = read_bytes(os.path.join(self.root, "structuring.toml")).decode("utf-8")
        self.assertIn("\nschema_version = 2\n", text)
        block = text[text.index("[covered_call]"):text.index("[roll]")]
        self.assertTrue(block.rstrip("\n").endswith('objective = "band_centre"\ndte_centre = 50\n'
                                                     'abs_delta_centre = 0.30\nband_weights = { dte = 0.5, quote = 2 }'),
                        block)
        cfg = structuring_config.load_structuring_config(os.path.join(self.root, "structuring.toml"))
        self.assertEqual((2, Decimal("50"), Decimal("0.5"), Decimal("2")),
                         (cfg.schema_version, cfg.covered_call.band_centre_of("dte"),
                          cfg.covered_call.band_weight("dte"), cfg.covered_call.band_weight("quote")))

    def test_shapes_desk_init_refuses(self) -> None:
        for change in ({"band_weights": ["0.5"]}, {"band_weights": {"dte": 0.5}}, {"band_weights": {"d te": "1"}},
                       {"dte_centre": 50}, {"roll_centre": "1"}):
            with self.subTest(change=change):
                code, body = self.run_json(self.answers(**change))
                self.assertEqual((1, "answers_invalid"), (code, body["error"]["code"]), body)
        self.assertEqual([], self.files())

    def test_values_the_reader_refuses(self) -> None:
        for change, needle in (({"dte_centre": "80"}, "[covered_call] dte_centre must lie inside dte"),
                               ({"band_weights": {"dte": "0"}}, "band_weights.dte must be above 0"),
                               ({"band_weights": {"gamma": "1"}}, "unknown band 'gamma'")):
            with self.subTest(change=change):
                code, body = self.run_json(self.answers(**change))
                self.assertEqual((1, "structuring_invalid"), (code, body["error"]["code"]), body)
                self.assertIn(needle, body["error"]["message"])
        answers = self.answers(dte_centre="50")
        answers["structuring"]["covered_call"]["objective"] = "extrinsic_per_day"
        code, body = self.run_json(answers)
        self.assertEqual((1, "structuring_invalid"), (code, body["error"]["code"]))
        self.assertIn('applies only with objective = "band_centre"', body["error"]["message"])


    def test_roll_test_replaces_credit_only_and_writes_version_two(self) -> None:
        values = copy.deepcopy(S3_VALUES)
        values["roll"].pop("credit_only")
        values["roll"]["roll_test"] = "net_time_value"
        code, body = self.run_json({"schema_version": 1, "structuring": values})
        self.assertEqual((0, "ok"), (code, body["status"]), body)
        text = read_bytes(os.path.join(self.root, "structuring.toml")).decode("utf-8")
        block = text[text.index("[roll]"):text.index("[protective_put]")]
        self.assertIn("\nschema_version = 2\n", text)
        self.assertNotIn("credit_only", block)
        self.assertTrue(block.rstrip("\n").endswith('objective = "extrinsic_per_day"\nroll_test = "net_time_value"'),
                        block)
        cfg = structuring_config.load_structuring_config(os.path.join(self.root, "structuring.toml"))
        self.assertEqual("net_time_value", cfg.roll.roll_rule())

    def test_roll_test_and_credit_only_together_are_refused(self) -> None:
        values = copy.deepcopy(S3_VALUES)
        values["roll"]["roll_test"] = "credit"
        code, body = self.run_json({"schema_version": 1, "structuring": values})
        self.assertEqual((1, "structuring_invalid"), (code, body["error"]["code"]))
        self.assertIn("[roll] give roll_test or credit_only, not both", body["error"]["message"])


if __name__ == "__main__":
    unittest.main()
