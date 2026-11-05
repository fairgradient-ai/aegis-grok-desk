# positions.csv

`positions.csv` in your desk root (`/workspace/aegis-desk/positions.csv`) is the desk's book of record: one row per
position leg, written by you. This page is the reference for its format. The skill
[aegis-positions-csv-book-of-record](../skills/aegis-positions-csv-book-of-record/SKILL.md) holds the event-by-event
recipes Aegis Position Control follows.

## The file is yours

- No tool writes `positions.csv`. The tools read it, check it, group it and reconcile it.
- Aegis Position Control turns what you report into exact CSV lines in the file's own column order, and you apply
  them. The amounts always come from your brokerage record, never from a quote.
- The one exception: on your explicit request, Position Control saves the exact text you supply to the file. It is a
  verbatim transcription, never an edit and never rows it derived. It then runs `positions_check.py` and reports the
  verdict, the number of rows and the file's sha256 for you to confirm.
- After any change, the next book check posts CONFIG CHANGED first, naming `positions.csv`, until you tap
  `ack.py config --owner-tapped`. A change you did not make is visible at once.
- There is no broker import, no sync and no cash balance in the file.

## Columns

The file is Aegis's CSV import format (the core columns) with the desk's extension columns. Column names are matched
case-insensitively. Lines whose first non-space character is `#` are comments; blank lines are skipped. Unknown
columns are ignored, except a name within two edits of a known column, which is a file error: a typo never silently
drops money.

| Column | Kind | What it holds |
| --- | --- | --- |
| `instrument_type` | core, required | `equity`, `option`, `etf`, `index` or `crypto` |
| `quantity` | core, required | signed: negative is short (contracts for an option, shares for stock) |
| `symbol` | core | the ticker (an option row may give the OCC symbol instead) |
| `occ_symbol` | core | the OCC option symbol, for example `WMT261120C00105000` |
| `expiration`, `option_right`, `strike` | core | an option's parts, when there is no OCC symbol |
| `account` | core | the account name; every row of one group shares it |
| `cost_basis_total` | core | the signed cash flow of opening (see "Money conventions") |
| `cost_basis_per_unit` | core | stock rows only: the cost per share |
| `trade_id` | extension | `AG-YYYYMMDD-NN`: groups the legs of one structure |
| `opened` | extension | the opening date, `YYYY-MM-DD` |
| `open_price_per_share` | extension | option rows: the unsigned premium per share |
| `status` | extension | `open` or `closed` |
| `closed`, `closed_quantity` and `close_price_per_unit` | extension | the close: date, unsigned quantity, unsigned price per share (0 = expired worthless) |
| `underlying_stop` | extension | your stop on the underlying, read by the rule `USTOP` |
| `note` | extension | free text; its `#tags` are read |

Every row needs `instrument_type`, `quantity`, and `symbol` or `occ_symbol`. Dates are `YYYY-MM-DD`.

## Money conventions

- `cost_basis_total` is the signed cash flow of opening: paid is positive, received is negative. Its sign must equal
  the sign of `quantity`: a long position was paid for, a short position was paid to you. Otherwise the row error
  reads `cost basis sign must equal quantity sign: paid is positive, received is negative`.
- A stock or ETF row may give `cost_basis_per_unit` instead: total = quantity x per unit.
- An option row gives `cost_basis_total` or `open_price_per_share`: total = quantity x 100 x premium, with the sign
  of the quantity. When both are present they must agree within 0.01 x |quantity| x 100. `cost_basis_per_unit` on an
  option row is refused: `ambiguous for options: use cost_basis_total or open_price_per_share`.
- A partial close: `closed_quantity`, `closed` and `close_price_per_unit`, with `status` still `open`.
  `status = closed` needs `closed` and `close_price_per_unit`; a blank `closed_quantity` then means the whole row.
- Every amount is read as an exact decimal from the cell text. No exponents, no NaN.

## Tags and trade ids

- `trade_id` is `AG-` followed by eight digits of a date, a hyphen and two or three digits, for example
  `AG-20261020-01`. Every row of one structure shares it. A row without one forms its own group, `ROW-<row number>`.
- Tags in `note` (a tag silences a rule only when the rule's `suppress_if_tag` key names it; the rules below are the
  example rulebook's):
  - `#assign-ok`: you accept assignment; silences the rules `EXDIV`, `DELTA_WATCH`, `DELTA_SIGNAL` and `CAP` for the
    group;
  - `#earnings-ok`: silences `EARN2` for the group;
  - `#horizon:YYYY-MM`: your intended horizon;
  - a `#roll-of:AG-...` tag: this group replaced that one;
  - `#from:AG-...`: a stock row that came from that group's assignment or exercise.

## Validation: file errors, row quarantine, plausibility

`positions_check.py` validates the file, groups the legs and checks every open leg against its quote once:

```text
python3 /workspace/aegis-grok-desk/scripts/positions_check.py --desk-root /workspace/aegis-desk
```

- **A file error** stops the whole file: a bad header, the encoding, more than 5,000 rows, a duplicate column, a
  near-typo column name (`did_you_mean`), or unreadable CSV. Synthetic, a fixture copy whose header says
  `cost_basis_totl` (2026-11-03, 19:50 UTC):

  ```text
  ERRORS: positions.csv cannot be used: row 1 did_you_mean: unknown column 'cost_basis_totl': did you mean 'cost_basis_total'?
  ```

- **A row error** quarantines that row's whole group (every row with its `trade_id`); the other groups still load.
  A quarantined group is UNAVAILABLE in every message until you fix the row. Synthetic, a fixture copy whose WMT call
  row lost the minus sign of its `cost_basis_total`:

  ```text
  quarantined AG-20261020-01: row 4 open_price_per_share: cost_basis_total 684.00 disagrees with open_price_per_share 3.42 (quantity -2 x 100 x 3.42 = -684.00) (AG-20261020-01)
  ```

- **Formula guard.** A cell that starts with `=`, `+` or `@`, or a text cell that starts with `-`, is refused, so a
  spreadsheet formula never enters the book.
- **Plausibility.** The first time an open option leg is checked, an entry premium per share outside
  [mid / 20, mid x 20] of its quote is the row error `unit mismatch?` (usually a total typed as a per-share price).
  A stock row outside [mid / 10, mid x 10] is a warning.
- Index underlyings cannot be quoted on the sandbox: they get the warning `UNQUOTABLE_INDEX`, not an error.

The exit codes: 0 CLEAN, 20 when a group is quarantined or a check could not run, 1 on a file error.

## How rows become structures

The desk groups rows by `trade_id` and names the structure from the OPEN legs; closed rows stay in the group for
realized P/L.

| Structure | Open legs |
| --- | --- |
| `long_stock`, `short_stock` | one stock-like leg |
| `long_call`, `long_put` | one long option |
| `short_call` | one short call (undefined risk) |
| `cash_secured_put` | one short put; the warning `cash reserve not verified` (the file holds no cash) |
| `covered_call` | long shares and one short call, with at least 100 shares per call |
| `protective_put` | long shares and long put leg(s) |
| `collar` | long shares, one long put and one short call (the shares cover the calls) |
| `vertical_credit_call`, `vertical_credit_put`, `vertical_debit_call`, `vertical_debit_put` | two options of the same right and expiry, equal size, opposite signs; credit when the net cost is negative |
| `custom` | anything else, reported with the warning `custom structure` |

Every row of a group must share the account and the underlying, or the group is quarantined. Two open rows of the same
contract are one leg, matched first in first out by `opened`. Which rules reach each structure is on
[the exit rules page](EXIT_RULES.md).

## Reconciliation and proposed edits

`csv_recon.py` runs in the 07:30 preopen, in the 16:35 end-of-day routine and on Saturday at 09:30. It settles expired legs from
the official close, checks quotes, coverage and roll links, lists the proposed edits (never applies them) and saves a
snapshot of the file:

```text
python3 /workspace/aegis-grok-desk/scripts/csv_recon.py --desk-root /workspace/aegis-desk
python3 /workspace/aegis-grok-desk/scripts/csv_recon.py --diff --desk-root /workspace/aegis-desk
```

The verdict: CLEAN (exit 0), EDITS_PENDING (exit 10), UNAVAILABLE (exit 20) or ERRORS (exit 1). An expired leg's
outcome is one of:

| Outcome | What it means |
| --- | --- |
| `EXPIRED_OTM` | not in the money by one cent at the close; edit: the row closed at 0 |
| `PROBABLE_ASSIGNED`, `PROBABLE_EXERCISED` | in the money at the close; edits follow the convention below |
| `PIN_UNCERTAIN` | the close within the pin band of the strike (0.25 by default), or the next session opened across the strike: confirm with your broker; no edit |
| `CASH_SETTLED` | an AM-settled index with its settlement value in `research/calendar.toml`; edit: the row closed at its intrinsic value |
| `SETTLEMENT_UNKNOWN` | an AM-settled index without that value: you record it |
| `SETTLEMENT_PENDING` | an AM-settled index whose value does not exist yet; nothing to do |
| `EXPIRED_UNRECORDED` | no official close for the expiry day: you record the outcome |

The one assignment convention: the option row closes at 0, so its premium stays in the option's P/L, and the stock is
booked at the strike. A short put assigned or a long call exercised adds a long stock row at the strike, with the
note `#from:<trade_id> assigned` or `exercised`. A short call assigned or a long put exercised delivers the shares
from the group's stock rows, first in first out, at `close_price_per_unit` equal to the strike.

Synthetic, the Saturday after the fixture's 2026-11-20 expiry (post_expiry set, 2026-11-21 14:31 UTC), with `--diff`:

```text
EDITS_PENDING (2026-11-21 ET): 2 expired legs, 3 proposed edits, 0 unavailable items, 0 warnings
AG-20261020-01 row 4 WMT261120C00105000 -2 EXPIRED_OTM: WMT closed 104.40 on 2026-11-20: the 105 call expired out of the money (edit proposed)
AG-20261022-02 row 6 NVDA261120C00150000 -2 PROBABLE_ASSIGNED: NVDA closed 153.40 on 2026-11-20: the 150 call finished 3.40 in the money; short: assignment probable (edits proposed)
quote check not run: market_closed
snapshot: data/positions/2026-11-21.csv sha256 9483c8f3dffd4b7ad4b3358a6a9b7b453829a85f6b4f8ac246a96f16ae60afd3
proposed edits (3; not applied: positions.csv is yours to edit):
# row 4 (AG-20261020-01) EXPIRED_OTM: status=closed, closed=2026-11-20, closed_quantity=2, close_price_per_unit=0
option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,closed,2026-11-20,2,0,,covered call #assign-ok
# row 6 (AG-20261022-02) PROBABLE_ASSIGNED: status=closed, closed=2026-11-20, closed_quantity=2, close_price_per_unit=0
option,NVDA,,-2,2026-11-20,CALL,150,Brokerage (margin),AG-20261022-02,2026-10-22,-610.00,,,closed,2026-11-20,2,0,,covered call from component columns #earnings-ok
# row 5 (AG-20261022-02) PROBABLE_ASSIGNED: status=closed, closed=2026-11-20, closed_quantity=200, close_price_per_unit=150.00
equity,NVDA,,200,,,,Brokerage (margin),AG-20261022-02,2026-10-22,28240.00,,,closed,2026-11-20,200,150.00,,
Tradier sandbox, delayed 15 min, as of 14:31 UTC
```

The outcomes are inferred from the official close. Check them against your broker before you apply any line. Other
findings `csv_recon.py` reports: `QUOTE_MISSING` (a symbol the vendor does not know), `UNCOVERED_SHORT_CALL` (a short
call left with fewer shares than it needs), the link check `ROLL_LINK` (a `#roll-of` or `#from` tag naming no group in the file) and
`ACTED_UNCHANGED` (you marked a signal acted with `ack.py acted`, and the group's rows are unchanged a trading day
later).

Each run writes `data/positions/<date>.csv` (a copy of the file), `<date>.sha256` and `<date>.recon.json`.

## Worked example (the fixture, synthetic)

The fixture book `tests/fixtures/desk/positions.csv`; every value in it is made up:

```text
instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened,cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,close_price_per_unit,underlying_stop,note
# Fixture book of record (plan section 8): paid is positive, received is negative; option rows carry cost_basis_total or open_price_per_share; stock rows may carry cost_basis_per_unit.
equity,WMT,,200,,,,Brokerage (margin),AG-20261020-01,2026-10-20,,98.50,,open,,,,92.00,core holding #horizon:2027-03
option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,open,,,,,covered call #assign-ok
equity,NVDA,,200,,,,Brokerage (margin),AG-20261022-02,2026-10-22,28240.00,,,open,,,,,
option,NVDA,,-2,2026-11-20,CALL,150,Brokerage (margin),AG-20261022-02,2026-10-22,-610.00,,,open,,,,,covered call from component columns #earnings-ok
etf,SPY,,100,,,,Roth IRA,AG-20261028-03,2026-10-28,63240.00,,,open,,,,,
option,SPY,SPY261218P00620000,1,,,,Roth IRA,AG-20261028-03,2026-10-28,1130.00,,11.30,open,,,,,protective put
option,SPY,SPY261016P00600000,-1,,,,Roth IRA,AG-20261001-04,2026-10-01,-480.00,,,closed,2026-10-14,1,0.55,,closed early
```

- WMT: 200 shares at 98.50 per share and two short calls opened at 3.42 (received, so `-684.00`), tagged
  `#assign-ok`, with a stop at 92.00.
- NVDA: the call written from its parts (`expiration`, `option_right`, `strike`) instead of an OCC symbol.
- SPY: 100 shares with one long put (paid, so `1130.00`), and a cash-secured put closed early at 0.55.

`positions_check.py` on it (regular set, 2026-11-03 19:50 UTC), the first lines:

```text
CLEAN: 7 rows, 4 groups (3 open, 1 closed), 0 quarantined; plausibility passed for 6 open legs
AG-20261020-01 covered_call WMT [Brokerage (margin)] net_cost 19016.00 (stock); rows 3, 4
AG-20261022-02 covered_call NVDA [Brokerage (margin)] net_cost 27630.00 (stock); rows 5, 6
AG-20261028-03 protective_put SPY [Roth IRA] net_cost 64370.00 (stock); rows 7, 8
AG-20261001-04 cash_secured_put SPY [Roth IRA] net_cost -480.00 (credit); rows 9; closed
```
