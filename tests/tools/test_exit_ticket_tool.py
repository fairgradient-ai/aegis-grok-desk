"""``scripts/exit_ticket.py``: a ticket for a position you hold (PR 3b; designer Q's D11-D13, designer C's D3, D6, D7
and D15 under the addendum S1, S2, S5-S11).

Every run is in-process (``main(argv)``) on a copy of ``tests/fixtures/desk`` that holds the fixture criteria
(``tests/fixtures/init/expected_structuring.toml``) and whose configuration ``ack.py config --owner-tapped`` acknowledged
at 19:49 UTC. Pinned here: designer Q's D16 ticket goldens under S17 with their full text (WMT CLOSE, ROLL with the
2027-01-15 115 call as the replacement, UNWIND_STOCK, LET_ASSIGN, MONETIZE_PUT not applicable; the NVDA ROLL with the
2027-01-15 160 call, a TICKET with two FAILS_CRITERION lines and exit 0; SPY MONETIZE_PUT, UNWIND_STOCK, ROLL with the
2027-03-19 610 put, LET_ASSIGN not applicable), the expiry-day WMT CLOSE at grade E, the order of C D3.6 (CONFIG CHANGED first, then the
criteria file, then the approval-level check, then market data), every usage error, replacement_not_listed,
market_closed, the ticket files (only on exit 0, idempotent, never overwritten), the journal record of every printed
text, the WITHHELD line, the request counts, ``--now`` and that every request is an allow-listed GET.
"""

from __future__ import annotations

# isort: off
import tests._path  # noqa: F401
# isort: on

import contextlib
import hashlib
import io
import json
import os
import shutil
import tempfile
import unittest
from typing import Any, Dict, List, Sequence, Tuple
from unittest import mock
from urllib.parse import urlsplit

from aegis_desk import (alert_lint, alpaca_client, contract, desk_paths, market_clock, quote_cache, structuring_text,
                        tickets)

import ack as ack_tool
import exit_check as exit_check_tool
import exit_ticket

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FIXTURE_DESK = os.path.join(ROOT, "tests", "fixtures", "desk")
CRITERIA_FILE = os.path.join(ROOT, "tests", "fixtures", "init", "expected_structuring.toml")
REGULAR_NOW = "2026-11-03T19:50:00Z"
ACK_NOW = "2026-11-03T19:49:00Z"
AFTER_CLOSE_NOW = "2026-11-03T21:25:00Z"
EXPIRY_DAY_NOW = "2026-11-20T19:50:00Z"
HOLIDAY_NOW = "2026-11-26T15:00:00Z"
WMT, NVDA, SPY = "AG-20261020-01", "AG-20261022-02", "AG-20261028-03"
FOOTER = ("computed by the desk's tools from your criteria · posted by an AI assistant · a personalized suggestion for "
          "positions you hold, not an order · you decide and act in your brokerage account · data delayed 15 min, as of ")
# an unwind is shares and options together, not a roll (reviews L1-5, L2-6, L4-5)

GOLDEN_TEXTS: Dict[str, Tuple[Tuple[str, ...], int, Tuple[str, ...]]] = {
    "wmt_close": (('AG-20261020-01', 'CLOSE'), 0, (
        "### TICKET AG-20261020-01 | WMT covered call: the option legs closed (action CLOSE)",
        "*account 1 of desk.toml (margin) | computed 19:50 UTC | valid until 20:20 UTC (30 min) | quotes 19:31 "
        "UTC, 15-min delayed*",
        "",
        "**The legs**",
        "- leg 1 of 1: closes your short 2 x WMT 2026-11-20 105 call | bid 3.35, ask 3.50, mid 3.425 | quote grade "
        "B",
        "",
        "**The cash**",
        "- **net cash** -700.00 at the conservative prices, -685.00 at the mids",
        "- **realized on the closing leg** -16.00 at the conservative prices, -1.00 at the mids",
        "- **the most the steps need at once** 700.00",
        "",
        "**Approval-level check:** the highest step needs level 0 by the desk's convention, within your "
        "self-attested level 2 (desk.toml); your broker's own approval for this account decides",
        "- after leg 1: you hold 200 WMT shares, no option | level 0",
        "",
        "> - after 20:20 UTC its quotes are too old, and a new ticket needs a new request",
        "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
        "> - the desk does not see your cash balance",
        "> - after a fill, tell the Aegis Desk Lead the fill; it proposes the positions.csv lines",
        "> - you supply the limit; the desk prints none",
        "> - if you decide to act, you type it into your brokerage account yourself",
        "",
        FOOTER + "19:31 UTC",
    )),
    "wmt_roll": (('AG-20261020-01', 'ROLL', '--new-expiration', '2027-01-15', '--new-strike', '115'), 0, (
        "### TICKET AG-20261020-01 | WMT covered call: a roll of the short call (action ROLL)",
        "*account 1 of desk.toml (margin) | computed 19:50 UTC | valid until 20:20 UTC (30 min) | quotes 19:31 "
        "UTC, 15-min delayed*",
        "",
        "**The legs**",
        "- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 3.35, ask 3.50, mid 3.425 | quote grade "
        "B",
        "- leg 2 of 2: opens a new short 2 x WMT 2027-01-15 115 call | bid 3.55, ask 3.65, mid 3.60 | quote grade B",
        "",
        "**The cash**",
        "- **net cash** +10.00 at the conservative prices, +35.00 at the mids",
        "- **realized on the closing leg** -16.00 at the conservative prices, -1.00 at the mids",
        "- **the most the steps need at once** 700.00",
        "- **spread cost against the mids** 25.00",
        "",
        "**Checks for the new call**",
        "- \U0001F7E2 **net per share** credit 0.05 (10.00 in all) | your criteria: credit only",
        "- \U0001F7E2 **strike** 115 | your criteria: at or above your cost 98.50 or the held strike 105",
        "- \U0001F7E2 **absolute delta** 0.33 | your criteria: 0.15 to 0.35",
        "- \U0001F7E2 **bid** 3.55 | your criteria: at least 0.50",
        "- \U0001F7E2 **calendar days** 73 | your criteria: 40 to 75",
        "- \U0001F7E2 **spread** 2.78 % | your criteria: at most 25 %",
        "- \U0001F7E1 **ex-dividend** 2026-11-24 (cum 2026-11-23), inside the new call's life | accepted by "
        "#assign-ok",
        "",
        "**The group, now and after both legs**",
        "- **max loss** 19,016.00 now, 19,006.00 after",
        "- **max profit** 1,984.00 now, 3,994.00 after",
        "- **breakeven** 95.08 now, 95.03 after",
        "- **group delta** 101.71 now, 134.07 after",
        "",
        "**Approval-level check (leg 1 first):** the highest step needs level 1 by the desk's convention, within "
        "your self-attested level 2 (desk.toml); your broker's own approval for this account decides",
        "- after leg 1: you hold 200 WMT shares, no option | level 0",
        "- after leg 2: you hold 200 WMT shares, 2 short calls | level 1",
        "- in the other order, after leg 2 you would hold 2 uncovered calls (level 4)",
        "- entered as one multi-leg order, some brokers class a roll as a spread, which can need a higher level",
        "",
        "> - after 20:20 UTC its quotes are too old, and a new ticket needs a new request",
        "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
        "> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style "
        "options is not modelled",
        "> - the desk does not see your cash balance",
        "> - after a fill, tell the Aegis Desk Lead the fill; it proposes the positions.csv lines",
        "> - you supply the limit; the desk prints none",
        "> - if you decide to act, you type it into your brokerage account yourself",
        "",
        FOOTER + "19:31 UTC",
    )),
    "wmt_unwind": (('AG-20261020-01', 'UNWIND_STOCK'), 0, (
        "### TICKET AG-20261020-01 | WMT covered call: the shares and every option leg closed (action UNWIND_STOCK)",
        "*account 1 of desk.toml (margin) | computed 19:50 UTC | valid until 20:20 UTC (30 min) | quotes 19:31 "
        "UTC, 15-min delayed*",
        "",
        "**The legs**",
        "- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 3.35, ask 3.50, mid 3.425 | quote grade "
        "B",
        "- leg 2 of 2: closes 200 of your long WMT shares | bid 104.20, ask 104.26, mid 104.23 | quote grade A",
        "",
        "**The cash**",
        "- **net cash** +20,140.00 at the conservative prices, +20,161.00 at the mids",
        "- **realized on the closing legs** +1,124.00 at the conservative prices, +1,145.00 at the mids",
        "- **the most the steps need at once** 700.00",
        "- **spread cost against the mids** 21.00",
        "",
        "**Approval-level check (leg 1 first):** the highest step needs level 0 by the desk's convention, within "
        "your self-attested level 2 (desk.toml); your broker's own approval for this account decides",
        "- after leg 1: you hold 200 WMT shares, no option | level 0",
        "- after leg 2: you hold no WMT shares, no option | level 0",
        "- in the other order, after leg 2 you would hold 2 uncovered calls (level 4)",
        "- entered as one multi-leg order, some brokers class shares and options together as a combination, which "
        "can need a higher level",
        "",
        "> - after 20:20 UTC its quotes are too old, and a new ticket needs a new request",
        "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
        "> - the desk does not see your cash balance",
        "> - after a fill, tell the Aegis Desk Lead the fill; it proposes the positions.csv lines",
        "> - you supply the limit; the desk prints none",
        "> - if you decide to act, you type it into your brokerage account yourself",
        "",
        FOOTER + "19:31 UTC",
    )),
    "wmt_let_assign": (('AG-20261020-01', 'LET_ASSIGN'), 0, (
        "### TICKET AG-20261020-01 | WMT covered call: the short option left to expiry (action LET_ASSIGN)",
        "*account 1 of desk.toml (margin) | computed 19:50 UTC | valid until 20:20 UTC (30 min) | quotes 19:31 "
        "UTC, 15-min delayed*",
        "",
        "no leg to enter: if the 105 call is at least 0.01 in the money at expiry, OCC exercises it by exception; "
        "your broker's cutoff is 16:30 ET (desk.toml)",
        "",
        "**The 2026-11-20 105 call**",
        "- **now** out of the money: the spot mid 104.23 is below its strike",
        "- **if you decide otherwise** you act by Fri 2026-11-20 16:00 ET (21:00 UTC): a short option left to "
        "expiry can be closed only while the session trades",
        "- **if assigned** 200 WMT shares are called away at the strike, cash +21,000.00; realized on the call and "
        "the shares +1,984.00",
        "- **ex-dividend** the ex-dividend date 2026-11-24 falls after the call's last trading day 2026-11-20",
        "",
        "> - after 20:20 UTC its quotes are too old, and a new ticket needs a new request",
        "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
        "> - after expiry, the post-expiry check of Aegis Position Control proposes the positions.csv lines",
        "",
        FOOTER + "19:31 UTC",
    )),
    "wmt_monetize": (('AG-20261020-01', 'MONETIZE_PUT'), 0, (
        "NOT_APPLICABLE | AG-20261020-01 MONETIZE_PUT ticket | AG-20261020-01 is a covered call: the put of "
        "a protective put or a collar is what MONETIZE_PUT closes",
        "",
        FOOTER + "19:50 UTC",
    )),
    "nvda_roll": (('AG-20261022-02', 'ROLL', '--new-expiration', '2027-01-15', '--new-strike', '160'), 0, (
        "### TICKET AG-20261022-02 | NVDA covered call: a roll of the short call (action ROLL)",
        "*account 1 of desk.toml (margin) | computed 19:50 UTC | valid until 20:20 UTC (30 min) | quotes 19:31 "
        "UTC, 15-min delayed*",
        "",
        "**The legs**",
        "- leg 1 of 2: closes your short 2 x NVDA 2026-11-20 150 call | bid 3.10, ask 3.30, mid 3.20 | quote grade "
        "C",
        "- leg 2 of 2: opens a new short 2 x NVDA 2027-01-15 160 call | bid 3.25, ask 3.35, mid 3.30 | quote grade "
        "B",
        "",
        "**The cash**",
        "- **net cash** -10.00 at the conservative prices, +20.00 at the mids",
        "- **realized on the closing leg** -50.00 at the conservative prices, -30.00 at the mids",
        "- **the most the steps need at once** 660.00",
        "- **spread cost against the mids** 30.00",
        "",
        "**Checks for the new call**",
        "- \U0001F534 **net per share** debit 0.05 (10.00 in all) | your criteria: credit only",
        "- \U0001F7E2 **strike** 160 | your criteria: at or above your cost 141.20 or the held strike 150",
        "- \U0001F7E2 **absolute delta** 0.31 | your criteria: 0.15 to 0.35",
        "- \U0001F7E2 **bid** 3.25 | your criteria: at least 0.50",
        "- \U0001F7E2 **calendar days** 73 | your criteria: 40 to 75",
        "- \U0001F7E2 **spread** 3.03 % | your criteria: at most 25 %",
        "- \U0001F534 **ex-dividend** 2026-12-03 (cum 2026-12-02), inside the new call's life | excluded by your "
        "structuring.toml",
        "",
        "**The group, now and after both legs**",
        "- **max loss** 27,630.00 now, 27,640.00 after",
        "- **max profit** 2,370.00 now, 4,360.00 after",
        "- **breakeven** 138.15 now, 138.20 after",
        "- **group delta** 104.61 now, 137.35 after",
        "",
        "**Approval-level check (leg 1 first):** the highest step needs level 1 by the desk's convention, within "
        "your self-attested level 2 (desk.toml); your broker's own approval for this account decides",
        "- after leg 1: you hold 200 NVDA shares, no option | level 0",
        "- after leg 2: you hold 200 NVDA shares, 2 short calls | level 1",
        "- in the other order, after leg 2 you would hold 2 uncovered calls (level 4)",
        "- entered as one multi-leg order, some brokers class a roll as a spread, which can need a higher level",
        "",
        "> - after 20:20 UTC its quotes are too old, and a new ticket needs a new request",
        "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
        "> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style "
        "options is not modelled",
        "> - the desk does not see your cash balance",
        "> - after a fill, tell the Aegis Desk Lead the fill; it proposes the positions.csv lines",
        "> - you supply the limit; the desk prints none",
        "> - if you decide to act, you type it into your brokerage account yourself",
        "",
        FOOTER + "19:31 UTC",
    )),
    "spy_monetize": (('AG-20261028-03', 'MONETIZE_PUT'), 0, (
        "### TICKET AG-20261028-03 | SPY protective put: the long put closed for its value (action MONETIZE_PUT)",
        "*account 2 of desk.toml (ira) | computed 19:50 UTC | valid until 20:20 UTC (30 min) | quotes 19:31 UTC, "
        "15-min delayed*",
        "",
        "**The legs**",
        "- leg 1 of 1: closes your long 1 x SPY 2026-12-18 620 put | bid 9.80, ask 10.10, mid 9.95 | quote grade B",
        "",
        "**The cash**",
        "- **net cash** +980.00 at the conservative prices, +995.00 at the mids",
        "- **realized on the closing leg** -150.00 at the conservative prices, -135.00 at the mids",
        "- **the most the steps need at once** 0.00",
        "",
        "**The put**",
        "- **the 2026-12-18 620 put** out of the money: the spot mid 648.12 is above its strike, so its bid 9.80 "
        "is all time value",
        "- **after the leg** your 100 SPY shares carry no put: their downside is unhedged",
        "",
        "**Approval-level check:** the highest step needs level 0 by the desk's convention, within your "
        "self-attested level 2 (desk.toml); your broker's own approval for this account decides",
        "- after leg 1: you hold 100 SPY shares, no option | level 0",
        "",
        "> - after 20:20 UTC its quotes are too old, and a new ticket needs a new request",
        "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
        "> - the desk does not see your cash balance",
        "> - after a fill, tell the Aegis Desk Lead the fill; it proposes the positions.csv lines",
        "> - you supply the limit; the desk prints none",
        "> - if you decide to act, you type it into your brokerage account yourself",
        "",
        FOOTER + "19:31 UTC",
    )),
    "spy_unwind": (('AG-20261028-03', 'UNWIND_STOCK'), 0, (
        "### TICKET AG-20261028-03 | SPY protective put: the shares and every option leg closed (action "
        "UNWIND_STOCK)",
        "*account 2 of desk.toml (ira) | computed 19:50 UTC | valid until 20:20 UTC (30 min) | quotes 19:31 UTC, "
        "15-min delayed*",
        "",
        "**The legs**",
        "- leg 1 of 2: closes 100 of your long SPY shares | bid 648.10, ask 648.14, mid 648.12 | quote grade A",
        "- leg 2 of 2: closes your long 1 x SPY 2026-12-18 620 put | bid 9.80, ask 10.10, mid 9.95 | quote grade B",
        "",
        "**The cash**",
        "- **net cash** +65,790.00 at the conservative prices, +65,807.00 at the mids",
        "- **realized on the closing legs** +1,420.00 at the conservative prices, +1,437.00 at the mids",
        "- **the most the steps need at once** 0.00",
        "- **spread cost against the mids** 17.00",
        "",
        "**Approval-level check (leg 1 first):** the highest step needs level 0 by the desk's convention, within "
        "your self-attested level 2 (desk.toml); your broker's own approval for this account decides",
        "- after leg 1: you hold no SPY shares, 1 long put | level 0",
        "- after leg 2: you hold no SPY shares, no option | level 0",
        "- entered as one multi-leg order, some brokers class shares and options together as a combination, which "
        "can need a higher level",
        "",
        "> - after 20:20 UTC its quotes are too old, and a new ticket needs a new request",
        "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
        "> - the desk does not see your cash balance",
        "> - after a fill, tell the Aegis Desk Lead the fill; it proposes the positions.csv lines",
        "> - you supply the limit; the desk prints none",
        "> - if you decide to act, you type it into your brokerage account yourself",
        "",
        FOOTER + "19:31 UTC",
    )),
    "spy_roll": (('AG-20261028-03', 'ROLL', '--new-expiration', '2027-03-19', '--new-strike', '610'), 0, (
        "### TICKET AG-20261028-03 | SPY protective put: a roll of the long put (action ROLL)",
        "*account 2 of desk.toml (ira) | computed 19:50 UTC | valid until 20:20 UTC (30 min) | quotes 19:31 UTC, "
        "15-min delayed*",
        "",
        "**The legs**",
        "- leg 1 of 2: opens a new long 1 x SPY 2027-03-19 610 put | bid 22.90, ask 23.10, mid 23.00 | quote grade "
        "A",
        "- leg 2 of 2: closes your long 1 x SPY 2026-12-18 620 put | bid 9.80, ask 10.10, mid 9.95 | quote grade B",
        "",
        "**The cash**",
        "- **net cash** -1,330.00 at the conservative prices, -1,305.00 at the mids",
        "- **realized on the closing leg** -150.00 at the conservative prices, -135.00 at the mids",
        "- **the most the steps need at once** 2,310.00",
        "- **spread cost against the mids** 25.00",
        "",
        "**Checks for the new put**",
        "- \U0001F7E2 **floor** 5.88 % below | your criteria: 3 to 10 %",
        "- **cost** ask 23.10 (2,310.00 in all)",
        "- **net cost per day added** 0.0226 % of the share price (ask 23.10 less the held put's bid 9.80, over "
        "91 days added)",
        "- \U0001F7E2 **covered at your stress drop** 91.504 per share (9,150.40 in all) | your criteria: drop 20 "
        "% (stressed price 518.496)",
        "- \U0001F7E2 **calendar days** 136 | your criteria: 120 to 210",
        "- \U0001F7E2 **spread** 0.87 % | your criteria: at most 25 %",
        "",
        "**The group, now and after both legs**",
        "- **max loss** 2,370.00 now, 4,700.00 after",
        "- **max profit** unbounded now, unbounded after",
        "- **breakeven** 643.70 now, 657.00 after",
        "- **group delta** 72.52 now, 70.17 after",
        "",
        "**Approval-level check (leg 1 first):** the highest step needs level 2 by the desk's convention, within "
        "your self-attested level 2 (desk.toml); your broker's own approval for this account decides",
        "- after leg 1: you hold 100 SPY shares, 2 long puts | level 2",
        "- after leg 2: you hold 100 SPY shares, 1 long put | level 0",
        "- entered as one multi-leg order, some brokers class a roll as a spread, which can need a higher level",
        "",
        "> - after 20:20 UTC its quotes are too old, and a new ticket needs a new request",
        "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
        "> - prices: European Black-Scholes-Merton on the quotes' mids; early exercise of these American-style "
        "options is not modelled",
        "> - carry: the 2027-03-19 options are priced at the dividend carry their put-call parity implies, 0.01 % "
        "a year, because those quotes exclude the calendar's 1.15 %",
        "> - the desk does not see your cash balance",
        "> - after a fill, tell the Aegis Desk Lead the fill; it proposes the positions.csv lines",
        "> - you supply the limit; the desk prints none",
        "> - if you decide to act, you type it into your brokerage account yourself",
        "",
        FOOTER + "19:31 UTC",
    )),
    "spy_let_assign": (('AG-20261028-03', 'LET_ASSIGN'), 0, (
        "NOT_APPLICABLE | AG-20261028-03 LET_ASSIGN ticket | AG-20261028-03 holds no short option: only a "
        "short option can be left to assignment",
        "",
        FOOTER + "19:50 UTC",
    )),
}

EXPIRY_DAY_CLOSE: Tuple[str, ...] = (
    "### TICKET AG-20261020-01 | WMT covered call: the option legs closed (action CLOSE)",
    "*account 1 of desk.toml (margin) | computed 19:50 UTC | valid until 20:20 UTC (30 min) | quotes 19:31 UTC, "
    "15-min delayed*",
    "",
    "**The legs**",
    "- leg 1 of 1: closes your short 2 x WMT 2026-11-20 105 call | bid 0.12, ask 0.16, mid 0.14 | quote grade E | "
    "quote grade E: the conservative side stands",
    "",
    "**The cash**",
    "- **net cash** -32.00 at the conservative prices, -28.00 at the mids",
    "- **realized on the closing leg** +652.00 at the conservative prices, +656.00 at the mids",
    "- **the most the steps need at once** 32.00",
    "",
    "**Approval-level check:** the highest step needs level 0 by the desk's convention, within your self-attested "
    "level 2 (desk.toml); your broker's own approval for this account decides",
    "- after leg 1: you hold 200 WMT shares, no option | level 0",
    "",
    "> - after 20:20 UTC its quotes are too old, and a new ticket needs a new request",
    "> - quotes: Alpaca paper (IEX stocks, indicative options: approximate), 15-min delayed, as of 19:31 UTC",
    "> - the desk does not see your cash balance",
    "> - after a fill, tell the Aegis Desk Lead the fill; it proposes the positions.csv lines",
    "> - you supply the limit; the desk prints none",
    "> - if you decide to act, you type it into your brokerage account yourself",
    "",
    FOOTER + "19:31 UTC",
)


def call(main: Any, argv: Sequence[str]) -> Tuple[int, str]:
    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        try:
            result = main(list(argv))
        except SystemExit as exc:  # argparse usage errors exit through the parser
            result = exc.code
    return result, buffer.getvalue()


def sha256_file(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


class DeskCase(unittest.TestCase):
    """A copy of the fixture desk with the fixture criteria, acknowledged at 19:49 UTC (``acknowledge=False`` skips the
    acknowledgement, ``criteria=False`` the criteria file)."""

    acknowledge = True
    criteria = True

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="aegis-exit-ticket-")
        self.root = os.path.join(os.path.realpath(self._tmp.name), "desk")
        shutil.copytree(FIXTURE_DESK, self.root)
        self.paths = desk_paths.for_root(self.root)
        if self.criteria:
            shutil.copyfile(CRITERIA_FILE, self.paths.structuring_toml)
        if self.acknowledge:
            self.ack()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def ack(self, now: str = ACK_NOW) -> None:
        code, out = call(ack_tool.main, ["config", "--owner-tapped", "--offline", "--desk-root", self.root, "--now", now,
                                         "--json"])
        self.assertEqual(0, code, out)

    def screen(self, out: str) -> None:
        self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(out)], out)
        self.assertNotIn("Brokerage (margin)", "" if out.lstrip().startswith("{") else out)

    def argv(self, trade: str, action: str, *extra: str, now: str = REGULAR_NOW, fixture_set: str = "regular",
             json_mode: bool = True) -> List[str]:
        argv = ["--trade", trade, "--action", action] + list(extra) + ["--offline", "--desk-root", self.root, "--now", now,
                                                                       "--fixture-set", fixture_set]
        return argv + (["--json"] if json_mode else [])

    def run_json(self, trade: str, action: str, *extra: str, **kwargs: Any) -> Tuple[int, Dict[str, Any]]:
        code, out = call(exit_ticket.main, self.argv(trade, action, *extra, **kwargs))
        self.screen(out)
        return code, json.loads(out)

    def run_text(self, trade: str, action: str, *extra: str, **kwargs: Any) -> Tuple[int, str]:
        code, out = call(exit_ticket.main, self.argv(trade, action, *extra, json_mode=False, **kwargs))
        self.screen(out)
        return code, out

    def journal(self) -> List[Dict[str, Any]]:
        try:
            with open(self.paths.structuring_journal, encoding="utf-8") as handle:
                return [json.loads(line) for line in handle if line.strip()]
        except FileNotFoundError:
            return []

    def ticket_files(self) -> List[str]:
        try:
            return sorted(os.listdir(self.paths.tickets_dir))
        except FileNotFoundError:
            return []


# ------------------------------------------------------------------------------------------------
# --schema
# ------------------------------------------------------------------------------------------------


class SchemaTest(unittest.TestCase):
    def test_schema_describes_every_payload_key(self) -> None:
        code, out = call(exit_ticket.main, ["--schema"])
        self.assertEqual(0, code)
        body = json.loads(out)
        self.assertEqual("exit_ticket", body["tool"])
        self.assertEqual(list(exit_ticket.PAYLOAD_KEYS), sorted(body["envelope"]["payload"], key=exit_ticket.PAYLOAD_KEYS.index))
        self.assertEqual(set(exit_ticket.PAYLOAD_KEYS), set(body["envelope"]["payload"]))
        self.assertIn("GET", body["network_access"])
        self.assertEqual([], [p.pattern for p in alert_lint.FORBIDDEN_PATTERNS if p.search(out)])
        self.assertEqual(tickets.TICKET_KEYS, exit_ticket.PAYLOAD_KEYS[:len(tickets.TICKET_KEYS)])

    def test_the_flags(self) -> None:
        parser = exit_ticket.build_parser("0.1.0")
        flags = {option for action in parser._actions for option in action.option_strings}
        for flag in ("--trade", "--action", "--new-expiration", "--new-strike", "--fixture-set", "--json", "--schema",
                     "--timeout", "--desk-root", "--offline", "--now"):
            self.assertIn(flag, flags)
        self.assertFalse([flag for flag in flags if "candidate" in flag or "--to" == flag])


# ------------------------------------------------------------------------------------------------
# Designer Q's D16 ticket goldens (S17), full text
# ------------------------------------------------------------------------------------------------


class GoldenTextTest(DeskCase):
    def test_every_golden_text(self) -> None:
        for name, (args, exit_code, lines) in GOLDEN_TEXTS.items():
            with self.subTest(name=name):
                code, out = self.run_text(*args)
                self.assertEqual(exit_code, code, out)
                self.assertEqual("\n".join(lines) + "\n", out)
                self.assertIsNone(structuring_text.OCC_RE.search(out))

    def test_the_json_carries_the_same_text_and_its_lint(self) -> None:
        for name in ("wmt_roll", "nvda_roll", "spy_monetize", "wmt_let_assign", "wmt_monetize"):
            args, exit_code, lines = GOLDEN_TEXTS[name]
            with self.subTest(name=name):
                code, body = self.run_json(*args)
                self.assertEqual(exit_code, code)
                payload = body["payload"]
                self.assertEqual(set(exit_ticket.PAYLOAD_KEYS), set(payload))
                self.assertEqual("\n".join(lines), payload["message"])
                self.assertEqual("ok", body["status"])
                self.assertEqual({"ok": True, "problems": [], "numbers_checked": payload["lint"]["numbers_checked"]},
                                 payload["lint"])
                self.assertEqual("structuring", payload["emitted"]["footer"])
                self.assertEqual([], payload["emitted"]["rule_ids"])
                result = alert_lint.check(payload["message"], payload["emitted"]["emitted_numbers"], rule_ids=[],
                                          identifiers=payload["emitted"]["identifiers"],
                                          clocks=payload["emitted"]["clocks"],
                                          as_of=contract.parse_utc_instant(payload["emitted"]["as_of"]),
                                          footer="structuring")
                self.assertTrue(result.ok, result.problems)

    def test_the_wmt_roll_payload(self) -> None:
        code, body = self.run_json(*GOLDEN_TEXTS["wmt_roll"][0])
        payload = body["payload"]
        self.assertEqual((0, "TICKET", "TK-AG-20261020-01-ROLL-20261103T195000Z", "covered_call", "margin"),
                         (code, payload["verdict"], payload["ticket_id"], payload["structure"], payload["account_type"]))
        self.assertEqual(["close_short", "open_short"], [leg["intent"] for leg in payload["legs"]])
        self.assertEqual(["WMT261120C00105000", "WMT270115C00115000"], [leg["contract"] for leg in payload["legs"]])
        self.assertEqual([("ask", "3.50", "-700.00", "-685.00"), ("bid", "3.55", "710.00", "720.00")],
                         [(leg["conservative_side"], leg["conservative"], leg["cash_conservative"], leg["cash_mid"])
                          for leg in payload["legs"]])
        self.assertEqual(["0.10", "0.10"], [leg["tick"] for leg in payload["legs"]])  # standard class at 3.00 and up
        self.assertEqual({"net_conservative": "10.00", "net_mid": "35.00", "peak_needed": "700.00",
                          "prefix": ["-700.00", "10.00"]}, payload["cash"])
        self.assertEqual([{"row_number": 4, "conservative": "-16.00", "mid": "-1.00"}], payload["realized"])
        self.assertEqual((True, 1, 2, None), (payload["approval"]["ok"], payload["approval"]["required_level"],
                                              payload["approval"]["owner_level"], payload["approval"]["exposure_step"]))
        self.assertEqual([0, 1], [step["level"] for step in payload["steps"]])
        replacement = payload["replacement"]
        self.assertEqual(("replacement_call", "roll", "WMT270115C00115000", "ELIGIBLE", "0.0467", "10.00"),
                         (replacement["kind"], replacement["section"], replacement["candidate"]["id"],
                          replacement["candidate"]["status"], replacement["candidate"]["score"],
                          replacement["candidate"]["net_credit_total"]))
        self.assertEqual("extrinsic_per_day", replacement["objective"]["id"])
        self.assertIsNotNone(payload["model_note"])
        self.assertEqual({"made": 4, "chains": 1, "per_run_max_requests": 40, "per_run_max_chains": 4,
                          "budget_left": 176}, payload["requests"])
        self.assertEqual("5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c",
                         payload["criteria"]["sha256"])
        self.assertEqual("2026-11-03T20:20:00+00:00", payload["valid_until"])
        self.assertTrue(payload["valid_full"])
        self.assertEqual("open", payload["session"]["session"])

    def test_the_wmt_let_assign_payload(self) -> None:
        _code, body = self.run_json(*GOLDEN_TEXTS["wmt_let_assign"][0])
        (leg,) = body["payload"]["let_assign"]
        self.assertEqual(("WMT261120C00105000", False, "104.23", "2026-11-20T21:00:00+00:00", "21000.00", "1984.00"),
                         (leg["contract"], leg["itm"], leg["spot_mid"], leg["act_by_utc"], leg["cash_if_assigned"],
                          leg["realized_if_assigned"]))
        self.assertEqual([], body["payload"]["legs"])
        self.assertIsNone(body["payload"]["cash"])

    def test_the_expiry_day_close_at_grade_e(self) -> None:
        code, out = self.run_text(WMT, "CLOSE", now=EXPIRY_DAY_NOW, fixture_set="expiry_day")
        self.assertEqual(0, code, out)
        self.assertEqual("\n".join(EXPIRY_DAY_CLOSE) + "\n", out)

    def test_the_expiry_day_roll(self) -> None:
        code, body = self.run_json(WMT, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "115",
                                   now=EXPIRY_DAY_NOW, fixture_set="expiry_day")
        self.assertEqual((0, "TICKET"), (code, body["payload"]["verdict"]))
        lines = body["payload"]["message"].split("\n")
        self.assertIn("- \U0001F534 **earnings** 2026-11-17 has passed, the next is unknown: counted inside the new "
                      "call's life | excluded by your structuring.toml", lines)
        self.assertIn("- leg 1 of 2: closes your short 2 x WMT 2026-11-20 105 call | bid 0.12, ask 0.16, mid 0.14 | quote "
                      "grade E | quote grade E: the conservative side stands", lines)


# ------------------------------------------------------------------------------------------------
# Files and the journal
# ------------------------------------------------------------------------------------------------


class FilesTest(DeskCase):
    def test_a_ticket_writes_its_pair_once_and_a_rerun_is_a_no_op(self) -> None:
        code, body = self.run_json(WMT, "CLOSE")
        stem = "TK-AG-20261020-01-CLOSE-20261103T195000Z"
        self.assertEqual(0, code)
        self.assertEqual({"json": "tickets/%s.json" % stem, "md": "tickets/%s.md" % stem, "written": True},
                         body["payload"]["files"])
        self.assertEqual([stem + ".json", stem + ".md"], self.ticket_files())
        with open(os.path.join(self.paths.tickets_dir, stem + ".md"), encoding="utf-8") as handle:
            self.assertEqual(structuring_text.report_markdown("Ticket %s" % stem, body["payload"]["message"]), handle.read())
        with open(os.path.join(self.paths.tickets_dir, stem + ".json"), encoding="utf-8") as handle:
            document = json.load(handle)
        # the standard envelope, as roll_analysis.py and propose.py write their reports, without the run's own keys
        self.assertEqual((contract.SCHEMA_VERSION, "exit_ticket", "ok", "2026-11-03T19:50:00+00:00", body["source"],
                          body["delay_min"]),
                         (document["schema_version"], document["tool"], document["status"], document["generated_at"],
                          document["source"], document["delay_min"]))
        self.assertEqual((stem, body["payload"]["message"]), (document["payload"]["ticket_id"],
                                                              document["payload"]["message"]))
        self.assertEqual(sorted(key for key in exit_ticket.PAYLOAD_KEYS if key not in exit_ticket.FILE_EXCLUDED),
                         sorted(document["payload"]))
        for key in ("requests", "warnings", "files", "journal", "exit_code"):
            self.assertNotIn(key, document["payload"])
        before = {name: sha256_file(os.path.join(self.paths.tickets_dir, name)) for name in self.ticket_files()}
        code, body = self.run_json(WMT, "CLOSE")
        self.assertEqual((0, False), (code, body["payload"]["files"]["written"]))
        self.assertEqual(0, body["payload"]["requests"]["made"])  # served from the quote cache, same bytes
        self.assertEqual(before, {name: sha256_file(os.path.join(self.paths.tickets_dir, name))
                                  for name in self.ticket_files()})
        self.assertEqual(2, len(self.journal()))

    def test_a_conflicting_file_is_never_overwritten(self) -> None:
        stem = "TK-AG-20261020-01-CLOSE-20261103T195000Z"
        os.makedirs(self.paths.tickets_dir)
        with open(os.path.join(self.paths.tickets_dir, stem + ".json"), "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        code, body = self.run_json(WMT, "CLOSE")
        self.assertEqual((1, "error", "ticket_exists"), (code, body["status"], body["error"]["code"]))
        self.assertEqual([stem + ".json"], self.ticket_files())
        self.assertEqual([], self.journal())
        code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(1, code)
        self.assertTrue(out.startswith("error: "), out)

    def test_files_only_on_a_ticket(self) -> None:
        for args in ((WMT, "MONETIZE_PUT"), (WMT, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "117")):
            with self.subTest(args=args):
                code, body = self.run_json(*args)
                self.assertIsNone(body["payload"]["files"])
        self.assertEqual([], self.ticket_files())
        code, _body = self.run_json(WMT, "CLOSE", now=AFTER_CLOSE_NOW, fixture_set="after_close")
        self.assertEqual(20, code)
        self.assertEqual([], self.ticket_files())

    def test_the_journal_records_every_printed_text(self) -> None:
        code, out = self.run_text(NVDA, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "160")
        self.assertEqual(0, code)
        self.run_json(WMT, "MONETIZE_PUT")
        self.run_json(WMT, "CLOSE", now=AFTER_CLOSE_NOW, fixture_set="after_close")
        records = self.journal()
        self.assertEqual(3, len(records))
        self.assertEqual(set(structuring_text.JOURNAL_KEYS), set(records[0]))
        first = records[0]
        self.assertEqual(("exit_ticket", "ROLL", "covered_call", NVDA, "TICKET", "2026-11-03T19:50:00+00:00"),
                         (first["tool"], first["action"], first["structure"], first["trade_id"], first["verdict"],
                          first["utc"]))
        self.assertEqual(hashlib.sha256(out[:-1].encode("utf-8")).hexdigest(), first["text_sha256"])
        self.assertEqual("5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c", first["criteria_sha256"])
        self.assertEqual(sha256_file(self.paths.desk_toml), first["desk_toml_sha256"])
        self.assertEqual(sha256_file(self.paths.positions_csv), first["positions_csv_sha256"])
        self.assertEqual(("NOT_APPLICABLE", None), (records[1]["verdict"], records[1]["refused_code"]))
        self.assertEqual(("UNAVAILABLE", "market_closed"), (records[2]["verdict"], records[2]["refused_code"]))
        self.assertEqual("journal/structuring.jsonl", json.loads(call(exit_ticket.main, self.argv(WMT, "CLOSE"))[1])
                         ["payload"]["journal"])
        # the desk's other journals are never touched
        self.assertFalse(os.path.exists(os.path.join(self.root, "journal", "signals.jsonl")))
        self.assertFalse(os.path.exists(os.path.join(self.root, "journal", "alerts.jsonl")))


# ------------------------------------------------------------------------------------------------
# Usage, errors and the answers that print no ticket
# ------------------------------------------------------------------------------------------------


class UsageTest(DeskCase):
    def usage(self, argv: List[str]) -> Dict[str, Any]:
        code, out = call(exit_ticket.main, argv + ["--json"])
        self.assertEqual(2, code, out)
        body = json.loads(out)
        self.assertEqual(("usage", "invalid_arguments"), (body["status"], body["error"]["code"]))
        self.screen(out)
        return body

    def test_each_usage_error(self) -> None:
        base = ["--offline", "--desk-root", self.root, "--now", REGULAR_NOW]
        cases = [
            ["--action", "CLOSE"] + base,
            ["--trade", WMT] + base,
            ["--trade", "WMT-1", "--action", "CLOSE"] + base,
            ["--trade", WMT, "--action", "EXPIRE"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-strike", "115"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-expiration", "2027-01-15"] + base,
            ["--trade", WMT, "--action", "CLOSE", "--new-expiration", "2027-01-15", "--new-strike", "115"] + base,
            ["--trade", WMT, "--action", "CLOSE", "--new-strike", "115"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-expiration", "2027-1-15", "--new-strike", "115"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-expiration", "2027-02-30", "--new-strike", "115"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "0"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "-5"] + base,
            ["--trade", WMT, "--action", "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "abc"] + base,
            ["--trade", WMT, "--action", "CLOSE", "--fixture-set", "regular", "--desk-root", self.root],
        ]
        for argv in cases:
            with self.subTest(argv=argv):
                self.usage(argv)
        self.assertEqual([], self.journal())

    def test_the_replacement_must_expire_after_the_held_leg(self) -> None:
        for day in ("2026-11-20", "2026-11-13"):
            with self.subTest(day=day):
                body = self.usage(["--trade", WMT, "--action", "ROLL", "--new-expiration", day, "--new-strike", "110",
                                   "--offline", "--desk-root", self.root, "--now", REGULAR_NOW])
                self.assertIn("must be later than the held leg's expiration 2026-11-20", body["error"]["message"])
        self.assertEqual([], self.journal())
        self.assertEqual([], self.ticket_files())

    def test_unknown_trade_and_unusable_files(self) -> None:
        code, body = self.run_json("AG-20990101-01", "CLOSE")
        self.assertEqual((1, "unknown_trade"), (code, body["error"]["code"]))
        with open(self.paths.structuring_toml, "a", encoding="utf-8") as handle:
            handle.write("surprise = 1\n")
        self.ack(now="2026-11-03T19:49:30Z")
        code, body = self.run_json(WMT, "CLOSE")
        self.assertEqual((1, "structuring_invalid"), (code, body["error"]["code"]))
        self.assertEqual([], self.journal())


class QuietTest(DeskCase):
    def test_replacement_not_listed(self) -> None:
        code, body = self.run_json(WMT, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "117")
        payload = body["payload"]
        self.assertEqual((20, "unavailable", "UNAVAILABLE", "replacement_not_listed", 4),
                         (code, body["status"], payload["verdict"], payload["cause"], payload["requests"]["made"]))
        self.assertEqual("UNAVAILABLE | AG-20261020-01 ROLL ticket | the chain does not list that contract\n\n" + FOOTER
                         + "19:50 UTC", payload["message"])

    def test_market_closed(self) -> None:
        for fixture_set, now, clock in (("after_close", AFTER_CLOSE_NOW, "21:25"), ("holiday", HOLIDAY_NOW, "15:00")):
            with self.subTest(fixture_set=fixture_set):
                if fixture_set == "holiday":
                    self.ack(now="2026-11-26T14:59:00Z")
                code, out = self.run_text(WMT, "CLOSE", now=now, fixture_set=fixture_set)
                self.assertEqual(20, code)
                self.assertEqual("UNAVAILABLE | AG-20261020-01 CLOSE ticket | a ticket needs quotes live enough to act "
                                 "on: the session is not open\n\n" + FOOTER + clock + " UTC\n", out)
                code, body = self.run_json(WMT, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "115", now=now,
                                           fixture_set=fixture_set)
                self.assertEqual((20, "market_closed", 0), (code, body["payload"]["cause"],
                                                            body["payload"]["requests"]["made"]))

    def test_refused_at_a_lower_acknowledged_level(self) -> None:
        with open(self.paths.desk_toml, encoding="utf-8") as handle:
            text = handle.read()
        old = "approval_level = 2 "
        self.assertEqual(1, text.count(old))
        with open(self.paths.desk_toml, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, "approval_level = 1 "))
        code, body = self.run_json(SPY, "ROLL", "--new-expiration", "2027-03-19", "--new-strike", "610")
        self.assertEqual((20, "config_unacknowledged"), (code, body["payload"]["cause"]))  # the edit is not acknowledged
        self.ack(now="2026-11-03T19:49:30Z")
        code, out = self.run_text(SPY, "ROLL", "--new-expiration", "2027-03-19", "--new-strike", "610")
        self.assertEqual(20, code)
        self.assertEqual("REFUSED AG-20261028-03 | SPY protective put | opening 1 new long put needs level 2 by the desk's "
                         "convention; desk.toml records your self-attested level 1 | the desk shows nothing above your "
                         "level; your broker's own approval decides what you can enter\n\n" + FOOTER + "19:50 UTC\n", out)
        code, body = self.run_json(SPY, "ROLL", "--new-expiration", "2027-03-19", "--new-strike", "610")
        payload = body["payload"]
        self.assertEqual(("REFUSED", "approval_level", 0, None), (payload["verdict"], payload["refused_code"],
                                                                 payload["requests"]["made"], payload["files"]))
        self.assertEqual((False, 2, 1), (payload["approval"]["ok"], payload["approval"]["required_level"],
                                         payload["approval"]["owner_level"]))
        code, _out = self.run_text(SPY, "MONETIZE_PUT")
        self.assertEqual(0, code)  # a closing ticket needs no level
        self.assertEqual("approval_level", self.journal()[-2]["refused_code"])

    def test_config_unacknowledged_comes_first(self) -> None:
        with open(self.paths.structuring_toml, "a", encoding="utf-8") as handle:
            handle.write("# an edit\n")
        new = sha256_file(self.paths.structuring_toml)
        code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(20, code)
        self.assertEqual("UNAVAILABLE | CONFIG CHANGED: structuring.toml: sha 5155427c -> sha %s | the desk names no "
                         "candidate and prints no ticket while desk.toml, positions.csv or structuring.toml differ from "
                         "what you acknowledged | acknowledge with ack.py config --owner-tapped\n\n" % new[:8]
                         + FOOTER + "19:50 UTC\n", out)
        code, body = self.run_json(WMT, "CLOSE")
        payload = body["payload"]
        self.assertEqual(("UNAVAILABLE", "config_unacknowledged", None), (payload["verdict"], payload["cause"],
                                                                          payload["requests"]))
        self.assertEqual("desk.toml, positions.csv or structuring.toml differ from what you acknowledged",
                         payload["reason"])
        self.assertEqual([{"name": "structuring_toml", "old": "5155427cbe37f8ff252db1181ff766c3d4be06739aca3a15a1b58055479a8d5c",
                           "new": new}], payload["config_changed"])
        self.assertEqual("config_unacknowledged", self.journal()[-1]["refused_code"])
        self.assertFalse(os.path.exists(os.path.join(self.root, "data", "cache", "ledger.jsonl")))

    def test_a_rulebook_change_alone_prints_first_and_does_not_block(self) -> None:
        rulebook = exit_check_tool.adopted_target(self.paths)
        self.assertEqual(os.path.join(self.root, "rules", "exit_rules-1.0.0.toml"), rulebook)
        old = sha256_file(rulebook)
        with open(rulebook, "a", encoding="utf-8") as handle:
            handle.write("\n")
        code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(0, code, out)
        lines = out.split("\n")
        self.assertEqual("CONFIG CHANGED: adopted rulebook: sha %s -> sha %s | the structuring text below reads the files "
                         "as they are now; acknowledge with ack.py config --owner-tapped" % (old[:8], sha256_file(rulebook)[:8]),
                         lines[0])
        self.assertEqual(["", GOLDEN_TEXTS["wmt_close"][2][0]], lines[1:3])


class UnacknowledgedDeskTest(DeskCase):
    acknowledge = False

    def test_a_desk_never_acknowledged(self) -> None:
        code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(20, code)
        self.assertTrue(out.startswith("UNAVAILABLE | CONFIG CHANGED: desk.toml: not acknowledged -> sha %s | positions.csv: "
                                       "not acknowledged -> sha %s | adopted rulebook: not acknowledged"
                                       % (sha256_file(self.paths.desk_toml)[:8], sha256_file(self.paths.positions_csv)[:8])),
                        out)
        self.assertIn("structuring.toml: not acknowledged -> sha 5155427c", out)


class CriteriaMissingTest(DeskCase):
    criteria = False

    def test_criteria_missing(self) -> None:
        code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(20, code)
        self.assertEqual("UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your "
                         "criteria (SETUP step 9)\n\n" + FOOTER + "19:50 UTC\n", out)
        code, body = self.run_json(WMT, "CLOSE")
        self.assertEqual(("criteria_missing", None, None), (body["payload"]["cause"], body["payload"]["criteria"],
                                                            self.journal()[-1]["criteria_sha256"]))
        self.assertEqual("no structuring.toml: the Structuring Desk shows nothing until you set your criteria",
                         body["payload"]["reason"])

    def test_a_rulebook_change_prints_before_the_missing_criteria(self) -> None:
        rulebook = exit_check_tool.adopted_target(self.paths)
        old = sha256_file(rulebook)
        with open(rulebook, "a", encoding="utf-8") as handle:
            handle.write("\n")
        code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(20, code)
        self.assertEqual("CONFIG CHANGED: adopted rulebook: sha %s -> sha %s | the structuring text below reads the files "
                         "as they are now; acknowledge with ack.py config --owner-tapped\n" % (old[:8], sha256_file(rulebook)[:8])
                         + "UNAVAILABLE | no structuring.toml: the Structuring Desk shows nothing until you set your "
                         "criteria (SETUP step 9)\n\n" + FOOTER + "19:50 UTC\n", out)


class MarketDataRefusedTest(DeskCase):
    """Market data the desk cannot use as configured is exit 20 UNAVAILABLE, as in every landed market tool
    (``quote_cache.MarketDataRefused``: "Tools map it to exit 20"), and so is an expired holiday table."""

    def test_refused_market_data_and_an_expired_holiday_table_are_unavailable(self) -> None:
        for error, code in ((quote_cache.MarketDataRefused(quote_cache.REFUSED_NO_CREDENTIAL, quote_cache.NO_CREDENTIAL_MESSAGE),
                             "no_credential"),
                            (market_clock.TableExpiredError("the table ends 2027-12-31"), "holiday_table_expired")):
            with self.subTest(code=code):
                with mock.patch.object(exit_ticket.quote_cache, "open_market_data", side_effect=error):
                    exit_code, body = self.run_json(WMT, "CLOSE")
                self.assertEqual((20, "unavailable", code), (exit_code, body["status"], body["error"]["code"]))
                with mock.patch.object(exit_ticket.quote_cache, "open_market_data", side_effect=error):
                    exit_code, out = self.run_text(WMT, "CLOSE")
                self.assertEqual(20, exit_code)
                self.assertTrue(out.startswith("UNAVAILABLE: "), out)
        self.assertEqual([], self.ticket_files())


# ------------------------------------------------------------------------------------------------
# The self-lint, the requests, --now and the network
# ------------------------------------------------------------------------------------------------


class WithheldTest(DeskCase):
    def test_a_text_that_fails_the_lint_is_withheld(self) -> None:
        original = tickets.ticket_lines

        def bad(w: Any, ticket: Any, changes: Any = (), delay_min: int = 15) -> List[str]:
            return original(w, ticket, changes, delay_min) + ["close it now, you should"]

        with mock.patch.object(tickets, "ticket_lines", side_effect=bad):
            code, out = self.run_text(WMT, "CLOSE")
            json_code, raw = call(exit_ticket.main, self.argv(WMT, "CLOSE"))  # the JSON shows the text that failed
        body = json.loads(raw)
        self.assertEqual((1, 1), (code, json_code))
        self.assertEqual("WITHHELD | the text of exit_ticket.py failed the alert lint (forbidden_language, "
                         "imperative_start) and is withheld; the JSON of exit_ticket.py --json shows it\n\n" + FOOTER
                         + "19:31 UTC\n", out)
        payload = body["payload"]
        self.assertEqual(("error", False, 1), (body["status"], payload["lint"]["ok"], payload["exit_code"]))
        self.assertIn("close it now, you should", payload["withheld"]["text"])
        self.assertEqual(out[:-1], payload["message"])
        self.assertEqual(["forbidden_language", "imperative_start"], payload["withheld"]["codes"])
        self.assertEqual(([], []), (self.ticket_files(), self.journal()))


class RequestsTest(DeskCase):
    def test_request_counts(self) -> None:
        # a quote batch with an option leg is two requests (equities, then options); a chain is two more
        cases = [((WMT, "CLOSE"), 2), ((WMT, "LET_ASSIGN"), 2), ((SPY, "MONETIZE_PUT"), 2), ((SPY, "UNWIND_STOCK"), 2),
                 ((NVDA, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "160"), 4), ((WMT, "MONETIZE_PUT"), 0)]
        for args, made in cases:
            with self.subTest(args=args):
                self.tearDown()
                self.setUp()  # a fresh acknowledged copy: an empty quote cache and ledger
                _code, body = self.run_json(*args)
                self.assertEqual(made, body["payload"]["requests"]["made"])

    def test_now_is_honoured(self) -> None:
        code, body = self.run_json(WMT, "CLOSE", now="2026-11-03T19:55:07Z")
        payload = body["payload"]
        self.assertEqual(("2026-11-03T19:55:07+00:00", "2026-11-03T19:55:07+00:00", "TK-AG-20261020-01-CLOSE-20261103T195507Z",
                          "2026-11-03T20:25:07+00:00"),
                         (body["generated_at"], payload["computed_utc"], payload["ticket_id"], payload["valid_until"]))
        self.assertEqual("2026-11-03T19:55:07+00:00", self.journal()[-1]["utc"])
        self.assertIn("*account 1 of desk.toml (margin) | computed 19:55 UTC | valid until 20:25 UTC (30 min) | "
                      "quotes 19:31 UTC, 15-min delayed*",
                      payload["message"].split("\n"))
        self.assertIn("> - after 20:25 UTC its quotes are too old, and a new ticket needs a new request",
                      payload["message"].split("\n"))

    def test_every_request_is_an_allow_listed_get(self) -> None:
        original = alpaca_client.OfflineTransport.fetch
        seen: List[str] = []

        def recording(transport: Any, url: str, headers: Any, timeout_s: float) -> Any:
            seen.append(url)
            return original(transport, url, headers, timeout_s)

        with mock.patch.object(alpaca_client.OfflineTransport, "fetch", autospec=True, side_effect=recording), \
                mock.patch("http.client.HTTPConnection.request", autospec=True) as request:
            code, body = self.run_json(WMT, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "115")
        self.assertEqual(0, code)
        self.assertEqual(0, request.call_count)
        paths = ["/v2/stocks/quotes/latest", "/v1beta1/options/quotes/latest", "/v2/options/contracts",
                 "/v1beta1/options/snapshots/WMT"]
        self.assertEqual(paths, [urlsplit(url).path for url in seen])
        self.assertTrue(all(alpaca_client.host_for_path(urlsplit(url).path) == urlsplit(url).hostname for url in seen))
        with open(os.path.join(self.root, "data", "cache", "ledger.jsonl"), encoding="utf-8") as handle:
            ledger = [json.loads(line) for line in handle]
        self.assertEqual(paths, [record["path"] for record in ledger])


QUARANTINED_ROW = ("option,WMT,WMT261218C00110000,-2,,,,Brokerage (margin),AG-20261030-05,2026-10-30,-400.00,,2.50,open,"
                   ",,,,")
WMT_CALL_ROW = ("option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,open,,,,,"
                "covered call #assign-ok")


class FixerTest(DeskCase):
    """The PR 3b review, through the tool: L2-1/L3-1 (repro A), L3-2, L3-3, L3-6 and BD4."""

    acknowledge = False

    def edit(self, relative: str, old: str, new: str) -> None:
        path = os.path.join(self.root, relative)
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(1, text.count(old), old)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace(old, new))

    def test_a_quarantined_group_fails_closed(self) -> None:
        self.edit("positions.csv", WMT_CALL_ROW, WMT_CALL_ROW + "\n" + QUARANTINED_ROW)
        self.ack()
        for action in ("UNWIND_STOCK", "CLOSE"):
            with self.subTest(action=action):
                code, body = self.run_json(WMT, action)
                payload = body["payload"]
                self.assertEqual((20, "UNAVAILABLE", "positions_quarantined", 0),
                                 (code, payload["verdict"], payload["cause"], payload["requests"]["made"]))
                self.assertNotIn("after leg", payload["message"])
        self.assertEqual([], self.ticket_files())
        self.assertEqual(["positions_quarantined", "positions_quarantined"], [r["refused_code"] for r in self.journal()])

    def test_an_account_named_with_a_desk_word_prints_the_ticket(self) -> None:
        for relative in ("desk.toml", "positions.csv"):
            path = os.path.join(self.root, relative)
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(text.replace("Roth IRA", "options"))
        self.ack()
        code, out = self.run_text(WMT, "ROLL", "--new-expiration", "2027-01-15", "--new-strike", "115")
        self.assertEqual(0, code, out)
        self.assertTrue(out.startswith("### TICKET AG-20261020-01 | WMT covered call"), out)

    def test_two_tickets_in_one_second_are_one_ticket(self) -> None:
        self.ack()
        first_code, first = self.run_json(WMT, "CLOSE", now="2026-11-03T19:50:00.100000Z")
        second_code, second = self.run_json(WMT, "CLOSE", now="2026-11-03T19:50:00.900000Z")
        self.assertEqual((0, 0), (first_code, second_code))
        self.assertEqual((True, False), (first["payload"]["files"]["written"], second["payload"]["files"]["written"]))
        self.assertEqual("2026-11-03T19:50:00+00:00", second["payload"]["computed_utc"])

    def test_refused_market_data_is_journaled(self) -> None:
        self.ack()
        error = quote_cache.MarketDataRefused("no_credential", quote_cache.NO_CREDENTIAL_MESSAGE)
        with mock.patch.object(exit_ticket.quote_cache, "open_market_data", side_effect=error):
            code, out = self.run_text(WMT, "CLOSE")
        self.assertEqual(20, code)
        (record,) = self.journal()
        self.assertEqual(("UNAVAILABLE", "no_credential", "CLOSE", hashlib.sha256(out[:-1].encode("utf-8")).hexdigest()),
                         (record["verdict"], record["refused_code"], record["action"], record["text_sha256"]))

    def test_an_unwind_of_fractional_shares(self) -> None:
        self.edit("positions.csv", "equity,WMT,,200,", "equity,WMT,,200.5,")
        self.ack()
        code, out = self.run_text(WMT, "UNWIND_STOCK")
        self.assertEqual(0, code, out)
        self.assertIn("leg 2 of 2: closes 200.5 of your long WMT shares", out)


class VerifierTest(DeskCase):
    """The PR 3b verifier: the JSON of a fractional unwind (BD4) matches its --schema."""

    acknowledge = False

    def test_a_fractional_unwind_payload_matches_the_schema(self) -> None:
        path = os.path.join(self.root, "positions.csv")
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertEqual(1, text.count("equity,WMT,,200,"))
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text.replace("equity,WMT,,200,", "equity,WMT,,200.5,"))
        self.ack()
        code, body = self.run_json(WMT, "UNWIND_STOCK")
        self.assertEqual(0, code, body)
        payload = body["payload"]
        self.assertEqual([2, "200.5"], [leg["quantity"] for leg in payload["legs"]])
        self.assertEqual([("200.5", 0), (0, 0)], [(step["after"]["shares"], step["after"]["short_calls"])
                                                  for step in payload["steps"]])
        self.assertIs(int, type(payload["steps"][-1]["after"]["shares"]))
        schema = exit_ticket.OUTPUT
        self.assertIn("decimal string", schema["legs"][0]["quantity"])
        self.assertIn("decimal string", schema["steps"][0]["after"]["shares"])


if __name__ == "__main__":
    unittest.main()
