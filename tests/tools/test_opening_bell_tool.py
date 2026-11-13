"""``scripts/opening_bell.py`` (the README's "try it now"): the quote, the nearest standard expiration at least 20
calendar days out, the ATM implied volatility from that chain's call and put mids (``aegis_desk.iv``) and the expected
move stated both ways, from three read-only calls: four GET requests to Alpaca's paper environment (PR 2d decision
D2-14). Offline runs read the copied fixture root's recorded set and write nothing; a live run reads no desk file and
needs the paper key pair; the goldens are the
regular set's, recomputed with the landed engine. ``--demo`` (the README's three-command try-it) is the same offline run
on the checkout's own fixture desk at the regular instant, opened by a plain line saying the data is synthetic; it
refuses a desk root, an instant, a fixture set and a credentials file."""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
from tests.floats import assert_close
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

import opening_bell as tool  # the tool scripts/opening_bell.py
from aegis_desk import alert_lint, alpaca_client, contract, market_types, version

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
# Not token-shaped (no run of 20 alphanumerics), never a real credential.
KEY_ID = "PKFAKE-bell-key-0000"
SECRET = "fake-bell-secret-0000-1111-2222"
KEYS = {"APCA_API_KEY_ID": KEY_ID, "APCA_API_SECRET_KEY": SECRET}
TEXT = [
    "SPY 648.12 mid (bid 648.10, ask 648.14), as of 19:31 UTC",
    "nearest standard expiration at least 20 calendar days out: 2026-12-18 (45 calendar days)",
    "ATM implied volatility at the 640 strike: 25.02 % (call 25.07 %, put 24.98 %; mids 28.65 and 17.30; r 0.04, q 0)",
    "expected move to 2026-12-18: straddle-based (call mid + put mid) x 0.80 = 36.76; one-sigma S x IV x sqrt(tau) = "
    "56.98",
    "Alpaca paper (IEX stocks, indicative options: approximate), delayed 15 min, as of 19:31 UTC; a public market snapshot, not a signal",
    # the text is relayed verbatim, so it passes the strict alert lint; a public quote shown before any rule exists
    # ends with the market-snapshot footer (the rules footer would claim rules the owner has not adopted)
    alert_lint.snapshot_footer_line(contract.parse_utc_instant("2026-11-03T19:31:12Z")),
]
SNAPSHOTS = os.path.join("fixtures", "alpaca", "regular", "snapshots__SPY__2026-12-18.json")
CONTRACTS = os.path.join("fixtures", "alpaca", "regular", "contracts__SPY__2026-12-18.json")
EXPIRATIONS = os.path.join("fixtures", "alpaca", "regular", "contracts__SPY__2026-11-03__2027-12-08.json")


def is_put(symbol: str) -> bool:
    return symbol[-9] == "P"


def shifted(stamp: str, seconds: int) -> str:
    instant = market_types.from_rfc3339(stamp, "t") + datetime.timedelta(seconds=seconds)
    return instant.strftime("%Y-%m-%dT%H:%M:%SZ")


def run_tool(argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer), contextlib.redirect_stderr(io.StringIO()):
        try:
            code = tool.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 1
    return code, buffer.getvalue()


def order_shaped(text: str) -> List[str]:
    return [pattern.pattern for pattern in alert_lint.FORBIDDEN_PATTERNS if pattern.search(text)]


class Recording:
    """A transport that records each request and answers from the regular recorded set."""

    def __init__(self, now: str = REGULAR_NOW) -> None:
        self.inner = alpaca_client.OfflineTransport(os.path.join(FIXTURE_DESK, "fixtures", "alpaca"), "regular",
                                                     now=contract.parse_utc_instant(now))
        self.calls: List[Tuple[str, Dict[str, str]]] = []

    def fetch(self, url: str, headers: Dict[str, str], timeout_s: float) -> Any:
        self.calls.append((url, dict(headers)))
        return self.inner.fetch(url, headers, timeout_s)


class PaperHostRefuses(Recording):
    """The recorded set, except that ``host`` answers ``http_status`` with Alpaca's error body once ``failing_after``
    requests have been answered (a live key: the data host answers it, the paper trading host does not)."""

    def __init__(self, http_status: int, failing_after: int, host: str = "paper-api.alpaca.markets") -> None:
        super().__init__()
        self.http_status, self.failing_after, self.host = http_status, failing_after, host

    def fetch(self, url: str, headers: Dict[str, str], timeout_s: float) -> Any:
        if len(self.calls) >= self.failing_after and url.split("/")[2] == self.host:
            self.calls.append((url, dict(headers)))
            body = b'{"message":"unauthorized."}' if self.http_status == 401 else b'{"message":"forbidden."}'
            return alpaca_client.TransportResponse(self.http_status, {}, body, contract.parse_utc_instant(REGULAR_NOW))
        return super().fetch(url, headers, timeout_s)


NOT_PAPER_KEY_LINE = ("UNAVAILABLE: not a paper key: Alpaca's paper trading host refused the key pair (HTTP %d) after "
                      "the market-data host answered it; delete both secrets and add a new pair from the Paper Trading "
                      "dashboard")


class DeskCopy(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-opening-bell-tool-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.addCleanup(self._tmp.cleanup)

    def offline(self, *extra: str, now: str = REGULAR_NOW, json_mode: bool = True) -> Tuple[int, str]:
        argv = ["--symbol", "SPY", "--offline", "--desk-root", self.root, "--now", now] + list(extra)
        code, out = run_tool(argv + (["--json"] if json_mode else []))
        self.assertEqual([], order_shaped(out))
        return code, out

    def tree(self) -> Dict[str, str]:
        found: Dict[str, str] = {}
        for folder, _dirs, files in os.walk(self.root):
            for name in files:
                path = os.path.join(folder, name)
                with open(path, "rb") as handle:
                    found[os.path.relpath(path, self.root)] = hashlib.sha256(handle.read()).hexdigest()
        return found

    def edit_json(self, rel: str, edit: Any) -> None:
        path = os.path.join(self.root, rel)
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
        edit(document)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)

    def edit_snapshots(self, edit: Any) -> None:
        """``edit(symbol, snapshot)`` on every snapshot of the SPY 2026-12-18 chain."""
        self.edit_json(SNAPSHOTS, lambda document: [edit(symbol, snapshot)
                                                    for symbol, snapshot in document["snapshots"].items()])

    def drop_puts(self) -> None:
        """No put at any strike: gone from the contract listing and from the snapshots."""
        self.edit_json(CONTRACTS, lambda document: document.update(option_contracts=[
            contract for contract in document["option_contracts"] if contract["type"] != "put"]))
        self.edit_json(SNAPSHOTS, lambda document: document.update(snapshots={
            symbol: snapshot for symbol, snapshot in document["snapshots"].items() if not is_put(symbol)}))


class SchemaAndUsageTest(DeskCopy):
    def test_schema(self) -> None:
        code, out = run_tool(["--schema"])
        self.assertEqual(0, code)
        body = json.loads(out)
        self.assertEqual("opening_bell", body["tool"])
        for path in ("https://data.alpaca.markets/v2/stocks/quotes/latest",
                     "https://paper-api.alpaca.markets/v2/options/contracts", "/v1beta1/options/snapshots/<SYMBOL>"):
            self.assertIn(path, body["network_access"])
        self.assertEqual(sorted(tool.PAYLOAD_KEYS), sorted(body["envelope"]["payload"]))

    def test_usage_errors(self) -> None:
        for argv in (["--offline"], ["--symbol", "SPY", "--offline"], ["--symbol", "SPY", "--fixture-set", "regular"],
                     ["--offline", "--desk-root", self.root], ["--symbol", "S P Y", "--offline", "--desk-root", self.root],
                     ["--symbol", "SPY", "--rate", "abc", "--offline", "--desk-root", self.root],
                     ["--symbol", "SPY", "--rate", "-0.01", "--offline", "--desk-root", self.root],
                     ["--symbol", "SPY", "--rate", "1.5", "--offline", "--desk-root", self.root],
                     ["--symbol", "SPY", "--rate", "4e-2", "--offline", "--desk-root", self.root]):
            code, out = run_tool(argv + ["--json"])
            self.assertEqual(2, code, argv)
            self.assertEqual("invalid_arguments", json.loads(out)["error"]["code"], argv)


class OfflineTest(DeskCopy):
    def test_the_regular_set_goldens_and_nothing_written(self) -> None:
        before = self.tree()
        code, out = self.offline(json_mode=False)
        self.assertEqual(0, code, out)
        self.assertEqual(TEXT, out.rstrip("\n").split("\n"))
        self.assertEqual(before, self.tree(), "no quote cache, no runtime directory, no ledger")
        code, out = self.offline()
        self.assertEqual(0, code)
        body = json.loads(out)
        self.assertEqual(("ok", "alpaca:paper", 15), (body["status"], body["source"], body["delay_min"]))
        payload = body["payload"]
        self.assertEqual(list(tool.PAYLOAD_KEYS), sorted(payload, key=tool.PAYLOAD_KEYS.index))
        self.assertEqual("\n".join(TEXT), payload["message"])
        self.assertEqual({"bid": "648.10", "ask": "648.14", "mid": "648.12", "as_of": "2026-11-03T19:31:12+00:00"},
                         payload["quote"], "each money field as the text prints it")
        self.assertEqual(("SPY", "2026-12-18", 45, "640"), (payload["symbol"], payload["expiration"],
                                                            payload["dte_calendar"], payload["strike"]))
        # Solved IVs come from libm: within 1e-12, not to the last digit (tests/floats.py).
        assert_close(self, {"mid": "28.65", "iv": 0.25066666696134243}, payload["call"])
        assert_close(self, {"mid": "17.30", "iv": 0.24979018665520808}, payload["put"])
        assert_close(self, 0.2502284268082753, payload["atm_iv"])
        assert_close(self, 0.12345662100456621, payload["tau_years"])
        self.assertEqual(("0.04", "0"), (payload["rate"], payload["q"]))
        self.assertEqual({"straddle": "36.76", "one_sigma": "56.98"}, payload["expected_move"])
        self.assertEqual((4, "regular"), (payload["requests_made"], payload["fixture_set"]))
        self.assertEqual(before, self.tree())
        self.assertEqual({"ok": True, "problems": []}, {key: payload["lint"][key] for key in ("ok", "problems")})
        self.assertEqual(sorted(alert_lint.EMITTED_DOCUMENT_KEYS + ("footer",)), sorted(payload["emitted"]))
        self.assertEqual("snapshot", payload["emitted"]["footer"])
        self.assertEqual("2026-11-03T19:31:12+00:00", payload["emitted"]["as_of"])
        relayed = alert_lint.load_emitted(payload["emitted"]).check(payload["message"])
        self.assertTrue(relayed.ok, relayed.problems)
        self.assertEqual(["2026-12-18"], payload["emitted"]["identifiers"])
        self.assertEqual(["19:31 UTC"], payload["emitted"]["clocks"])
        self.assertEqual(17, payload["lint"]["numbers_checked"], "every number outside the date and the clocks")

    def test_a_text_that_fails_the_lint_is_never_printed(self) -> None:
        with mock.patch.object(tool, "SNAPSHOT_LINE", tool.SNAPSHOT_LINE + "; you sho" + "uld look"):
            code, out = self.offline(json_mode=False)
            self.assertEqual(1, code)
            self.assertEqual("error: the snapshot failed alert_lint and was not printed: forbidden_language", out.strip())
            code, out = self.offline()
        body = json.loads(out)
        self.assertEqual((1, "error", "lint_failed"), (code, body["status"], body["error"]["code"]))

    def test_the_rate_flag(self) -> None:
        code, out = self.offline("--rate", "0.0412")
        self.assertEqual(0, code, out)
        payload = json.loads(out)["payload"]
        assert_close(self, (0.25006528161319297, 0.25025856294841303), (payload["call"]["iv"], payload["put"]["iv"]))
        self.assertEqual("0.0412", payload["rate"])
        self.assertIn("; r 0.0412, q 0)", payload["message"])

    def test_option_stamps_a_feed_delay_behind_the_stock_stamp_are_unaligned(self) -> None:
        # Open item (owner decision, docs/PROVENANCE.md): if the indicative option quotes carry stamps 15 minutes
        # older than the real-time IEX stock quote, the 60 s alignment rule makes the IV lines UNAVAILABLE rather
        # than invert a 15-minute-old option price against a current spot. This pins today's verdict.
        self.edit_snapshots(lambda symbol, snapshot: snapshot["latestQuote"].update(
            t=shifted(snapshot["latestQuote"]["t"], -15 * 60)))
        code, out = self.offline()
        body = json.loads(out)
        self.assertEqual((20, "unavailable", "stamps_unaligned"), (code, body["status"], body["error"]["code"]))
        self.assertEqual("an option stamp is more than 60 s from the SPY stamp", body["error"]["message"])

    def test_unavailable_answers_are_exit_20(self) -> None:
        cases = (
            # only expirations under 20 calendar days out are listed
            ("no_expiration", lambda: self.edit_json(EXPIRATIONS, lambda d: d.update(option_contracts=[
                contract for contract in d["option_contracts"] if contract["expiration_date"] < "2026-11-23"]))),
            ("stamps_unaligned", lambda: self.edit_snapshots(lambda symbol, snapshot: snapshot["latestQuote"].update(
                t=shifted(snapshot["latestQuote"]["t"], 61)))),
            ("iv_failed", lambda: self.edit_snapshots(lambda symbol, snapshot: None if is_put(symbol) else
                                                      snapshot["latestQuote"].update(bp=1.0, ap=1.1))),
            ("no_strike_pair", self.drop_puts),
            ("no_two_sided_quote", lambda: self.edit_snapshots(lambda symbol, snapshot: snapshot["latestQuote"].update(
                ap=None))),
        )
        for code_name, edit in cases:
            self.setUp()
            edit()
            code, out = self.offline()
            body = json.loads(out)
            self.assertEqual((20, "unavailable", code_name), (code, body["status"], body["error"]["code"]), code_name)
        self.setUp()
        code, out = self.offline(now="2026-12-01T19:50:00Z")
        body = json.loads(out)
        self.assertEqual((20, "request_failed"), (code, body["error"]["code"]),
                         "the 2027-01-15 chain is not in the recorded set: a failed request")
        code, out = self.offline("--fixture-set", "nope")
        self.assertEqual((1, "fixtures_missing"), (code, json.loads(out)["error"]["code"]))


DEMO_LINE = "Demo: synthetic sample data from the desk's test fixtures, not real market prices; nothing is written"


class DemoTest(DeskCopy):
    """``--demo``: the README's three-command try-it. It names the checkout's own fixture desk, so the owner types no
    path and no instant; the runs below point the tool's demo root at this test's copy, so nothing here reads or could
    write the repository's fixture root."""

    def demo(self, *extra: str, json_mode: bool = False) -> Tuple[int, str]:
        with mock.patch.object(tool, "DEMO_DESK_ROOT", self.root):
            code, out = run_tool(["--demo"] + list(extra) + (["--json"] if json_mode else []))
        self.assertEqual([], order_shaped(out))
        return code, out

    def test_the_demo_names_the_checkouts_fixture_desk_and_the_regular_instant(self) -> None:
        self.assertEqual(os.path.realpath(FIXTURE_DESK), os.path.realpath(tool.DEMO_DESK_ROOT))
        self.assertEqual(contract.parse_utc_instant(REGULAR_NOW), contract.parse_utc_instant(tool.DEMO_NOW))
        self.assertEqual(DEMO_LINE, tool.DEMO_LINE)

    def test_the_demo_line_then_the_regular_goldens_and_nothing_written(self) -> None:
        before = self.tree()
        code, out = self.demo()
        self.assertEqual(0, code, out)
        self.assertEqual([DEMO_LINE] + TEXT, out.rstrip("\n").split("\n"))
        self.assertEqual(before, self.tree(), "no quote cache, no runtime directory, no ledger")
        # the whole printed text, the plain first line included, passes the strict lint with the snapshot footer
        self.assertEqual([], [token.text for token in alert_lint.numeric_tokens(DEMO_LINE)], "the line prints no number")
        code, out = self.demo(json_mode=True)
        self.assertEqual(0, code, out)
        body = json.loads(out)
        payload = body["payload"]
        self.assertEqual(("ok", "alpaca:paper"), (body["status"], body["source"]))
        self.assertEqual((True, "regular", "SPY", "648.12"), (payload["demo"], payload["fixture_set"], payload["symbol"],
                                                              payload["quote"]["mid"]))
        self.assertEqual("\n".join([DEMO_LINE] + TEXT), payload["message"])
        self.assertEqual("snapshot", payload["emitted"]["footer"])
        relayed = alert_lint.load_emitted(payload["emitted"]).check(payload["message"])
        self.assertTrue(relayed.ok, relayed.problems)
        self.assertEqual("2026-11-03T19:50:00+00:00", body["generated_at"], "the fixture instant, not the clock")
        self.assertEqual(before, self.tree())

    def test_a_regular_run_carries_demo_false_and_no_demo_line(self) -> None:
        code, out = self.offline()
        self.assertEqual(0, code, out)
        payload = json.loads(out)["payload"]
        self.assertIs(False, payload["demo"])
        self.assertNotIn(DEMO_LINE, payload["message"])

    def test_the_demo_loads_no_credential_and_opens_no_connection(self) -> None:
        with mock.patch.dict(os.environ, KEYS), \
                mock.patch.object(alpaca_client, "UrllibTransport", side_effect=AssertionError("no transport")), \
                mock.patch.object(alpaca_client, "load_credential", side_effect=AssertionError("no credential")):
            code, out = self.demo(json_mode=True)
        self.assertEqual(0, code, out)
        self.assertNotIn(SECRET, out)
        self.assertNotIn(KEY_ID, out)
        self.assertEqual(4, json.loads(out)["payload"]["requests_made"])

    def test_the_demo_takes_another_symbol_and_a_redundant_offline_flag(self) -> None:
        code, out = self.demo("--offline", json_mode=True)
        self.assertEqual(0, code, out)
        self.assertTrue(json.loads(out)["payload"]["demo"])
        code, out = self.demo("--symbol", "spy", json_mode=True)
        self.assertEqual((0, "SPY"), (code, json.loads(out)["payload"]["symbol"]))

    def test_the_demo_refuses_a_desk_root_an_instant_and_the_live_options(self) -> None:
        credentials_file = os.path.join(self.root, "alpaca.key")
        for extra in (["--desk-root", self.root], ["--now", REGULAR_NOW], ["--credentials-file", credentials_file],
                      ["--fixture-set", "stale"], ["--offline", "--desk-root", self.root]):
            with self.subTest(extra=extra):
                code, out = self.demo(*extra, json_mode=True)
                self.assertEqual(2, code, out)
                error = json.loads(out)["error"]
                self.assertEqual("invalid_arguments", error["code"])
                self.assertIn("--demo", error["message"])
        err = io.StringIO()
        with mock.patch.object(tool, "DEMO_DESK_ROOT", self.root), contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit) as raised:
                tool.main(["--demo", "--now", REGULAR_NOW])
        self.assertEqual(2, raised.exception.code)
        self.assertIn("--demo", err.getvalue())

    def test_a_missing_demo_desk_is_a_desk_root_error(self) -> None:
        with mock.patch.object(tool, "DEMO_DESK_ROOT", os.path.join(self.root, "not-there")):
            code, out = run_tool(["--demo", "--json"])
        self.assertEqual((1, "desk_root"), (code, json.loads(out)["error"]["code"]))


class LiveTest(DeskCopy):
    def test_no_key_pair_is_unavailable_and_opens_nothing(self) -> None:
        missing = os.path.join(self.root, "missing.key")
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(tool, "CREDENTIALS_FILE", missing), \
                mock.patch.object(alpaca_client, "UrllibTransport", side_effect=AssertionError("no transport")):
            code, out = run_tool(["--symbol", "SPY", "--now", REGULAR_NOW, "--json"])
            self.assertEqual((20, "no_credential"), (code, json.loads(out)["error"]["code"]))
            code, out = run_tool(["--symbol", "SPY", "--now", REGULAR_NOW, "--credentials-file", missing, "--json"])
            self.assertEqual((20, "no_credential"), (code, json.loads(out)["error"]["code"]))
            with mock.patch.dict(os.environ, {"APCA_API_KEY_ID": KEY_ID}):
                code, out = run_tool(["--symbol", "SPY", "--now", REGULAR_NOW, "--json"])
            self.assertEqual((20, "no_credential"), (code, json.loads(out)["error"]["code"]), "half a pair is none")
            self.assertNotIn(KEY_ID, out)
            self.assertNotIn(SECRET, out)

    def test_four_get_requests_on_the_paper_hosts(self) -> None:
        recording = Recording()
        with mock.patch.dict(os.environ, KEYS), \
                mock.patch.object(tool, "LIVE_TRANSPORT", recording):
            code, out = run_tool(["--symbol", "spy", "--now", REGULAR_NOW])
        self.assertEqual(0, code, out)
        self.assertEqual(TEXT, out.rstrip("\n").split("\n"))
        self.assertNotIn(SECRET, out)
        urls = [url for url, _headers in recording.calls]
        self.assertEqual(["https://data.alpaca.markets/v2/stocks/quotes/latest",
                          "https://paper-api.alpaca.markets/v2/options/contracts",
                          "https://paper-api.alpaca.markets/v2/options/contracts",
                          "https://data.alpaca.markets/v1beta1/options/snapshots/SPY"],
                         [url.split("?")[0] for url in urls])
        self.assertIn("feed=iex", urls[0])
        self.assertIn("feed=indicative", urls[3])
        self.assertIn("expiration_date_gte=2026-11-03", urls[1])
        for _url, headers in recording.calls:
            self.assertEqual((KEY_ID, SECRET), (headers.get("APCA-API-KEY-ID"), headers.get("APCA-API-SECRET-KEY")))
        agents = {headers.get("User-Agent") for _url, headers in recording.calls}
        self.assertEqual({"aegis-desk/%s opening_bell (read-only market data)" % version.release_version()}, agents)

    def test_a_key_the_paper_host_refuses_is_not_a_paper_key(self) -> None:
        # The data host answers live keys too, but the contract listing is read on the PAPER trading host, which
        # refuses a key from another environment (401/403): after a quote, that refusal names the key, not the pair.
        for http_status, failing_after in ((401, 1), (403, 1), (401, 2)):
            with self.subTest(http_status=http_status, failing_after=failing_after):
                recording = PaperHostRefuses(http_status, failing_after)
                with mock.patch.dict(os.environ, KEYS), mock.patch.object(tool, "LIVE_TRANSPORT", recording):
                    code, out = run_tool(["--symbol", "SPY", "--now", REGULAR_NOW])
                self.assertEqual(20, code, out)
                self.assertEqual([NOT_PAPER_KEY_LINE % http_status], out.rstrip("\n").split("\n"))
                with mock.patch.dict(os.environ, KEYS), \
                        mock.patch.object(tool, "LIVE_TRANSPORT", PaperHostRefuses(http_status, failing_after)):
                    code, out = run_tool(["--symbol", "SPY", "--now", REGULAR_NOW, "--json"])
                body = json.loads(out)
                self.assertEqual((20, "unavailable", "not_paper_key"), (code, body["status"], body["error"]["code"]))
                for value in (KEY_ID, SECRET):
                    self.assertNotIn(value, out)
        # A refused DATA host request (the first one) is still an ordinary failed request: a mismatched pair.
        with mock.patch.dict(os.environ, KEYS), \
                mock.patch.object(tool, "LIVE_TRANSPORT", PaperHostRefuses(401, 0, host="data.alpaca.markets")):
            code, out = run_tool(["--symbol", "SPY", "--now", REGULAR_NOW, "--json"])
        self.assertEqual((20, "request_failed"), (code, json.loads(out)["error"]["code"]))
        self.assertIn("the quotes request failed: auth", json.loads(out)["error"]["message"])

    def test_a_live_run_reads_no_desk_file(self) -> None:
        recording = Recording()
        empty = os.path.join(self.root, "nothing-here")
        os.makedirs(empty)
        with mock.patch.dict(os.environ, KEYS), \
                mock.patch.object(tool, "LIVE_TRANSPORT", recording):
            code, out = run_tool(["--symbol", "SPY", "--now", REGULAR_NOW, "--desk-root", empty, "--json"])
        self.assertEqual(0, code, out)
        self.assertEqual([], os.listdir(empty))
        self.assertIsNone(json.loads(out)["payload"]["fixture_set"])


if __name__ == "__main__":
    unittest.main()
