"""``scripts/exit_check.py`` run in-process against a copied fixture desk root: the per-group verdict table of the
adopted rulebook (fired, unavailable, pending, suppressed and no-history items, the portfolio and underlying
sections), the exit code by the one verdict table (10 any EXIT_SIGNAL group, 20 any UNAVAILABLE group, 0 otherwise;
the sections never change it), ``--next-run-utc`` (a single observation suffices after the close), ``--rules`` with
``--dry-run`` (a candidate rulebook), ``--book`` (a saved book read back through ``exit_rules.book_from_json``), the
CONFIG CHANGED list, the error paths and the rule: exit_check never writes the rule state (it reads the routine's
record; ``book_check.py --post`` is the one writer)."""

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

from aegis_desk import alert_lint, book, contract, desk_paths, exit_rules, quote_cache, rule_state

import exit_check as tool  # the tool scripts/exit_check.py

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
CANDIDATE = os.path.join(ROOT, "tests", "fixtures", "rules", "candidate-1.1.0.toml")
EXAMPLE_RULES = os.path.join(ROOT, "rules", "examples", "exit_rules-1.0.0.toml")
REGULAR_NOW = "2026-11-03T19:50:00Z"
PREV_EOD_NOW = "2026-11-02T21:30:00Z"
T1_NOW = "2026-11-19T19:50:00Z"
EXPIRY_DAY_NOW = "2026-11-20T19:50:00Z"
AFTER_CLOSE_NEXT_RUN = "2026-11-20T21:35:00Z"
WMT, NVDA, SPY = "AG-20261020-01", "AG-20261022-02", "AG-20261028-03"
ADOPTED_SHA = "cc09859c896e22f84402ce8b686d0f79ec920b503b647754232247668b8bc5fe"
FIRST = ", already past this threshold at the desk's first observation"


def order_shaped(text: str) -> List[str]:
    """The lint's own forbidden-pattern table (``alert_lint.FORBIDDEN_PATTERNS``, which the journeys screen every
    tool's output with) matched against ``text``: no order-shaped word is spelled in this module."""
    return [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(text)]


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = tool.main(list(argv))
    return code, buffer.getvalue()


def run_json(argv: Sequence[str]) -> Tuple[int, Dict[str, Any]]:
    code, out = run_tool(argv)
    return code, json.loads(out)


def sha_of(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def group_entry(payload: Dict[str, Any], group_id: str) -> Dict[str, Any]:
    found = [entry for entry in payload["verdicts"] if entry["group_id"] == group_id]
    if len(found) != 1:
        raise AssertionError("no single verdict for %s in %r" % (group_id, payload["verdicts"]))
    return found[0]


def ids(results: Sequence[Dict[str, Any]]) -> List[str]:
    return [result["rule_id"] for result in results]


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-exit-check-tool-")
        self.tmp = os.path.realpath(self._tmp.name)
        self.root = os.path.join(self.tmp, "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, fixture_set: Optional[str] = "regular", now: str = REGULAR_NOW, json_mode: bool = True,
             extra: Sequence[str] = ()) -> List[str]:
        argv = ["--offline", "--desk-root", self.root, "--now", now]
        if fixture_set is not None:
            argv += ["--fixture-set", fixture_set]
        argv += list(extra)
        if json_mode:
            argv.append("--json")
        return argv

    def rewrite(self, name: str, old: str, new: str) -> None:
        path = os.path.join(self.root, name)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(1, text.count(old), old)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new))

    def ledger(self) -> List[Dict[str, Any]]:
        path = os.path.join(self.root, "data", "cache", "ledger.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]

    def build_run(self, fixture_set: str, now: str) -> book.BookRun:
        market = quote_cache.open_market_data(self.root, now=contract.parse_utc_instant(now), offline=True,
                                              fixture_set=fixture_set, timeout_s=5.0, user_agent="aegis-desk/test exit_check")
        return book.load_book(market)

    def save_book(self, fixture_set: str, now: str, name: str = "saved-book.json") -> str:
        path = os.path.join(self.tmp, name)
        run = self.build_run(fixture_set, now)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(contract.json_dumps(run.document) + "\n")
        return path

    def write_desk_state(self, document: Any) -> None:
        os.makedirs(self.paths.state_dir, exist_ok=True)
        with open(self.paths.desk_state_json, "w", encoding="utf-8") as handle:
            json.dump(document, handle)

    def acknowledged_hashes(self) -> Dict[str, str]:
        return {"desk_toml": sha_of(self.paths.desk_toml), "positions_csv": sha_of(self.paths.positions_csv),
                "rulebook": sha_of(os.path.join(self.paths.rules_dir, "exit_rules-1.0.0.toml"))}


class SchemaAndUsageTest(DeskCopy):
    def test_schema_describes_the_payload_and_the_exit_codes(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual("exit_check", body["tool"])
        described = body["envelope"]["payload"]
        self.assertEqual(set(tool.PAYLOAD_KEYS), set(described))
        for key in ("rulebook", "verdicts", "config_changed", "evaluation_utc"):
            self.assertIn(key, described, "the brief's payload keys")
        self.assertEqual(set(tool.GROUP_KEYS), set(described["verdicts"][0]))
        self.assertEqual(set(tool.RESULT_KEYS), set(described["verdicts"][0]["fired"][0]))
        self.assertEqual(set(tool.SECTION_KEYS), set(described["portfolio"]))
        for exit_code in ("0", "1", "2", "10", "20"):
            self.assertIn(exit_code, body["exit_codes"])
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual(set(described), set(payload))
        for entry in payload["verdicts"]:
            self.assertEqual(set(tool.GROUP_KEYS), set(entry), entry["group_id"])
            for kind in ("fired", "pending", "unavailable", "no_history", "suppressed"):
                for result in entry[kind]:
                    self.assertEqual(set(tool.RESULT_KEYS), set(result), result["rule_id"])
        self.assertEqual(set(tool.SECTION_KEYS), set(payload["portfolio"]))
        for section in payload["underlyings"]:
            self.assertEqual(set(tool.SECTION_KEYS), set(section), section["subject"])

    def test_usage_errors_are_exit_2(self) -> None:
        for argv in (["--json", "--offline"],
                     ["--json", "--desk-root", self.root, "--fixture-set", "regular"],
                     self.argv(extra=["--rules", CANDIDATE]),
                     self.argv(extra=["--next-run-utc", "2026-11-20 21:35"]),
                     self.argv(extra=["--next-run-utc", "2026-11-20T21:35:00+01:00"]),
                     self.argv(extra=["--next-run-utc", "2026-11-03T19:00:00Z"]),
                     self.argv(extra=["--next-run-utc", REGULAR_NOW]),
                     self.argv(extra=["--book", os.path.join(self.tmp, "book.json")]),
                     self.argv(extra=["--bogus"])):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(argv)
                self.assertEqual(2, ctx.exception.code)


class JourneysTest(DeskCopy):
    """The addendum's journeys 1-5 (decision 3 with decision 17's corrections, recomputed with the engine)."""

    def test_regular_is_exit_0_with_two_watch_groups_and_a_hold(self) -> None:
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertEqual(("ok", "exit_check", "tradier:sandbox", 15), (body["status"], body["tool"], body["source"],
                                                                        body["delay_min"]))
        payload = body["payload"]
        self.assertEqual({"version": "1.0.0", "sha": ADOPTED_SHA, "path": "rules/exit_rules-1.0.0.toml",
                          "adopted": True, "rules": 27}, payload["rulebook"])
        self.assertEqual({WMT: "WATCH", NVDA: "WATCH", SPY: "HOLD"}, payload["group_verdicts"])
        self.assertEqual({"EXIT_SIGNAL": 0, "UNAVAILABLE": 0, "WATCH": 2, "HOLD": 1}, payload["counts"])
        self.assertEqual(("2026-11-03T19:50:00+00:00", "2026-11-03", "open", False, None, False),
                         (payload["evaluation_utc"], payload["et_date"], payload["session"], payload["single_observation"],
                          payload["next_run_utc"], payload["dry_run"]))
        wmt = group_entry(payload, WMT)
        self.assertEqual(("WATCH", "WMT", "covered_call", "ok", False), (wmt["verdict"], wmt["underlying"],
                                                                         wmt["structure"], wmt["data_quality"],
                                                                         wmt["quarantined"]))
        self.assertEqual(["DTE21"], ids(wmt["fired"]))
        self.assertEqual((13, "21", "trading", "WATCH", "roll_review", True, True),
                         (wmt["fired"][0]["value"], wmt["fired"][0]["threshold"], wmt["fired"][0]["dte_unit"],
                          wmt["fired"][0]["verdict"], wmt["fired"][0]["plan"], wmt["fired"][0]["first_observation"],
                          wmt["fired"][0]["new_signal"]),
                         "the desk's first observation of a group opened before today, already past the threshold")
        self.assertEqual(["DELTA_WATCH", "DELTA_SIGNAL", "CAP"], ids(wmt["suppressed"]), "#assign-ok on the WMT call")
        self.assertEqual("0.49", wmt["suppressed"][0]["value"], "a suppressed rule still carries its value")
        self.assertEqual(["TP50", "TP80", "PIN3", "EXDIV", "AUTOEX", "USTOP", "EARN2"], wmt["clear"])
        self.assertEqual(([], []), (wmt["pending"], wmt["unavailable"]))
        nvda = group_entry(payload, NVDA)
        self.assertEqual((["DTE21"], ["DELTA_WATCH"], ["EARN2"]), (ids(nvda["fired"]), ids(nvda["pending"]),
                                                                   ids(nvda["suppressed"])))
        pending = nvda["pending"][0]
        self.assertEqual(("0.48", "0.30", 1, 2, "pending"), (pending["value"], pending["threshold"],
                                                            pending["observations"], pending["confirmations"],
                                                            pending["result"]))
        spy = group_entry(payload, SPY)
        self.assertEqual(("HOLD", [], [], []), (spy["verdict"], spy["fired"], spy["pending"], spy["unavailable"]))
        portfolio = payload["portfolio"]
        self.assertEqual(("portfolio", ["CONC"], ["DAILY"]), (portfolio["subject"], ids(portfolio["fired"]),
                                                              ids(portfolio["no_history"])))
        self.assertEqual(("26.32", "25", "percent"), (portfolio["fired"][0]["value"], portfolio["fired"][0]["threshold"],
                                                     portfolio["fired"][0]["unit"]))
        self.assertEqual(["NVDA", "SPY", "WMT"], [section["subject"] for section in payload["underlyings"]])
        for section in payload["underlyings"]:
            self.assertEqual(["MOVE5_DOWN", "MOVE5_UP"], ids(section["no_history"]), section["subject"])
        self.assertEqual({"rule_ids": ["MOVE5_DOWN", "MOVE5_UP", "DAILY"], "history_date": "2026-11-02",
                          "line": "no history: MOVE5_DOWN, MOVE5_UP and DAILY need the previous end-of-day snapshot "
                                  "(none for 2026-11-02)"}, payload["no_history"])
        self.assertEqual({"status": "missing", "written": False, "updates": 32}, payload["state"])
        self.assertEqual({"origin": "quotes", "path": None, "generated_at": "2026-11-03T19:50:00+00:00",
                          "as_of_label": "as of 14:31:12 ET", "data_exit_code": 0}, payload["book"])
        self.assertEqual([], payload["warnings"])
        self.assertEqual(1, len(self.ledger()), "one quotes request for the whole book")

    def test_expiry_day_with_the_after_close_next_run_is_exit_10(self) -> None:
        code, body = run_json(self.argv("expiry_day", EXPIRY_DAY_NOW, extra=["--next-run-utc", AFTER_CLOSE_NEXT_RUN]))
        self.assertEqual(10, code, body)
        self.assertEqual("fired", body["status"])
        payload = body["payload"]
        self.assertEqual((True, "2026-11-20T21:35:00+00:00"), (payload["single_observation"], payload["next_run_utc"]))
        self.assertEqual({WMT: "EXIT_SIGNAL", NVDA: "EXIT_SIGNAL", SPY: "WATCH"}, payload["group_verdicts"])
        wmt = group_entry(payload, WMT)
        self.assertEqual(["TP80", "DTE21", "PIN3"], ids(wmt["fired"]))
        tp80 = wmt["fired"][0]
        self.assertEqual(("95.32", "80", "percent", "EXIT_SIGNAL", "close", "E", True),
                         (tp80["value"], tp80["threshold"], tp80["unit"], tp80["verdict"], tp80["plan"],
                          tp80["quote_grade"], tp80["new_signal"]))
        self.assertIn("quote grade E: the conservative side stands", tp80["notes"])
        self.assertIn({"name": "fresh_ask", "value": "0.16", "source": "legs[row 4].fresh_ask",
                       "as_of": tp80["as_of"], "grade": "E", "note": "quote grade E: the conservative side stands"},
                      tp80["inputs"])
        nvda = group_entry(payload, NVDA)
        self.assertEqual(["DTE21", "DELTA_WATCH", "DELTA_SIGNAL", "PIN3", "CAP"], ids(nvda["fired"]))
        self.assertEqual("0.96", nvda["fired"][2]["value"])
        self.assertEqual("2026-11-19", payload["no_history"]["history_date"])
        self.assertEqual(["DAILY"], ids(payload["portfolio"]["unavailable"]),
                         "data, not history: the WMT group is unavailable (decision 11)")
        self.assertEqual(20, payload["book"]["data_exit_code"], "the book says 20; the verdicts are rule-centric")

    def test_expiry_day_without_next_run_leaves_tp80_pending(self) -> None:
        code, body = run_json(self.argv("expiry_day", EXPIRY_DAY_NOW))
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual({WMT: "WATCH", NVDA: "WATCH", SPY: "WATCH"}, payload["group_verdicts"])
        wmt = group_entry(payload, WMT)
        self.assertEqual((["DTE21"], ["TP80", "PIN3"]), (ids(wmt["fired"]), ids(wmt["pending"])))
        self.assertEqual((1, 2, False), (wmt["pending"][0]["observations"], wmt["pending"][0]["confirmations"],
                                         wmt["pending"][0]["new_signal"]))
        code, text = run_tool(self.argv("expiry_day", EXPIRY_DAY_NOW, json_mode=False))
        self.assertEqual(0, code, text)
        self.assertIn("  pending: TP80 pct_captured 95.32 >= 80 percent, observation 1 of 2 (EXIT_SIGNAL, plan close)",
                      text.splitlines())

    def test_stale_is_exit_20_with_every_reason(self) -> None:
        code, body = run_json(self.argv("stale"))
        self.assertEqual(20, code, body)
        self.assertEqual("unavailable", body["status"])
        payload = body["payload"]
        self.assertEqual({WMT: "UNAVAILABLE", NVDA: "UNAVAILABLE", SPY: "UNAVAILABLE"}, payload["group_verdicts"])
        wmt = group_entry(payload, WMT)
        self.assertEqual((["DTE21"], ["TP80", "USTOP"]), (ids(wmt["fired"]), ids(wmt["unavailable"])),
                         "E rules still evaluate; every unavailable rule is listed")
        self.assertEqual("row 4 WMT261120C00105000: quote grade F (stale): no fresh ask", wmt["unavailable"][0]["reason"])
        nvda = group_entry(payload, NVDA)
        self.assertEqual(["TP80", "DELTA_WATCH", "DELTA_SIGNAL", "CAP"], ids(nvda["unavailable"]))
        spy = group_entry(payload, SPY)
        self.assertEqual(["MONETIZE", "FLOOR"], ids(spy["unavailable"]))
        self.assertEqual(["DAILY", "CONC"], ids(payload["portfolio"]["unavailable"]))
        code, text = run_tool(self.argv("stale", json_mode=False))
        self.assertEqual(20, code)
        self.assertIn("AG-20261028-03 SPY protective_put: UNAVAILABLE", text.splitlines())
        self.assertIn("  unavailable: FLOOR: underlying SPY: no usable mark (quote grade F)", text.splitlines())

    def test_dry_run_of_a_candidate_writes_no_state(self) -> None:
        code, body = run_json(self.argv(extra=["--dry-run", "--rules", CANDIDATE]))
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual(("1.1.0", sha_of(CANDIDATE), False, True),
                         (payload["rulebook"]["version"], payload["rulebook"]["sha"], payload["rulebook"]["adopted"],
                          payload["dry_run"]))
        self.assertEqual(os.path.abspath(CANDIDATE), payload["rulebook"]["path"])
        self.assertEqual({WMT: "WATCH", NVDA: "WATCH", SPY: "HOLD"}, payload["group_verdicts"])
        self.assertFalse(os.path.exists(self.paths.rule_state_json), "no state file written")

    def test_t1_reports_pin3_pending_and_exdiv_not_applicable(self) -> None:
        code, body = run_json(self.argv("t1", T1_NOW))
        self.assertEqual(0, code, body)
        wmt = group_entry(body["payload"], WMT)
        self.assertEqual((["DTE21"], ["PIN3"]), (ids(wmt["fired"]), ids(wmt["pending"])))
        self.assertIn("EXDIV", wmt["clear"], "not applicable: the call is gone before the last cum-dividend day")
        self.assertEqual(["DELTA_WATCH", "DELTA_SIGNAL", "CAP"], ids(wmt["suppressed"]))


class StateTest(DeskCopy):
    """exit_check reads the routine's rule state and never writes it."""

    def seed_tp80_observation(self) -> bytes:
        run = self.build_run("expiry_day", EXPIRY_DAY_NOW)
        adopted, rules = exit_rules.load_adopted_rulebook(self.paths)
        evaluation = exit_rules.evaluate(rules, run.document, now=run.market.now, session=run.session,
                                         calendar=run.calendar, state={}, next_run_utc=None, rows=run.positions.rows)
        tp80 = [result for result in evaluation.group(WMT).pending if result.rule_id == "TP80"][0]
        entry = rule_state.new_entry(rules.rules_sha["TP80"], datetime.date(2026, 10, 20))
        earlier = tp80.as_of - datetime.timedelta(minutes=30)
        entry.update(status=rule_state.STATUS_PENDING, observations=[{"as_of": rule_state.iso_instant(earlier),
                                                                      "value": "95.10"}])
        self.assertIsNone(rule_state.entry_problem(entry))
        rule_state.save(self.paths, {rule_state.state_key(WMT, "TP80"): entry})
        with open(self.paths.rule_state_json, "rb") as handle:
            return handle.read()

    def test_a_stored_observation_confirms_tp80_and_the_file_is_untouched(self) -> None:
        before = self.seed_tp80_observation()
        code, body = run_json(self.argv("expiry_day", EXPIRY_DAY_NOW))
        self.assertEqual(10, code, body)
        payload = body["payload"]
        wmt = group_entry(payload, WMT)
        self.assertEqual(["TP80", "DTE21"], ids(wmt["fired"]))
        self.assertEqual((2, 2, True, "fired"), (wmt["fired"][0]["observations"], wmt["fired"][0]["confirmations"],
                                                 wmt["fired"][0]["new_signal"], wmt["fired"][0]["state_status"]))
        self.assertEqual(("ok", False), (payload["state"]["status"], payload["state"]["written"]))
        with open(self.paths.rule_state_json, "rb") as handle:
            self.assertEqual(before, handle.read(), "exit_check never writes the rule state")

    def test_a_plain_run_creates_no_state_file(self) -> None:
        code, _ = run_json(self.argv("expiry_day", EXPIRY_DAY_NOW, extra=["--next-run-utc", AFTER_CLOSE_NEXT_RUN]))
        self.assertEqual(10, code)
        self.assertFalse(os.path.exists(self.paths.rule_state_json))

    def test_a_corrupt_state_file_is_reported_and_every_rule_starts_armed(self) -> None:
        os.makedirs(self.paths.state_dir, exist_ok=True)
        with open(self.paths.rule_state_json, "w", encoding="utf-8") as handle:
            handle.write('{"half": ')
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertEqual(("corrupt", False), (body["payload"]["state"]["status"], body["payload"]["state"]["written"]))


class SavedBookTest(DeskCopy):
    def test_a_saved_book_gives_the_verdicts_of_the_built_one(self) -> None:
        path = self.save_book("regular", REGULAR_NOW)
        code, built = run_json(self.argv())
        self.assertEqual(0, code, built)
        code, saved = run_json(["--offline", "--desk-root", self.root, "--now", REGULAR_NOW, "--book", path, "--json"])
        self.assertEqual(0, code, saved)
        self.assertEqual(built["payload"]["verdicts"], saved["payload"]["verdicts"])
        self.assertEqual(built["payload"]["portfolio"], saved["payload"]["portfolio"])
        self.assertEqual({"origin": "file", "path": path, "generated_at": "2026-11-03T19:50:00+00:00",
                          "as_of_label": "as of 14:31:12 ET", "data_exit_code": 0}, saved["payload"]["book"])
        self.assertEqual(("tradier:sandbox", 15), (saved["source"], saved["delay_min"]))

    def test_a_saved_book_is_evaluated_at_its_own_instant(self) -> None:
        # The 2026-11-02 close snapshot: a closed session, so one observation suffices; the rules see that day.
        path = self.save_book("pre_open", PREV_EOD_NOW)
        code, body = run_json(["--desk-root", self.root, "--now", T1_NOW, "--book", path, "--json"])
        self.assertIn(code, (0, 10, 20))
        payload = body["payload"]
        self.assertEqual(("2026-11-02T21:30:00+00:00", "2026-11-02", "closed", True),
                         (payload["evaluation_utc"], payload["et_date"], payload["session"], payload["single_observation"]))
        self.assertEqual("2026-11-19T19:50:00+00:00", body["generated_at"], "the envelope carries the run's own instant")

    def test_a_snapshot_written_by_book_reads_back(self) -> None:
        run = self.build_run("pre_open", PREV_EOD_NOW)
        path = book.write_snapshot(self.paths, run.document, run.session.et_date)
        code, body = run_json(["--desk-root", self.root, "--book", path, "--json"])
        self.assertIn(code, (0, 10, 20), body)
        self.assertEqual("file", body["payload"]["book"]["origin"])

    def test_missing_corrupt_and_malformed_books_are_exit_1_book_invalid(self) -> None:
        missing = os.path.join(self.tmp, "missing.json")
        corrupt = os.path.join(self.tmp, "corrupt.json")
        malformed = os.path.join(self.tmp, "malformed.json")
        with open(corrupt, "w", encoding="utf-8") as handle:
            handle.write('{"generated_at": ')
        with open(malformed, "w", encoding="utf-8") as handle:
            json.dump({"legs": []}, handle)
        for path in (missing, corrupt, malformed):
            with self.subTest(path=os.path.basename(path)):
                code, body = run_json(["--desk-root", self.root, "--book", path, "--json"])
                self.assertEqual((1, "error", "book_invalid"), (code, body["status"], body["error"]["code"]))
        self.assertFalse(os.path.exists(corrupt), "state.read_json quarantined the corrupt book")

    def test_saved_book_mode_without_positions_warns_and_suppresses_nothing(self) -> None:
        path = self.save_book("regular", REGULAR_NOW)
        os.remove(self.paths.positions_csv)
        code, body = run_json(["--desk-root", self.root, "--book", path, "--json"])
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual([], group_entry(payload, WMT)["suppressed"], "no rows: a real condition is never switched off")
        self.assertIn(exit_rules.WARNING_NO_ROWS, payload["warnings"])
        self.assertTrue(any("positions.csv" in warning for warning in payload["warnings"]), payload["warnings"])


class ConfigChangedTest(DeskCopy):
    def test_without_acknowledged_hashes_every_file_is_listed(self) -> None:
        code, body = run_json(self.argv())
        self.assertEqual(0, code, "CONFIG CHANGED never changes the exit code")
        hashes = self.acknowledged_hashes()
        self.assertEqual([{"name": "desk_toml", "old": None, "new": hashes["desk_toml"]},
                          {"name": "positions_csv", "old": None, "new": hashes["positions_csv"]},
                          {"name": "rulebook", "old": None, "new": hashes["rulebook"]}], body["payload"]["config_changed"])
        code, text = run_tool(self.argv(json_mode=False))
        self.assertEqual(0, code)
        line = [item for item in text.splitlines() if item.startswith("CONFIG CHANGED")]
        self.assertEqual(1, len(line), text)
        self.assertIn("desk.toml: not acknowledged -> sha %s" % hashes["desk_toml"][:8], line[0])
        self.assertIn("acknowledge with ack.py config --owner-tapped", line[0])

    def test_acknowledged_hashes_clear_the_list_and_an_edit_brings_one_back(self) -> None:
        hashes = self.acknowledged_hashes()
        self.write_desk_state({"acknowledged_hashes": hashes})
        code, body = run_json(self.argv())
        self.assertEqual((0, []), (code, body["payload"]["config_changed"]))
        self.rewrite("positions.csv", "core holding", "core position")
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertEqual([{"name": "positions_csv", "old": hashes["positions_csv"], "new": sha_of(self.paths.positions_csv)}],
                         body["payload"]["config_changed"])
        code, text = run_tool(self.argv(json_mode=False))
        self.assertTrue(any(line.startswith("CONFIG CHANGED: positions.csv: sha %s -> sha " % hashes["positions_csv"][:8])
                            for line in text.splitlines()), text)

    def test_config_hashes_name_the_adopted_target_file(self) -> None:
        hashes = tool.config_hashes(self.paths)
        # PR 3b (designer C's D3): structuring_toml is hashed too, None while the fixture desk holds no such file.
        self.assertEqual(dict(self.acknowledged_hashes(), structuring_toml=None), hashes)
        os.remove(self.paths.adopted_file)
        self.assertIsNone(tool.config_hashes(self.paths)["rulebook"], "no adoption: no adopted file to hash")


class RulebookErrorsTest(DeskCopy):
    def test_no_adoption_is_exit_1_not_adopted(self) -> None:
        os.remove(self.paths.adopted_file)
        code, body = run_json(self.argv())
        self.assertEqual((1, "error", "not_adopted"), (code, body["status"], body["error"]["code"]))
        self.assertEqual([], self.ledger(), "the rulebook is read before any quote is fetched")

    def test_an_edited_adopted_rulebook_is_exit_1_rules_tampered(self) -> None:
        self.rewrite(os.path.join("rules", "exit_rules-1.0.0.toml"), "value = 80, unit", "value = 70, unit")
        code, body = run_json(self.argv())
        self.assertEqual((1, "rules_tampered"), (code, body["error"]["code"]))

    def test_an_invalid_candidate_is_exit_1_config_invalid(self) -> None:
        path = os.path.join(self.tmp, "broken.toml")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write('schema_version = 1\nrulebook_version = "1.2.0"\ntitle = "x"\n')
        code, body = run_json(self.argv(extra=["--dry-run", "--rules", path]))
        self.assertEqual((1, "config_invalid"), (code, body["error"]["code"]))
        code, body = run_json(self.argv(extra=["--dry-run", "--rules", os.path.join(self.tmp, "absent.toml")]))
        self.assertEqual((1, "rules_missing"), (code, body["error"]["code"]))

    def test_the_example_file_evaluates_as_a_dry_run(self) -> None:
        # Evaluation does not lint: an example rulebook (origin "example") can be tried as a dry run.
        code, body = run_json(self.argv(extra=["--dry-run", "--rules", EXAMPLE_RULES]))
        self.assertEqual(0, code, body)
        self.assertEqual({WMT: "WATCH", NVDA: "WATCH", SPY: "HOLD"}, body["payload"]["group_verdicts"])


class PlumbingTest(DeskCopy):
    def test_a_quarantined_group_is_unavailable_with_its_reasons(self) -> None:
        self.rewrite("positions.csv", "2026-11-20,CALL,150", "2026-11-20,CALLS,150")
        code, body = run_json(self.argv())
        self.assertEqual(20, code, body)
        nvda = group_entry(body["payload"], NVDA)
        self.assertEqual(("UNAVAILABLE", True), (nvda["verdict"], nvda["quarantined"]))
        self.assertTrue(nvda["reasons"], nvda)
        self.assertEqual(([], [], []), (nvda["fired"], nvda["unavailable"], nvda["clear"]))
        code, text = run_tool(self.argv(json_mode=False))
        self.assertEqual(20, code)
        self.assertIn("AG-20261022-02 quarantined: UNAVAILABLE", text.splitlines())

    def test_production_host_is_exit_20(self) -> None:
        self.rewrite("desk.toml", 'host = "sandbox"', 'host = "production"')
        code, body = run_json(["--json", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
        self.assertEqual((20, "unavailable", "production_host"), (code, body["status"], body["error"]["code"]))

    def test_invalid_config_and_positions_are_exit_1(self) -> None:
        self.rewrite("desk.toml", "grace_min = 5", "grace_min = 31")
        code, body = run_json(self.argv())
        self.assertEqual((1, "config_invalid"), (code, body["error"]["code"]))

    def test_positions_errors_are_exit_1(self) -> None:
        self.rewrite("positions.csv", "cost_basis_total,", "cost_basis_totl,")
        code, body = run_json(self.argv())
        self.assertEqual((1, "positions_invalid"), (code, body["error"]["code"]))
        os.remove(self.paths.positions_csv)
        code, body = run_json(self.argv())
        self.assertEqual((1, "positions_missing"), (code, body["error"]["code"]))

    def test_a_bad_calendar_is_exit_1(self) -> None:
        with open(self.paths.calendar_toml, "a", encoding="utf-8") as handle:
            handle.write("\nunknown_key = 1\n")
        code, body = run_json(self.argv())
        self.assertEqual((1, "calendar_invalid"), (code, body["error"]["code"]))
        path = os.path.join(self.tmp, "book.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{}")
        code, body = run_json(["--desk-root", self.root, "--book", path, "--json"])
        self.assertEqual(1, code, body)

    def test_an_empty_desk_root_is_exit_1_not_adopted(self) -> None:
        empty = os.path.join(self.tmp, "empty")
        os.mkdir(empty)
        code, body = run_json(["--json", "--desk-root", empty, "--now", REGULAR_NOW])
        self.assertEqual((1, "not_adopted"), (code, body["error"]["code"]))


class ProseTest(DeskCopy):
    def test_the_verdict_table(self) -> None:
        code, text = run_tool(self.argv(json_mode=False))
        self.assertEqual(0, code, text)
        lines = text.splitlines()
        self.assertEqual("exit check: rules 1.0.0, sha cc09859c (adopted), on the book built from quotes at "
                         "2026-11-03 19:50 UTC (session open, as of 14:31:12 ET)", lines[0])
        for needle in ("AG-20261020-01 WMT covered_call: WATCH",
                       "  fired: DTE21 dte 13 <= 21 trading days%s (WATCH, plan roll_review)" % FIRST,
                       "  suppressed: DELTA_WATCH (suppressed by #assign-ok on row 4)",
                       "AG-20261022-02 NVDA covered_call: WATCH",
                       "  pending: DELTA_WATCH abs_delta 0.48 >= 0.30 delta, observation 1 of 2 (WATCH, plan hold_review)",
                       "  suppressed: EARN2 (suppressed by #earnings-ok on row 6)",
                       "AG-20261028-03 SPY protective_put: HOLD",
                       "portfolio: fired CONC max_concentration_pct 26.32 >= 25 percent%s (WATCH, plan hold_review)" % FIRST,
                       "no history: MOVE5_DOWN, MOVE5_UP and DAILY need the previous end-of-day snapshot (none for 2026-11-02)",
                       "verdicts: EXIT_SIGNAL 0, UNAVAILABLE 0, WATCH 2, HOLD 1 (the portfolio and underlying sections "
                       "never set a group verdict or the exit code)",
                       "rule state: read state/rule_state.json (missing); exit_check writes nothing (book_check.py --post "
                       "records the transitions)"):
            self.assertIn(needle, lines)
        self.assertEqual("Tradier sandbox, delayed 15 min, as of 19:31 UTC", lines[-1])
        self.assertEqual([], order_shaped(text), text)

    def test_the_exit_signal_table_and_the_json_never_carry_order_shaped_text(self) -> None:
        for json_mode in (False, True):
            code, text = run_tool(self.argv("expiry_day", EXPIRY_DAY_NOW, json_mode=json_mode,
                                            extra=["--next-run-utc", AFTER_CLOSE_NEXT_RUN]))
            self.assertEqual(10, code)
            self.assertEqual([], order_shaped(text), text)
        code, text = run_tool(self.argv("expiry_day", EXPIRY_DAY_NOW, json_mode=False,
                                        extra=["--next-run-utc", AFTER_CLOSE_NEXT_RUN]))
        lines = text.splitlines()
        self.assertIn("AG-20261020-01 WMT covered_call: EXIT_SIGNAL", lines)
        self.assertIn("  fired: TP80 pct_captured 95.32 >= 80 percent%s (EXIT_SIGNAL, plan close)" % FIRST, lines)
        self.assertIn("  fired: CAP stock_above_short_call true and dte 0 <= 21 trading days%s (WATCH, plan roll_review)"
                      % FIRST, lines)
        self.assertIn("portfolio: unavailable DAILY: day P/L unavailable: row 4 WMT261120C00105000: no usable mark "
                      "(quote grade E, fresh)", lines)

    def test_a_dry_run_names_the_candidate(self) -> None:
        code, text = run_tool(self.argv(json_mode=False, extra=["--dry-run", "--rules", CANDIDATE]))
        self.assertEqual(0, code, text)
        self.assertTrue(text.splitlines()[0].startswith("exit check (dry run): rules 1.1.0, sha %s (candidate %s)"
                                                        % (sha_of(CANDIDATE)[:8], os.path.abspath(CANDIDATE))), text)


class ConditionTextTest(unittest.TestCase):
    def condition(self, metric: str, op: str, threshold: Any, value: Any, unit: Optional[str] = None,
                  dte_unit: Optional[str] = None) -> exit_rules.ConditionResult:
        return exit_rules.ConditionResult(metric=metric, op=op, threshold=threshold, unit=unit, dte_unit=dte_unit,
                                          truth=exit_rules.TRUE, kind=exit_rules.KIND_VALUE, value=value, reason="")

    def test_condition_words(self) -> None:
        from decimal import Decimal
        cases = ((self.condition("pct_captured", ">=", Decimal("80"), Decimal("95.32"), "percent"),
                  "pct_captured 95.32 >= 80 percent"),
                 (self.condition("dte", "<=", Decimal("21"), 13, dte_unit="trading"), "dte 13 <= 21 trading days"),
                 (self.condition("abs_delta", ">=", Decimal("0.30"), Decimal("0.48")), "abs_delta 0.48 >= 0.30 delta"),
                 (self.condition("loss_multiple_of_credit", ">=", Decimal("2"), Decimal("2.10"), "multiple"),
                  "loss_multiple_of_credit 2.10 >= 2 x"),
                 (self.condition("day_pl", "<=", Decimal("-1000"), Decimal("-1250.00"), "usd"), "day_pl -1250.00 <= -1000 usd"),
                 (self.condition("stock_above_short_call", "is_true", None, True), "stock_above_short_call true"),
                 (self.condition("days_to_ex_dividend", "<=", Decimal("5"), 2), "days_to_ex_dividend 2 <= 5 days"),
                 (self.condition("pct_captured", ">=", Decimal("1E+2"), None, "percent"), "pct_captured unknown >= 100 percent"))
        for condition, expected in cases:
            with self.subTest(expected=expected):
                self.assertEqual(expected, tool.condition_text(condition))



class CooldownTest(DeskCopy):
    """A rule the routine recorded in cooldown is reported as ``cooldown`` (true again before its re-arm) and sets no
    verdict: exit_check reads the same state book_check posts from (the lens-1 review)."""

    def test_a_cooled_take_profit_is_listed_and_sets_no_verdict(self) -> None:
        _adopted, rules = exit_rules.load_adopted_rulebook(self.paths)
        entry = rule_state.new_entry(rules.rules_sha["TP80"], datetime.date(2026, 10, 20))
        entry.update(status=rule_state.STATUS_COOLDOWN, fired_at="2026-11-20T18:30:00+00:00", fired_value="95.32",
                     last_posted_at="2026-11-20T18:30:00+00:00", posts=1, rearm_at="2026-11-23T05:00:00+00:00")
        desk_paths.ensure_runtime_dirs(self.paths)
        rule_state.save(self.paths, {rule_state.state_key(WMT, "TP80"): entry})
        code, body = run_json(self.argv("expiry_day", EXPIRY_DAY_NOW, extra=["--next-run-utc", AFTER_CLOSE_NEXT_RUN]))
        wmt = group_entry(body["payload"], WMT)
        self.assertEqual(("WATCH", ["TP80"], ["DTE21", "PIN3"]), (wmt["verdict"], ids(wmt["cooldown"]), ids(wmt["fired"])))
        self.assertEqual("cooldown", wmt["cooldown"][0]["result"])
        self.assertIn("2026-11-23 00:00 ET", wmt["cooldown"][0]["reason"])
        self.assertEqual(10, code, "the NVDA call's DELTA_SIGNAL still makes the run an EXIT_SIGNAL")
        _code, text = run_tool(self.argv("expiry_day", EXPIRY_DAY_NOW, json_mode=False,
                                         extra=["--next-run-utc", AFTER_CLOSE_NEXT_RUN]))
        self.assertIn("  cooldown: TP80 pct_captured 95.32 >= 80 percent", text)


if __name__ == "__main__":
    unittest.main()
