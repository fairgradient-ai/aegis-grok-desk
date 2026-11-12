"""``scripts/ack.py`` run in-process against a copied fixture desk root: ``list`` (open signals with their ladder
position and ack state), ``show``, ``ack``, ``snooze --until``, ``acted`` (the ``rows_sha`` that ``csv_recon``'s
``ACTED_UNCHANGED`` check reads) and ``config`` (the acknowledged hashes ``book_check`` compares); the
``--owner-tapped`` requirement of ``snooze``, ``acted`` and ``config``; the exact ``state/acks.jsonl`` record; the
usage (2) and error (1) paths; nothing is written by ``list`` and ``show``."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import datetime
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Sequence, Tuple

from aegis_desk import alert_lint, desk_paths, recon, signals, state

import ack as tool  # the tool scripts/ack.py
import csv_recon as recon_tool

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
RECON_DESK = os.path.join(ROOT, "tests", "fixtures", "desk_recon")
NOW = "2026-11-20T19:55:00Z"
POSTED = datetime.datetime(2026, 11, 20, 19, 50, tzinfo=datetime.timezone.utc)
RECON_NOW = "2026-11-24T12:00:00Z"
WMT_TRADE = "AG-20261020-01"
NVDA_TRADE = "AG-20261022-02"
ACK_KEYS = ("utc", "signal_id", "trade_id", "action", "until", "rows_sha", "hashes")


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = tool.main(list(argv))
    return code, buffer.getvalue()


def sha256_file(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def signal(trade_id: str, structure: str, verdict: str, plan: Optional[str],
           posted_at: datetime.datetime = POSTED) -> Dict[str, Any]:
    return {"signal_id": None, "trade_id": trade_id, "structure": structure, "verdict": verdict, "reasons": [],
            "plan": plan, "data_quality": "ok", "quote_age_at_post": 18, "run_lateness_min": None,
            "posted_at": posted_at}


class DeskCopy(unittest.TestCase):
    source = FIXTURE_DESK

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-ack-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(self.source, self.root)
        self.paths = desk_paths.for_root(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, *words: str, now: str = NOW, json_mode: bool = True) -> List[str]:
        argv = list(words) + ["--offline", "--desk-root", self.root, "--now", now]
        if json_mode:
            argv.append("--json")
        return argv

    def screen(self, out: str) -> None:
        """No order-shaped text: the alert lint's own forbidden-pattern table (cli_journeys screens with it too)."""
        self.assertEqual([], [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(out)], out)

    def run_json(self, *words: str, now: str = NOW) -> Tuple[int, Dict[str, Any]]:
        code, out = run_tool(self.argv(*words, now=now))
        self.screen(out)
        return code, json.loads(out)

    def run_text(self, *words: str, now: str = NOW) -> Tuple[int, str]:
        code, out = run_tool(self.argv(*words, now=now, json_mode=False))
        self.screen(out)
        return code, out

    def post_signals(self) -> Dict[str, str]:
        """Two EXIT_SIGNALs and a portfolio WATCH, each posted once (as book_check --post records them)."""
        desk_paths.ensure_runtime_dirs(self.paths)
        ids = {}
        for trade, structure, verdict, plan in ((WMT_TRADE, "covered_call", "EXIT_SIGNAL", "close"),
                                                (NVDA_TRADE, "covered_call", "EXIT_SIGNAL", "roll_review"),
                                                ("portfolio", "portfolio", "WATCH", "hold_review")):
            stored = signals.append_signal(self.paths, signal(trade, structure, verdict, plan))
            signals.append_alert(self.paths, stored["signal_id"], "EXIT SIGNAL %s text" % trade, True, posted_at=POSTED)
            ids[trade] = stored["signal_id"]
        return ids

    def acks(self) -> List[Dict[str, Any]]:
        records, status = state.read_jsonl(self.paths.acks_jsonl)
        self.assertIn(status, ("ok", "missing"))
        return records

    def desk_state(self) -> Any:
        document, status = state.read_json(self.paths.desk_state_json)
        return document if status == "ok" else status

    def rewrite(self, name: str, old: str, new: str) -> None:
        path = os.path.join(self.root, name)
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
        self.assertEqual("ack", body["tool"])
        payload = body["envelope"]["payload"]
        for key in ("action", "count", "signals", "record", "acknowledged_hashes"):
            self.assertIn(key, payload)
        self.assertEqual(set(ACK_KEYS), set(payload["record"]))
        self.assertEqual("none", body["network_access"])

    def test_usage_errors_are_exit_2(self) -> None:
        cases = (
            ("bogus",),                                            # unknown action
            ("list", "SIG-20261120-001"),                          # list takes no signal id
            ("config", "SIG-20261120-001", "--owner-tapped"),      # nor does config
            ("ack",),                                              # ack needs a signal id
            ("show", "SIG-1"),                                     # not SIG-YYYYMMDD-NNN
            ("ack", "SIG-20261120-001", "--until", "20:30 ET"),     # --until is snooze's
            ("snooze", "SIG-20261120-001", "--owner-tapped"),      # snooze needs --until
            ("snooze", "SIG-20261120-001", "--until", "20:30 ET"),  # snooze needs --owner-tapped
            ("snooze", "SIG-20261120-001", "--owner-tapped", "--until", "tomorrow"),
            ("snooze", "SIG-20261120-001", "--owner-tapped", "--until", "14:00 ET"),             # already passed
            ("snooze", "SIG-20261120-001", "--owner-tapped", "--until", "2026-11-20T19:00:00Z"),  # already passed
            ("acted", "SIG-20261120-001"),                         # acted needs --owner-tapped
            ("config",),                                           # config needs --owner-tapped
        )
        for words in cases:
            with self.subTest(words=words):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(self.argv(*words))
                self.assertEqual(2, ctx.exception.code)
        self.assertFalse(os.path.exists(self.paths.acks_jsonl))

    def test_owner_tap_message_names_the_card(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(SystemExit):
                tool.main(self.argv("config"))
        body = json.loads(buffer.getvalue())
        self.assertEqual(("usage", "invalid_arguments"), (body["status"], body["error"]["code"]))
        self.assertIn("--owner-tapped", body["error"]["message"])
        self.assertIn("Ask-first", body["error"]["message"])

    def test_missing_desk_root_is_usage(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            run_tool(["list", "--offline", "--desk-root", os.path.join(self.root, "absent"), "--json"])
        self.assertEqual(2, ctx.exception.code)


class ListTest(DeskCopy):
    def test_fresh_copy_lists_nothing_and_writes_nothing(self) -> None:
        code, body = self.run_json("list")
        self.assertEqual(0, code)
        self.assertEqual(("ok", "ack", "list", 0, []),
                         (body["status"], body["tool"], body["payload"]["action"], body["payload"]["count"],
                          body["payload"]["signals"]))
        code, text = self.run_text("list")
        self.assertEqual((0, "0 open signals\n"), (code, text))
        self.assertFalse(os.path.exists(os.path.join(self.root, "state")), "list never creates the runtime directories")
        self.assertFalse(os.path.exists(os.path.join(self.root, "journal")))

    def test_open_signals_with_ladder_and_ack_state(self) -> None:
        ids = self.post_signals()
        code, body = self.run_json("list")
        self.assertEqual(0, code)
        payload = body["payload"]
        self.assertEqual(3, payload["count"])
        entries = {entry["trade_id"]: entry for entry in payload["signals"]}
        wmt = entries[WMT_TRADE]
        self.assertEqual((ids[WMT_TRADE], "EXIT_SIGNAL", "close", 1, "open", None),
                         (wmt["signal_id"], wmt["verdict"], wmt["plan"], wmt["posts"], wmt["ack_state"],
                          wmt["snoozed_until"]))
        self.assertEqual({"applies": True, "posts": 1, "of": 4, "next_repost_after_runs": 1, "done": False,
                          "not_before": None, "rule_id": None, "cooldown_min": None}, wmt["ladder"],
                         "a signal without a primary rule names no cooldown instant")
        self.assertEqual("2026-11-20T19:50:00+00:00", wmt["posted_at"])
        self.assertEqual({"applies": False, "posts": 1, "of": None, "next_repost_after_runs": None, "done": False,
                          "not_before": None, "rule_id": None, "cooldown_min": None},
                         entries["portfolio"]["ladder"], "WATCH signals have no re-post ladder")
        code, text = self.run_text("list")
        self.assertIn("%s | %s covered_call | EXIT_SIGNAL, your plan: close | first posted 2026-11-20 19:50 UTC | "
                      "posted 1 of 4 times; next re-post 1 run after the first post | open" % (ids[WMT_TRADE], WMT_TRADE),
                      text)
        self.assertIn("no re-post ladder for WATCH", text)
        unavailable = signals.OpenSignal("SIG-20261120-009", "AG-20261020-01", "covered_call", "UNAVAILABLE", None, POSTED, 2,
                                         POSTED, "open", None, {})
        self.assertIn("posted 2 times; re-posted on every run until acknowledged", tool.signal_line(unavailable))
        self.assertIn("%s covered_call | EXIT_SIGNAL, your plan: review a roll" % NVDA_TRADE, text)
        self.assertIn("%s | portfolio | WATCH, your plan: review and decide" % ids["portfolio"], text)
        self.assertTrue(text.endswith("3 open signals\n"), text)

    def test_ladder_done_after_four_posts(self) -> None:
        ids = self.post_signals()
        for minute in (51, 52, 53):
            signals.append_alert(self.paths, ids[WMT_TRADE], "again", True,
                                 posted_at=POSTED.replace(minute=minute))
        code, body = self.run_json("list")
        wmt = [entry for entry in body["payload"]["signals"] if entry["trade_id"] == WMT_TRADE][0]
        self.assertEqual({"applies": True, "posts": 4, "of": 4, "next_repost_after_runs": None, "done": True,
                          "not_before": None, "rule_id": None, "cooldown_min": None}, wmt["ladder"])
        code, text = self.run_text("list")
        self.assertIn("posted 4 of 4 times; the re-post ladder is done", text)

    def test_a_bad_journal_line_is_skipped_reported_and_never_moved(self) -> None:
        ids = self.post_signals()
        with open(signals.signals_path(self.paths), "a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        code, body = self.run_json("list")
        self.assertEqual((0, 3), (code, body["payload"]["count"]), "the open signals survive one bad line")
        self.assertIn("journal/signals.jsonl has lines that do not parse", " ".join(body["payload"]["warnings"]))
        self.assertTrue(os.path.exists(signals.signals_path(self.paths)), "a read never moves the journal aside")
        self.assertEqual([], [name for name in os.listdir(self.paths.journal_dir) if ".corrupt-" in name])
        code, body = self.run_json("show", ids[WMT_TRADE])
        self.assertEqual(0, code)


class ShowTest(DeskCopy):
    def test_show_a_signal_with_its_posts(self) -> None:
        ids = self.post_signals()
        code, body = self.run_json("show", ids[WMT_TRADE])
        self.assertEqual(0, code)
        payload = body["payload"]
        self.assertEqual(("show", ids[WMT_TRADE], "open", 1), (payload["action"], payload["signal"]["signal_id"],
                                                               payload["state"], payload["posts"]))
        self.assertEqual(["EXIT SIGNAL %s text" % WMT_TRADE], [alert["text"] for alert in payload["alerts"]])
        self.assertEqual([], payload["acks"])
        code, text = self.run_text("show", ids[WMT_TRADE])
        self.assertEqual(0, code)
        self.assertIn("EXIT SIGNAL %s text" % WMT_TRADE, text)

    def test_unknown_signal_is_exit_1(self) -> None:
        self.post_signals()
        code, body = self.run_json("show", "SIG-20261120-099")
        self.assertEqual((1, "error", "unknown_signal"), (code, body["status"], body["error"]["code"]))
        code, text = self.run_text("show", "SIG-20261120-099")
        self.assertTrue(text.startswith("error: "), text)

    def test_superseded_and_closed_states(self) -> None:
        ids = self.post_signals()
        later = signals.append_signal(self.paths, signal(WMT_TRADE, "covered_call", "UNAVAILABLE", None,
                                                         POSTED.replace(minute=59)))
        code, body = self.run_json("show", ids[WMT_TRADE])
        self.assertEqual(("superseded", later["signal_id"]), (body["payload"]["state"], body["payload"]["superseded_by"]))
        self.assertEqual(0, self.run_json("ack", ids[NVDA_TRADE])[0])
        code, body = self.run_json("show", ids[NVDA_TRADE])
        self.assertEqual("acknowledged", body["payload"]["state"])
        self.assertEqual(["ack"], [record["action"] for record in body["payload"]["acks"]])


class AckTest(DeskCopy):
    def test_ack_appends_the_exact_record_and_closes_the_signal(self) -> None:
        ids = self.post_signals()
        code, body = self.run_json("ack", ids[WMT_TRADE])
        self.assertEqual(0, code)
        records = self.acks()
        self.assertEqual(1, len(records))
        self.assertEqual(list(ACK_KEYS), sorted(records[0], key=ACK_KEYS.index))
        self.assertEqual({"utc": "2026-11-20T19:55:00+00:00", "signal_id": ids[WMT_TRADE], "trade_id": WMT_TRADE,
                          "action": "ack", "until": None, "rows_sha": None, "hashes": None}, records[0])
        self.assertEqual(("ack", "open", "acknowledged"), (body["payload"]["action"], body["payload"]["state_before"],
                                                           body["payload"]["state_after"]))
        code, body = self.run_json("list")
        self.assertEqual([NVDA_TRADE, "portfolio"], [entry["trade_id"] for entry in body["payload"]["signals"]])
        code, text = self.run_text("ack", ids[NVDA_TRADE])
        self.assertEqual(0, code)
        self.assertIn("acknowledged", text)
        self.assertNotIn("dry run", text)

    def test_ack_of_an_unknown_signal_writes_nothing(self) -> None:
        self.post_signals()
        code, body = self.run_json("ack", "SIG-20261120-077")
        self.assertEqual((1, "unknown_signal"), (code, body["error"]["code"]))
        self.assertEqual([], self.acks())


class SnoozeTest(DeskCopy):
    def test_snooze_until_an_et_clock_time(self) -> None:
        ids = self.post_signals()
        code, body = self.run_json("snooze", ids[WMT_TRADE], "--until", "15:30 ET", "--owner-tapped")
        self.assertEqual(0, code, body)
        record = self.acks()[0]
        self.assertEqual(("snooze", "2026-11-20T20:30:00+00:00", WMT_TRADE), (record["action"], record["until"],
                                                                              record["trade_id"]))
        code, body = self.run_json("list")
        wmt = [entry for entry in body["payload"]["signals"] if entry["trade_id"] == WMT_TRADE][0]
        self.assertEqual(("snoozed", "2026-11-20T20:30:00+00:00"), (wmt["ack_state"], wmt["snoozed_until"]))
        code, text = self.run_text("list")
        self.assertIn("snoozed until 2026-11-20 20:30 UTC", text)

    def test_snooze_until_an_iso_instant(self) -> None:
        ids = self.post_signals()
        code, _body = self.run_json("snooze", ids[NVDA_TRADE], "--until", "2026-11-23T15:00:00Z", "--owner-tapped")
        self.assertEqual(0, code)
        self.assertEqual("2026-11-23T15:00:00+00:00", self.acks()[0]["until"])

    def test_snooze_of_an_acknowledged_signal_is_refused(self) -> None:
        ids = self.post_signals()
        self.assertEqual(0, self.run_json("ack", ids[WMT_TRADE])[0])
        code, body = self.run_json("snooze", ids[WMT_TRADE], "--until", "15:30 ET", "--owner-tapped")
        self.assertEqual((1, "signal_not_open"), (code, body["error"]["code"]))
        self.assertEqual(["ack"], [record["action"] for record in self.acks()])


class ActedTest(DeskCopy):
    def test_acted_records_the_group_rows_sha(self) -> None:
        ids = self.post_signals()
        code, body = self.run_json("acted", ids[WMT_TRADE], "--owner-tapped")
        self.assertEqual(0, code, body)
        with open(self.paths.positions_csv, "rb") as handle:
            text = handle.read().decode("utf-8")
        expected = recon.group_rows_sha(recon.csv_table(text), WMT_TRADE)
        self.assertIsNotNone(expected)
        record = self.acks()[0]
        self.assertEqual(("acted", WMT_TRADE, expected), (record["action"], record["trade_id"], record["rows_sha"]))
        self.assertEqual("acted", self.run_json("show", ids[WMT_TRADE])[1]["payload"]["state"])
        # recon reads it: unchanged rows a trading day later are the ACTED_UNCHANGED nag ...
        records, status = state.read_jsonl(self.paths.acks_jsonl)
        findings = recon.ack_findings(records, status, table=recon.csv_table(text), et_date=datetime.date(2026, 11, 23),
                                      group_ids=[WMT_TRADE, NVDA_TRADE])
        self.assertEqual([("ACTED_UNCHANGED", WMT_TRADE)], [(item.kind, item.group_id) for item in findings])
        # ... and an edited row silences it.
        self.rewrite("positions.csv", "covered call #assign-ok", "covered call #assign-ok closed at the broker")
        with open(self.paths.positions_csv, "rb") as handle:
            edited = handle.read().decode("utf-8")
        findings = recon.ack_findings(records, status, table=recon.csv_table(edited), et_date=datetime.date(2026, 11, 23),
                                      group_ids=[WMT_TRADE, NVDA_TRADE])
        self.assertEqual([], findings)

    def test_acted_on_a_portfolio_signal_has_no_rows(self) -> None:
        ids = self.post_signals()
        code, body = self.run_json("acted", ids["portfolio"], "--owner-tapped")
        self.assertEqual(0, code)
        self.assertEqual((None, "portfolio"), (self.acks()[0]["rows_sha"], self.acks()[0]["trade_id"]))


class ActedReconJourneyTest(DeskCopy):
    source = RECON_DESK

    def test_csv_recon_reads_the_acted_record(self) -> None:
        desk_paths.ensure_runtime_dirs(self.paths)
        stored = signals.append_signal(self.paths, signal("AG-20261110-08", "protective_put", "WATCH", "roll_review",
                                                          datetime.datetime(2026, 11, 20, 19, 0, tzinfo=datetime.timezone.utc)))
        code, _body = self.run_json("acted", stored["signal_id"], "--owner-tapped", now="2026-11-20T19:05:00Z")
        self.assertEqual(0, code)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = recon_tool.main(["--offline", "--desk-root", self.root, "--now", RECON_NOW, "--fixture-set", "recon",
                                    "--json"])
        body = json.loads(buffer.getvalue())
        nags = [item for item in body["payload"]["findings"] if item["kind"] == "ACTED_UNCHANGED"]
        self.assertEqual([("warning", "AG-20261110-08")], [(item["level"], item["group_id"]) for item in nags])


class ConfigTest(DeskCopy):
    def expected_hashes(self) -> Dict[str, Optional[str]]:
        # PR 3b (designer C's D3): structuring_toml is the last acknowledged name, null while the file is missing.
        return {"desk_toml": sha256_file(self.paths.desk_toml), "positions_csv": sha256_file(self.paths.positions_csv),
                "rulebook": sha256_file(os.path.join(self.root, "rules", "exit_rules-1.0.0.toml")),
                "structuring_toml": None}

    def test_config_writes_the_acknowledged_hashes(self) -> None:
        code, body = self.run_json("config", "--owner-tapped")
        self.assertEqual(0, code, body)
        expected = self.expected_hashes()
        self.assertEqual("cc09859c896e22f84402ce8b686d0f79ec920b503b647754232247668b8bc5fe", expected["rulebook"],
                         "the ADOPTED sha of the fixture rulebook")
        document = self.desk_state()
        self.assertEqual(expected, document["acknowledged_hashes"])
        self.assertEqual("2026-11-20T19:55:00+00:00", document["acknowledged_utc"])
        self.assertEqual(expected, body["payload"]["acknowledged_hashes"])
        record = self.acks()[0]
        self.assertEqual({"utc": "2026-11-20T19:55:00+00:00", "signal_id": None, "trade_id": None, "action": "config",
                          "until": None, "rows_sha": None, "hashes": expected}, record)
        self.assertEqual(["desk_toml", "positions_csv", "rulebook", "structuring_toml"], list(expected))
        code, text = self.run_text("config", "--owner-tapped")
        self.assertIn("desk.toml sha %s" % expected["desk_toml"][:8], text)
        self.assertIn("adopted rulebook 1.0.0 sha cc09859c", text)
        self.assertNotIn("structuring", text, "without structuring.toml the line is the one SETUP and the journeys show")

    def test_config_keeps_the_other_desk_state_keys(self) -> None:
        desk_paths.ensure_runtime_dirs(self.paths)
        state.atomic_write_json(self.paths.desk_state_json, {"verdicts": {WMT_TRADE: "WATCH"},
                                                             "acknowledged_hashes": {"desk_toml": "0" * 64}})
        self.assertEqual(0, self.run_json("config", "--owner-tapped")[0])
        document = self.desk_state()
        self.assertEqual({WMT_TRADE: "WATCH"}, document["verdicts"])
        self.assertEqual(self.expected_hashes(), document["acknowledged_hashes"])

    def test_corrupt_desk_state_is_replaced_with_a_warning(self) -> None:
        desk_paths.ensure_runtime_dirs(self.paths)
        with open(self.paths.desk_state_json, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        code, body = self.run_json("config", "--owner-tapped")
        self.assertEqual(0, code)
        self.assertIn("state/desk_state.json was corrupt", " ".join(body["payload"]["warnings"]))
        self.assertEqual(self.expected_hashes(), self.desk_state()["acknowledged_hashes"])

    def test_no_adopted_rulebook_acknowledges_none(self) -> None:
        os.remove(self.paths.adopted_file)
        code, body = self.run_json("config", "--owner-tapped")
        self.assertEqual(0, code)
        self.assertIsNone(self.desk_state()["acknowledged_hashes"]["rulebook"])
        code, text = self.run_text("config", "--owner-tapped")
        self.assertIn("adopted rulebook: none adopted", text)

    def test_invalid_adopted_file_is_exit_1(self) -> None:
        with open(self.paths.adopted_file, "a", encoding="utf-8") as handle:
            handle.write("\nunknown = 1\n")
        code, body = self.run_json("config", "--owner-tapped")
        self.assertEqual((1, "adopted_invalid"), (code, body["error"]["code"]))
        self.assertFalse(os.path.exists(self.paths.desk_state_json))
        self.assertEqual([], self.acks())



class ConnectorsTest(DeskCopy):
    """``connectors --owner-tapped`` (PR 3 decision P1): the owner read back Connected apps and MCP servers."""

    def config_bytes(self) -> Dict[str, bytes]:
        found = {}
        for rel in ("desk.toml", "positions.csv", os.path.join("rules", "ADOPTED"),
                    os.path.join("rules", "exit_rules-1.0.0.toml")):
            with open(os.path.join(self.root, rel), "rb") as handle:
                found[rel] = handle.read()
        return found

    def test_connectors_records_the_readback_instant(self) -> None:
        before = self.config_bytes()
        code, body = self.run_json("connectors", "--owner-tapped")
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual(("connectors", "2026-11-20T19:55:00+00:00", "state/acks.jsonl", []),
                         (payload["action"], payload["connector_readback_utc"], payload["path"], payload["warnings"]))
        self.assertEqual("2026-11-20T19:55:00+00:00", self.desk_state()[tool.CONNECTOR_KEY])
        record = {"utc": "2026-11-20T19:55:00+00:00", "signal_id": None, "trade_id": None, "action": "connectors",
                  "until": None, "rows_sha": None, "hashes": None}
        self.assertEqual([record], self.acks())
        self.assertEqual(record, payload["record"])
        self.assertEqual(before, self.config_bytes(), "the readback never touches the configuration")
        self.assertFalse(os.path.exists(os.path.join(self.root, "journal", "signals.jsonl")))

    def test_the_line_names_the_time_and_the_re_ask(self) -> None:
        code, text = self.run_text("connectors", "--owner-tapped")
        self.assertEqual((0, "connector readback recorded at 19:55 UTC; desk_doctor re-asks after 30 days\n"), (code, text))

    def test_the_key_and_the_age_are_the_doctor_s(self) -> None:
        from aegis_desk import doctor
        self.assertEqual(doctor.CONNECTOR_KEY, tool.CONNECTOR_KEY)
        self.assertEqual(doctor.CONNECTOR_MAX_AGE_DAYS, tool.CONNECTOR_REASK_DAYS)

    def test_doctor_check_10_passes_for_30_days_after_the_readback(self) -> None:
        from aegis_desk import doctor
        self.assertEqual("WARN", doctor.connector_readback_check(self.paths, POSTED).status)
        self.assertEqual(0, self.run_json("connectors", "--owner-tapped")[0])
        recorded = datetime.datetime(2026, 11, 20, 19, 55, tzinfo=datetime.timezone.utc)
        for days, status in ((0, "PASS"), (30, "PASS"), (31, "WARN")):
            with self.subTest(days=days):
                item = doctor.connector_readback_check(self.paths, recorded + datetime.timedelta(days=days))
                self.assertEqual(status, item.status, item.reason)

    def test_connectors_keeps_the_other_desk_state_keys(self) -> None:
        desk_paths.ensure_runtime_dirs(self.paths)
        hashes = {"desk_toml": "0" * 64, "positions_csv": None, "rulebook": None}
        state.atomic_write_json(self.paths.desk_state_json, {"verdicts": {WMT_TRADE: "WATCH"},
                                                             "acknowledged_hashes": hashes,
                                                             "connector_readback_utc": "2026-10-01T12:00:00Z"})
        self.assertEqual(0, self.run_json("connectors", "--owner-tapped")[0])
        document = self.desk_state()
        self.assertEqual(({WMT_TRADE: "WATCH"}, hashes, "2026-11-20T19:55:00+00:00"),
                         (document["verdicts"], document["acknowledged_hashes"], document["connector_readback_utc"]))

    def test_corrupt_desk_state_is_replaced_with_a_warning(self) -> None:
        desk_paths.ensure_runtime_dirs(self.paths)
        with open(self.paths.desk_state_json, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        code, body = self.run_json("connectors", "--owner-tapped")
        self.assertEqual(0, code)
        self.assertIn("state/desk_state.json was corrupt", " ".join(body["payload"]["warnings"]))
        self.assertEqual("2026-11-20T19:55:00+00:00", self.desk_state()["connector_readback_utc"])

    def test_connectors_needs_the_owner_tap_and_takes_no_signal_id(self) -> None:
        for words in (("connectors",), ("connectors", "SIG-20261120-001", "--owner-tapped")):
            with self.subTest(words=words):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    with self.assertRaises(SystemExit) as ctx:
                        tool.main(self.argv(*words))
                self.assertEqual(2, ctx.exception.code)
                message = json.loads(buffer.getvalue())["error"]["message"]
                self.assertIn("--owner-tapped" if len(words) == 1 else "takes no signal id", message)
        self.assertFalse(os.path.exists(self.paths.acks_jsonl))
        self.assertFalse(os.path.exists(self.paths.desk_state_json))
        self.assertIn("connectors", tool.OWNER_TAPPED_ACTIONS)

    def test_schema_describes_the_action(self) -> None:
        code, out = run_tool(["--schema"])
        payload = json.loads(out)["envelope"]["payload"]
        self.assertIn("connectors", payload["action"])
        self.assertIn("connector_readback_utc", payload)
        self.assertIn("connectors", payload["record"]["action"])

if __name__ == "__main__":
    unittest.main()
