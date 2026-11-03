"""Regenerate the ``recon`` fixture set of the reconciliation root ``tests/fixtures/desk_recon``.

Run from the repository root::

    python3 tests/fixtures/desk_recon/fixtures/tradier/_generate.py

The helpers (quote and history shapes, the error bodies, the epoch arithmetic) are imported by
path from the main fixture generator ``tests/fixtures/desk/fixtures/tradier/_generate.py``, so
both roots share one set of shapes; only the price book, the clock and the file list live here.
The set is read by ``csv_recon.py`` at ``--now 2026-11-24T12:00:00Z`` (Tuesday 07:00 ET): the
quotes are Monday 2026-11-23's closing quotes (stamped 15:59:50 ET, 10 s before the close) and
the history files carry the 2026-11-20 expiration day plus the 2026-11-23 session (the open of
which is the after-hours-crossing proxy). Every value is synthetic; see ``README.md`` beside
this file. ``tests/engine/test_desk_fixtures.py`` checks the output.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN_TRADIER = os.path.normpath(os.path.join(HERE, "..", "..", "..", "desk", "fixtures", "tradier"))
sys.path.insert(0, MAIN_TRADIER)

import _generate as main  # noqa: E402  (the main fixture generator, imported by path)

D = Decimal
SET_NAME = "recon"
SET_DIR = os.path.join(HERE, SET_NAME)

# Monday 2026-11-23 closing quotes, read Tuesday 2026-11-24 07:00 ET (pre-open; clock premarket).
FIXTURE = main.FixtureSet(SET_NAME, "nov23", date(2026, 11, 23), main.utc(2026, 11, 23, 20, 59, 50),
                          main.utc(2026, 11, 24, 12, 0, 0), date(2026, 11, 24), "premarket",
                          "Market is in premarket hours from 07:00 to 09:24", "09:30", "open", True, 1.0)

BOOK: Dict[str, Dict[str, Any]] = {
    "SPY": dict(bid="651.90", ask="651.94", last="651.92", prevclose="650.02", open="650.40", high="652.30",
                low="649.90", volume=42_700_000),
    "SPY261218P00620000": dict(bid="4.05", ask="4.25", last="4.15", prevclose="4.20", open="4.20", high="4.30",
                               low="4.00", volume=4_400, oi=151_600, bidsize=75, asksize=90),
}
QUOTE_SUBSETS = (("SPY",), ("SPY261218P00620000",), ("SPY", "SPY261218P00620000"))

# Two-bar history files: the WMT and NVDA bars are the main generator's (identical files in the
# holiday set); AMD is reconciliation-only: closed 1.00 in the money and opened below the strike
# on Monday (PIN_UNCERTAIN by the crossing rule). No AAPL file: EXPIRED_UNRECORDED.
HISTORY: Dict[str, List[Any]] = {
    "WMT": list(main.TWO_BAR_HISTORY["WMT"]),
    "NVDA": list(main.TWO_BAR_HISTORY["NVDA"]),
    "AMD": [("2026-11-20", "228.10", "231.60", "227.80", "231.00", 52_000_000),
            ("2026-11-23", "229.40", "230.20", "227.90", "228.75", 47_000_000)],
}


def generate() -> List[str]:
    os.makedirs(os.path.join(SET_DIR, "errors"), exist_ok=True)
    for name in os.listdir(SET_DIR):
        path = os.path.join(SET_DIR, name)
        if os.path.isfile(path):
            os.remove(path)
    quotes: Dict[str, Dict[str, Any]] = {
        "SPY": main.equity_quote("SPY", BOOK["SPY"], FIXTURE),
        "SPY261218P00620000": main.option_quote("SPY261218P00620000", "SPY", "2026-12-18", D("620"), "put",
                                                BOOK["SPY261218P00620000"], FIXTURE),
    }
    written = []
    for subset in QUOTE_SUBSETS:
        key = ",".join(sorted(s.upper() for s in subset))
        path = os.path.join(SET_DIR, "quotes__%s.json" % key)
        main.write_json(path, main.quotes_payload(list(subset), quotes))
        written.append(path)
    for symbol, bars in HISTORY.items():
        path = os.path.join(SET_DIR, "history__%s__%s__%s.json" % (symbol, bars[0][0], bars[-1][0]))
        main.write_json(path, main.history_payload(bars))
        written.append(path)
    clock = {"clock": {"date": FIXTURE.clock_date.isoformat(), "description": FIXTURE.clock_description,
                       "state": FIXTURE.clock_state, "timestamp": main.seconds(FIXTURE.now),
                       "next_change": FIXTURE.next_change, "next_state": FIXTURE.next_state}}
    path = os.path.join(SET_DIR, "clock.json")
    main.write_json(path, clock)
    written.append(path)
    for name, body in main.ERRORS.items():
        path = os.path.join(SET_DIR, "errors", name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(body)
        written.append(path)
    return written


def run() -> int:
    files = generate()
    et = main.market_clock.to_et(FIXTURE.stamp)
    print("%-12s %3d files; quotes stamped %s ET = %d ms; --now %s = %d s" % (
        SET_NAME, len(files), et.strftime("%Y-%m-%d %H:%M:%S"), main.ms(FIXTURE.stamp),
        FIXTURE.now.strftime("%Y-%m-%dT%H:%M:%SZ"), main.seconds(FIXTURE.now)))
    return 0


if __name__ == "__main__":
    sys.exit(run())
