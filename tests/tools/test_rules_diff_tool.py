"""``scripts/rules_diff.py`` run in-process against a copied fixture desk root: the adopted and a candidate rulebook
evaluated on the same book (built from quotes, a ``--book`` file or the newest ``data/book/`` snapshot) with a scratch
rule state, the per-group verdict changes (exit 10), the rule-level and section differences (reported, never the exit
code), the rule set changes (added, removed, changed by per-rule sha), exit 20 when nothing changed but a group is
UNAVAILABLE, the error paths, and nothing written (the state file is neither read nor touched)."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Sequence, Tuple
from unittest import mock

from aegis_desk import alert_lint, book, contract, desk_paths, quote_cache

import rules_diff as tool  # the tool scripts/rules_diff.py

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
CANDIDATE = os.path.join(ROOT, "tests", "fixtures", "rules", "candidate-1.1.0.toml")
REGULAR_NOW = "2026-11-03T19:50:00Z"
PREV_EOD_NOW = "2026-11-02T21:30:00Z"
EXPIRY_DAY_NOW = "2026-11-20T19:50:00Z"
AFTER_CLOSE_NEXT_RUN = "2026-11-20T21:35:00Z"
WMT, NVDA, SPY = "AG-20261020-01", "AG-20261022-02", "AG-20261028-03"
ADOPTED_SHA = "cc09859c896e22f84402ce8b686d0f79ec920b503b647754232247668b8bc5fe"


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


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-rules-diff-tool-")
        self.tmp = os.path.realpath(self._tmp.name)
        self.root = os.path.join(self.tmp, "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)
        with open(os.path.join(self.root, "rules", "exit_rules-1.0.0.toml"), encoding="utf-8") as handle:
            self.adopted_text = handle.read()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, candidate: str = CANDIDATE, fixture_set: Optional[str] = "regular", now: str = REGULAR_NOW,
             json_mode: bool = True, extra: Sequence[str] = ()) -> List[str]:
        argv = ["--offline", "--desk-root", self.root, "--now", now, "--candidate", candidate]
        if fixture_set is not None:
            argv += ["--fixture-set", fixture_set]
        argv += list(extra)
        if json_mode:
            argv.append("--json")
        return argv

    def candidate(self, version: str, edits: Sequence[Tuple[str, str, str]] = (), drop: Sequence[str] = (),
                  append: str = "") -> str:
        """A candidate written to the temp directory: the adopted file with ``edits`` (rule id, old, new) inside the
        named rules, the ``drop``ped rules removed and ``append`` added at the end."""
        text = self.adopted_text.replace('rulebook_version = "1.0.0"', 'rulebook_version = "%s"' % version)
        for rule_id, old, new in edits:
            head, sep, tail = text.partition('id = "%s"\n' % rule_id)
            self.assertTrue(sep, rule_id)
            self.assertIn(old, tail)
            text = head + sep + tail.replace(old, new, 1)
        for rule_id in drop:
            marker = '[[rule]]\nid = "%s"\n' % rule_id
            self.assertEqual(1, text.count(marker), rule_id)
            head, _, tail = text.partition(marker)
            following = tail.find("[[rule]]\n")
            text = head + ("" if following < 0 else tail[following:])
        text += append
        path = os.path.join(self.tmp, "candidate-%s.toml" % version)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def dte5(self) -> str:
        return self.candidate("1.2.0", [("DTE21", "value = 21", "value = 5")])

    def snapshot(self) -> str:
        market = quote_cache.open_market_data(self.root, now=contract.parse_utc_instant(PREV_EOD_NOW), offline=True,
                                              fixture_set="pre_open", timeout_s=5.0, user_agent="aegis-desk/test rules_diff")
        run = book.load_book(market)
        return book.write_snapshot(self.paths, run.document, run.session.et_date)


class SchemaAndUsageTest(DeskCopy):
    def test_schema(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual("rules_diff", body["tool"])
        described = body["envelope"]["payload"]
        self.assertEqual(set(tool.PAYLOAD_KEYS), set(described))
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertEqual(set(described), set(body["payload"]))
        for key in ("changes", "rule_changes", "section_changes"):
            self.assertIsInstance(body["payload"][key], list)

    def test_usage_errors_are_exit_2(self) -> None:
        book_path = os.path.join(self.tmp, "book.json")
        for argv in (["--json", "--offline", "--desk-root", self.root],
                     ["--json", "--desk-root", self.root, "--candidate", CANDIDATE, "--fixture-set", "regular"],
                     self.argv(fixture_set=None, extra=["--book", book_path, "--latest-snapshot"]),
                     self.argv(extra=["--book", book_path]),
                     self.argv(extra=["--latest-snapshot"]),
                     self.argv(extra=["--next-run-utc", "tomorrow"]),
                     self.argv(extra=["--next-run-utc", "2026-11-03T19:00:00Z"])):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(argv)
                self.assertEqual(2, ctx.exception.code)


class JourneysTest(DeskCopy):
    def test_the_candidate_changes_no_verdict_on_the_regular_day(self) -> None:
        # Journey 11: TP50 at 60 percent; TP50 needs more than 21 trading days and none is left, so nothing moves.
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertEqual(("ok", "rules_diff"), (body["status"], body["tool"]))
        payload = body["payload"]
        self.assertEqual({"version": "1.0.0", "sha": ADOPTED_SHA, "path": "rules/exit_rules-1.0.0.toml",
                          "adopted": True, "rules": 27}, payload["adopted"])
        self.assertEqual({"version": "1.1.0", "sha": sha_of(CANDIDATE), "path": CANDIDATE, "adopted": False,
                          "rules": 27}, payload["candidate"])
        self.assertEqual({"added": [], "removed": [], "changed": ["TP50"], "unchanged": 26}, payload["rules"])
        self.assertEqual(([], 0, [], []), (payload["changes"], payload["verdicts_changed"], payload["rule_changes"],
                                           payload["section_changes"]))
        self.assertEqual({WMT: "WATCH", NVDA: "WATCH", SPY: "HOLD"}, payload["adopted_verdicts"])
        self.assertEqual(payload["adopted_verdicts"], payload["candidate_verdicts"])
        self.assertEqual(("scratch", "quotes", "open"), (payload["state"], payload["book"]["origin"], payload["session"]))

    def test_the_candidate_changes_no_verdict_on_expiry_day(self) -> None:
        for extra in ((), ("--next-run-utc", AFTER_CLOSE_NEXT_RUN)):
            with self.subTest(extra=extra):
                code, body = run_json(self.argv(fixture_set="expiry_day", now=EXPIRY_DAY_NOW, extra=extra))
                self.assertEqual(0, code, body)
                self.assertEqual(0, body["payload"]["verdicts_changed"])

    def test_a_tighter_dte_rule_changes_two_verdicts(self) -> None:
        candidate = self.dte5()
        code, body = run_json(self.argv(candidate=candidate))
        self.assertEqual(10, code, body)
        self.assertEqual("fired", body["status"])
        payload = body["payload"]
        self.assertEqual({"added": [], "removed": [], "changed": ["DTE21"], "unchanged": 26}, payload["rules"])
        self.assertEqual([{"group_id": WMT, "trade_id": WMT, "underlying": "WMT", "structure": "covered_call",
                           "before": "WATCH", "after": "HOLD"},
                          {"group_id": NVDA, "trade_id": NVDA, "underlying": "NVDA", "structure": "covered_call",
                           "before": "WATCH", "after": "HOLD"}], payload["changes"])
        self.assertEqual(2, payload["verdicts_changed"])
        self.assertEqual([{"group_id": WMT, "rule_id": "DTE21", "before": "fired", "after": "clear"},
                          {"group_id": NVDA, "rule_id": "DTE21", "before": "fired", "after": "clear"}],
                         payload["rule_changes"])
        code, text = run_tool(self.argv(candidate=candidate, json_mode=False))
        self.assertEqual(10, code, text)
        lines = text.splitlines()
        self.assertIn("AG-20261020-01 WMT covered_call: WATCH -> HOLD", lines)
        self.assertIn("rule result: AG-20261022-02 DTE21 fired -> clear", lines)
        self.assertIn("rules: changed DTE21; added none; removed none; 26 unchanged", lines)
        self.assertIn("verdicts changed: 2", lines)
        self.assertEqual("Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of 19:31 UTC", lines[-1])
        self.assertEqual([], order_shaped(text), text)

    def test_rules_added_and_removed_are_reported_and_sections_never_set_the_exit_code(self) -> None:
        extra_rule = ('\n[[rule]]\nid = "TP90"\ntitle = "Take profit at 90 percent of the credit"\nrole = "take_profit"\n'
                      'scope = "group"\napplies_to = ["cash_secured_put"]\ninput_class = "M"\nwhen = [\n'
                      '  { metric = "pct_captured", op = ">=", value = 90, unit = "percent" },\n]\n'
                      'verdict = "EXIT_SIGNAL"\nplan = "close"\norigin = "owner-entered"\nsource = "owner threshold"\n')
        candidate = self.candidate("1.3.0", drop=["CONC"], append=extra_rule)
        code, body = run_json(self.argv(candidate=candidate))
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual({"added": ["TP90"], "removed": ["CONC"], "changed": [], "unchanged": 26}, payload["rules"])
        self.assertEqual([], payload["changes"])
        self.assertEqual([{"subject": "portfolio", "rule_id": "CONC", "before": "fired", "after": None}],
                         payload["section_changes"])

    def test_stale_quotes_are_exit_20_when_no_verdict_changed(self) -> None:
        code, body = run_json(self.argv(fixture_set="stale"))
        self.assertEqual(20, code, body)
        payload = body["payload"]
        self.assertEqual((0, [WMT, NVDA, SPY]), (payload["verdicts_changed"], payload["unavailable_groups"]))
        code, body = run_json(self.argv(candidate=self.dte5(), fixture_set="stale"))
        self.assertEqual(20, code, "UNAVAILABLE stays UNAVAILABLE whatever DTE21 says")


class BookSourcesTest(DeskCopy):
    def test_no_snapshot_is_exit_20(self) -> None:
        code, body = run_json(self.argv(fixture_set=None, extra=["--latest-snapshot"]))
        self.assertEqual((20, "unavailable", "snapshot_missing"), (code, body["status"], body["error"]["code"]))

    def test_the_newest_snapshot_is_evaluated_at_its_own_instant(self) -> None:
        path = self.snapshot()
        older = os.path.join(self.paths.book_dir, "2026-10-30.json")
        shutil.copyfile(path, older)
        with open(os.path.join(self.paths.book_dir, "notes.json"), "w", encoding="utf-8") as handle:
            handle.write("{}")
        code, body = run_json(self.argv(candidate=self.dte5(), fixture_set=None, extra=["--latest-snapshot"]))
        self.assertEqual(10, code, body)
        payload = body["payload"]
        self.assertEqual(("file", path, "2026-11-02T21:30:00+00:00", "closed", True),
                         (payload["book"]["origin"], payload["book"]["path"], payload["evaluation_utc"],
                          payload["session"], payload["single_observation"]))
        self.assertEqual([WMT], [change["group_id"] for change in payload["changes"]],
                         "at the close NVDA's DELTA_WATCH fires on one observation and keeps the group at WATCH")

    def test_a_book_file(self) -> None:
        path = self.snapshot()
        code, body = run_json(self.argv(fixture_set=None, extra=["--book", path]))
        self.assertEqual(0, code, body)
        self.assertEqual(path, body["payload"]["book"]["path"])
        code, body = run_json(self.argv(fixture_set=None, extra=["--book", os.path.join(self.tmp, "absent.json")]))
        self.assertEqual((1, "book_invalid"), (code, body["error"]["code"]))


class ErrorsAndStateTest(DeskCopy):
    def test_no_adoption_and_bad_candidates_are_exit_1(self) -> None:
        code, body = run_json(self.argv(candidate=os.path.join(self.tmp, "absent.toml")))
        self.assertEqual((1, "rules_missing"), (code, body["error"]["code"]))
        broken = os.path.join(self.tmp, "broken.toml")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write("schema_version = 2\n")
        code, body = run_json(self.argv(candidate=broken))
        self.assertEqual((1, "config_invalid"), (code, body["error"]["code"]))
        os.remove(self.paths.adopted_file)
        code, body = run_json(self.argv())
        self.assertEqual((1, "not_adopted"), (code, body["error"]["code"]))

    def test_market_data_refused_is_exit_20(self) -> None:
        refusal = quote_cache.MarketDataRefused(quote_cache.REFUSED_NO_CREDENTIAL, quote_cache.NO_CREDENTIAL_MESSAGE)
        with mock.patch.object(quote_cache, "build_client", side_effect=refusal):
            code, body = run_json(["--json", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW,
                                   "--candidate", CANDIDATE])
        self.assertEqual((20, "no_credential"), (code, body["error"]["code"]))

    def test_the_rule_state_is_neither_used_nor_written(self) -> None:
        os.makedirs(self.paths.state_dir, exist_ok=True)
        with open(self.paths.rule_state_json, "w", encoding="utf-8") as handle:
            handle.write('{"half": ')
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertEqual("scratch", body["payload"]["state"])
        with open(self.paths.rule_state_json, encoding="utf-8") as handle:
            self.assertEqual('{"half": ', handle.read(), "a scratch state: the routine's file is not even read")


class DiffFunctionTest(unittest.TestCase):
    def test_rule_set_changes_compare_per_rule_shas(self) -> None:
        from aegis_desk import exit_rules
        adopted = exit_rules.load_rulebook(os.path.join(FIXTURE_DESK, "rules", "exit_rules-1.0.0.toml"))
        candidate = exit_rules.load_rulebook(CANDIDATE)
        self.assertEqual({"added": [], "removed": [], "changed": ["TP50"], "unchanged": 26},
                         tool.rule_set_changes(adopted, candidate))
        self.assertEqual({"added": [], "removed": [], "changed": [], "unchanged": 27},
                         tool.rule_set_changes(adopted, adopted))
        self.assertEqual({"added": list(adopted.rule_ids), "removed": [], "changed": [], "unchanged": 0},
                         tool.rule_set_changes(None, adopted))


if __name__ == "__main__":
    unittest.main()
