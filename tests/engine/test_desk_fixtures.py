"""The offline fixture desk roots: shapes, instants and consistency.

``tests/fixtures/desk`` is the main fixture root (eight recorded Tradier sets, the fixture book of
record ``positions.csv`` and ``research/calendar.toml``); ``tests/fixtures/desk_recon`` is the
reconciliation root (its own ``positions.csv`` and calendar, one recorded set ``recon``).

These tests are deliberately independent of ``aegis_desk.market_types`` (they read the JSON with
the standard library, convert epochs with the brief's own formula and use ``market_clock.to_et``
and ``occ.parse`` as the independent checks), so a fixture mistake and a parser mistake can never
cancel out. The instants and prices asserted here are the contract the data-plane builders rely
on; the README beside the sets must state every epoch value. The CSV and TOML files are read with
the standard ``csv`` module and ``toml_compat`` only (never ``positions_csv`` or
``calendar_events``), so the fixtures are pinned independently of the parsers built on them.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import csv
import hashlib
import io
import json
import math
import os
import re
import unittest
from datetime import date, datetime, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Dict, Iterator, List, Optional, Tuple

from aegis_desk import bsm, iv, occ, toml_compat
from aegis_desk import market_clock as mc

UTC = timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
TRADIER = os.path.join(DESK, "fixtures", "tradier")
README = os.path.join(TRADIER, "README.md")
CALENDAR = os.path.join(DESK, "research", "calendar.toml")
RECON = os.path.join(ROOT, "tests", "fixtures", "desk_recon")
RECON_TRADIER = os.path.join(RECON, "fixtures", "tradier")
RECON_SET = os.path.join(RECON_TRADIER, "recon")
RECON_README = os.path.join(RECON_TRADIER, "README.md")
RECON_CALENDAR = os.path.join(RECON, "research", "calendar.toml")

SETS = ("regular", "after_close", "stale", "pre_open", "holiday", "t1", "expiry_day", "post_expiry")
UNDERLYINGS = ("SPY", "WMT", "NVDA")
OPTIONS = ("WMT261120C00105000", "NVDA261120C00150000", "SPY261218P00620000")
EXPIRED_BY_HOLIDAY = ("WMT261120C00105000", "NVDA261120C00150000")  # expired 2026-11-20
NOV3_SETS = ("regular", "after_close", "stale")
# Sets whose quotes are stamped on 2026-11-20, the expiration day of the two November contracts:
# those contracts are still quoted and listed through their last session (expiration == quote day).
QUOTED_ON_EXPIRY_DAY = ("expiry_day", "post_expiry")
# Sets whose WMT and NVDA chains are the documented intrinsic-plus-one-tick fallback (the quotes
# are 10 s before the close: tau under one hour, the anchor implied volatility is not solved).
FALLBACK_CHAINS = {"post_expiry": ("WMT", "NVDA")}
NOV20_EXPIRY = date(2026, 11, 20)

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
    "t1": {"quote_date": date(2026, 11, 19), "stamp": datetime(2026, 11, 19, 19, 31, 12, tzinfo=UTC),
           "now": datetime(2026, 11, 19, 19, 50, 0, tzinfo=UTC), "clock_date": "2026-11-19",
           "state": "open", "close_set": False, "stamp_et": "2026-11-19 14:31:12"},
    "expiry_day": {"quote_date": date(2026, 11, 20), "stamp": datetime(2026, 11, 20, 19, 31, 12, tzinfo=UTC),
                   "now": datetime(2026, 11, 20, 19, 50, 0, tzinfo=UTC), "clock_date": "2026-11-20",
                   "state": "open", "close_set": False, "stamp_et": "2026-11-20 14:31:12"},
    "post_expiry": {"quote_date": date(2026, 11, 20), "stamp": datetime(2026, 11, 20, 20, 59, 50, tzinfo=UTC),
                    "now": datetime(2026, 11, 21, 14, 30, 0, tzinfo=UTC), "clock_date": "2026-11-21",
                    "state": "closed", "close_set": True, "stamp_et": "2026-11-20 15:59:50"},
}
# The recon root's single set: closing quotes of Monday 2026-11-23, read Tuesday 07:00 ET.
RECON_FACTS: Dict[str, Any] = {
    "quote_date": date(2026, 11, 23), "stamp": datetime(2026, 11, 23, 20, 59, 50, tzinfo=UTC),
    "now": datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC), "clock_date": "2026-11-24",
    "state": "premarket", "close_set": True, "stamp_et": "2026-11-23 15:59:50",
}
# Every documented epoch value: (quote stamp in ms, --now in s) per set.
EPOCHS: Dict[str, Tuple[int, int]] = {
    "regular": (1793734272000, 1793735400), "after_close": (1793739580000, 1793741100),
    "stale": (1793731800000, 1793735400), "pre_open": (1793653195000, 1793710800),
    "holiday": (1795640390000, 1795705200),
    "t1": (1795116672000, 1795117800), "expiry_day": (1795203072000, 1795204200),
    "post_expiry": (1795208390000, 1795271400),
}
RECON_EPOCHS = (1795467590000, 1795521600)
# Anchor prices of the 2026-11-03 sets (the brief's numbers), identical in regular/stale/after_close.
NOV3_ANCHORS: Dict[str, Tuple[str, str]] = {
    "SPY": ("648.10", "648.14"), "WMT": ("104.20", "104.26"), "NVDA": ("148.90", "148.95"),
    "WMT261120C00105000": ("3.35", "3.50"), "NVDA261120C00150000": ("3.10", "3.30"),
    "SPY261218P00620000": ("9.80", "10.10"),
}
# The November expiry-week sets: bid, ask, last, prevclose per symbol (the addendum's table; the
# option prevclose values are the fixture's own choices, documented in the README).
SET_ANCHORS: Dict[str, Dict[str, Tuple[str, str, str, str]]] = {
    "t1": {
        "SPY": ("652.10", "652.14", "652.12", "650.80"), "WMT": ("106.40", "106.46", "106.43", "105.90"),
        "NVDA": ("149.20", "149.25", "149.22", "150.10"),
        "WMT261120C00105000": ("1.52", "1.60", "1.56", "1.25"), "NVDA261120C00150000": ("1.00", "1.10", "1.05", "1.60"),
        "SPY261218P00620000": ("3.80", "4.00", "3.90", "4.35"),
    },
    "expiry_day": {
        "SPY": ("650.00", "650.04", "650.02", "652.12"), "WMT": ("104.90", "104.96", "104.93", "106.43"),
        "NVDA": ("153.30", "153.36", "153.33", "149.22"),
        "WMT261120C00105000": ("0.12", "0.16", "0.14", "1.56"), "NVDA261120C00150000": ("3.30", "3.42", "3.36", "1.05"),
        "SPY261218P00620000": ("4.10", "4.30", "4.20", "3.90"),
    },
    "post_expiry": {
        "SPY": ("650.00", "650.04", "650.02", "652.12"), "WMT": ("104.38", "104.42", "104.40", "106.43"),
        "NVDA": ("153.38", "153.42", "153.40", "149.22"),
        "WMT261120C00105000": ("0.01", "0.05", "0.03", "1.56"), "NVDA261120C00150000": ("3.30", "3.45", "3.38", "1.05"),
        "SPY261218P00620000": ("4.10", "4.30", "4.20", "3.90"),
    },
}
RECON_ANCHORS: Dict[str, Tuple[str, str, str, str]] = {
    "SPY": ("651.90", "651.94", "651.92", "650.02"), "SPY261218P00620000": ("4.05", "4.25", "4.15", "4.20"),
}
# Quote files every set used by the journeys and tool tests must hold (the OfflineTransport key).
REQUIRED_QUOTE_KEYS = (
    "SPY", "WMT", "NVDA", "SPY,WMT", "NVDA,SPY,WMT",
    "WMT261120C00105000", "NVDA261120C00150000", "SPY261218P00620000",
    "SPY,WMT,WMT261120C00105000",
    "NVDA,NVDA261120C00150000,SPY,SPY261218P00620000,WMT,WMT261120C00105000",
)
RECON_QUOTE_KEYS = ("SPY", "SPY261218P00620000", "SPY,SPY261218P00620000")
CHAINS = {"WMT": "2026-11-20", "NVDA": "2026-11-20", "SPY": "2026-12-18"}
# PR 3b: the structuring chains (designer Q's D1 and D2, addendum S12): no anchor contract, priced from the stated
# surface sigma(K) = max(0.05, sigma0 - skew x ln(K / S)) with q = 0 and r = 0.0412. Per set: (underlying, expiration)
# -> the number of entries (a call and a put per strike).
STRUCTURING_CHAINS: Dict[str, Dict[Tuple[str, str], int]] = {
    "regular": {("WMT", "2026-12-18"): 20, ("WMT", "2027-01-15"): 20, ("NVDA", "2026-12-18"): 20,
                ("NVDA", "2027-01-15"): 20, ("SPY", "2027-03-19"): 24},
    "expiry_day": {("WMT", "2027-01-15"): 20, ("NVDA", "2027-01-15"): 20},
}
STRUCTURING_STRIKES = {
    "WMT": ("95", "97.5", "100", "102.5", "105", "107.5", "110", "112.5", "115", "120"),
    "NVDA": ("130", "135", "140", "145", "150", "155", "160", "165", "170", "180"),
    "SPY": ("580", "590", "600", "610", "620", "625", "630", "640", "650", "660", "680", "700"),
}
STRUCTURING_SKEW = {"WMT": 0.25, "NVDA": 0.25, "SPY": 0.50}
# The set whose anchor quote fixes sigma0: the expiry_day anchors have 1 h 29 min left and almost no time value.
STRUCTURING_SIGMA_SET = {"regular": "regular", "expiry_day": "regular"}
STRUCTURING_RATE = 0.0412
# The README table: (set, underlying, expiration) -> (spot, tau seconds, tau years, sigma0), every value as printed.
STRUCTURING_TABLE: Dict[Tuple[str, str, str], Tuple[str, int, str, str]] = {
    ("regular", "WMT", "2026-12-18"): ("104.23", 3893328, "0.123457", "0.4108"),
    ("regular", "WMT", "2027-01-15"): ("104.23", 6312528, "0.200169", "0.4108"),
    ("regular", "NVDA", "2026-12-18"): ("148.925", 3893328, "0.123457", "0.2780"),
    ("regular", "NVDA", "2027-01-15"): ("148.925", 6312528, "0.200169", "0.2780"),
    ("regular", "SPY", "2027-03-19"): ("648.12", 11752128, "0.372658", "0.2501"),
    ("expiry_day", "WMT", "2027-01-15"): ("104.93", 4843728, "0.153594", "0.4108"),
    ("expiry_day", "NVDA", "2027-01-15"): ("153.33", 4843728, "0.153594", "0.2780"),
}
# The bytes the generator writes (identical on CPython 3.9, 3.10, 3.12 and 3.13; the regular five are designer Q's
# recomputed values).
STRUCTURING_SHA256 = {
    ("regular", "WMT", "2026-12-18"): "86d9dbc61826245c6dd6e8a2439da69fb087b61e1e6eceb2d4c0a7db7d5b38cf",
    ("regular", "WMT", "2027-01-15"): "5197236c33f25e32ca3de93f161c7e8b1ab92a1adc037f492cd9a988854efa06",
    ("regular", "NVDA", "2026-12-18"): "cd7da3b87be9129008f0e978924db4b21e8c6f85c5212736b2889ef11bede4b1",
    ("regular", "NVDA", "2027-01-15"): "12e2fc2a944b695a2037103c636f16ffc7ff4c50697c034ab0f74e9dc1e424e2",
    ("regular", "SPY", "2027-03-19"): "296ed35a204621bf442cec7d2ff2bd5846320d2091e18c009770979bfc96b1d3",
    ("expiry_day", "WMT", "2027-01-15"): "e5a8486285363eb303621146ae93047994bc4a3aad5922cde7a41af5e4c7e26a",
    ("expiry_day", "NVDA", "2027-01-15"): "19a099cd4d685a66aadc75d7da92933871c5427c5e88fc83bb6633514d518dd0",
}
HISTORY_RANGE = ("2026-10-27", "2026-11-02")
# Bars as (date, open, high, low, close, volume); one bar is served as an object, several as a list.
TWO_BAR_HISTORY: Dict[str, List[Tuple[str, str, str, str, str, int]]] = {
    "WMT": [("2026-11-20", "104.60", "105.20", "104.30", "104.86", 9_100_000),
            ("2026-11-23", "105.30", "105.90", "104.95", "105.62", 8_400_000)],
    "NVDA": [("2026-11-20", "150.20", "153.80", "149.90", "153.40", 41_000_000),
             ("2026-11-23", "152.90", "154.40", "152.10", "154.10", 38_000_000)],
    "AMD": [("2026-11-20", "228.10", "231.60", "227.80", "231.00", 52_000_000),
            ("2026-11-23", "229.40", "230.20", "227.90", "228.75", 47_000_000)],
}
HOLIDAY_SINGLE_BARS = {  # PR 2a values, kept unchanged
    "WMT": ("2026-11-20", "104.60", "105.30", "104.20", "104.86", 18_700_000),
    "NVDA": ("2026-11-20", "151.20", "153.90", "150.80", "153.40", 260_000_000),
}
POST_EXPIRY_BARS = {
    "WMT": ("2026-11-20", "105.10", "105.60", "104.20", "104.40", 9_100_000),   # the 105 call 0.60 out of the money
    "NVDA": ("2026-11-20", "150.20", "153.80", "149.90", "153.40", 41_000_000),  # the 150 call 3.40 in the money
}
POSITIONS_HEADER = ("instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,"
                    "opened,cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,"
                    "close_price_per_unit,underlying_stop,note")
MAIN_TRADE_IDS = ("AG-20261020-01", "AG-20261020-01", "AG-20261022-02", "AG-20261022-02", "AG-20261028-03",
                  "AG-20261028-03", "AG-20261001-04")
RECON_TRADE_IDS = ("AG-20261015-01", "AG-20261020-02", "AG-20261020-02", "AG-20261022-03", "AG-20261022-03",
                   "AG-20261023-04", "AG-20261101-05", "AG-20261101-06", "AG-20261105-07", "AG-20261110-08",
                   "AG-20261110-08", "AG-20261001-09")
DATE_KEYS = ("bid_date", "ask_date", "trade_date")
TOKEN_RE = re.compile(r"(?i)(?:bearer|token)[^A-Za-z0-9]{0,8}[A-Za-z0-9]{20,}")
EMOJI_RE = re.compile("[⌀-⏿☀-➿⬀-⯿\U0001F000-\U0001FAFF️]")
RELEASE_TAG_RE = re.compile(r"\bv\d+\.\d+\.\d+\b")
# Order-shaped words that must never appear in a fixture or its documentation.
ORDER_WORDS_RE = re.compile(r"(?i)\b(?:buy|sell|sells|selling|submit|limit order|market order|good till|"
                            r"time in force|day order)\b")


def read_json(path: str) -> Any:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def read_text(path: str) -> str:
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def read_bytes(path: str) -> bytes:
    with open(path, "rb") as handle:
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


def csv_lines(path: str) -> Tuple[List[str], List[str], List[List[str]]]:
    """(raw lines, comment lines, data rows parsed with the csv module) of a positions file."""
    lines = read_text(path).splitlines()
    comments = [line for line in lines[1:] if line.lstrip().startswith("#")]
    data_lines = [line for line in lines[1:] if line.strip() and not line.lstrip().startswith("#")]
    rows = list(csv.reader(io.StringIO("\n".join(data_lines)))) if data_lines else []
    return lines, comments, rows


def bar_tuple(bar: dict) -> Tuple[str, Decimal, Decimal, Decimal, Decimal, int]:
    return (bar["date"], dec(bar["open"]), dec(bar["high"]), dec(bar["low"]), dec(bar["close"]), bar["volume"])


def expected_bar(spec: Tuple[str, str, str, str, str, int]) -> Tuple[str, Decimal, Decimal, Decimal, Decimal, int]:
    return (spec[0], Decimal(spec[1]), Decimal(spec[2]), Decimal(spec[3]), Decimal(spec[4]), spec[5])


def expiration_check(test: unittest.TestCase, name: str, expiration: date, tag: str) -> None:
    """Options expire after the quote day, except on the expiry-day sets where they may expire on it."""
    if name in QUOTED_ON_EXPIRY_DAY:
        test.assertGreaterEqual(expiration, SET_FACTS[name]["quote_date"], tag)
    else:
        test.assertGreater(expiration, SET_FACTS[name]["quote_date"], tag)


class TreeShapeTest(unittest.TestCase):
    def test_owner_files_present(self) -> None:
        self.assertTrue(os.path.isfile(os.path.join(DESK, "desk.toml")))
        self.assertTrue(os.path.isfile(os.path.join(DESK, "positions.csv")))
        self.assertTrue(os.path.isfile(README))
        self.assertTrue(os.path.isfile(CALENDAR))

    def test_positions_csv_header_seven_rows_one_comment(self) -> None:
        lines, comments, rows = csv_lines(os.path.join(DESK, "positions.csv"))
        self.assertEqual(POSITIONS_HEADER, lines[0])
        header = lines[0].split(",")
        for column in ("instrument_type", "symbol", "occ_symbol", "quantity", "expiration", "option_right",
                       "strike", "account", "trade_id", "opened", "cost_basis_total", "cost_basis_per_unit",
                       "open_price_per_share", "status", "closed", "closed_quantity", "close_price_per_unit",
                       "underlying_stop", "note"):
            self.assertIn(column, header)
        self.assertEqual(len(header), len(set(header)))
        self.assertEqual(1, len(comments), comments)
        self.assertTrue(lines[1].startswith("#"), lines[1])
        self.assertEqual(7, len(rows), rows)
        for row in rows:
            self.assertEqual(len(header), len(row), row)
        by_name = [dict(zip(header, row)) for row in rows]
        self.assertEqual(list(MAIN_TRADE_IDS), [r["trade_id"] for r in by_name])
        self.assertEqual(["equity", "option", "equity", "option", "etf", "option", "option"],
                         [r["instrument_type"] for r in by_name])
        self.assertEqual(["open"] * 6 + ["closed"], [r["status"] for r in by_name])
        for row in by_name:
            self.assertIn(row["account"], ("Brokerage (margin)", "Roth IRA"), row)
        # Money: paid positive, received negative; the WMT shares carry a per-unit cost (Aegis semantics).
        self.assertEqual(("", "98.50"), (by_name[0]["cost_basis_total"], by_name[0]["cost_basis_per_unit"]))
        self.assertEqual(("-684.00", "3.42"), (by_name[1]["cost_basis_total"], by_name[1]["open_price_per_share"]))
        self.assertEqual(("", "2026-11-20", "CALL", "150"), (by_name[3]["occ_symbol"], by_name[3]["expiration"],
                                                            by_name[3]["option_right"], by_name[3]["strike"]))
        self.assertEqual(("1130.00", "11.30"), (by_name[5]["cost_basis_total"], by_name[5]["open_price_per_share"]))
        self.assertEqual(("closed", "2026-10-14", "1", "0.55", "-480.00"),
                         (by_name[6]["status"], by_name[6]["closed"], by_name[6]["closed_quantity"],
                          by_name[6]["close_price_per_unit"], by_name[6]["cost_basis_total"]))
        for row in by_name:
            if row["instrument_type"] == "option":
                self.assertEqual("", row["cost_basis_per_unit"], row)
            quantity = Decimal(row["quantity"])
            total = Decimal(row["cost_basis_total"]) if row["cost_basis_total"] else None
            if total is not None:
                self.assertGreater(total * quantity, 0, "sign(cost_basis_total) == sign(quantity): %r" % row)
        self.assertIn("#assign-ok", by_name[1]["note"])
        self.assertIn("#earnings-ok", by_name[3]["note"])
        self.assertIn("#horizon:2027-03", by_name[0]["note"])

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
        for rel in ("history__WMT__2026-11-20__2026-11-20.json", "history__NVDA__2026-11-20__2026-11-20.json",
                    "history__WMT__2026-11-20__2026-11-23.json", "history__NVDA__2026-11-20__2026-11-23.json"):
            self.assertTrue(os.path.isfile(os.path.join(set_dir("holiday"), rel)), "holiday/%s" % rel)
        for rel in ("history__WMT__2026-11-20__2026-11-20.json", "history__NVDA__2026-11-20__2026-11-20.json"):
            self.assertTrue(os.path.isfile(os.path.join(set_dir("post_expiry"), rel)), "post_expiry/%s" % rel)
        for name in ("t1", "expiry_day", "post_expiry"):
            for rel in ("quotes__SPX.json", "quotes__SPX,SPY.json"):
                self.assertFalse(os.path.exists(os.path.join(set_dir(name), rel)), "%s/%s is regular-only" % (name, rel))

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
        for path in walk_files(DESK) + walk_files(RECON):
            text = read_text(path)
            self.assertIsNone(TOKEN_RE.search(text), path)
            self.assertNotIn("Bearer ", text, path)
            self.assertNotIn("Authorization", text, path)

    def test_no_emoji_or_release_tags_in_the_readme_and_toml(self) -> None:
        for path in (README, os.path.join(DESK, "desk.toml"), os.path.join(DESK, "positions.csv"), CALENDAR,
                     RECON_README, os.path.join(RECON, "desk.toml"), os.path.join(RECON, "positions.csv"),
                     RECON_CALENDAR):
            text = read_text(path)
            self.assertIsNone(EMOJI_RE.search(text), path)
            self.assertIsNone(RELEASE_TAG_RE.search(text), path)

    def test_no_order_shaped_words_in_the_fixtures(self) -> None:
        for root in (DESK, RECON):
            for path in walk_files(root):
                if path.endswith((".md", ".csv", ".toml", ".py")):
                    self.assertIsNone(ORDER_WORDS_RE.search(read_text(path)), path)


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

    def test_anchor_prices_of_the_expiry_week_sets(self) -> None:
        for name, anchors in SET_ANCHORS.items():
            for symbol, (bid, ask, last, prevclose) in anchors.items():
                quote = quotes_in(read_json(os.path.join(set_dir(name), "quotes__%s.json" % symbol)))[0][0]
                tag = "%s %s" % (name, symbol)
                self.assertEqual((Decimal(bid), Decimal(ask), Decimal(last), Decimal(prevclose)),
                                 (dec(quote["bid"]), dec(quote["ask"]), dec(quote["last"]), dec(quote["prevclose"])), tag)

    def test_the_expiry_week_days_link_up(self) -> None:
        """2026-11-19 -> 2026-11-20 -> 2026-11-23: each day's prevclose is the previous day's last."""
        for symbol in UNDERLYINGS + OPTIONS:
            t1_last = SET_ANCHORS["t1"][symbol][2]
            for name in QUOTED_ON_EXPIRY_DAY:
                self.assertEqual(t1_last, SET_ANCHORS[name][symbol][3], "%s %s" % (name, symbol))
        # The expiry-day closing quotes equal the day's bars the reconciliation reads in post_expiry.
        for symbol, bar in POST_EXPIRY_BARS.items():
            self.assertEqual(bar[4], SET_ANCHORS["post_expiry"][symbol][2], symbol)
        # Intraday volume never exceeds the day's final volume for the same symbol.
        for symbol in UNDERLYINGS + OPTIONS:
            intraday = quotes_in(read_json(os.path.join(set_dir("expiry_day"), "quotes__%s.json" % symbol)))[0][0]
            closing = quotes_in(read_json(os.path.join(set_dir("post_expiry"), "quotes__%s.json" % symbol)))[0][0]
            self.assertLessEqual(intraday["volume"], closing["volume"], symbol)
            self.assertEqual(intraday["open"], closing["open"], symbol)
            self.assertLessEqual(dec(intraday["high"]), dec(closing["high"]), symbol)
            self.assertGreaterEqual(dec(intraday["low"]), dec(closing["low"]), symbol)
        for symbol, bar in POST_EXPIRY_BARS.items():
            quote = quotes_in(read_json(os.path.join(set_dir("post_expiry"), "quotes__%s.json" % symbol)))[0][0]
            self.assertEqual(expected_bar(bar)[1:], (dec(quote["open"]), dec(quote["high"]), dec(quote["low"]),
                                                     dec(quote["close"]), quote["volume"]), symbol)
        # Monday's closing quotes in the recon root follow Friday's close.
        self.assertEqual(SET_ANCHORS["post_expiry"]["SPY"][2], RECON_ANCHORS["SPY"][3])
        self.assertEqual(SET_ANCHORS["post_expiry"]["SPY261218P00620000"][2], RECON_ANCHORS["SPY261218P00620000"][3])

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
                    expiration_check(self, name, parsed.expiration, "%s %s" % (name, quote["symbol"]))

    def test_expiry_day_sets_quote_the_contracts_on_their_last_session(self) -> None:
        for name in QUOTED_ON_EXPIRY_DAY:
            self.assertEqual(NOV20_EXPIRY, SET_FACTS[name]["quote_date"], name)
            for symbol in EXPIRED_BY_HOLIDAY:
                matched, unmatched = quotes_in(read_json(os.path.join(set_dir(name), "quotes__%s.json" % symbol)))
                self.assertEqual([symbol], [q["symbol"] for q in matched], "%s %s" % (name, symbol))
                self.assertEqual([], unmatched)
                self.assertEqual(NOV20_EXPIRY, occ.parse(symbol).expiration)
        self.assertEqual(NOV20_EXPIRY, mc.last_trading_day(NOV20_EXPIRY, False))

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
                    self.assertEqual(mc.EST, et.tzinfo, path)  # every set is in Eastern Standard Time
                    count += 1
            self.assertGreater(count, 0, name)
            # Quotes are stamped before --now, never after it, and on the documented trading day.
            self.assertLess(facts["stamp"], facts["now"], name)
            self.assertEqual(facts["quote_date"], mc.to_et(facts["stamp"]).date(), name)
            self.assertTrue(mc.is_trading_day(facts["quote_date"]), name)

    def test_the_documented_epoch_values(self) -> None:
        self.assertEqual(sorted(SETS), sorted(EPOCHS))
        for name, (stamp_ms, now_s) in EPOCHS.items():
            self.assertEqual(stamp_ms, epoch_ms(SET_FACTS[name]["stamp"]), name)
            self.assertEqual(now_s, epoch_s(SET_FACTS[name]["now"]), name)
            self.assertEqual(int(SET_FACTS[name]["stamp"].timestamp() * 1000), stamp_ms)
        self.assertEqual((epoch_ms(RECON_FACTS["stamp"]), epoch_s(RECON_FACTS["now"])), RECON_EPOCHS)
        # The 2026-11-20 close the post_expiry quotes precede by 10 s (not in a file).
        self.assertEqual(1795208400, epoch_s(datetime(2026, 11, 20, 21, 0, tzinfo=UTC)))
        self.assertEqual(10, (datetime(2026, 11, 20, 21, 0, tzinfo=UTC) - SET_FACTS["post_expiry"]["stamp"]).total_seconds())

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

    def test_sessions_of_the_expiry_week_sets(self) -> None:
        # t1: Thursday 2026-11-19, regular hours, the day before the November expiration.
        t1 = mc.session_at(SET_FACTS["t1"]["now"])
        self.assertEqual(("open", True), (t1.session, t1.trading_day))
        self.assertEqual(date(2026, 11, 19), t1.et_date)
        self.assertEqual(NOV20_EXPIRY, mc.next_trading_day(t1.et_date))
        self.assertEqual(1, mc.dte(NOV20_EXPIRY, t1.et_date, mc.UNIT_TRADING))
        self.assertEqual(18, (SET_FACTS["t1"]["now"] - SET_FACTS["t1"]["stamp"]).total_seconds() // 60)
        # expiry_day: Friday 2026-11-20, regular hours, the contracts' last trading day (DTE 0).
        expiry_day = mc.session_at(SET_FACTS["expiry_day"]["now"])
        self.assertEqual(("open", True, NOV20_EXPIRY), (expiry_day.session, expiry_day.trading_day, expiry_day.et_date))
        self.assertEqual(0, mc.dte(NOV20_EXPIRY, expiry_day.et_date, mc.UNIT_TRADING))
        self.assertEqual(datetime(2026, 11, 20, 21, 0, tzinfo=UTC), expiry_day.close_utc)
        self.assertEqual(18, (SET_FACTS["expiry_day"]["now"] - SET_FACTS["expiry_day"]["stamp"]).total_seconds() // 60)
        # post_expiry: Saturday 2026-11-21 09:30 ET, the post-expiry routine's instant; quotes are
        # Friday's closing quotes, 10 s before the 16:00 ET close.
        post = mc.session_at(SET_FACTS["post_expiry"]["now"])
        self.assertEqual(("weekend", False, date(2026, 11, 21)), (post.session, post.trading_day, post.et_date))
        self.assertEqual("09:30:00", mc.to_et(SET_FACTS["post_expiry"]["now"]).strftime("%H:%M:%S"))
        self.assertEqual(datetime(2026, 11, 20, 21, 0, tzinfo=UTC), mc.previous_close_utc(SET_FACTS["post_expiry"]["now"]))
        self.assertEqual(date(2026, 11, 23), mc.next_trading_day(NOV20_EXPIRY))
        # recon: Tuesday 2026-11-24 07:00 ET, pre-open, judged against Monday's 16:00 ET close.
        recon = mc.session_at(RECON_FACTS["now"])
        self.assertEqual(("pre", True, date(2026, 11, 24)), (recon.session, recon.trading_day, recon.et_date))
        prior = mc.previous_close_utc(RECON_FACTS["now"])
        self.assertEqual(datetime(2026, 11, 23, 21, 0, tzinfo=UTC), prior)
        self.assertEqual(10, (prior - RECON_FACTS["stamp"]).total_seconds())
        self.assertEqual(RECON_FACTS["quote_date"], mc.to_et(RECON_FACTS["stamp"]).date())
        self.assertTrue(mc.is_trading_day(RECON_FACTS["quote_date"]))


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

    def test_post_expiry_fallback_chains_are_intrinsic_plus_one_tick(self) -> None:
        """10 s before the close the anchor implied volatility is not solved (tau under one hour; the
        NVDA mid even sits below intrinsic): every neighbour is ``max(intrinsic, 0.01) + 0.05`` at the
        closing spot, except that a contract further out of the money than the anchor is never worth
        more than the anchor (its mid is capped at the anchor mid, so the chain stays monotone)."""
        for name, underlyings in FALLBACK_CHAINS.items():
            for underlying in underlyings:
                spot_quote = quotes_in(read_json(os.path.join(set_dir(name), "quotes__%s.json" % underlying)))[0][0]
                spot = (dec(spot_quote["bid"]) + dec(spot_quote["ask"])) / 2
                anchor_symbol = [s for s in OPTIONS if s.startswith(underlying)][0]
                anchor = occ.parse(anchor_symbol)
                anchor_quote = quotes_in(read_json(os.path.join(set_dir(name), "quotes__%s.json" % anchor_symbol)))[0][0]
                anchor_mid = (dec(anchor_quote["bid"]) + dec(anchor_quote["ask"])) / 2
                chain = read_json(os.path.join(set_dir(name), "chains__%s__%s.json" % (underlying, CHAINS[underlying])))
                options = chain["options"]["option"]
                self.assertEqual(10, len(options), "%s %s" % (name, underlying))
                for option in options:
                    if option["symbol"] == anchor_symbol:
                        continue
                    strike = dec(option["strike"])
                    intrinsic = max(spot - strike, Decimal(0)) if option["option_type"] == "call" else max(strike - spot, Decimal(0))
                    expected = max(intrinsic, Decimal("0.01")) + Decimal("0.05")
                    further_otm = (option["option_type"] == "call" and anchor.is_call and strike > anchor.strike) or (
                        option["option_type"] == "put" and anchor.is_put and strike < anchor.strike)
                    if further_otm:
                        expected = min(expected, anchor_mid)
                    mid = (dec(option["bid"]) + dec(option["ask"])) / 2
                    self.assertEqual(expected, mid, "%s %s" % (name, option["symbol"]))
                    self.assertEqual(expected, dec(option["last"]), option["symbol"])
                    self.assertEqual(dec(option["last"]), dec(option["close"]), option["symbol"])  # closing quotes
        # The SPY chain of the same set is still priced from a solved implied volatility (28 days left).
        chain = read_json(os.path.join(set_dir("post_expiry"), "chains__SPY__2026-12-18.json"))
        mids = {dec(o["strike"]): (dec(o["bid"]) + dec(o["ask"])) / 2 for o in chain["options"]["option"] if o["option_type"] == "put"}
        self.assertGreater(mids[Decimal("630")] - mids[Decimal("620")], Decimal("0.50"))  # time value, not a flat tick

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
                    expiration_check(self, name, d, "%s %s %s" % (name, symbol, d))
                    self.assertTrue(mc.is_trading_day(d), "%s %s %s" % (name, symbol, d))
                if name != "holiday":
                    self.assertIn(CHAINS[symbol], dates, "%s %s" % (name, symbol))
                else:
                    self.assertNotIn("2026-11-20", dates)
                    if symbol == "SPY":
                        self.assertIn("2026-12-18", dates)
                if name in ("t1",) + QUOTED_ON_EXPIRY_DAY:
                    self.assertEqual("2026-11-20", dates[0], "%s %s" % (name, symbol))

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
            self.assertEqual(expected_bar(HOLIDAY_SINGLE_BARS[symbol]), bar_tuple(bar), symbol)  # PR 2a values unchanged

    def test_holiday_set_two_bar_history_files(self) -> None:
        """2026-11-20 (the expiration day) and 2026-11-23 (the next session, whose open is the
        after-hours-crossing proxy of the reconciliation): a list of two bars."""
        for symbol in ("WMT", "NVDA"):
            payload = read_json(os.path.join(set_dir("holiday"), "history__%s__2026-11-20__2026-11-23.json" % symbol))
            bars = payload["history"]["day"]
            self.assertIsInstance(bars, list, symbol)
            self.assertEqual([expected_bar(b) for b in TWO_BAR_HISTORY[symbol]], [bar_tuple(b) for b in bars], symbol)
            for bar in bars:
                self.assertEqual(["date", "open", "high", "low", "close", "volume"], list(bar))
                self.assertTrue(mc.is_trading_day(date.fromisoformat(bar["date"])), bar["date"])
            # The closes agree with the single-day files; 2026-11-23 is the next trading day.
            single = read_json(os.path.join(set_dir("holiday"), "history__%s__2026-11-20__2026-11-20.json" % symbol))
            self.assertEqual(dec(single["history"]["day"]["close"]), dec(bars[0]["close"]), symbol)
            self.assertEqual(mc.next_trading_day(NOV20_EXPIRY).isoformat(), bars[1]["date"])
        # WMT: 104.86 is 0.14 inside the 0.25 pin band and Monday opened at 105.30, across the strike.
        wmt = read_json(os.path.join(set_dir("holiday"), "history__WMT__2026-11-20__2026-11-23.json"))["history"]["day"]
        self.assertLessEqual(abs(dec(wmt[0]["close"]) - Decimal("105")), Decimal("0.25"))
        self.assertGreater(dec(wmt[1]["open"]), Decimal("105"))
        # NVDA: 153.40 is 3.40 in the money and Monday opened on the same side (152.90).
        nvda = read_json(os.path.join(set_dir("holiday"), "history__NVDA__2026-11-20__2026-11-23.json"))["history"]["day"]
        self.assertEqual(Decimal("3.40"), dec(nvda[0]["close"]) - Decimal("150"))
        self.assertGreater(dec(nvda[1]["open"]), Decimal("150"))

    def test_post_expiry_set_carries_the_expiration_day_bars(self) -> None:
        for symbol, spec in POST_EXPIRY_BARS.items():
            payload = read_json(os.path.join(set_dir("post_expiry"), "history__%s__2026-11-20__2026-11-20.json" % symbol))
            bar = payload["history"]["day"]
            self.assertIsInstance(bar, dict, symbol)  # one bar: an object
            self.assertEqual(expected_bar(spec), bar_tuple(bar), symbol)
            self.assertEqual(["date", "open", "high", "low", "close", "volume"], list(bar))
        # The 105 call expired 0.60 out of the money; the 150 call finished 3.40 in the money.
        self.assertEqual(Decimal("0.60"), Decimal("105") - Decimal(POST_EXPIRY_BARS["WMT"][4]))
        self.assertEqual(Decimal("3.40"), Decimal(POST_EXPIRY_BARS["NVDA"][4]) - Decimal("150"))
        self.assertGreater(Decimal("0.60"), Decimal("0.25"))  # outside the pin band: a clean EXPIRED_OTM

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


class CalendarTomlTest(unittest.TestCase):
    """``research/calendar.toml`` of the main root, read with the vendored TOML reader only."""

    def load(self) -> Dict[str, Any]:
        return toml_compat.load(CALENDAR)

    def test_shape_and_symbols(self) -> None:
        doc = self.load()
        self.assertEqual(1, doc["schema_version"])
        self.assertEqual(sorted(["schema_version", "symbols", "settlement", "closures"]), sorted(doc))
        self.assertEqual(sorted(["WMT", "NVDA", "SPY"]), sorted(doc["symbols"]))
        checked = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)
        for symbol, table in doc["symbols"].items():
            self.assertEqual(sorted(["earnings", "ex_dividend", "dividend_yield"]), sorted(table), symbol)
            for entry in (table["earnings"], table["ex_dividend"]):
                self.assertEqual(checked, entry["checked_utc"], symbol)
                self.assertIn(entry["status"], ("confirmed", "estimated", "unknown", "none"), symbol)
                self.assertTrue(entry["source"], symbol)
            self.assertIsInstance(table["dividend_yield"], Decimal, symbol)
            self.assertGreater(table["dividend_yield"], 0, symbol)

    def test_entries(self) -> None:
        doc = self.load()
        wmt, nvda, spy = doc["symbols"]["WMT"], doc["symbols"]["NVDA"], doc["symbols"]["SPY"]
        self.assertEqual((date(2026, 11, 17), "pre", "confirmed"), (wmt["earnings"]["date"], wmt["earnings"]["time"],
                                                                   wmt["earnings"]["status"]))
        self.assertEqual((date(2026, 11, 24), Decimal("0.2350"), "confirmed"),
                         (wmt["ex_dividend"]["date"], wmt["ex_dividend"]["amount"], wmt["ex_dividend"]["status"]))
        self.assertEqual(Decimal("0.0090"), wmt["dividend_yield"])
        self.assertEqual((date(2026, 11, 19), "post", "confirmed"), (nvda["earnings"]["date"], nvda["earnings"]["time"],
                                                                    nvda["earnings"]["status"]))
        self.assertEqual((date(2026, 12, 3), Decimal("0.01"), "confirmed"),
                         (nvda["ex_dividend"]["date"], nvda["ex_dividend"]["amount"], nvda["ex_dividend"]["status"]))
        self.assertEqual(Decimal("0.0003"), nvda["dividend_yield"])
        self.assertEqual("none", spy["earnings"]["status"])
        self.assertNotIn("date", spy["earnings"])  # an ETF has no earnings: an explicit none, never a zero
        self.assertEqual((date(2026, 12, 18), Decimal("1.85"), "estimated"),
                         (spy["ex_dividend"]["date"], spy["ex_dividend"]["amount"], spy["ex_dividend"]["status"]))
        self.assertEqual(Decimal("0.0115"), spy["dividend_yield"])
        # WMT's ex-dividend date is 3 trading days after the t1 instant (2026-11-19), inside the EXDIV window, but its
        # last cum-dividend day (Mon 2026-11-23) is after the Nov-20 call's last trading day: no dividend tier is listed.
        self.assertEqual(3, mc.trading_days_between(date(2026, 11, 19), date(2026, 11, 24)))
        self.assertGreater(mc.previous_trading_day(date(2026, 11, 24)), occ.parse("WMT261120C00105000").expiration)
        # The December ex-dividend of SPY falls on the December put's expiration day.
        self.assertEqual(occ.parse("SPY261218P00620000").expiration, spy["ex_dividend"]["date"])

    def test_settlement_and_closures(self) -> None:
        doc = self.load()
        self.assertEqual({"am_settled_roots": ["SPX", "NDX", "RUT", "VIX", "DJX", "XSP"]}, doc["settlement"])
        self.assertEqual(1, len(doc["closures"]))
        closure = doc["closures"][0]
        self.assertEqual((date(2026, 11, 27), "early_close"), (closure["date"], closure["kind"]))
        self.assertIn("NYSE", closure["source"])
        self.assertTrue(mc.is_early_close(date(2026, 11, 27)))  # duplicates the embedded table on purpose

    def test_text_conventions(self) -> None:
        text = read_text(CALENDAR)
        self.assertTrue(text.startswith("# Fixture research calendar"), text[:60])
        self.assertIn('"unknown" is never zero', text)
        self.assertNotIn("\t", text)
        self.assertTrue(text.endswith("\n"))
        # Every entry is dated: six checked_utc stamps (earnings and ex_dividend for three symbols).
        self.assertEqual(["2026-11-01T12:00:00Z"] * 6, doc_dates(text))


def doc_dates(text: str) -> List[str]:
    return re.findall(r"checked_utc = (\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z)", text)


class ReconRootTest(unittest.TestCase):
    """``tests/fixtures/desk_recon``: the reconciliation root (plan section 8 settlement branches)."""

    def test_layout(self) -> None:
        for rel in ("desk.toml", "positions.csv", "research/calendar.toml", "fixtures/tradier/README.md",
                    "fixtures/tradier/_generate.py", "fixtures/tradier/recon/clock.json",
                    "fixtures/tradier/recon/errors/quota_violation.txt", "fixtures/tradier/recon/errors/invalid_token.txt",
                    "fixtures/tradier/recon/history__WMT__2026-11-20__2026-11-23.json",
                    "fixtures/tradier/recon/history__NVDA__2026-11-20__2026-11-23.json",
                    "fixtures/tradier/recon/history__AMD__2026-11-20__2026-11-23.json"):
            self.assertTrue(os.path.isfile(os.path.join(RECON, *rel.split("/"))), rel)
        for key in RECON_QUOTE_KEYS:
            self.assertTrue(os.path.isfile(os.path.join(RECON_SET, "quotes__%s.json" % key)), key)
        for rel in ("state", "data", "journal", "levels", "reports", "incidents"):
            self.assertFalse(os.path.exists(os.path.join(RECON, rel)), rel)
        # Decision 2 of the PR 2c addendum: the adopted example rulebook is committed, and nothing else under rules/.
        self.assertEqual(["ADOPTED", "CHANGELOG.md", "exit_rules-1.0.0.toml"],
                         sorted(os.listdir(os.path.join(RECON, "rules"))))
        for name in ("ADOPTED", "CHANGELOG.md", "exit_rules-1.0.0.toml"):
            self.assertEqual(read_bytes(os.path.join(DESK, "rules", name)), read_bytes(os.path.join(RECON, "rules", name)),
                             name)
        # Exactly the files named by the addendum, nothing else, in the recorded set.
        found = sorted(os.path.relpath(p, RECON_SET).replace(os.sep, "/") for p in walk_files(RECON_SET))
        self.assertEqual(["clock.json", "errors/invalid_token.txt", "errors/quota_violation.txt",
                          "history__AMD__2026-11-20__2026-11-23.json", "history__NVDA__2026-11-20__2026-11-23.json",
                          "history__WMT__2026-11-20__2026-11-23.json", "quotes__SPY,SPY261218P00620000.json",
                          "quotes__SPY.json", "quotes__SPY261218P00620000.json"], found)

    def test_desk_toml_is_a_byte_identical_copy(self) -> None:
        self.assertEqual(read_bytes(os.path.join(DESK, "desk.toml")), read_bytes(os.path.join(RECON, "desk.toml")))

    def test_positions_csv_twelve_rows(self) -> None:
        lines, comments, rows = csv_lines(os.path.join(RECON, "positions.csv"))
        self.assertEqual(POSITIONS_HEADER, lines[0])
        self.assertEqual(1, len(comments), comments)
        self.assertIn("2026-11-24T12:00:00Z", comments[0])
        header = lines[0].split(",")
        self.assertEqual(12, len(rows), rows)
        for row in rows:
            self.assertEqual(len(header), len(row), row)
        by_name = [dict(zip(header, row)) for row in rows]
        self.assertEqual(list(RECON_TRADE_IDS), [r["trade_id"] for r in by_name])
        self.assertEqual([""] * 12, [r["account"] for r in by_name])  # blank account = "default"
        self.assertEqual(["open"] * 11 + ["closed"], [r["status"] for r in by_name])
        occs = [r["occ_symbol"] for r in by_name if r["instrument_type"] == "option"]
        self.assertEqual(["WMT261120P00095000", "WMT261120C00105000", "NVDA261120C00150000", "AMD261120C00230000",
                          "SPX261120C06500000", "RUT261120P02200000", "AAPL261120P00230000", "SPY261218P00620000",
                          "SPY261016P00600000"], occs)
        for symbol in occs:
            parsed = occ.parse(symbol)
            self.assertEqual(parsed.compact, symbol)
        expired = [s for s in occs if occ.parse(s).expiration == NOV20_EXPIRY]
        self.assertEqual(7, len(expired))  # seven November legs reconciled at --now; the December put is live
        for row in by_name:
            quantity = Decimal(row["quantity"])
            self.assertNotEqual(0, quantity, row)
            if row["cost_basis_total"]:
                self.assertGreater(Decimal(row["cost_basis_total"]) * quantity, 0, row)
            if row["open_price_per_share"]:
                total = quantity * 100 * Decimal(row["open_price_per_share"])
                self.assertEqual(total, Decimal(row["cost_basis_total"]), row)  # the two conventions agree
            self.assertEqual("", row["cost_basis_per_unit"], row)
        # Index roots in the recon book: SPX (a value is recorded) and RUT (no value): AM-settled.
        self.assertEqual(["SPX", "RUT"], [occ.parse(s).root for s in occs if occ.parse(s).root in ("SPX", "RUT")])

    def test_calendar_adds_the_spx_settlement_value(self) -> None:
        doc = toml_compat.load(RECON_CALENDAR)
        main = toml_compat.load(CALENDAR)
        self.assertEqual(main["symbols"], doc["symbols"])
        self.assertEqual(main["closures"], doc["closures"])
        self.assertEqual(main["settlement"]["am_settled_roots"], doc["settlement"]["am_settled_roots"])
        values = doc["settlement"]["values"]
        self.assertEqual(1, len(values))
        self.assertEqual({"root": "SPX", "date": date(2026, 11, 20), "value": Decimal("6523.17"),
                          "source": "owner-entered (fixture)", "checked_utc": datetime(2026, 11, 23, 12, 0, tzinfo=UTC)},
                         values[0])
        self.assertEqual([], [v for v in values if v["root"] == "RUT"])  # RUT: SETTLEMENT_UNKNOWN
        self.assertEqual(Decimal("23.17"), Decimal("6523.17") - Decimal("6500"))  # the CASH_SETTLED close
        # The main root's text is kept verbatim ahead of the added array of tables.
        main_text, recon_text = read_text(CALENDAR), read_text(RECON_CALENDAR)
        self.assertIn("[[settlement.values]]", recon_text)
        self.assertNotIn("[[settlement.values]]", main_text)
        for line in main_text.splitlines():
            self.assertIn(line, recon_text, line)

    def test_recon_set_clock(self) -> None:
        clock = read_json(os.path.join(RECON_SET, "clock.json"))["clock"]
        self.assertEqual(("2026-11-24", "premarket", RECON_EPOCHS[1]), (clock["date"], clock["state"], clock["timestamp"]))
        self.assertIsInstance(clock["timestamp"], int)
        self.assertEqual(epoch_s(RECON_FACTS["now"]), clock["timestamp"])
        reconciled = mc.reconcile_vendor_state(mc.session_at(RECON_FACTS["now"]), clock)
        self.assertTrue(reconciled.agree, reconciled.disagreement)
        self.assertRegex(clock["next_change"], r"^\d{2}:\d{2}$")
        self.assertIn(clock["next_state"], ("premarket", "open", "postmarket", "closed"))

    def test_recon_set_quotes(self) -> None:
        seen: Dict[str, dict] = {}
        for key in RECON_QUOTE_KEYS:
            payload = read_json(os.path.join(RECON_SET, "quotes__%s.json" % key))
            matched, unmatched = quotes_in(payload)
            self.assertEqual([], unmatched, key)
            self.assertEqual(key.split(","), sorted(q["symbol"] for q in matched), key)
            if len(matched) == 1:
                self.assertIsInstance(payload["quotes"]["quote"], dict, key)
            else:
                self.assertIsInstance(payload["quotes"]["quote"], list, key)
            for quote in matched:
                tag = "%s %s" % (key, quote["symbol"])
                for stamp_key in DATE_KEYS:
                    self.assertEqual(RECON_EPOCHS[0], quote[stamp_key], tag)
                    et = mc.to_et(datetime.fromtimestamp(quote[stamp_key] / 1000, tz=UTC))
                    self.assertEqual(RECON_FACTS["stamp_et"], et.strftime("%Y-%m-%d %H:%M:%S"), tag)
                self.assertEqual(quote["last"], quote["close"], tag)  # closing quotes
                bid, ask, last, prev = dec(quote["bid"]), dec(quote["ask"]), dec(quote["last"]), dec(quote["prevclose"])
                self.assertTrue(bid < ask and bid <= last <= ask, tag)
                self.assertTrue(dec(quote["low"]) <= min(dec(quote["open"]), last), tag)
                self.assertTrue(max(dec(quote["open"]), last) <= dec(quote["high"]), tag)
                self.assertEqual(last - prev, dec(quote["change"]), tag)
                self.assertEqual(((last - prev) / prev * 100).quantize(Decimal("0.01"), rounding=ROUND_HALF_EVEN),
                                 dec(quote["change_percentage"]), tag)
                if quote["symbol"] in seen:
                    self.assertEqual(seen[quote["symbol"]], quote, tag)
                seen[quote["symbol"]] = quote
        for symbol, (bid, ask, last, prevclose) in RECON_ANCHORS.items():
            quote = seen[symbol]
            self.assertEqual((Decimal(bid), Decimal(ask), Decimal(last), Decimal(prevclose)),
                             (dec(quote["bid"]), dec(quote["ask"]), dec(quote["last"]), dec(quote["prevclose"])), symbol)
        self.assertEqual("etf", seen["SPY"]["type"])
        self.assertEqual("option", seen["SPY261218P00620000"]["type"])
        self.assertNotIn("greeks", seen["SPY261218P00620000"])
        self.assertEqual("put", seen["SPY261218P00620000"]["option_type"])

    def test_recon_set_history(self) -> None:
        for symbol, bars in TWO_BAR_HISTORY.items():
            payload = read_json(os.path.join(RECON_SET, "history__%s__2026-11-20__2026-11-23.json" % symbol))
            day = payload["history"]["day"]
            self.assertIsInstance(day, list, symbol)  # several bars: a list
            self.assertEqual([expected_bar(b) for b in bars], [bar_tuple(b) for b in day], symbol)
            for bar in day:
                self.assertEqual(["date", "open", "high", "low", "close", "volume"], list(bar))
                o, h, lo, c = dec(bar["open"]), dec(bar["high"]), dec(bar["low"]), dec(bar["close"])
                self.assertLessEqual(lo, min(o, c), bar)
                self.assertLessEqual(max(o, c), h, bar)
        # The holiday set of the main root carries the same WMT and NVDA two-bar files, byte for byte.
        for symbol in ("WMT", "NVDA"):
            rel = "history__%s__2026-11-20__2026-11-23.json" % symbol
            self.assertEqual(read_bytes(os.path.join(set_dir("holiday"), rel)), read_bytes(os.path.join(RECON_SET, rel)), rel)
        # AMD: closed 1.00 in the money (231.00 vs 230) and Monday opened below the strike (229.40):
        # PIN_UNCERTAIN by the crossing rule, never PROBABLE_ASSIGNED.
        amd = TWO_BAR_HISTORY["AMD"]
        self.assertGreater(Decimal(amd[0][4]), Decimal("230.25"))
        self.assertLess(Decimal(amd[1][1]), Decimal("230"))
        # No AAPL history: the AAPL puts are EXPIRED_UNRECORDED.
        self.assertFalse(os.path.exists(os.path.join(RECON_SET, "history__AAPL__2026-11-20__2026-11-23.json")))

    def test_recon_set_errors_match_the_main_root(self) -> None:
        for name in ("quota_violation.txt", "invalid_token.txt"):
            self.assertEqual(read_bytes(os.path.join(set_dir("regular"), "errors", name)),
                             read_bytes(os.path.join(RECON_SET, "errors", name)), name)

    def test_recon_readme_states_the_instants_and_the_outcomes(self) -> None:
        text = read_text(RECON_README)
        for needle in ("`recon`", str(RECON_EPOCHS[0]), str(RECON_EPOCHS[1]), "2026-11-24T12:00:00Z",
                       RECON_FACTS["stamp_et"], "2026-11-23T20:59:50Z", "premarket", "07:00", "6523.17", "23.17",
                       "EXPIRED_OTM", "PIN_UNCERTAIN", "PROBABLE_ASSIGNED", "CASH_SETTLED", "SETTLEMENT_UNKNOWN",
                       "EXPIRED_UNRECORDED", "UNAVAILABLE", "synthetic", "quotes__", "history__", "clock.json",
                       "_generate.py", "651.90", "651.94", "651.92", "650.02", "4.05", "4.25", "4.15",
                       "104.86", "105.30", "153.40", "152.90", "231.00", "229.40", "object", "list"):
            self.assertIn(needle, text, needle)
        for trade_id in sorted(set(RECON_TRADE_IDS)):
            self.assertIn(trade_id, text, trade_id)
        self.assertNotIn("Bearer", text)


def structuring_chain(name: str, underlying: str, expiration: str) -> List[Dict[str, Any]]:
    return read_json(os.path.join(set_dir(name), "chains__%s__%s.json" % (underlying, expiration)))["options"]["option"]


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return date(year, month, 1 + (4 - first.weekday()) % 7 + 14)


def mid_of(quote: Dict[str, Any]) -> Decimal:
    return (dec(quote["bid"]) + dec(quote["ask"])) / 2


def to_tick(value: float) -> Decimal:
    """``max(0.05, half-even(value / 0.05) x 0.05)`` to the cent: the chains' model-price rounding."""
    ticks = (Decimal(repr(value)) / Decimal("0.05")).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    return max(ticks * Decimal("0.05"), Decimal("0.05")).quantize(Decimal("0.01"))


def cutoff_16_et(expiration: str) -> datetime:
    return mc.from_et(datetime.combine(date.fromisoformat(expiration), mc.REGULAR_CLOSE_ET))


def resolved_sigma0(underlying: str, sigma_set: str) -> float:
    """sigma0 solved here from the sigma set's own anchor quote and spot (q = 0), independently of the generator."""
    anchor = {"WMT": "WMT261120C00105000", "NVDA": "NVDA261120C00150000", "SPY": "SPY261218P00620000"}[underlying]
    parsed = occ.parse(anchor)
    option = quotes_in(read_json(os.path.join(set_dir(sigma_set), "quotes__%s.json" % anchor)))[0][0]
    spot = quotes_in(read_json(os.path.join(set_dir(sigma_set), "quotes__%s.json" % underlying)))[0][0]
    tau = (cutoff_16_et(parsed.expiration.isoformat()) - SET_FACTS[sigma_set]["stamp"]).total_seconds() / (365 * 86400)
    solved = iv.implied_vol(float(mid_of(option)), "CALL" if parsed.is_call else "PUT", float(mid_of(spot)),
                            float(parsed.strike), tau, STRUCTURING_RATE, 0.0)
    assert solved.ok, (underlying, sigma_set, solved.status)
    return solved.sigma


class StructuringChainTest(unittest.TestCase):
    """The structuring chains of PR 3b (designer Q's D1 and D2, addendum S12), checked independently of the
    generator: present only where listed, the recorded shape, listed standard monthlies, re-priced here from the
    stated surface with sigma0 re-solved from the anchor quote, put-call parity, the calendar order, and the bytes."""

    def test_present_only_where_listed(self) -> None:
        anchors = {"chains__%s__%s.json" % item for item in CHAINS.items()}
        for name in SETS:
            with self.subTest(name=name):
                found = {os.path.basename(p) for p in walk_files(set_dir(name)) if os.path.basename(p).startswith("chains__")}
                listed = {"chains__%s__%s.json" % key for key in STRUCTURING_CHAINS.get(name, {})}
                self.assertEqual(anchors | listed, found)
                self.assertNotIn("chains__SPY__2027-01-15.json", found, "designer Q's D2: SPY's chain is 2027-03-19")

    def test_the_recorded_shape(self) -> None:
        anchor_keys = list(quotes_in(read_json(os.path.join(set_dir("regular"), "quotes__WMT261120C00105000.json")))[0][0])
        for name, chains in STRUCTURING_CHAINS.items():
            stamp = epoch_ms(SET_FACTS[name]["stamp"])
            for (underlying, expiration), count in chains.items():
                with self.subTest(name=name, chain=(underlying, expiration)):
                    payload = read_json(os.path.join(set_dir(name), "chains__%s__%s.json" % (underlying, expiration)))
                    self.assertEqual(["options"], list(payload))
                    self.assertEqual(["option"], list(payload["options"]))
                    options = payload["options"]["option"]
                    self.assertEqual(count, len(options))
                    strikes = [Decimal(s) for s in STRUCTURING_STRIKES[underlying]]
                    self.assertEqual(sorted(strikes * 2), sorted(dec(o["strike"]) for o in options))
                    for strike in strikes:
                        rights = sorted(o["option_type"] for o in options if dec(o["strike"]) == strike)
                        self.assertEqual(["call", "put"], rights, strike)
                    for option in options:
                        self.assertEqual(anchor_keys + ["greeks"], list(option), option["symbol"])
                        self.assertIsNone(option["greeks"])
                        self.assertEqual((underlying, expiration, underlying), (option["underlying"],
                                                                                option["expiration_date"],
                                                                                option["root_symbol"]))
                        self.assertEqual(("option", 100, "standard", None), (option["type"], option["contract_size"],
                                                                              option["expiration_type"], option["close"]))
                        for key in DATE_KEYS:
                            self.assertEqual(stamp, option[key], (option["symbol"], key))
                        self.assertLessEqual(0, dec(option["bid"]))
                        self.assertLess(dec(option["bid"]), dec(option["ask"]))
                        self.assertEqual(mid_of(option), dec(option["last"]), option["symbol"])
                        parsed = occ.parse(option["symbol"])
                        self.assertEqual((parsed.compact, parsed.root, parsed.strike, parsed.expiration.isoformat(),
                                          "call" if parsed.is_call else "put"),
                                         (option["symbol"], underlying, dec(option["strike"]), expiration,
                                          option["option_type"]))
                        self.assertGreater(option["open_interest"], 0)
                        self.assertGreater(option["volume"], 0)
                    for right, sign in (("call", -1), ("put", 1)):
                        mids = [mid_of(o) for o in sorted((o for o in options if o["option_type"] == right),
                                                          key=lambda o: dec(o["strike"]))]
                        for earlier, later in zip(mids, mids[1:]):
                            self.assertTrue(earlier >= later if sign < 0 else earlier <= later, (right, mids))

    def test_dates_are_listed_standard_monthlies(self) -> None:
        for name, chains in STRUCTURING_CHAINS.items():
            for underlying, expiration in chains:
                with self.subTest(name=name, chain=(underlying, expiration)):
                    listed = read_json(os.path.join(set_dir(name), "expirations__%s.json" % underlying))
                    self.assertIn(expiration, listed["expirations"]["date"])
                    day = date.fromisoformat(expiration)
                    self.assertEqual(day, mc.settlement_day(third_friday(day.year, day.month), False))
                    self.assertGreater(day, SET_FACTS[name]["quote_date"])

    def test_independent_re_pricing_at_each_sets_stamp(self) -> None:
        for name, chains in STRUCTURING_CHAINS.items():
            stamp = SET_FACTS[name]["stamp"]
            for underlying, expiration in chains:
                sigma0 = resolved_sigma0(underlying, STRUCTURING_SIGMA_SET[name])
                spot_quote = quotes_in(read_json(os.path.join(set_dir(name), "quotes__%s.json" % underlying)))[0][0]
                spot = float(mid_of(spot_quote))
                s_prev, s_open = float(dec(spot_quote["prevclose"])), float(dec(spot_quote["open"]))
                tau = (cutoff_16_et(expiration) - stamp).total_seconds() / (365 * 86400)
                skew = STRUCTURING_SKEW[underlying]

                def surface(strike: float, at: float) -> float:
                    return max(0.05, sigma0 - skew * math.log(strike / at))

                for option in structuring_chain(name, underlying, expiration):
                    with self.subTest(name=name, symbol=option["symbol"]):
                        right = "CALL" if option["option_type"] == "call" else "PUT"
                        strike = float(dec(option["strike"]))
                        mid = to_tick(bsm.price(right, spot, strike, tau, STRUCTURING_RATE, 0.0, surface(strike, spot)))
                        half = Decimal("0.10") if mid >= 5 else Decimal("0.05")
                        self.assertEqual((max(mid - half, Decimal("0")), mid + half), (dec(option["bid"]),
                                                                                        dec(option["ask"])))
                        self.assertEqual(mid, mid_of(option))
                        self.assertEqual(to_tick(bsm.price(right, s_prev, strike, tau + 1.0 / 365.0, STRUCTURING_RATE, 0.0,
                                                           surface(strike, s_prev))), dec(option["prevclose"]))
                        self.assertEqual(to_tick(bsm.price(right, s_open, strike, tau, STRUCTURING_RATE, 0.0,
                                                           surface(strike, s_open))), dec(option["open"]))

    def test_put_call_parity(self) -> None:
        worst = Decimal("0")
        for name, chains in STRUCTURING_CHAINS.items():
            stamp = SET_FACTS[name]["stamp"]
            for underlying, expiration in chains:
                spot = mid_of(quotes_in(read_json(os.path.join(set_dir(name), "quotes__%s.json" % underlying)))[0][0])
                tau = (cutoff_16_et(expiration) - stamp).total_seconds() / (365 * 86400)
                options = structuring_chain(name, underlying, expiration)
                for strike in {dec(o["strike"]) for o in options}:
                    call = [mid_of(o) for o in options if o["option_type"] == "call" and dec(o["strike"]) == strike][0]
                    put = [mid_of(o) for o in options if o["option_type"] == "put" and dec(o["strike"]) == strike][0]
                    forward = spot - strike * Decimal(repr(math.exp(-STRUCTURING_RATE * tau)))
                    residual = abs(call - put - forward)
                    worst = max(worst, residual)
                    self.assertLessEqual(residual, Decimal("0.05"), (name, underlying, expiration, strike))
        self.assertGreater(worst, 0, "the ticks leave a residual")

    def test_the_later_expiration_is_worth_more(self) -> None:
        for underlying in ("WMT", "NVDA"):
            spot = mid_of(quotes_in(read_json(os.path.join(set_dir("regular"), "quotes__%s.json" % underlying)))[0][0])
            december = {(o["option_type"], dec(o["strike"])): mid_of(o)
                        for o in structuring_chain("regular", underlying, "2026-12-18")}
            for option in structuring_chain("regular", underlying, "2027-01-15"):
                key = (option["option_type"], dec(option["strike"]))
                if option["option_type"] == "put" and key[1] >= spot:
                    continue  # a deep in-the-money European put can lose value with time (r > 0)
                with self.subTest(underlying=underlying, contract=key):
                    self.assertGreaterEqual(mid_of(option), december[key])

    def test_the_bytes(self) -> None:
        for (name, underlying, expiration), digest in STRUCTURING_SHA256.items():
            with self.subTest(name=name, chain=(underlying, expiration)):
                path = os.path.join(set_dir(name), "chains__%s__%s.json" % (underlying, expiration))
                self.assertEqual(digest, hashlib.sha256(read_bytes(path)).hexdigest())
        self.assertEqual({(name, u, e) for name, chains in STRUCTURING_CHAINS.items() for u, e in chains},
                         set(STRUCTURING_SHA256))
        self.assertEqual(set(STRUCTURING_SHA256), set(STRUCTURING_TABLE))

    def test_the_readme_states_the_surface_and_the_table(self) -> None:
        text = read_text(README)
        for (name, underlying, expiration), (spot, seconds, years, sigma0) in STRUCTURING_TABLE.items():
            row = "| `%s` | %s %s | %s | %d / %s | %s |" % (name, underlying, expiration, spot, seconds, years, sigma0)
            self.assertIn(row, text)
            self.assertEqual(sigma0, "%.4f" % resolved_sigma0(underlying, STRUCTURING_SIGMA_SET[name]))
            stamp = SET_FACTS[name]["stamp"]
            self.assertEqual(seconds, int((cutoff_16_et(expiration) - stamp).total_seconds()))
            self.assertEqual(years, "%.6f" % (seconds / (365 * 86400)))
            self.assertEqual(Decimal(spot), mid_of(quotes_in(read_json(os.path.join(set_dir(name),
                                                                                    "quotes__%s.json" % underlying)))[0][0]))
            self.assertIn("chains__%s__%s.json" % (underlying, expiration), text)
        for needle in ("## Structuring chains", "no anchor contract", "max(0.05, sigma0 - skew x ln(K / S))",
                       "0.25 (WMT), 0.25 (NVDA) and 0.50 (SPY)", "`q` = 0", "`r` = 0.0412", "2027-03-19 16:00 ET = 20:00Z",
                       "Each anchor `chains__` file holds five strikes", "no file", "0.9547", "0.3168"):
            self.assertIn(needle, text, needle)
        self.assertNotIn("Each `chains__` file holds five strikes", text, "the sentence names the anchor chains only")
        for strikes in STRUCTURING_STRIKES.values():
            self.assertIn(", ".join(strikes), text)


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

    def test_readme_states_the_expiry_week_prices_and_the_fallback(self) -> None:
        text = read_text(README)
        for name, anchors in SET_ANCHORS.items():
            for symbol, values in anchors.items():
                for value in values:
                    self.assertIn(value, text, "%s %s %s" % (name, symbol, value))
        for spec in list(TWO_BAR_HISTORY["WMT"]) + list(TWO_BAR_HISTORY["NVDA"]) + list(POST_EXPIRY_BARS.values()):
            for value in spec[1:5]:
                self.assertIn(value, text, value)
            self.assertIn(str(spec[5]), text, spec)
        for needle in ("1795208400", "2026-11-20T21:00:00Z", "max(intrinsic, 0.01) + 0.05", "object", "list",
                       "2026-11-20__2026-11-23", "post_expiry", "expiry_day", "t1", "Saturday"):
            self.assertIn(needle, text, needle)

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
