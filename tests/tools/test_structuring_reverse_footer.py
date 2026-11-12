"""The reverse refusal on real tool texts (PR 3b review L2-4): no structuring text, of any verdict, passes the lint
when its structuring footer is swapped for the rules footer or the market-snapshot footer.

Every text the three structuring tools print (``payload.message`` of ``--json``: TICKET for all five actions, BEST,
NONE_QUALIFIES, NOT_APPLICABLE, NOTHING_TO_PROPOSE, REFUSED, UNAVAILABLE, ``criteria_missing`` and
``config_unacknowledged``) is re-footered and linted against its own emitted document: the finding ``footer_missing``
must stand, so a ticket or a ranked candidate can never be relayed as a rule alert or a public snapshot. Each run is on
a copy of ``tests/fixtures/desk`` (never the fixture tree itself).
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import datetime
import io
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Sequence, Tuple

import ack as ack_tool
import desk_init as desk_init_tool
import exit_ticket
import propose
import roll_analysis
from aegis_desk import alert_lint

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
ANSWERS = os.path.join(ROOT, "tests", "fixtures", "init", "structuring-answers.json")
NOW = "2026-11-03T19:50:00Z"
WMT, NVDA, SPY = "AG-20261020-01", "AG-20261022-02", "AG-20261028-03"
WMT_CALL = ("option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,open,,,,,"
            "covered call #assign-ok\n")
WMT_NOTE = ("92.00,core holding #horizon:2027-03", "92.00,core holding #horizon:2027-03 #earnings-ok #assign-ok")


def call(main: Any, argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            code = main(list(argv))
        except SystemExit as exc:
            code = exc.code
    return code, buffer.getvalue()


class ReverseFooterTest(unittest.TestCase):
    def desk(self, kind: str) -> str:
        tmp = tempfile.mkdtemp(prefix="aegis-reverse-footer-")
        self.addCleanup(shutil.rmtree, tmp, True)
        root = os.path.join(tmp, "desk")
        shutil.copytree(FIXTURE_DESK, root)

        def edit(name: str, old: str, new: str) -> None:
            path = os.path.join(root, name)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            self.assertEqual(1, text.count(old), old)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text.replace(old, new))

        if kind in ("wmt_shares", "wmt_plain"):
            edit("positions.csv", WMT_CALL, "")
            if kind == "wmt_shares":
                edit("positions.csv", *WMT_NOTE)
        if kind == "level0":
            edit("desk.toml", "approval_level = 2 ", "approval_level = 0 ")
        if kind != "no_criteria":
            code, out = call(desk_init_tool.main, ["--answers", ANSWERS, "--desk-root", root, "--json"])
            self.assertEqual(0, code, out)
        if kind != "unacknowledged":
            code, out = call(ack_tool.main, ["config", "--owner-tapped", "--offline", "--desk-root", root, "--now",
                                             "2026-11-03T19:49:00Z", "--json"])
            self.assertEqual(0, code, out)
        return root

    def texts(self) -> List[Tuple[str, int, Dict[str, Any]]]:
        roots = {kind: self.desk(kind) for kind in ("main", "level0", "wmt_shares", "wmt_plain", "unacknowledged",
                                                    "no_criteria")}
        runs = [
            ("main", roll_analysis, [WMT], NOW, None), ("main", roll_analysis, [NVDA], NOW, None),
            ("main", roll_analysis, [SPY], NOW, None), ("main", roll_analysis, [WMT], NOW, "stale"),
            ("level0", roll_analysis, [WMT], NOW, None), ("no_criteria", roll_analysis, [WMT], NOW, None),
            ("unacknowledged", roll_analysis, [WMT], NOW, None),
            ("main", propose, [SPY, "--structure", "protective_put"], NOW, None),
            ("wmt_shares", propose, [WMT, "--structure", "covered_call"], NOW, None),
            ("wmt_plain", propose, [WMT, "--structure", "covered_call"], NOW, None),
            ("level0", propose, [WMT, "--structure", "covered_call"], NOW, None),
            ("main", exit_ticket, [WMT, "--action", "CLOSE"], NOW, None),
            ("main", exit_ticket, [WMT, "--action", "UNWIND_STOCK"], NOW, None),
            ("main", exit_ticket, [WMT, "--action", "LET_ASSIGN"], NOW, None),
            ("main", exit_ticket, [SPY, "--action", "MONETIZE_PUT"], NOW, None),
            ("main", exit_ticket, [WMT, "--action", "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "115"],
             NOW, None),
            ("main", exit_ticket, [WMT, "--action", "MONETIZE_PUT"], NOW, None),
            ("main", exit_ticket, [WMT, "--action", "CLOSE"], "2026-11-03T21:25:00Z", "after_close"),
            ("level0", exit_ticket, [WMT, "--action", "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "115"],
             NOW, None),
        ]
        out = []
        for kind, tool, args, now, fixture_set in runs:
            argv = ["--trade"] + list(args) + ["--offline", "--desk-root", roots[kind], "--now", now, "--json"]
            if fixture_set is not None:
                argv += ["--fixture-set", fixture_set]
            code, printed = call(tool.main, argv)
            body = json.loads(printed)
            out.append(("%s %s %s" % (tool.__name__, kind, " ".join(args)), code, body["payload"]))
        return out

    def test_every_tool_text_fails_under_the_rules_and_snapshot_footers(self) -> None:
        verdicts = set()
        for name, code, payload in self.texts():
            verdicts.add(payload["verdict"])
            text, document = payload["message"], payload["emitted"]
            lines = text.split("\n")
            emitted = alert_lint.load_emitted(document)
            self.assertTrue(emitted.check(text).ok, (name, emitted.check(text).problems))
            for kind, footer in ((alert_lint.FOOTER_RULES, alert_lint.footer_line(emitted.as_of)),
                                 (alert_lint.FOOTER_SNAPSHOT, alert_lint.snapshot_footer_line(emitted.as_of))):
                swapped = "\n".join(lines[:-1] + [footer])
                result = emitted.check(swapped, kind)
                with self.subTest(text=name, footer=kind):
                    self.assertIn("footer_missing", [finding.code for finding in result.findings], result.problems)
        self.assertEqual({"BEST", "NONE_QUALIFIES", "NOT_APPLICABLE", "NOTHING_TO_PROPOSE", "UNAVAILABLE", "REFUSED",
                          "TICKET"}, verdicts)


if __name__ == "__main__":
    unittest.main()
