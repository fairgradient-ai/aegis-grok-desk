"""``scripts/rules_adopt.py`` run in-process against a copied fixture desk root: the owner-tapped adoption of a
rulebook version. The lint against the held structures (``--allow-uncovered`` accepts uncovered structures and
nothing else), the regular-trading-hours refusal (``--allow-rth``; both recorded in ``rules/CHANGELOG.md``), the
owner phrase and the version, the immutable copy (a version whose bytes differ is refused), ``rules/ADOPTED`` and the
append-only CHANGELOG, the rule state carried forward (same per-rule sha kept, changed reset, removed dropped), the
"rulebook changed: N verdicts changed" digest against the newest snapshot (or "no snapshot: digest unavailable")
recorded for the next book_check, the no-op re-adoption and the rollback; every refusal writes nothing."""

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
from unittest import mock

from aegis_desk import alert_lint, book, contract, desk_paths, exit_rules, quote_cache, rule_state, state

import exit_check
import rules_adopt as tool  # the tool scripts/rules_adopt.py

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
CANDIDATE = os.path.join(ROOT, "tests", "fixtures", "rules", "candidate-1.1.0.toml")
EXAMPLE_RULES = os.path.join(ROOT, "rules", "examples", "exit_rules-1.0.0.toml")
AFTER_CLOSE = "2026-11-03T22:00:00Z"
REGULAR_NOW = "2026-11-03T19:50:00Z"
PREV_EOD_NOW = "2026-11-02T21:30:00Z"
WMT = "AG-20261020-01"
ADOPTED_SHA = "cc09859c896e22f84402ce8b686d0f79ec920b503b647754232247668b8bc5fe"
PHRASE_SHA_110 = hashlib.sha256(b"adopt rules 1.1.0").hexdigest()
CUSTOM_ROWS = ("option,WMT,WMT261120C00110000,1,,,,Brokerage (margin),AG-20261103-05,2026-11-03,150.00,,1.50,open,,,,,mixed\n"
               "option,WMT,WMT261120P00095000,-1,,,,Brokerage (margin),AG-20261103-05,2026-11-03,-120.00,,1.20,open,,,,,mixed\n")


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


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
        return handle.read()


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-rules-adopt-tool-")
        self.tmp = os.path.realpath(self._tmp.name)
        self.root = os.path.join(self.tmp, "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)
        self.changelog = os.path.join(self.paths.rules_dir, "CHANGELOG.md")
        self.adopted_text = read_text(os.path.join(self.paths.rules_dir, "exit_rules-1.0.0.toml"))

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, path: str = CANDIDATE, version: str = "1.1.0", phrase: Optional[str] = None, now: str = AFTER_CLOSE,
             json_mode: bool = True, extra: Sequence[str] = ()) -> List[str]:
        argv = ["--offline", "--desk-root", self.root, "--now", now, "--file", path, "--version", version,
                "--owner-phrase", "adopt rules %s" % version if phrase is None else phrase] + list(extra)
        if json_mode:
            argv.append("--json")
        return argv

    def tree(self) -> Dict[str, bytes]:
        """Every file under rules/ and state/ with its bytes (the lock file aside)."""
        found: Dict[str, bytes] = {}
        for top in ("rules", "state", "data"):
            base = os.path.join(self.root, top)
            for directory, _, names in os.walk(base):
                for name in names:
                    path = os.path.join(directory, name)
                    if name == ".lock":
                        continue
                    found[os.path.relpath(path, self.root)] = read_bytes(path)
        return found

    def candidate(self, version: str, edits: Sequence[Tuple[str, str, str]] = ()) -> str:
        text = self.adopted_text.replace('rulebook_version = "1.0.0"', 'rulebook_version = "%s"' % version)
        for rule_id, old, new in edits:
            head, sep, tail = text.partition('id = "%s"\n' % rule_id)
            self.assertTrue(sep, rule_id)
            text = head + sep + tail.replace(old, new, 1)
        path = os.path.join(self.tmp, "candidate-%s.toml" % version)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text)
        return path

    def snapshot(self) -> str:
        market = quote_cache.open_market_data(self.root, now=contract.parse_utc_instant(PREV_EOD_NOW), offline=True,
                                              fixture_set="pre_open", timeout_s=5.0, user_agent="aegis-desk/test rules_adopt")
        run = book.load_book(market)
        return book.write_snapshot(self.paths, run.document, run.session.et_date)

    def assert_refused(self, argv: Sequence[str], codes: Sequence[str]) -> Dict[str, Any]:
        before = self.tree()
        code, body = run_json(argv)
        self.assertEqual((1, "error"), (code, body["status"]), body)
        payload = body["payload"]
        self.assertEqual(sorted(codes), sorted(item["code"] for item in payload["refusals"]))
        self.assertEqual((False, False, []), (payload["adopted"], payload["already_adopted"], payload["written"]))
        self.assertEqual(before, self.tree(), "a refusal writes nothing")
        return payload


class SchemaAndUsageTest(DeskCopy):
    def test_schema(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual("rules_adopt", body["tool"])
        described = body["envelope"]["payload"]
        self.assertEqual(set(tool.PAYLOAD_KEYS), set(described))
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertEqual(set(described), set(body["payload"]))

    def test_usage_errors_are_exit_2(self) -> None:
        base = ["--json", "--offline", "--desk-root", self.root, "--now", AFTER_CLOSE]
        for argv in (base + ["--version", "1.1.0", "--owner-phrase", "adopt rules 1.1.0"],
                     base + ["--file", CANDIDATE, "--owner-phrase", "adopt rules 1.1.0"],
                     base + ["--file", CANDIDATE, "--version", "1.1.0"],
                     self.argv(version="v1.1.0", phrase="adopt rules 1.1.0"),
                     self.argv(version="1.1", phrase="adopt rules 1.1")):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(argv)
                self.assertEqual(2, ctx.exception.code)


class AdoptionTest(DeskCopy):
    def test_adoption_after_the_close(self) -> None:
        # Journey 12: the candidate 1.1.0 (TP50 at 60) adopted at 17:00 ET.
        old_changelog = read_text(self.changelog)
        code, body = run_json(self.argv())
        self.assertEqual((0, "ok"), (code, body["status"]), body)
        payload = body["payload"]
        candidate_sha = hashlib.sha256(read_bytes(CANDIDATE)).hexdigest()
        self.assertEqual((True, False, "1.1.0", candidate_sha, "rules/exit_rules-1.1.0.toml", CANDIDATE),
                         (payload["adopted"], payload["already_adopted"], payload["version"], payload["sha256"],
                          payload["file"], payload["source_file"]))
        self.assertEqual({"version": "1.0.0", "sha256": ADOPTED_SHA, "error": None}, payload["previous"])
        self.assertEqual(("2026-11-03T22:00:00+00:00", PHRASE_SHA_110, "closed", False, False, []),
                         (payload["adopted_utc"], payload["owner_phrase_sha256"], payload["session"],
                          payload["during_rth"], payload["allow_rth"], payload["allow_uncovered"]))
        self.assertEqual((["covered_call", "protective_put"], [], []),
                         (payload["held_structures"], payload["findings"], payload["refusals"]))
        self.assertEqual(["rules/exit_rules-1.1.0.toml", "rules/ADOPTED", "rules/CHANGELOG.md", "state/desk_state.json"],
                         payload["written"])
        self.assertEqual(read_bytes(CANDIDATE), read_bytes(os.path.join(self.paths.rules_dir, "exit_rules-1.1.0.toml")),
                         "the immutable copy holds exactly the adopted bytes")
        adopted, rulebook = exit_rules.load_adopted_rulebook(self.paths)
        self.assertEqual(("1.1.0", candidate_sha, PHRASE_SHA_110, datetime.datetime(2026, 11, 3, 22, tzinfo=datetime.timezone.utc)),
                         (adopted.version, adopted.sha256, adopted.owner_phrase_sha256, adopted.adopted_utc))
        self.assertEqual(rulebook.rules_sha, adopted.rules_sha)
        changelog = read_text(self.changelog)
        self.assertTrue(changelog.startswith(old_changelog), "the CHANGELOG is append-only")
        entry = changelog[len(old_changelog):]
        self.assertTrue(entry.startswith("\n## 1.1.0 adopted 2026-11-03T22:00:00Z\n"), entry)
        self.assertIn("- file exit_rules-1.1.0.toml, sha256 %s\n" % candidate_sha, entry)
        self.assertIn("- owner phrase sha256 %s\n" % PHRASE_SHA_110, entry)
        self.assertIn("- TP50 example-accepted: pct_captured >= 60 percent and dte > 21 trading days; EXIT_SIGNAL, "
                      "plan close\n", entry)
        self.assertIn("- previous adopted rulebook 1.0.0, sha256 %s\n" % ADOPTED_SHA, entry)
        self.assertIn("- no snapshot: digest unavailable\n", entry)
        self.assertNotIn("--allow-rth", entry)
        self.assertNotIn("--allow-uncovered", entry)
        self.assertEqual({"snapshot": None, "previous_version": "1.0.0", "verdicts_changed": None, "changes": [],
                          "text": "no snapshot: digest unavailable"}, payload["digest"])
        self.assertEqual({"status": "missing", "kept": 0, "reset": 0, "dropped": 0, "written": False}, payload["rule_state"])
        desk_state, status = state.read_json(self.paths.desk_state_json)
        self.assertEqual("ok", status)
        self.assertEqual({"rulebook_change": {"version": "1.1.0", "sha256": candidate_sha, "previous_version": "1.0.0",
                                              "previous_sha256": ADOPTED_SHA, "adopted_utc": "2026-11-03T22:00:00+00:00",
                                              "changed": None, "snapshot": None}}, desk_state,
                         "the record book_check.py prints once as the rulebook-changed digest")
        code, body = exit_check_json(self.root, AFTER_CLOSE)
        self.assertEqual("1.1.0", body["payload"]["rulebook"]["version"], "exit_check evaluates the new adoption")

    def test_adoption_during_regular_hours_is_refused(self) -> None:
        payload = self.assert_refused(self.argv(now=REGULAR_NOW), ["regular_trading_hours"])
        self.assertEqual(("open", True), (payload["session"], payload["during_rth"]))
        self.assertIn("--allow-rth", payload["refusals"][0]["message"])
        self.assertEqual("1.0.0", exit_rules.load_adopted(self.paths.adopted_file).version)

    def test_allow_rth_adopts_during_regular_hours_and_says_so(self) -> None:
        code, body = run_json(self.argv(now=REGULAR_NOW, extra=["--allow-rth"]))
        self.assertEqual(0, code, body)
        self.assertEqual((True, True), (body["payload"]["during_rth"], body["payload"]["allow_rth"]))
        self.assertIn("- adopted during regular trading hours (--allow-rth)\n", read_text(self.changelog))

    def test_allow_rth_outside_regular_hours_records_nothing(self) -> None:
        code, body = run_json(self.argv(extra=["--allow-rth"]))
        self.assertEqual(0, code, body)
        self.assertNotIn("regular trading hours", read_text(self.changelog))

    def test_the_owner_phrase_and_the_version_must_match(self) -> None:
        self.assert_refused(self.argv(phrase="adopt rules 1.0.0"), ["owner_phrase_mismatch"])
        self.assert_refused(self.argv(phrase="adopt the rules"), ["owner_phrase_mismatch"])
        self.assert_refused(self.argv(version="1.2.0"), ["version_mismatch"])

    def test_every_refusal_is_reported_at_once(self) -> None:
        # The example file: every rule is an example (not adopted individually), two owner values still to set, and
        # version 1.0.0 is already on disk with other bytes.
        payload = self.assert_refused(self.argv(path=EXAMPLE_RULES, version="1.0.0", now=REGULAR_NOW),
                                      ["immutable_version", "lint_failed", "regular_trading_hours"])
        codes = {(item["code"], item["rule_id"]) for item in payload["findings"]}
        self.assertIn(("not_accepted", "TP50"), codes)
        self.assertIn(("owner_must_set", "CONC"), codes)
        self.assertIn(("owner_must_set", "DAILY"), codes)
        self.assertTrue(all(item["accepted"] is False for item in payload["findings"]))

    def test_an_uncovered_structure_needs_allow_uncovered(self) -> None:
        with open(self.paths.positions_csv, "a", encoding="utf-8") as handle:
            handle.write(CUSTOM_ROWS)
        payload = self.assert_refused(self.argv(), ["lint_failed"])
        self.assertEqual([{"code": "uncovered_structure", "rule_id": None, "structure": "custom", "accepted": False,
                           "message": "no take-profit rule applies to the held custom (accept it explicitly with "
                                      "--allow-uncovered)"}], payload["findings"])
        self.assertEqual(["covered_call", "custom", "protective_put"], payload["held_structures"])
        code, body = run_json(self.argv(extra=["--allow-uncovered"]))
        self.assertEqual(0, code, body)
        self.assertEqual((["custom"], True), (body["payload"]["allow_uncovered"], body["payload"]["findings"][0]["accepted"]))
        self.assertIn("- uncovered structures accepted (--allow-uncovered): custom\n", read_text(self.changelog))

    def test_allow_uncovered_never_waives_another_finding(self) -> None:
        self.assert_refused(self.argv(path=EXAMPLE_RULES, version="1.0.0", extra=["--allow-uncovered"]),
                            ["immutable_version", "lint_failed"])

    def test_allow_uncovered_without_an_uncovered_structure_is_not_recorded(self) -> None:
        code, body = run_json(self.argv(extra=["--allow-uncovered"]))
        self.assertEqual(0, code, body)
        self.assertEqual([], body["payload"]["allow_uncovered"])
        self.assertNotIn("--allow-uncovered", read_text(self.changelog))


class ImmutabilityTest(DeskCopy):
    def test_a_version_on_disk_with_other_bytes_is_refused(self) -> None:
        version_file = os.path.join(self.paths.rules_dir, "exit_rules-1.1.0.toml")
        other = self.candidate("1.1.0", [("TP50", "value = 50", "value = 55")])
        shutil.copyfile(other, version_file)
        payload = self.assert_refused(self.argv(), ["immutable_version"])
        self.assertIn("exit_rules-1.1.0.toml", payload["refusals"][0]["message"])
        self.assertEqual(read_bytes(other), read_bytes(version_file))

    def test_a_version_on_disk_with_the_same_bytes_is_not_rewritten(self) -> None:
        version_file = os.path.join(self.paths.rules_dir, "exit_rules-1.1.0.toml")
        shutil.copyfile(CANDIDATE, version_file)
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertNotIn("rules/exit_rules-1.1.0.toml", body["payload"]["written"])

    def edit_in_place(self, version: str, old: str, new: str) -> str:
        path = os.path.join(self.paths.rules_dir, "exit_rules-%s.toml" % version)
        text = read_text(path)
        self.assertEqual(1, text.count(old), old)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new))
        return path

    def test_the_adopted_version_edited_in_place_is_refused(self) -> None:
        self.assertEqual(0, run_json(self.argv())[0])
        path = self.edit_in_place("1.1.0", '"pct_captured", op = ">=", value = 60,', '"pct_captured", op = ">=", value = 30,')
        payload = self.assert_refused(self.argv(path=path, now="2026-11-03T22:20:00Z"), ["immutable_version"])
        self.assertIn("131490c8", payload["refusals"][0]["message"], "names the sha its adoption recorded")
        self.assertIn("new rulebook_version", payload["refusals"][0]["message"])

    def test_an_earlier_version_edited_in_place_is_refused(self) -> None:
        self.assertEqual(0, run_json(self.argv())[0])
        path = self.edit_in_place("1.0.0", 'op = ">=", value = 25, unit = "percent"', 'op = ">=", value = 90, unit = "percent"')
        payload = self.assert_refused(self.argv(path=path, version="1.0.0", now="2026-11-03T22:30:00Z"),
                                      ["immutable_version"])
        self.assertIn("cc09859c", payload["refusals"][0]["message"])
        self.assertEqual(2, read_text(self.changelog).count("\n## "), "no second sha recorded for one version")

    def test_the_adopted_rulebook_again_is_a_no_op(self) -> None:
        before = self.tree()
        code, body = run_json(self.argv(path=os.path.join(self.paths.rules_dir, "exit_rules-1.0.0.toml"),
                                        version="1.0.0", now=REGULAR_NOW))
        self.assertEqual((0, "quiet"), (code, body["status"]), body)
        payload = body["payload"]
        self.assertEqual((False, True, [], []), (payload["adopted"], payload["already_adopted"], payload["written"],
                                                 payload["refusals"]))
        self.assertEqual(before, self.tree(), "nothing written")

    def test_a_rollback_re_adopts_an_earlier_version(self) -> None:
        self.assertEqual(0, run_json(self.argv())[0])
        code, body = run_json(self.argv(path=os.path.join(self.paths.rules_dir, "exit_rules-1.0.0.toml"), version="1.0.0"))
        self.assertEqual(0, code, body)
        self.assertEqual(["rules/ADOPTED", "rules/CHANGELOG.md", "state/desk_state.json"], body["payload"]["written"])
        self.assertEqual(("1.0.0", ADOPTED_SHA), (exit_rules.load_adopted(self.paths.adopted_file).version,
                                                  exit_rules.load_adopted(self.paths.adopted_file).sha256))
        self.assertEqual(3, read_text(self.changelog).count("\n## "))


class RuleStateTest(DeskCopy):
    def seed(self) -> Dict[str, Any]:
        old = exit_rules.load_rulebook(os.path.join(self.paths.rules_dir, "exit_rules-1.0.0.toml")).rules_sha
        opened = datetime.date(2026, 10, 20)
        pending = rule_state.new_entry(old["TP50"], opened)
        pending.update(status=rule_state.STATUS_PENDING,
                       observations=[{"as_of": "2026-11-03T19:31:00+00:00", "value": "52.00"}])
        fired = rule_state.new_entry(old["DTE21"], opened)
        fired.update(status=rule_state.STATUS_FIRED, fired_at="2026-11-03T19:50:00+00:00", fired_value="13",
                     last_posted_at="2026-11-03T19:50:00+00:00", posts=1)
        entries = {rule_state.state_key(WMT, "TP50"): pending, rule_state.state_key(WMT, "DTE21"): fired,
                   rule_state.state_key(WMT, "GONE"): rule_state.new_entry("0" * 64, opened)}
        for entry in entries.values():
            self.assertIsNone(rule_state.entry_problem(entry))
        rule_state.save(self.paths, entries)
        return entries

    def test_the_rule_state_is_carried_forward(self) -> None:
        entries = self.seed()
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual({"status": "ok", "kept": 1, "reset": 1, "dropped": 1, "written": True}, payload["rule_state"])
        self.assertIn("state/rule_state.json", payload["written"])
        carried = rule_state.load(self.paths)
        self.assertEqual(entries[rule_state.state_key(WMT, "DTE21")], carried[rule_state.state_key(WMT, "DTE21")],
                         "same per-rule sha: kept as it was")
        self.assertNotIn(rule_state.state_key(WMT, "TP50"), carried,
                         "changed rule: reset, so its next observation is a first observation (as step's own reset)")
        self.assertNotIn(rule_state.state_key(WMT, "GONE"), carried, "a rule the new rulebook does not hold is dropped")
        self.assertIn("- rule state carried forward: 1 kept, 1 reset, 1 dropped\n", read_text(self.changelog))

    def test_a_refusal_leaves_the_rule_state_alone(self) -> None:
        self.seed()
        self.assert_refused(self.argv(now=REGULAR_NOW), ["regular_trading_hours"])


class DigestTest(DeskCopy):
    def test_the_digest_against_the_newest_snapshot(self) -> None:
        self.snapshot()
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertEqual({"snapshot": "data/book/2026-11-02.json", "previous_version": "1.0.0", "verdicts_changed": 0,
                          "changes": [], "text": "rulebook changed: 0 verdicts changed"}, body["payload"]["digest"])
        self.assertIn("- rulebook changed: 0 verdicts changed (against the snapshot data/book/2026-11-02.json)\n",
                      read_text(self.changelog))

    def test_a_verdict_that_changes_is_counted_and_recorded_for_book_check(self) -> None:
        self.snapshot()
        path = self.candidate("1.2.0", [("DTE21", "value = 21", "value = 5")])
        code, body = run_json(self.argv(path=path, version="1.2.0"))
        self.assertEqual(0, code, body)
        digest = body["payload"]["digest"]
        self.assertEqual((1, "rulebook changed: 1 verdict changed"), (digest["verdicts_changed"], digest["text"]))
        self.assertEqual([{"group_id": WMT, "trade_id": WMT, "underlying": "WMT", "structure": "covered_call",
                           "before": "WATCH", "after": "HOLD"}], digest["changes"])
        desk_state, _ = state.read_json(self.paths.desk_state_json)
        self.assertEqual((1, "1.0.0", "data/book/2026-11-02.json"),
                         (desk_state["rulebook_change"]["changed"], desk_state["rulebook_change"]["previous_version"],
                          desk_state["rulebook_change"]["snapshot"]))
        code, text = run_tool(self.argv(path=os.path.join(self.paths.rules_dir, "exit_rules-1.0.0.toml"), version="1.0.0",
                                        json_mode=False))
        self.assertEqual(0, code, text)
        self.assertIn("rulebook changed: 1 verdict changed", text.splitlines())

    def test_the_first_adoption_counts_every_group(self) -> None:
        os.remove(self.paths.adopted_file)
        os.remove(self.changelog)
        self.snapshot()
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual((None, 3, "rulebook changed: 3 verdicts changed (no previous adoption)"),
                         (payload["previous"], payload["digest"]["verdicts_changed"], payload["digest"]["text"]))
        changelog = read_text(self.changelog)
        self.assertTrue(changelog.startswith(exit_rules.CHANGELOG_HEADER + "## 1.1.0 adopted 2026-11-03T22:00:00Z\n"),
                        changelog)
        self.assertIn("- no previous adoption\n", changelog)

    def test_an_unreadable_previous_adoption_is_replaced(self) -> None:
        with open(os.path.join(self.paths.rules_dir, "exit_rules-1.0.0.toml"), "a", encoding="utf-8") as handle:
            handle.write("# edited after adoption\n")
        self.snapshot()
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual({"version": None, "sha256": None, "error": "rules_tampered"}, payload["previous"])
        self.assertEqual((None, "previous rulebook unreadable (rules_tampered): digest unavailable"),
                         (payload["digest"]["verdicts_changed"], payload["digest"]["text"]))
        self.assertEqual("1.1.0", exit_rules.load_adopted_rulebook(self.paths)[0].version)

    def test_the_desk_state_keeps_its_other_keys(self) -> None:
        os.makedirs(self.paths.state_dir, exist_ok=True)
        with open(self.paths.desk_state_json, "w", encoding="utf-8") as handle:
            json.dump({"acknowledged_hashes": {"desk_toml": "a" * 64}}, handle)
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        desk_state, _ = state.read_json(self.paths.desk_state_json)
        self.assertEqual({"desk_toml": "a" * 64}, desk_state["acknowledged_hashes"])
        self.assertEqual("1.1.0", desk_state["rulebook_change"]["version"])


class ErrorsTest(DeskCopy):
    def test_unreadable_inputs_are_exit_1(self) -> None:
        code, body = run_json(self.argv(path=os.path.join(self.tmp, "absent.toml")))
        self.assertEqual((1, "rules_missing"), (code, body["error"]["code"]))
        broken = os.path.join(self.tmp, "broken.toml")
        with open(broken, "w", encoding="utf-8") as handle:
            handle.write('schema_version = 1\nrulebook_version = "1.1.0"\n')
        code, body = run_json(self.argv(path=broken))
        self.assertEqual((1, "config_invalid"), (code, body["error"]["code"]))
        with open(broken, "wb") as handle:
            handle.write(b"\xff\xfe")
        code, body = run_json(self.argv(path=broken))
        self.assertEqual((1, "config_invalid"), (code, body["error"]["code"]))
        os.remove(self.paths.positions_csv)
        code, body = run_json(self.argv())
        self.assertEqual((1, "positions_missing"), (code, body["error"]["code"]))

    def test_a_bad_calendar_is_exit_1(self) -> None:
        with open(self.paths.calendar_toml, "a", encoding="utf-8") as handle:
            handle.write("\nunknown_key = 1\n")
        code, body = run_json(self.argv())
        self.assertEqual((1, "calendar_invalid"), (code, body["error"]["code"]))

    def test_a_held_lock_is_exit_1_lock_busy_and_nothing_is_written(self) -> None:
        before = self.tree()
        os.makedirs(self.paths.state_dir, exist_ok=True)
        with mock.patch.object(tool, "LOCK_TIMEOUT_S", 0.0):
            with state.locked(self.paths.lock_path):
                code, body = run_json(self.argv())
        self.assertEqual((1, "lock_busy"), (code, body["error"]["code"]))
        self.assertEqual(before, self.tree())


class ProseTest(DeskCopy):
    def test_the_adoption_report(self) -> None:
        self.snapshot()
        code, text = run_tool(self.argv(json_mode=False))
        self.assertEqual(0, code, text)
        lines = text.splitlines()
        candidate_sha = hashlib.sha256(read_bytes(CANDIDATE)).hexdigest()
        self.assertEqual("adopted rules 1.1.0, sha %s, as rules/exit_rules-1.1.0.toml at 2026-11-03 22:00 UTC "
                         "(session closed)" % candidate_sha[:8], lines[0])
        for needle in ("previous: rules 1.0.0, sha cc09859c",
                       "lint: clean for the held covered_call, protective_put",
                       "rule state: no state file to carry forward",
                       "rulebook changed: 0 verdicts changed",
                       "written: rules/exit_rules-1.1.0.toml, rules/ADOPTED, rules/CHANGELOG.md, state/desk_state.json"):
            self.assertIn(needle, lines)
        self.assertEqual([], order_shaped(text), text)

    def test_the_refusal_report(self) -> None:
        code, text = run_tool(self.argv(path=EXAMPLE_RULES, version="1.0.0", now=REGULAR_NOW, json_mode=False))
        self.assertEqual(1, code, text)
        lines = text.splitlines()
        self.assertEqual("refused: rules 1.0.0 was not adopted; nothing was written", lines[0])
        self.assertTrue(any(line.startswith("refused: regular_trading_hours: ") for line in lines), text)
        self.assertTrue(any(line.startswith("lint: not_accepted TP50: ") for line in lines), text)
        self.assertEqual([], order_shaped(text), text)

    def test_the_no_op_report(self) -> None:
        code, text = run_tool(self.argv(path=os.path.join(self.paths.rules_dir, "exit_rules-1.0.0.toml"), version="1.0.0",
                                        json_mode=False))
        self.assertEqual(0, code)
        self.assertEqual(["rules 1.0.0, sha cc09859c, is already the adopted rulebook: nothing was written"],
                         text.splitlines())


def exit_check_json(root: str, now: str) -> Tuple[int, Dict[str, Any]]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = exit_check.main(["--offline", "--desk-root", root, "--now", now, "--fixture-set", "after_close", "--json"])
    return code, json.loads(buffer.getvalue())


if __name__ == "__main__":
    unittest.main()
