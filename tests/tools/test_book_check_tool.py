"""``scripts/book_check.py`` run in-process against a copied fixture desk root: the routine check (book, evaluation,
rule state, posting rules, the re-post ladder, CONFIG CHANGED first, the rulebook-changed digest, the pre-close and
watch-alive lines, the closed-day line, alert_lint before anything prints, the heartbeat), the dry run (``dry run``
first, nothing written, the exit code of the posted run) and ``--post`` (rule state, journals, desk state and the
heartbeat), the exit codes (10 an EXIT SIGNAL in the message, 20 an UNAVAILABLE group, 0 quiet or informational,
1 errors and lint failures, 2 usage) and the JSON payload."""

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
from typing import Any, Dict, List, Sequence, Tuple
from unittest import mock

from aegis_desk import alert_lint, alerts, desk_paths, exit_rules, heartbeat, rule_state, signals, state

import ack as ack_tool
import book as book_tool
import book_check as tool  # the tool scripts/book_check.py
import rules_adopt as adopt_tool

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
CANDIDATE = os.path.join(ROOT, "tests", "fixtures", "rules", "candidate-1.1.0.toml")
REGULAR_NOW = "2026-11-03T19:50:00Z"
STALE_NOW = "2026-11-03T19:50:00Z"
T1_NOW = "2026-11-19T19:50:00Z"
EXPIRY_DAY_NOW = "2026-11-20T19:50:00Z"
EXPIRY_NEXT_RUN = "2026-11-20T21:35:00Z"
HOLIDAY_NOW = "2026-11-26T15:00:00Z"
WMT = "AG-20261020-01"
NVDA = "AG-20261022-02"
SPY = "AG-20261028-03"
RULEBOOK_SHA = "cc09859c896e22f84402ce8b686d0f79ec920b503b647754232247668b8bc5fe"


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = tool.main(list(argv))
    return code, buffer.getvalue()


def order_shaped(text: str) -> List[str]:
    return [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(text)]


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-book-check-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, fixture_set: str, now: str, *extra: str, json_mode: bool = True) -> List[str]:
        argv = ["--offline", "--desk-root", self.root, "--now", now, "--fixture-set", fixture_set] + list(extra)
        if json_mode:
            argv.append("--json")
        return argv

    def run_json(self, fixture_set: str, now: str, *extra: str) -> Tuple[int, Dict[str, Any]]:
        code, out = run_tool(self.argv(fixture_set, now, *extra))
        self.assertEqual([], order_shaped(out))
        return code, json.loads(out)

    def run_text(self, fixture_set: str, now: str, *extra: str) -> Tuple[int, str]:
        code, out = run_tool(self.argv(fixture_set, now, *extra, json_mode=False))
        self.assertEqual([], order_shaped(out))
        return code, out

    def ack_config(self, now: str) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = ack_tool.main(["config", "--owner-tapped", "--offline", "--desk-root", self.root, "--now", now, "--json"])
        self.assertEqual(0, code, buffer.getvalue())

    def journal(self, name: str) -> List[Dict[str, Any]]:
        records, status = state.read_jsonl(os.path.join(self.root, "journal", name))
        self.assertIn(status, ("ok", "missing"))
        return records

    def history(self) -> List[Dict[str, Any]]:
        records, _status = state.read_jsonl(heartbeat.history_path(self.paths))
        return records

    def desk_state(self) -> Dict[str, Any]:
        document, status = state.read_json(self.paths.desk_state_json)
        self.assertEqual("ok", status)
        return document

    def rewrite(self, name: str, old: str, new: str) -> None:
        path = os.path.join(self.root, name)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))

    def readopt(self, old: str, new: str) -> None:
        """Rewrite the adopted rulebook in the copy and record it in rules/ADOPTED (a test-only re-adoption)."""
        self.rewrite("rules/exit_rules-1.0.0.toml", old, new)
        path = os.path.join(self.root, "rules", "exit_rules-1.0.0.toml")
        with open(path, "rb") as handle:
            data = handle.read()
        rulebook = exit_rules.parse_rulebook_text(data.decode("utf-8"), path, hashlib.sha256(data).hexdigest())
        text = exit_rules.adopted_text(version="1.0.0", sha256=rulebook.sha256,
                                       adopted_utc=datetime.datetime(2026, 11, 2, 22, tzinfo=datetime.timezone.utc),
                                       owner_phrase_sha256="0" * 64, rules_sha=rulebook.rules_sha)
        with open(self.paths.adopted_file, "w", encoding="utf-8") as handle:
            handle.write(text)

    def runtime_files(self) -> List[str]:
        """The desk's records under state/ and journal/; ``state/.lock`` is the market-data layer's (the quote cache
        takes the desk lock around its cache and ledger writes on every market tool's run)."""
        found = []
        for top in ("state", "journal"):
            for folder, _dirs, files in os.walk(os.path.join(self.root, top)):
                found.extend(os.path.relpath(os.path.join(folder, name), self.root) for name in files)
        return sorted(item for item in found if item != os.path.join("state", ".lock"))

    def sha_of(self, path: str) -> str:
        with open(path, "rb") as handle:
            return hashlib.sha256(handle.read()).hexdigest()


class SchemaAndUsageTest(DeskCopy):
    def test_schema_describes_the_payload(self) -> None:
        code, out = run_tool(["--schema"])
        self.assertEqual(0, code)
        body = json.loads(out)
        self.assertEqual("book_check", body["tool"])
        described = body["envelope"]["payload"]
        self.ack_config("2026-11-03T19:49:00Z")
        code, body = self.run_json("regular", REGULAR_NOW)
        self.assertEqual(0, code, body)
        self.assertEqual(set(described), set(body["payload"]))
        self.assertEqual(set(described["parts"][0]), set(body["payload"]["parts"][0]))
        self.assertEqual(set(described["verdicts"][0]), set(body["payload"]["verdicts"][0]))

    def test_usage_errors(self) -> None:
        for argv in (["--json", "--desk-root", self.root, "--fixture-set", "regular"],
                     self.argv("regular", REGULAR_NOW, "--next-run-utc", "2026-11-03 21:35"),
                     self.argv("regular", REGULAR_NOW, "--next-run-utc", "2026-11-03T19:00:00Z"),
                     self.argv("regular", REGULAR_NOW, "--routine", "Book_Check"),
                     self.argv("regular", REGULAR_NOW, "--scheduled-et", "25:00"),
                     self.argv("regular", REGULAR_NOW, "--bogus")):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(argv)
                self.assertEqual(2, ctx.exception.code)


class DryRunTest(DeskCopy):
    def test_unacknowledged_config_prints_config_changed_first(self) -> None:
        code, text = self.run_text("regular", REGULAR_NOW)
        self.assertEqual(0, code, text)
        lines = text.splitlines()
        self.assertEqual("dry run", lines[0])
        self.assertTrue(lines[1].startswith("CONFIG CHANGED | desk.toml: not acknowledged -> sha "), lines[1])
        self.assertIn(" | adopted rulebook: not acknowledged -> sha cc09859c | ", lines[1])
        self.assertIn("acknowledge with ack.py config --owner-tapped", lines[1])
        self.assertTrue(lines[2].startswith("WATCH %s | WMT covered call | your rule DTE21" % WMT), lines[2])
        self.assertEqual([], self.runtime_files(), "a dry run writes none of the desk's records")
        code, body = self.run_json("regular", REGULAR_NOW)
        payload = body["payload"]
        self.assertEqual((0, True, False, "config_changed"), (code, payload["dry_run"], payload["posted"],
                                                               payload["parts"][0]["kind"]))
        self.assertTrue(payload["message"].startswith("CONFIG CHANGED"))
        self.assertEqual(["desk_toml", "positions_csv", "rulebook"], [item["name"] for item in payload["config_changed"]])
        self.assertEqual([None, None, None], [item["old"] for item in payload["config_changed"]])
        self.assertEqual(RULEBOOK_SHA, payload["config_changed"][2]["new"])
        self.assertTrue(payload["lint"]["ok"])

    def test_dry_run_exit_code_is_the_posted_runs(self) -> None:
        self.ack_config("2026-11-20T19:49:00Z")
        dry_code, dry = self.run_json("expiry_day", EXPIRY_DAY_NOW, "--next-run-utc", EXPIRY_NEXT_RUN)
        before = self.runtime_files()
        code, posted = self.run_json("expiry_day", EXPIRY_DAY_NOW, "--next-run-utc", EXPIRY_NEXT_RUN, "--post")
        self.assertEqual((10, 10), (dry_code, code))
        self.assertEqual(dry["payload"]["message"], posted["payload"]["message"])
        self.assertEqual(["state/acks.jsonl", "state/desk_state.json"], before, "only the ack step wrote")


class PostTest(DeskCopy):
    def test_expiry_day_posts_once_then_is_quiet(self) -> None:
        self.ack_config("2026-11-20T19:49:00Z")
        code, body = self.run_json("expiry_day", EXPIRY_DAY_NOW, "--next-run-utc", EXPIRY_NEXT_RUN, "--post",
                                   "--routine", "book-check")
        self.assertEqual(10, code, body)
        payload = body["payload"]
        self.assertEqual(("fired", False, True, True), (body["status"], payload["dry_run"], payload["posted"],
                                                         payload["lint"]["ok"]))
        self.assertTrue(alert_lint.check(payload["message"], payload["emitted_numbers"]).ok)
        message = payload["message"]
        self.assertTrue(message.startswith("EXIT SIGNAL " + WMT + " | WMT covered call | your rule TP80 (rules 1.0.0, "
                                           "sha cc09859c) fired: credit captured 95.32 % (your threshold: at or above "
                                           "80 %)"), message)
        self.assertIn("EXIT SIGNAL %s | NVDA covered call | your rule DELTA_SIGNAL" % NVDA, message)
        self.assertIn("WATCH %s | SPY protective put | your rule ROLL30" % SPY, message)
        self.assertIn("WATCH portfolio | all open groups | your rule CONC", message)
        self.assertEqual(1, message.count("deterministic rules you adopted"), "one footer for the composed message")
        recorded = self.journal("signals.jsonl")
        self.assertEqual([(WMT, "EXIT_SIGNAL"), (NVDA, "EXIT_SIGNAL"), (SPY, "WATCH"), ("portfolio", "WATCH")],
                         [(record["trade_id"], record["verdict"]) for record in recorded])
        self.assertEqual(["SIG-20261120-001", "SIG-20261120-002", "SIG-20261120-003", "SIG-20261120-004"],
                         [record["signal_id"] for record in recorded])
        self.assertEqual([(item["signal_id"], item["kind"]) for item in payload["signals"]],
                         [(record["signal_id"], "new") for record in recorded])
        posted = self.journal("alerts.jsonl")
        self.assertEqual([record["signal_id"] for record in recorded], [record["signal_id"] for record in posted])
        self.assertTrue(all(record["lint_ok"] for record in posted))
        self.assertTrue(os.path.exists(os.path.join(self.root, "state", "heartbeat", "book-check.json")))
        self.assertTrue(os.path.exists(os.path.join(self.root, "journal", "2026-11-20.md")))
        entries = rule_state.load(self.paths)
        self.assertEqual("fired", entries["%s|TP80" % WMT]["status"])
        self.assertEqual((1, "2026-11-20T19:50:00+00:00"), (entries["%s|TP80" % WMT]["posts"],
                                                          entries["%s|TP80" % WMT]["last_posted_at"]))
        self.assertEqual({WMT: "EXIT_SIGNAL", NVDA: "EXIT_SIGNAL", SPY: "WATCH", "portfolio": "WATCH"},
                         self.desk_state()["last_verdicts"])
        record, status = heartbeat.read_heartbeat(self.paths, "book-check")
        self.assertEqual(("ok", 10, "fired"), (status, record["exit_code"], record["status"]))
        self.assertEqual(hashlib.sha256(message.encode("utf-8")).hexdigest(), record["message_sha256"])

        code, text = self.run_text("expiry_day", "2026-11-20T19:51:00Z", "--next-run-utc", EXPIRY_NEXT_RUN, "--post",
                                   "--routine", "book-check")
        self.assertEqual((0, "quiet\n"), (code, text))
        self.assertEqual(4, len(self.journal("signals.jsonl")), "no new signal record")
        self.assertEqual(4, len(self.journal("alerts.jsonl")), "nothing re-posted inside the rules' cooldown")
        self.assertEqual(2, len(self.history()))

    def test_without_next_run_the_take_profit_is_pending(self) -> None:
        self.ack_config("2026-11-20T19:49:00Z")
        code, body = self.run_json("expiry_day", EXPIRY_DAY_NOW, "--post")
        self.assertEqual(0, code, body)
        message = body["payload"]["message"]
        self.assertIn("pending confirmation: your rule TP80: credit captured 95.32 %", message)
        self.assertNotIn("EXIT SIGNAL", message)
        wmt = [item for item in body["payload"]["verdicts"] if item["group_id"] == WMT][0]
        self.assertEqual(("WATCH", ["TP80", "PIN3"]), (wmt["verdict"], wmt["pending"]))

    def test_stale_quotes_post_the_banners_on_every_run(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        code, body = self.run_json("stale", STALE_NOW, "--post")
        self.assertEqual(20, code, body)
        payload = body["payload"]
        self.assertEqual("unavailable", body["status"])
        self.assertEqual(["unavailable", "unavailable", "unavailable"],
                         [part["kind"] for part in payload["parts"] if part["group_id"] is not None])
        self.assertIn("UNAVAILABLE %s | WMT covered call | rules 1.0.0, sha cc09859c: no verdict while an input is "
                      "unavailable; this is not HOLD" % WMT, payload["message"])
        self.assertEqual(3, len([r for r in self.journal("signals.jsonl") if r["verdict"] == "UNAVAILABLE"]))
        code, body = self.run_json("stale", "2026-11-03T19:52:00Z", "--post")
        self.assertEqual(20, code)
        self.assertEqual(["repost", "repost", "repost"],
                         [part["signal"] for part in body["payload"]["parts"] if part["kind"] == "unavailable"])
        self.assertEqual(3, len([r for r in self.journal("signals.jsonl") if r["verdict"] == "UNAVAILABLE"]),
                         "a standing UNAVAILABLE is re-posted under its signal id, never a new signal")

    def test_unavailable_ended_posts_the_transitions(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        self.assertEqual(20, self.run_json("stale", STALE_NOW, "--post")[0])
        # 19:56: past the 5-minute quote TTL of the stale run's cached quotes, inside the regular set's freshness.
        code, body = self.run_json("regular", "2026-11-03T19:56:00Z", "--post")
        self.assertEqual(0, code, body)
        parts = {(part["group_id"], part["kind"], part["reason"]) for part in body["payload"]["parts"]}
        self.assertIn((WMT, "watch", "verdict_changed"), parts)
        self.assertIn((SPY, "hold", "verdict_changed"), parts)
        self.assertIn("HOLD %s | SPY protective put | rules 1.0.0, sha cc09859c: none of your rules fires now; was "
                      "UNAVAILABLE | not an instruction to trade; you act in your brokerage account." % SPY,
                      body["payload"]["message"])
        self.assertEqual("HOLD", self.desk_state()["last_verdicts"][SPY])

    def test_new_watch_on_a_later_day(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        self.assertEqual(0, self.run_json("regular", REGULAR_NOW, "--post")[0])
        self.assertEqual("HOLD", self.desk_state()["last_verdicts"][SPY])
        code, body = self.run_json("t1", T1_NOW, "--post")
        self.assertEqual(0, code, body)
        reasons = {part["group_id"]: part["reason"] for part in body["payload"]["parts"]}
        # SPY: HOLD -> WATCH (ROLL30, 29 calendar days); WMT and NVDA stay WATCH (DTE21 stays fired, PIN3 is
        # pending without --next-run-utc) and the portfolio's CONC stays fired: nothing else is news.
        self.assertEqual({SPY: "verdict_changed"}, reasons)
        self.assertEqual(4, len(self.journal("signals.jsonl")))

    def test_lateness_and_the_heartbeat(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        code, body = self.run_json("regular", REGULAR_NOW, "--post", "--scheduled-et", "14:45", "--routine", "pre-close")
        self.assertEqual(0, code)
        self.assertIn("run 5 min late", body["payload"]["message"])
        record, status = heartbeat.read_heartbeat(self.paths, "pre-close")
        self.assertEqual(("ok", 5, "14:45", "ok"), (status, record["lateness_min"], record["scheduled_et"],
                                                    record["status"]))


class TransitionTest(DeskCopy):
    def test_first_sight_of_a_hold_group_is_no_change(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        code, body = self.run_json("regular", REGULAR_NOW, "--post")
        self.assertEqual(0, code)
        self.assertEqual([(WMT, "watch"), (NVDA, "watch"), ("portfolio", "watch")],
                         [(part["group_id"], part["kind"]) for part in body["payload"]["parts"]])
        self.assertEqual({"verdict": "HOLD", "previous": None}, {key: [row for row in body["payload"]["verdicts"]
                                                                       if row["group_id"] == SPY][0][key]
                                                                for key in ("verdict", "previous")})

    def test_watch_back_to_hold_posts_the_notice(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        state.atomic_write_json(self.paths.desk_state_json, dict(
            self.desk_state(), last_verdicts={WMT: "WATCH", NVDA: "WATCH", SPY: "WATCH", "portfolio": "WATCH"}))
        code, body = self.run_json("regular", REGULAR_NOW)
        self.assertEqual(0, code)
        holds = [part for part in body["payload"]["parts"] if part["kind"] == "hold"]
        self.assertEqual([(SPY, "HOLD", None)], [(part["group_id"], part["verdict"], part["signal"]) for part in holds])
        self.assertIn("HOLD %s | SPY protective put | rules 1.0.0, sha cc09859c: none of your rules fires now; was "
                      "WATCH" % SPY, body["payload"]["message"])

    def test_portfolio_back_to_hold_posts_the_notice(self) -> None:
        self.rewrite("desk.toml", "declared_equity = 250000.00", "declared_equity = 1000000.00")
        self.ack_config("2026-11-03T19:49:00Z")
        state.atomic_write_json(self.paths.desk_state_json, dict(
            self.desk_state(), last_verdicts={WMT: "WATCH", NVDA: "WATCH", SPY: "HOLD", "portfolio": "WATCH"}))
        code, body = self.run_json("regular", REGULAR_NOW, "--post")
        self.assertEqual(0, code)
        self.assertIn("HOLD portfolio | all open groups | rules 1.0.0, sha cc09859c: none of your portfolio or "
                      "underlying rules fires now; was WATCH", body["payload"]["message"])
        self.assertEqual("HOLD", self.desk_state()["last_verdicts"]["portfolio"])
        holds = [record for record in self.journal("alerts.jsonl") if record["text"].startswith("HOLD portfolio")]
        self.assertEqual([None], [record["signal_id"] for record in holds], "a HOLD notice is journaled, never a signal")
        self.assertNotIn("portfolio", [record["trade_id"] for record in self.journal("signals.jsonl")])

    def test_a_quiet_posted_run_still_records_the_observations(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        self.assertEqual(0, self.run_json("regular", REGULAR_NOW, "--post")[0])
        first = rule_state.load(self.paths)["%s|DELTA_WATCH" % NVDA]
        code, body = self.run_json("regular", "2026-11-03T19:51:00Z", "--post")
        self.assertEqual(("quiet", True, None), (body["status"], body["payload"]["quiet"], body["payload"]["message"]))
        entry = rule_state.load(self.paths)["%s|DELTA_WATCH" % NVDA]
        self.assertEqual(("pending", "pending"), (first["status"], entry["status"]))
        self.assertIn("state/rule_state.json", body["payload"]["written"])
        self.assertIn("state/heartbeat/book-check.json", body["payload"]["written"])

    def test_corrupt_desk_state_is_unacknowledged(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        with open(self.paths.desk_state_json, "w", encoding="utf-8") as handle:
            handle.write("{not json")
        code, body = self.run_json("regular", REGULAR_NOW)
        self.assertEqual(0, code)
        self.assertEqual("config_changed", body["payload"]["parts"][0]["kind"])
        self.assertIn("state/desk_state.json was corrupt", " ".join(body["payload"]["warnings"]))

    def test_an_acknowledged_hash_with_a_trailing_newline_is_unacknowledged(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        document = self.desk_state()
        document["acknowledged_hashes"]["desk_toml"] += "\n"
        with open(self.paths.desk_state_json, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(document))
        code, body = self.run_json("regular", REGULAR_NOW)
        self.assertEqual(0, code, body)
        self.assertEqual([("desk_toml", None)], [(item["name"], item["old"]) for item in body["payload"]["config_changed"]])


class LadderTest(DeskCopy):
    def test_unacknowledged_exit_signal_is_reposted_on_the_ladder(self) -> None:
        self.readopt('cooldown_min = 240                 # minimum', 'cooldown_min = 0                   # minimum')
        self.ack_config("2026-11-20T19:49:00Z")
        instants = ["2026-11-20T19:50:%02dZ" % second for second in (0, 10, 20, 30, 40, 50)] + [
            "2026-11-20T19:51:%02dZ" % second for second in (0, 10, 20, 30)]
        codes = []
        for now in instants:
            code, body = self.run_json("expiry_day", now, "--next-run-utc", EXPIRY_NEXT_RUN, "--post")
            codes.append(code)
        self.assertEqual([10, 10, 0, 10, 0, 0, 0, 10, 0, 0], codes)
        posts = [record["signal_id"] for record in self.journal("alerts.jsonl")]
        self.assertEqual(4, posts.count("SIG-20261120-001"), "first post plus three re-posts")
        self.assertEqual(4, len(self.journal("signals.jsonl")), "re-posts are never new signals")

    def test_ack_and_snooze_stop_the_ladder(self) -> None:
        self.readopt('cooldown_min = 240                 # minimum', 'cooldown_min = 0                   # minimum')
        self.ack_config("2026-11-20T19:49:00Z")
        self.assertEqual(10, self.run_json("expiry_day", EXPIRY_DAY_NOW, "--next-run-utc", EXPIRY_NEXT_RUN, "--post")[0])
        for words in (["ack", "SIG-20261120-001"], ["snooze", "SIG-20261120-002", "--until", "15:30 ET", "--owner-tapped"]):
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                self.assertEqual(0, ack_tool.main(words + ["--offline", "--desk-root", self.root, "--now",
                                                           "2026-11-20T19:50:05Z"]))
        code, text = self.run_text("expiry_day", "2026-11-20T19:50:10Z", "--next-run-utc", EXPIRY_NEXT_RUN, "--post")
        self.assertEqual((0, "quiet\n"), (code, text))


class SummaryAndWatchAliveTest(DeskCopy):
    def test_summary_prints_the_pre_close_line(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        code, body = self.run_json("regular", REGULAR_NOW, "--summary")
        self.assertEqual(0, code, body)
        message = body["payload"]["message"]
        self.assertIn("pre-close | verdicts: 0 EXIT SIGNAL, 0 UNAVAILABLE, 2 WATCH, 1 HOLD | open signals: 3 | "
                      "data age: 18 min | rules 1.0.0, sha cc09859c | verify live quotes in your broker before acting",
                      message)
        self.assertEqual("pre_close", body["payload"]["parts"][-1]["kind"])
        self.assertEqual(0, self.run_json("regular", REGULAR_NOW, "--post")[0])
        code, body = self.run_json("regular", "2026-11-03T19:51:00Z", "--summary")
        self.assertEqual(0, code)
        self.assertEqual(["pre_close"], [part["kind"] for part in body["payload"]["parts"]],
                         "--summary always prints the line, quiet or not")

    def test_summary_after_the_quotes_went_stale_is_unavailable(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        code, body = self.run_json("regular", "2026-11-03T20:35:00Z", "--summary")
        self.assertEqual(20, code)
        self.assertIn("pre-close | verdicts: 0 EXIT SIGNAL, 3 UNAVAILABLE, 0 WATCH, 0 HOLD", body["payload"]["message"])

    def test_watch_alive_when_nothing_posted_since_the_open(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        self.assertEqual(0, self.run_json("regular", REGULAR_NOW, "--post")[0])
        code, text = self.run_text("regular", "2026-11-03T19:51:00Z", "--watch-alive")
        self.assertEqual((0, "dry run\nquiet\n"), (code, text), "something was posted since the open")
        os.remove(signals.alerts_path(self.paths))
        code, body = self.run_json("regular", "2026-11-03T19:51:00Z", "--watch-alive", "--post")
        self.assertEqual(0, code)
        self.assertEqual(["watch_alive"], [part["kind"] for part in body["payload"]["parts"]])
        self.assertTrue(body["payload"]["message"].startswith("watch alive | nothing posted since the open | "
                                                              "rules 1.0.0, sha cc09859c | data age: 19 min"))
        self.assertEqual([None], [record["signal_id"] for record in self.journal("alerts.jsonl")])

    def test_no_watch_alive_before_the_open(self) -> None:
        # 08:00 ET on 2026-11-03 (the pre_open set): the session has not opened, so "since the open" means nothing.
        self.ack_config("2026-11-03T12:59:00Z")
        self.assertEqual(0, self.run_json("pre_open", "2026-11-03T13:00:00Z", "--post")[0])
        os.remove(signals.alerts_path(self.paths))
        code, text = self.run_text("pre_open", "2026-11-03T13:01:00Z", "--watch-alive")
        self.assertEqual((0, "dry run\nquiet\n"), (code, text))


class ConfigAndRulebookTest(DeskCopy):
    def test_a_changed_desk_toml_is_named_until_acknowledged(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        old = self.sha_of(self.paths.desk_toml)
        self.rewrite("desk.toml", "pin_band = 0.25", "pin_band = 0.25  # edited")
        new = self.sha_of(self.paths.desk_toml)
        code, body = self.run_json("regular", REGULAR_NOW, "--post")
        self.assertEqual(0, code)
        self.assertEqual([{"name": "desk_toml", "old": old, "new": new}], body["payload"]["config_changed"])
        self.assertTrue(body["payload"]["message"].startswith(
            "CONFIG CHANGED | desk.toml: sha %s -> sha %s | evaluated with the files as they are now" % (old[:8], new[:8])))
        code, body = self.run_json("regular", "2026-11-03T19:51:00Z", "--post")
        self.assertEqual(["config_changed"], [part["kind"] for part in body["payload"]["parts"]], "on every run")
        self.ack_config("2026-11-03T19:52:00Z")
        code, text = self.run_text("regular", "2026-11-03T19:53:00Z", "--post")
        self.assertEqual((0, "quiet\n"), (code, text))

    def test_rulebook_changed_digest_is_printed_once(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        state.atomic_write_json(self.paths.desk_state_json, dict(
            self.desk_state(), rulebook_change={"previous_version": "0.9.0", "version": "1.0.0", "changed": 2,
                                                "adopted_utc": "2026-11-02T22:00:00+00:00"}))
        code, body = self.run_json("regular", REGULAR_NOW, "--post")
        self.assertEqual(0, code)
        self.assertEqual("rulebook_changed", body["payload"]["parts"][0]["kind"])
        self.assertTrue(body["payload"]["message"].startswith(
            "rulebook changed: 2 verdicts changed | rules 0.9.0 -> rules 1.0.0, sha cc09859c"))
        self.assertEqual("2026-11-03T19:50:00+00:00", self.desk_state()["rulebook_change"]["posted_utc"])
        code, body = self.run_json("regular", "2026-11-03T19:51:00Z", "--post")
        self.assertTrue(body["payload"]["quiet"])

    def test_a_digest_of_another_rulebook_file_is_not_printed(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        state.atomic_write_json(self.paths.desk_state_json, dict(
            self.desk_state(), rulebook_change={"previous_version": "0.9.0", "version": "1.0.0", "sha256": "1" * 64,
                                                "changed": 2, "adopted_utc": "2026-11-02T22:00:00+00:00"}))
        code, body = self.run_json("regular", REGULAR_NOW)
        self.assertNotIn("rulebook_changed", [part["kind"] for part in body["payload"]["parts"]])

    def test_rules_adopt_hands_its_digest_to_the_next_check(self) -> None:
        def run(module: Any, argv: List[str]) -> int:
            buffer = io.StringIO()
            with contextlib.redirect_stdout(buffer):
                code = module.main(argv + ["--offline", "--desk-root", self.root, "--json"])
            return code

        self.assertEqual(0, run(book_tool, ["--snapshot", "--fixture-set", "pre_open", "--now", "2026-11-02T21:30:00Z"]))
        self.assertEqual(0, run(adopt_tool, ["--file", CANDIDATE, "--version", "1.1.0", "--owner-phrase",
                                             "adopt rules 1.1.0", "--now", "2026-11-03T21:26:00Z"]))
        self.ack_config("2026-11-03T21:27:00Z")
        code, body = self.run_json("after_close", "2026-11-03T21:28:00Z", "--post")
        self.assertEqual(0, code, body)
        self.assertEqual("rulebook_changed", body["payload"]["parts"][0]["kind"])
        self.assertTrue(body["payload"]["message"].startswith(
            "rulebook changed: 0 verdicts changed | rules 1.0.0 -> rules 1.1.0, sha "), body["payload"]["message"])
        code, body = self.run_json("after_close", "2026-11-03T21:29:00Z", "--post")
        self.assertNotIn("rulebook_changed", [part["kind"] for part in body["payload"]["parts"]])


class ClosedDayAndErrorsTest(DeskCopy):
    def test_closed_day_is_one_line_and_no_evaluation(self) -> None:
        code, text = self.run_text("holiday", HOLIDAY_NOW, "--post")
        self.assertEqual((0, "NYSE closed (Thanksgiving Day); no evaluation\n"), (code, text))
        self.assertFalse(os.path.exists(self.paths.rule_state_json))
        self.assertEqual([], self.journal("alerts.jsonl"))
        record, status = heartbeat.read_heartbeat(self.paths, "book-check")
        self.assertEqual(("ok", 0), (status, record["exit_code"]))

    def test_no_adopted_rulebook_is_exit_1(self) -> None:
        os.remove(self.paths.adopted_file)
        code, body = self.run_json("regular", REGULAR_NOW)
        self.assertEqual((1, "error", "not_adopted"), (code, body["status"], body["error"]["code"]))

    def test_tampered_rulebook_is_exit_1(self) -> None:
        with open(os.path.join(self.root, "rules", "exit_rules-1.0.0.toml"), "a", encoding="utf-8") as handle:
            handle.write("# edited after adoption\n")
        code, body = self.run_json("regular", REGULAR_NOW, "--post")
        self.assertEqual((1, "rules_tampered"), (code, body["error"]["code"]))
        self.assertFalse(os.path.exists(self.paths.rule_state_json))
        record, status = heartbeat.read_heartbeat(self.paths, "book-check")
        self.assertEqual(("ok", 1, "error"), (status, record["exit_code"], record["status"]), "a failed run is recorded")

    def test_book_input_errors_map_like_the_book_tool(self) -> None:
        self.rewrite("positions.csv", "cost_basis_total,", "cost_basis_totl,")
        code, body = self.run_json("regular", REGULAR_NOW)
        self.assertEqual((1, "positions_invalid"), (code, body["error"]["code"]))

    def test_production_host_is_refused_with_exit_20(self) -> None:
        self.rewrite("desk.toml", 'host = "sandbox"', 'host = "production"')
        code, body = self.run_json("regular", REGULAR_NOW)
        self.assertEqual((20, "production_host"), (code, body["error"]["code"]))
        self.assertFalse(os.path.exists(os.path.join(self.root, "state", "heartbeat")), "a dry run records nothing")
        code, body = self.run_json("regular", REGULAR_NOW, "--post")
        self.assertEqual(20, code)
        record, status = heartbeat.read_heartbeat(self.paths, "book-check")
        self.assertEqual(("ok", 20, "unavailable"), (status, record["exit_code"], record["status"]))

    def test_invalid_desk_toml_is_exit_1_and_recorded(self) -> None:
        self.rewrite("desk.toml", "grace_min = 5", "grace_min = 31")
        code, body = self.run_json("regular", REGULAR_NOW, "--post")
        self.assertEqual((1, "config_invalid"), (code, body["error"]["code"]))
        record, status = heartbeat.read_heartbeat(self.paths, "book-check")
        self.assertEqual(("ok", 1, "error"), (status, record["exit_code"], record["status"]))

    def test_a_failing_part_is_withheld_and_never_posted(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        real = alerts.render

        def bad_render(evaluation: Any, book: Any, run_info: Any) -> List[alerts.Alert]:
            out = real(evaluation, book, run_info)
            first = out[0]
            first.text = first.text.replace("13 trading days", "34-DTE", 1)  # 34 was never emitted
            return out

        with mock.patch.object(tool.alerts, "render", bad_render):
            code, body = self.run_json("regular", REGULAR_NOW, "--post")
        self.assertEqual(0, code, body)
        self.assertNotIn("34-DTE", body["payload"]["message"])
        self.assertEqual(["number_not_emitted"], body["payload"]["withheld"][0]["codes"])
        self.assertTrue(all(record["lint_ok"] for record in self.journal("alerts.jsonl")))
        self.assertNotIn("34-DTE", json.dumps(self.journal("signals.jsonl")), "the journaled reasons are withheld too")

    def test_lint_failure_is_exit_1_and_never_posted(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        real = alerts.compose

        def bad_compose(parts: Any) -> alerts.Alert:
            composed = real(parts)
            composed.text = composed.text.replace("13 trading days", "34-DTE", 1)  # 34 was never emitted
            return composed

        with mock.patch.object(tool.alerts, "compose", bad_compose):
            code, body = self.run_json("regular", REGULAR_NOW, "--post")
            self.assertEqual((1, "lint_failed"), (code, body["error"]["code"]))
            self.assertIn("number_not_emitted", body["error"]["message"])
            code, text = self.run_text("regular", REGULAR_NOW)
        self.assertEqual(1, code)
        self.assertTrue(text.startswith("error: "), text)
        self.assertNotIn("34-DTE", text)
        self.assertEqual([], self.journal("signals.jsonl"))
        self.assertEqual([False], [record["lint_ok"] for record in self.journal("alerts.jsonl")])
        self.assertFalse(os.path.exists(self.paths.rule_state_json), "nothing consumed: the next run tries again")
        record, status = heartbeat.read_heartbeat(self.paths, "book-check")
        self.assertEqual(("ok", 1, "error", None), (status, record["exit_code"], record["status"], record["message_sha256"]))

    def test_render_error_is_exit_1(self) -> None:
        with mock.patch.object(tool.alerts, "render", side_effect=ValueError("the EXIT_SIGNAL rule X carries no plan")):
            code, body = self.run_json("regular", REGULAR_NOW)
        self.assertEqual((1, "render_failed"), (code, body["error"]["code"]))

    def test_lock_busy_is_exit_1(self) -> None:
        desk_paths.ensure_runtime_dirs(self.paths)
        with mock.patch.object(tool, "RUN_LOCK_TIMEOUT_S", 0.2):
            with state.locked(tool.run_lock_path(self.paths)):
                code, body = self.run_json("regular", REGULAR_NOW, "--post")
        self.assertEqual((1, "lock_busy"), (code, body["error"]["code"]))


class QuarantineTest(DeskCopy):
    def test_quarantined_group_is_the_unavailable_banner(self) -> None:
        self.rewrite("positions.csv", "AG-20261028-03,2026-10-28,1130.00,,11.30", "AG-20261028-03,2026-10-28,-1130.00,,11.30")
        self.ack_config("2026-11-03T19:49:00Z")
        code, body = self.run_json("regular", REGULAR_NOW)
        self.assertEqual(20, code, body)
        message = body["payload"]["message"]
        self.assertIn("UNAVAILABLE %s | quarantined in positions.csv | rules 1.0.0, sha cc09859c" % SPY, message)
        self.assertTrue(body["payload"]["lint"]["ok"])


class ReviewPostingTest(DeskCopy):
    """The reviews' posting findings: one cell word cannot block every alert, a flapping rule posts once until it
    re-arms, an unreadable portfolio section is never HOLD, an acknowledged UNAVAILABLE is not re-posted, the ladder
    at the default cooldown, the strict emitted document."""

    def test_a_bad_cell_never_blocks_the_other_alerts(self) -> None:
        word = "so" + "ld"
        self.rewrite("positions.csv", "-480.00,,,closed,2026-10-14", "-480.00,,,%s,2026-10-14" % word)
        self.ack_config("2026-11-20T19:49:00Z")
        code, body = self.run_json("expiry_day", EXPIRY_DAY_NOW, "--next-run-utc", EXPIRY_NEXT_RUN, "--post")
        self.assertEqual(10, code, body)
        payload = body["payload"]
        message = payload["message"]
        self.assertTrue(payload["lint"]["ok"], payload["lint"])
        self.assertIn("EXIT SIGNAL %s | WMT covered call" % WMT, message)
        self.assertIn("UNAVAILABLE AG-20261001-04 | quarantined in positions.csv", message)
        self.assertIn("unavailable: row 9: the status cell cannot be used (positions_check.py shows it)", message)
        self.assertNotIn(word, message.lower())
        self.assertEqual([], payload["withheld"])

    def test_a_part_that_fails_the_lint_is_withheld_and_the_rest_is_posted(self) -> None:
        self.ack_config("2026-11-20T19:49:00Z")
        real = alerts.render
        bad = "so" + "ld"

        def bad_render(evaluation: Any, book: Any, run_info: Any) -> List[alerts.Alert]:
            out = real(evaluation, book, run_info)
            spy = next(alert for alert in out if alert.group_id == SPY)
            spy.text = spy.text.replace("SPY protective put", "SPY %s put" % bad, 1)
            spy.body = spy.body.replace("SPY protective put", "SPY %s put" % bad, 1)
            return out

        with mock.patch.object(tool.alerts, "render", bad_render):
            code, body = self.run_json("expiry_day", EXPIRY_DAY_NOW, "--next-run-utc", EXPIRY_NEXT_RUN, "--post")
        self.assertEqual(10, code, body)
        payload = body["payload"]
        self.assertTrue(payload["lint"]["ok"])
        self.assertEqual([{"group_id": SPY, "kind": "watch", "codes": ["forbidden_language"]}], payload["withheld"])
        message = payload["message"]
        self.assertNotIn(bad, message)
        self.assertIn("WATCH %s | rules 1.0.0, sha cc09859c: the message text failed the alert lint (forbidden_language) "
                      "and is withheld" % SPY, message)
        self.assertIn("EXIT SIGNAL %s | WMT covered call" % WMT, message)
        self.assertTrue(any("withheld" in warning for warning in payload["warnings"]))
        self.assertEqual([True] * 4, [record["lint_ok"] for record in self.journal("alerts.jsonl")])

    def test_a_flapping_portfolio_rule_posts_once_until_it_rearms(self) -> None:
        seen = []
        desk_toml = os.path.join(self.root, "desk.toml")
        for minute, equity in ((50, "250000.00"), (52, "300000.00"), (54, "250000.00"), (56, "300000.00"),
                               (58, "250000.00")):
            with open(desk_toml, encoding="utf-8") as handle:
                text = handle.read()
            for value in ("250000.00", "300000.00"):
                text = text.replace("declared_equity = %s" % value, "declared_equity = %s" % equity)
            with open(desk_toml, "w", encoding="utf-8") as handle:
                handle.write(text)
            now = "2026-11-03T19:%d:00Z" % minute
            self.ack_config(now)
            code, body = self.run_json("regular", now, "--post")
            seen.append([(part["group_id"], part["kind"]) for part in body["payload"]["parts"]
                         if part["group_id"] == "portfolio"])
        self.assertEqual([[("portfolio", "watch")], [("portfolio", "hold")], [], [], []], seen)
        self.assertEqual(1, len([r for r in self.journal("signals.jsonl") if r["trade_id"] == "portfolio"]))

    def test_an_unreadable_portfolio_section_is_never_hold(self) -> None:
        self.ack_config("2026-11-20T19:49:00Z")
        self.assertEqual(10, self.run_json("expiry_day", EXPIRY_DAY_NOW, "--next-run-utc", EXPIRY_NEXT_RUN, "--post")[0])
        code, body = self.run_json("expiry_day", "2026-11-20T20:05:00Z", "--next-run-utc", EXPIRY_NEXT_RUN, "--post")
        self.assertEqual(20, code, body)
        payload = body["payload"]
        self.assertEqual(("UNAVAILABLE", "WATCH"), (payload["portfolio"]["verdict"], payload["portfolio"]["previous"]))
        self.assertEqual([], [part for part in payload["parts"] if part["group_id"] == "portfolio"])
        self.assertNotIn("HOLD portfolio", payload["message"])
        self.assertEqual("WATCH", self.desk_state()["last_verdicts"]["portfolio"], "the verdict of record stands")

    def test_an_acknowledged_unavailable_is_not_reposted(self) -> None:
        self.ack_config("2026-11-03T19:49:00Z")
        self.assertEqual(20, self.run_json("stale", STALE_NOW, "--post")[0])
        first = self.journal("signals.jsonl")[0]["signal_id"]
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.assertEqual(0, ack_tool.main(["ack", first, "--offline", "--desk-root", self.root, "--now",
                                               "2026-11-03T19:54:00Z"]))
        self.assertIn("it is no longer re-posted", buffer.getvalue())
        code, body = self.run_json("stale", "2026-11-03T19:55:00Z", "--post")
        self.assertEqual(20, code, body)
        reposted = [part["signal_id"] for part in body["payload"]["parts"] if part["kind"] == "unavailable"]
        self.assertNotIn(first, reposted)
        self.assertEqual(2, len(reposted), "the two unacknowledged banners still stand")

    def test_the_ladder_waits_for_the_default_cooldown(self) -> None:
        self.ack_config("2026-11-20T19:49:00Z")
        self.assertEqual(10, self.run_json("expiry_day", EXPIRY_DAY_NOW, "--next-run-utc", EXPIRY_NEXT_RUN, "--post")[0])
        code, text = self.run_text("expiry_day", "2026-11-20T19:51:00Z", "--next-run-utc", EXPIRY_NEXT_RUN, "--post")
        self.assertEqual((0, "quiet\n"), (code, text), "the next run is due by the ladder but inside cooldown_min 240")
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            ack_tool.main(["list", "--offline", "--desk-root", self.root, "--now", "2026-11-20T19:52:00Z"])
        self.assertIn("SIG-20261120-001 | %s covered_call | EXIT_SIGNAL, your plan: close | first posted 2026-11-20 19:50 "
                      "UTC | posted 1 of 4 times; next re-post 1 run after the first post, not before 2026-11-20 23:50 "
                      "UTC (rule TP80 cooldown 240 min)" % WMT, buffer.getvalue())

    def test_the_strict_emitted_document_is_printed(self) -> None:
        self.ack_config("2026-11-20T19:49:00Z")
        code, body = self.run_json("expiry_day", EXPIRY_DAY_NOW, "--next-run-utc", EXPIRY_NEXT_RUN)
        payload = body["payload"]
        document = payload["emitted"]
        self.assertEqual(set(alert_lint.EMITTED_DOCUMENT_KEYS), set(document))
        self.assertEqual(payload["emitted_numbers"], document["emitted_numbers"])
        self.assertTrue(alert_lint.load_emitted(document).check(payload["message"]).ok)
        for old, new in (("your rule TP80", "your rule TP95"), ("sha cc09859c", "sha 00000110")):
            changed = payload["message"].replace(old, new, 1)
            self.assertFalse(alert_lint.load_emitted(document).check(changed).ok, new)

    def test_verdict_rows_name_the_cooling_rules(self) -> None:
        _adopted, rules = exit_rules.load_adopted_rulebook(self.paths)
        entry = rule_state.new_entry(rules.rules_sha["TP80"], datetime.date(2026, 10, 20))
        entry.update(status=rule_state.STATUS_COOLDOWN, fired_at="2026-11-20T18:30:00+00:00", fired_value="95.32",
                     last_posted_at="2026-11-20T18:30:00+00:00", posts=1, rearm_at="2026-11-23T05:00:00+00:00")
        desk_paths.ensure_runtime_dirs(self.paths)
        rule_state.save(self.paths, {rule_state.state_key(WMT, "TP80"): entry})
        self.ack_config("2026-11-20T19:49:00Z")
        code, body = self.run_json("expiry_day", EXPIRY_DAY_NOW, "--next-run-utc", EXPIRY_NEXT_RUN)
        wmt = next(row for row in body["payload"]["verdicts"] if row["group_id"] == WMT)
        self.assertEqual(("WATCH", ["TP80"]), (wmt["verdict"], wmt["cooldown"]))
        self.assertNotIn("EXIT SIGNAL %s" % WMT, body["payload"]["message"], "no second signal inside the re-arm")
        self.assertIn("cooling down: your rule TP80", body["payload"]["message"])

if __name__ == "__main__":
    unittest.main()
