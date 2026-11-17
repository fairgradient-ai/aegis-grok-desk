"""``scripts/beta_refresh.py`` (roadmap 2.7): each held underlying's beta against SPY from split-adjusted daily closes,
written to ``data/beta/<ET date>.json`` for the book. Offline runs read the copied fixture desk's regular set (one
split-adjusted bars request per symbol, then the cache); the file is the only thing written besides the cache."""

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
from typing import Dict, List, Sequence, Tuple

import beta_refresh as tool  # the tool scripts/beta_refresh.py
from aegis_desk import alert_lint, beta, book, contract, desk_paths, quote_cache

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
TODAY = datetime.date(2026, 11, 3)
TEXT = [
    "BETA | NVDA | 2.04 (r2 0.50, ok) over 251 daily returns",
    "BETA | SPY | 1.00 (the benchmark)",
    "BETA | WMT | 0.53 (r2 0.23, low) over 251 daily returns",
    "source: split-adjusted daily closes from the IEX feed, 2025-09-28 to 2026-11-02; written to "
    "data/beta/2026-11-03.json",
]


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = tool.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-beta-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def offline(self, *extra: str, now: str = REGULAR_NOW, json_mode: bool = False) -> Tuple[int, str]:
        code, out = run_tool(["--offline", "--desk-root", self.root, "--now", now] + list(extra)
                             + (["--json"] if json_mode else []))
        self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(out)])
        return code, out

    def ledger(self) -> List[Dict[str, object]]:
        path = quote_cache.ledger_path(self.paths)
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]

    def remove_split_fixture(self, symbol: str) -> None:
        os.remove(os.path.join(self.paths.alpaca_fixtures_dir, "regular",
                               "bars__%s__2025-09-28__2026-11-02__split.json" % symbol))


class SchemaAndUsageTest(DeskCopy):
    def test_schema(self) -> None:
        code, out = run_tool(["--schema"])
        self.assertEqual(0, code)
        body = json.loads(out)
        self.assertEqual("beta_refresh", body["tool"])
        self.assertIn("https://data.alpaca.markets/v2/stocks/bars (adjustment=split)", body["network_access"])
        self.assertEqual(sorted(tool.OUTPUT), sorted(body["envelope"]["payload"]))

    def test_usage_errors(self) -> None:
        for argv in (["--fixture-set", "regular", "--desk-root", self.root], ["--bogus"]):
            code, _ = run_tool(argv + ["--json"])
            self.assertEqual(2, code, argv)


class OfflineTest(DeskCopy):
    def test_the_regular_goldens_and_the_file(self) -> None:
        code, out = self.offline()
        self.assertEqual((0, TEXT), (code, out.splitlines()))
        self.assertEqual(["/v2/stocks/bars"] * 3, [record["path"] for record in self.ledger()],
                         "one split-adjusted bars request per symbol: SPY, then NVDA and WMT")
        fits = beta.load_betas(self.paths.beta_dir, TODAY)
        self.assertEqual({"NVDA": "ok", "SPY": "ok", "WMT": "low"}, {s: f.r2_flag for s, f in fits.items()})
        self.assertAlmostEqual(2.040109114663841, fits["NVDA"].beta, places=12)

    def test_a_second_run_reads_the_cache(self) -> None:
        self.offline()
        first = len(self.ledger())
        code, out = self.offline()
        self.assertEqual((0, TEXT, first), (code, out.splitlines(), len(self.ledger())), "closed bars are final")

    def test_json(self) -> None:
        code, out = self.offline(json_mode=True)
        payload = json.loads(out)["payload"]
        self.assertEqual((0, "ok", 0, "2026-11-03", "2025-09-28", "2026-11-02", "data/beta/2026-11-03.json"),
                         (code, payload["verdict"], payload["exit_code"], payload["et_date"], payload["bars_start"],
                          payload["bars_end"], payload["written"]))
        self.assertEqual(["NVDA", "SPY", "WMT"], [item["symbol"] for item in payload["betas"]])
        self.assertEqual([], payload["warnings"])

    def test_missing_bars_are_unavailable_exit_20_and_still_written(self) -> None:
        self.remove_split_fixture("WMT")
        code, out = self.offline()
        self.assertEqual(20, code)
        self.assertIn("UNAVAILABLE | WMT beta | %s" % beta.REASON_NO_BARS, out.splitlines())
        self.assertIn("BETA | NVDA | 2.04 (r2 0.50, ok) over 251 daily returns", out.splitlines())
        self.assertTrue(any(line.startswith("warning: history fetch failed for history WMT") for line in out.splitlines()))
        fits = beta.load_betas(self.paths.beta_dir, TODAY)
        self.assertEqual((None, beta.FLAG_UNAVAILABLE), (fits["WMT"].beta, fits["WMT"].r2_flag))
        code, out = self.offline(json_mode=True)
        self.assertEqual((20, "unavailable"), (code, json.loads(out)["payload"]["verdict"]))

    def test_the_book_reads_the_file(self) -> None:
        self.offline()
        market = quote_cache.open_market_data(self.root, now=contract.parse_utc_instant(REGULAR_NOW), offline=True,
                                              fixture_set=None, timeout_s=5.0, user_agent="aegis-desk/test beta")
        document = book.load_book(market).document
        self.assertEqual("low", document["portfolio"]["beta_weighted_delta"]["r2_flag"])
        self.assertIn("beta-weighted delta", book.portfolio_line(document))

    def test_an_unusable_positions_file_is_exit_1(self) -> None:
        os.remove(self.paths.positions_csv)
        code, out = self.offline(json_mode=True)
        self.assertEqual((1, "positions_missing"), (code, json.loads(out)["error"]["code"]))
        self.assertFalse(os.path.exists(self.paths.beta_dir))


if __name__ == "__main__":
    unittest.main()
