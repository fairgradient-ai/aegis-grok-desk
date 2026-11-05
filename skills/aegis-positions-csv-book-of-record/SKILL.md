---
name: aegis-positions-csv-book-of-record
description: The owner's positions.csv, the desk's book of record, for Aegis Position Control. Who writes it (the owner; the one verbatim-save exception), the columns and the required ones, the money conventions, trade ids and note tags, file errors, quarantined groups and the plausibility check, how rows become structures, and the recipes for opening, closing, partial closes, rolls, expiry, assignment and exercise, always as exact lines the owner applies, then positions_check and the configuration acknowledgement.
license: MIT
metadata:
  version: "0.1.0"
  author: FairGradient
  category: book
---

# positions.csv: the book of record

`/workspace/aegis-desk/positions.csv` is the one record of what the owner holds. Every tool reads it; no tool writes
it. Aegis Position Control keeps it honest: it checks the file, explains what the tools found, and proposes exact
lines. The owner decides what goes in the file. Field-by-field reference: [docs/POSITIONS_CSV.md](../../docs/POSITIONS_CSV.md).

## Who writes it

The owner writes the file, by hand, from the brokerage account's own records. The desk has no broker import and no
sync: the file is the whole book.

There is one exception, and Position Control states it in these terms:

- On the owner's explicit request, Position Control saves the exact text the owner supplies to
  `/workspace/aegis-desk/positions.csv`. It is a verbatim transcription, never an edit and never derived rows.
- It then runs `python3 scripts/positions_check.py --desk-root /workspace/aegis-desk` and reports the verdict, the
  number of rows and the file's sha256 (from the snapshot line of `csv_recon.py`), and the owner confirms them.
- Later changes follow the same path, with rows the owner supplies or approves.
- The next book check posts CONFIG CHANGED until the owner taps `ack.py config --owner-tapped`, so every change is
  visible.

Position Control never adds, removes or changes a row on its own, never fills a column the owner left blank, and never
computes a cost basis for the owner.

## Columns

The header row names the columns, in any order and any letter case. Unknown columns are ignored, except a name within
two typing errors of a known one, which stops the whole file (see Errors).

| Column | What it holds |
| --- | --- |
| `account` | the account name; every row of a group shares it |
| `symbol` | the ticker (the underlying for an option row given by its components) |
| `occ_symbol` | the OCC option symbol, for example `WMT261120C00105000` |
| `instrument_type` | `equity`, `option`, `etf`, `index` or `crypto` (required) |
| `quantity` | signed: negative is short (required) |
| `cost_basis_total` | the signed cash flow of opening (Money conventions) |
| `cost_basis_per_unit` | per share, stock-like rows only |
| `expiration`, `option_right`, `strike` | an option given by its components instead of `occ_symbol` |
| `trade_id` | `AG-YYYYMMDD-NN` (or three digits): groups the legs of one structure |
| `opened` | the opening date, `YYYY-MM-DD` |
| `open_price_per_share` | the unsigned premium per share, option rows only |
| `status` | `open` or `closed` |
| `closed`, `closed_quantity` and `close_price_per_unit` | the closing date, the closed amount (unsigned) and the price per share (unsigned; 0 means expired worthless) |
| `underlying_stop` | the owner's own stop level for the underlying, read by the `USTOP` rule |
| `note` | free text; its `#tags` are read (Tags) |

Required in the header: `instrument_type`, `quantity`, and `symbol` or `occ_symbol`. Dates are `YYYY-MM-DD`. Lines that
start with `#` are comments.

## Money conventions

- `cost_basis_total` is the cash flow of opening: paid is positive, received is negative. Its sign must equal the sign
  of `quantity`: a long position was paid for, a short position was received.
- A stock-like row may give `cost_basis_per_unit` instead: the total is quantity x per-unit.
- An option row gives `cost_basis_total` or `open_price_per_share`. The total is quantity x 100 x premium, with the sign
  of the quantity. When both are given they must agree within 0.01 x |quantity| x 100.
- `cost_basis_per_unit` on an option row is an error: per unit is ambiguous for options.
- A partial close records `closed_quantity`, `closed` and `close_price_per_unit` while `status` stays `open`.
- A full close is `status` = `closed` with `closed` and `close_price_per_unit`; a blank `closed_quantity` then means
  the whole row.
- Plain decimals only: no exponent, no thousands separator, no currency sign.

## Trade ids and tags

Rows that share a `trade_id` are one group: one structure, marked and checked together. A row without one is its own
group, `ROW-<line number>`. The `opened` date matters: two open rows of one contract are matched first in, first out by
it, and it tells the rule state whether a threshold was already past when the desk first saw the position (the alert
then says "already past this threshold at the desk's first observation").

Tags in `note`:

- The tag `#assign-ok` means the owner accepts assignment. A rule is silenced by a tag only when its
  `suppress_if_tag` key names it; in the example rulebook `#assign-ok` silences `EXDIV`, `DELTA_WATCH`,
  `DELTA_SIGNAL` and `CAP`. The expiry protocol also turns a HIGH dividend tier into NOTED for it.
- The tag `#earnings-ok` silences `EARN2` in the example rulebook.
- The tag `#horizon:YYYY-MM` records the owner's intended holding horizon.
- The tag `#roll-of:AG-...` says this group replaced the named group.
- The tag `#from:AG-...` says this row came from the named group (an assignment or an exercise).

A `#roll-of:` or `#from:` tag naming no group in the file is the warning `ROLL_LINK`.

## Errors, quarantine and the plausibility check

A file error stops the whole file: a bad header, the encoding, more than 5,000 rows, a duplicate column, a near-miss
column name or a CSV syntax error. Nothing loads; the verdict is ERRORS. A synthetic fixture copy whose header says
`cost_basis_totl`:

```text
ERRORS: positions.csv cannot be used: row 1 did_you_mean: unknown column 'cost_basis_totl': did you mean 'cost_basis_total'?
```

A row error quarantines its whole group: that group is UNAVAILABLE and every other group still loads. Rows are
counted as an editor shows them, the header being row 1. Row errors from synthetic fixture copies:

```text
quarantined AG-20261022-02: row 6 cost_basis_total: cost basis sign must equal quantity sign: paid is positive, received is negative (quantity -2, cost_basis_total 610.00) (AG-20261022-02)
quarantined AG-20261028-03: row 8 cost_basis_per_unit: ambiguous for options: use cost_basis_total or open_price_per_share (AG-20261028-03)
quarantined AG-20261028-03: row 8 note: starts with '=', a formula-injection prefix: rejected (AG-20261028-03)
```

A cell that starts with `=`, `+` or `@` is refused, and so is a leading `-` in a text column: those are spreadsheet
formula prefixes.

The plausibility check compares each open leg's entry price with its current mid. An entry outside the band from
mid / 20 to mid x 20 is an error, usually a per-contract total typed where a per-share price belongs. A synthetic copy
whose call shows a total of -61000.00 for two contracts:

```text
quarantined AG-20261022-02: row 6 cost_basis_total: unit mismatch? entry 305.00 vs mark 3.20 (AG-20261022-02)
```

## How rows become structures

The open legs of a group decide its structure: `long_stock`, `short_stock`, `long_call`, `long_put`, `short_call`,
`cash_secured_put`, `covered_call`, `protective_put`, `collar`, `vertical_credit_call`, `vertical_credit_put`,
`vertical_debit_call`, `vertical_debit_put`, or `custom`.

- A covered call is long shares and one short call leg, with shares of at least 100 per call. Shares beyond that are
  the warning `uncovered shares`; fewer shares than the calls need make the group `custom`.
- A cash-secured put carries the warning `cash reserve not verified`: the file holds no cash balance.
- A collar is long shares, one long put leg and one short call leg.
- A vertical is two option legs of one right and expiry, equal size, opposite signs.
- Every row of a group must share the account and the underlying, or the group is quarantined.
- A group whose legs are all closed keeps its structure for realized P/L.

The synthetic fixture book, `tests/fixtures/desk/positions.csv`, shows three groups and one closed one:

```text
instrument_type,symbol,occ_symbol,quantity,expiration,option_right,strike,account,trade_id,opened,cost_basis_total,cost_basis_per_unit,open_price_per_share,status,closed,closed_quantity,close_price_per_unit,underlying_stop,note
equity,WMT,,200,,,,Brokerage (margin),AG-20261020-01,2026-10-20,,98.50,,open,,,,92.00,core holding #horizon:2027-03
option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,open,,,,,covered call #assign-ok
equity,NVDA,,200,,,,Brokerage (margin),AG-20261022-02,2026-10-22,28240.00,,,open,,,,,
option,NVDA,,-2,2026-11-20,CALL,150,Brokerage (margin),AG-20261022-02,2026-10-22,-610.00,,,open,,,,,covered call from component columns #earnings-ok
etf,SPY,,100,,,,Roth IRA,AG-20261028-03,2026-10-28,63240.00,,,open,,,,,
option,SPY,SPY261218P00620000,1,,,,Roth IRA,AG-20261028-03,2026-10-28,1130.00,,11.30,open,,,,,protective put
option,SPY,SPY261016P00600000,-1,,,,Roth IRA,AG-20261001-04,2026-10-01,-480.00,,,closed,2026-10-14,1,0.55,,closed early
```

`positions_check.py` on it (synthetic, regular set, 2026-11-03 19:50 UTC) prints first:

```text
CLEAN: 7 rows, 4 groups (3 open, 1 closed), 0 quarantined; plausibility passed for 6 open legs
```

## Recipes: what the owner writes for each event

Position Control turns what the owner reports into exact lines in the file's own column order, and the owner applies
them. The amounts always come from the owner's brokerage record, never from a quote.

**A new position.** One row per leg, all under one new `trade_id`, with `opened`, `status` `open`, and the opening
cash flow as `cost_basis_total` (or the premium per share as `open_price_per_share` on an option row).

**A full close.** On each closed row: `status` `closed`, the `closed` date and the unsigned
per-share `close_price_per_unit`. Nothing else on the row changes; the opening cells stay as they were.

**A partial close.** On the row: `closed_quantity` (unsigned), `closed` and `close_price_per_unit`; `status` stays
`open`. A row holds only one close price, so `csv_recon.py` reports a row it cannot represent as
`EDIT_UNREPRESENTABLE` and proposes no edit for it.

**A roll of a single option or a vertical.** The old rows are recorded as closed (the status, the closing date and the price per share). The new
legs are new rows under a new `trade_id` whose `note` carries `#roll-of:<old trade_id>`.

**A roll of the call of a covered call or a collar.** The old call row is recorded as closed, and the new call row is
added under the SAME `trade_id`, so the shares stay with exactly one open short call. The desk keeps rule state per
group and rule, so the group's rule state carries over to the new call. An illustrative example, not tool output: the
fixture's November call recorded as closed and a December call added (the new row's amounts are made up for the
example):

```text
option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,closed,2026-11-03,2,0.65,,covered call #assign-ok
option,WMT,WMT261218C00110000,-2,,,,Brokerage (margin),AG-20261020-01,2026-11-03,-500.00,,2.50,open,,,,,covered call: the December call that replaced the November call
```

On a fixture copy, `positions_check.py` groups the three WMT rows as one covered call.

**Expiry, assignment and exercise.** These are never typed from memory. `csv_recon.py --diff` proposes them after the
expiry (`aegis-expiry-and-assignment-protocol`), with one convention: the option row closes at 0, so its premium stays
in the option's P/L, and the stock is booked at the strike.

- A short put assigned or a long call exercised adds a long stock row of 100 shares per contract at the strike, with
  the same `trade_id`, `opened` on the settlement day and the note `#from:<trade_id> assigned` (or `exercised`).
- A short call assigned or a long put exercised delivers 100 shares per contract from the group's stock rows, first in
  first out, at `close_price_per_unit` equal to the strike. Shares the group does not hold become an added short stock
  row at the strike, marked "confirm with your broker".

The proposed block from a synthetic Saturday run after the 2026-11-20 expiry (`csv_recon.py --diff`, post_expiry set,
2026-11-21 14:31 UTC):

```text
proposed edits (3; not applied: positions.csv is yours to edit):
# row 4 (AG-20261020-01) EXPIRED_OTM: status=closed, closed=2026-11-20, closed_quantity=2, close_price_per_unit=0
option,WMT,WMT261120C00105000,-2,,,,Brokerage (margin),AG-20261020-01,2026-10-20,-684.00,,3.42,closed,2026-11-20,2,0,,covered call #assign-ok
# row 6 (AG-20261022-02) PROBABLE_ASSIGNED: status=closed, closed=2026-11-20, closed_quantity=2, close_price_per_unit=0
option,NVDA,,-2,2026-11-20,CALL,150,Brokerage (margin),AG-20261022-02,2026-10-22,-610.00,,,closed,2026-11-20,2,0,,covered call from component columns #earnings-ok
# row 5 (AG-20261022-02) PROBABLE_ASSIGNED: status=closed, closed=2026-11-20, closed_quantity=200, close_price_per_unit=150.00
equity,NVDA,,200,,,,Brokerage (margin),AG-20261022-02,2026-10-22,28240.00,,,closed,2026-11-20,200,150.00,,
```

The outcomes are inferred from the official close. The owner checks them against the broker before applying any line.

## After the owner applies the lines

1. Position Control runs `python3 scripts/positions_check.py --desk-root /workspace/aegis-desk` and reports the first
   line: CLEAN, or the errors to fix.
2. The next book check posts CONFIG CHANGED first, naming `positions.csv`, until the owner acknowledges. The Desk Lead
   runs `python3 scripts/ack.py config --owner-tapped --desk-root /workspace/aegis-desk` only after the owner taps the
   Ask-first card.
3. After `ack.py acted`, the group's rows are expected to change. If they are unchanged a trading day later,
   `csv_recon.py` warns `ACTED_UNCHANGED`: the owner has not yet recorded what was done.

Position Control's diagnostics (`positions_check.py`, `csv_recon.py --diff`) are quoted only when the owner asks,
unchanged, in a fenced block headed by the exact command, and the proposed lines appear only in Position Control's own
chat.
