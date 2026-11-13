"""The offline fixture desk roots: shapes, instants and consistency.

``tests/fixtures/desk`` is the main fixture root (eight recorded Alpaca paper sets, the fixture book of
record ``positions.csv`` and ``research/calendar.toml``); ``tests/fixtures/desk_recon`` is the
reconciliation root (its own ``positions.csv`` and calendar, one recorded set ``recon``).

These tests are deliberately independent of ``aegis_desk.market_types`` and of the client's parsers
(they read the JSON with the standard library, convert RFC 3339 stamps with ``datetime.fromisoformat``
and epochs with the brief's own formula, and use ``market_clock.to_et`` and ``occ.parse`` as the
independent checks), so a fixture mistake and a parser mistake can never cancel out. The instants and
prices asserted here are the contract the data-plane builders rely on; the README beside the sets must
state every epoch value. The one exception is :class:`ClientRoundTripTest`: it replays every request the
tools make through ``alpaca_client.OfflineTransport`` on a temporary copy of the trees, as an added
check that each request resolves to a file and normalises to the book's values (it never replaces the
independent checks). The CSV and TOML files are read with the standard ``csv`` module and
``toml_compat`` only (never ``positions_csv`` or ``calendar_events``), so the fixtures are pinned
independently of the parsers built on them.
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
import shutil
import tempfile
import unittest
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Dict, Iterator, List, Optional, Tuple

from aegis_desk import alpaca_client, bsm, iv, occ, toml_compat
from aegis_desk import market_clock as mc

UTC = timezone.utc
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
ALPACA = os.path.join(DESK, "fixtures", "alpaca")
README = os.path.join(ALPACA, "README.md")
CALENDAR = os.path.join(DESK, "research", "calendar.toml")
RECON = os.path.join(ROOT, "tests", "fixtures", "desk_recon")
RECON_ALPACA = os.path.join(RECON, "fixtures", "alpaca")
RECON_SET = os.path.join(RECON_ALPACA, "recon")
RECON_README = os.path.join(RECON_ALPACA, "README.md")
RECON_CALENDAR = os.path.join(RECON, "research", "calendar.toml")

SETS = ("regular", "after_close", "stale", "pre_open", "holiday", "t1", "expiry_day", "post_expiry")
UNDERLYINGS = ("SPY", "WMT", "NVDA")
OPTIONS = ("WMT261120C00105000", "NVDA261120C00150000", "SPY261218P00620000")
ANCHOR_FOR = {"WMT": "WMT261120C00105000", "NVDA": "NVDA261120C00150000", "SPY": "SPY261218P00620000"}
EXPIRED_BY_HOLIDAY = ("WMT261120C00105000", "NVDA261120C00150000")  # expired 2026-11-20
NOV3_SETS = ("regular", "after_close", "stale")
# Sets whose quotes are stamped on 2026-11-20, the expiration day of the two November contracts:
# those contracts are still quoted and listed through their last session (expiration == quote day).
QUOTED_ON_EXPIRY_DAY = ("expiry_day", "post_expiry")
# Sets whose WMT and NVDA chains are the documented intrinsic-plus-one-tick fallback (the quotes
# are 10 s before the close: tau under one hour, the anchor implied volatility is not solved).
FALLBACK_CHAINS = {"post_expiry": ("WMT", "NVDA")}
NOV20_EXPIRY = date(2026, 11, 20)
EXPIRATIONS_WINDOW_DAYS = 400  # the client's option_expirations window: start (the ET date of --now) + 400 days

# Per set: ET date of the quotes, the quote stamp (UTC), the --now instant (UTC), the clock's is_open.
SET_FACTS: Dict[str, Dict[str, Any]] = {
    "regular": {"quote_date": date(2026, 11, 3), "stamp": datetime(2026, 11, 3, 19, 31, 12, tzinfo=UTC),
                "now": datetime(2026, 11, 3, 19, 50, 0, tzinfo=UTC), "is_open": True,
                "stamp_et": "2026-11-03 14:31:12"},
    "after_close": {"quote_date": date(2026, 11, 3), "stamp": datetime(2026, 11, 3, 20, 59, 40, tzinfo=UTC),
                    "now": datetime(2026, 11, 3, 21, 25, 0, tzinfo=UTC), "is_open": False,
                    "stamp_et": "2026-11-03 15:59:40"},
    "stale": {"quote_date": date(2026, 11, 3), "stamp": datetime(2026, 11, 3, 18, 50, 0, tzinfo=UTC),
              "now": datetime(2026, 11, 3, 19, 50, 0, tzinfo=UTC), "is_open": True,
              "stamp_et": "2026-11-03 13:50:00"},
    "pre_open": {"quote_date": date(2026, 11, 2), "stamp": datetime(2026, 11, 2, 20, 59, 55, tzinfo=UTC),
                 "now": datetime(2026, 11, 3, 13, 0, 0, tzinfo=UTC), "is_open": False,
                 "stamp_et": "2026-11-02 15:59:55"},
    "holiday": {"quote_date": date(2026, 11, 25), "stamp": datetime(2026, 11, 25, 20, 59, 50, tzinfo=UTC),
                "now": datetime(2026, 11, 26, 15, 0, 0, tzinfo=UTC), "is_open": False,
                "stamp_et": "2026-11-25 15:59:50"},
    "t1": {"quote_date": date(2026, 11, 19), "stamp": datetime(2026, 11, 19, 19, 31, 12, tzinfo=UTC),
           "now": datetime(2026, 11, 19, 19, 50, 0, tzinfo=UTC), "is_open": True,
           "stamp_et": "2026-11-19 14:31:12"},
    "expiry_day": {"quote_date": date(2026, 11, 20), "stamp": datetime(2026, 11, 20, 19, 31, 12, tzinfo=UTC),
                   "now": datetime(2026, 11, 20, 19, 50, 0, tzinfo=UTC), "is_open": True,
                   "stamp_et": "2026-11-20 14:31:12"},
    "post_expiry": {"quote_date": date(2026, 11, 20), "stamp": datetime(2026, 11, 20, 20, 59, 50, tzinfo=UTC),
                    "now": datetime(2026, 11, 21, 14, 30, 0, tzinfo=UTC), "is_open": False,
                    "stamp_et": "2026-11-20 15:59:50"},
}
# The recon root's single set: closing quotes of Monday 2026-11-23, read Tuesday 07:00 ET.
RECON_FACTS: Dict[str, Any] = {
    "quote_date": date(2026, 11, 23), "stamp": datetime(2026, 11, 23, 20, 59, 50, tzinfo=UTC),
    "now": datetime(2026, 11, 24, 12, 0, 0, tzinfo=UTC), "is_open": False, "stamp_et": "2026-11-23 15:59:50",
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
# option prevclose values are the fixture's own choices, documented in the README). Bid and ask are in
# the quote files; an option's last is its snapshot's latestTrade price and its prevclose the contract
# listing's close_price; a stock's last and prevclose are the generator's book only (no stock snapshot
# endpoint is allow-listed), stated in the README.
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
# The quote batches the journeys and tool tests send (one request each before the split): the client sends the equities
# of a batch to /v2/stocks/quotes/latest and its options to /v1beta1/options/quotes/latest, so each batch reads
# one stock_quotes__ and/or one option_quotes__ file.
QUOTE_BATCHES: Tuple[Tuple[str, ...], ...] = (
    ("SPY",), ("WMT",), ("NVDA",), ("SPY", "WMT"), ("SPY", "WMT", "NVDA"),
    ("WMT261120C00105000",), ("NVDA261120C00150000",), ("SPY261218P00620000",),
    ("SPY", "WMT", "WMT261120C00105000"),
    ("WMT", "WMT261120C00105000"), ("NVDA", "NVDA261120C00150000"), ("SPY", "SPY261218P00620000"),
    ("SPY", "WMT", "NVDA", "WMT261120C00105000", "NVDA261120C00150000", "SPY261218P00620000"),
)
REGULAR_ONLY_BATCHES: Tuple[Tuple[str, ...], ...] = (("SPX",), ("SPX", "SPY"))
RECON_BATCHES: Tuple[Tuple[str, ...], ...] = (("SPY",), ("SPY261218P00620000",), ("SPY", "SPY261218P00620000"))
REQUIRED_STOCK_KEYS = ("SPY", "WMT", "NVDA", "SPY,WMT", "NVDA,SPY,WMT")
REQUIRED_OPTION_KEYS = ("WMT261120C00105000", "NVDA261120C00150000", "SPY261218P00620000",
                        "NVDA261120C00150000,SPY261218P00620000,WMT261120C00105000")
CHAINS = {"WMT": "2026-11-20", "NVDA": "2026-11-20", "SPY": "2026-12-18"}
CHAIN_STRIKES = {"WMT": ("100", "102.5", "105", "107.5", "110"), "NVDA": ("140", "145", "150", "155", "160"),
                 "SPY": ("600", "610", "620", "630", "640")}
# The listed strikes the expirations listing picks from (one call per expiration, the strike nearest the spot).
LISTED_STRIKES = {
    "WMT": ("85", "90", "95", "97.5", "100", "102.5", "105", "107.5", "110", "112.5", "115", "120"),
    "NVDA": ("120", "125", "130", "135", "140", "145", "150", "155", "160", "165", "170", "180"),
    "SPY": ("560", "580", "590", "600", "610", "620", "625", "630", "640", "650", "660", "680", "700"),
}
# PR 3b: the structuring chains (designer Q's D1 and D2, addendum S12): no anchor contract, priced from the stated
# surface sigma(K) = max(0.05, sigma0 - skew x ln(K / S)) with q = 0 and r = 0.0412. Per set: (underlying, expiration)
# -> the number of contracts (a call and a put per strike).
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
# The bytes the generator writes for each structuring chain: (snapshots__ file, contracts__ file), re-pinned from
# the generator (identical on CPython 3.9 and 3.13).
STRUCTURING_SHA256: Dict[Tuple[str, str, str], Tuple[str, str]] = {
    ("regular", "WMT", "2026-12-18"): ("8e7133b464523704eaa862903f1b5425aafebe09beb95b7e2948f1ffa4f6a7a3",
                                       "150f2821aab9b6e7e0a21ff6c1748df654ac6894de2073400ce807b56ff94c64"),
    ("regular", "WMT", "2027-01-15"): ("2d249c016271bb497a85b6e991a9208b22ed73f84f60ead24e5915a588ed6d97",
                                       "4fb45b13f6adefcb9eeff0bb9783aa9f83fa9e34e78ca3fc288d2d05d5ea5c0e"),
    ("regular", "NVDA", "2026-12-18"): ("904861ad3bbcec8252357ef03708760c1d862c231b038fcfbca008d09a50e30a",
                                        "cc5e15e27650d52a84316da19b82071c7f9546f47b005217fb16f40f35e9191e"),
    ("regular", "NVDA", "2027-01-15"): ("b49aba533a2f157fdf6c8e56a1cdeb6e6bf6d815b3a6a8a3b5005bc8a1a60a6b",
                                        "03427aa6a3b44e0362fc5118b4e2d89138a763e84fd962ad8da3f9262f7f3d7a"),
    ("regular", "SPY", "2027-03-19"): ("a15de9d0b244d6632b84fe621efd1da19837dbf0e7c9b6341baa7f50e4481ec3",
                                       "44a8a1860b01f553cf8f95342ffbcb08b4f7170620400bb5f3dd77162504071e"),
    ("expiry_day", "WMT", "2027-01-15"): ("606520eabd96eb1974bee9327f0dde3fe54b4df40bbf53a121263364fb76989e",
                                          "38a0e4242f521b052c0f9ddd03f19a2b94a447b9f6e23fbdfe1089d8f28c65e2"),
    ("expiry_day", "NVDA", "2027-01-15"): ("734db10fe6e925b8e1218fc900e54a7eebd19302d72f04fe5cb4d618777f48e2",
                                           "c58b4e3475434cda566be9af6388e3ac5ffe6a43c1589c90d5600fff9e384ea4"),
}
HISTORY_RANGE = ("2026-10-27", "2026-11-02")
HISTORY: Dict[str, List[Tuple[str, str, str, str, str, int]]] = {
    "SPY": [("2026-10-27", "638.40", "641.20", "637.10", "640.55", 52_100_000),
            ("2026-10-28", "640.90", "643.80", "639.60", "642.70", 49_800_000),
            ("2026-10-29", "642.10", "644.30", "640.20", "641.35", 51_300_000),
            ("2026-10-30", "641.80", "644.00", "640.90", "643.15", 57_200_000),
            ("2026-11-02", "644.20", "646.75", "642.90", "645.90", 55_600_000)],
    "WMT": [("2026-10-27", "101.90", "102.80", "101.40", "102.45", 15_200_000),
            ("2026-10-28", "102.50", "103.30", "102.10", "102.95", 14_100_000),
            ("2026-10-29", "102.80", "103.40", "102.20", "102.60", 13_600_000),
            ("2026-10-30", "102.70", "103.60", "102.30", "103.25", 16_400_000),
            ("2026-11-02", "103.30", "104.10", "102.95", "103.80", 13_900_000)],
    "NVDA": [("2026-10-27", "143.20", "145.60", "142.50", "144.90", 210_000_000),
             ("2026-10-28", "145.10", "146.80", "144.20", "145.70", 198_000_000),
             ("2026-10-29", "145.90", "147.20", "144.60", "145.20", 205_000_000),
             ("2026-10-30", "145.50", "146.90", "144.80", "146.10", 232_000_000),
             ("2026-11-02", "146.50", "148.20", "145.80", "147.60", 225_000_000)],
}
# Bars as (date, open, high, low, close, volume).
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
# The recorded shapes (key order as written): Alpaca's latest-quote objects, a snapshot, a trade, a bar, a listed
# contract, a calendar day and the clock.
STOCK_QUOTE_KEYS = ["ap", "as", "ax", "bp", "bs", "bx", "c", "t", "z"]
OPTION_QUOTE_KEYS = ["ap", "as", "ax", "bp", "bs", "bx", "c", "t"]
SNAPSHOT_KEYS = ["latestQuote", "latestTrade", "dailyBar"]
TRADE_KEYS = ["c", "p", "s", "t", "x"]
BAR_KEYS = ["t", "o", "h", "l", "c", "v", "n", "vw"]
CONTRACT_KEYS = ["id", "symbol", "name", "status", "tradable", "expiration_date", "root_symbol", "underlying_symbol",
                 "type", "style", "strike_price", "multiplier", "size", "open_interest", "open_interest_date",
                 "close_price", "close_price_date"]
CALENDAR_KEYS = ["date", "open", "close", "session_open", "session_close", "settlement_date"]
CLOCK_KEYS = ["timestamp", "is_open", "next_open", "next_close"]
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
ERROR_FILES = ("errors/too_many_requests.txt", "errors/subscription.txt", "errors/unauthorized.txt")
STAMP_KEYS = ("t",)
TOKEN_RE = re.compile(r"(?i)(?:bearer|token)[^A-Za-z0-9]{0,8}[A-Za-z0-9]{20,}")
KEY_SHAPED_RE = re.compile(r"\b[PA]K[A-Z0-9]{16,}\b")  # an Alpaca key id (paper PK..., live AK...)
EMOJI_RE = re.compile("[⌀-⏿☀-➿⬀-⯿\U0001F000-\U0001FAFF️]")
RELEASE_TAG_RE = re.compile(r"\bv\d+\.\d+\.\d+\b")
# Order-shaped words that must never appear in a fixture or its documentation.
ORDER_WORDS_RE = re.compile(r"(?i)\b(?:buy|sell|sells|selling|submit|limit order|market order|good till|"
                            r"time in force|day order)\b")
FILE_NAME_RE = re.compile(r"^(?:(stock_quotes|option_quotes)__([A-Z0-9,]+)"
                          r"|(snapshots|contracts)__([A-Z]+)__(\d{4}-\d{2}-\d{2})(?:__(\d{4}-\d{2}-\d{2}))?"
                          r"|bars__([A-Z]+)__(\d{4}-\d{2}-\d{2})__(\d{4}-\d{2}-\d{2})"
                          r"|calendar__(\d{4}-\d{2}-\d{2})__(\d{4}-\d{2}-\d{2})"
                          r"|(clock))\.json$")


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
    return os.path.join(ALPACA, name)


def set_file(name: str, rel: str) -> str:
    return os.path.join(set_dir(name), rel)


def files_named(directory: str, prefix: str) -> List[str]:
    return sorted(p for p in walk_files(directory) if os.path.basename(p).startswith(prefix))


def rfc_z(moment: datetime) -> str:
    """The recorded RFC 3339 form of a whole-second UTC instant (Alpaca omits a zero fraction)."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_rfc(text: str) -> datetime:
    """An independent RFC 3339 reading (whole seconds, ``Z`` or an offset) as an aware UTC datetime."""
    self_check = re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:Z|[+-]\d{2}:\d{2})$", text)
    assert self_check, text
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def et_midnight_utc(day: date) -> datetime:
    return mc.from_et(datetime.combine(day, time(0, 0)))


def epoch_ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def epoch_s(moment: datetime) -> int:
    return int(moment.timestamp())


def dec(value: Any) -> Decimal:
    return Decimal(str(value))


def start_of(name: str) -> date:
    """The ``start`` a tool passes to ``option_expirations``: the ET date of the set's ``--now``."""
    facts = RECON_FACTS if name == "recon" else SET_FACTS[name]
    return mc.to_et(facts["now"]).date()


def listing_name(name: str, underlying: str) -> str:
    start = start_of(name)
    return "contracts__%s__%s__%s.json" % (underlying, start.isoformat(),
                                            (start + timedelta(days=EXPIRATIONS_WINDOW_DAYS)).isoformat())


def quotes_of(path: str) -> Dict[str, Dict[str, Any]]:
    """The ``quotes`` map of a recorded latest-quotes body."""
    return read_json(path)["quotes"]


def stock_quote(name: str, symbol: str) -> Dict[str, Any]:
    return quotes_of(set_file(name, "stock_quotes__%s.json" % symbol))[symbol]


def option_quote(name: str, symbol: str) -> Dict[str, Any]:
    return quotes_of(set_file(name, "option_quotes__%s.json" % symbol))[symbol]


def snapshots_of(name: str, underlying: str, expiration: str) -> Dict[str, Dict[str, Any]]:
    return read_json(set_file(name, "snapshots__%s__%s.json" % (underlying, expiration)))["snapshots"]


def contracts_of(name: str, underlying: str, expiration: str) -> List[Dict[str, Any]]:
    return read_json(set_file(name, "contracts__%s__%s.json" % (underlying, expiration)))["option_contracts"]


def mid_of(quote: Dict[str, Any]) -> Decimal:
    """The mid of an Alpaca quote object (``bp`` and ``ap``)."""
    return (dec(quote["bp"]) + dec(quote["ap"])) / 2


def split_batch(batch: Tuple[str, ...]) -> Tuple[List[str], List[str]]:
    stocks = sorted(s for s in batch if occ.try_parse(s)[0] is None)
    options = sorted(s for s in batch if occ.try_parse(s)[0] is not None)
    return stocks, options


def iter_stamps(node: Any, path: str = "") -> Iterator[Tuple[str, Any]]:
    """Every ``t`` of the quote, trade and bar objects of a body, with its JSON path."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key in STAMP_KEYS and isinstance(value, str):
                yield path + "." + key, value
            else:
                for item in iter_stamps(value, path + "." + str(key)):
                    yield item
    elif isinstance(node, list):
        for index, value in enumerate(node):
            for item in iter_stamps(value, "%s[%d]" % (path, index)):
                yield item


def csv_lines(path: str) -> Tuple[List[str], List[str], List[List[str]]]:
    """(raw lines, comment lines, data rows parsed with the csv module) of a positions file."""
    lines = read_text(path).splitlines()
    comments = [line for line in lines[1:] if line.lstrip().startswith("#")]
    data_lines = [line for line in lines[1:] if line.strip() and not line.lstrip().startswith("#")]
    rows = list(csv.reader(io.StringIO("\n".join(data_lines)))) if data_lines else []
    return lines, comments, rows


def bars_of(path: str, symbol: str) -> List[Dict[str, Any]]:
    payload = read_json(path)
    assert list(payload) == ["bars", "next_page_token"], (path, list(payload))
    assert payload["next_page_token"] is None, path
    assert list(payload["bars"]) == [symbol], (path, list(payload["bars"]))
    return payload["bars"][symbol]


def bar_tuple(bar: Dict[str, Any]) -> Tuple[str, Decimal, Decimal, Decimal, Decimal, int]:
    day = mc.to_et(parse_rfc(bar["t"])).date().isoformat()
    return (day, dec(bar["o"]), dec(bar["h"]), dec(bar["l"]), dec(bar["c"]), bar["v"])


def expected_bar(spec: Tuple[str, str, str, str, str, int]) -> Tuple[str, Decimal, Decimal, Decimal, Decimal, int]:
    return (spec[0], Decimal(spec[1]), Decimal(spec[2]), Decimal(spec[3]), Decimal(spec[4]), spec[5])


def typical_price(bar: Dict[str, Any]) -> Decimal:
    """The bar's ``vw`` rule: ``(h + l + c) / 3`` rounded half-even to 0.0001 (a stated synthetic value)."""
    return ((dec(bar["h"]) + dec(bar["l"]) + dec(bar["c"])) / 3).quantize(Decimal("0.0001"), rounding=ROUND_HALF_EVEN)


def check_bar_shape(test: unittest.TestCase, bar: Dict[str, Any], tag: str) -> None:
    test.assertEqual(BAR_KEYS, list(bar), tag)
    day = mc.to_et(parse_rfc(bar["t"])).date()
    test.assertEqual(et_midnight_utc(day), parse_rfc(bar["t"]), "%s: a daily bar is stamped at 00:00 ET" % tag)
    o, h, lo, c = dec(bar["o"]), dec(bar["h"]), dec(bar["l"]), dec(bar["c"])
    test.assertLessEqual(lo, min(o, c), tag)
    test.assertLessEqual(max(o, c), h, tag)
    for key in ("v", "n"):
        test.assertIsInstance(bar[key], int, tag)
        test.assertNotIsInstance(bar[key], bool, tag)
        test.assertGreater(bar[key], 0, tag)
    test.assertEqual(typical_price(bar), dec(bar["vw"]), tag)


def expiration_check(test: unittest.TestCase, name: str, expiration: date, tag: str) -> None:
    """Options expire after the quote day, except on the expiry-day sets where they may expire on it."""
    if name in QUOTED_ON_EXPIRY_DAY:
        test.assertGreaterEqual(expiration, SET_FACTS[name]["quote_date"], tag)
    else:
        test.assertGreater(expiration, SET_FACTS[name]["quote_date"], tag)


def plain(value: Decimal) -> str:
    """``Decimal('105')`` -> ``"105"``, ``Decimal('97.500')`` -> ``"97.5"``: Alpaca's strike text."""
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def expected_listing_strike(underlying: str, spot: Decimal) -> str:
    """The listed strike nearest the spot (the lower on a tie)."""
    return min(LISTED_STRIKES[underlying], key=lambda s: (abs(Decimal(s) - spot), Decimal(s)))


def check_clock(test: unittest.TestCase, clock: Dict[str, Any], now: datetime, is_open: bool, tag: str) -> None:
    """Alpaca's ``/v2/clock``: the --now instant in ET with its offset, is_open, and the next regular open and
    close (09:30 ET and the day's close, 13:00 on an early close), computed here from ``market_clock``."""
    test.assertEqual(CLOCK_KEYS, list(clock), tag)
    test.assertEqual(mc.to_et(now).isoformat(), clock["timestamp"], tag)
    test.assertEqual(now, parse_rfc(clock["timestamp"]), tag)
    test.assertIs(is_open, clock["is_open"], tag)
    test.assertEqual(is_open, mc.session_at(now).session == "open", tag)
    local = mc.to_et(now)
    today = local.date()
    day = today if mc.is_trading_day(today) and local.time() < time(9, 30) else mc.next_trading_day(today)
    next_open = mc.from_et(datetime.combine(day, time(9, 30)))
    close_day = today if mc.is_trading_day(today) and local.time() < mc.session_close_et(today) else (
        mc.next_trading_day(today))
    next_close = mc.from_et(datetime.combine(close_day, mc.session_close_et(close_day)))
    test.assertEqual(mc.to_et(next_open).isoformat(), clock["next_open"], tag)
    test.assertEqual(mc.to_et(next_close).isoformat(), clock["next_close"], tag)
    test.assertLess(now, next_open, tag)
    test.assertLess(now, next_close, tag)


class TreeShapeTest(unittest.TestCase):
    def test_owner_files_present(self) -> None:
        self.assertTrue(os.path.isfile(os.path.join(DESK, "desk.toml")))
        self.assertTrue(os.path.isfile(os.path.join(DESK, "positions.csv")))
        self.assertTrue(os.path.isfile(README))
        self.assertTrue(os.path.isfile(CALENDAR))
        self.assertEqual(["alpaca"], sorted(os.listdir(os.path.join(DESK, "fixtures"))))
        self.assertEqual(["alpaca"], sorted(os.listdir(os.path.join(RECON, "fixtures"))))

    def test_desk_toml_market_data_is_the_alpaca_paper_block(self) -> None:
        doc = toml_compat.load(os.path.join(DESK, "desk.toml"))
        self.assertEqual({"vendor": "alpaca", "host": "paper", "key_env": "APCA_API_KEY_ID",
                          "secret_env": "APCA_API_SECRET_KEY", "options_feed": "indicative", "stock_feed": "iex",
                          "feed_delay_min": 15, "quote_ttl_min": 5, "grace_min": 5, "pin_band": Decimal("0.25")},
                         doc["market_data"])
        expected = toml_compat.load(os.path.join(ROOT, "tests", "fixtures", "init", "expected_desk.toml"))
        self.assertEqual(expected["market_data"], doc["market_data"])

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
            rels = ["clock.json", "calendar__2026-11-01__2026-11-30.json"] + list(ERROR_FILES)
            for underlying in UNDERLYINGS:
                rels.append(listing_name(name, underlying))
                rels.append("bars__%s__%s__%s.json" % ((underlying,) + HISTORY_RANGE))
            for underlying, expiration in CHAINS.items():
                rels.append("contracts__%s__%s.json" % (underlying, expiration))
                rels.append("snapshots__%s__%s.json" % (underlying, expiration))
            rels += ["stock_quotes__%s.json" % key for key in REQUIRED_STOCK_KEYS]
            rels += ["option_quotes__%s.json" % key for key in REQUIRED_OPTION_KEYS]
            for rel in rels:
                self.assertTrue(os.path.isfile(os.path.join(directory, *rel.split("/"))), "%s/%s" % (name, rel))
        for rel in ("bars__WMT__2026-11-20__2026-11-20.json", "bars__NVDA__2026-11-20__2026-11-20.json",
                    "bars__WMT__2026-11-20__2026-11-23.json", "bars__NVDA__2026-11-20__2026-11-23.json"):
            self.assertTrue(os.path.isfile(set_file("holiday", rel)), "holiday/%s" % rel)
        for rel in ("bars__WMT__2026-11-20__2026-11-20.json", "bars__NVDA__2026-11-20__2026-11-20.json"):
            self.assertTrue(os.path.isfile(set_file("post_expiry", rel)), "post_expiry/%s" % rel)
        self.assertTrue(os.path.isfile(set_file("regular", "stock_quotes__SPX.json")))
        self.assertTrue(os.path.isfile(set_file("regular", "stock_quotes__SPX,SPY.json")))
        for name in SETS:
            if name == "regular":
                continue
            for rel in ("stock_quotes__SPX.json", "stock_quotes__SPX,SPY.json"):
                self.assertFalse(os.path.exists(set_file(name, rel)), "%s/%s is regular-only" % (name, rel))

    def test_every_batch_of_the_tools_has_its_files(self) -> None:
        for name in SETS:
            batches = QUOTE_BATCHES + (REGULAR_ONLY_BATCHES if name == "regular" else ())
            expected = set()
            for batch in batches:
                stocks, options = split_batch(batch)
                if stocks:
                    expected.add("stock_quotes__%s.json" % ",".join(stocks))
                if options:
                    expected.add("option_quotes__%s.json" % ",".join(options))
            found = {os.path.basename(p) for p in walk_files(set_dir(name))
                     if os.path.basename(p).startswith(("stock_quotes__", "option_quotes__"))}
            self.assertEqual(expected, found, name)

    def test_every_json_file_parses(self) -> None:
        files = [p for p in walk_files(DESK) + walk_files(RECON) if p.endswith(".json")]
        self.assertGreaterEqual(len(files), 5 * 16)
        for path in files:
            payload = read_json(path)
            if os.path.basename(path).startswith("calendar__"):
                self.assertIsInstance(payload, list, path)  # Alpaca's calendar is a bare JSON list
            else:
                self.assertIsInstance(payload, dict, path)

    def test_file_names_follow_the_naming_rule(self) -> None:
        """The OfflineTransport key, checked independently of ``alpaca_client.fixture_file_name``."""
        for directory in [set_dir(name) for name in SETS] + [RECON_SET]:
            for path in walk_files(directory):
                rel = os.path.relpath(path, directory).replace(os.sep, "/")
                if rel.startswith("errors/"):
                    self.assertIn(rel, ERROR_FILES)
                    continue
                match = FILE_NAME_RE.match(rel)
                self.assertIsNotNone(match, rel)
                assert match is not None
                if match.group(1):
                    symbols = match.group(2).split(",")
                    self.assertEqual(sorted(symbols), symbols, rel)
                    self.assertEqual(len(set(symbols)), len(symbols), rel)
                    is_option = [occ.try_parse(s)[0] is not None for s in symbols]
                    self.assertEqual([match.group(1) == "option_quotes"] * len(symbols), is_option, rel)
                    for symbol in symbols:
                        if match.group(1) == "option_quotes":
                            self.assertEqual(occ.parse(symbol).compact, symbol, rel)
                elif match.group(3):
                    date.fromisoformat(match.group(5))
                    if match.group(6):
                        self.assertEqual("contracts", match.group(3), rel)
                        first, last = date.fromisoformat(match.group(5)), date.fromisoformat(match.group(6))
                        self.assertEqual(timedelta(days=EXPIRATIONS_WINDOW_DAYS), last - first, rel)
                elif match.group(7):
                    self.assertLessEqual(date.fromisoformat(match.group(8)), date.fromisoformat(match.group(9)), rel)
                elif match.group(10):
                    self.assertEqual(("2026-11-01", "2026-11-30"), (match.group(10), match.group(11)), rel)
                else:
                    self.assertEqual("clock.json", rel)
                self.assertNotIn("__page-", rel, "no recorded answer here is paginated")

    def test_error_bodies(self) -> None:
        for directory in [set_dir(name) for name in SETS] + [RECON_SET]:
            unauthorized = read_text(os.path.join(directory, "errors", "unauthorized.txt"))
            self.assertEqual({"message": "unauthorized."}, json.loads(unauthorized))
            too_many = read_text(os.path.join(directory, "errors", "too_many_requests.txt"))
            self.assertEqual({"message": "too many requests."}, json.loads(too_many))
            subscription = json.loads(read_text(os.path.join(directory, "errors", "subscription.txt")))
            self.assertEqual(["code", "message"], list(subscription))
            self.assertIn("subscription does not permit", subscription["message"])
            for body in (unauthorized, too_many):
                self.assertTrue(body.endswith("}\n"), directory)

    def test_no_credential_shaped_string_anywhere(self) -> None:
        for path in walk_files(DESK) + walk_files(RECON):
            text = read_text(path)
            self.assertIsNone(TOKEN_RE.search(text), path)
            self.assertIsNone(KEY_SHAPED_RE.search(text), path)
            for needle in ("Bearer ", "Authorization", "APCA-API-KEY-ID", "APCA-API-SECRET-KEY"):
                self.assertNotIn(needle, text, path)

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
                if path.endswith((".md", ".csv", ".toml", ".py", ".json", ".txt")):
                    self.assertIsNone(ORDER_WORDS_RE.search(read_text(path)), path)


class QuoteFileTest(unittest.TestCase):
    def test_symbols_match_the_file_name(self) -> None:
        """Every requested symbol is quoted except the documented gaps: SPX (an index) and the two November
        contracts in ``holiday`` (expired). Alpaca echoes no unmatched list: a gap is an absent key."""
        for name in SETS:
            for path in files_named(set_dir(name), "stock_quotes__") + files_named(set_dir(name), "option_quotes__"):
                key = os.path.basename(path).split("__", 1)[1][:-len(".json")]
                symbols = key.split(",")
                quoted = sorted(quotes_of(path))
                gaps = [s for s in symbols if s == "SPX" or (name == "holiday" and s in EXPIRED_BY_HOLIDAY)]
                self.assertEqual(sorted(set(symbols) - set(gaps)), quoted, path)

    def test_the_recorded_shapes(self) -> None:
        for name in SETS:
            for path in files_named(set_dir(name), "stock_quotes__"):
                payload = read_json(path)
                self.assertEqual(["quotes", "currency"], list(payload), path)
                self.assertEqual("USD", payload["currency"], path)
                self.assertEqual(sorted(payload["quotes"]), list(payload["quotes"]), path)
                for symbol, quote in payload["quotes"].items():
                    self.assertEqual(STOCK_QUOTE_KEYS, list(quote), "%s %s" % (path, symbol))
                    self.assertEqual(("V", "V"), (quote["bx"], quote["ax"]), "IEX is the exchange of the iex feed")
                    self.assertEqual(["R"], quote["c"], path)
                    self.assertIn(quote["z"], ("A", "B", "C"), path)
            for path in files_named(set_dir(name), "option_quotes__"):
                payload = read_json(path)
                self.assertEqual(["quotes"], list(payload), path)
                self.assertEqual(sorted(payload["quotes"]), list(payload["quotes"]), path)
                for symbol, quote in payload["quotes"].items():
                    self.assertEqual(OPTION_QUOTE_KEYS, list(quote), "%s %s" % (path, symbol))
                    self.assertNotIn("greeks", quote)

    def test_every_quote_carries_the_sets_stamp(self) -> None:
        for name in SETS:
            expected = rfc_z(SET_FACTS[name]["stamp"])
            for path in files_named(set_dir(name), "stock_quotes__") + files_named(set_dir(name), "option_quotes__"):
                for symbol, quote in quotes_of(path).items():
                    self.assertEqual(expected, quote["t"], "%s %s" % (path, symbol))
                    self.assertEqual(EPOCHS[name][0], epoch_ms(parse_rfc(quote["t"])), path)

    def test_prices_are_coherent(self) -> None:
        for name in SETS:
            for path in files_named(set_dir(name), "stock_quotes__") + files_named(set_dir(name), "option_quotes__"):
                for symbol, quote in quotes_of(path).items():
                    tag = "%s %s" % (path, symbol)
                    for key in ("bp", "ap"):
                        self.assertIsInstance(quote[key], (int, float), tag)
                        self.assertNotIsInstance(quote[key], bool, tag)
                    for key in ("bs", "as"):
                        self.assertIsInstance(quote[key], int, tag)
                        self.assertGreater(quote[key], 0, tag)
                    self.assertGreater(dec(quote["bp"]), 0, tag)
                    self.assertLess(dec(quote["bp"]), dec(quote["ap"]), tag)

    def test_the_same_symbol_is_identical_across_the_files_of_one_set(self) -> None:
        for name in SETS:
            seen: Dict[str, dict] = {}
            sources = [quotes_of(p) for p in files_named(set_dir(name), "stock_quotes__")
                       + files_named(set_dir(name), "option_quotes__")]
            sources += [{s: snap["latestQuote"] for s, snap in read_json(p)["snapshots"].items()}
                        for p in files_named(set_dir(name), "snapshots__")]
            for quotes in sources:
                for symbol, quote in quotes.items():
                    if symbol in seen:
                        self.assertEqual(seen[symbol], quote, "%s %s" % (name, symbol))
                    else:
                        seen[symbol] = quote
            self.assertGreaterEqual(len(seen), 4, name)

    def test_nov3_sets_share_prices_and_differ_only_in_stamps(self) -> None:
        for symbol in UNDERLYINGS + OPTIONS:
            reader = stock_quote if symbol in UNDERLYINGS else option_quote
            base = dict(reader("regular", symbol))
            base.pop("t")
            for name in ("stale", "after_close"):
                other = dict(reader(name, symbol))
                self.assertEqual(rfc_z(SET_FACTS[name]["stamp"]), other.pop("t"))
                self.assertEqual(base, other, "%s %s" % (name, symbol))
        # The day's activity accumulates: stale < regular < after_close daily volume in the snapshots.
        for underlying, expiration in CHAINS.items():
            anchor = ANCHOR_FOR[underlying]
            volumes = [snapshots_of(name, underlying, expiration)[anchor]["dailyBar"]["v"]
                       for name in ("stale", "regular", "after_close")]
            self.assertEqual(sorted(volumes), volumes, underlying)

    def test_anchor_prices_of_the_brief(self) -> None:
        for name in NOV3_SETS:
            for symbol, (bid, ask) in NOV3_ANCHORS.items():
                quote = stock_quote(name, symbol) if symbol in UNDERLYINGS else option_quote(name, symbol)
                self.assertEqual((Decimal(bid), Decimal(ask)), (dec(quote["bp"]), dec(quote["ap"])), "%s %s" % (name, symbol))

    def test_anchor_prices_of_the_expiry_week_sets(self) -> None:
        for name, anchors in SET_ANCHORS.items():
            for symbol, (bid, ask, last, prevclose) in anchors.items():
                tag = "%s %s" % (name, symbol)
                if symbol in UNDERLYINGS:
                    quote = stock_quote(name, symbol)
                    self.assertEqual((Decimal(bid), Decimal(ask)), (dec(quote["bp"]), dec(quote["ap"])), tag)
                    continue
                quote = option_quote(name, symbol)
                parsed = occ.parse(symbol)
                snapshot = snapshots_of(name, parsed.root, parsed.expiration.isoformat())[symbol]
                contract = [c for c in contracts_of(name, parsed.root, parsed.expiration.isoformat())
                            if c["symbol"] == symbol][0]
                self.assertEqual((Decimal(bid), Decimal(ask), Decimal(last), Decimal(prevclose)),
                                 (dec(quote["bp"]), dec(quote["ap"]), dec(snapshot["latestTrade"]["p"]),
                                  dec(contract["close_price"])), tag)

    def test_the_expiry_week_days_link_up(self) -> None:
        """2026-11-19 -> 2026-11-20 -> 2026-11-23: each day's prevclose is the previous day's last."""
        for symbol in UNDERLYINGS + OPTIONS:
            t1_last = SET_ANCHORS["t1"][symbol][2]
            for name in QUOTED_ON_EXPIRY_DAY:
                self.assertEqual(t1_last, SET_ANCHORS[name][symbol][3], "%s %s" % (name, symbol))
        # The expiry-day closing quotes equal the day's bars the reconciliation reads in post_expiry.
        for symbol, bar in POST_EXPIRY_BARS.items():
            self.assertEqual(bar[4], SET_ANCHORS["post_expiry"][symbol][2], symbol)
        # Intraday option volume never exceeds the day's final volume; the day's open is shared.
        for symbol in OPTIONS:
            parsed = occ.parse(symbol)
            intraday = snapshots_of("expiry_day", parsed.root, parsed.expiration.isoformat())[symbol]["dailyBar"]
            closing = snapshots_of("post_expiry", parsed.root, parsed.expiration.isoformat())[symbol]["dailyBar"]
            self.assertLessEqual(intraday["v"], closing["v"], symbol)
            self.assertEqual(intraday["o"], closing["o"], symbol)
            self.assertLessEqual(dec(intraday["h"]), dec(closing["h"]), symbol)
            self.assertGreaterEqual(dec(intraday["l"]), dec(closing["l"]), symbol)
        # Monday's closing quotes in the recon root follow Friday's close.
        self.assertEqual(SET_ANCHORS["post_expiry"]["SPY"][2], RECON_ANCHORS["SPY"][3])
        self.assertEqual(SET_ANCHORS["post_expiry"]["SPY261218P00620000"][2], RECON_ANCHORS["SPY261218P00620000"][3])

    def test_expiry_day_sets_quote_the_contracts_on_their_last_session(self) -> None:
        for name in QUOTED_ON_EXPIRY_DAY:
            self.assertEqual(NOV20_EXPIRY, SET_FACTS[name]["quote_date"], name)
            for symbol in EXPIRED_BY_HOLIDAY:
                self.assertEqual([symbol], list(quotes_of(set_file(name, "option_quotes__%s.json" % symbol))))
                self.assertEqual(NOV20_EXPIRY, occ.parse(symbol).expiration)
        self.assertEqual(NOV20_EXPIRY, mc.last_trading_day(NOV20_EXPIRY, False))

    def test_holiday_set_omits_the_expired_contracts(self) -> None:
        for symbol in EXPIRED_BY_HOLIDAY:
            self.assertEqual({"quotes": {}}, read_json(set_file("holiday", "option_quotes__%s.json" % symbol)), symbol)
        three = quotes_of(set_file("holiday", "option_quotes__%s.json" % REQUIRED_OPTION_KEYS[-1]))
        self.assertEqual(["SPY261218P00620000"], list(three))
        self.assertEqual(sorted(UNDERLYINGS), list(quotes_of(set_file("holiday", "stock_quotes__NVDA,SPY,WMT.json"))))

    def test_regular_set_has_an_index_symbol_the_data_source_cannot_quote(self) -> None:
        self.assertEqual({"quotes": {}, "currency": "USD"}, read_json(set_file("regular", "stock_quotes__SPX.json")))
        pair = quotes_of(set_file("regular", "stock_quotes__SPX,SPY.json"))
        self.assertEqual(["SPY"], list(pair))
        self.assertEqual(stock_quote("regular", "SPY"), pair["SPY"])


class ContractTest(unittest.TestCase):
    def test_every_listed_contract_agrees_with_its_occ_symbol(self) -> None:
        for directory, name in [(set_dir(n), n) for n in SETS]:
            for path in files_named(directory, "contracts__"):
                payload = read_json(path)
                self.assertEqual(["option_contracts", "next_page_token"], list(payload), path)
                self.assertIsNone(payload["next_page_token"], path)
                for contract in payload["option_contracts"]:
                    tag = "%s %s" % (path, contract["symbol"])
                    self.assertEqual(CONTRACT_KEYS, list(contract), tag)
                    parsed = occ.parse(contract["symbol"])
                    self.assertEqual(parsed.compact, contract["symbol"], tag)
                    self.assertEqual(plain(parsed.strike), contract["strike_price"], tag)
                    self.assertEqual(parsed.expiration.isoformat(), contract["expiration_date"], tag)
                    self.assertEqual("call" if parsed.is_call else "put", contract["type"], tag)
                    self.assertEqual((parsed.root, parsed.root), (contract["root_symbol"], contract["underlying_symbol"]))
                    self.assertEqual(("active", True, "american", "100", "100"),
                                     (contract["status"], contract["tradable"], contract["style"],
                                      contract["multiplier"], contract["size"]), tag)
                    month = MONTHS[parsed.expiration.month - 1]
                    self.assertEqual("%s %s %02d %d %s %s" % (parsed.root, month, parsed.expiration.day,
                                                             parsed.expiration.year, plain(parsed.strike),
                                                             "Call" if parsed.is_call else "Put"),
                                     contract["name"], tag)
                    self.assertRegex(contract["id"], r"^[0-9a-f]{8}-[0-9a-f]{4}-5[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
                    expiration_check(self, name, parsed.expiration, tag)
                    if contract["open_interest"] is None:
                        self.assertEqual((None, None, None), (contract["open_interest_date"], contract["close_price"],
                                                              contract["close_price_date"]), tag)
                    else:
                        prior = mc.previous_trading_day(SET_FACTS[name]["quote_date"]).isoformat()
                        self.assertGreater(int(contract["open_interest"]), 0, tag)
                        self.assertEqual((prior, prior), (contract["open_interest_date"], contract["close_price_date"]))
                        self.assertGreater(dec(contract["close_price"]), 0, tag)

    def test_the_same_contract_is_identical_across_the_listings_of_one_set(self) -> None:
        for name in SETS:
            seen: Dict[str, dict] = {}
            for path in files_named(set_dir(name), "contracts__"):
                for contract in read_json(path)["option_contracts"]:
                    if contract["symbol"] in seen:
                        self.assertEqual(seen[contract["symbol"]], contract, "%s %s" % (path, contract["symbol"]))
                    seen[contract["symbol"]] = contract

    def test_contract_ids_are_unique_per_symbol(self) -> None:
        ids: Dict[str, str] = {}
        for path in files_named(ALPACA, "contracts__"):
            for contract in read_json(path)["option_contracts"]:
                self.assertEqual(ids.setdefault(contract["id"], contract["symbol"]), contract["symbol"], path)

class StampTest(unittest.TestCase):
    def test_regular_set_stamps_read_14_31_12_et_on_2026_11_03(self) -> None:
        count = 0
        for path in walk_files(set_dir("regular")):
            base = os.path.basename(path)
            if not base.startswith(("stock_quotes__", "option_quotes__", "snapshots__")):
                continue
            for where, value in iter_stamps(read_json(path)):
                if where.endswith("dailyBar.t"):
                    continue
                et = mc.to_et(parse_rfc(value))
                self.assertEqual("2026-11-03 14:31:12", et.strftime("%Y-%m-%d %H:%M:%S"), "%s %s" % (path, where))
                self.assertEqual(mc.EST, et.tzinfo, path)
                self.assertEqual(1793734272000, epoch_ms(parse_rfc(value)), path)
                count += 1
        self.assertGreater(count, 30)

    def test_every_set_stamps_read_as_documented(self) -> None:
        for name in SETS:
            facts = SET_FACTS[name]
            count = 0
            for path in walk_files(set_dir(name)):
                base = os.path.basename(path)
                if not base.startswith(("stock_quotes__", "option_quotes__", "snapshots__")):
                    continue
                for where, value in iter_stamps(read_json(path)):
                    instant = parse_rfc(value)
                    if where.endswith("dailyBar.t"):
                        self.assertEqual(et_midnight_utc(facts["quote_date"]), instant, "%s %s" % (path, where))
                        continue
                    et = mc.to_et(instant)
                    self.assertEqual(facts["stamp_et"], et.strftime("%Y-%m-%d %H:%M:%S"), "%s %s" % (path, where))
                    self.assertEqual(epoch_ms(facts["stamp"]), epoch_ms(instant), path)
                    self.assertEqual(rfc_z(facts["stamp"]), value, path)
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
            clock = read_json(set_file(name, "clock.json"))
            check_clock(self, clock, facts["now"], facts["is_open"], name)
            self.assertEqual(EPOCHS[name][1], epoch_s(parse_rfc(clock["timestamp"])), name)
        # The early close the holiday set's next session falls on: Friday 2026-11-27, 13:00 ET.
        self.assertEqual("2026-11-27T13:00:00-05:00", read_json(set_file("holiday", "clock.json"))["next_close"])
        self.assertEqual("2026-11-23T09:30:00-05:00", read_json(set_file("post_expiry", "clock.json"))["next_open"])

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




class ChainExpirationBarsCalendarTest(unittest.TestCase):
    def test_chains_are_a_listing_and_its_snapshots(self) -> None:
        for name in SETS:
            for symbol, expiration in CHAINS.items():
                tag = "%s %s" % (name, symbol)
                payload = read_json(set_file(name, "snapshots__%s__%s.json" % (symbol, expiration)))
                self.assertEqual(["snapshots", "next_page_token"], list(payload), tag)
                self.assertIsNone(payload["next_page_token"], tag)
                snapshots = payload["snapshots"]
                contracts = contracts_of(name, symbol, expiration)
                expired = name == "holiday" and date.fromisoformat(expiration) < SET_FACTS[name]["quote_date"]
                if expired:
                    self.assertEqual(({}, []), (snapshots, contracts), tag)
                    continue
                self.assertEqual(sorted(snapshots), list(snapshots), tag)
                self.assertEqual(sorted(c["symbol"] for c in contracts), list(snapshots), tag)
                self.assertEqual([c["symbol"] for c in contracts], sorted(c["symbol"] for c in contracts), tag)
                strikes = sorted({dec(c["strike_price"]) for c in contracts})
                self.assertEqual([Decimal(s) for s in CHAIN_STRIKES[symbol]], strikes, tag)
                for strike in strikes:
                    rights = sorted(c["type"] for c in contracts if dec(c["strike_price"]) == strike)
                    self.assertEqual(["call", "put"], rights, "%s %s" % (tag, strike))
                for option_symbol, snapshot in snapshots.items():
                    parsed = occ.parse(option_symbol)
                    self.assertEqual((symbol, expiration), (parsed.root, parsed.expiration.isoformat()))
                    self.assertEqual(SNAPSHOT_KEYS, list(snapshot), option_symbol)
                    self.assertEqual(OPTION_QUOTE_KEYS, list(snapshot["latestQuote"]), option_symbol)
                    self.assertEqual(TRADE_KEYS, list(snapshot["latestTrade"]), option_symbol)
                    check_bar_shape(self, snapshot["dailyBar"], "%s %s dailyBar" % (name, option_symbol))
                    quote = snapshot["latestQuote"]
                    self.assertLess(dec(quote["bp"]), dec(quote["ap"]), option_symbol)
                    self.assertGreaterEqual(dec(quote["bp"]), 0, option_symbol)
                    trade = snapshot["latestTrade"]
                    self.assertEqual(rfc_z(SET_FACTS[name]["stamp"]), trade["t"], option_symbol)
                    self.assertEqual(dec(trade["p"]), dec(snapshot["dailyBar"]["c"]), option_symbol)
                    self.assertGreater(trade["s"], 0, option_symbol)
                    self.assertNotIn("greeks", snapshot)  # vendor Greeks are ignored; none are recorded
                    self.assertNotIn("impliedVolatility", snapshot)

    def test_chain_anchor_equals_the_standalone_quote(self) -> None:
        for name in SETS:
            for symbol, expiration in CHAINS.items():
                anchor = ANCHOR_FOR[symbol]
                if name == "holiday" and anchor in EXPIRED_BY_HOLIDAY:
                    continue
                snapshot = snapshots_of(name, symbol, expiration)[anchor]
                self.assertEqual(option_quote(name, anchor), snapshot["latestQuote"], "%s %s" % (name, anchor))

    def test_chain_puts_and_calls_are_monotone_in_strike(self) -> None:
        for name in SETS:
            for symbol, expiration in CHAINS.items():
                snapshots = snapshots_of(name, symbol, expiration)
                for right, sign in ((occ.CALL, -1), (occ.PUT, 1)):
                    series = sorted((occ.parse(s).strike, mid_of(snap["latestQuote"])) for s, snap in snapshots.items()
                                    if occ.parse(s).right == right)
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
                spot = mid_of(stock_quote(name, underlying))
                anchor_symbol = ANCHOR_FOR[underlying]
                anchor = occ.parse(anchor_symbol)
                anchor_mid = mid_of(option_quote(name, anchor_symbol))
                snapshots = snapshots_of(name, underlying, CHAINS[underlying])
                self.assertEqual(10, len(snapshots), "%s %s" % (name, underlying))
                for symbol, snapshot in snapshots.items():
                    if symbol == anchor_symbol:
                        continue
                    parsed = occ.parse(symbol)
                    strike = parsed.strike
                    intrinsic = max(spot - strike, Decimal(0)) if parsed.is_call else max(strike - spot, Decimal(0))
                    expected = max(intrinsic, Decimal("0.01")) + Decimal("0.05")
                    further_otm = (parsed.is_call and anchor.is_call and strike > anchor.strike) or (
                        parsed.is_put and anchor.is_put and strike < anchor.strike)
                    if further_otm:
                        expected = min(expected, anchor_mid)
                    self.assertEqual(expected, mid_of(snapshot["latestQuote"]), "%s %s" % (name, symbol))
                    self.assertEqual(expected, dec(snapshot["latestTrade"]["p"]), symbol)
        # The SPY chain of the same set is still priced from a solved implied volatility (28 days left).
        snapshots = snapshots_of("post_expiry", "SPY", "2026-12-18")
        mids = {occ.parse(s).strike: mid_of(snap["latestQuote"]) for s, snap in snapshots.items() if occ.parse(s).is_put}
        self.assertGreater(mids[Decimal("630")] - mids[Decimal("620")], Decimal("0.50"))  # time value, not a flat tick

    def test_the_chain_listing_is_the_strikes_answer(self) -> None:
        """``option_strikes`` reads the same listing as the chain: the anchor strike is in it, and so is every
        snapshot's strike."""
        anchors = {"WMT": Decimal("105"), "NVDA": Decimal("150"), "SPY": Decimal("620")}
        for name in SETS:
            for symbol, expiration in CHAINS.items():
                contracts = contracts_of(name, symbol, expiration)
                if name == "holiday" and date.fromisoformat(expiration) < SET_FACTS[name]["quote_date"]:
                    self.assertEqual([], contracts)
                    continue
                strikes = {dec(c["strike_price"]) for c in contracts}
                self.assertIn(anchors[symbol], strikes, "%s %s" % (name, symbol))
                self.assertTrue({occ.parse(s).strike for s in snapshots_of(name, symbol, expiration)} <= strikes)
                for contract in contracts:
                    self.assertIsInstance(contract["strike_price"], str, "Alpaca sends strike_price as text")

    def test_expirations_listing(self) -> None:
        """``option_expirations(symbol, start)`` lists active calls from ``start`` (the ET date of --now) through
        ``start + 400`` days: one call per expiration here (the listed strike nearest the set's spot)."""
        for name in SETS:
            start = start_of(name)
            last = start + timedelta(days=EXPIRATIONS_WINDOW_DAYS)
            for symbol in UNDERLYINGS:
                tag = "%s %s" % (name, symbol)
                payload = read_json(set_file(name, listing_name(name, symbol)))
                self.assertEqual(["option_contracts", "next_page_token"], list(payload), tag)
                self.assertIsNone(payload["next_page_token"])
                contracts = payload["option_contracts"]
                dates = [date.fromisoformat(c["expiration_date"]) for c in contracts]
                self.assertGreaterEqual(len(dates), 6, tag)
                self.assertEqual(sorted(set(dates)), dates, "%s: one contract per expiration, ascending" % tag)
                spot = mid_of(stock_quote(name, symbol))
                strike = expected_listing_strike(symbol, spot)
                for contract, day in zip(contracts, dates):
                    self.assertEqual(("call", strike), (contract["type"], contract["strike_price"]), tag)
                    self.assertTrue(start <= day <= last, "%s %s" % (tag, day))
                    expiration_check(self, name, day, "%s %s" % (tag, day))
                    self.assertTrue(mc.is_trading_day(day), "%s %s" % (tag, day))
                iso = [d.isoformat() for d in dates]
                if name not in ("holiday", "post_expiry"):
                    self.assertIn(CHAINS[symbol], iso, tag)
                else:
                    self.assertNotIn("2026-11-20", iso)  # expired before the window's start
                    if symbol == "SPY":
                        self.assertIn("2026-12-18", iso)
                if name in ("t1", "expiry_day"):
                    self.assertEqual("2026-11-20", iso[0], tag)
                if name == "post_expiry":
                    # The window starts at Saturday's date: the expired November contracts are no longer listed.
                    self.assertEqual({"SPY": "2026-11-23", "WMT": "2026-11-27", "NVDA": "2026-11-27"}[symbol], iso[0], tag)
                self.assertNotIn("2028-01-21", iso, "%s: beyond the 400-day window" % tag)

    def test_bars_are_identical_in_every_set(self) -> None:
        for symbol in UNDERLYINGS:
            rel = "bars__%s__%s__%s.json" % ((symbol,) + HISTORY_RANGE)
            reference = read_bytes(set_file("regular", rel))
            bars = bars_of(set_file("regular", rel), symbol)
            self.assertEqual([expected_bar(spec) for spec in HISTORY[symbol]], [bar_tuple(b) for b in bars], symbol)
            for bar in bars:
                check_bar_shape(self, bar, "%s %s" % (symbol, bar["t"]))
                self.assertTrue(mc.is_trading_day(mc.to_et(parse_rfc(bar["t"])).date()), bar["t"])
            # Daylight time ended 2026-11-01: the October bars are 04:00Z, the November bar 05:00Z.
            self.assertEqual("2026-10-27T04:00:00Z", bars[0]["t"])
            self.assertEqual("2026-11-02T05:00:00Z", bars[-1]["t"])
            for name in SETS:
                self.assertEqual(reference, read_bytes(set_file(name, rel)), "%s %s" % (name, symbol))

    def test_holiday_set_carries_the_expiration_day_closes(self) -> None:
        for symbol, expected_close in (("WMT", Decimal("104.86")), ("NVDA", Decimal("153.40"))):
            bars = bars_of(set_file("holiday", "bars__%s__2026-11-20__2026-11-20.json" % symbol), symbol)
            self.assertEqual(1, len(bars))  # Alpaca always answers a list, one bar or several
            self.assertEqual("2026-11-20T05:00:00Z", bars[0]["t"])
            self.assertEqual(expected_close, dec(bars[0]["c"]))
            self.assertEqual(expected_bar(HOLIDAY_SINGLE_BARS[symbol]), bar_tuple(bars[0]), symbol)  # PR 2a values
            check_bar_shape(self, bars[0], symbol)

    def test_holiday_set_two_bar_files(self) -> None:
        """2026-11-20 (the expiration day) and 2026-11-23 (the next session, whose open is the
        after-hours-crossing proxy of the reconciliation)."""
        for symbol in ("WMT", "NVDA"):
            bars = bars_of(set_file("holiday", "bars__%s__2026-11-20__2026-11-23.json" % symbol), symbol)
            self.assertEqual([expected_bar(b) for b in TWO_BAR_HISTORY[symbol]], [bar_tuple(b) for b in bars], symbol)
            for bar in bars:
                check_bar_shape(self, bar, symbol)
            single = bars_of(set_file("holiday", "bars__%s__2026-11-20__2026-11-20.json" % symbol), symbol)
            self.assertEqual(dec(single[0]["c"]), dec(bars[0]["c"]), symbol)
            self.assertEqual(mc.next_trading_day(NOV20_EXPIRY).isoformat(), bar_tuple(bars[1])[0])
        wmt = bars_of(set_file("holiday", "bars__WMT__2026-11-20__2026-11-23.json"), "WMT")
        self.assertLessEqual(abs(dec(wmt[0]["c"]) - Decimal("105")), Decimal("0.25"))
        self.assertGreater(dec(wmt[1]["o"]), Decimal("105"))
        nvda = bars_of(set_file("holiday", "bars__NVDA__2026-11-20__2026-11-23.json"), "NVDA")
        self.assertEqual(Decimal("3.40"), dec(nvda[0]["c"]) - Decimal("150"))
        self.assertGreater(dec(nvda[1]["o"]), Decimal("150"))

    def test_post_expiry_set_carries_the_expiration_day_bars(self) -> None:
        for symbol, spec in POST_EXPIRY_BARS.items():
            bars = bars_of(set_file("post_expiry", "bars__%s__2026-11-20__2026-11-20.json" % symbol), symbol)
            self.assertEqual([expected_bar(spec)], [bar_tuple(b) for b in bars], symbol)
            check_bar_shape(self, bars[0], symbol)
        self.assertEqual(Decimal("0.60"), Decimal("105") - Decimal(POST_EXPIRY_BARS["WMT"][4]))
        self.assertEqual(Decimal("3.40"), Decimal(POST_EXPIRY_BARS["NVDA"][4]) - Decimal("150"))
        self.assertGreater(Decimal("0.60"), Decimal("0.25"))  # outside the pin band: a clean EXPIRED_OTM

    def test_calendar_2026_11(self) -> None:
        reference = read_bytes(set_file("regular", "calendar__2026-11-01__2026-11-30.json"))
        days = read_json(set_file("regular", "calendar__2026-11-01__2026-11-30.json"))
        listed = [date.fromisoformat(day["date"]) for day in days]
        trading = [date(2026, 11, n) for n in range(1, 31) if mc.is_trading_day(date(2026, 11, n))]
        self.assertEqual(trading, listed, "Alpaca lists trading days only")
        self.assertNotIn(date(2026, 11, 26), listed)  # Thanksgiving
        for day in days:
            d = date.fromisoformat(day["date"])
            self.assertEqual(CALENDAR_KEYS, list(day), day["date"])
            close = mc.session_close_et(d).strftime("%H:%M")
            self.assertEqual(("09:30", close), (day["open"], day["close"]), day["date"])
            self.assertEqual("0400", day["session_open"], day["date"])
            self.assertEqual("2000" if close == "16:00" else "1700", day["session_close"], day["date"])
            self.assertEqual(mc.next_trading_day(d).isoformat(), day["settlement_date"], day["date"])
        self.assertEqual("13:00", [d for d in days if d["date"] == "2026-11-27"][0]["close"])  # the early close
        for name in SETS:
            self.assertEqual(reference, read_bytes(set_file(name, "calendar__2026-11-01__2026-11-30.json")), name)


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
        for rel in ("desk.toml", "positions.csv", "research/calendar.toml", "fixtures/alpaca/README.md",
                    "fixtures/alpaca/_generate.py", "fixtures/alpaca/recon/clock.json"):
            self.assertTrue(os.path.isfile(os.path.join(RECON, *rel.split("/"))), rel)
        for rel in ("state", "data", "journal", "levels", "reports", "incidents"):
            self.assertFalse(os.path.exists(os.path.join(RECON, rel)), rel)
        # Decision 2 of the PR 2c addendum: the adopted example rulebook is committed, and nothing else under rules/.
        self.assertEqual(["ADOPTED", "CHANGELOG.md", "exit_rules-1.0.0.toml"],
                         sorted(os.listdir(os.path.join(RECON, "rules"))))
        for name in ("ADOPTED", "CHANGELOG.md", "exit_rules-1.0.0.toml"):
            self.assertEqual(read_bytes(os.path.join(DESK, "rules", name)), read_bytes(os.path.join(RECON, "rules", name)),
                             name)
        # Exactly the files the reconciliation reads, nothing else, in the recorded set.
        found = sorted(os.path.relpath(p, RECON_SET).replace(os.sep, "/") for p in walk_files(RECON_SET))
        self.assertEqual(sorted(["bars__AMD__2026-11-20__2026-11-23.json", "bars__NVDA__2026-11-20__2026-11-23.json",
                                 "bars__WMT__2026-11-20__2026-11-23.json", "clock.json",
                                 "option_quotes__SPY261218P00620000.json", "stock_quotes__SPY.json"]
                                + list(ERROR_FILES)), found)

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
        clock = read_json(os.path.join(RECON_SET, "clock.json"))
        check_clock(self, clock, RECON_FACTS["now"], False, "recon")
        self.assertEqual(RECON_EPOCHS[1], epoch_s(parse_rfc(clock["timestamp"])))
        self.assertEqual(("2026-11-24T07:00:00-05:00", "2026-11-24T09:30:00-05:00", "2026-11-24T16:00:00-05:00"),
                         (clock["timestamp"], clock["next_open"], clock["next_close"]))

    def test_recon_set_quotes(self) -> None:
        stock = read_json(os.path.join(RECON_SET, "stock_quotes__SPY.json"))
        option = read_json(os.path.join(RECON_SET, "option_quotes__SPY261218P00620000.json"))
        self.assertEqual(["quotes", "currency"], list(stock))
        self.assertEqual(["quotes"], list(option))
        self.assertEqual(["SPY"], list(stock["quotes"]))
        self.assertEqual(["SPY261218P00620000"], list(option["quotes"]))
        seen = {"SPY": stock["quotes"]["SPY"], "SPY261218P00620000": option["quotes"]["SPY261218P00620000"]}
        self.assertEqual(STOCK_QUOTE_KEYS, list(seen["SPY"]))
        self.assertEqual(OPTION_QUOTE_KEYS, list(seen["SPY261218P00620000"]))
        for symbol, quote in seen.items():
            self.assertEqual(RECON_EPOCHS[0], epoch_ms(parse_rfc(quote["t"])), symbol)
            self.assertEqual(RECON_FACTS["stamp_et"], mc.to_et(parse_rfc(quote["t"])).strftime("%Y-%m-%d %H:%M:%S"))
            bid, ask = RECON_ANCHORS[symbol][:2]
            self.assertEqual((Decimal(bid), Decimal(ask)), (dec(quote["bp"]), dec(quote["ap"])), symbol)
            self.assertLess(dec(quote["bp"]), dec(quote["ap"]), symbol)

    def test_recon_set_bars(self) -> None:
        for symbol, bars in TWO_BAR_HISTORY.items():
            recorded = bars_of(os.path.join(RECON_SET, "bars__%s__2026-11-20__2026-11-23.json" % symbol), symbol)
            self.assertEqual([expected_bar(b) for b in bars], [bar_tuple(b) for b in recorded], symbol)
            for bar in recorded:
                check_bar_shape(self, bar, symbol)
        # The holiday set of the main root carries the same WMT and NVDA two-bar files, byte for byte.
        for symbol in ("WMT", "NVDA"):
            rel = "bars__%s__2026-11-20__2026-11-23.json" % symbol
            self.assertEqual(read_bytes(set_file("holiday", rel)), read_bytes(os.path.join(RECON_SET, rel)), rel)
        # AMD: closed 1.00 in the money (231.00 vs 230) and Monday opened below the strike (229.40):
        # PIN_UNCERTAIN by the crossing rule, never PROBABLE_ASSIGNED.
        amd = TWO_BAR_HISTORY["AMD"]
        self.assertGreater(Decimal(amd[0][4]), Decimal("230.25"))
        self.assertLess(Decimal(amd[1][1]), Decimal("230"))
        # No AAPL bars: the AAPL puts are EXPIRED_UNRECORDED.
        self.assertFalse(os.path.exists(os.path.join(RECON_SET, "bars__AAPL__2026-11-20__2026-11-23.json")))

    def test_recon_set_errors_match_the_main_root(self) -> None:
        for rel in ERROR_FILES:
            self.assertEqual(read_bytes(set_file("regular", rel)), read_bytes(os.path.join(RECON_SET, rel)), rel)

    def test_recon_readme_states_the_instants_and_the_outcomes(self) -> None:
        text = read_text(RECON_README)
        for needle in ("`recon`", str(RECON_EPOCHS[0]), str(RECON_EPOCHS[1]), "2026-11-24T12:00:00Z",
                       RECON_FACTS["stamp_et"], "2026-11-23T20:59:50Z", "2026-11-24T07:00:00-05:00", "is_open",
                       "6523.17", "23.17", "EXPIRED_OTM", "PIN_UNCERTAIN", "PROBABLE_ASSIGNED", "CASH_SETTLED",
                       "SETTLEMENT_UNKNOWN", "EXPIRED_UNRECORDED", "UNAVAILABLE", "synthetic", "stock_quotes__",
                       "option_quotes__", "bars__", "clock.json", "_generate.py", "651.90", "651.94", "651.92",
                       "650.02", "4.05", "4.25", "4.15", "104.86", "105.30", "153.40", "152.90", "231.00", "229.40"):
            self.assertIn(needle, text, needle)
        for trade_id in sorted(set(RECON_TRADE_IDS)):
            self.assertIn(trade_id, text, trade_id)
        self.assertNotIn("Bearer", text)


def structuring_snapshots(name: str, underlying: str, expiration: str) -> Dict[str, Dict[str, Any]]:
    return snapshots_of(name, underlying, expiration)


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return date(year, month, 1 + (4 - first.weekday()) % 7 + 14)


def to_tick(value: float) -> Decimal:
    """``max(0.05, half-even(value / 0.05) x 0.05)`` to the cent: the chains' model-price rounding."""
    ticks = (Decimal(repr(value)) / Decimal("0.05")).quantize(Decimal("1"), rounding=ROUND_HALF_EVEN)
    return max(ticks * Decimal("0.05"), Decimal("0.05")).quantize(Decimal("0.01"))


def cutoff_16_et(expiration: str) -> datetime:
    return mc.from_et(datetime.combine(date.fromisoformat(expiration), mc.REGULAR_CLOSE_ET))


def resolved_sigma0(underlying: str, sigma_set: str) -> float:
    """sigma0 solved here from the sigma set's own anchor quote and spot (q = 0), independently of the generator."""
    anchor = ANCHOR_FOR[underlying]
    parsed = occ.parse(anchor)
    option = option_quote(sigma_set, anchor)
    spot = stock_quote(sigma_set, underlying)
    tau = (cutoff_16_et(parsed.expiration.isoformat()) - SET_FACTS[sigma_set]["stamp"]).total_seconds() / (365 * 86400)
    solved = iv.implied_vol(float(mid_of(option)), "CALL" if parsed.is_call else "PUT", float(mid_of(spot)),
                            float(parsed.strike), tau, STRUCTURING_RATE, 0.0)
    assert solved.ok, (underlying, sigma_set, solved.status)
    return solved.sigma


# The generator's book (not in a quote file: no stock snapshot endpoint is allow-listed): the prior close and the
# day's open of each underlying, the spots the structuring chains' prevclose and open are priced at.
SPOT_BOOK = {("regular", "WMT"): ("103.80", "103.95"), ("regular", "NVDA"): ("147.60", "147.80"),
             ("regular", "SPY"): ("645.90", "646.40"), ("expiry_day", "WMT"): ("106.43", "105.10"),
             ("expiry_day", "NVDA"): ("149.22", "150.20")}


class StructuringChainTest(unittest.TestCase):
    """The structuring chains of PR 3b (designer Q's D1 and D2, addendum S12), checked independently of the
    generator: present only where listed, the recorded shape, listed standard monthlies, re-priced here from the
    stated surface with sigma0 re-solved from the anchor quote, put-call parity, the calendar order, and the bytes."""

    def test_present_only_where_listed(self) -> None:
        anchors = {"%s__%s" % item for item in CHAINS.items()}
        for name in SETS:
            with self.subTest(name=name):
                found = {os.path.basename(p)[len("snapshots__"):-len(".json")]
                         for p in files_named(set_dir(name), "snapshots__")}
                listed = {"%s__%s" % key for key in STRUCTURING_CHAINS.get(name, {})}
                self.assertEqual(anchors | listed, found)
                chain_listings = {os.path.basename(p)[len("contracts__"):-len(".json")]
                                  for p in files_named(set_dir(name), "contracts__") if p.count("__") == 2}
                self.assertEqual(anchors | listed, chain_listings)
                self.assertNotIn("SPY__2027-01-15", found, "designer Q's D2: SPY's chain is 2027-03-19")

    def test_the_recorded_shape(self) -> None:
        for name, chains in STRUCTURING_CHAINS.items():
            stamp = rfc_z(SET_FACTS[name]["stamp"])
            for (underlying, expiration), count in chains.items():
                with self.subTest(name=name, chain=(underlying, expiration)):
                    snapshots = structuring_snapshots(name, underlying, expiration)
                    contracts = contracts_of(name, underlying, expiration)
                    self.assertEqual(count, len(snapshots))
                    self.assertEqual(sorted(snapshots), [c["symbol"] for c in contracts])
                    strikes = [Decimal(s) for s in STRUCTURING_STRIKES[underlying]]
                    self.assertEqual(sorted(strikes * 2), sorted(occ.parse(s).strike for s in snapshots))
                    for symbol, snapshot in snapshots.items():
                        self.assertEqual(SNAPSHOT_KEYS, list(snapshot), symbol)
                        quote = snapshot["latestQuote"]
                        self.assertEqual(stamp, quote["t"])
                        self.assertEqual(stamp, snapshot["latestTrade"]["t"])
                        self.assertLessEqual(0, dec(quote["bp"]))
                        self.assertLess(dec(quote["bp"]), dec(quote["ap"]))
                        self.assertEqual(mid_of(quote), dec(snapshot["latestTrade"]["p"]), symbol)
                        parsed = occ.parse(symbol)
                        self.assertEqual((underlying, expiration), (parsed.root, parsed.expiration.isoformat()))
                        self.assertGreater(snapshot["dailyBar"]["v"], 0)
                    for contract in contracts:
                        self.assertGreater(int(contract["open_interest"]), 0)
                    for right, sign in ((occ.CALL, -1), (occ.PUT, 1)):
                        mids = [mid_of(snapshots[s]["latestQuote"]) for s in
                                sorted((s for s in snapshots if occ.parse(s).right == right),
                                       key=lambda s: occ.parse(s).strike)]
                        for earlier, later in zip(mids, mids[1:]):
                            self.assertTrue(earlier >= later if sign < 0 else earlier <= later, (right, mids))

    def test_dates_are_listed_standard_monthlies(self) -> None:
        for name, chains in STRUCTURING_CHAINS.items():
            for underlying, expiration in chains:
                with self.subTest(name=name, chain=(underlying, expiration)):
                    listed = read_json(set_file(name, listing_name(name, underlying)))["option_contracts"]
                    self.assertIn(expiration, [c["expiration_date"] for c in listed])
                    day = date.fromisoformat(expiration)
                    self.assertEqual(day, mc.settlement_day(third_friday(day.year, day.month), False))
                    self.assertGreater(day, SET_FACTS[name]["quote_date"])

    def test_independent_re_pricing_at_each_sets_stamp(self) -> None:
        for name, chains in STRUCTURING_CHAINS.items():
            stamp = SET_FACTS[name]["stamp"]
            for underlying, expiration in chains:
                sigma0 = resolved_sigma0(underlying, STRUCTURING_SIGMA_SET[name])
                spot = float(mid_of(stock_quote(name, underlying)))
                s_prev, s_open = (float(Decimal(v)) for v in SPOT_BOOK[(name, underlying)])
                tau = (cutoff_16_et(expiration) - stamp).total_seconds() / (365 * 86400)
                skew = STRUCTURING_SKEW[underlying]
                contracts = {c["symbol"]: c for c in contracts_of(name, underlying, expiration)}

                def surface(strike: float, at: float) -> float:
                    return max(0.05, sigma0 - skew * math.log(strike / at))

                for symbol, snapshot in structuring_snapshots(name, underlying, expiration).items():
                    with self.subTest(name=name, symbol=symbol):
                        parsed = occ.parse(symbol)
                        right = "CALL" if parsed.is_call else "PUT"
                        strike = float(parsed.strike)
                        quote = snapshot["latestQuote"]
                        mid = to_tick(bsm.price(right, spot, strike, tau, STRUCTURING_RATE, 0.0, surface(strike, spot)))
                        half = Decimal("0.10") if mid >= 5 else Decimal("0.05")
                        self.assertEqual((max(mid - half, Decimal("0")), mid + half), (dec(quote["bp"]), dec(quote["ap"])))
                        self.assertEqual(mid, mid_of(quote))
                        self.assertEqual(to_tick(bsm.price(right, s_prev, strike, tau + 1.0 / 365.0, STRUCTURING_RATE, 0.0,
                                                           surface(strike, s_prev))), dec(contracts[symbol]["close_price"]))
                        self.assertEqual(to_tick(bsm.price(right, s_open, strike, tau, STRUCTURING_RATE, 0.0,
                                                           surface(strike, s_open))), dec(snapshot["dailyBar"]["o"]))

    def test_put_call_parity(self) -> None:
        worst = Decimal("0")
        for name, chains in STRUCTURING_CHAINS.items():
            stamp = SET_FACTS[name]["stamp"]
            for underlying, expiration in chains:
                spot = mid_of(stock_quote(name, underlying))
                tau = (cutoff_16_et(expiration) - stamp).total_seconds() / (365 * 86400)
                snapshots = structuring_snapshots(name, underlying, expiration)
                mids = {(occ.parse(s).right, occ.parse(s).strike): mid_of(snap["latestQuote"])
                        for s, snap in snapshots.items()}
                for strike in {k for _, k in mids}:
                    call, put = mids[(occ.CALL, strike)], mids[(occ.PUT, strike)]
                    forward = spot - strike * Decimal(repr(math.exp(-STRUCTURING_RATE * tau)))
                    residual = abs(call - put - forward)
                    worst = max(worst, residual)
                    self.assertLessEqual(residual, Decimal("0.05"), (name, underlying, expiration, strike))
        self.assertGreater(worst, 0, "the ticks leave a residual")

    def test_the_later_expiration_is_worth_more(self) -> None:
        for underlying in ("WMT", "NVDA"):
            spot = mid_of(stock_quote("regular", underlying))
            december = {(occ.parse(s).right, occ.parse(s).strike): mid_of(snap["latestQuote"])
                        for s, snap in structuring_snapshots("regular", underlying, "2026-12-18").items()}
            for symbol, snapshot in structuring_snapshots("regular", underlying, "2027-01-15").items():
                parsed = occ.parse(symbol)
                key = (parsed.right, parsed.strike)
                if parsed.is_put and parsed.strike >= spot:
                    continue  # a deep in-the-money European put can lose value with time (r > 0)
                with self.subTest(underlying=underlying, contract=key):
                    self.assertGreaterEqual(mid_of(snapshot["latestQuote"]), december[key])

    def test_the_bytes(self) -> None:
        for (name, underlying, expiration), (snapshots_digest, contracts_digest) in STRUCTURING_SHA256.items():
            with self.subTest(name=name, chain=(underlying, expiration)):
                for kind, digest in (("snapshots", snapshots_digest), ("contracts", contracts_digest)):
                    path = set_file(name, "%s__%s__%s.json" % (kind, underlying, expiration))
                    self.assertEqual(digest, hashlib.sha256(read_bytes(path)).hexdigest(), path)
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
            self.assertEqual(Decimal(spot), mid_of(stock_quote(name, underlying)))
            self.assertIn("snapshots__%s__%s.json" % (underlying, expiration), text)
        for needle in ("## Structuring chains", "no anchor contract", "max(0.05, sigma0 - skew x ln(K / S))",
                       "0.25 (WMT), 0.25 (NVDA) and 0.50 (SPY)", "`q` = 0", "`r` = 0.0412", "2027-03-19 16:00 ET = 20:00Z",
                       "Each anchor chain holds five strikes", "no file", "0.9547", "0.3168"):
            self.assertIn(needle, text, needle)
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
            self.assertIn(rfc_z(facts["stamp"]), text, name)
            self.assertIn(facts["stamp_et"], text, name)
            self.assertIn(listing_name(name, "SPY")[len("contracts__SPY__"):-len(".json")], text, name)
        for needle in ("stock_quotes__", "option_quotes__", "snapshots__", "contracts__", "bars__", "clock.json",
                       "calendar__", "__page-", "sorted", "upper", "fixture_file_name", "synthetic", "indicative",
                       "iex", "/v2/stocks/quotes/latest", "/v1beta1/options/quotes/latest",
                       "/v1beta1/options/snapshots/", "/v2/options/contracts", "/v2/stocks/bars", "/v2/clock",
                       "/v2/calendar", "data.alpaca.markets", "paper-api.alpaca.markets", "400", "no offline fixture"):
            self.assertIn(needle, text, needle)
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
        for needle in ("1795208400", "2026-11-20T21:00:00Z", "max(intrinsic, 0.01) + 0.05", "2026-11-20__2026-11-23",
                       "post_expiry", "expiry_day", "t1", "Saturday", "typical price"):
            self.assertIn(needle, text, needle)

    def test_readme_names_every_file_kind_present(self) -> None:
        text = read_text(README)
        for path in walk_files(ALPACA):
            rel = os.path.relpath(path, ALPACA).replace(os.sep, "/")
            if rel in ("README.md", "_generate.py"):
                continue
            base = os.path.basename(rel)
            kind = base.split("__")[0] if "__" in base else base
            self.assertIn(kind, text, rel)


class ClientRoundTripTest(unittest.TestCase):
    """Every request the tools make, replayed through ``alpaca_client.OfflineTransport`` on a temporary copy of the
    two trees (nothing under ``tests/fixtures`` is read by the client in place): each resolves to a file and
    normalises to the book's values."""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.TemporaryDirectory(prefix="aegis-alpaca-fixtures-")
        cls.main = os.path.join(cls._tmp.name, "main")
        cls.recon = os.path.join(cls._tmp.name, "recon")
        shutil.copytree(ALPACA, cls.main)
        shutil.copytree(RECON_ALPACA, cls.recon)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def client(self, root: str, name: str) -> alpaca_client.AlpacaClient:
        transport = alpaca_client.OfflineTransport(root, name)
        return alpaca_client.AlpacaClient("paper", None, transport=transport, user_agent="aegis-desk-tests/0")

    def test_the_offline_receipt_instant_is_the_sets_now(self) -> None:
        for name in SETS:
            self.assertEqual(SET_FACTS[name]["now"], alpaca_client.OfflineTransport(self.main, name).receipt_utc, name)
        self.assertEqual(RECON_FACTS["now"], alpaca_client.OfflineTransport(self.recon, "recon").receipt_utc)

    def test_every_quote_batch(self) -> None:
        for name in SETS:
            client = self.client(self.main, name)
            stamp = SET_FACTS[name]["stamp"]
            for batch in QUOTE_BATCHES + (REGULAR_ONLY_BATCHES if name == "regular" else ()):
                with self.subTest(name=name, batch=batch):
                    result = client.quotes(list(batch))
                    gaps = tuple(s for s in batch if s == "SPX" or (name == "holiday" and s in EXPIRED_BY_HOLIDAY))
                    self.assertEqual(sorted(set(batch) - set(gaps)), sorted(result.quotes))
                    self.assertEqual(sorted(gaps), sorted(result.not_returned))
                    self.assertEqual([], result.unmatched)
                    stocks, options = split_batch(batch)
                    self.assertEqual(int(bool(stocks)) + int(bool(options)), len(result.receipts))
                    for symbol, quote in result.quotes.items():
                        raw = (stock_quote if symbol in UNDERLYINGS else option_quote)(name, symbol)
                        self.assertEqual((dec(raw["bp"]), dec(raw["ap"]), raw["bs"], raw["as"]),
                                         (quote.bid, quote.ask, quote.bidsize, quote.asksize))
                        self.assertEqual((stamp, stamp), (quote.bid_date, quote.ask_date))
        recon = self.client(self.recon, "recon")
        for batch in RECON_BATCHES:
            result = recon.quotes(list(batch))
            self.assertEqual(sorted(batch), sorted(result.quotes))
            for quote in result.quotes.values():
                self.assertEqual(RECON_FACTS["stamp"], quote.bid_date)

    def test_every_chain_and_its_strikes(self) -> None:
        for name in SETS:
            client = self.client(self.main, name)
            chains = dict(((u, e), None) for u, e in CHAINS.items())
            chains.update(STRUCTURING_CHAINS.get(name, {}))
            for underlying, expiration in chains:
                with self.subTest(name=name, chain=(underlying, expiration)):
                    day = date.fromisoformat(expiration)
                    result = client.option_chain(underlying, day)
                    self.assertEqual(2, len(result.receipts))  # the listing, then the snapshots
                    strikes = client.option_strikes(underlying, day).strikes
                    if name == "holiday" and day < SET_FACTS[name]["quote_date"]:
                        self.assertEqual(([], []), (result.options, strikes))
                        continue
                    self.assertEqual(sorted({o.strike for o in result.options}), strikes)
                    for option in result.options:
                        self.assertEqual((underlying, day, 100), (option.underlying, option.expiration_date,
                                                                  option.contract_size))
                        self.assertEqual(SET_FACTS[name]["stamp"], option.bid_date)
                        self.assertGreater(option.open_interest, 0)
                        self.assertIsNotNone(option.last)
                        self.assertIsNone(option.greeks)
                    anchor = ANCHOR_FOR[underlying]
                    if (underlying, expiration) in CHAINS.items() and not (name == "holiday" and anchor in
                                                                            EXPIRED_BY_HOLIDAY):
                        standalone = client.quotes([anchor]).quotes[anchor]
                        in_chain = [o for o in result.options if o.symbol == anchor][0]
                        self.assertEqual((standalone.bid, standalone.ask, standalone.bid_date),
                                         (in_chain.bid, in_chain.ask, in_chain.bid_date))

    def test_a_chain_without_a_file_is_a_network_failure(self) -> None:
        with self.assertRaises(alpaca_client.AlpacaError) as caught:
            self.client(self.main, "regular").option_chain("SPY", date(2027, 1, 15))
        self.assertEqual("network", caught.exception.status)
        self.assertIn("no offline fixture", str(caught.exception))

    def test_every_expirations_listing(self) -> None:
        for name in SETS:
            client = self.client(self.main, name)
            for symbol in UNDERLYINGS:
                with self.subTest(name=name, symbol=symbol):
                    result = client.option_expirations(symbol, start_of(name))
                    listed = read_json(set_file(name, listing_name(name, symbol)))["option_contracts"]
                    self.assertEqual([date.fromisoformat(c["expiration_date"]) for c in listed], result.dates)
                    self.assertEqual(1, len(result.receipts))

    def test_every_bars_file(self) -> None:
        cases = [(name, symbol, HISTORY_RANGE[0], HISTORY_RANGE[1]) for name in SETS for symbol in UNDERLYINGS]
        cases += [("holiday", s, "2026-11-20", e) for s in ("WMT", "NVDA") for e in ("2026-11-20", "2026-11-23")]
        cases += [("post_expiry", s, "2026-11-20", "2026-11-20") for s in ("WMT", "NVDA")]
        for name, symbol, first, last in cases:
            with self.subTest(name=name, symbol=symbol, range=(first, last)):
                bars = self.client(self.main, name).history(symbol, date.fromisoformat(first),
                                                            date.fromisoformat(last)).bars
                recorded = bars_of(set_file(name, "bars__%s__%s__%s.json" % (symbol, first, last)), symbol)
                self.assertEqual([bar_tuple(b) for b in recorded],
                                 [(b.date.isoformat(), b.open, b.high, b.low, b.close, b.volume) for b in bars])
        recon = self.client(self.recon, "recon")
        for symbol, specs in TWO_BAR_HISTORY.items():
            bars = recon.history(symbol, date(2026, 11, 20), date(2026, 11, 23)).bars
            self.assertEqual([expected_bar(s) for s in specs],
                             [(b.date.isoformat(), b.open, b.high, b.low, b.close, b.volume) for b in bars])

    def test_clock_and_calendar(self) -> None:
        for name in SETS:
            client = self.client(self.main, name)
            clock = client.clock()
            facts = SET_FACTS[name]
            self.assertEqual((facts["now"], "open" if facts["is_open"] else "closed", mc.to_et(facts["now"]).date()),
                             (clock.timestamp_utc, clock.state, clock.date), name)
            calendar = client.calendar(11, 2026)
            self.assertEqual(30, len(calendar.days), name)
            statuses = [day.status for day in calendar.days]
            self.assertEqual(["open" if mc.is_trading_day(date(2026, 11, n)) else "closed" for n in range(1, 31)],
                             statuses, name)
        recon = self.client(self.recon, "recon").clock()
        self.assertEqual((RECON_FACTS["now"], "closed", "open", "09:30"),
                         (recon.timestamp_utc, recon.state, recon.next_state, recon.next_change))


if __name__ == "__main__":
    unittest.main()
