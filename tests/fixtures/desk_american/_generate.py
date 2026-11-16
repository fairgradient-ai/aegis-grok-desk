"""Regenerate the American-model fixture desk root (data, not shipped code; roadmap step 2.3).

Run from the repository root::

    python3 tests/fixtures/desk_american/_generate.py

The root is the evening before an ex-dividend date, Thursday 2026-12-17 at 14:50 ET, with three deep in-the-money legs
the American model of record reads beside the expiry tiers (``aegis_desk.early_exercise``):

* ``AG-20261201-01``: 200 WMT shares with two short WMT 2026-12-18 80 calls. WMT goes ex-dividend 0.2350 on 2026-12-18:
  the call's bid's time value (40.05 - (120.00 - 80), 0.05) is below the dividend, so the dividend tier reads HIGH,
  while the lattice, on a continuous yield below the rate, never exercises a call early: the tier stays the test of
  that risk (the roadmap's example).
* ``AG-20261201-02``: 100 SPY shares with one long SPY 2027-01-15 700 put, quoted just above its intrinsic value: the
  lattice places it near its early-exercise boundary, and SPY's stated 1.85 dividend tomorrow exceeds the interest on
  the 700 strike until then, so the put reads the wait line.
* ``AG-20261201-03``: one short NVDA 2027-01-15 200 put, a cash-secured put quoted just above its intrinsic value: an
  early-assignment warning (NVDA's calendar states no dividend, a yield of zero).

Every value is synthetic. The shapes are those of the main fixture desk's sets (its README): one stamp for every quote,
2026-12-17T19:31:12Z, and the tools' ``--now`` 2026-12-17T19:50:00Z.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import date, timedelta
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))
SET = os.path.join(HERE, "fixtures", "alpaca", "deep")
STAMP = "2026-12-17T19:31:12Z"
STOCKS = {"NVDA": ("149.98", "150.02", "C"), "SPY": ("599.98", "600.02", "B"), "WMT": ("119.98", "120.02", "A")}
OPTIONS = {"NVDA270115P00200000": ("50.00", "50.04"), "SPY270115P00700000": ("100.00", "100.04"),
           "WMT261218C00080000": ("40.05", "40.25")}
HOLIDAYS = {date(2026, 12, 25)}


def write(name: str, payload: Any) -> None:
    with open(os.path.join(SET, name), "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2, sort_keys=False)
        handle.write("\n")


def stock_body(symbols: Any) -> Dict[str, Any]:
    quotes = {}
    for symbol in symbols:
        bid, ask, tape = STOCKS[symbol]
        quotes[symbol] = {"ap": float(ask), "as": 300, "ax": "V", "bp": float(bid), "bs": 400, "bx": "V", "c": ["R"],
                          "t": STAMP, "z": tape}
    return {"quotes": quotes, "currency": "USD"}


def option_body(symbols: Any) -> Dict[str, Any]:
    quotes = {}
    for symbol in symbols:
        bid, ask = OPTIONS[symbol]
        quotes[symbol] = {"ap": float(ask), "as": 50, "ax": "Z", "bp": float(bid), "bs": 60, "bx": "Z", "c": "A",
                          "t": STAMP}
    return {"quotes": quotes}


def calendar_body() -> Any:
    days = []
    day = date(2026, 12, 1)
    while day.month == 12:
        if day.weekday() < 5 and day not in HOLIDAYS:
            close = "13:00" if day == date(2026, 12, 24) else "16:00"
            settle = day + timedelta(days=1)
            while settle.weekday() >= 5 or settle in HOLIDAYS:
                settle += timedelta(days=1)
            days.append({"date": day.isoformat(), "open": "09:30", "close": close, "session_open": "0400",
                         "session_close": "2000", "settlement_date": settle.isoformat()})
        day += timedelta(days=1)
    return days


def main() -> int:
    if os.path.isdir(SET):
        shutil.rmtree(SET)
    os.makedirs(SET)
    stocks = sorted(STOCKS)
    options = sorted(OPTIONS)
    write("stock_quotes__%s.json" % ",".join(stocks), stock_body(stocks))
    for symbol in stocks:
        write("stock_quotes__%s.json" % symbol, stock_body([symbol]))
    write("option_quotes__%s.json" % ",".join(options), option_body(options))
    for symbol in options:
        write("option_quotes__%s.json" % symbol, option_body([symbol]))
    write("clock.json", {"timestamp": "2026-12-17T14:50:00-05:00", "is_open": True,
                         "next_open": "2026-12-18T09:30:00-05:00", "next_close": "2026-12-17T16:00:00-05:00"})
    write("calendar__2026-12-01__2026-12-31.json", calendar_body())
    print("wrote %d files to %s" % (len(os.listdir(SET)), os.path.relpath(SET, HERE)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
