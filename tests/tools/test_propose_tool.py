"""``scripts/propose.py``: a covered call, a protective put or a collar on a shares-only group, on the owner's request
only (PR 3b, the addendum S1-S17 over designer Q's D10-D16 and designer C's D3-D7).

Every run is in-process (``main(argv)``) on a copy of ``tests/fixtures/desk`` with the offline flags and a fixed
``--now``; the copy gets its ``structuring.toml`` as a journey does (``desk_init.py --answers
tests/fixtures/init/structuring-answers.json``, then ``ack.py config --owner-tapped``). The main fixture holds no
shares-only group, so every proposal there is NOTHING_TO_PROPOSE (designer Q's D10); the goldens of designer Q's D16
under S17 run on test-time copies of ``positions.csv`` without row 4 (the WMT call) or row 8 (the SPY put), with and
without the tags, pinned as full texts: the WMT covered call (NONE_QUALIFIES without ``#earnings-ok #assign-ok``, BEST
with them), the SPY protective put (BEST) and the SPY collar (NONE_QUALIFIES without ``#assign-ok``, BEST with it).
Then the approval-level REFUSED before any request, ``enabled = false``, no free shares, the fail-closed answers, the
stale and holiday sets, the request counts, the report files (exit 0 only, idempotent), the journal record, ``--now``
and every request a GET.
"""

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
from urllib.parse import urlsplit

import ack as ack_tool
import desk_init as desk_init_tool
import propose as tool  # the tool scripts/propose.py
from aegis_desk import alert_lint, alpaca_client, contract, desk_paths, state, structuring, structuring_text

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
ANSWERS = os.path.join(ROOT, "tests", "fixtures", "init", "structuring-answers.json")
CRITERIA_SHA = "5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c"
REGULAR_NOW = "2026-11-03T19:50:00Z"
ACK_NOW = "2026-11-03T19:49:00Z"
HOLIDAY_NOW = "2026-11-26T15:00:00Z"
WMT = "AG-20261020-01"
NVDA = "AG-20261022-02"
SPY = "AG-20261028-03"
WMT_CALL_ROW = "WMT261120C00105000"
SPY_PUT_ROW = "SPY261218P00620000"
WMT_SHARES_NOTE = ("92.00,core holding #horizon:2027-03", "92.00,core holding #horizon:2027-03 #earnings-ok #assign-ok")
SPY_SHARES_ROW = ("Roth IRA,AG-20261028-03,2026-10-28,63240.00,,,open,,,,,",
                  "Roth IRA,AG-20261028-03,2026-10-28,63240.00,,,open,,,,,#assign-ok")


def footer(clock: str) -> str:
    return alert_lint.structuring_footer_line(contract.parse_utc_instant("2026-11-03T%s:00Z" % clock))


FOOTER_1931 = footer("19:31")
CC_NOTHING_TEXT = [
    "NOTHING_TO_PROPOSE | AG-20261020-01 covered call proposal | AG-20261020-01 is a covered call: ask for a "
    "roll analysis",
    "",
    footer("19:50"),
]
CC_REFUSED_TEXT = [
    "REFUSED AG-20261020-01 | WMT covered call | opening 2 new short calls, covered by 200 free shares needs "
    "level 1 by the desk's convention; desk.toml records your self-attested level 0 | the desk shows nothing "
    "above your level; your broker's own approval decides what you can enter",
    "",
    footer("19:50"),
]
COLLAR_DISABLED_TEXT = [
    "NOT_APPLICABLE | AG-20261028-03 collar proposal | the collar section is disabled in your "
    "structuring.toml",
    "",
    footer("19:50"),
]
COLLAR_REFUSED_TEXT = [
    "REFUSED AG-20261028-03 | SPY collar | opening 1 new long put needs level 2 by the desk's convention; "
    "desk.toml records your self-attested level 1 | the desk shows nothing above your level; your broker's "
    "own approval decides what you can enter",
    "",
    footer("19:50"),
]
CRITERIA_MISSING_TEXT = [
    "UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your criteria "
    "(SETUP step 9)",
    "",
    footer("19:50"),
]
NO_FREE_SHARES_TEXT = [
    "NOTHING_TO_PROPOSE | AG-20261020-01 covered call proposal | no free shares: 200 WMT shares are covered "
    "by 2 short calls; the desk proposes no covered call",
    "",
    footer("19:50"),
]
PP_NOTHING_TEXT = [
    "NOTHING_TO_PROPOSE | AG-20261028-03 protective put proposal | the 100 SPY shares already carry 1 long "
    "put",
    "",
    footer("19:50"),
]
SPY_COLLAR_BEST_TEXT = [
    "### PROPOSAL AG-20261028-03 | a collar on the 100 SPY shares you hold",
    "",
    "| account | SPY mid | your cost | quotes |",
    "|---|--:|--:|---|",
    "| 2 of desk.toml (ira) | 648.12 | 632.40 | 19:31 UTC, 15-min delayed |",
    "",
    "**BEST CANDIDATE 1 of 10** by your objective cap_within_budget: **4.92**, the highest among the 4 "
    "candidates that passed every criterion",
    "- leg 1 of 2: opens a new long 1 x SPY 2027-03-19 620 put | bid 25.45, ask 25.65, mid 25.55 | quote "
    "grade A",
    "- leg 2 of 2: opens a new short 1 x SPY 2027-03-19 680 call | bid 26.40, ask 26.60, mid 26.50 | quote "
    "grade A",
    "",
    "> the score ranks the candidates only on the objective you chose; it is not a forecast",
    "",
    "| check | this candidate | your criteria |  |",
    "|---|---|---|:-:|",
    "| floor | 4.34 % below | 3 to 10 % | \U0001F7E2 |",
    "| cap | 4.92 % above | 2 to 10 % | \U0001F7E2 |",
    "| net per share | credit 0.75 (75.00 in all) | net debit at most 0.00 | \U0001F7E2 |",
    "| call strike | 680 | at or above your cost 632.40 | \U0001F7E2 |",
    "| calendar days | 136 | 120 to 210 | \U0001F7E2 |",
    "| spreads | 0.78 % and 0.75 % | at most 25 % | \U0001F7E2 |",
    "| ex-dividend | 2026-12-18 (estimated, cum 2026-12-17), inside the call's life | accepted by #assign-ok "
    "| \U0001F7E1 |",
    "| ex-dividend | a second ex-date may fall before the call's last trading day | accepted by #assign-ok | "
    "\U0001F7E1 |",
    "",
    "| the group | now | after both legs |",
    "|---|--:|--:|",
    "| max loss | 63,240.00 | 1,165.00 |",
    "| max profit | unbounded | 4,835.00 |",
    "| breakeven | 632.40 | 631.65 |",
    "| group delta | 100.00 | 24.14 |",
    "",
    "**approval-level check (leg 1 first):** the highest step needs level 2 by the desk's convention, within "
    "your self-attested level 2 (desk.toml); your broker's own approval for this account decides",
    "",
    "| after | you hold | level |",
    "|---|---|:-:|",
    "| leg 1 | 100 SPY shares, 1 long put | 2 |",
    "| leg 2 | 100 SPY shares, 1 short call, 1 long put | 1 |",
    "",
    "- entered as one multi-leg order, some brokers class a collar as a spread, which can need a higher level",
    "",
    "**candidates by rank:** 4 of 10 passed your criteria; 6 failed (max_net_debit 6)",
    "",
    "| # | expiry | put / call | floor | cap | net per share |  |",
    "|--:|---|---|--:|--:|---|---|",
    "| 1 | 2027-03-19 | 620 / 680 | 4.34 % | 4.92 % | credit 0.75 | \U0001F7E2 |",
    "| 2 | 2027-03-19 | 610 / 680 | 5.88 % | 4.92 % | credit 3.30 | \U0001F7E2 |",
    "| 3 | 2027-03-19 | 600 / 680 | 7.42 % | 4.92 % | credit 5.60 | \U0001F7E2 |",
    "",
    "- not paired, outside their bands: 7 puts outside your floor band and 10 calls outside your cap band",
    "",
    "> - cap_within_budget: the cap's distance above the share price, in percent, among pairs within your "
    "net-debit budget",
    "> - tie on cap_within_budget broken by the put strike (higher first)",
    "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
    "> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style "
    "options is not modelled",
    "> - criteria: your structuring.toml (sha 5155427c)",
    "> - you supply the limit; the desk prints none",
    "> - if you decide to act, you type it into your brokerage account yourself",
    "",
    FOOTER_1931,
]
SPY_COLLAR_NONE_TEXT = [
    "### PROPOSAL AG-20261028-03 | a collar on the 100 SPY shares you hold",
    "",
    "| account | SPY mid | your cost | quotes |",
    "|---|--:|--:|---|",
    "| 2 of desk.toml (ira) | 648.12 | 632.40 | 19:31 UTC, 15-min delayed |",
    "",
    "**no candidate passed every criterion: the desk names no best candidate**",
    "",
    "**candidates by rank:** 0 of 10 passed your criteria; 10 failed (max_net_debit 6, ex_dividend 10)",
    "",
    "| # | expiry | put / call | floor | cap | net per share |  |",
    "|--:|---|---|--:|--:|---|---|",
    "| 1 | 2027-03-19 | 620 / 680 | 4.34 % | 4.92 % | credit 0.75 | \U0001F534 ex_dividend |",
    "| 2 | 2027-03-19 | 610 / 680 | 5.88 % | 4.92 % | credit 3.30 | \U0001F534 ex_dividend |",
    "| 3 | 2027-03-19 | 600 / 680 | 7.42 % | 4.92 % | credit 5.60 | \U0001F534 ex_dividend |",
    "",
    "- 1, 2, 3 fail ex_dividend: the ex-dividend date 2026-12-18 (estimated, cum 2026-12-17) falls inside "
    "the call's life",
    "- 1, 2, 3 fail ex_dividend: a second ex-date may fall before the call's last trading day; the calendar "
    "holds one",
    "- not paired, outside their bands: 7 puts outside your floor band and 10 calls outside your cap band",
    "",
    "> - cap_within_budget: the cap's distance above the share price, in percent, among pairs within your "
    "net-debit budget",
    "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
    "> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style "
    "options is not modelled",
    "> - criteria: your structuring.toml (sha 5155427c)",
    "> - you supply the limit; the desk prints none",
    "> - if you decide to act, you type it into your brokerage account yourself",
    "",
    FOOTER_1931,
]
SPY_PP_TEXT = [
    "### PROPOSAL AG-20261028-03 | a protective put on the 100 SPY shares you hold",
    "",
    "| account | SPY mid | your cost | quotes |",
    "|---|--:|--:|---|",
    "| 2 of desk.toml (ira) | 648.12 | 632.40 | 19:31 UTC, 15-min delayed |",
    "",
    "**BEST CANDIDATE 1 of 12** by your objective stress_cost_per_day: **0.1856**, the lowest among the 5 "
    "candidates that passed every criterion",
    "- leg 1 of 1: opens a new long 1 x SPY 2027-03-19 610 put | bid 22.90, ask 23.10, mid 23.00 | quote "
    "grade A",
    "",
    "> the score ranks the candidates only on the objective you chose; it is not a forecast",
    "",
    "| check | this candidate | your criteria |  |",
    "|---|---|---|:-:|",
    "| floor | 5.88 % below | 3 to 10 % | \U0001F7E2 |",
    "| cost | ask 23.10 (2,310.00 in all) |  |  |",
    "| covered at your stress drop | 91.504 per share (9,150.40 in all) | drop 20 % (stressed price 518.496) "
    "| \U0001F7E2 |",
    "| calendar days | 136 | 120 to 210 | \U0001F7E2 |",
    "| spread | 0.87 % | at most 25 % | \U0001F7E2 |",
    "",
    "| the group | now | after the leg |",
    "|---|--:|--:|",
    "| max loss | 63,240.00 | 4,550.00 |",
    "| max profit | unbounded | unbounded |",
    "| breakeven | 632.40 | 655.50 |",
    "| group delta | 100.00 | 69.69 |",
    "",
    "**approval-level check:** the highest step needs level 2 by the desk's convention, within your "
    "self-attested level 2 (desk.toml); your broker's own approval for this account decides",
    "",
    "| after | you hold | level |",
    "|---|---|:-:|",
    "| leg 1 | 100 SPY shares, 1 long put | 2 |",
    "",
    "**candidates by rank:** 5 of 12 passed your criteria; 7 failed (floor_pct 7)",
    "",
    "| # | expiry | strike | floor | ask | stress_cost_per_day |  |",
    "|--:|---|---|--:|--:|--:|---|",
    "| 1 | 2027-03-19 | 610 | 5.88 % | 23.10 | 0.1856 | \U0001F7E2 |",
    "| 2 | 2027-03-19 | 620 | 4.34 % | 25.65 | 0.1858 | \U0001F7E2 |",
    "| 3 | 2027-03-19 | 625 | 3.57 % | 27.05 | 0.1868 | \U0001F7E2 |",
    "",
    "> - stress_cost_per_day: the put's ask per calendar day per 100 of loss it offsets at your stress drop",
    "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
    "> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style "
    "options is not modelled",
    "> - criteria: your structuring.toml (sha 5155427c)",
    "> - you supply the limit; the desk prints none",
    "> - if you decide to act, you type it into your brokerage account yourself",
    "",
    FOOTER_1931,
]
WMT_BEST_TEXT = [
    "### PROPOSAL AG-20261020-01 | a covered call on the 200 WMT shares you hold",
    "",
    "| account | WMT mid | your cost | quotes |",
    "|---|--:|--:|---|",
    "| 1 of desk.toml (margin) | 104.23 | 98.50 | 19:31 UTC, 15-min delayed |",
    "",
    "**BEST CANDIDATE 1 of 20** by your objective extrinsic_per_day: **0.0597**, the highest among the 5 "
    "candidates that passed every criterion",
    "- leg 1 of 1: opens a new short 2 x WMT 2026-12-18 112.5 call | bid 2.80, ask 2.90, mid 2.85 | quote "
    "grade B",
    "",
    "> the score ranks the candidates only on the objective you chose; it is not a forecast",
    "",
    "| check | this candidate | your criteria |  |",
    "|---|---|---|:-:|",
    "| strike | 112.5 | at or above your cost 98.50 | \U0001F7E2 |",
    "| absolute delta | 0.32 | 0.15 to 0.35 | \U0001F7E2 |",
    "| bid | 2.80 (560.00 in all) | at least 0.50 | \U0001F7E2 |",
    "| calendar days | 45 | 40 to 75 | \U0001F7E2 |",
    "| spread | 3.51 % | at most 25 % | \U0001F7E2 |",
    "| earnings | 2026-11-17 (before the open), inside the call's life | accepted by #earnings-ok | \U0001F7E1 |",
    "| ex-dividend | 2026-11-24 (cum 2026-11-23), inside the call's life | accepted by #assign-ok | \U0001F7E1 |",
    "",
    "| the group | now | after the leg |",
    "|---|--:|--:|",
    "| max loss | 19,700.00 | 19,140.00 |",
    "| max profit | unbounded | 3,360.00 |",
    "| breakeven | 98.50 | 95.70 |",
    "| group delta | 200.00 | 135.05 |",
    "",
    "**approval-level check:** the highest step needs level 1 by the desk's convention, within your "
    "self-attested level 2 (desk.toml); your broker's own approval for this account decides",
    "",
    "| after | you hold | level |",
    "|---|---|:-:|",
    "| leg 1 | 200 WMT shares, 2 short calls | 1 |",
    "",
    "**candidates by rank:** 5 of 20 passed your criteria; 15 failed (abs_delta 15, strike_floor 4)",
    "",
    "| # | expiry | strike | delta | bid | extrinsic_per_day |  |",
    "|--:|---|---|--:|--:|--:|---|",
    "| 1 | 2026-12-18 | 112.5 | 0.32 | 2.80 | 0.0597 | \U0001F7E2 |",
    "| 2 | 2027-01-15 | 115 | 0.33 | 3.55 | 0.0467 | \U0001F7E2 |",
    "| 3 | 2026-12-18 | 115 | 0.27 | 2.10 | 0.0448 | \U0001F7E2 |",
    "",
    "> - extrinsic_per_day: the bid's time value per calendar day, in percent of the share price",
    "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
    "> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style "
    "options is not modelled",
    "> - criteria: your structuring.toml (sha 5155427c)",
    "> - you supply the limit; the desk prints none",
    "> - if you decide to act, you type it into your brokerage account yourself",
    "",
    FOOTER_1931,
]
WMT_NONE_TEXT = [
    "### PROPOSAL AG-20261020-01 | a covered call on the 200 WMT shares you hold",
    "",
    "| account | WMT mid | your cost | quotes |",
    "|---|--:|--:|---|",
    "| 1 of desk.toml (margin) | 104.23 | 98.50 | 19:31 UTC, 15-min delayed |",
    "",
    "**no candidate passed every criterion: the desk names no best candidate**",
    "",
    "**candidates by rank:** 0 of 20 passed your criteria; 20 failed (abs_delta 15, strike_floor 4, earnings "
    "20, ex_dividend 20)",
    "",
    "| # | expiry | strike | delta | bid | extrinsic_per_day |  |",
    "|--:|---|---|--:|--:|--:|---|",
    "| 1 | 2026-12-18 | 112.5 | 0.32 | 2.80 | 0.0597 | \U0001F534 earnings, ex_dividend |",
    "| 2 | 2027-01-15 | 115 | 0.33 | 3.55 | 0.0467 | \U0001F534 earnings, ex_dividend |",
    "| 3 | 2026-12-18 | 115 | 0.27 | 2.10 | 0.0448 | \U0001F534 earnings, ex_dividend |",
    "",
    "- 1, 2, 3 fail earnings: earnings on 2026-11-17 (before the open) fall inside the call's life",
    "- 1, 2, 3 fail ex_dividend: the ex-dividend date 2026-11-24 (cum 2026-11-23) falls inside the call's "
    "life",
    "",
    "> - extrinsic_per_day: the bid's time value per calendar day, in percent of the share price",
    "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
    "> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style "
    "options is not modelled",
    "> - criteria: your structuring.toml (sha 5155427c)",
    "> - you supply the limit; the desk prints none",
    "> - if you decide to act, you type it into your brokerage account yourself",
    "",
    FOOTER_1931,
]
SPY_EX = ("FAILS_CRITERION ex_dividend: the ex-dividend date 2026-12-18 (estimated, cum 2026-12-17) falls inside the "
          "call's life | FAILS_CRITERION ex_dividend: a second ex-date may fall before the call's last trading day; the "
          "calendar holds one")
UNACKNOWLEDGED_TAIL = ("the desk names no candidate and prints no ticket while desk.toml, positions.csv or structuring.toml "
                       "differ from what you acknowledged | acknowledge with ack.py config --owner-tapped")


def call(main: Any, argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


def order_shaped(text: str) -> List[str]:
    return [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(text)]


def sha256_file(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class DeskCopy(unittest.TestCase):
    """A copy of the fixture desk; ``prepare()`` adds structuring.toml and acknowledges every file."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-propose-tool-")
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)

    def init_structuring(self) -> None:
        code, out = call(desk_init_tool.main, ["--answers", ANSWERS, "--desk-root", self.root, "--json"])
        self.assertEqual(0, code, out)

    def ack(self, now: str = ACK_NOW) -> None:
        code, out = call(ack_tool.main, ["config", "--owner-tapped", "--offline", "--desk-root", self.root, "--now", now,
                                         "--json"])
        self.assertEqual(0, code, out)

    def prepare(self) -> None:
        self.init_structuring()
        self.ack()

    def edit(self, relative: str, old: str, new: str) -> None:
        path = os.path.join(self.root, relative)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(1, text.count(old), old)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new))

    def drop_row(self, marker: str) -> None:
        with open(self.paths.positions_csv, encoding="utf-8") as handle:
            lines = handle.read().split("\n")
        kept = [line for line in lines if marker not in line]
        self.assertEqual(len(lines) - 1, len(kept))
        with open(self.paths.positions_csv, "w", encoding="utf-8") as handle:
            handle.write("\n".join(kept))

    def argv(self, trade: str, structure: str, now: str = REGULAR_NOW, fixture_set: Optional[str] = None,
             json_mode: bool = True) -> List[str]:
        argv = ["--trade", trade, "--structure", structure, "--offline", "--desk-root", self.root, "--now", now]
        if fixture_set is not None:
            argv += ["--fixture-set", fixture_set]
        return argv + (["--json"] if json_mode else [])

    def run_json(self, trade: str, structure: str, now: str = REGULAR_NOW, fixture_set: Optional[str] = None,
                 expected: int = 0) -> Dict[str, Any]:
        code, out = call(tool.main, self.argv(trade, structure, now, fixture_set))
        self.assertEqual([], order_shaped(out))
        body = json.loads(out)
        self.assertEqual(expected, code, out[:3000])
        return body

    def run_text(self, trade: str, structure: str, now: str = REGULAR_NOW, fixture_set: Optional[str] = None,
                 expected: int = 0) -> List[str]:
        code, out = call(tool.main, self.argv(trade, structure, now, fixture_set, json_mode=False))
        self.assertEqual([], order_shaped(out))
        self.assertEqual(expected, code, out)
        self.assertTrue(out.endswith("\n"))
        return out[:-1].split("\n")

    def journal(self) -> List[Dict[str, Any]]:
        records, status = state.read_jsonl(self.paths.structuring_journal)
        return records if status == "ok" else []

    def reports(self) -> List[str]:
        try:
            return sorted(os.listdir(self.paths.proposals_dir))
        except FileNotFoundError:
            return []

    def ledger(self) -> List[Dict[str, Any]]:
        records, status = state.read_jsonl(os.path.join(self.paths.cache_dir, "ledger.jsonl"))
        return records if status == "ok" else []

    # the shares-only copies of designer Q's D10 (row 4 or row 8 removed), with or without the tags
    def wmt_shares_only(self, tags: bool = False) -> None:
        self.drop_row(WMT_CALL_ROW)
        if tags:
            self.edit("positions.csv", *WMT_SHARES_NOTE)
        self.prepare()

    def spy_shares_only(self, tags: bool = False) -> None:
        self.drop_row(SPY_PUT_ROW)
        if tags:
            self.edit("positions.csv", *SPY_SHARES_ROW)
        self.prepare()


class SchemaTest(unittest.TestCase):
    def test_schema_describes_every_payload_key_without_desk_or_network(self) -> None:
        code, out = call(tool.main, ["--schema"])
        self.assertEqual(0, code)
        self.assertEqual([], order_shaped(out))
        body = json.loads(out)
        self.assertEqual(("propose", "read-only"), (body["tool"], body["mode"]))
        payload = body["envelope"]["payload"]
        self.assertEqual(set(tool.PAYLOAD_KEYS), set(payload))
        self.assertEqual(tuple(structuring.ANSWER_KEYS) + ("approval", "config_changed", "message", "emitted", "lint",
                                                          "withheld", "files", "journal"), tool.PAYLOAD_KEYS)
        self.assertEqual({"0", "1", "2", "10", "20"}, set(body["exit_codes"]))

    def test_the_flags(self) -> None:
        parser = tool.build_parser("0.1.0")
        actions = {option: action for action in parser._actions for option in action.option_strings}
        self.assertTrue({"--trade", "--structure", "--fixture-set", "--json", "--schema", "--timeout", "--desk-root",
                         "--offline", "--now"} <= set(actions))
        self.assertEqual(["covered_call", "protective_put", "collar"], list(actions["--structure"].choices))
        for forbidden in ("--symbol", "--to", "--candidate", "--target"):
            self.assertNotIn(forbidden, actions)


class UsageTest(DeskCopy):
    def usage(self, argv: Sequence[str]) -> None:
        code, out = call(tool.main, list(argv) + ["--json"])
        self.assertEqual(2, code, out)
        body = json.loads(out)
        self.assertEqual(("usage", "invalid_arguments"), (body["status"], body["error"]["code"]))

    def test_usage_errors_exit_2(self) -> None:
        common = ["--offline", "--desk-root", self.root, "--now", REGULAR_NOW]
        self.usage(["--structure", "collar"] + common)
        self.usage(["--trade", SPY] + common)
        self.usage(["--trade", SPY, "--structure", "vertical"] + common)
        self.usage(["--trade", SPY, "--structure", "cash_secured_put"] + common)
        self.usage(["--trade", "SPY", "--structure", "collar"] + common)
        self.usage(["--trade", SPY, "--structure", "collar", "--symbol", "SPY"] + common)
        self.usage(["--trade", SPY, "--structure", "collar", "--desk-root", self.root, "--fixture-set", "regular"])
        self.assertEqual([], self.journal())


class MainFixtureTest(DeskCopy):
    """The main fixture holds no shares-only group: every proposal is NOTHING_TO_PROPOSE, with no request."""

    def setUp(self) -> None:
        super().setUp()
        self.prepare()

    def test_the_protective_put_group(self) -> None:
        self.assertEqual(PP_NOTHING_TEXT, self.run_text(SPY, "protective_put"))
        payload = self.run_json(SPY, "protective_put")["payload"]
        self.assertEqual(("NOTHING_TO_PROPOSE", "not_shares_only", "protective_put", 0),
                         (payload["verdict"], payload["cause"], payload["structure_requested"],
                          payload["requests"]["made"]))
        self.assertIsNone(payload["approval"])
        self.assertEqual([], self.ledger())

    def test_the_covered_call_group(self) -> None:
        self.assertEqual(CC_NOTHING_TEXT, self.run_text(WMT, "covered_call"))
        payload = self.run_json(WMT, "covered_call")["payload"]
        self.assertEqual(("NOTHING_TO_PROPOSE", 0), (payload["verdict"], payload["requests"]["made"]))
        # BD6: a NOTHING_TO_PROPOSE writes no report, only its journal record
        self.assertEqual([], self.reports())
        self.assertIsNone(payload["files"])
        self.assertEqual(2, len(self.journal()))

    def test_a_collar_on_a_covered_call_group(self) -> None:
        payload = self.run_json(NVDA, "collar")["payload"]
        self.assertEqual(("NOTHING_TO_PROPOSE", "not_shares_only"), (payload["verdict"], payload["cause"]))


class GoldenTest(DeskCopy):
    def test_wmt_covered_call_without_the_tags_is_none_qualifies(self) -> None:
        self.wmt_shares_only()
        self.assertEqual(WMT_NONE_TEXT, self.run_text(WMT, "covered_call"))
        payload = self.run_json(WMT, "covered_call")["payload"]
        self.assertEqual(("NONE_QUALIFIES", None, 0, 20, "long_stock"),
                         (payload["verdict"], payload["best"], payload["passed"], payload["failed"],
                          payload["structure"]))

    def test_wmt_covered_call_with_the_tags_is_best(self) -> None:
        self.wmt_shares_only(tags=True)
        body = self.run_json(WMT, "covered_call")
        self.assertEqual(WMT_BEST_TEXT, self.run_text(WMT, "covered_call"))
        payload = body["payload"]
        self.assertEqual(("ok", "BEST", "WMT261218C00112500", "covered_call", "covered_call", 2, "200"),
                         (body["status"], payload["verdict"], payload["best"], payload["structure_requested"],
                          payload["section"], payload["contracts"], payload["shares"]))
        best = payload["candidates"][0]
        self.assertEqual(("0.0597", "560.00", "ELIGIBLE"), (best["score"], best["credit_total"], best["status"]))
        leg = best["legs"][0]
        self.assertEqual(("2.80", "2.90", "2.85", "B", "3.51", "0.32", 45, 32),
                         (leg["bid"], leg["ask"], leg["mid"], leg["grade"], leg["spread_pct"], leg["abs_delta"],
                          leg["dte_calendar"], leg["dte_trading"]))
        self.assertEqual({"max_loss": "19140.00", "max_profit": "3360.00", "breakeven": "95.70", "group_delta": 135.05},
                         {"max_loss": payload["after"]["max_loss"], "max_profit": payload["after"]["max_profit"],
                          "breakeven": payload["after"]["breakeven"],
                          "group_delta": round(payload["after"]["group_delta"], 2)})
        self.assertEqual([["#earnings-ok", "crosses_earnings"], ["#assign-ok", "crosses_ex_dividend"]],
                         [[label["by"], label["code"]] for label in best["accepted"]])
        self.assertEqual({"stage": "best_candidate", "ok": True, "owner_level": 2, "required_level": 1},
                         {k: payload["approval"][k] for k in ("stage", "ok", "owner_level", "required_level")})
        self.assertEqual({"made": 6, "chains": 2, "per_run_max_requests": 40, "per_run_max_chains": 4,
                          "budget_left": 174}, payload["requests"])
        self.assertEqual("\n".join(WMT_BEST_TEXT), payload["message"])
        self.assertTrue(payload["lint"]["ok"])

    def test_spy_protective_put_is_best(self) -> None:
        self.spy_shares_only()
        payload = self.run_json(SPY, "protective_put")["payload"]
        self.assertEqual(SPY_PP_TEXT, self.run_text(SPY, "protective_put"))
        self.assertEqual(("BEST", "SPY270319P00610000", 1), (payload["verdict"], payload["best"], payload["contracts"]))
        self.assertEqual({"drop_pct": "20", "spot": "518.496"}, payload["stress"])
        best = payload["candidates"][0]
        self.assertEqual(("0.1856", "5.88", "91.504", "9150.40", "2310.00"),
                         (best["score"], best["floor_pct"], best["covered_per_share"], best["covered_total"],
                          best["cost_total"]))
        self.assertEqual(("SPY270319P00620000", "ELIGIBLE", "0.1858"),
                         (payload["candidates"][1]["id"], payload["candidates"][1]["status"],
                          payload["candidates"][1]["score"]))
        self.assertEqual(("4550.00", "655.50"), (payload["after"]["max_loss"], payload["after"]["breakeven"]))
        self.assertEqual((4, 1), (payload["requests"]["made"], payload["requests"]["chains"]))

    def test_spy_collar_without_the_tag_is_none_qualifies(self) -> None:
        self.spy_shares_only()
        self.assertEqual(SPY_COLLAR_NONE_TEXT, self.run_text(SPY, "collar"))

    def test_spy_collar_with_assign_ok_is_best(self) -> None:
        self.spy_shares_only(tags=True)
        self.assertEqual(SPY_COLLAR_BEST_TEXT, self.run_text(SPY, "collar"))
        payload = self.run_json(SPY, "collar")["payload"]
        self.assertEqual(("BEST", "SPY270319P00620000+SPY270319C00680000", "put_strike"),
                         (payload["verdict"], payload["best"], payload["tie_break"]))
        best = payload["candidates"][0]
        self.assertEqual(("-0.75", "-75.00", "4.34", "4.92"),
                         (best["net_debit"], best["net_debit_total"], best["floor_pct"], best["cap_pct"]))
        self.assertEqual([["crosses_ex_dividend", "#assign-ok"], ["dividend_unknown", "#assign-ok"]],
                         [[label["code"], label["by"]] for label in best["accepted"]])
        self.assertEqual(("1165.00", "4835.00", "631.65"),
                         (payload["after"]["max_loss"], payload["after"]["max_profit"], payload["after"]["breakeven"]))
        self.assertEqual({"puts": 7, "calls": 10}, payload["outside_bands"])
        self.assertEqual([1, 2], [step["index"] for step in payload["approval"]["steps"]])
        self.assertEqual([2, 1], [step["level"] for step in payload["approval"]["steps"]])

    def test_the_owner_typed_account_name_is_never_printed(self) -> None:
        self.spy_shares_only(tags=True)
        for structure in ("protective_put", "collar"):
            text = "\n".join(self.run_text(SPY, structure))
            self.assertNotIn("Roth", text)
            self.assertIsNone(structuring_text.OCC_RE.search(text))


class ApplicabilityTest(DeskCopy):
    def test_refused_at_a_lower_acknowledged_level_before_any_request(self) -> None:
        self.drop_row(SPY_PUT_ROW)
        self.edit("desk.toml", "approval_level = 2 ", "approval_level = 1 ")
        self.prepare()
        self.assertEqual(COLLAR_REFUSED_TEXT, self.run_text(SPY, "collar", expected=20))
        payload = self.run_json(SPY, "collar", expected=20)["payload"]
        self.assertEqual(("REFUSED", "approval_level", [], None, 0),
                         (payload["verdict"], payload["cause"], payload["candidates"], payload["best"],
                          payload["requests"]["made"]))
        self.assertEqual({"stage": "before_market_data", "ok": False, "owner_level": 1, "required_level": 2,
                          "refusal_step": 1},
                         {k: payload["approval"][k] for k in ("stage", "ok", "owner_level", "required_level",
                                                             "refusal_step")})
        self.assertEqual([], self.ledger())
        self.assertEqual([], self.reports())
        self.assertEqual(("REFUSED", "approval_level", "collar"),
                         (self.journal()[-1]["verdict"], self.journal()[-1]["refused_code"],
                          self.journal()[-1]["structure"]))
        # a covered call needs level 1 only: it passes the check and goes on to the market data (the regular set holds
        # no 2027-01-15 SPY chain, designer Q's D2, so the in-window chain is not served)
        payload = self.run_json(SPY, "covered_call", expected=20)["payload"]
        self.assertEqual(("UNAVAILABLE", "chain_not_served", True, 1),
                         (payload["verdict"], payload["cause"], payload["approval"]["ok"],
                          payload["approval"]["required_level"]))
        self.assertGreater(payload["requests"]["made"], 0)

    def test_a_covered_call_at_level_0(self) -> None:
        self.drop_row(WMT_CALL_ROW)
        self.edit("desk.toml", "approval_level = 2 ", "approval_level = 0 ")
        self.prepare()
        self.assertEqual(CC_REFUSED_TEXT, self.run_text(WMT, "covered_call", expected=20))
        self.assertEqual([], self.ledger())

    def test_a_disabled_section_is_not_applicable(self) -> None:
        self.drop_row(SPY_PUT_ROW)
        self.init_structuring()
        self.edit("structuring.toml", "[collar]\nenabled = true", "[collar]\nenabled = false")
        self.ack()
        self.assertEqual(COLLAR_DISABLED_TEXT, self.run_text(SPY, "collar"))
        payload = self.run_json(SPY, "collar")["payload"]
        self.assertEqual(("NOT_APPLICABLE", "disabled", 0), (payload["verdict"], payload["cause"],
                                                             payload["requests"]["made"]))

    def test_no_free_shares(self) -> None:
        # the WMT call moved to a trade of its own: the 200 shares are a shares-only group, but the account's short
        # calls on WMT already rely on them (coverage is per account, whatever the group)
        self.edit("positions.csv", "Brokerage (margin),AG-20261020-01,2026-10-20,-684.00",
                  "Brokerage (margin),AG-20261020-02,2026-10-20,-684.00")
        self.prepare()
        self.assertEqual(NO_FREE_SHARES_TEXT, self.run_text(WMT, "covered_call"))
        payload = self.run_json(WMT, "covered_call")["payload"]
        self.assertEqual(("NOTHING_TO_PROPOSE", "no_free_shares", 0), (payload["verdict"], payload["cause"],
                                                                       payload["requests"]["made"]))


class FailClosedTest(DeskCopy):
    def test_criteria_missing(self) -> None:
        self.drop_row(SPY_PUT_ROW)
        self.ack()
        self.assertEqual(CRITERIA_MISSING_TEXT, self.run_text(SPY, "protective_put", expected=20))
        payload = self.run_json(SPY, "protective_put", expected=20)["payload"]
        self.assertEqual(("UNAVAILABLE", "criteria_missing", "protective_put"),
                         (payload["verdict"], payload["cause"], payload["structure_requested"]))
        self.assertEqual([], self.ledger())
        self.assertEqual([], self.reports())
        self.assertEqual(["criteria_missing", "criteria_missing"], [r["refused_code"] for r in self.journal()])

    def test_positions_csv_edited_after_the_acknowledgement(self) -> None:
        self.spy_shares_only()
        before = sha256_file(self.paths.positions_csv)
        self.edit("positions.csv", *SPY_SHARES_ROW)
        after = sha256_file(self.paths.positions_csv)
        expected = ["UNAVAILABLE | CONFIG CHANGED: positions.csv: sha %s -> sha %s | %s" % (before[:8], after[:8],
                                                                                        UNACKNOWLEDGED_TAIL),
                    "", footer("19:50")]
        self.assertEqual(expected, self.run_text(SPY, "collar", expected=20))
        payload = self.run_json(SPY, "collar", expected=20)["payload"]
        self.assertEqual(("UNAVAILABLE", "config_unacknowledged"), (payload["verdict"], payload["cause"]))
        self.assertEqual([], self.ledger())

    def test_structuring_invalid_is_exit_1(self) -> None:
        self.drop_row(SPY_PUT_ROW)
        self.init_structuring()
        self.edit("structuring.toml", "stress_drop_pct = 20", "stress_drop_pct = 5")
        self.ack()
        body = self.run_json(SPY, "protective_put", expected=1)
        self.assertEqual(("error", "structuring_invalid"), (body["status"], body["error"]["code"]))
        self.assertEqual([], self.journal())

    def test_an_unknown_trade_is_exit_1(self) -> None:
        self.prepare()
        body = self.run_json("AG-20991231-01", "collar", expected=1)
        self.assertEqual(("error", "unknown_trade"), (body["status"], body["error"]["code"]))
        self.assertEqual([], self.journal())


class MarketDataTest(DeskCopy):
    def test_the_stale_set_is_unavailable_after_one_request(self) -> None:
        self.spy_shares_only()
        lines = self.run_text(SPY, "protective_put", fixture_set="stale", expected=20)
        self.assertEqual(["UNAVAILABLE | AG-20261028-03 protective put proposal | no usable quote of the underlying",
                          "", footer("19:50")], lines)
        self.assertEqual(1, len(self.ledger()))
        self.assertEqual([], self.reports())

    def test_a_holiday_makes_no_request(self) -> None:
        self.spy_shares_only()
        lines = self.run_text(SPY, "collar", now=HOLIDAY_NOW, fixture_set="holiday", expected=20)
        self.assertEqual(["UNAVAILABLE | AG-20261028-03 collar proposal | the market is closed today", "",
                          alert_lint.structuring_footer_line(contract.parse_utc_instant(HOLIDAY_NOW))], lines)
        self.assertEqual([], self.ledger())

    def test_every_request_is_a_get_through_the_read_only_transport(self) -> None:
        self.spy_shares_only(tags=True)
        seen: List[str] = []
        original = alpaca_client.OfflineTransport.fetch

        def recording(transport: Any, url: str, headers: Dict[str, str], timeout_s: float) -> Any:
            seen.append(url)
            return original(transport, url, headers, timeout_s)

        with mock.patch.object(alpaca_client.OfflineTransport, "fetch", autospec=True, side_effect=recording), \
                mock.patch("http.client.HTTPConnection.request", side_effect=AssertionError("no socket offline")):
            payload = self.run_json(SPY, "collar")["payload"]
        paths = [urlsplit(url).path for url in seen]
        self.assertEqual(["/v2/stocks/quotes/latest", "/v2/options/contracts", "/v2/options/contracts",
                          "/v1beta1/options/snapshots/SPY"], paths, "shares only: one stock quote, then the listings")
        self.assertEqual([alpaca_client.host_for_path(path) for path in paths], [urlsplit(url).hostname for url in seen])
        self.assertEqual({"made": 4, "chains": 1, "per_run_max_requests": 40, "per_run_max_chains": 4,
                          "budget_left": 176}, payload["requests"])
        self.assertEqual(paths, [record["path"] for record in self.ledger()])

    def test_now_is_honoured_and_no_wall_clock_is_read(self) -> None:
        self.spy_shares_only()
        with mock.patch("aegis_desk.state.utc_now", side_effect=AssertionError("the wall clock was read")):
            body = self.run_json(SPY, "protective_put")
        self.assertEqual("2026-11-03T19:50:00+00:00", body["generated_at"])


class RecordTest(DeskCopy):
    def setUp(self) -> None:
        super().setUp()
        self.spy_shares_only()

    def test_the_report_files_and_the_journal_record(self) -> None:
        text = self.run_text(SPY, "protective_put")
        stem = "AG-20261028-03-protective_put-20261103T195000Z"
        self.assertEqual([stem + ".json", stem + ".md"], self.reports())
        with open(os.path.join(self.paths.proposals_dir, stem + ".md"), encoding="utf-8") as handle:
            self.assertEqual("# AG-20261028-03 protective put proposal (20261103T195000Z)\n\n```text\n%s\n```\n"
                             % "\n".join(text), handle.read())
        with open(os.path.join(self.paths.proposals_dir, stem + ".json"), encoding="utf-8") as handle:
            report = json.load(handle)
        self.assertEqual(set(tool.PAYLOAD_KEYS) - set(tool.REPORT_OMITTED_KEYS), set(report["payload"]))
        self.assertEqual(("ok", "SPY270319P00610000"), (report["status"], report["payload"]["best"]))
        record = self.journal()[0]
        self.assertEqual({
            "utc": "2026-11-03T19:50:00+00:00", "tool": "propose", "action": None, "structure": "protective_put",
            "trade_id": SPY, "verdict": "BEST", "criteria_sha256": CRITERIA_SHA,
            "desk_toml_sha256": sha256_file(self.paths.desk_toml),
            "positions_csv_sha256": sha256_file(self.paths.positions_csv), "best_candidate": "SPY270319P00610000",
            "candidates_passed": 5, "candidates_failed": 7, "refused_code": None,
            "text_sha256": hashlib.sha256("\n".join(text).encode("utf-8")).hexdigest()}, record)

    def test_a_rerun_is_idempotent_and_files_are_never_overwritten(self) -> None:
        first = self.run_json(SPY, "collar")["payload"]
        self.assertEqual({"json": "reports/proposals/AG-20261028-03-collar-20261103T195000Z.json",
                          "md": "reports/proposals/AG-20261028-03-collar-20261103T195000Z.md", "written": True},
                         first["files"])
        second = self.run_json(SPY, "collar")["payload"]
        self.assertEqual((0, False), (second["requests"]["made"], second["files"]["written"]))
        path = os.path.join(self.root, first["files"]["md"])
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("an edit\n")
        body = self.run_json(SPY, "collar", expected=1)
        self.assertEqual(("error", "ticket_exists"), (body["status"], body["error"]["code"]))
        self.assertEqual(2, len(self.journal()))

    def test_a_text_that_fails_the_lint_is_withheld(self) -> None:
        with mock.patch.object(structuring_text, "proposal_lines",
                               side_effect=lambda w, answer, level: ["PROPOSAL | the desk wrote 7.77 here"]):
            code, out = call(tool.main, self.argv(SPY, "protective_put", json_mode=False))
            body_code, body_out = call(tool.main, self.argv(SPY, "protective_put"))
        self.assertEqual((1, 1), (code, body_code))
        self.assertTrue(out.startswith("WITHHELD | the text of propose.py failed the alert lint ("), out)
        payload = json.loads(body_out)["payload"]
        self.assertIn("number_not_emitted", payload["withheld"]["codes"])
        self.assertEqual(out[:-1], payload["message"])
        self.assertEqual([], self.reports())
        self.assertEqual([], self.journal())


QUARANTINED_ROW = ("option,WMT,WMT261218C00110000,-2,,,,Brokerage (margin),AG-20261030-05,2026-10-30,-400.00,,2.50,open,"
                   ",,,,")
FULL_CALL_ROW = ("option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,open,,,,,"
                 "covered call #assign-ok")


class FixerTest(DeskCopy):
    """The PR 3b review, through the tool: BD5, L2-1/L3-1 (repro B), L3-4, L3-6, BD4 and L3-3."""

    def test_one_source_of_the_disabled_words(self) -> None:
        self.assertFalse(hasattr(tool, "DISABLED_WORDS"), "BD5: the library's words are the only source")

    def test_a_quarantined_group_fails_closed(self) -> None:
        self.edit("positions.csv", FULL_CALL_ROW, QUARANTINED_ROW)
        self.edit("positions.csv", *WMT_SHARES_NOTE)
        self.prepare()
        for structure in ("covered_call", "protective_put", "collar"):
            with self.subTest(structure=structure):
                payload = self.run_json(WMT, structure, expected=20)["payload"]
                self.assertEqual(("UNAVAILABLE", "positions_quarantined", 0, None),
                                 (payload["verdict"], payload["cause"], payload["requests"]["made"], payload["best"]))
        self.assertEqual([], self.reports())

    def test_partly_free_shares_say_how_many_are_free(self) -> None:
        self.edit("positions.csv", "equity,WMT,,200,", "equity,WMT,,300,")
        self.edit("positions.csv", FULL_CALL_ROW, "option,WMT,WMT261120C00105000,-1,,,,Brokerage (margin),AG-20261030-01,"
                                                  "2026-10-30,-342.00,,3.42,open,,,,,one call")
        self.prepare()
        lines = self.run_text(WMT, "covered_call")
        self.assertEqual("NOTHING_TO_PROPOSE | AG-20261020-01 covered call proposal | 200 of the 300 WMT shares are free: 1 "
                         "short call held in another group of this account relies on the rest; the desk proposes no "
                         "covered call on part of a group's shares", lines[0])
        self.assertEqual([], self.reports())

    def test_fractional_shares_are_proposed_on(self) -> None:
        self.drop_row(WMT_CALL_ROW)
        self.edit("positions.csv", "equity,WMT,,200,", "equity,WMT,,250.5,")
        self.prepare()
        payload = self.run_json(WMT, "covered_call")["payload"]
        self.assertEqual(("NONE_QUALIFIES", 2, "250.5"), (payload["verdict"], payload["contracts"], payload["shares"]))

    def test_refused_market_data_is_journaled(self) -> None:
        self.prepare()
        error = tool.quote_cache.MarketDataRefused("no_credential", tool.quote_cache.NO_CREDENTIAL_MESSAGE)
        with mock.patch.object(tool.quote_cache, "open_market_data", side_effect=error):
            code, out = call(tool.main, self.argv(SPY, "collar", json_mode=False))
        self.assertEqual(20, code)
        (record,) = self.journal()
        self.assertEqual(("UNAVAILABLE", "no_credential", "collar", hashlib.sha256(out[:-1].encode("utf-8")).hexdigest()),
                         (record["verdict"], record["refused_code"], record["structure"], record["text_sha256"]))
        self.assertIn(tool.quote_cache.NO_CREDENTIAL_MESSAGE, out)

    def test_two_runs_in_one_second_write_one_report(self) -> None:
        self.wmt_shares_only(tags=True)
        first = self.run_json(WMT, "covered_call", now="2026-11-03T19:50:00.100000Z")
        second = self.run_json(WMT, "covered_call", now="2026-11-03T19:50:00.900000Z")
        self.assertEqual((True, False), (first["payload"]["files"]["written"], second["payload"]["files"]["written"]))
        self.assertEqual("2026-11-03T19:50:00+00:00", second["generated_at"])


if __name__ == "__main__":
    unittest.main()
