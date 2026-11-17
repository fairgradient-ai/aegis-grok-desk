"""``scripts/rules_sanity.py`` (roadmap 3.3): the adopted rulebook, verified, linted against the structures held today,
and its weekly memo. The fixture desk's rulebook covers its covered calls and protective put; a long call opened after
the adoption is a structure no take-profit rule of the fixture's reaches only if the rulebook lacks one, and a tampered
rulebook is exit 1."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import io
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Sequence, Tuple

import rules_sanity as tool  # the tool scripts/rules_sanity.py
from aegis_desk import alert_lint

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
NOW = "2026-11-03T21:45:00Z"


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = tool.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


class SanityTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-sanity-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.rules = os.path.join(self.root, "rules", "exit_rules-1.0.0.toml")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_desk(self, *extra: str, now: str = NOW, json_mode: bool = False) -> Tuple[int, str]:
        code, out = run_tool(["--desk-root", self.root, "--now", now] + list(extra) + (["--json"] if json_mode else []))
        self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(out)])
        return code, out

    def test_sane_and_the_memo(self) -> None:
        code, out = self.run_desk()
        self.assertEqual((0, "RULES SANITY | rules 1.0.0, sha cc09859c | SANE: every structure held today has its rules"),
                         (code, out.rstrip("\n")))
        code, out = self.run_desk("--memo", now="2026-11-06T22:20:00Z")
        self.assertEqual((0, ["RULEBOOK MEMO | rules 1.0.0, sha cc09859c | adopted 2026-11-02 (4 calendar days ago)",
                              "27 rules: event 2, loss 3, portfolio 2, risk 10, take_profit 4, time 4, watch 2",
                              "structures held today: covered_call, protective_put",
                              "RULES SANITY | rules 1.0.0, sha cc09859c | SANE: every structure held today has its "
                              "rules"]), (code, out.splitlines()))
        payload = json.loads(self.run_desk("--memo", json_mode=True)[1])["payload"]
        self.assertEqual(("sane", True, 27, ["covered_call", "protective_put"]),
                         (payload["verdict"], payload["memo"], payload["rulebook"]["rules"], payload["held_structures"]))

    def test_a_tampered_rulebook_is_exit_1(self) -> None:
        with open(self.rules, "a", encoding="utf-8") as handle:
            handle.write("\n# edited by hand\n")
        code, out = self.run_desk(json_mode=True)
        self.assertEqual((1, "rules_tampered"), (code, json.loads(out)["error"]["code"]))

    def test_findings_against_the_positions_held_today(self) -> None:
        with mock_lint([("uncovered_structure", None, "long_call", "no take-profit rule reaches a long_call")]):
            code, out = self.run_desk()
        self.assertEqual(20, code)
        self.assertEqual(["RULES SANITY | rules 1.0.0, sha cc09859c | 1 finding against the positions held today",
                          "uncovered_structure | long_call | no take-profit rule reaches a long_call",
                          "the Aegis Rules Steward offers a candidate rulebook for your review; nothing changes until "
                          "you adopt one with your phrase and your tap"], out.splitlines())


@contextlib.contextmanager
def mock_lint(findings: Sequence[Tuple[str, Any, Any, str]]) -> Any:
    from unittest import mock
    from aegis_desk import exit_rules
    items = [exit_rules.LintFinding(*item) for item in findings]
    with mock.patch.object(tool.exit_rules, "lint", return_value=items):
        yield


if __name__ == "__main__":
    unittest.main()
