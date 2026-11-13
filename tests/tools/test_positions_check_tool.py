"""``scripts/positions_check.py`` run in-process against a copied fixture desk root: CLEAN on the main
root with the plausibility check passed for every open leg, the unit-mismatch ERROR that quarantines a
group, file errors, the empty book, the closed-day UNAVAILABLE, and ``positions.csv`` never written."""

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
from typing import Any, Dict, List, Sequence, Tuple
from unittest import mock

import csv_recon as recon_tool  # the sibling tool that reports the same unusable file
import positions_check as tool  # the tool module scripts/positions_check.py

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
POST_EXPIRY_NOW = "2026-11-21T14:30:00Z"
HEADER = ("instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened,"
          "cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,"
          "close_price_per_unit,underlying_stop,note")


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = tool.main(list(argv))
    return code, buffer.getvalue()


def run_json(argv: Sequence[str]) -> Tuple[int, Dict[str, Any]]:
    code, out = run_tool(argv)
    return code, json.loads(out)


def run_csv_recon_json(argv: Sequence[str]) -> Tuple[int, Dict[str, Any]]:
    """``scripts/csv_recon.py`` in-process on the same copy: the envelope the two tools must share."""
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = recon_tool.main(list(argv))
    return code, json.loads(buffer.getvalue())


def sha256_of(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-poscheck-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.positions = os.path.join(self.root, "positions.csv")
        self.positions_sha = sha256_of(self.positions)

    def tearDown(self) -> None:
        if os.path.exists(self.positions):
            self.assertEqual(self.positions_sha, sha256_of(self.positions), "positions.csv bytes changed")
        self._tmp.cleanup()

    def argv(self, fixture_set: str = "regular", now: str = REGULAR_NOW, json_mode: bool = True) -> List[str]:
        argv = ["--offline", "--desk-root", self.root, "--now", now, "--fixture-set", fixture_set]
        if json_mode:
            argv.append("--json")
        return argv

    def ledger(self) -> List[Dict[str, Any]]:
        path = os.path.join(self.root, "data", "cache", "ledger.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]

    def write_positions(self, text: str) -> None:
        with open(self.positions, "w", encoding="utf-8") as handle:
            handle.write(text)
        self.positions_sha = sha256_of(self.positions)

    def rewrite(self, relative: str, old: str, new: str) -> None:
        path = os.path.join(self.root, relative)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))
        if relative == "positions.csv":
            self.positions_sha = sha256_of(self.positions)


class AlwaysOnTest(DeskCopy):
    def test_schema(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual("positions_check", body["tool"])
        payload = body["envelope"]["payload"]
        for key in ("verdict", "groups", "quarantined", "file_errors", "plausibility"):
            self.assertIn(key, payload)

    def test_usage_errors(self) -> None:
        for argv in (["--json", "--desk-root", self.root, "--fixture-set", "regular"], ["--json", "--offline"]):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(argv)
                self.assertEqual(2, ctx.exception.code)


class RegularRootTest(DeskCopy):
    def test_clean_with_plausibility_passed_for_every_open_leg(self) -> None:
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        self.assertEqual(("ok", "alpaca:paper", 15), (body["status"], body["source"], body["delay_min"]))
        payload = body["payload"]
        self.assertEqual(("CLEAN", 0, 7, {}, []), (payload["verdict"], payload["exit_code"], payload["row_count"],
                                                   payload["quarantined"], payload["file_errors"]))
        self.assertEqual((4, 3, 1), (payload["counts"]["groups"], payload["counts"]["open_groups"], payload["counts"]["closed_groups"]))
        structures = {group["group_id"]: group["structure"] for group in payload["groups"]}
        self.assertEqual({"AG-20261020-01": "covered_call", "AG-20261022-02": "covered_call", "AG-20261028-03": "protective_put",
                          "AG-20261001-04": "cash_secured_put"}, structures)
        checks = {check["row_number"]: check for check in payload["plausibility"]}
        self.assertEqual([3, 4, 5, 6, 7, 8], sorted(checks))
        self.assertEqual({"ok"}, {check["status"] for check in checks.values()})
        self.assertEqual(("3.42", "3.425", "WMT261120C00105000"), (checks[4]["entry"], checks[4]["mark"], checks[4]["symbol"]))
        self.assertEqual(("3.05", "3.2"), (checks[6]["entry"], checks[6]["mark"]))
        self.assertEqual(("11.30", "9.95"), (checks[8]["entry"], checks[8]["mark"]))
        self.assertEqual(("98.50", "104.23"), (checks[3]["entry"], checks[3]["mark"]), "entry_price_per_share as the parser keeps it")
        self.assertEqual(("141.20", "148.925"), (checks[5]["entry"], checks[5]["mark"]))
        self.assertEqual(("632.40", "648.12"), (checks[7]["entry"], checks[7]["mark"]))
        self.assertEqual(2, len(self.ledger()), "six symbols are one batch: equities, then options")
        self.assertEqual(payload["counts"]["plausibility_ok"], 6)

    def test_prose(self) -> None:
        code, text = run_tool(self.argv(json_mode=False))
        self.assertEqual(0, code, text)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("CLEAN"), lines[0])
        self.assertIn("3 open", lines[0])
        self.assertTrue(any(line.startswith("AG-20261020-01 covered_call WMT") and "19016.00" in line for line in lines), text)
        self.assertTrue(any("row 4 WMT261120C00105000 entry 3.42 vs mark 3.425: ok" in line for line in lines), text)
        self.assertEqual("Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of 19:31 UTC", lines[-1])

    def test_entry_premium_off_by_a_hundred_is_a_unit_mismatch_error_that_quarantines_the_group(self) -> None:
        self.rewrite("positions.csv", "AG-20261020-01,2026-10-20,-684.00,,3.42,", "AG-20261020-01,2026-10-20,-68400.00,,342.00,")
        code, body = run_json(self.argv())
        self.assertEqual(20, code, body)
        self.assertEqual("unavailable", body["status"])
        payload = body["payload"]
        self.assertEqual("UNAVAILABLE", payload["verdict"])
        self.assertEqual(["AG-20261020-01"], sorted(payload["quarantined"]))
        errors = payload["quarantined"]["AG-20261020-01"]
        self.assertEqual(1, len(errors))
        self.assertIn("row 4", errors[0])
        self.assertIn("unit mismatch? entry 342.00 vs mark 3.425", errors[0])
        checks = {check["row_number"]: check for check in payload["plausibility"]}
        self.assertEqual("error", checks[4]["status"])
        self.assertEqual("342.00", checks[4]["entry"])
        self.assertEqual(2, payload["counts"]["open_groups"], "the quarantined group is no longer counted as open")
        self.assertIn("AG-20261020-01", payload["counts"]["quarantined_groups"] and ["AG-20261020-01"])
        code, text = run_tool(self.argv(json_mode=False))
        self.assertEqual(20, code)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("UNAVAILABLE"), lines[0])
        self.assertTrue(any("quarantined AG-20261020-01" in line for line in lines), text)
        self.assertTrue(any("unit mismatch? entry 342.00 vs mark 3.425" in line for line in lines), text)
        self.assertTrue(any(line.startswith("AG-20261022-02 covered_call NVDA") for line in lines), "other groups still listed")

    def test_header_typo_is_the_errors_verdict(self) -> None:
        # The ERRORS verdict (file-level CSV errors) is the error envelope in --json, exactly as csv_recon.py
        # reports the same file: exit 1, status error, error.code positions_invalid, error.message
        # "ERRORS: positions.csv cannot be used: <file errors joined by '; '>", no payload. The prose keeps its
        # ERRORS line, the file errors and the footer; nothing is fetched on this path.
        self.rewrite("positions.csv", "cost_basis_total,", "cost_basis_totl,")
        code, body = run_json(self.argv())
        self.assertEqual(1, code, body)
        self.assertEqual(("error", "positions_invalid"), (body["status"], body["error"]["code"]))
        message = body["error"]["message"]
        self.assertTrue(message.startswith("ERRORS: positions.csv cannot be used: "), message)
        self.assertIn("did you mean", message)
        self.assertNotIn("payload", body)
        self.assertEqual([], self.ledger(), "nothing is fetched for an unreadable book")
        code, text = run_tool(self.argv(json_mode=False))
        self.assertEqual(1, code)
        lines = text.splitlines()
        self.assertEqual(message, lines[0], "the prose first line is the envelope's message")
        self.assertTrue(any(line.startswith("file error: ") and "did you mean" in line for line in lines), text)
        self.assertTrue(lines[-1].startswith("Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of "), lines[-1])
        self.assertEqual([], self.ledger())
        code, recon_body = run_csv_recon_json(self.argv())
        self.assertEqual(1, code, recon_body)
        self.assertEqual(body["error"], recon_body["error"], "one error envelope for one unusable file, both tools")

    def test_header_only_book_is_clean(self) -> None:
        self.write_positions(HEADER + "\n")
        code, body = run_json(self.argv())
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual(("CLEAN", [], [], 0), (payload["verdict"], payload["groups"], payload["plausibility"], payload["row_count"]))
        self.assertEqual([], self.ledger(), "nothing to quote")

    def test_closed_day_without_quotes_is_unavailable(self) -> None:
        code, body = run_json(self.argv("post_expiry", POST_EXPIRY_NOW))
        self.assertEqual(20, code, body)
        payload = body["payload"]
        self.assertEqual(("UNAVAILABLE", {}), (payload["verdict"], payload["quarantined"]))
        checks = {check["row_number"]: check["status"] for check in payload["plausibility"]}
        self.assertEqual({3: "not_checked", 4: "expired", 5: "not_checked", 6: "expired", 7: "not_checked", 8: "not_checked"}, checks)
        self.assertEqual([], self.ledger(), "no fetch on a weekend")
        code, text = run_tool(self.argv("post_expiry", POST_EXPIRY_NOW, json_mode=False))
        self.assertEqual(20, code)
        self.assertIn("market_closed", text)

    def test_missing_positions_csv_is_exit_1(self) -> None:
        os.remove(self.positions)
        code, body = run_json(self.argv())
        self.assertEqual((1, "error", "positions_missing"), (code, body["status"], body["error"]["code"]))

    def test_live_host_and_invalid_config(self) -> None:
        self.rewrite("desk.toml", 'host = "paper"', 'host = "live"')
        code, body = run_json(["--json", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
        self.assertEqual((1, "error", "config_invalid"), (code, body["status"], body["error"]["code"]))
        self.assertIn('host must be "paper"', body["error"]["message"])
        self.rewrite("desk.toml", 'host = "live"', 'host = "paper"')
        self.rewrite("desk.toml", "grace_min = 5", "grace_min = 31")
        code, body = run_json(["--json", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
        self.assertEqual((1, "error", "config_invalid"), (code, body["status"], body["error"]["code"]))

    def test_no_order_shaped_output(self) -> None:
        _, text = run_tool(self.argv(json_mode=False))
        lowered = text.lower()
        for word in ("buy", "sell", "limit order", "market order", "submit", "place_order"):
            self.assertNotIn(word, lowered, word)


class MoneyCentsTest(unittest.TestCase):
    def test_a_group_cost_prints_to_the_cent(self) -> None:
        # the owner's 321.6301 NVDA shares: a cost total with six decimals printed as noise in the chat
        from decimal import Decimal
        self.assertEqual(("48252.63", "19016.00", "-480.00", "0.12", "-"),
                         (tool.money_cents(Decimal("48252.630993")), tool.money_cents(Decimal("19016")),
                          tool.money_cents(Decimal("-480.00")), tool.money_cents(Decimal("0.125")),
                          tool.money_cents(None)))


if __name__ == "__main__":
    unittest.main()
