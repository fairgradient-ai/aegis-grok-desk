"""``scripts/quote_cache.py`` run in-process (``main(argv)``) against a copied fixture desk root.

The offline journeys need Builder A's ``aegis_desk.tradier_client`` (the ``OfflineTransport``) and
Builder C's shared ``--now`` flag; those test classes skip, with a message, while either is still
missing from the tree and run unchanged once both have landed. The usage, schema and
production-refusal checks need neither and always run.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import io
import json
import os
import re
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Sequence, Tuple
from unittest import mock

from aegis_desk import contract

import quote_cache as tool  # the tool module scripts/quote_cache.py, not the library

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
CLIENT_MODULE = os.path.join(ROOT, "scripts", "aegis_desk", "tradier_client.py")
REGULAR_NOW = "2026-11-03T19:50:00Z"
AFTER_CLOSE_NOW = "2026-11-03T21:25:00Z"
PRE_OPEN_NOW = "2026-11-03T13:00:00Z"
HOLIDAY_NOW = "2026-11-26T15:00:00Z"
TOKEN_RE = re.compile(r"(?i)bearer|authorization")


def now_flag_present() -> bool:
    probe = contract.ReadOnlyParser("probe", "0")
    _, extras = probe.parse_known_args(["--now", REGULAR_NOW])
    return not extras


NOW_READY = now_flag_present()
NOW_SKIP = "waits for the shared --now flag of ReadOnlyParser (Builder C)"
READY = os.path.isfile(CLIENT_MODULE) and NOW_READY
SKIP = "waits for scripts/aegis_desk/tradier_client.py (Builder A) and the shared --now flag (Builder C)"


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = tool.main(list(argv))
    return code, buffer.getvalue()


def run_json(argv: Sequence[str]) -> Tuple[int, Dict[str, Any]]:
    code, out = run_tool(argv)
    return code, json.loads(out)


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def argv(self, *symbols: str, fixture_set: str = None, now: str = REGULAR_NOW, json_mode: bool = True,
             extra: Sequence[str] = ()) -> List[str]:
        argv = list(symbols) + ["--offline", "--desk-root", self.root, "--now", now] + list(extra)
        if fixture_set is not None:
            argv += ["--fixture-set", fixture_set]
        if json_mode:
            argv.append("--json")
        return argv

    def ledger(self) -> List[Dict[str, Any]]:
        path = os.path.join(self.root, "data", "cache", "ledger.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as handle:
            return [json.loads(line) for line in handle.read().splitlines() if line]

    def rewrite_desk_toml(self, old: str, new: str) -> None:
        path = os.path.join(self.root, "desk.toml")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn(old, text)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new, 1))


class AlwaysOnTest(DeskCopy):
    """Usage, schema and the production refusal: no client, no fixture set, no ``--now`` needed."""

    def test_schema_exits_0_without_touching_the_desk(self) -> None:
        code, body = run_json(["--schema"])
        self.assertEqual(0, code)
        self.assertEqual("quote_cache", body["tool"])
        self.assertEqual("read-only", body["mode"])
        self.assertIn("20", body["exit_codes"])
        self.assertIn("quotes", body["envelope"]["payload"])
        self.assertIn("budget", body["envelope"]["payload"])

    def test_no_symbol_is_a_usage_error(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            run_tool(["--json", "--offline", "--desk-root", self.root])
        self.assertEqual(2, ctx.exception.code)

    def test_fixture_set_requires_offline(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            run_tool(["SPY", "--json", "--desk-root", self.root, "--fixture-set", "regular"])
        self.assertEqual(2, ctx.exception.code)

    def test_chain_and_expiration_come_together(self) -> None:
        for argv in (["SPY", "--chain", "WMT"], ["SPY", "--expiration", "2026-11-20"],
                     ["SPY", "--chain", "WMT", "--expiration", "20261120"]):
            with self.subTest(argv=argv):
                with self.assertRaises(SystemExit) as ctx:
                    run_tool(argv + ["--json", "--offline", "--desk-root", self.root])
                self.assertEqual(2, ctx.exception.code)

    def test_usage_error_in_json_mode_is_an_envelope_on_stdout(self) -> None:
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            with self.assertRaises(SystemExit):
                tool.main(["SPY", "--json", "--desk-root", self.root, "--fixture-set", "regular"])
        body = json.loads(buffer.getvalue())
        self.assertEqual(("usage", "invalid_arguments"), (body["status"], body["error"]["code"]))

    def test_malformed_symbol_is_a_usage_error(self) -> None:
        # A typo is the caller's error (exit 2), reported before anything is read or sent, so it can
        # never sink the batch it travels with.
        for argv in (["SPY", "BAD SYMBOL"], ["SPY", "--chain", "W MT", "--expiration", "2026-11-20"],
                     ["S" * 33]):
            with self.subTest(argv=argv):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    with self.assertRaises(SystemExit) as ctx:
                        tool.main(argv + ["--json", "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
                self.assertEqual(2, ctx.exception.code)
                body = json.loads(buffer.getvalue())
                self.assertEqual("invalid_arguments", body["error"]["code"])
                self.assertIn("not a valid symbol", body["error"]["message"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "data")), "nothing is written for a usage error")


@unittest.skipUnless(NOW_READY, NOW_SKIP)
class PlumbingTest(DeskCopy):
    """Config and host checks: no client is built, but ``main`` resolves ``args.now``."""

    def test_production_host_is_refused_with_exit_20(self) -> None:
        self.rewrite_desk_toml('host = "sandbox"', 'host = "production"')
        code, body = run_json(["SPY", "--json", "--offline", "--desk-root", self.root])
        self.assertEqual(20, code)
        self.assertEqual("unavailable", body["status"])
        self.assertEqual("production_host", body["error"]["code"])
        self.assertEqual("production market data is not supported before the market-data proxy (Stage 3); "
                         "use the sandbox host", body["error"]["message"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "data")), "nothing is written for a refused host")
        code, text = run_tool(["SPY", "--offline", "--desk-root", self.root])
        self.assertEqual(20, code)
        self.assertIn("use the sandbox host", text)

    def test_invalid_desk_toml_is_exit_1(self) -> None:
        self.rewrite_desk_toml('quote_ttl_min = 5', 'quote_ttl_min = 99')
        code, body = run_json(["SPY", "--json", "--offline", "--desk-root", self.root])
        self.assertEqual(1, code)
        self.assertEqual(("error", "config_invalid"), (body["status"], body["error"]["code"]))
        self.assertIn("quote_ttl_min", body["error"]["message"])

    def test_missing_desk_toml_is_exit_1(self) -> None:
        os.remove(os.path.join(self.root, "desk.toml"))
        code, body = run_json(["SPY", "--json", "--offline", "--desk-root", self.root])
        self.assertEqual(1, code)
        self.assertEqual("config_invalid", body["error"]["code"])


@unittest.skipUnless(READY, SKIP)
class RegularSetTest(DeskCopy):
    def test_journey_spy_wmt_and_the_covered_call_leg(self) -> None:
        code, body = run_json(self.argv("SPY", "WMT", "WMT261120C00105000"))
        self.assertEqual(0, code, body)
        self.assertEqual({"schema_version": 1, "tool": "quote_cache", "mode": "read-only", "status": "ok",
                          "generated_at": "2026-11-03T19:50:00+00:00", "source": "tradier:sandbox", "delay_min": 15},
                         {k: body[k] for k in ("schema_version", "tool", "mode", "status", "generated_at", "source",
                                               "delay_min")})
        payload = body["payload"]
        self.assertEqual(1, payload["requests_made"])
        self.assertEqual({}, payload["missing"])
        self.assertEqual({"window_60s_used": 1, "left": 54}, payload["budget"])
        self.assertEqual("open", payload["session"]["session"])
        self.assertEqual("2026-11-03", payload["session"]["et_date"])
        self.assertEqual(["SPY", "WMT", "WMT261120C00105000"], [q["symbol"] for q in payload["quotes"]])
        spy = payload["quotes"][0]
        self.assertEqual(("648.1", "648.14", "648.12"), (spy["bid"], spy["ask"], spy["mid"]))
        self.assertEqual("2026-11-03T19:31:12+00:00", spy["as_of"])
        self.assertEqual(18, spy["age_min"])
        self.assertEqual("fetch", spy["source"])
        self.assertEqual("2026-11-03T19:50:00+00:00", spy["fetched_utc"])
        leg = payload["quotes"][2]
        self.assertEqual(("3.35", "3.5", "3.425", "call", "105.0", "2026-11-20"),
                         (leg["bid"], leg["ask"], leg["mid"], leg["option_type"], leg["strike"], leg["expiration_date"]))
        self.assertEqual([], payload["stale"])
        self.assertNotIn("chain", payload)
        cache_file = os.path.join(self.root, "data", "cache", "quotes.json")
        with open(cache_file, encoding="utf-8") as handle:
            document = json.load(handle)
        self.assertEqual({"SPY", "WMT", "WMT261120C00105000"}, set(document["quotes"]))
        self.assertEqual(648.1, document["quotes"]["SPY"]["quote"]["bid"])
        lines = self.ledger()
        self.assertEqual(1, len(lines))
        self.assertEqual(("/v1/markets/quotes", 3, "ok", "2026-11-03T19:50:00+00:00", 200),
                         (lines[0]["path"], lines[0]["symbols"], lines[0]["status"], lines[0]["utc"],
                          lines[0]["http_status"]))
        self.assertFalse(os.path.exists(os.path.join(FIXTURE_DESK, "data")), "the fixture tree is never written")

    def test_second_run_is_served_from_cache(self) -> None:
        run_json(self.argv("SPY", "WMT"))
        code, body = run_json(self.argv("SPY", "WMT"))
        self.assertEqual(0, code)
        payload = body["payload"]
        self.assertEqual(0, payload["requests_made"])
        self.assertEqual(["cache", "cache"], [q["source"] for q in payload["quotes"]])
        self.assertEqual({"window_60s_used": 1, "left": 54}, payload["budget"])
        code, body = run_json(self.argv("SPY", "WMT", extra=["--force"]))
        self.assertEqual(1, body["payload"]["requests_made"])
        self.assertEqual({"window_60s_used": 2, "left": 53}, body["payload"]["budget"])

    def test_prose_table_and_footer(self) -> None:
        code, text = run_tool(self.argv("SPY", "WMT", "WMT261120C00105000", json_mode=False))
        self.assertEqual(0, code, text)
        lines = text.splitlines()
        self.assertIn("SPY 648.10/648.14 648.12 as-of 14:31:12 ET (age 18 min) fetch", lines)
        self.assertIn("WMT 104.20/104.26 104.23 as-of 14:31:12 ET (age 18 min) fetch", lines)
        self.assertIn("WMT261120C00105000 3.35/3.50 3.425 as-of 14:31:12 ET (age 18 min) fetch", lines)
        self.assertIn("session open (2026-11-03 14:50:00 ET); requests 1; 60-s window used 1, left 54", lines)
        self.assertEqual("Tradier sandbox, delayed 15 min, as of 19:31 UTC", lines[-1])
        self.assertIsNone(TOKEN_RE.search(text))

    def test_stale_set_is_served_and_graded_elsewhere(self) -> None:
        code, body = run_json(self.argv("SPY", fixture_set="stale"))
        self.assertEqual(0, code, "the cache serves what the fetch returned; quote_qc decides staleness")
        spy = body["payload"]["quotes"][0]
        self.assertEqual((60, "2026-11-03T18:50:00+00:00"), (spy["age_min"], spy["as_of"]))

    def test_unmatched_index_symbol_is_exit_20(self) -> None:
        code, body = run_json(self.argv("SPX"))
        self.assertEqual(20, code)
        self.assertEqual("unavailable", body["status"])
        self.assertEqual({"SPX": "unmatched"}, body["payload"]["missing"])
        self.assertEqual([], body["payload"]["quotes"])
        self.assertEqual(1, body["payload"]["requests_made"])
        code, body = run_json(self.argv("SPX", "SPY"))
        self.assertEqual(20, code)
        self.assertEqual(["SPY"], [q["symbol"] for q in body["payload"]["quotes"]])
        self.assertEqual({"SPX": "unmatched"}, body["payload"]["missing"])

    def test_unknown_fixture_key_fails_the_whole_batch(self) -> None:
        code, body = run_json(self.argv("SPY", "QQQ"))
        self.assertEqual(20, code)
        self.assertEqual({"SPY": "fetch_failed:network", "QQQ": "fetch_failed:network"}, body["payload"]["missing"])
        self.assertEqual("network", self.ledger()[0]["status"])
        code, text = run_tool(self.argv("SPY", "QQQ", json_mode=False))
        self.assertEqual(20, code)
        self.assertIn("UNAVAILABLE", text)

    def test_expired_copy_is_displayed_as_stale_when_the_refetch_fails(self) -> None:
        run_json(self.argv("SPY"))
        code, body = run_json(self.argv("SPY", "QQQ", now="2026-11-03T20:00:00Z"))
        self.assertEqual(20, code)
        self.assertEqual([], body["payload"]["quotes"])
        self.assertEqual(["SPY"], [q["symbol"] for q in body["payload"]["stale"]])
        self.assertEqual("648.1", body["payload"]["stale"][0]["bid"])
        code, text = run_tool(self.argv("SPY", "QQQ", now="2026-11-03T20:00:00Z", json_mode=False))
        self.assertRegex(text, r"(?m)^SPY 648\.10/648\.14 648\.12 as-of 14:31:12 ET \(age 28 min\) stale")

    def test_chain_on_demand(self) -> None:
        code, body = run_json(self.argv("WMT", extra=["--chain", "WMT", "--expiration", "2026-11-20"]))
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual(2, payload["requests_made"])
        chain = payload["chain"]
        self.assertEqual(("WMT", "2026-11-20", "fetch", 10, None),
                         (chain["symbol"], chain["expiration"], chain["served_from"], chain["count"],
                          chain["missing_reason"]))
        anchor = [o for o in chain["options"] if o["symbol"] == "WMT261120C00105000"][0]
        self.assertEqual(("3.35", "3.5", "3.425", "105.0", "call"),
                         (anchor["bid"], anchor["ask"], anchor["mid"], anchor["strike"], anchor["option_type"]))
        self.assertTrue(os.path.isfile(os.path.join(self.root, "data", "cache", "chains", "WMT__2026-11-20.json")))
        self.assertEqual(["/v1/markets/quotes", "/v1/markets/options/chains"], [l["path"] for l in self.ledger()])
        code, body = run_json(self.argv("WMT", extra=["--chain", "WMT", "--expiration", "2026-11-20"]))
        self.assertEqual(0, body["payload"]["requests_made"])
        self.assertEqual("cache", body["payload"]["chain"]["served_from"])
        code, text = run_tool(self.argv("WMT", extra=["--chain", "WMT", "--expiration", "2026-11-20"],
                                        json_mode=False))
        self.assertIn("chain WMT 2026-11-20: 10 contracts (cache)", text)

    def test_max_batch_and_symbol_pattern_match_the_client(self) -> None:
        from aegis_desk import quote_cache as library
        from aegis_desk import tradier_client
        self.assertEqual(tradier_client.MAX_BATCH, library.MAX_BATCH)
        self.assertEqual(tradier_client.SYMBOL_RE.pattern, library.SYMBOL_RE.pattern)

    def test_fixture_set_defaults_to_regular_in_the_payload(self) -> None:
        code, body = run_json(self.argv("SPY"))
        self.assertEqual((0, "regular"), (code, body["payload"]["fixture_set"]))
        code, body = run_json(self.argv("SPY", fixture_set="stale"))
        self.assertEqual("stale", body["payload"]["fixture_set"])

    def test_file_in_place_of_a_runtime_directory_is_io_error(self) -> None:
        os.makedirs(os.path.join(self.root, "data"))
        with open(os.path.join(self.root, "data", "cache"), "w", encoding="utf-8") as handle:
            handle.write("not a directory\n")
        code, body = run_json(self.argv("SPY"))
        self.assertEqual(1, code)
        self.assertEqual(("error", "io_error"), (body["status"], body["error"]["code"]))
        self.assertIn("runtime directories", body["error"]["message"])
        code, text = run_tool(self.argv("SPY", json_mode=False))
        self.assertEqual(1, code)
        self.assertTrue(text.startswith("error: "), text)

    def test_failed_forced_refetch_keeps_the_fresh_copy(self) -> None:
        run_json(self.argv("SPY"))
        # SPY,QQQ has no recorded file: the forced batch fails, SPY's copy is inside the TTL and is
        # served from the cache; QQQ alone is missing.
        code, body = run_json(self.argv("SPY", "QQQ", extra=["--force"]))
        self.assertEqual(20, code)
        payload = body["payload"]
        self.assertEqual(["SPY"], [q["symbol"] for q in payload["quotes"]])
        self.assertEqual("cache", payload["quotes"][0]["source"])
        self.assertEqual({"QQQ": "fetch_failed:network"}, payload["missing"])
        self.assertEqual([], payload["stale"])
        self.assertTrue(any("forced refetch" in w for w in payload["warnings"]), payload["warnings"])
        self.assertEqual(2, payload["budget"]["window_60s_used"], "the failed attempt counts")


@unittest.skipUnless(READY, SKIP)
class OtherSetsTest(DeskCopy):
    def test_holiday_never_fetches_and_is_exit_20_with_nothing_cached(self) -> None:
        code, body = run_json(self.argv("SPY", fixture_set="holiday", now=HOLIDAY_NOW))
        self.assertEqual(20, code)
        payload = body["payload"]
        self.assertEqual({"SPY": "market_closed"}, payload["missing"])
        self.assertEqual(0, payload["requests_made"])
        self.assertEqual("holiday", payload["session"]["session"])
        self.assertEqual("Thanksgiving Day", payload["session"]["holiday_name"])
        self.assertEqual([], self.ledger())
        code, text = run_tool(self.argv("SPY", fixture_set="holiday", now=HOLIDAY_NOW, json_mode=False))
        self.assertIn("NYSE closed", text)

    def test_holiday_serves_an_older_cached_copy(self) -> None:
        run_json(self.argv("SPY"))
        code, body = run_json(self.argv("SPY", fixture_set="holiday", now=HOLIDAY_NOW))
        self.assertEqual(0, code)
        payload = body["payload"]
        self.assertEqual("cache", payload["quotes"][0]["source"])
        self.assertEqual(0, payload["requests_made"])
        self.assertTrue(any("non-trading day" in w for w in payload["warnings"]), payload["warnings"])

    def test_force_on_a_holiday_serves_the_cached_copy(self) -> None:
        run_json(self.argv("SPY"))
        code, body = run_json(self.argv("SPY", fixture_set="holiday", now=HOLIDAY_NOW, extra=["--force"]))
        self.assertEqual(0, code, "force never discards a copy on a day nothing can be fetched")
        payload = body["payload"]
        self.assertEqual(("cache", 0, {}), (payload["quotes"][0]["source"], payload["requests_made"], payload["missing"]))
        self.assertEqual(1, len(self.ledger()), "the holiday run sent nothing")

    def test_after_close_and_pre_open_sets_fetch_normally(self) -> None:
        code, body = run_json(self.argv("SPY", fixture_set="after_close", now=AFTER_CLOSE_NOW))
        self.assertEqual(0, code)
        self.assertEqual("closed", body["payload"]["session"]["session"])
        self.assertEqual("2026-11-03T20:59:40+00:00", body["payload"]["quotes"][0]["as_of"])
        code, body = run_json(self.argv("SPY", fixture_set="pre_open", now=PRE_OPEN_NOW))
        self.assertEqual(0, code)
        self.assertEqual("pre", body["payload"]["session"]["session"])
        self.assertEqual(("645.88", "645.92"), (body["payload"]["quotes"][0]["bid"], body["payload"]["quotes"][0]["ask"]))

    def test_missing_token_online_is_exit_20_never_1(self) -> None:
        self.rewrite_desk_toml('token_env = "TRADIER_API_TOKEN"',
                               'token_env = "AEGIS_TEST_NO_SUCH_TOKEN"\ntoken_file = "%s"'
                               % os.path.join(self.root, "no-such-token-file"))
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("AEGIS_TEST_NO_SUCH_TOKEN", None)
            code, body = run_json(["SPY", "--json", "--desk-root", self.root, "--now", REGULAR_NOW])
        self.assertEqual(20, code)
        self.assertEqual(("unavailable", "no_token"), (body["status"], body["error"]["code"]))
        self.assertEqual("no market-data token: add the Tradier sandbox token through the secret card "
                         "(or the acknowledged token file)", body["error"]["message"])
        self.assertEqual([], self.ledger(), "no request is attempted without a credential")


if __name__ == "__main__":
    unittest.main()
