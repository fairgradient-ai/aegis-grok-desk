"""Regenerate the ``recon`` fixture set of the reconciliation root ``tests/fixtures/desk_recon``.

Run from the repository root::

    python3 tests/fixtures/desk_recon/fixtures/alpaca/_generate.py

The helpers (the Alpaca quote, bar and clock bodies, the error bodies, the instant arithmetic) are imported by
path from the main fixture generator ``tests/fixtures/desk/fixtures/alpaca/_generate.py``, so both roots share
one set of shapes; only the price book, the instants and the file list live here. The set is read by
``csv_recon.py`` at ``--now 2026-11-24T12:00:00Z`` (Tuesday 07:00 ET): the quotes are Monday 2026-11-23's
closing quotes (stamped 15:59:50 ET, 10 s before the close) and the bars files carry the 2026-11-20 expiration
day plus the 2026-11-23 session (the open of which is the after-hours-crossing proxy). Every value is synthetic;
see ``README.md`` beside this file. ``tests/engine/test_desk_fixtures.py`` checks the output.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List

HERE = os.path.dirname(os.path.abspath(__file__))
MAIN_ALPACA = os.path.normpath(os.path.join(HERE, "..", "..", "..", "desk", "fixtures", "alpaca"))
sys.path.insert(0, MAIN_ALPACA)

import _generate as main  # noqa: E402  (the main fixture generator, imported by path)

D = Decimal
SET_NAME = "recon"
SET_DIR = os.path.join(HERE, SET_NAME)

# Monday 2026-11-23 closing quotes, read Tuesday 2026-11-24 07:00 ET (pre-open: the clock is closed).
FIXTURE = main.FixtureSet(SET_NAME, "nov23", date(2026, 11, 23), main.utc(2026, 11, 23, 20, 59, 50),
                          main.utc(2026, 11, 24, 12, 0, 0), 1.0)

BOOK: Dict[str, Dict[str, Any]] = {
    "SPY": dict(bid="651.90", ask="651.94", last="651.92", prevclose="650.02", open="650.40", high="652.30",
                low="649.90", volume=42_700_000),
    "SPY261218P00620000": dict(bid="4.05", ask="4.25", last="4.15", prevclose="4.20", open="4.20", high="4.30",
                               low="4.00", volume=4_400, oi=151_600, bidsize=75, asksize=90),
}
# The one quote batch the reconciliation's quote check sends for the unexpired legs (the SPY shares and the
# December put), and each leg alone: the client sends the equity and the option to their own endpoints.
QUOTE_BATCHES = (("SPY",), ("SPY261218P00620000",), ("SPY", "SPY261218P00620000"))

# Two-bar files: the WMT and NVDA bars are the main generator's (identical files in the holiday set); AMD is
# reconciliation-only: closed 1.00 in the money and opened below the strike on Monday (PIN_UNCERTAIN by the
# crossing rule). No AAPL file: EXPIRED_UNRECORDED.
HISTORY: Dict[str, List[Any]] = {
    "WMT": list(main.TWO_BAR_HISTORY["WMT"]),
    "NVDA": list(main.TWO_BAR_HISTORY["NVDA"]),
    "AMD": [("2026-11-20", "228.10", "231.60", "227.80", "231.00", 52_000_000),
            ("2026-11-23", "229.40", "230.20", "227.90", "228.75", 47_000_000)],
}


def generate() -> List[str]:
    main.clean(SET_DIR)
    records: Dict[str, Dict[str, Any]] = {
        "SPY": main.equity_record("SPY", BOOK["SPY"], FIXTURE),
        "SPY261218P00620000": main.option_record("SPY261218P00620000", "SPY", "2026-12-18", D("620"), "put",
                                                 BOOK["SPY261218P00620000"], FIXTURE),
    }
    written = main.write_quote_files(SET_DIR, QUOTE_BATCHES, records, FIXTURE)
    for symbol, bars in HISTORY.items():
        path = os.path.join(SET_DIR, "bars__%s__%s__%s.json" % (symbol, bars[0][0], bars[-1][0]))
        main.write_json(path, main.bars_body(symbol, bars))
        written.append(path)
    path = os.path.join(SET_DIR, "clock.json")
    main.write_json(path, main.clock_body(FIXTURE.now))
    written.append(path)
    return written + main.write_errors(SET_DIR)


def run() -> int:
    files = generate()
    et = main.market_clock.to_et(FIXTURE.stamp)
    print("%-12s %3d files; quotes stamped %s ET = %s = %d ms; --now %s = %d s" % (
        SET_NAME, len(files), et.strftime("%Y-%m-%d %H:%M:%S"), main.rfc_z(FIXTURE.stamp), main.ms(FIXTURE.stamp),
        FIXTURE.now.strftime("%Y-%m-%dT%H:%M:%SZ"), main.seconds(FIXTURE.now)))
    return 0


if __name__ == "__main__":
    sys.exit(run())
