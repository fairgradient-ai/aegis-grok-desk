"""Regenerate the synthetic Tradier fixture sets under this directory (data, not shipped code).

Run from the repository root::

    python3 tests/fixtures/desk/fixtures/tradier/_generate.py

Every epoch stamp is computed here with ``int(datetime(..., tzinfo=timezone.utc).timestamp() * 1000)``
(milliseconds for quotes, seconds for the clock) and listed in ``README.md`` beside this file. The
chain prices are Black-Scholes-Merton values at the implied volatility of each chain's anchor
contract (``aegis_desk.iv.implied_vol`` on the anchor mid, ``r`` = 0.0412, ``q`` = 0, tau from
the quote stamp to 16:00 ET on the expiration date on a 365-day year), rounded to the 0.05 tick,
so a chain is arbitrage-consistent with the quote it is tested against. When the anchor is under
one hour from its exercise cutoff (the ``post_expiry`` set: closing quotes 10 s before the close)
no implied volatility is solved, mirroring the book's ``expiring`` rule, and the neighbours are
written at ``max(intrinsic, 0.01) + 0.05`` (capped at the anchor mid for a contract further out of
the money than the anchor, so the chain stays monotone). The structuring chains of PR 3b hold no
anchor contract: they are priced from a stated implied-volatility surface around an anchor sigma
(:data:`STRUCTURING_CHAINS`, :func:`structuring_entries`; the README's structuring section). Nothing
here is live data; see the README's provenance section. ``tests/engine/test_desk_fixtures.py``
checks the output.
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any, Dict, List, Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(HERE)))))
sys.path.insert(0, os.path.join(REPO_ROOT, "scripts"))

from aegis_desk import bsm, iv, market_clock  # noqa: E402

UTC = timezone.utc
D = Decimal
CENT = D("0.01")
TICK = D("0.05")
R = 0.0412
Q = 0.0
SECONDS_PER_YEAR = 365 * 86400
EXPIRING_SECONDS = 3600  # under one hour to the cutoff: no implied volatility (the book's "expiring" rule)


def utc(*parts: int) -> datetime:
    return datetime(*parts, tzinfo=UTC)


def ms(moment: datetime) -> int:
    return int(moment.timestamp() * 1000)


def seconds(moment: datetime) -> int:
    return int(moment.timestamp())


def f(value: Any) -> float:
    """A JSON number from a Decimal or text (Tradier sends JSON numbers)."""
    return float(D(str(value)))


def pct_change(last: Decimal, prev: Decimal) -> Decimal:
    return ((last - prev) / prev * 100).quantize(CENT, rounding=ROUND_HALF_EVEN)


def to_tick(value: float) -> Decimal:
    ticks = (D(repr(value)) / TICK).quantize(D("1"), rounding=ROUND_HALF_EVEN)
    return max(ticks * TICK, TICK).quantize(CENT)


# --------------------------------------------------------------------------------------------
# Sets and the price books
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FixtureSet:
    name: str
    day: str                   # key into BOOKS: the trading day the quotes belong to
    quote_date: date
    stamp: datetime            # bid_date == ask_date == trade_date for every quote
    now: datetime              # the tools' --now; also the clock's timestamp
    clock_date: date
    clock_state: str
    clock_description: str
    next_change: str
    next_state: str
    close_set: bool            # the vendor's ``close`` field is populated only after the close
    activity: float            # volume scale against the day's book (intraday growth)


SETS: Tuple[FixtureSet, ...] = (
    FixtureSet("regular", "nov3", date(2026, 11, 3), utc(2026, 11, 3, 19, 31, 12), utc(2026, 11, 3, 19, 50, 0),
               date(2026, 11, 3), "open", "Market is open from 09:30 to 16:00", "16:00", "postmarket", False, 1.0),
    FixtureSet("stale", "nov3", date(2026, 11, 3), utc(2026, 11, 3, 18, 50, 0), utc(2026, 11, 3, 19, 50, 0),
               date(2026, 11, 3), "open", "Market is open from 09:30 to 16:00", "16:00", "postmarket", False, 0.87),
    FixtureSet("after_close", "nov3", date(2026, 11, 3), utc(2026, 11, 3, 20, 59, 40), utc(2026, 11, 3, 21, 25, 0),
               date(2026, 11, 3), "postmarket", "Market is in postmarket hours from 16:00 to 20:00", "20:00", "closed",
               True, 1.45),
    FixtureSet("pre_open", "nov2", date(2026, 11, 2), utc(2026, 11, 2, 20, 59, 55), utc(2026, 11, 3, 13, 0, 0),
               date(2026, 11, 3), "premarket", "Market is in premarket hours from 07:00 to 09:24", "09:30", "open",
               True, 1.0),
    FixtureSet("holiday", "nov25", date(2026, 11, 25), utc(2026, 11, 25, 20, 59, 50), utc(2026, 11, 26, 15, 0, 0),
               date(2026, 11, 26), "closed", "Market is closed", "07:00", "premarket", True, 1.0),
    # PR 2b: the November expiry week. t1 is the Thursday before the 2026-11-20 expiration (regular
    # hours), expiry_day the Friday in regular hours, post_expiry the Saturday 09:30 ET routine
    # reading Friday's closing quotes (stamped 10 s before the 16:00 ET close).
    FixtureSet("t1", "nov19", date(2026, 11, 19), utc(2026, 11, 19, 19, 31, 12), utc(2026, 11, 19, 19, 50, 0),
               date(2026, 11, 19), "open", "Market is open from 09:30 to 16:00", "16:00", "postmarket", False, 1.0),
    FixtureSet("expiry_day", "nov20_open", date(2026, 11, 20), utc(2026, 11, 20, 19, 31, 12), utc(2026, 11, 20, 19, 50, 0),
               date(2026, 11, 20), "open", "Market is open from 09:30 to 16:00", "16:00", "postmarket", False, 1.0),
    FixtureSet("post_expiry", "nov20_close", date(2026, 11, 20), utc(2026, 11, 20, 20, 59, 50), utc(2026, 11, 21, 14, 30, 0),
               date(2026, 11, 21), "closed", "Market is closed", "07:00", "premarket", True, 1.0),
)

UNDERLYING_META = {
    "SPY": {"description": "SPDR S&P 500 ETF Trust", "exch": "P", "type": "etf", "avg": 62_000_000,
            "w52h": "652.30", "w52l": "481.80", "bidsize": 300, "asksize": 200},
    "WMT": {"description": "Walmart Inc", "exch": "N", "type": "stock", "avg": 17_500_000,
            "w52h": "109.90", "w52l": "78.40", "bidsize": 400, "asksize": 300},
    "NVDA": {"description": "NVIDIA Corp", "exch": "Q", "type": "stock", "avg": 220_000_000,
             "w52h": "155.40", "w52l": "86.60", "bidsize": 500, "asksize": 400},
}
OPTION_META = {
    "WMT261120C00105000": {"underlying": "WMT", "strike": "105", "expiration": "2026-11-20", "right": "call"},
    "NVDA261120C00150000": {"underlying": "NVDA", "strike": "150", "expiration": "2026-11-20", "right": "call"},
    "SPY261218P00620000": {"underlying": "SPY", "strike": "620", "expiration": "2026-12-18", "right": "put"},
}

# One price book per trading day; every value is the text of a JSON number.
BOOKS: Dict[str, Dict[str, Dict[str, Any]]] = {
    "nov3": {
        "SPY": dict(bid="648.10", ask="648.14", last="648.12", prevclose="645.90", open="646.40", high="649.05",
                    low="645.70", volume=41_250_000),
        "WMT": dict(bid="104.20", ask="104.26", last="104.23", prevclose="103.80", open="103.95", high="104.60",
                    low="103.70", volume=9_800_000),
        "NVDA": dict(bid="148.90", ask="148.95", last="148.92", prevclose="147.60", open="147.80", high="149.70",
                     low="147.10", volume=165_000_000),
        "WMT261120C00105000": dict(bid="3.35", ask="3.50", last="3.40", prevclose="3.15", open="3.20", high="3.55",
                                   low="3.10", volume=1_240, oi=8_420, bidsize=112, asksize=95),
        "NVDA261120C00150000": dict(bid="3.10", ask="3.30", last="3.20", prevclose="2.85", open="2.90", high="3.40",
                                    low="2.80", volume=22_400, oi=61_300, bidsize=210, asksize=185),
        "SPY261218P00620000": dict(bid="9.80", ask="10.10", last="9.95", prevclose="10.45", open="10.30", high="10.60",
                                   low="9.70", volume=5_300, oi=148_000, bidsize=60, asksize=75),
    },
    "nov2": {
        "SPY": dict(bid="645.88", ask="645.92", last="645.90", prevclose="643.15", open="644.20", high="646.75",
                    low="642.90", volume=55_600_000),
        "WMT": dict(bid="103.78", ask="103.84", last="103.80", prevclose="103.25", open="103.30", high="104.10",
                    low="102.95", volume=13_900_000),
        "NVDA": dict(bid="147.58", ask="147.63", last="147.60", prevclose="146.10", open="146.50", high="148.20",
                     low="145.80", volume=225_000_000),
        "WMT261120C00105000": dict(bid="3.10", ask="3.25", last="3.15", prevclose="3.00", open="3.00", high="3.25",
                                   low="2.95", volume=1_520, oi=8_150, bidsize=140, asksize=120),
        "NVDA261120C00150000": dict(bid="2.75", ask="2.95", last="2.85", prevclose="2.60", open="2.65", high="2.95",
                                    low="2.55", volume=25_800, oi=60_900, bidsize=230, asksize=190),
        "SPY261218P00620000": dict(bid="10.30", ask="10.60", last="10.45", prevclose="11.10", open="11.00", high="11.20",
                                   low="10.25", volume=6_100, oi=147_200, bidsize=55, asksize=70),
    },
    "nov25": {
        "SPY": dict(bid="651.38", ask="651.44", last="651.40", prevclose="650.20", open="650.60", high="652.10",
                    low="649.80", volume=38_400_000),
        "WMT": dict(bid="105.92", ask="105.98", last="105.95", prevclose="105.60", open="105.70", high="106.20",
                    low="105.35", volume=7_900_000),
        "NVDA": dict(bid="152.27", ask="152.33", last="152.30", prevclose="151.80", open="152.00", high="153.10",
                     low="151.20", volume=148_000_000),
        "SPY261218P00620000": dict(bid="6.00", ask="6.20", last="6.10", prevclose="6.45", open="6.40", high="6.55",
                                   low="5.95", volume=3_800, oi=152_600, bidsize=80, asksize=95),
    },
    # Thursday 2026-11-19 (t1): the day before the November expiration.
    "nov19": {
        "SPY": dict(bid="652.10", ask="652.14", last="652.12", prevclose="650.80", open="651.00", high="652.60",
                    low="650.50", volume=39_800_000),
        "WMT": dict(bid="106.40", ask="106.46", last="106.43", prevclose="105.90", open="106.00", high="106.70",
                    low="105.80", volume=8_600_000),
        "NVDA": dict(bid="149.20", ask="149.25", last="149.22", prevclose="150.10", open="149.90", high="150.40",
                     low="148.80", volume=158_000_000),
        "WMT261120C00105000": dict(bid="1.52", ask="1.60", last="1.56", prevclose="1.25", open="1.30", high="1.70",
                                   low="1.20", volume=2_900, oi=9_100, bidsize=160, asksize=140),
        "NVDA261120C00150000": dict(bid="1.00", ask="1.10", last="1.05", prevclose="1.60", open="1.45", high="1.55",
                                    low="0.95", volume=31_500, oi=64_200, bidsize=240, asksize=210),
        "SPY261218P00620000": dict(bid="3.80", ask="4.00", last="3.90", prevclose="4.35", open="4.25", high="4.40",
                                   low="3.75", volume=4_900, oi=150_400, bidsize=70, asksize=85),
    },
    # Friday 2026-11-20 at 14:31 ET (expiry_day): the November contracts' last session, intraday.
    "nov20_open": {
        "SPY": dict(bid="650.00", ask="650.04", last="650.02", prevclose="652.12", open="651.80", high="652.40",
                    low="649.60", volume=36_500_000),
        "WMT": dict(bid="104.90", ask="104.96", last="104.93", prevclose="106.43", open="105.10", high="105.60",
                    low="104.70", volume=6_600_000),
        "NVDA": dict(bid="153.30", ask="153.36", last="153.33", prevclose="149.22", open="150.20", high="153.80",
                     low="149.90", volume=29_500_000),
        "WMT261120C00105000": dict(bid="0.12", ask="0.16", last="0.14", prevclose="1.56", open="0.55", high="0.65",
                                   low="0.10", volume=4_100, oi=9_300, bidsize=180, asksize=150),
        "NVDA261120C00150000": dict(bid="3.30", ask="3.42", last="3.36", prevclose="1.05", open="0.95", high="3.60",
                                    low="0.55", volume=38_900, oi=63_800, bidsize=120, asksize=95),
        "SPY261218P00620000": dict(bid="4.10", ask="4.30", last="4.20", prevclose="3.90", open="3.95", high="4.35",
                                   low="3.85", volume=5_600, oi=151_100, bidsize=65, asksize=80),
    },
    # Friday 2026-11-20 closing quotes (post_expiry): the WMT and NVDA bars of this day equal the
    # quotes' open/high/low/close/volume; the 105 call closed 0.60 out of the money, the 150 call
    # 3.40 in the money.
    "nov20_close": {
        "SPY": dict(bid="650.00", ask="650.04", last="650.02", prevclose="652.12", open="651.80", high="652.40",
                    low="649.60", volume=44_900_000),
        "WMT": dict(bid="104.38", ask="104.42", last="104.40", prevclose="106.43", open="105.10", high="105.60",
                    low="104.20", volume=9_100_000),
        "NVDA": dict(bid="153.38", ask="153.42", last="153.40", prevclose="149.22", open="150.20", high="153.80",
                     low="149.90", volume=41_000_000),
        "WMT261120C00105000": dict(bid="0.01", ask="0.05", last="0.03", prevclose="1.56", open="0.55", high="0.65",
                                   low="0.01", volume=6_200, oi=9_300, bidsize=400, asksize=350),
        "NVDA261120C00150000": dict(bid="3.30", ask="3.45", last="3.38", prevclose="1.05", open="0.95", high="3.60",
                                    low="0.55", volume=52_700, oi=63_800, bidsize=90, asksize=70),
        "SPY261218P00620000": dict(bid="4.10", ask="4.30", last="4.20", prevclose="3.90", open="3.95", high="4.35",
                                   low="3.85", volume=7_300, oi=151_100, bidsize=65, asksize=80),
    },
}

CHAIN_STRIKES = {
    "WMT": ["100", "102.5", "105", "107.5", "110"],
    "NVDA": ["140", "145", "150", "155", "160"],
    "SPY": ["600", "610", "620", "630", "640"],
}
CHAIN_EXPIRY = {"WMT": "2026-11-20", "NVDA": "2026-11-20", "SPY": "2026-12-18"}
ANCHOR_FOR = {"WMT": "WMT261120C00105000", "NVDA": "NVDA261120C00150000", "SPY": "SPY261218P00620000"}

LISTED_STRIKES = {
    "WMT": [85, 90, 95, 97.5, 100, 102.5, 105, 107.5, 110, 112.5, 115, 120],
    "NVDA": [120, 125, 130, 135, 140, 145, 150, 155, 160, 165, 170, 180],
    "SPY": [560, 580, 590, 600, 610, 620, 625, 630, 640, 650, 660, 680, 700],
}

EXPIRATIONS = {
    "nov3": {
        "SPY": ["2026-11-04", "2026-11-06", "2026-11-09", "2026-11-11", "2026-11-13", "2026-11-16", "2026-11-18",
                "2026-11-20", "2026-11-23", "2026-11-25", "2026-11-27", "2026-11-30", "2026-12-04", "2026-12-11",
                "2026-12-18", "2026-12-31", "2027-01-15", "2027-03-19", "2027-06-17", "2027-12-17", "2028-01-21"],
        "WMT": ["2026-11-06", "2026-11-13", "2026-11-20", "2026-11-27", "2026-12-04", "2026-12-18", "2027-01-15",
                "2027-02-19", "2027-03-19", "2027-06-17", "2028-01-21"],
        "NVDA": ["2026-11-06", "2026-11-13", "2026-11-20", "2026-11-27", "2026-12-04", "2026-12-11", "2026-12-18",
                 "2027-01-15", "2027-03-19", "2027-06-17", "2027-09-17", "2028-01-21"],
    },
    "nov25": {
        "SPY": ["2026-11-27", "2026-11-30", "2026-12-02", "2026-12-04", "2026-12-07", "2026-12-11", "2026-12-18",
                "2026-12-31", "2027-01-15", "2027-03-19", "2027-06-17", "2027-12-17", "2028-01-21"],
        "WMT": ["2026-11-27", "2026-12-04", "2026-12-11", "2026-12-18", "2027-01-15", "2027-02-19", "2027-03-19",
                "2027-06-17", "2028-01-21"],
        "NVDA": ["2026-11-27", "2026-12-04", "2026-12-11", "2026-12-18", "2027-01-15", "2027-03-19", "2027-06-17",
                 "2027-09-17", "2028-01-21"],
    },
}
EXPIRATIONS["nov2"] = EXPIRATIONS["nov3"]
EXPIRATIONS["nov19"] = {
    "SPY": ["2026-11-20", "2026-11-23", "2026-11-25", "2026-11-27", "2026-11-30", "2026-12-02", "2026-12-04",
            "2026-12-07", "2026-12-11", "2026-12-18", "2026-12-31", "2027-01-15", "2027-03-19", "2027-06-17",
            "2027-12-17", "2028-01-21"],
    "WMT": ["2026-11-20", "2026-11-27", "2026-12-04", "2026-12-11", "2026-12-18", "2027-01-15", "2027-02-19",
            "2027-03-19", "2027-06-17", "2028-01-21"],
    "NVDA": ["2026-11-20", "2026-11-27", "2026-12-04", "2026-12-11", "2026-12-18", "2027-01-15", "2027-03-19",
             "2027-06-17", "2027-09-17", "2028-01-21"],
}
EXPIRATIONS["nov20_open"] = EXPIRATIONS["nov20_close"] = EXPIRATIONS["nov19"]

HISTORY_RANGE = ("2026-10-27", "2026-11-02")
HISTORY = {
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
# Expiration-day bars (holiday set only): the official closes PR 2b's settlement check reads.
EXPIRY_DAY_BARS = {
    "WMT": ("2026-11-20", "104.60", "105.30", "104.20", "104.86", 18_700_000),   # |close - 105| = 0.14: pin band
    "NVDA": ("2026-11-20", "151.20", "153.90", "150.80", "153.40", 260_000_000),  # 150 call finishes 3.40 ITM
}
# Two-bar files (holiday set; the recon root's generator reuses them): the expiration day and the next
# session, whose open is the reconciliation's after-hours-crossing proxy. WMT closed 0.14 inside the
# 0.25 pin band and Monday opened across the strike; NVDA closed 3.40 in the money and stayed there.
TWO_BAR_HISTORY = {
    "WMT": [("2026-11-20", "104.60", "105.20", "104.30", "104.86", 9_100_000),
            ("2026-11-23", "105.30", "105.90", "104.95", "105.62", 8_400_000)],
    "NVDA": [("2026-11-20", "150.20", "153.80", "149.90", "153.40", 41_000_000),
             ("2026-11-23", "152.90", "154.40", "152.10", "154.10", 38_000_000)],
}
# post_expiry set: the expiration day alone (Saturday reads only Friday's bar). The WMT close of
# this world is 104.40 (the 105 call 0.60 out of the money, outside the pin band); NVDA 153.40.
POST_EXPIRY_BARS = {
    "WMT": ("2026-11-20", "105.10", "105.60", "104.20", "104.40", 9_100_000),
    "NVDA": ("2026-11-20", "150.20", "153.80", "149.90", "153.40", 41_000_000),
}

ERRORS = {
    "quota_violation.txt": '{"fault": {"faultstring": "Rate limit quota violation. Quota limit exceeded.", '
                           '"detail": {"errorcode": "policies.ratelimit.QuotaViolation"}}}\n',
    "invalid_token.txt": "Invalid Access Token\n",
}


# --------------------------------------------------------------------------------------------
# Quote builders (field order follows the recorded Tradier responses)
# --------------------------------------------------------------------------------------------


def equity_quote(symbol: str, book: Dict[str, Any], fixture: FixtureSet) -> Dict[str, Any]:
    meta = UNDERLYING_META[symbol]
    last, prev = D(book["last"]), D(book["prevclose"])
    stamp = ms(fixture.stamp)
    return {
        "symbol": symbol,
        "description": meta["description"],
        "exch": meta["exch"],
        "type": meta["type"],
        "last": f(last),
        "change": f(last - prev),
        "volume": int(round(book["volume"] * fixture.activity)),
        "open": f(book["open"]),
        "high": f(book["high"]),
        "low": f(book["low"]),
        "close": f(last) if fixture.close_set else None,
        "bid": f(book["bid"]),
        "ask": f(book["ask"]),
        "change_percentage": f(pct_change(last, prev)),
        "average_volume": meta["avg"],
        "last_volume": 100,
        "trade_date": stamp,
        "prevclose": f(prev),
        "week_52_high": f(meta["w52h"]),
        "week_52_low": f(meta["w52l"]),
        "bidsize": meta["bidsize"],
        "bidexch": meta["exch"],
        "bid_date": stamp,
        "asksize": meta["asksize"],
        "askexch": meta["exch"],
        "ask_date": stamp,
        "root_symbols": symbol,
        "open_interest": 0,
        "underlying": None,
        "strike": None,
        "contract_size": None,
        "expiration_date": None,
        "expiration_type": None,
        "option_type": None,
    }


def option_description(underlying: str, expiration: str, strike: Decimal, right: str) -> str:
    day = date.fromisoformat(expiration)
    return "%s %s $%s %s" % (underlying, day.strftime("%b %d %Y"), strike.quantize(CENT), right.capitalize())


def option_quote(symbol: str, underlying: str, expiration: str, strike: Decimal, right: str,
                 book: Dict[str, Any], fixture: FixtureSet) -> Dict[str, Any]:
    last, prev = D(book["last"]), D(book["prevclose"])
    stamp = ms(fixture.stamp)
    return {
        "symbol": symbol,
        "description": option_description(underlying, expiration, strike, right),
        "exch": "Z",
        "type": "option",
        "last": f(last),
        "change": f(last - prev),
        "volume": int(round(book["volume"] * fixture.activity)),
        "open": f(book["open"]),
        "high": f(book["high"]),
        "low": f(book["low"]),
        "close": f(last) if fixture.close_set else None,
        "bid": f(book["bid"]),
        "ask": f(book["ask"]),
        "underlying": underlying,
        "strike": f(strike),
        "change_percentage": f(pct_change(last, prev)),
        "average_volume": 0,
        "last_volume": 5,
        "trade_date": stamp,
        "prevclose": f(prev),
        "week_52_high": 0.0,
        "week_52_low": 0.0,
        "bidsize": book["bidsize"],
        "bidexch": "Z",
        "bid_date": stamp,
        "asksize": book["asksize"],
        "askexch": "Z",
        "ask_date": stamp,
        "open_interest": book["oi"],
        "contract_size": 100,
        "expiration_date": expiration,
        "expiration_type": "standard",
        "option_type": right,
        "root_symbol": underlying,
    }


def occ_compact(underlying: str, expiration: str, right: str, strike: Decimal) -> str:
    day = date.fromisoformat(expiration)
    return "%s%s%s%08d" % (underlying, day.strftime("%y%m%d"), "C" if right == "call" else "P",
                           int((strike * 1000).quantize(D("1"))))


def cutoff_utc(expiration: str) -> datetime:
    """16:00 ET on the expiration date (an EST date in Nov/Dec 2026) as the UTC instant."""
    return market_clock.from_et(datetime.combine(date.fromisoformat(expiration), market_clock.REGULAR_CLOSE_ET))


def fallback_value(right: str, spot: Decimal, strike: Decimal) -> Decimal:
    """``max(intrinsic, 0.01) + 0.05`` per share: the chain value when no implied volatility is solved."""
    intrinsic = max(spot - strike, D("0")) if right == "call" else max(strike - spot, D("0"))
    return (max(intrinsic, CENT) + TICK).quantize(CENT)


# PR 3b: the structuring chains. Set -> {underlying: (expirations, strikes)}: the regular set's five
# chains (the covered-call and roll windows of WMT and NVDA, the protection window of SPY) and two
# expiry_day chains (a replacement chosen on the expiration day of the November calls). They hold no
# anchor contract: each is priced from a stated implied-volatility surface around sigma0, skewed in
# log-moneyness and flat in maturity, with the spot, stamp and tau of its own set.
STRUCTURING_STRIKES = {
    "WMT": ("95", "97.5", "100", "102.5", "105", "107.5", "110", "112.5", "115", "120"),
    "NVDA": ("130", "135", "140", "145", "150", "155", "160", "165", "170", "180"),
    "SPY": ("580", "590", "600", "610", "620", "625", "630", "640", "650", "660", "680", "700"),
}
STRUCTURING_CHAINS: Dict[str, Dict[str, Tuple[Tuple[str, ...], Tuple[str, ...]]]] = {
    "regular": {
        "WMT": (("2026-12-18", "2027-01-15"), STRUCTURING_STRIKES["WMT"]),
        "NVDA": (("2026-12-18", "2027-01-15"), STRUCTURING_STRIKES["NVDA"]),
        "SPY": (("2027-03-19",), STRUCTURING_STRIKES["SPY"]),
    },
    "expiry_day": {
        "WMT": (("2027-01-15",), STRUCTURING_STRIKES["WMT"]),
        "NVDA": (("2027-01-15",), STRUCTURING_STRIKES["NVDA"]),
    },
}
# The set whose anchor quote fixes sigma0. On the expiration day the November anchors have 1 h 29 min
# to their cutoff and almost no time value (the NVDA 150 call's mid is 0.03 above intrinsic inside a
# 0.12 spread), so their implied volatilities (0.3168 and 0.9547) identify no level for a 56-day
# surface: the expiry_day chains keep the regular set's sigma0, flat in calendar time.
STRUCTURING_SIGMA_SET = {"regular": "regular", "expiry_day": "regular"}
SKEW = {"WMT": 0.25, "NVDA": 0.25, "SPY": 0.50}  # vol points per unit of ln(K / S)
SIGMA_FLOOR = 0.05


def anchor_sigma(underlying: str, fixture: FixtureSet, quotes: Dict[str, Dict[str, Any]]) -> float:
    """The implied volatility of the underlying's anchor contract at the set's stamp (q = 0)."""
    anchor_symbol = ANCHOR_FOR[underlying]
    anchor = quotes[anchor_symbol]
    meta = OPTION_META[anchor_symbol]
    spot = quotes[underlying]
    s_mid = float((D(str(spot["bid"])) + D(str(spot["ask"]))) / 2)
    tau = (cutoff_utc(meta["expiration"]) - fixture.stamp).total_seconds() / SECONDS_PER_YEAR
    mid = float((D(str(anchor["bid"])) + D(str(anchor["ask"]))) / 2)
    right = "CALL" if meta["right"] == "call" else "PUT"
    solved = iv.implied_vol(mid, right, s_mid, float(meta["strike"]), tau, R, Q)
    if not solved.ok:
        raise SystemExit("implied vol for %s in set %s: %s" % (anchor_symbol, fixture.name, solved.status))
    return solved.sigma


def surface_sigma(underlying: str, sigma0: float, strike: float, spot: float) -> float:
    """sigma(K) = max(0.05, sigma0 - skew x ln(K / S)): the stated surface of the structuring chains."""
    return max(SIGMA_FLOOR, sigma0 - SKEW[underlying] * math.log(strike / spot))


def structuring_entries(underlying: str, expiration: str, strikes: Tuple[str, ...], sigma0: float,
                        fixture: FixtureSet, quotes: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every strike of one structuring chain, a call and a put each, priced at the stated surface."""
    spot = quotes[underlying]
    s_mid = float((D(str(spot["bid"])) + D(str(spot["ask"]))) / 2)
    s_prev = float(D(str(spot["prevclose"])))
    s_open = float(D(str(spot["open"])))
    oi_base = quotes[ANCHOR_FOR[underlying]]["open_interest"]
    tau = (cutoff_utc(expiration) - fixture.stamp).total_seconds() / SECONDS_PER_YEAR
    entries: List[Dict[str, Any]] = []
    for strike_text in strikes:
        strike = D(strike_text)
        k = float(strike)
        for right in ("call", "put"):
            bsm_right = "CALL" if right == "call" else "PUT"
            mid = to_tick(bsm.price(bsm_right, s_mid, k, tau, R, Q, surface_sigma(underlying, sigma0, k, s_mid)))
            prev = to_tick(bsm.price(bsm_right, s_prev, k, tau + 1.0 / 365.0, R, Q,
                                     surface_sigma(underlying, sigma0, k, s_prev)))
            opened = to_tick(bsm.price(bsm_right, s_open, k, tau, R, Q, surface_sigma(underlying, sigma0, k, s_open)))
            half = D("0.10") if mid >= 5 else TICK
            bid = max(mid - half, D("0")).quantize(CENT)
            ask = (mid + half).quantize(CENT)
            high = (max(opened, mid) + TICK).quantize(CENT)
            low = max(min(opened, mid) - TICK, TICK).quantize(CENT)
            distance = abs(k - s_mid) / s_mid
            oi = int(round(oi_base * (0.35 + 0.65 * max(0.0, 1.0 - distance * 12))))
            book = dict(bid=str(bid), ask=str(ask), last=str(mid), prevclose=str(prev), open=str(opened),
                        high=str(high), low=str(low), volume=max(10, oi // 7), oi=max(oi, 25),
                        bidsize=20 + int(strike) % 37, asksize=15 + int(strike) % 29)
            entry = option_quote(occ_compact(underlying, expiration, right, strike), underlying, expiration, strike,
                                 right, book, fixture)
            entry["greeks"] = None  # the sandbox publishes no Greeks
            entries.append(entry)
    return entries


def chain_entries(underlying: str, fixture: FixtureSet, quotes: Dict[str, Dict[str, Any]]) -> Optional[List[Dict[str, Any]]]:
    """BSM-consistent chain around the anchor contract (``None`` once the expiration has passed)."""
    expiration = CHAIN_EXPIRY[underlying]
    if date.fromisoformat(expiration) <= fixture.quote_date and fixture.name == "holiday":
        return None
    anchor_symbol = ANCHOR_FOR[underlying]
    anchor = quotes[anchor_symbol]
    anchor_meta = OPTION_META[anchor_symbol]
    spot = quotes[underlying]
    s_mid = float((D(str(spot["bid"])) + D(str(spot["ask"]))) / 2)
    s_prev = float(D(str(spot["prevclose"])))
    s_open = float(D(str(spot["open"])))
    tau = (cutoff_utc(expiration) - fixture.stamp).total_seconds() / SECONDS_PER_YEAR
    anchor_mid_d = (D(str(anchor["bid"])) + D(str(anchor["ask"]))) / 2
    anchor_mid = float(anchor_mid_d)
    anchor_right = "CALL" if anchor_meta["right"] == "call" else "PUT"
    anchor_strike = D(anchor_meta["strike"])
    sigma: Optional[float] = None
    if tau * SECONDS_PER_YEAR >= EXPIRING_SECONDS:
        solved = iv.implied_vol(anchor_mid, anchor_right, s_mid, float(anchor_strike), tau, R, Q)
        if not solved.ok:
            raise SystemExit("implied vol for %s in set %s: %s" % (anchor_symbol, fixture.name, solved.status))
        sigma = solved.sigma
    s_mid_d = (D(str(spot["bid"])) + D(str(spot["ask"]))) / 2
    entries: List[Dict[str, Any]] = []
    for strike_text in CHAIN_STRIKES[underlying]:
        strike = D(strike_text)
        for right in ("call", "put"):
            symbol = occ_compact(underlying, expiration, right, strike)
            if symbol == anchor_symbol:
                entry = dict(anchor)
            elif sigma is None:
                # Fallback (no implied volatility): intrinsic plus one tick at the closing spot; a
                # contract further out of the money than the anchor never exceeds the anchor's mid.
                mid = fallback_value(right, s_mid_d, strike)
                further_otm = right == anchor_meta["right"] and (
                    (right == "call" and strike > anchor_strike) or (right == "put" and strike < anchor_strike))
                if further_otm:
                    mid = min(mid, anchor_mid_d).quantize(CENT)
                prev = fallback_value(right, D(str(spot["prevclose"])), strike)
                opened = fallback_value(right, D(str(spot["open"])), strike)
                half = D("0.10") if mid >= 5 else TICK
                bid = max(mid - half, D("0")).quantize(CENT)
                ask = (2 * mid - bid).quantize(CENT)  # keeps (bid + ask) / 2 == mid when bid is floored at 0
                high = (max(opened, mid) + TICK).quantize(CENT)
                low = max(min(opened, mid) - TICK, CENT).quantize(CENT)
                distance = abs(float(strike) - s_mid) / s_mid
                oi = int(round(anchor["open_interest"] * (0.35 + 0.65 * max(0.0, 1.0 - distance * 12))))
                book = dict(bid=str(bid), ask=str(ask), last=str(mid), prevclose=str(prev), open=str(opened),
                            high=str(high), low=str(low), volume=max(10, oi // 7), oi=max(oi, 25),
                            bidsize=20 + int(strike) % 37, asksize=15 + int(strike) % 29)
                entry = option_quote(symbol, underlying, expiration, strike, right, book, fixture)
            else:
                bsm_right = "CALL" if right == "call" else "PUT"
                mid = to_tick(bsm.price(bsm_right, s_mid, float(strike), tau, R, Q, sigma))
                prev = to_tick(bsm.price(bsm_right, s_prev, float(strike), tau + 1.0 / 365.0, R, Q, sigma))
                opened = to_tick(bsm.price(bsm_right, s_open, float(strike), tau, R, Q, sigma))
                half = D("0.10") if mid >= 5 else TICK
                bid = max(mid - half, D("0")).quantize(CENT)
                ask = (mid + half).quantize(CENT)
                high = (max(opened, mid) + TICK).quantize(CENT)
                low = max(min(opened, mid) - TICK, TICK).quantize(CENT)
                distance = abs(float(strike) - s_mid) / s_mid
                oi = int(round(anchor["open_interest"] * (0.35 + 0.65 * max(0.0, 1.0 - distance * 12))))
                book = dict(bid=str(bid), ask=str(ask), last=str(mid), prevclose=str(prev), open=str(opened),
                            high=str(high), low=str(low), volume=max(10, oi // 7), oi=max(oi, 25),
                            bidsize=20 + int(strike) % 37, asksize=15 + int(strike) % 29)
                entry = option_quote(symbol, underlying, expiration, strike, right, book, fixture)
            entry["greeks"] = None  # the sandbox publishes no Greeks
            entries.append(entry)
    return entries


# --------------------------------------------------------------------------------------------
# Endpoint payloads
# --------------------------------------------------------------------------------------------


def quotes_payload(symbols: List[str], quotes: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    matched = [quotes[s] for s in sorted(symbols) if s in quotes]
    unmatched = [s for s in sorted(symbols) if s not in quotes]
    body: Dict[str, Any] = {}
    if len(matched) == 1:
        body["quote"] = matched[0]
    elif matched:
        body["quote"] = matched
    if len(unmatched) == 1:
        body["unmatched_symbols"] = {"symbol": unmatched[0]}
    elif unmatched:
        body["unmatched_symbols"] = {"symbol": unmatched}
    return {"quotes": body}


def calendar_payload(year: int, month: int) -> Dict[str, Any]:
    days = []
    day = date(year, month, 1)
    while day.month == month:
        if market_clock.is_trading_day(day):
            close = market_clock.session_close_et(day).strftime("%H:%M")
            description = "Market is open" if close == "16:00" else "Market is open from 09:30 to %s" % close
            days.append({"date": day.isoformat(), "status": "open", "description": description,
                         "premarket": {"start": "07:00", "end": "09:24"},
                         "open": {"start": "09:30", "end": close},
                         "postmarket": {"start": close, "end": "20:00" if close == "16:00" else "17:00"}})
        else:
            days.append({"date": day.isoformat(), "status": "closed", "description": "Market is closed"})
        day += timedelta(days=1)
    return {"calendar": {"month": month, "year": year, "days": {"day": days}}}


def history_payload(bars: List[Tuple[str, str, str, str, str, int]]) -> Dict[str, Any]:
    days = [{"date": d, "open": f(o), "high": f(h), "low": f(lo), "close": f(c), "volume": v} for d, o, h, lo, c, v in bars]
    return {"history": {"day": days[0] if len(days) == 1 else days}}


def write_json(path: str, payload: Any) -> None:
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        json.dump(payload, handle, indent=2)
        handle.write("\n")


QUOTE_SUBSETS: Tuple[Tuple[str, ...], ...] = (
    ("SPY",), ("WMT",), ("NVDA",), ("SPY", "WMT"), ("SPY", "WMT", "NVDA"),
    ("WMT261120C00105000",), ("NVDA261120C00150000",), ("SPY261218P00620000",),
    ("SPY", "WMT", "WMT261120C00105000"),
    ("WMT", "WMT261120C00105000"), ("NVDA", "NVDA261120C00150000"), ("SPY", "SPY261218P00620000"),
    ("SPY", "WMT", "NVDA", "WMT261120C00105000", "NVDA261120C00150000", "SPY261218P00620000"),
)
REGULAR_ONLY_SUBSETS: Tuple[Tuple[str, ...], ...] = (("SPX",), ("SPX", "SPY"))


def set_quotes(fixture: FixtureSet) -> Dict[str, Dict[str, Any]]:
    """Every quote of one set (the underlyings and the anchor options its day's book holds)."""
    book = BOOKS[fixture.day]
    quotes: Dict[str, Dict[str, Any]] = {}
    for symbol in UNDERLYING_META:
        quotes[symbol] = equity_quote(symbol, book[symbol], fixture)
    for symbol, meta in OPTION_META.items():
        if symbol in book:
            quotes[symbol] = option_quote(symbol, meta["underlying"], meta["expiration"], D(meta["strike"]),
                                          meta["right"], book[symbol], fixture)
    return quotes


def fixture_set(name: str) -> FixtureSet:
    return next(fixture for fixture in SETS if fixture.name == name)


def generate_set(fixture: FixtureSet) -> List[str]:
    directory = os.path.join(HERE, fixture.name)
    os.makedirs(os.path.join(directory, "errors"), exist_ok=True)
    for name in os.listdir(directory):
        path = os.path.join(directory, name)
        if os.path.isfile(path):
            os.remove(path)
    quotes = set_quotes(fixture)
    written = []
    subsets = QUOTE_SUBSETS + (REGULAR_ONLY_SUBSETS if fixture.name == "regular" else ())
    for subset in subsets:
        key = ",".join(sorted(s.upper() for s in subset))
        path = os.path.join(directory, "quotes__%s.json" % key)
        write_json(path, quotes_payload(list(subset), quotes))
        written.append(path)
    for underlying in UNDERLYING_META:
        expiration = CHAIN_EXPIRY[underlying]
        entries = chain_entries(underlying, fixture, quotes)
        path = os.path.join(directory, "chains__%s__%s.json" % (underlying, expiration))
        write_json(path, {"options": None if entries is None else {"option": entries}})
        written.append(path)
        path = os.path.join(directory, "strikes__%s__%s.json" % (underlying, expiration))
        strikes = None if entries is None else {"strike": [float(s) for s in LISTED_STRIKES[underlying]]}
        write_json(path, {"strikes": strikes})
        written.append(path)
        path = os.path.join(directory, "expirations__%s.json" % underlying)
        write_json(path, {"expirations": {"date": list(EXPIRATIONS[fixture.day][underlying])}})
        written.append(path)
        path = os.path.join(directory, "history__%s__%s__%s.json" % ((underlying,) + HISTORY_RANGE))
        write_json(path, history_payload(HISTORY[underlying]))
        written.append(path)
    if fixture.name == "holiday":
        for underlying, bar in EXPIRY_DAY_BARS.items():
            path = os.path.join(directory, "history__%s__%s__%s.json" % (underlying, bar[0], bar[0]))
            write_json(path, history_payload([bar]))
            written.append(path)
        for underlying, bars in TWO_BAR_HISTORY.items():
            path = os.path.join(directory, "history__%s__%s__%s.json" % (underlying, bars[0][0], bars[-1][0]))
            write_json(path, history_payload(list(bars)))
            written.append(path)
    if fixture.name == "post_expiry":
        for underlying, bar in POST_EXPIRY_BARS.items():
            path = os.path.join(directory, "history__%s__%s__%s.json" % (underlying, bar[0], bar[0]))
            write_json(path, history_payload([bar]))
            written.append(path)
    for underlying, (expirations, strikes) in STRUCTURING_CHAINS.get(fixture.name, {}).items():
        sigma_fixture = fixture_set(STRUCTURING_SIGMA_SET[fixture.name])
        sigma0 = anchor_sigma(underlying, sigma_fixture, set_quotes(sigma_fixture))
        for expiration in expirations:
            path = os.path.join(directory, "chains__%s__%s.json" % (underlying, expiration))
            write_json(path, {"options": {"option": structuring_entries(underlying, expiration, strikes, sigma0,
                                                                        fixture, quotes)}})
            written.append(path)
    clock = {"clock": {"date": fixture.clock_date.isoformat(), "description": fixture.clock_description,
                       "state": fixture.clock_state, "timestamp": seconds(fixture.now),
                       "next_change": fixture.next_change, "next_state": fixture.next_state}}
    write_json(os.path.join(directory, "clock.json"), clock)
    write_json(os.path.join(directory, "calendar__2026-11.json"), calendar_payload(2026, 11))
    for name, body in ERRORS.items():
        with open(os.path.join(directory, "errors", name), "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
    return written


def main() -> int:
    for fixture in SETS:
        files = generate_set(fixture)
        et = market_clock.to_et(fixture.stamp)
        print("%-12s %3d files; quotes stamped %s ET = %d ms; --now %s = %d s" % (
            fixture.name, len(files) + 4, et.strftime("%Y-%m-%d %H:%M:%S"), ms(fixture.stamp),
            fixture.now.strftime("%Y-%m-%dT%H:%M:%SZ"), seconds(fixture.now)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
