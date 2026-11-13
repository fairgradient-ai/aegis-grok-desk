"""``scripts/csv_recon.py`` run in-process against copies of the two fixture desk roots: the recon
root's settlement branches at the Tuesday pre-open instant, the main root CLEAN on a regular day,
EDITS_PENDING on the post-expiry Saturday and UNAVAILABLE on Thanksgiving, the proposed-edit block,
the snapshot, the acks nag, the error paths, and the proof that ``positions.csv`` is never written."""

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

from aegis_desk import alert_lint, alerts, contract, desk_paths, positions_csv, recon, state

import csv_recon as tool  # the tool module scripts/csv_recon.py

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAIN_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
RECON_DESK = os.path.join(ROOT, "tests", "fixtures", "desk_recon")
RECON_NOW = "2026-11-24T12:00:00Z"
REGULAR_NOW = "2026-11-03T19:50:00Z"
POST_EXPIRY_NOW = "2026-11-21T14:30:00Z"
HOLIDAY_NOW = "2026-11-26T15:00:00Z"
FRIDAY_PREOPEN_NOW = "2026-11-20T12:00:00Z"  # 07:00 ET on the settlement morning, before the 09:30 ET print
SPY_PUT = "SPY261218P00620000"
RECON_EDIT_LINES = (
    "option,WMT,WMT261120P00095000,-1,,,,,AG-20261015-01,2026-10-15,-165.00,,1.65,closed,2026-11-20,1,0,,cash-secured put",
    "option,NVDA,NVDA261120C00150000,-2,,,,,AG-20261022-03,2026-10-22,-610.00,,3.05,closed,2026-11-20,2,0,,covered call",
    "equity,NVDA,,200,,,,,AG-20261022-03,2026-10-22,28240.00,,,closed,2026-11-20,200,150.00,,",
    "option,SPX,SPX261120C06500000,1,,,,,AG-20261101-05,2026-11-01,2450.00,,24.50,closed,2026-11-20,1,23.17,,AM-settled index call",
)


def posted_lint(payload: Dict[str, Any]) -> Any:
    """The routine's composition of a text part (PR 2d decision D2-3): the message through ``alerts.fmt_reason``, the
    alert footer at ``payload.as_of``, then the strict lint."""
    emitted: List[str] = []
    rule_ids: List[str] = []
    identifiers: List[str] = []
    clocks: List[str] = []
    text = alerts.fmt_reason(payload["message"], emitted, rule_ids=rule_ids, identifiers=identifiers, clocks=clocks)
    as_of = contract.parse_utc_instant(payload["as_of"])
    text += "\n" + alerts.fmt_footer(as_of, 15, emitted, clocks)
    return alert_lint.check(text, emitted, rule_ids=rule_ids or None, identifiers=identifiers, clocks=clocks, as_of=as_of)


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = tool.main(list(argv))
    return code, buffer.getvalue()


def run_json(argv: Sequence[str]) -> Tuple[int, Dict[str, Any]]:
    code, out = run_tool(argv)
    return code, json.loads(out)


def sha256_of(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class DeskCopy(unittest.TestCase):
    source = RECON_DESK

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-recon-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(self.source, self.root)
        self.positions = os.path.join(self.root, "positions.csv")
        self.positions_sha = sha256_of(self.positions)
        self.positions_stat = os.stat(self.positions)

    def tearDown(self) -> None:
        self.assert_positions_untouched()
        self._tmp.cleanup()

    def assert_positions_untouched(self) -> None:
        self.assertEqual(self.positions_sha, sha256_of(self.positions), "positions.csv bytes changed")
        after = os.stat(self.positions)
        self.assertEqual((self.positions_stat.st_mtime_ns, self.positions_stat.st_ino), (after.st_mtime_ns, after.st_ino))

    def argv(self, fixture_set: str, now: str, json_mode: bool = True, extra: Sequence[str] = ()) -> List[str]:
        argv = ["--offline", "--desk-root", self.root, "--now", now, "--fixture-set", fixture_set] + list(extra)
        if json_mode:
            argv.append("--json")
        return argv

    def ledger(self) -> List[Dict[str, Any]]:
        path = os.path.join(self.root, "data", "cache", "ledger.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]

    def history_files(self) -> List[str]:
        directory = os.path.join(self.root, "data", "cache", "history")
        return sorted(os.listdir(directory)) if os.path.isdir(directory) else []

    def rewrite(self, relative: str, old: str, new: str) -> None:
        path = os.path.join(self.root, relative)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))
        if relative == "positions.csv":
            self.positions_sha = sha256_of(self.positions)
            self.positions_stat = os.stat(self.positions)


class AlwaysOnTest(DeskCopy):
    def test_schema(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual("csv_recon", body["tool"])
        payload = body["envelope"]["payload"]
        for key in ("verdict", "settlements", "edits", "findings", "quote_check", "snapshot"):
            self.assertIn(key, payload)
        self.assertEqual("read-only", body["envelope"]["mode"])

    def test_usage_errors(self) -> None:
        for argv in (["--json", "--desk-root", self.root, "--fixture-set", "recon"],
                     ["--json", "--offline"],
                     ["--json", "--offline", "--desk-root", self.root, "--now", "yesterday"]):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(argv)
                self.assertEqual(2, ctx.exception.code)


class ReconRootTest(DeskCopy):
    def test_every_settlement_branch_and_the_verdict(self) -> None:
        code, body = run_json(self.argv("recon", RECON_NOW))
        self.assertEqual(20, code, body)
        self.assertEqual(("unavailable", "alpaca:paper", 15), (body["status"], body["source"], body["delay_min"]))
        payload = body["payload"]
        self.assertEqual(("UNAVAILABLE", 20, "2026-11-24", "pre", "recon"),
                         (payload["verdict"], payload["exit_code"], payload["et_date"], payload["session"]["session"],
                          payload["fixture_set"]))
        outcomes = {item["group_id"]: item["outcome"] for item in payload["settlements"]}
        self.assertEqual({
            "AG-20261015-01": "EXPIRED_OTM",
            "AG-20261020-02": "PIN_UNCERTAIN",
            "AG-20261022-03": "PROBABLE_ASSIGNED",
            "AG-20261023-04": "PIN_UNCERTAIN",
            "AG-20261101-05": "CASH_SETTLED",
            "AG-20261101-06": "SETTLEMENT_UNKNOWN",
            "AG-20261105-07": "EXPIRED_UNRECORDED",
        }, outcomes)
        by_group = {item["group_id"]: item for item in payload["settlements"]}
        self.assertEqual(("104.86", "105.3", "0"), (by_group["AG-20261015-01"]["close"], by_group["AG-20261015-01"]["next_open"],
                                                    by_group["AG-20261015-01"]["close_price"]))
        self.assertEqual("23.17", by_group["AG-20261101-05"]["close_price"])
        self.assertEqual("6523.17", by_group["AG-20261101-05"]["close"])
        self.assertEqual("fetch_failed:network", by_group["AG-20261105-07"]["history_reason"])
        self.assertEqual(list(RECON_EDIT_LINES), [edit["csv_line"] for edit in payload["edits"]])
        self.assertEqual(["update"] * 4, [edit["kind"] for edit in payload["edits"]])
        self.assertEqual([3, 7, 6, 9], [edit["row_number"] for edit in payload["edits"]])
        self.assertEqual(4, payload["counts"]["edits"])
        self.assertEqual(4, payload["counts"]["unavailable"])
        self.assertEqual({}, payload["quarantined"])
        self.assertEqual([], payload["file_errors"])

    def test_quote_check_is_the_spy_pair_and_history_is_one_request_per_underlying(self) -> None:
        code, body = run_json(self.argv("recon", RECON_NOW))
        self.assertEqual(20, code)
        payload = body["payload"]
        check = payload["quote_check"]
        self.assertEqual((["SPY", SPY_PUT], ["SPY", SPY_PUT], None, {}), (check["symbols"], check["quoted"], check["not_run_reason"],
                                                                        check["missing"]))
        history = {entry["symbol"]: entry for entry in payload["history"]}
        self.assertEqual(["AAPL", "AMD", "NVDA", "WMT"], sorted(history))
        self.assertEqual(("2026-11-20", "2026-11-23", "fetch", 2, None), (history["WMT"]["start"], history["WMT"]["end"],
                                                                        history["WMT"]["served_from"], history["WMT"]["bars"],
                                                                        history["WMT"]["missing_reason"]))
        self.assertEqual(("fetch_failed:network", 0, None), (history["AAPL"]["missing_reason"], history["AAPL"]["bars"],
                                                             history["AAPL"]["served_from"]))
        ledger = self.ledger()
        self.assertEqual(6, len(ledger), ledger)
        self.assertEqual(["/v2/stocks/bars"] * 4 + ["/v2/stocks/quotes/latest", "/v1beta1/options/quotes/latest"],
                         [record["path"] for record in ledger])
        self.assertEqual(["AMD__2026-11-20__2026-11-23.json", "NVDA__2026-11-20__2026-11-23.json", "WMT__2026-11-20__2026-11-23.json"],
                         self.history_files())

    def test_snapshot_of_the_file_bytes_is_written_beside_its_digest(self) -> None:
        code, body = run_json(self.argv("recon", RECON_NOW))
        self.assertEqual(20, code)
        snapshot = body["payload"]["snapshot"]
        self.assertEqual({"path": "data/positions/2026-11-24.csv", "sha256": self.positions_sha}, snapshot)
        self.assertEqual(self.positions_sha, body["payload"]["positions_sha256"])
        csv_path = os.path.join(self.root, "data", "positions", "2026-11-24.csv")
        self.assertEqual(self.positions_sha, sha256_of(csv_path))
        with open(os.path.join(self.root, "data", "positions", "2026-11-24.sha256"), encoding="utf-8") as handle:
            self.assertEqual("%s  2026-11-24.csv\n" % self.positions_sha, handle.read())

    def test_prose_and_the_diff_block(self) -> None:
        code, text = run_tool(self.argv("recon", RECON_NOW, json_mode=False))
        self.assertEqual(20, code, text)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("UNAVAILABLE"), lines[0])
        self.assertIn("4 proposed edit", lines[0])
        self.assertTrue(any(line.startswith("AG-20261015-01 row 3 WMT261120P00095000 -1 EXPIRED_OTM:") for line in lines), text)
        self.assertTrue(any("AG-20261020-02 row 5 WMT261120C00105000 -2 PIN_UNCERTAIN:" in line and "confirm with your broker" in line
                            for line in lines), text)
        self.assertTrue(any("RUT261120P02200000 -1 SETTLEMENT_UNKNOWN:" in line and "(short 2200 put x 1)" in line
                            for line in lines), text)
        self.assertTrue(any("AAPL261120P00230000 2 EXPIRED_UNRECORDED:" in line and "long 230 put x 2 by hand" in line
                            for line in lines), text)
        self.assertNotIn("x -1", text)
        self.assertIn("quote check: 2 symbols quoted (SPY, SPY261218P00620000)", lines)
        self.assertTrue(any(line.startswith("snapshot: data/positions/2026-11-24.csv") for line in lines), text)
        self.assertEqual("Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of 20:59 UTC", lines[-1])
        for edit_line in RECON_EDIT_LINES:
            self.assertNotIn(edit_line, lines, "the diff block prints only with --diff")
        code, text = run_tool(self.argv("recon", RECON_NOW, json_mode=False, extra=["--diff"]))
        self.assertEqual(20, code)
        lines = text.splitlines()
        for edit_line in RECON_EDIT_LINES:
            self.assertIn(edit_line, lines)
        header_index = next(i for i, line in enumerate(lines) if line.startswith("proposed edits"))
        self.assertIn("not applied", lines[header_index])
        self.assertTrue(lines[header_index + 1].startswith("# row 3 (AG-20261015-01) EXPIRED_OTM:"), lines[header_index + 1])
        self.assertEqual(RECON_EDIT_LINES[0], lines[header_index + 2])

    def test_positions_csv_is_read_once_and_the_snapshot_is_of_the_parsed_bytes(self) -> None:
        # Recon finding 5: a second open of positions.csv between the snapshot and the parse could describe
        # another file; the tool parses the bytes it snapshotted, so load_positions is never called.
        with mock.patch.object(positions_csv, "load_positions", side_effect=AssertionError("positions.csv opened twice")):
            code, body = run_json(self.argv("recon", RECON_NOW))
        self.assertEqual(20, code, body)
        payload = body["payload"]
        self.assertEqual(payload["snapshot"]["sha256"], payload["positions_sha256"])
        self.assertEqual(self.positions_sha, payload["positions_sha256"])
        self.assertEqual(7, payload["counts"]["expired_legs"])

    def test_friday_preopen_lists_the_am_legs_as_pending_not_unavailable(self) -> None:
        # Recon finding 4: at 07:00 ET on the settlement Friday the SPX and RUT legs have stopped trading (Thursday
        # was their last day) but the AM settlement value is struck at 09:30 ET. The recorded SPX value is used;
        # RUT has none and is SETTLEMENT_PENDING, never an UNAVAILABLE "record the official settlement value".
        code, body = run_json(self.argv("recon", FRIDAY_PREOPEN_NOW))
        payload = body["payload"]
        outcomes = {item["group_id"]: item["outcome"] for item in payload["settlements"]}
        self.assertEqual({"AG-20261101-05": "CASH_SETTLED", "AG-20261101-06": "SETTLEMENT_PENDING"}, outcomes)
        self.assertEqual((10, "fired", "EDITS_PENDING", 0, 1), (code, body["status"], payload["verdict"],
                                                              payload["counts"]["unavailable"], payload["counts"]["edits"]))
        rut = [item for item in payload["settlements"] if item["group_id"] == "AG-20261101-06"][0]
        self.assertEqual((False, None, None), (rut["unavailable"], rut["close"], rut["close_price"]))
        self.assertIn("settles at the open of 2026-11-20", rut["message"])
        self.assertEqual([], payload["history"], "no PM leg has expired before the Friday open")
        code, text = run_tool(self.argv("recon", FRIDAY_PREOPEN_NOW, json_mode=False))
        self.assertEqual(10, code)
        self.assertTrue(any("RUT261120P02200000 -1 SETTLEMENT_PENDING:" in line for line in text.splitlines()), text)
        self.assertNotIn("record the official settlement value", text)

    def test_second_run_serves_the_closed_history_from_the_cache(self) -> None:
        run_json(self.argv("recon", RECON_NOW))
        code, body = run_json(self.argv("recon", RECON_NOW))
        self.assertEqual(20, code)
        history = {entry["symbol"]: entry for entry in body["payload"]["history"]}
        self.assertEqual("cache", history["WMT"]["served_from"])
        self.assertEqual("cache", history["AMD"]["served_from"])
        self.assertEqual("fetch_failed:network", history["AAPL"]["missing_reason"], "a failure is never cached")
        self.assertEqual(7, len(self.ledger()), "three cached histories, one AAPL retry, quotes within the TTL")
        self.assertEqual(["SPY", SPY_PUT], body["payload"]["quote_check"]["quoted"])

    def test_acted_unchanged_nag_from_the_acks_journal(self) -> None:
        with open(self.positions, encoding="utf-8") as handle:
            table = recon.csv_table(handle.read())
        os.makedirs(os.path.join(self.root, "state"), exist_ok=True)
        state.append_jsonl(os.path.join(self.root, "state", "acks.jsonl"),
                           {"utc": "2026-11-20T19:05:00+00:00", "signal_id": "SIG-1", "trade_id": "AG-20261110-08", "action": "acted",
                            "until": None, "rows_sha": recon.group_rows_sha(table, "AG-20261110-08")})
        code, body = run_json(self.argv("recon", RECON_NOW))
        self.assertEqual(20, code)
        nags = [f for f in body["payload"]["findings"] if f["kind"] == "ACTED_UNCHANGED"]
        self.assertEqual(1, len(nags), body["payload"]["findings"])
        self.assertEqual(("warning", "AG-20261110-08"), (nags[0]["level"], nags[0]["group_id"]))
        code, text = run_tool(self.argv("recon", RECON_NOW, json_mode=False))
        self.assertIn("ACTED_UNCHANGED", text)

    def test_quarantined_group_is_listed_as_unavailable(self) -> None:
        self.rewrite("positions.csv", "AG-20261110-08,2026-11-10,1130.00,,11.30", "AG-20261110-08,2026-11-10,-1130.00,,11.30")
        code, body = run_json(self.argv("recon", RECON_NOW))
        self.assertEqual(20, code)
        payload = body["payload"]
        self.assertEqual(["AG-20261110-08"], sorted(payload["quarantined"]))
        kinds = [(f["kind"], f["level"], f["group_id"]) for f in payload["findings"]]
        self.assertIn(("QUARANTINED", "unavailable", "AG-20261110-08"), kinds)
        self.assertEqual([], payload["quote_check"]["symbols"], "the quarantined SPY group is not quoted")


class MainRootTest(DeskCopy):
    source = MAIN_DESK

    def test_regular_day_is_clean(self) -> None:
        code, body = run_json(self.argv("regular", REGULAR_NOW))
        self.assertEqual(0, code, body)
        self.assertEqual("ok", body["status"])
        payload = body["payload"]
        self.assertEqual(("CLEAN", 0, [], []), (payload["verdict"], payload["exit_code"], payload["settlements"], payload["edits"]))
        self.assertEqual(["NVDA", "NVDA261120C00150000", "SPY", SPY_PUT, "WMT", "WMT261120C00105000"],
                         payload["quote_check"]["quoted"])
        self.assertEqual([], payload["history"])
        self.assertEqual(2, len(self.ledger()), "six symbols are one batch: equities, then options")
        self.assertEqual([f["kind"] for f in payload["findings"]], [])
        self.assertEqual("data/positions/2026-11-03.csv", payload["snapshot"]["path"])
        code, text = run_tool(self.argv("regular", REGULAR_NOW, json_mode=False))
        self.assertEqual(0, code)
        self.assertTrue(text.splitlines()[0].startswith("CLEAN"), text)
        self.assertEqual("Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of 19:31 UTC", text.splitlines()[-1])

    def test_post_expiry_saturday_is_edits_pending(self) -> None:
        code, body = run_json(self.argv("post_expiry", POST_EXPIRY_NOW))
        self.assertEqual(10, code, body)
        self.assertEqual("fired", body["status"])
        payload = body["payload"]
        self.assertEqual(("EDITS_PENDING", 10, "weekend"), (payload["verdict"], payload["exit_code"], payload["session"]["session"]))
        outcomes = {item["group_id"]: item["outcome"] for item in payload["settlements"]}
        self.assertEqual({"AG-20261020-01": "EXPIRED_OTM", "AG-20261022-02": "PROBABLE_ASSIGNED"}, outcomes)
        self.assertEqual(3, len(payload["edits"]))
        self.assertEqual([
            "option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,closed,2026-11-20,2,0,,"
            "covered call #assign-ok",
            "option,NVDA,,-2,2026-11-20,CALL,150,Brokerage (margin),AG-20261022-02,2026-10-22,-610.00,,,closed,2026-11-20,2,0,,"
            "covered call from component columns #earnings-ok",
            "equity,NVDA,,200,,,,Brokerage (margin),AG-20261022-02,2026-10-22,28240.00,,,closed,2026-11-20,200,150.00,,",
        ], [edit["csv_line"] for edit in payload["edits"]])
        self.assertEqual("market_closed", payload["quote_check"]["not_run_reason"])
        self.assertIn("quote check not run: market_closed", [f["message"] for f in payload["findings"]])
        self.assertEqual(["NVDA__2026-11-20__2026-11-20.json", "WMT__2026-11-20__2026-11-20.json"], self.history_files())
        self.assertEqual(2, len(self.ledger()))
        code, text = run_tool(self.argv("post_expiry", POST_EXPIRY_NOW, json_mode=False))
        self.assertEqual(10, code)
        self.assertTrue(text.splitlines()[0].startswith("EDITS_PENDING"), text)
        self.assertIn("quote check not run: market_closed", text)

    def test_thanksgiving_is_unavailable_with_the_nvda_edits(self) -> None:
        code, body = run_json(self.argv("holiday", HOLIDAY_NOW))
        self.assertEqual(20, code, body)
        payload = body["payload"]
        self.assertEqual(("UNAVAILABLE", "holiday"), (payload["verdict"], payload["session"]["session"]))
        outcomes = {item["group_id"]: item["outcome"] for item in payload["settlements"]}
        self.assertEqual({"AG-20261020-01": "PIN_UNCERTAIN", "AG-20261022-02": "PROBABLE_ASSIGNED"}, outcomes)
        self.assertEqual(["PROBABLE_ASSIGNED", "PROBABLE_ASSIGNED"], [edit["outcome"] for edit in payload["edits"]])
        self.assertEqual([6, 5], [edit["row_number"] for edit in payload["edits"]])
        self.assertEqual(["NVDA__2026-11-20__2026-11-23.json", "WMT__2026-11-20__2026-11-23.json"], self.history_files())
        self.assertEqual("market_closed", payload["quote_check"]["not_run_reason"])

    def test_header_typo_is_the_errors_verdict(self) -> None:
        # Recon finding 7: the ERRORS verdict is exit 1 through the error envelope with error.code
        # positions_invalid, the same shape book/assignment_watch/expiry_checklist give the same file; the
        # snapshot of record is still written first.
        self.rewrite("positions.csv", "cost_basis_total,", "cost_basis_totl,")
        code, body = run_json(self.argv("regular", REGULAR_NOW))
        self.assertEqual(1, code, body)
        self.assertEqual(("error", "positions_invalid"), (body["status"], body["error"]["code"]))
        self.assertIn("did you mean", body["error"]["message"])
        self.assertIn("ERRORS", body["error"]["message"])
        self.assertNotIn("payload", body)
        snapshot_path = os.path.join(self.root, "data", "positions", "2026-11-03.csv")
        self.assertEqual(self.positions_sha, sha256_of(snapshot_path), "the bytes of record are still kept")
        self.assertEqual([], self.ledger(), "nothing is fetched for an unreadable book")
        code, text = run_tool(self.argv("regular", REGULAR_NOW, json_mode=False))
        self.assertEqual(1, code)
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith("ERRORS (2026-11-03 ET): positions.csv cannot be used:"), text)
        self.assertTrue(any(line.startswith("snapshot: data/positions/2026-11-03.csv") for line in lines), text)

    def test_missing_positions_csv_is_exit_1(self) -> None:
        os.remove(self.positions)
        code, body = run_json(self.argv("regular", REGULAR_NOW))
        self.assertEqual((1, "error", "positions_missing"), (code, body["status"], body["error"]["code"]))
        shutil.copy(os.path.join(MAIN_DESK, "positions.csv"), self.positions)
        self.positions_sha = sha256_of(self.positions)
        self.positions_stat = os.stat(self.positions)

    def test_calendar_parse_error_is_exit_1(self) -> None:
        self.rewrite(os.path.join("research", "calendar.toml"), "schema_version = 1", "schema_version = 1\nbogus = 2")
        code, body = run_json(self.argv("regular", REGULAR_NOW))
        self.assertEqual((1, "error", "calendar_invalid"), (code, body["status"], body["error"]["code"]))

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
        for fixture_set, now in (("regular", REGULAR_NOW), ("post_expiry", POST_EXPIRY_NOW), ("holiday", HOLIDAY_NOW)):
            _, text = run_tool(self.argv(fixture_set, now, json_mode=False, extra=["--diff"]))
            lowered = text.lower()
            for word in ("buy", "sell", "limit order", "market order", "submit", "place_order"):
                self.assertNotIn(word, lowered, (fixture_set, word))


# (fixture root, fixture set, instant, exit code, verdict, the footer's instant as payload.as_of): the quote check's
# as-of (the landed tool's own payload.quote_check.as_of on a fresh copy), or the run instant when no quote was
# fetched (a weekend, a holiday).
MESSAGE_CASES = (
    (MAIN_DESK, "regular", REGULAR_NOW, 0, "CLEAN", "2026-11-03T19:31:12+00:00"),
    (MAIN_DESK, "pre_open", "2026-11-03T12:31:00Z", 0, "CLEAN", "2026-11-02T20:59:55+00:00"),
    (MAIN_DESK, "post_expiry", POST_EXPIRY_NOW, 10, "EDITS_PENDING", "2026-11-21T14:30:00+00:00"),
    (MAIN_DESK, "holiday", HOLIDAY_NOW, 20, "UNAVAILABLE", "2026-11-26T15:00:00+00:00"),
    (RECON_DESK, "recon", RECON_NOW, 20, "UNAVAILABLE", "2026-11-23T20:59:50+00:00"),
)


class MessageAndRecordTest(DeskCopy):
    """PR 2d decisions D2-15 (``payload.message`` is the posted prose without ``--diff``, ``payload.as_of`` the
    instant of its footer line) and D1-11 (every run writes the reconciliation record
    ``data/positions/<ET date>.recon.json`` under the desk lock, whatever the verdict)."""

    source = MAIN_DESK

    def fresh_root(self, source: str) -> str:
        holder = tempfile.TemporaryDirectory(prefix="aegis-recon-tool-twin-")
        self.addCleanup(holder.cleanup)
        root = os.path.join(os.path.realpath(holder.name), "desk")
        shutil.copytree(source, root)
        return root

    def run_on(self, root: str, fixture_set: str, now: str, json_mode: bool, extra: Sequence[str] = ()) -> Tuple[int, str]:
        argv = ["--offline", "--desk-root", root, "--now", now, "--fixture-set", fixture_set] + list(extra)
        return run_tool(argv + (["--json"] if json_mode else []))

    def record(self, root: str, et_date: str) -> Dict[str, Any]:
        with open(os.path.join(root, "data", "positions", et_date + ".recon.json"), encoding="utf-8") as handle:
            return json.load(handle)

    def test_schema_names_the_message_and_its_as_of(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        payload = body["envelope"]["payload"]
        self.assertIn("message", payload)
        self.assertIn("as_of", payload)

    def test_recon_path_is_beside_the_snapshot(self) -> None:
        paths = desk_paths.for_root(self.root)
        self.assertEqual(os.path.join(self.root, "data", "positions", "2026-11-03.recon.json"),
                         recon.recon_path(paths, datetime.date(2026, 11, 3)))
        self.assertIn("recon_path", recon.__all__)

    def test_message_is_the_prose_without_diff_and_as_of_is_the_footer_instant(self) -> None:
        for source, fixture_set, now, exit_code, verdict, as_of in MESSAGE_CASES:
            with self.subTest(fixture_set=fixture_set, now=now):
                code, out = self.run_on(self.fresh_root(source), fixture_set, now, True)
                body = json.loads(out)
                human_code, text = self.run_on(self.fresh_root(source), fixture_set, now, False)
                self.assertEqual((exit_code, exit_code, verdict), (code, human_code, body["payload"]["verdict"]), out)
                self.assertEqual(text.rstrip("\n"), body["payload"]["message"])
                self.assertEqual(as_of, body["payload"]["as_of"])
                lint = posted_lint(body["payload"])
                self.assertTrue(lint.ok, lint.problems)
                self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(body["payload"]["message"])])
                self.assertEqual("Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of %s UTC" % as_of[11:16], text.splitlines()[-1])
                _code, diff_out = self.run_on(self.fresh_root(source), fixture_set, now, True, extra=["--diff"])
                self.assertEqual(body["payload"]["message"], json.loads(diff_out)["payload"]["message"],
                                 "the message never carries the --diff rows")

    def test_every_verdict_writes_the_record(self) -> None:
        for source, fixture_set, now, exit_code, verdict, _as_of in MESSAGE_CASES:
            with self.subTest(fixture_set=fixture_set, now=now):
                json_root = self.fresh_root(source)
                code, out = self.run_on(json_root, fixture_set, now, True)
                printed = json.loads(out)
                et_date = printed["payload"]["et_date"]
                record = self.record(json_root, et_date)
                self.assertEqual(printed, record, "the record is the envelope --json prints")
                self.assertEqual((verdict, exit_code), (record["payload"]["verdict"], record["payload"]["exit_code"]))
                for key in ("edits", "unavailable"):
                    self.assertIsInstance(record["payload"]["counts"][key], int)
                self.assertEqual(now.replace("Z", "+00:00"), record["generated_at"])
                human_root = self.fresh_root(source)
                human_code, _text = self.run_on(human_root, fixture_set, now, False, extra=["--diff"])
                self.assertEqual(code, human_code)
                self.assertEqual(record, self.record(human_root, et_date), "a human --diff run writes the same record")
                self.assertEqual([et_date + ".csv", et_date + ".recon.json", et_date + ".sha256"],
                                 sorted(os.listdir(os.path.join(human_root, "data", "positions"))))

    def test_the_errors_verdict_writes_the_record_and_prints_the_error_envelope(self) -> None:
        self.rewrite("positions.csv", "cost_basis_total,", "cost_basis_totl,")
        code, body = run_json(self.argv("regular", REGULAR_NOW))
        self.assertEqual((1, "error", "positions_invalid"), (code, body["status"], body["error"]["code"]))
        self.assertNotIn("payload", body)
        record = self.record(self.root, "2026-11-03")
        self.assertEqual(("csv_recon", "error", "ERRORS", 1, "2026-11-03"),
                         (record["tool"], record["status"], record["payload"]["verdict"], record["payload"]["exit_code"],
                          record["payload"]["et_date"]))
        self.assertTrue(record["payload"]["file_errors"])
        self.assertEqual(self.positions_sha, record["payload"]["positions_sha256"])
        self.assertTrue(record["payload"]["message"].startswith("ERRORS (2026-11-03 ET): positions.csv cannot be used:"))
        self.assertEqual("2026-11-03T19:50:00+00:00", record["payload"]["as_of"])

    def test_a_busy_lock_is_a_warning_and_no_record(self) -> None:
        def busy(paths: Any, et_date: Any, body: Any) -> str:
            raise state.LockBusy(paths.lock_path, 5.0)

        with mock.patch.object(tool, "write_record", side_effect=busy):
            code, body = run_json(self.argv("regular", REGULAR_NOW))
            human_code, text = run_tool(self.argv("regular", REGULAR_NOW, json_mode=False))
        self.assertEqual((0, 0, "ok"), (code, human_code, body["status"]))
        warnings = [w for w in body["payload"]["warnings"] if w.startswith("reconciliation record not written: ")]
        self.assertEqual(1, len(warnings), body["payload"]["warnings"])
        self.assertIn("is busy", warnings[0])
        self.assertIn("warning: " + warnings[0], text.splitlines())
        self.assertIn("warning: " + warnings[0], body["payload"]["message"].splitlines())
        self.assertFalse(os.path.exists(os.path.join(self.root, "data", "positions", "2026-11-03.recon.json")))

    def test_the_record_is_written_under_the_desk_lock(self) -> None:
        held = []
        real_locked = state.locked

        def watching(lock_path: str, *args: Any, **kwargs: Any) -> Any:
            held.append(lock_path)
            return real_locked(lock_path, *args, **kwargs)

        real_write = state.atomic_write_json

        def write(path: str, obj: Any) -> None:
            if path.endswith(".recon.json"):
                self.assertTrue(held and held[-1].endswith(os.path.join("state", ".lock")), held)
            real_write(path, obj)

        with mock.patch.object(state, "locked", side_effect=watching), \
                mock.patch.object(state, "atomic_write_json", side_effect=write):
            code, _body = run_json(self.argv("regular", REGULAR_NOW))
        self.assertEqual(0, code)
        self.assertTrue(os.path.isfile(os.path.join(self.root, "data", "positions", "2026-11-03.recon.json")))

    def test_a_record_that_cannot_be_written_is_an_io_error(self) -> None:
        os.makedirs(os.path.join(self.root, "data", "positions", "2026-11-03.recon.json"))
        code, body = run_json(self.argv("regular", REGULAR_NOW))
        self.assertEqual((1, "error", "io_error"), (code, body["status"], body["error"]["code"]))
        self.assertIn("data/positions/2026-11-03.recon.json", body["error"]["message"])


if __name__ == "__main__":
    unittest.main()
