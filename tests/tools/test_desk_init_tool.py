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
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Sequence, Tuple

import desk_init as tool  # the tool scripts/desk_init.py
from aegis_desk import alert_lint, calendar_events, desk_config

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ANSWERS = os.path.join(ROOT, "tests", "fixtures", "init", "answers.json")
EXPECTED = os.path.join(ROOT, "tests", "fixtures", "init", "expected_desk.toml")
FIXTURE_DESK_TOML = os.path.join(ROOT, "tests", "fixtures", "desk", "desk.toml")
EXPECTED_SHA = "f011fe058582203498b9082ec247cbfd388ac5b241d486ee26584c333eb91a94"
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
        self.assertEqual(["desk", "ex_dividends", "levels", "market_data", "owner", "rates", "schema_version"],
                         sorted(answers))
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
        self.assertEqual(962, len(written))
        self.assertEqual(EXPECTED_SHA, hashlib.sha256(written).hexdigest())
        self.assertEqual(["desk.toml"], self.files(), "desk_init writes desk.toml only: no state, no runtime directory")
        payload = body["payload"]
        self.assertEqual(list(tool.PAYLOAD_KEYS), sorted(payload, key=tool.PAYLOAD_KEYS.index))
        self.assertEqual(["desk.toml"], payload["written"])
        self.assertEqual({"path": "desk.toml", "sha256": EXPECTED_SHA, "bytes": 962,
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
        self.assertEqual(["wrote desk.toml (962 bytes, sha256 %s)" % EXPECTED_SHA, HINT], out.rstrip("\n").split("\n"))

    def test_optional_keys_token_file_and_penny_classes(self) -> None:
        answers = fixture_answers()
        answers["market_data"]["token_file"] = "/workspace/.aegis-secrets/tradier.token"
        answers["levels"] = {"penny_classes": ["SPY", "QQQ"], "penny_all_classes": ["SPXW"]}
        self.assertEqual(0, self.invoke(answers)[0])
        text = read_bytes(os.path.join(self.root, "desk.toml")).decode("utf-8")
        self.assertIn('token_env = "TRADIER_API_TOKEN"\ntoken_file = "/workspace/.aegis-secrets/tradier.token"\n'
                      'feed_delay_min = 15\n', text)
        self.assertTrue(text.endswith('[levels]\norder_shape_enabled = false\npenny_classes = ["SPY", "QQQ"]\n'
                                      'penny_all_classes = ["SPXW"]\n'), text)
        cfg = desk_config.load_desk_config(os.path.join(self.root, "desk.toml"))
        self.assertEqual((("SPY", "QQQ"), ("SPXW",)), (cfg.levels.penny_classes, cfg.levels.penny_all_classes))
        self.assertEqual("/workspace/.aegis-secrets/tradier.token", cfg.market_data.token_file)


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

    def test_production_is_refused(self) -> None:
        answers = fixture_answers()
        answers["market_data"]["host"] = "production"
        code, body = self.run_json(answers)
        self.assertEqual((1, "production_host"), (code, body["error"]["code"]))
        self.assertEqual([], self.files())

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
            changed(lambda a: a["market_data"].update(token_file="")),
            changed(lambda a: a["market_data"].update(vendor="tradier")),
            changed(lambda a: a["market_data"].update(host="local")),
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


if __name__ == "__main__":
    unittest.main()
