"""The offline fixture desk root (``tests/fixtures/desk``): shapes, instants and consistency.

These tests are deliberately independent of ``aegis_desk.market_types`` (they read the JSON with
the standard library, convert epochs with the brief's own formula and use ``market_clock.to_et``
and ``occ.parse`` as the independent checks), so a fixture mistake and a parser mistake can never
cancel out. The instants and prices asserted here are the contract the data-plane builders rely
on; the README beside the sets must state every epoch value.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import json
import os
import re
import unittest
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Dict, Iterator, List, Tuple

from aegis_desk import market_clock as mc
from aegis_desk import occ

UTC = timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
TRADIER = os.path.join(DESK, "fixtures", "tradier")
README = os.path.join(TRADIER, "README.md")

SETS = ("regular", "after_close", "stale", "pre_open", "holiday")
UNDERLYINGS = ("SPY", "WMT", "NVDA")
OPTIONS = ("WMT261120C00105000", "NVDA261120C00150000", "SPY261218P00620000")
EXPIRED_BY_HOLIDAY = ("WMT261120C00105000", "NVDA261120C00150000")  # expired 2026-11-20
NOV3_SETS = ("regular", "after_close", "stale")

# Per set: ET date of the quotes, the quote stamp (UTC), the --now instant (UTC), the clock date
# and state, whether the vendor's ``close`` field is populated (only after a close).
SET_FACTS: Dict[str, Dict[str, Any]] = {
    "regular": {"quote_date": date(2026, 11, 3), "stamp": datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC),
                "now": datetime(2026, 11, 3, 19, 50, 0, tzinfo=UTC), "clock_date": "2026-11-03",
                "state": "open", "close_set": False, "stamp_et": "2026-11-03 14:31:12"},
    "after_close": {"quote_date": date(2026, 11, 3), "stamp": datetime(2026, 11, 3, 20, 59, 40, tzinfo=UTC),
                    "now": datetime(2026, 11, 3, 21, 25, 0, tzinfo=UTC), "clock_date": "2026-11-03",
                    "state": "postmarket", "close_set": True, "stamp_et": "2026-11-03 15:59:40"},
    "stale": {"quote_date": date(2026, 11, 3), "stamp": datetime(2026, 11, 3, 18, 50, 0, tzinfo=UTC),
              "now": datetime(2026, 11, 3, 19, 50, 0, tzinfo=UTC), "clock_date": "2026-11-03",
              "state": "open", "close_set": False, "stamp_et": "2026-11-03 13:50:00"},
    "pre_open": {"quote_date": date(2026, 11, 2), "stamp": datetime(2026, 11, 2, 20, 59, 55, tzinfo=UTC),
                 "now": datetime(2026, 11, 3, 13, 0, 0, tzinfo=UTC), "clock_date": "2026-11-03",
                 "state": "premarket", "close_set": True, "stamp_et": "2026-11-02 15:59:55"},
    "holiday": {"quote_date": date(2026, 11, 25), "stamp": datetime(2026, 11, 25, 20, 59, 50, tzinfo=UTC),
                "now": datetime(2026, 11, 26, 15, 0, 0, tzinfo=UTC), "clock_date": "2026-11-26",
                "state": "closed", "close_set": True, "stamp_et": "2026-11-25 15:59:50"},
}
# Anchor prices of the 2026-11-03 sets (the brief's numbers), identical in regular/stale/after_close.
NOV3_ANCHORS: Dict[str, Tuple[str, str]] = {
    "SPY": ("648.10", "648.14"), "WMT": ("104.20", "104.26"), "NVDA": ("148.90", "148.95"),
    "WMT261120C00105000": ("3.35", "3.50"), "NVDA261120C00150000": ("3.10", "3.30"),
    "SPY261218P00620000": ("9.80", "10.10"),
}
# Quote files every set used by the journeys and tool tests must hold (the OfflineTransport key).
REQUIRED_QUOTE_KEYS = (
    "SPY", "WMT", "NVDA", "SPY,WMT", "NVDA,SPY,WMT",
    "WMT261120C00105000", "NVDA261120C00150000", "SPY261218P00620000",
    "SPY,WMT,WMT261120C00105000",
    "NVDA,NVDA261120C00150000,SPY,SPY261218P00620000,WMT,WMT261120C00105000",
)
CHAINS = {"WMT": "2026-11-20", "NVDA": "2026-11-20", "SPY": "2026-12-18"}
HISTORY_RANGE = ("2026-10-27", "2026-11-02")
DATE_KEYS = ("bid_date", "ask_date", "trade_date")
TOKEN_RE = re.compile(r"(?i)(?:bearer|token)[^A-Za-z0-9]{0,8}[A-Za-z0-9]{20,}")


def read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def walk_files(root: str) -> List[str]:
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for name in sorted(filenames):
            found.append(os.path.join(dirpath, name))
    return found


def set_dir(name: str) -> str:
    return os.path.join(TRADIER, name)


def quote_files(set_name: str) -> List[str]:
    return sorted(p for p in walk_files(set_dir(set_name)) if os.path.basename(p).startswith("quotes__"))


def as_list(value: Any) -> list:
    if value is None or value == "null" or value == "":
        return []
    return list(value) if isinstance(value, list) else [value]


def quotes_in(payload: Any) -> Tuple[List[dict], List[str]]:
    """(matched quote dicts, unmatched symbols) from a recorded /markets/quotes body."""
    quotes = payload.get("quotes")
    if quotes in (None, "null"):
        return [], []
    matched = as_list(quotes.get("quote"))
    unmatched = quotes.get("unmatched_symbols") or {}
    return matched, [str(s) for s in as_list(unmatched.get("symbol"))]


def iter_stamps(node: Any) -> Iterator[Tuple[str, Any]]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in DATE_KEYS:
                yield key, value
            else:
                for item in iter_stamps(value):
                    yield item
    elif isinstance(node, list):
        for value in node:
            for item in iter_stamps(value):
                yield item


def epoch_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def epoch_s(moment: datetime) -> int:
    return int(moment.timestamp())


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


class TreeShapeTest(unittest.TestCase):
    def test_owner_files_present(self) -> None:
        self.assertTrue(os.path.isfile(os.path.join(DESK, "desk.toml")))
        self.assertTrue(os.path.isfile(os.path.join(DESK, "positions.csv")))
        self.assertTrue(os.path.isfile(README))

    def test_positions_csv_is_header_plus_one_comment(self) -> None:
        lines = read_text(os.path.join(DESK, "positions.csv")).splitlines()
        self.assertEqual(2, len(lines), lines)
        header = lines[0].split(",")
        for column in ("instrument_type", "symbol", "occ_symbol", "quantity", "expiration", "option_right",
                       "strike", "account", "trade_id", "opened", "cost_basis_total", "cost_basis_per_unit",
                       "open_price_per_share", "status", "closed", "closed_quantity", "close_price_per_unit",
                       "underlying_stop", "note"):
            self.assertIn(column, header)
        self.assertEqual(len(header), len(set(header)))
        self.assertTrue(lines[1].startswith("#"), lines[1])

    def test_every_set_has_its_mandatory_files(self) -> None:
        for name in SETS:
            directory = set_dir(name)
            self.assertTrue(os.path.isdir(directory), name)
            for rel in ("clock.json", "calendar__2026-11.json", "errors/quota_violation.txt", "errors/invalid_token.txt",
                        "expirations__SPY.json", "expirations__WMT.json", "expirations__NVDA.json",
                        "strikes__SPY__2026-12-18.json", "strikes__WMT__2026-11-20.json", "strikes__NVDA__2026-11-20.json",
                        "chains__SPY__2026-12-18.json", "chains__WMT__2026-11-20.json", "chains__NVDA__2026-11-20.json",
                        "history__SPY__2026-10-27__2026-11-02.json", "history__WMT__2026-10-27__2026-11-02.json",
                        "history__NVDA__2026-10-27__2026-11-02.json"):
                self.assertTrue(os.path.isfile(os.path.join(directory, *rel.split("/"))), "%s/%s" % (name, rel))
            for key in REQUIRED_QUOTE_KEYS:
                self.assertTrue(os.path.isfile(os.path.join(directory, "quotes__%s.json" % key)), "%s/quotes__%s" % (name, key))

    def test_every_json_file_parses(self) -> None:
        files = [p for p in walk_files(DESK) if p.endswith(".json")]
        self.assertGreaterEqual(len(files), 5 * 16)
        for path in files:
            payload = read_json(path)
            self.assertIsInstance(payload, dict, path)

    def test_file_names_follow_the_naming_rule(self) -> None:
        name_re = re.compile(r"^(quotes|chains|expirations|strikes|history|calendar)__(.+)\.json$|^clock\.json$")
        for name in SETS:
            for path in walk_files(set_dir(name)):
                rel = os.path.relpath(path, set_dir(name)).replace(os.sep, "/")
                if rel.startswith("errors/"):
                    self.assertIn(rel, ("errors/quota_violation.txt", "errors/invalid_token.txt"))
                    continue
                self.assertRegex(rel, name_re)
                if rel.startswith("quotes__"):
                    key = rel[len("quotes__"):-len(".json")]
                    self.assertEqual(key, key.upper(), rel)
                kind = rel.split("__")[0]
                parts = rel[:-5].split("__")[1:]
                if kind == "chains" or kind == "strikes":
                    self.assertEqual(2, len(parts), rel)
                    date.fromisoformat(parts[1])
                elif kind == "history":
                    self.assertEqual(3, len(parts), rel)
                    self.assertLessEqual(date.fromisoformat(parts[1]), date.fromisoformat(parts[2]))
                elif kind == "expirations":
                    self.assertEqual(1, len(parts), rel)
                elif kind == "calendar":
                    self.assertRegex(parts[0], r"^\d{4}-\d{2}$")

    def test_error_bodies(self) -> None:
        for name in SETS:
            quota = read_text(os.path.join(set_dir(name), "errors", "quota_violation.txt"))
            self.assertIn("quota violation", quota.lower())
            self.assertIn("QuotaViolation", quota)
            json.loads(quota)  # the recorded fault body is JSON text
            token = read_text(os.path.join(set_dir(name), "errors", "invalid_token.txt"))
            self.assertEqual("Invalid Access Token", token.strip())

    def test_no_token_shaped_string_anywhere(self) -> None:
        for path in walk_files(DESK):
            text = read_text(path)
            self.assertIsNone(TOKEN_RE.search(text), path)
            self.assertNotIn("Bearer ", text, path)
            self.assertNotIn("Authorization", text, path)

    def test_no_emoji_or_release_tags_in_the_readme_and_toml(self) -> None:
        emoji = re.compile("[⌀-⏿☀-➿⬀-⯿\U0001F000-\U0001FAFF️]")
        for path in (README, os.path.join(DESK, "desk.toml"), os.path.join(DESK, "positions.csv")):
            text = read_text(path)
            self.assertIsNone(emoji.search(text), path)
            self.assertIsNone(re.search(r"\bv\d+\.\d+\.\d+\b", text), path)


class QuoteFileTest(unittest.TestCase):
    def test_symbols_match_the_file_name(self) -> None:
        for name in SETS:
            for path in quote_files(name):
                key = os.path.basename(path)[len("quotes__"):-len(".json")]
                symbols = key.split(",")
                self.assertEqual(symbols, sorted(symbols), path)
                self.assertEqual(key, key.upper(), path)
                self.assertEqual(len(symbols), len(set(symbols)), path)
                matched, unmatched = quotes_in(read_json(path))
                found = sorted([q["symbol"] for q in matched] + unmatched)
                self.assertEqual(symbols, found, path)

    def test_one_symbol_is_an_object_and_several_are_a_list(self) -> None:
        for name in SETS:
            for path in quote_files(name):
                quotes = read_json(path)["quotes"]
                matched = quotes.get("quote")
                if matched is None:
                    self.assertIn("unmatched_symbols", quotes, path)
                elif isinstance(matched, list):
                    self.assertGreaterEqual(len(matched), 2, path)
                else:
                    self.assertIsInstance(matched, dict, path)
                unmatched = quotes.get("unmatched_symbols")
                if unmatched is not None:
                    symbol = unmatched["symbol"]
                    self.assertTrue(isinstance(symbol, str) or (isinstance(symbol, list) and len(symbol) >= 2), path)

    def test_every_quote_carries_the_sets_stamp_on_every_side(self) -> None:
        for name in SETS:
            facts = SET_FACTS[name]
            expected_ms = epoch_ms(facts["stamp"])
            for path in quote_files(name):
                matched, _ = quotes_in(read_json(path))
                for quote in matched:
                    for key in DATE_KEYS:
                        self.assertEqual(expected_ms, quote[key], "%s %s %s" % (path, quote["symbol"], key))
                        self.assertIsInstance(quote[key], int)

    def test_quote_shapes_follow_the_recorded_tradier_fields(self) -> None:
        required = ("symbol", "description", "exch", "type", "last", "change", "volume", "open", "high", "low",
                    "close", "bid", "ask", "change_percentage", "average_volume", "last_volume", "trade_date",
                    "prevclose", "week_52_high", "week_52_low", "bidsize", "bidexch", "bid_date", "asksize",
                    "askexch", "ask_date")
        for name in SETS:
            for path in quote_files(name):
                matched, _ = quotes_in(read_json(path))
                for quote in matched:
                    for key in required:
                        self.assertIn(key, quote, "%s %s" % (path, key))
                    self.assertIn(quote["type"], ("stock", "etf", "option"), path)
                    if quote["type"] == "option":
                        for key in ("underlying", "strike", "open_interest", "contract_size", "expiration_date",
                                    "expiration_type", "option_type", "root_symbol"):
                            self.assertIn(key, quote, "%s %s" % (path, key))
                        self.assertNotIn("greeks", quote, path)  # the sandbox has no Greeks
                        self.assertNotIn("root_symbols", quote, path)
                    else:
                        self.assertIn("root_symbols", quote, path)
                        self.assertIsNone(quote["strike"], path)
                        self.assertIsNone(quote["option_type"], path)
                    for key in ("bid", "ask", "last", "prevclose", "open", "high", "low"):
                        self.assertIsInstance(quote[key], (int, float), "%s %s %s" % (path, quote["symbol"], key))
                        self.assertNotIsInstance(quote[key], bool)
                    if SET_FACTS[name]["close_set"]:
                        self.assertEqual(quote["last"], quote["close"], "%s %s" % (path, quote["symbol"]))
                    else:
                        self.assertIsNone(quote["close"], "%s %s" % (path, quote["symbol"]))

    def test_prices_are_coherent(self) -> None:
        for name in SETS:
            for path in quote_files(name):
                matched, _ = quotes_in(read_json(path))
                for quote in matched:
                    tag = "%s %s" % (path, quote["symbol"])
                    bid, ask, last = dec(quote["bid"]), dec(quote["ask"]), dec(quote["last"])
                    self.assertLess(bid, ask, tag)
                    self.assertLessEqual(bid, last, tag)
                    self.assertLessEqual(last, ask, tag)
                    self.assertLessEqual(dec(quote["low"]), last, tag)
                    self.assertLessEqual(last, dec(quote["high"]), tag)
                    self.assertLessEqual(dec(quote["low"]), dec(quote["open"]), tag)
                    self.assertLessEqual(dec(quote["open"]), dec(quote["high"]), tag)
                    prev = dec(quote["prevclose"])
                    self.assertGreater(prev, 0, tag)
                    self.assertEqual(last - prev, dec(quote["change"]), tag)
                    pct = ((last - prev) / prev * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN)
                    self.assertEqual(pct, dec(quote["change_percentage"]), tag)
                    self.assertGreater(quote["volume"], 0, tag)
                    self.assertGreater(quote["bidsize"], 0, tag)
                    self.assertGreater(quote["asksize"], 0, tag)

    def test_the_same_symbol_is_identical_across_the_files_of_one_set(self) -> None:
        for name in SETS:
            seen: Dict[str, dict] = {}
            for path in quote_files(name):
                matched, _ = quotes_in(read_json(path))
                for quote in matched:
                    if quote["symbol"] in seen:
                        self.assertEqual(seen[quote["symbol"]], quote, "%s %s" % (path, quote["symbol"]))
                    else:
                        seen[quote["symbol"]] = quote
            self.assertGreaterEqual(len(seen), 4, name)

    def test_nov3_sets_share_prices_and_differ_only_in_stamps_and_activity(self) -> None:
        by_set = {}
        for name in NOV3_SETS:
            quotes = {}
            for path in quote_files(name):
                matched, _ = quotes_in(read_json(path))
                for quote in matched:
                    quotes[quote["symbol"]] = quote
            by_set[name] = quotes
        for symbol in UNDERLYINGS + OPTIONS:
            base = by_set["regular"][symbol]
            for name in ("stale", "after_close"):
                other = by_set[name][symbol]
                for key in ("bid", "ask", "last", "prevclose", "open", "high", "low", "change", "change_percentage",
                            "description", "exch", "type", "bidsize", "asksize"):
                    self.assertEqual(base[key], other[key], "%s %s %s" % (name, symbol, key))
            self.assertLessEqual(by_set["stale"][symbol]["volume"], base["volume"], symbol)
            self.assertLessEqual(base["volume"], by_set["after_close"][symbol]["volume"], symbol)

    def test_anchor_prices_of_the_brief(self) -> None:
        for name in NOV3_SETS:
            for symbol, (bid, ask) in NOV3_ANCHORS.items():
                quote = quotes_in(read_json(os.path.join(set_dir(name), "quotes__%s.json" % symbol)))[0][0]
                self.assertEqual(Decimal(bid), dec(quote["bid"]), "%s %s" % (name, symbol))
                self.assertEqual(Decimal(ask), dec(quote["ask"]), "%s %s" % (name, symbol))

    def test_option_symbols_agree_with_their_fields(self) -> None:
        for name in SETS:
            for path in quote_files(name) + [os.path.join(set_dir(name), "chains__%s__%s.json" % item) for item in CHAINS.items()]:
                payload = read_json(path)
                if os.path.basename(path).startswith("chains__"):
                    options = payload.get("options")
                    quotes = [] if options is None else as_list(options.get("option"))
                else:
                    quotes = [q for q in quotes_in(payload)[0] if q["type"] == "option"]
                for quote in quotes:
                    parsed = occ.parse(quote["symbol"])
                    self.assertEqual(parsed.compact, quote["symbol"])
                    self.assertEqual(parsed.strike, dec(quote["strike"]), quote["symbol"])
                    self.assertEqual(parsed.expiration.isoformat(), quote["expiration_date"], quote["symbol"])
                    self.assertEqual("call" if parsed.is_call else "put", quote["option_type"], quote["symbol"])
                    self.assertEqual(parsed.root, quote["underlying"], quote["symbol"])
                    self.assertEqual(parsed.root, quote["root_symbol"], quote["symbol"])
                    self.assertEqual(100, quote["contract_size"], quote["symbol"])
                    self.assertEqual("standard", quote["expiration_type"], quote["symbol"])
                    self.assertGreater(quote["open_interest"], 0, quote["symbol"])
                    self.assertGreater(parsed.expiration, SET_FACTS[name]["quote_date"], "%s %s" % (name, quote["symbol"]))

    def test_holiday_set_reports_the_expired_contracts_as_unmatched(self) -> None:
        for symbol in EXPIRED_BY_HOLIDAY:
            payload = read_json(os.path.join(set_dir("holiday"), "quotes__%s.json" % symbol))
            matched, unmatched = quotes_in(payload)
            self.assertEqual([], matched, symbol)
            self.assertEqual([symbol], unmatched, symbol)
            self.assertIsInstance(payload["quotes"]["unmatched_symbols"]["symbol"], str)
        six = read_json(os.path.join(set_dir("holiday"), "quotes__%s.json" % REQUIRED_QUOTE_KEYS[-1]))
        matched, unmatched = quotes_in(six)
        self.assertEqual(sorted(UNDERLYINGS + ("SPY261218P00620000",)), sorted(q["symbol"] for q in matched))
        self.assertEqual(sorted(EXPIRED_BY_HOLIDAY), sorted(unmatched))
        self.assertIsInstance(six["quotes"]["unmatched_symbols"]["symbol"], list)
        three = read_json(os.path.join(set_dir("holiday"), "quotes__SPY,WMT,WMT261120C00105000.json"))
        matched, unmatched = quotes_in(three)
        self.assertEqual(["SPY", "WMT"], sorted(q["symbol"] for q in matched))
        self.assertEqual(["WMT261120C00105000"], unmatched)

    def test_regular_set_has_an_index_symbol_the_sandbox_cannot_quote(self) -> None:
        alone = read_json(os.path.join(set_dir("regular"), "quotes__SPX.json"))
        self.assertEqual(([], ["SPX"]), quotes_in(alone))
        self.assertNotIn("quote", alone["quotes"])
        pair = read_json(os.path.join(set_dir("regular"), "quotes__SPX,SPY.json"))
        matched, unmatched = quotes_in(pair)
        self.assertEqual(["SPY"], [q["symbol"] for q in matched])
        self.assertIsInstance(pair["quotes"]["quote"], dict)  # one match: an object, not a list
        self.assertEqual(["SPX"], unmatched)


class StampTest(unittest.TestCase):
    def test_regular_set_stamps_read_14_31_12_et_on_2026_11_03(self) -> None:
        count = 0
        for path in walk_files(set_dir("regular")):
            if not path.endswith(".json") or os.path.basename(path) == "clock.json":
                continue
            for key, value in iter_stamps(read_json(path)):
                moment = datetime.fromtimestamp(value / 1000, tz=UTC)
                et = mc.to_et(moment)
                self.assertEqual("2026-11-03 14:31:12", et.strftime("%Y-%m-%d %H:%M:%S"), "%s %s" % (path, key))
                self.assertEqual(mc.EST, et.tzinfo, path)
                self.assertEqual(1793734272000, value, path)
                count += 1
        self.assertGreater(count, 30)

    def test_every_set_stamps_read_as_documented(self) -> None:
        for name in SETS:
            facts = SET_FACTS[name]
            count = 0
            for path in walk_files(set_dir(name)):
                if not path.endswith(".json") or os.path.basename(path) == "clock.json":
                    continue
                for key, value in iter_stamps(read_json(path)):
                    et = mc.to_et(datetime.fromtimestamp(value / 1000, tz=UTC))
                    self.assertEqual(facts["stamp_et"], et.strftime("%Y-%m-%d %H:%M:%S"), "%s %s" % (path, key))
                    self.assertEqual(epoch_ms(facts["stamp"]), value, path)
                    count += 1
            self.assertGreater(count, 0, name)
            # Quotes are stamped before --now, never after it, and on the documented trading day.
            self.assertLess(facts["stamp"], facts["now"], name)
            self.assertEqual(facts["quote_date"], mc.to_et(facts["stamp"]).date(), name)
            self.assertTrue(mc.is_trading_day(facts["quote_date"]), name)

    def test_the_documented_epoch_values(self) -> None:
        expected = {
            "regular": (1793734272000, 1793735400), "after_close": (1793739580000, 1793741100),
            "stale": (1793731800000, 1793735400), "pre_open": (1793653195000, 1793710800),
            "holiday": (1795640390000, 1795705200),
        }
        for name, (stamp_ms, now_s) in expected.items():
            self.assertEqual(stamp_ms, epoch_ms(SET_FACTS[name]["stamp"]), name)
            self.assertEqual(now_s, epoch_s(SET_FACTS[name]["now"]), name)
            self.assertEqual(int(SET_FACTS[name]["stamp"].timestamp() * 1000), stamp_ms)

    def test_clock_json_per_set(self) -> None:
        for name in SETS:
            facts = SET_FACTS[name]
            clock = read_json(os.path.join(set_dir(name), "clock.json"))["clock"]
            self.assertEqual(facts["clock_date"], clock["date"], name)
            self.assertEqual(facts["state"], clock["state"], name)
            self.assertEqual(epoch_s(facts["now"]), clock["timestamp"], name)
            self.assertIsInstance(clock["timestamp"], int)
            self.assertEqual(mc.to_et(facts["now"]).date().isoformat(), clock["date"], name)
            self.assertIn("description", clock)
            self.assertRegex(clock["next_change"], r"^\d{2}:\d{2}$")
            self.assertIn(clock["next_state"], ("premarket", "open", "postmarket", "closed"))
            local = mc.session_at(facts["now"])
            reconciled = mc.reconcile_vendor_state(local, clock)
            self.assertTrue(reconciled.agree, "%s: %s" % (name, reconciled.disagreement))

    def test_sessions_the_sets_exercise(self) -> None:
        self.assertEqual("open", mc.session_at(SET_FACTS["regular"]["now"]).session)
        self.assertEqual("open", mc.session_at(SET_FACTS["stale"]["now"]).session)
        self.assertEqual("closed", mc.session_at(SET_FACTS["after_close"]["now"]).session)
        self.assertEqual("pre", mc.session_at(SET_FACTS["pre_open"]["now"]).session)
        holiday = mc.session_at(SET_FACTS["holiday"]["now"])
        self.assertEqual("holiday", holiday.session)
        self.assertEqual("Thanksgiving Day", holiday.holiday_name)
        # Ages under the 25-minute bound: 18 minutes (fresh) and 60 minutes (stale).
        age = (SET_FACTS["regular"]["now"] - SET_FACTS["regular"]["stamp"]).total_seconds() // 60
        self.assertEqual(18, age)
        age = (SET_FACTS["stale"]["now"] - SET_FACTS["stale"]["stamp"]).total_seconds() // 60
        self.assertEqual(60, age)
        close = mc.session_at(SET_FACTS["after_close"]["now"]).close_utc
        self.assertEqual(datetime(2026, 11, 3, 21, 0, tzinfo=UTC), close)
        self.assertEqual(20, (close - SET_FACTS["after_close"]["stamp"]).total_seconds())
        prior = mc.previous_close_utc(SET_FACTS["pre_open"]["now"])
        self.assertEqual(datetime(2026, 11, 2, 21, 0, tzinfo=UTC), prior)
        self.assertEqual(5, (prior - SET_FACTS["pre_open"]["stamp"]).total_seconds())


class ChainExpirationStrikeHistoryTest(unittest.TestCase):
    def test_chains(self) -> None:
        for name in SETS:
            for symbol, expiration in CHAINS.items():
                payload = read_json(os.path.join(set_dir(name), "chains__%s__%s.json" % (symbol, expiration)))
                self.assertEqual(["options"], list(payload))
                expired = name == "holiday" and date.fromisoformat(expiration) < SET_FACTS[name]["quote_date"]
                if expired:
                    self.assertIsNone(payload["options"], "%s %s" % (name, symbol))
                    continue
                options = payload["options"]["option"]
                self.assertIsInstance(options, list)
                self.assertGreaterEqual(len(options), 6, "%s %s" % (name, symbol))
                strikes = sorted({dec(o["strike"]) for o in options})
                self.assertEqual(sorted(dec(o["strike"]) for o in options), sorted(dec(o["strike"]) for o in options))
                for strike in strikes:
                    rights = sorted(o["option_type"] for o in options if dec(o["strike"]) == strike)
                    self.assertEqual(["call", "put"], rights, "%s %s %s" % (name, symbol, strike))
                for option in options:
                    self.assertEqual(symbol, option["underlying"])
                    self.assertEqual(expiration, option["expiration_date"])
                    self.assertIn("greeks", option)
                    self.assertIsNone(option["greeks"], "%s %s" % (name, option["symbol"]))
                    self.assertLess(dec(option["bid"]), dec(option["ask"]), option["symbol"])
                    self.assertGreaterEqual(dec(option["bid"]), 0, option["symbol"])

    def test_chain_anchor_equals_the_standalone_quote(self) -> None:
        anchors = {"WMT": "WMT261120C00105000", "NVDA": "NVDA261120C00150000", "SPY": "SPY261218P00620000"}
        for name in SETS:
            for symbol, expiration in CHAINS.items():
                anchor = anchors[symbol]
                if name == "holiday" and anchor in EXPIRED_BY_HOLIDAY:
                    continue
                chain = read_json(os.path.join(set_dir(name), "chains__%s__%s.json" % (symbol, expiration)))
                in_chain = [o for o in chain["options"]["option"] if o["symbol"] == anchor]
                self.assertEqual(1, len(in_chain), "%s %s" % (name, anchor))
                standalone = quotes_in(read_json(os.path.join(set_dir(name), "quotes__%s.json" % anchor)))[0][0]
                chain_copy = dict(in_chain[0])
                chain_copy.pop("greeks")
                self.assertEqual(standalone, chain_copy, "%s %s" % (name, anchor))

    def test_chain_puts_and_calls_are_monotone_in_strike(self) -> None:
        for name in SETS:
            for symbol, expiration in CHAINS.items():
                payload = read_json(os.path.join(set_dir(name), "chains__%s__%s.json" % (symbol, expiration)))
                if payload["options"] is None:
                    continue
                options = payload["options"]["option"]
                for right, sign in (("call", -1), ("put", 1)):
                    series = sorted(((dec(o["strike"]), (dec(o["bid"]) + dec(o["ask"])) / 2) for o in options
                                     if o["option_type"] == right))
                    mids = [m for _, m in series]
                    for earlier, later in zip(mids, mids[1:]):
                        if sign < 0:
                            self.assertGreaterEqual(earlier, later, "%s %s %s" % (name, symbol, right))
                        else:
                            self.assertLessEqual(earlier, later, "%s %s %s" % (name, symbol, right))

    def test_expirations(self) -> None:
        for name in SETS:
            for symbol in UNDERLYINGS:
                payload = read_json(os.path.join(set_dir(name), "expirations__%s.json" % symbol))
                dates = payload["expirations"]["date"]
                self.assertIsInstance(dates, list)
                self.assertGreaterEqual(len(dates), 6)
                parsed = [date.fromisoformat(d) for d in dates]
                self.assertEqual(sorted(parsed), parsed, "%s %s" % (name, symbol))
                self.assertEqual(len(set(parsed)), len(parsed))
                for d in parsed:
                    self.assertGreater(d, SET_FACTS[name]["quote_date"], "%s %s %s" % (name, symbol, d))
                    self.assertTrue(mc.is_trading_day(d), "%s %s %s" % (name, symbol, d))
                if name != "holiday":
                    self.assertIn(CHAINS[symbol], dates, "%s %s" % (name, symbol))
                else:
                    self.assertNotIn("2026-11-20", dates)
                    if symbol == "SPY":
                        self.assertIn("2026-12-18", dates)

    def test_strikes(self) -> None:
        anchors = {"WMT": Decimal("105"), "NVDA": Decimal("150"), "SPY": Decimal("620")}
        for name in SETS:
            for symbol, expiration in CHAINS.items():
                payload = read_json(os.path.join(set_dir(name), "strikes__%s__%s.json" % (symbol, expiration)))
                self.assertEqual(["strikes"], list(payload))
                if name == "holiday" and date.fromisoformat(expiration) < SET_FACTS[name]["quote_date"]:
                    self.assertIsNone(payload["strikes"], "%s %s" % (name, symbol))
                    continue
                strikes = payload["strikes"]["strike"]
                self.assertIsInstance(strikes, list)
                values = [dec(s) for s in strikes]
                self.assertEqual(sorted(values), values)
                self.assertIn(anchors[symbol], values, "%s %s" % (name, symbol))
                for raw in strikes:
                    self.assertIsInstance(raw, float, "%s %s %r" % (name, symbol, raw))
                chain = read_json(os.path.join(set_dir(name), "chains__%s__%s.json" % (symbol, expiration)))
                chain_strikes = {dec(o["strike"]) for o in chain["options"]["option"]}
                self.assertTrue(chain_strikes <= set(values), "%s %s" % (name, symbol))

    def test_history_is_consistent_with_the_quotes(self) -> None:
        for name in SETS:
            for symbol in UNDERLYINGS:
                payload = read_json(os.path.join(set_dir(name), "history__%s__%s__%s.json" % ((symbol,) + HISTORY_RANGE)))
                bars = payload["history"]["day"]
                self.assertEqual(5, len(bars), "%s %s" % (name, symbol))
                dates = [date.fromisoformat(b["date"]) for b in bars]
                self.assertEqual(sorted(dates), dates)
                self.assertEqual(date(2026, 10, 27), dates[0])
                self.assertEqual(date(2026, 11, 2), dates[-1])
                for bar in bars:
                    self.assertTrue(mc.is_trading_day(date.fromisoformat(bar["date"])), bar["date"])
                    o, h, lo, c = dec(bar["open"]), dec(bar["high"]), dec(bar["low"]), dec(bar["close"])
                    self.assertLessEqual(lo, min(o, c), bar)
                    self.assertLessEqual(max(o, c), h, bar)
                    self.assertGreater(bar["volume"], 0, bar)
                    self.assertIsInstance(bar["volume"], int)
                    self.assertEqual(["date", "open", "high", "low", "close", "volume"], list(bar))
                last_close = dec(bars[-1]["close"])
                if name in NOV3_SETS:
                    quote = quotes_in(read_json(os.path.join(set_dir(name), "quotes__%s.json" % symbol)))[0][0]
                    self.assertEqual(dec(quote["prevclose"]), last_close, "%s %s" % (name, symbol))
                if name == "pre_open":
                    quote = quotes_in(read_json(os.path.join(set_dir(name), "quotes__%s.json" % symbol)))[0][0]
                    self.assertEqual(dec(quote["close"]), last_close, symbol)
                    self.assertEqual(dec(quote["open"]), dec(bars[-1]["open"]), symbol)
                    self.assertEqual(dec(quote["high"]), dec(bars[-1]["high"]), symbol)
                    self.assertEqual(dec(quote["low"]), dec(bars[-1]["low"]), symbol)
                    self.assertEqual(quote["volume"], bars[-1]["volume"], symbol)
                    self.assertEqual(dec(quote["prevclose"]), dec(bars[-2]["close"]), symbol)

    def test_history_is_identical_in_every_set(self) -> None:
        for symbol in UNDERLYINGS:
            rel = "history__%s__%s__%s.json" % ((symbol,) + HISTORY_RANGE)
            reference = read_json(os.path.join(set_dir("regular"), rel))
            for name in SETS:
                self.assertEqual(reference, read_json(os.path.join(set_dir(name), rel)), "%s %s" % (name, symbol))

    def test_holiday_set_carries_the_expiration_day_closes(self) -> None:
        for symbol, expected_close in (("WMT", Decimal("104.86")), ("NVDA", Decimal("153.40"))):
            payload = read_json(os.path.join(set_dir("holiday"), "history__%s__2026-11-20__2026-11-20.json" % symbol))
            bar = payload["history"]["day"]
            self.assertIsInstance(bar, dict)  # a single day comes back as an object, not a list
            self.assertEqual("2026-11-20", bar["date"])
            self.assertEqual(expected_close, dec(bar["close"]))

    def test_calendar_2026_11(self) -> None:
        for name in SETS:
            payload = read_json(os.path.join(set_dir(name), "calendar__2026-11.json"))["calendar"]
            self.assertEqual(11, payload["month"])
            self.assertEqual(2026, payload["year"])
            days = payload["days"]["day"]
            self.assertEqual(30, len(days))
            for index, day in enumerate(days):
                d = date(2026, 11, index + 1)
                self.assertEqual(d.isoformat(), day["date"])
                trading = mc.is_trading_day(d)
                self.assertEqual("open" if trading else "closed", day["status"], day["date"])
                if trading:
                    self.assertEqual({"start": "07:00", "end": "09:24"}, day["premarket"], day["date"])
                    self.assertEqual("09:30", day["open"]["start"], day["date"])
                    close = mc.session_close_et(d).strftime("%H:%M")
                    self.assertEqual(close, day["open"]["end"], day["date"])
                    self.assertEqual(close, day["postmarket"]["start"], day["date"])
                    self.assertIn("Market is open", day["description"])
                else:
                    self.assertEqual(["date", "status", "description"], list(day), day["date"])
                    self.assertEqual("Market is closed", day["description"])
            self.assertEqual("closed", days[25]["status"])  # 2026-11-26 Thanksgiving
            self.assertEqual("13:00", days[26]["open"]["end"])  # 2026-11-27 early close


class ReadmeTest(unittest.TestCase):
    def test_readme_states_every_instant_and_the_naming_rule(self) -> None:
        text = read_text(README)
        for name in SETS:
            facts = SET_FACTS[name]
            self.assertIn("`%s`" % name, text)
            self.assertIn(str(epoch_ms(facts["stamp"])), text, name)
            self.assertIn(str(epoch_s(facts["now"])), text, name)
            self.assertIn(facts["now"].strftime("%Y-%m-%dT%H:%M:%SZ"), text, name)
            self.assertIn(facts["stamp_et"], text, name)
        for needle in ("quotes__", "chains__", "expirations__", "strikes__", "history__", "clock.json", "calendar__",
                       "sorted", "upper", "fixtures.py", "FairGradient", "synthetic", "PR 4"):
            self.assertIn(needle, text, needle)
        self.assertIn("1762", text) if "1762" in text else None  # no requirement; keeps the linter quiet
        self.assertNotIn("Bearer", text)

    def test_readme_names_every_file_kind_present(self) -> None:
        text = read_text(README)
        for path in walk_files(TRADIER):
            rel = os.path.relpath(path, TRADIER).replace(os.sep, "/")
            if rel in ("README.md", "_generate.py"):
                continue
            base = os.path.basename(rel)
            kind = base.split("__")[0] if "__" in base else base
            self.assertIn(kind, text, rel)


if __name__ == "__main__":
    unittest.main()
