"""``scripts/opening_bell.py`` (the README's "try it now"): the quote, the nearest standard expiration at least 20
calendar days out, the ATM implied volatility from that chain's call and put mids (``aegis_desk.iv``) and the expected
move stated both ways, from exactly three read-only requests (PR 2d decision D2-14). Offline runs read the copied
fixture root's recorded set and write nothing; a live run reads no desk file and needs a token; the goldens are the
regular set's, recomputed with the landed engine."""

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

import opening_bell as tool  # the tool scripts/opening_bell.py
from aegis_desk import alert_lint, contract, tradier_client

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
REGULAR_NOW = "2026-11-03T19:50:00Z"
# Not token-shaped (no run of 20 alphanumerics), never a real credential.
SECRET = "fake-bell-secret-0000-1111-2222"
TEXT = [
    "SPY 648.12 mid (bid 648.10, ask 648.14), as of 19:31 UTC",
    "nearest standard expiration at least 20 calendar days out: 2026-12-18 (45 calendar days)",
    "ATM implied volatility at the 640 strike: 25.02 % (call 25.07 %, put 24.98 %; mids 28.65 and 17.30; r 0.04, q 0)",
    "expected move to 2026-12-18: straddle-based (call mid + put mid) x 0.80 = 36.76; one-sigma S x IV x sqrt(tau) = "
    "56.98",
    "Tradier sandbox, delayed 15 min, as of 19:31 UTC; a public market snapshot, not a signal",
    # the text is relayed verbatim, so it passes the strict alert lint; a public quote shown before any rule exists
    # ends with the market-snapshot footer (the rules footer would claim rules the owner has not adopted)
    alert_lint.snapshot_footer_line(contract.parse_utc_instant("2026-11-03T19:31:12Z")),
]
CHAIN = os.path.join("fixtures", "tradier", "regular", "chains__SPY__2026-12-18.json")
EXPIRATIONS = os.path.join("fixtures", "tradier", "regular", "expirations__SPY.json")


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
        self.inner = tradier_client.OfflineTransport(os.path.join(FIXTURE_DESK, "fixtures", "tradier"), "regular",
                                                     now=contract.parse_utc_instant(now))
        self.calls: List[Tuple[str, Dict[str, str]]] = []

    def fetch(self, url: str, headers: Dict[str, str], timeout_s: float) -> Any:
        self.calls.append((url, dict(headers)))
        return self.inner.fetch(url, headers, timeout_s)


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

    def edit_options(self, edit: Any) -> None:
        self.edit_json(CHAIN, lambda document: edit(document["options"]["option"]))


class SchemaAndUsageTest(DeskCopy):
    def test_schema(self) -> None:
        code, out = run_tool(["--schema"])
        self.assertEqual(0, code)
        body = json.loads(out)
        self.assertEqual("opening_bell", body["tool"])
        for path in ("/v1/markets/quotes", "/v1/markets/options/expirations", "/v1/markets/options/chains"):
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
        self.assertEqual(("ok", "tradier:sandbox", 15), (body["status"], body["source"], body["delay_min"]))
        payload = body["payload"]
        self.assertEqual(list(tool.PAYLOAD_KEYS), sorted(payload, key=tool.PAYLOAD_KEYS.index))
        self.assertEqual("\n".join(TEXT), payload["message"])
        self.assertEqual({"bid": "648.10", "ask": "648.14", "mid": "648.12", "as_of": "2026-11-03T19:31:12+00:00"},
                         payload["quote"], "each money field as the text prints it")
        self.assertEqual(("SPY", "2026-12-18", 45, "640"), (payload["symbol"], payload["expiration"],
                                                            payload["dte_calendar"], payload["strike"]))
        self.assertEqual({"mid": "28.65", "iv": 0.25066666696134243}, payload["call"])
        self.assertEqual({"mid": "17.30", "iv": 0.24979018665520808}, payload["put"])
        self.assertEqual(0.2502284268082753, payload["atm_iv"])
        self.assertEqual(0.12345662100456621, payload["tau_years"])
        self.assertEqual(("0.04", "0"), (payload["rate"], payload["q"]))
        self.assertEqual({"straddle": "36.76", "one_sigma": "56.98"}, payload["expected_move"])
        self.assertEqual((3, "regular"), (payload["requests_made"], payload["fixture_set"]))
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
        self.assertEqual((0.25006528161319297, 0.25025856294841303), (payload["call"]["iv"], payload["put"]["iv"]))
        self.assertEqual("0.0412", payload["rate"])
        self.assertIn("; r 0.0412, q 0)", payload["message"])

    def test_unavailable_answers_are_exit_20(self) -> None:
        cases = (
            ("no_expiration", lambda: self.edit_json(EXPIRATIONS, lambda d: d["expirations"].update(
                date=["2026-11-04", "2026-11-20", "2026-12-17"]))),
            ("stamps_unaligned", lambda: self.edit_options(lambda options: [
                option.update(bid_date=option["bid_date"] + 61000, ask_date=option["ask_date"] + 61000)
                for option in options])),
            ("iv_failed", lambda: self.edit_options(lambda options: [
                option.update(bid=1.0, ask=1.1) for option in options if option["option_type"] == "call"])),
            ("no_strike_pair", lambda: self.edit_options(lambda options: [
                options.remove(option) for option in list(options) if option["option_type"] == "put"])),
            ("no_two_sided_quote", lambda: self.edit_options(lambda options: [
                option.update(ask=None) for option in options])),
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


class LiveTest(DeskCopy):
    def test_no_token_is_unavailable_and_opens_nothing(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True), \
                mock.patch.object(tradier_client, "UrllibTransport", side_effect=AssertionError("no transport")):
            code, out = run_tool(["--symbol", "SPY", "--now", REGULAR_NOW, "--json"])
            self.assertEqual((20, "no_token"), (code, json.loads(out)["error"]["code"]))
            code, out = run_tool(["--symbol", "SPY", "--now", REGULAR_NOW, "--token-file",
                                  os.path.join(self.root, "missing.token"), "--json"])
            self.assertEqual((20, "no_token"), (code, json.loads(out)["error"]["code"]))
            self.assertNotIn(SECRET, out)

    def test_three_get_requests_on_the_sandbox_host(self) -> None:
        recording = Recording()
        with mock.patch.dict(os.environ, {"TRADIER_API_TOKEN": SECRET}), \
                mock.patch.object(tool, "LIVE_TRANSPORT", recording):
            code, out = run_tool(["--symbol", "spy", "--now", REGULAR_NOW])
        self.assertEqual(0, code, out)
        self.assertEqual(TEXT, out.rstrip("\n").split("\n"))
        self.assertNotIn(SECRET, out)
        urls = [url for url, _headers in recording.calls]
        self.assertEqual(3, len(urls))
        self.assertTrue(all(url.startswith("https://sandbox.tradier.com/v1/markets/") for url in urls), urls)
        self.assertEqual(["quotes", "options/expirations", "options/chains"],
                         [url.split("/v1/markets/")[1].split("?")[0] for url in urls])
        agents = {headers.get("User-Agent") for _url, headers in recording.calls}
        self.assertEqual({"aegis-desk/0.1.0 opening_bell (read-only market data)"}, agents)

    def test_a_live_run_reads_no_desk_file(self) -> None:
        recording = Recording()
        empty = os.path.join(self.root, "nothing-here")
        os.makedirs(empty)
        with mock.patch.dict(os.environ, {"TRADIER_API_TOKEN": SECRET}), \
                mock.patch.object(tool, "LIVE_TRANSPORT", recording):
            code, out = run_tool(["--symbol", "SPY", "--now", REGULAR_NOW, "--desk-root", empty, "--json"])
        self.assertEqual(0, code, out)
        self.assertEqual([], os.listdir(empty))
        self.assertIsNone(json.loads(out)["payload"]["fixture_set"])


if __name__ == "__main__":
    unittest.main()
