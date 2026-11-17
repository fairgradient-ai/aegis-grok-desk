"""``scripts/weekly_review.py`` and the alert lint's review mode (roadmap 3.4, open question 6: counts only): the week's
per-signal facts and counts, a candidate replayed over the week, money only on fact lines, the refused phrases."""

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
from unittest import mock

import book as book_tool
import weekly_review as tool  # the tool scripts/weekly_review.py
from aegis_desk import alert_lint, desk_paths, signals

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
SATURDAY = "2026-11-07T16:00:00Z"


def run_tool(module: Any, argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = module.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


class ReviewTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-review-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)
        desk_paths.ensure_runtime_dirs(self.paths)
        for fixture_set, now in (("pre_open", "2026-11-02T21:30:00Z"), ("after_close", "2026-11-03T21:35:00Z")):
            run_tool(book_tool, ["--snapshot", "--offline", "--fixture-set", fixture_set, "--desk-root", self.root,
                                 "--now", now])
        self.signal = signals.append_signal(self.paths, {
            "signal_id": None, "trade_id": "AG-20261020-01", "structure": "covered_call", "verdict": "WATCH",
            "reasons": [{"rule_id": "DTE21", "rule_sha": "0" * 64, "result": "fired", "metric": "dte", "value": 13,
                         "threshold": 21, "unit": None, "verdict": "WATCH", "plan": "roll_review", "text": "x"}],
            "plan": "roll_review", "data_quality": "ok", "quote_age_at_post": 19, "run_lateness_min": 0,
            "posted_at": "2026-11-02T19:50:00+00:00"})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_desk(self, *extra: str, now: str = SATURDAY, json_mode: bool = False) -> Tuple[int, str]:
        code, out = run_tool(tool, ["--desk-root", self.root, "--now", now] + list(extra)
                             + (["--json"] if json_mode else []))
        self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(out)])
        return code, out

    def test_the_week_without_a_candidate(self) -> None:
        code, out = self.run_desk(json_mode=True)
        payload = json.loads(out)["payload"]
        lines = payload["message"].splitlines()
        self.assertEqual("WEEKLY REVIEW 2026-11-02 to 2026-11-06 | your signals and your rules, from the desk's own "
                         "records; counts only", lines[0])
        self.assertIn("DTE21: 1 signal this week; 0 acknowledged, 0 marked acted, 0 snoozed", lines)
        self.assertIn("no candidate rulebook under rules/ (candidate-X.Y.Z.toml): nothing to replay this week", lines)
        self.assertEqual({"start": "2026-11-02", "end": "2026-11-06"}, payload["week"])
        self.assertEqual((None, []), (payload["replay"]["candidate"], payload["replay"]["days"]))
        self.assertEqual(20, code, "Wednesday to Friday passed without a snapshot: the signal's marks are UNAVAILABLE")
        self.assertEqual([], alert_lint.review_findings(payload["message"]))
        for line in lines:
            if "$" in line:
                self.assertTrue(alert_lint.REVIEW_FACT_LINE_RE.match(line), line)

    def test_the_newest_candidate_is_replayed(self) -> None:
        for version in ("1.0.1", "1.1.0"):
            shutil.copy(os.path.join(ROOT, "tests", "fixtures", "rules", "candidate-1.1.0.toml"),
                        os.path.join(self.root, "rules", "candidate-%s.toml" % version))
        code, out = self.run_desk(json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual(("rules/candidate-1.1.0.toml", 0, ["2026-11-02", "2026-11-03"]),
                         (payload["replay"]["candidate"], payload["replay"]["days_differed"],
                          [day["et_date"] for day in payload["replay"]["days"]]))
        self.assertIn("verdict differed from your adopted rule on 0 of 2 trading days", payload["message"].splitlines())

    def test_a_text_that_fails_the_review_mode_is_never_printed(self) -> None:
        with mock.patch.object(tool.journal, "count_line", return_value="the rules saved money"):
            code, out = self.run_desk(json_mode=True)
        body = json.loads(out)
        self.assertEqual((1, "lint_failed"), (code, body["error"]["code"]))
        self.assertNotIn("saved", out)

    def test_usage(self) -> None:
        for argv in (["--week-of", "x"], ["--week-of", "2026-11-09"]):
            self.assertEqual(2, self.run_desk(*argv)[0], argv)


class ReviewLintTest(unittest.TestCase):
    def test_currency_only_on_fact_lines_and_the_refused_phrases(self) -> None:
        text = ("WEEKLY REVIEW\nSIG-20261103-001 | AG-1 covered_call | P/L +$1,145.00\nall rules: 3 signals, $50 in all\n"
                "these rules outperformed\nWin  Rate of the week\nthe return path is fine")
        found = [(item.code, item.line) for item in alert_lint.review_findings(text)]
        self.assertEqual([("review_currency", 3), ("review_phrase", 4), ("review_phrase", 5), ("review_phrase", 6)],
                         found)
        self.assertTrue(all("outperformed" not in item.message for item in alert_lint.review_findings(text)))
        self.assertEqual([], alert_lint.review_findings("SIG-20261103-001 | AG-1 | +$1.00\nTP50: 1 signal this week"))


if __name__ == "__main__":
    unittest.main()
