"""``positions.csv``: the hand-edited book of record, parsed and validated (standard library only).

The file is Aegis's CSV import schema (``services/api/src/aegis_api/integrations/csv_import/parser.py``
and ``normalizer.py``, FairGradient) with the desk's extension columns. The Aegis semantics are ported
exactly where the two agree (comment and blank lines, the formula-injection guard, the case-insensitive
header, the per-unit stock basis, OCC synthesis from component columns, adjusted roots flagged and
not rejected, the 5,000-row cap, the length caps); the desk is stricter wherever money could be
misread, and every difference is a named error with a message that says what to write instead.

Columns
-------
Core (Aegis, byte-compatible): ``account``, ``symbol``, ``occ_symbol``, ``instrument_type``
(``equity|option|etf|index|crypto``), ``quantity`` (signed; negative is short), ``cost_basis_total``,
``cost_basis_per_unit``, ``expiration``, ``option_right``, ``strike``. Extensions: ``trade_id``
(``AG-YYYYMMDD-NN``, groups the legs of one structure), ``opened``, ``open_price_per_share``,
``status`` (``open|closed``), ``closed``, ``closed_quantity``, ``close_price_per_unit``,
``underlying_stop``, ``note`` (free text whose ``#tags`` are extracted). Column names are lower-cased
and stripped. Unknown columns are ignored, EXCEPT a name within Damerau-Levenshtein distance 2 of a
known column, which is the file error ``did_you_mean`` (a typo never silently drops money).

Money conventions (plan section 8)
----------------------------------
* ``cost_basis_total`` is the signed cash flow of opening: paid is positive, received is negative,
  and its sign must equal the sign of ``quantity`` (a long position paid, a short position received).
* Stock-like rows (equity, ETF, index, crypto; multiplier 1) may give ``cost_basis_per_unit`` instead:
  ``total = quantity x per_unit`` (the Aegis semantics). When both are present the total wins and a
  row warning notes a disagreement.
* Option rows (multiplier 100) give ``cost_basis_total`` or ``open_price_per_share`` (the unsigned
  premium per share): ``total = quantity x 100 x premium``, which carries the sign of ``quantity``.
  Both present must agree within ``0.01 x |quantity| x 100``. ``cost_basis_per_unit`` on an option row
  is an error (ambiguous), and so is ``open_price_per_share`` on a stock row.
* ``closed_quantity`` (unsigned) with ``closed`` (date) and ``close_price_per_unit`` (unsigned per
  share; 0 = expired worthless) record a partial close while ``status`` stays ``open``; ``status =
  closed`` needs ``closed`` and ``close_price_per_unit`` and takes ``closed_quantity = |quantity|``
  when it is blank. The closed part of the basis is pro rata, rounded to the cent (or to the written
  precision of the total when finer) so that closed plus remaining equals the total exactly.
* Every money value is a :class:`decimal.Decimal` built from the cell text; nothing here is ever a
  binary float. Plain decimal spellings only: no exponent, NaN or Infinity.

Errors and quarantine
---------------------
A row error (:class:`RowError`) quarantines the row's whole trade group (every row with that
``trade_id``), recorded in ``PositionsFile.quarantined``; rows of other groups still load. A file
error (fields ``header``, ``encoding``, ``row_limit``, ``duplicate_column``, ``did_you_mean``, ``csv``)
makes ``rows`` empty. A header-only file (no data rows) is an empty book, not an error. Row numbers
are physical 1-based line numbers of the file, the number an editor shows (Aegis counts kept lines
instead); a row with a blank ``trade_id`` forms its own group ``ROW-<row_number>``.

Dates are ISO ``YYYY-MM-DD``. ``today`` (keyword, default the current UTC date) bounds option
expirations to ``[2000-01-01, today + 5 years]``; expired legs still parse (the reconciliation
needs them). ``load_positions`` raises ``OSError`` when the file cannot be read; the tools map that.
"""

from __future__ import annotations

import csv
import datetime
import hashlib
import re
import unicodedata
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Dict, FrozenSet, Iterable, List, Optional, Sequence, Tuple

from aegis_desk import occ, state, units
from aegis_desk.occ import OccParseError, OccSymbol

INSTRUMENT_EQUITY: str = "equity"
INSTRUMENT_OPTION: str = "option"
INSTRUMENT_ETF: str = "etf"
INSTRUMENT_INDEX: str = "index"
INSTRUMENT_CRYPTO: str = "crypto"
INSTRUMENT_TYPES: Tuple[str, ...] = (INSTRUMENT_EQUITY, INSTRUMENT_OPTION, INSTRUMENT_ETF, INSTRUMENT_INDEX,
                                     INSTRUMENT_CRYPTO)
"""Accepted ``instrument_type`` values (Aegis ``ALLOWED_INSTRUMENT_TYPES``), lower case."""

STOCK_LIKE_TYPES: Tuple[str, ...] = (INSTRUMENT_EQUITY, INSTRUMENT_ETF, INSTRUMENT_INDEX, INSTRUMENT_CRYPTO)
"""Instrument types with multiplier 1."""

STATUS_OPEN: str = "open"
STATUS_CLOSED: str = "closed"
STATUSES: Tuple[str, str] = (STATUS_OPEN, STATUS_CLOSED)

SIDE_LONG: str = "long"
SIDE_SHORT: str = "short"

CORE_COLUMNS: Tuple[str, ...] = ("account", "symbol", "occ_symbol", "instrument_type", "quantity", "cost_basis_total",
                                 "cost_basis_per_unit", "expiration", "option_right", "strike")
"""The Aegis import columns."""

EXTENSION_COLUMNS: Tuple[str, ...] = ("trade_id", "opened", "open_price_per_share", "status", "closed",
                                      "closed_quantity", "close_price_per_unit", "underlying_stop", "note")
"""The desk's extension columns (plan section 8)."""

KNOWN_COLUMNS: Tuple[str, ...] = CORE_COLUMNS + EXTENSION_COLUMNS
REQUIRED_COLUMNS: Tuple[str, ...] = ("instrument_type", "quantity")
"""Required in the header besides ``symbol`` or ``occ_symbol``."""

MINUS_GUARDED_COLUMNS: Tuple[str, ...] = ("account", "symbol", "expiration", "option_right", "trade_id", "note")
"""Text columns where a leading ``-`` is a formula-injection prefix too (the numeric columns carry signs)."""

FORMULA_PREFIXES: Tuple[str, ...] = ("=", "+", "@")

MAX_ROWS: int = 5000
"""Data rows accepted per file (Aegis ``MAX_ROWS``); the 5,001st is the file error ``row_limit``."""

MAX_ACCOUNT_LEN: int = 200
MAX_SYMBOL_LEN: int = 32
MAX_OCC_SYMBOL_LEN: int = 64
DID_YOU_MEAN_DISTANCE: int = 2
OPTION_MULTIPLIER: int = units.CONTRACT_MULTIPLIER
STOCK_MULTIPLIER: int = 1
EXPIRATION_FLOOR: datetime.date = datetime.date(2000, 1, 1)
EXPIRATION_HORIZON_YEARS: int = 5
MAX_STRIKE: Decimal = Decimal(100000)
"""Strikes are below this (the eight-digit OSI field holds at most 99999.999)."""

INDEX_ROOTS: Tuple[str, ...] = ("SPX", "SPXW", "NDX", "RUT", "VIX", "DJX", "XSP")
"""OCC roots (and stock-like symbols) the sandbox cannot quote: index underlyings."""

TRADE_ID_RE = re.compile(r"^AG-\d{8}-\d{2,3}$")
DECIMAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)$")
ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
HORIZON_RE = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")
TAG_RE = re.compile(r"#(\S+)")
TAG_TRAILING_PUNCTUATION: str = ".,;:)!?"

TAG_ASSIGN_OK: str = "assign-ok"
TAG_EARNINGS_OK: str = "earnings-ok"
TAG_HORIZON: str = "horizon"
TAG_ROLL_OF: str = "roll-of"
TAG_FROM: str = "from"
KNOWN_TAGS: Tuple[str, ...] = (TAG_ASSIGN_OK, TAG_EARNINGS_OK, TAG_HORIZON, TAG_ROLL_OF, TAG_FROM)
"""``#assign-ok``, ``#earnings-ok``, ``#horizon:YYYY-MM``, ``#roll-of:AG-...``, ``#from:AG-...``."""

WARNING_ADJUSTED: str = "adjusted contract: deliverable may differ from 100 shares"
WARNING_OPENED_BLANK: str = ("opened blank: FIFO matching and first-observation suppression need the opened date")
MESSAGE_SIGN: str = "cost basis sign must equal quantity sign: paid is positive, received is negative"
MESSAGE_PER_UNIT_OPTION: str = "ambiguous for options: use cost_basis_total or open_price_per_share"
MESSAGE_ROOT_MISMATCH: str = "symbol/occ root mismatch"
MESSAGE_PREMIUM_EXCEEDS_STRIKE: str = "premium exceeds strike"

FILE_ERROR_FIELDS: Tuple[str, ...] = ("header", "encoding", "row_limit", "duplicate_column", "did_you_mean", "csv")
"""``RowError.field`` values of file-level errors."""

TEXT_PATH: str = "<text>"
_FOUR_DP = Decimal("0.0001")
_CENT = Decimal("0.01")
_ZERO = Decimal(0)
_ONE = Decimal(1)
_SHARES_PER_CONTRACT = Decimal(OPTION_MULTIPLIER)


# --------------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RowError:
    """One validation failure: the physical line, the column (or a file-error field), the message
    and the ``trade_id`` text of the row when it had one (``None`` for file errors and blank ids)."""

    row_number: int
    field: str
    message: str
    trade_id: Optional[str] = None

    def __str__(self) -> str:
        text = "row %d %s: %s" % (self.row_number, self.field, self.message)
        if self.trade_id:
            text += " (%s)" % self.trade_id
        return text


@dataclass(frozen=True)
class PositionRow:
    """One validated data row (module notes for every convention). Frozen; built by the parser."""

    row_number: int
    account: str
    trade_id: Optional[str]
    group_id: str
    instrument_type: str
    symbol: str
    occ: Optional[OccSymbol]
    quantity: Decimal
    multiplier: int
    cost_basis_total: Decimal
    cost_basis_per_unit: Optional[Decimal]
    open_price_per_share: Optional[Decimal]
    opened: Optional[datetime.date]
    status: str
    closed: Optional[datetime.date]
    closed_quantity: Optional[Decimal]
    close_price_per_unit: Optional[Decimal]
    underlying_stop: Optional[Decimal]
    note: str
    tags: FrozenSet[str]
    warnings: Tuple[str, ...]

    # -- instrument -----------------------------------------------------------------------
    @property
    def is_option(self) -> bool:
        return self.instrument_type == INSTRUMENT_OPTION

    @property
    def is_stock_like(self) -> bool:
        return not self.is_option

    @property
    def compact_symbol(self) -> str:
        """The Tradier quote symbol: ``occ.compact`` for options, the ticker otherwise."""
        return self.occ.compact if self.occ is not None else self.symbol

    @property
    def underlying(self) -> str:
        """The ticker for stock-like rows, the OCC base root (digit suffix removed) for options."""
        return self.occ.base_root if self.occ is not None else self.symbol

    @property
    def expiration(self) -> Optional[datetime.date]:
        return self.occ.expiration if self.occ is not None else None

    @property
    def right(self) -> Optional[str]:
        return self.occ.right if self.occ is not None else None

    @property
    def strike(self) -> Optional[Decimal]:
        return self.occ.strike if self.occ is not None else None

    @property
    def is_call(self) -> bool:
        return self.occ is not None and self.occ.is_call

    @property
    def is_put(self) -> bool:
        return self.occ is not None and self.occ.is_put

    @property
    def is_index_underlying(self) -> bool:
        """An ``index`` row, an option on an :data:`INDEX_ROOTS` root, or a stock-like row whose
        symbol is such a root: the sandbox cannot quote it (the "unquotable" warning)."""
        if self.instrument_type == INSTRUMENT_INDEX:
            return True
        if self.occ is not None:
            return self.occ.base_root in INDEX_ROOTS
        return self.symbol in INDEX_ROOTS

    # -- quantities and money --------------------------------------------------------------
    @property
    def sign(self) -> Decimal:
        return _ONE if self.quantity > 0 else -_ONE

    @property
    def side(self) -> str:
        return SIDE_LONG if self.quantity > 0 else SIDE_SHORT

    @property
    def is_long(self) -> bool:
        return self.quantity > 0

    @property
    def is_short(self) -> bool:
        return self.quantity < 0

    @property
    def is_open(self) -> bool:
        return self.status == STATUS_OPEN

    @property
    def is_closed(self) -> bool:
        return self.status == STATUS_CLOSED

    @property
    def is_partially_closed(self) -> bool:
        return self.is_open and self.closed_quantity is not None

    @property
    def open_quantity(self) -> Decimal:
        """The signed quantity still open: 0 for a closed row, ``quantity`` less the closed part."""
        if self.is_closed:
            return _ZERO
        if self.closed_quantity is None:
            return self.quantity
        return self.quantity - self.sign * self.closed_quantity

    @property
    def closed_cost_basis(self) -> Decimal:
        """The signed part of ``cost_basis_total`` that belongs to the closed quantity (pro rata, see
        the module notes); the whole total for a closed row, 0 when nothing is closed."""
        if self.closed_quantity is None:
            return self.cost_basis_total if self.is_closed else _ZERO
        return pro_rata(self.cost_basis_total, self.closed_quantity, abs(self.quantity))

    @property
    def remaining_cost_basis(self) -> Decimal:
        """``cost_basis_total`` less :attr:`closed_cost_basis`: the basis of the open quantity."""
        return self.cost_basis_total - self.closed_cost_basis

    @property
    def entry_price_per_share(self) -> Decimal:
        """Unsigned ``|cost_basis_total| / (|quantity| x multiplier)``, exact, or rounded to four
        decimals when the division does not terminate."""
        return per_share(abs(self.cost_basis_total), abs(self.quantity) * self.multiplier)

    # -- tags -------------------------------------------------------------------------------
    @property
    def assign_ok(self) -> bool:
        return TAG_ASSIGN_OK in self.tags

    @property
    def earnings_ok(self) -> bool:
        return TAG_EARNINGS_OK in self.tags

    def _tag_value(self, key: str) -> Optional[str]:
        prefix = key + ":"
        for tag in self.tags:
            if tag.startswith(prefix):
                return tag[len(prefix):]
        return None

    @property
    def horizon(self) -> Optional[str]:
        """``YYYY-MM`` from ``#horizon:``, or ``None``."""
        return self._tag_value(TAG_HORIZON)

    @property
    def roll_of(self) -> Optional[str]:
        """The trade id named by ``#roll-of:``, or ``None``."""
        return self._tag_value(TAG_ROLL_OF)

    @property
    def from_trade(self) -> Optional[str]:
        """The trade id named by ``#from:``, or ``None``."""
        return self._tag_value(TAG_FROM)

    def as_dict(self) -> Dict[str, object]:
        """JSON-ready view: Decimals stay Decimals (``state.json_dumps`` renders them as strings),
        dates become ISO strings, the OCC symbol its compact form."""
        return {
            "row_number": self.row_number,
            "account": self.account,
            "trade_id": self.trade_id,
            "group_id": self.group_id,
            "instrument_type": self.instrument_type,
            "symbol": self.symbol,
            "occ": self.occ.compact if self.occ is not None else None,
            "underlying": self.underlying,
            "right": self.right,
            "strike": self.strike,
            "expiration": self.expiration.isoformat() if self.expiration is not None else None,
            "quantity": self.quantity,
            "multiplier": self.multiplier,
            "cost_basis_total": self.cost_basis_total,
            "cost_basis_per_unit": self.cost_basis_per_unit,
            "open_price_per_share": self.open_price_per_share,
            "entry_price_per_share": self.entry_price_per_share,
            "opened": self.opened.isoformat() if self.opened is not None else None,
            "status": self.status,
            "closed": self.closed.isoformat() if self.closed is not None else None,
            "closed_quantity": self.closed_quantity,
            "close_price_per_unit": self.close_price_per_unit,
            "open_quantity": self.open_quantity,
            "remaining_cost_basis": self.remaining_cost_basis,
            "underlying_stop": self.underlying_stop,
            "note": self.note,
            "tags": sorted(self.tags),
            "warnings": list(self.warnings),
        }


@dataclass
class PositionsFile:
    """The parsed file: the rows that loaded, the file errors (``rows`` is empty when any exist), the
    quarantined groups (``group id -> row errors``), the SHA-256 of the bytes, the number of data rows
    seen (quarantined ones included), the normalised header and the path."""

    rows: List[PositionRow]
    file_errors: List[RowError]
    quarantined: Dict[str, List[RowError]]
    sha256: str
    row_count: int
    header: List[str]
    path: str = TEXT_PATH

    @property
    def row_errors(self) -> List[RowError]:
        """Every row error, in row order."""
        return sorted((error for errors in self.quarantined.values() for error in errors),
                      key=lambda error: (error.row_number, error.field))

    @property
    def is_clean(self) -> bool:
        return not self.file_errors and not self.quarantined

    @property
    def open_rows(self) -> List[PositionRow]:
        return [row for row in self.rows if row.is_open]

    @property
    def closed_rows(self) -> List[PositionRow]:
        return [row for row in self.rows if row.is_closed]

    @property
    def group_ids(self) -> List[str]:
        """Group ids of the loaded rows in order of first appearance."""
        seen: List[str] = []
        for row in self.rows:
            if row.group_id not in seen:
                seen.append(row.group_id)
        return seen


# --------------------------------------------------------------------------------------------
# Arithmetic helpers (Decimal only)
# --------------------------------------------------------------------------------------------


def pro_rata(total: Decimal, part: Decimal, whole: Decimal) -> Decimal:
    """``total x part / whole`` rounded (half-even) to the cent, or to the written precision of
    ``total`` when that is finer, so the two parts of a split add up to ``total`` exactly."""
    exponent = total.as_tuple().exponent
    quantum = _ONE.scaleb(min(exponent if isinstance(exponent, int) else -2, -2))
    return (total * part / whole).quantize(quantum, rounding=ROUND_HALF_EVEN)


def per_share(amount: Decimal, shares: Decimal) -> Decimal:
    """``amount / shares`` exactly when the division terminates within four decimals, else rounded
    half-even to four decimals."""
    exact = amount / shares
    rounded = exact.quantize(_FOUR_DP, rounding=ROUND_HALF_EVEN)
    return exact if exact == rounded else rounded


def damerau_levenshtein(a: str, b: str) -> int:
    """Edit distance with insertions, deletions, substitutions and adjacent transpositions (the
    optimal string alignment form of Damerau-Levenshtein); used for the did-you-mean header check."""
    rows = len(a) + 1
    cols = len(b) + 1
    table = [[0] * cols for _ in range(rows)]
    for i in range(rows):
        table[i][0] = i
    for j in range(cols):
        table[0][j] = j
    for i in range(1, rows):
        for j in range(1, cols):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            best = min(table[i - 1][j] + 1, table[i][j - 1] + 1, table[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                best = min(best, table[i - 2][j - 2] + 1)
            table[i][j] = best
    return table[rows - 1][cols - 1]


def synthesize_occ_symbol(symbol: str, expiration: datetime.date, right: str, strike: Decimal) -> str:
    """The canonical 21-character OCC symbol from component columns, exactly as Aegis builds it:
    the root is the upper-cased symbol cut to 6 characters and space-padded, the date ``YYMMDD``,
    the right ``C``/``P``, the strike ``x 1000`` rounded half-even to an integer in 8 digits.
    Raises ``ValueError`` when the strike is not positive or does not fit the field."""
    root = symbol.upper().strip()[:occ.ROOT_WIDTH].ljust(occ.ROOT_WIDTH)
    right_char = "C" if right.upper() == occ.CALL else "P"
    thousandths = int((strike * occ.STRIKE_SCALE).quantize(_ONE, rounding=ROUND_HALF_EVEN))
    if thousandths <= 0:
        raise ValueError("strike %s must be positive" % strike)
    if thousandths > 99999999:
        raise ValueError("strike %s exceeds the OCC maximum 99999.999 (the eight-digit strike field would overflow)"
                         % strike)
    return "%s%s%s%08d" % (root, expiration.strftime("%y%m%d"), right_char, thousandths)


def extract_tags(note: str) -> Tuple[FrozenSet[str], List[str]]:
    """``(tags, errors)`` from a note: the known tags (module notes) without their ``#``, lower-cased
    keys, values as written; a malformed known tag is an error; unknown tags stay text."""
    tags: List[str] = []
    errors: List[str] = []
    for match in TAG_RE.finditer(note):
        token = match.group(1).rstrip(TAG_TRAILING_PUNCTUATION)
        lowered = token.lower()
        if lowered in (TAG_ASSIGN_OK, TAG_EARNINGS_OK):
            tags.append(lowered)
            continue
        key, separator, value = token.partition(":")
        key = key.lower()
        if key == TAG_HORIZON and separator:
            if HORIZON_RE.match(value):
                tags.append("%s:%s" % (TAG_HORIZON, value))
            else:
                errors.append("tag #horizon: expects YYYY-MM, got %r" % value)
        elif key in (TAG_ROLL_OF, TAG_FROM) and separator:
            if TRADE_ID_RE.match(value):
                tags.append("%s:%s" % (key, value))
            else:
                errors.append("tag #%s: expects a trade id AG-YYYYMMDD-NN, got %r" % (key, value))
    return frozenset(tags), errors


def _has_invisible(value: str) -> bool:
    """Null bytes and Unicode Cc/Cf characters: invisible, and able to hide a formula prefix."""
    return any(unicodedata.category(char) in ("Cc", "Cf") for char in value)


def _years_ahead(day: datetime.date, years: int) -> datetime.date:
    try:
        return day.replace(year=day.year + years)
    except ValueError:  # 29 February
        return day.replace(year=day.year + years, day=28)


def _plain(value: Decimal) -> str:
    return str(int(value)) if value == value.to_integral_value() else str(value)


# --------------------------------------------------------------------------------------------
# One record
# --------------------------------------------------------------------------------------------


class _Record:
    """Validation of one data row: every check appends a :class:`RowError`; dependent checks run
    only when their inputs parsed."""

    def __init__(self, cells: Dict[str, str], row_number: int, today: datetime.date) -> None:
        self.cells = cells
        self.row_number = row_number
        self.today = today
        self.errors: List[RowError] = []
        self.warnings: List[str] = []
        trade_text = cells.get("trade_id", "").strip()
        self.trade_text: Optional[str] = trade_text or None
        self.group_key: str = trade_text or ("ROW-%d" % row_number)

    def fail(self, field_name: str, message: str) -> None:
        self.errors.append(RowError(self.row_number, field_name, message, self.trade_text))

    def present(self, column: str) -> bool:
        return bool(self.cells.get(column, "").strip())

    def text(self, column: str) -> Optional[str]:
        """The stripped cell after the formula-injection guard: ``""`` when blank, ``None`` when the
        guard failed (an error is recorded)."""
        raw = self.cells.get(column, "")
        if _has_invisible(raw):
            self.fail(column, "contains invisible control/format characters (null bytes, Unicode Cc/Cf): rejected, "
                              "they can hide a formula-injection prefix")
            return None
        value = raw.strip()
        if not value:
            return ""
        first = value[0]
        if first in FORMULA_PREFIXES or (first == "-" and column in MINUS_GUARDED_COLUMNS):
            self.fail(column, "starts with %r, a formula-injection prefix: rejected" % first)
            return None
        return value

    def decimal(self, column: str) -> Optional[Decimal]:
        """A finite plain decimal, ``None`` when blank or invalid (an error is recorded)."""
        raw = self.cells.get(column, "")
        if _has_invisible(raw):
            self.fail(column, "contains invisible control/format characters (null bytes, Unicode Cc/Cf): rejected")
            return None
        value = raw.strip()
        if not value:
            return None
        if not DECIMAL_RE.match(value):
            self.fail(column, "%r is not a plain decimal number (digits, an optional sign and point; no exponent, "
                              "NaN or Infinity)" % value)
            return None
        return Decimal(value)

    def date(self, column: str) -> Optional[datetime.date]:
        value = self.text(column)
        if not value:
            return None
        if not ISO_DATE_RE.match(value):
            self.fail(column, "%r is not an ISO date (YYYY-MM-DD)" % value)
            return None
        try:
            return datetime.date.fromisoformat(value)
        except ValueError as exc:
            self.fail(column, "%r is not a valid date: %s" % (value, exc))
            return None

    # -- the row --------------------------------------------------------------------------
    def parse(self) -> Optional[PositionRow]:
        trade_id = self._trade_id()
        account = self._account()
        symbol = self._symbol()
        occ_text = self.text("occ_symbol")
        instrument_type = self._instrument_type()
        quantity = self._quantity(instrument_type)
        expiration = self.date("expiration")
        right = self._right()
        strike = self._strike()

        parsed_occ: Optional[OccSymbol] = None
        if instrument_type == INSTRUMENT_OPTION:
            parsed_occ, symbol = self._option_instrument(symbol, occ_text, expiration, right, strike)
        elif instrument_type is not None:
            self._stock_instrument(instrument_type, symbol, occ_text, expiration, right, strike)

        multiplier = OPTION_MULTIPLIER if instrument_type == INSTRUMENT_OPTION else STOCK_MULTIPLIER
        total, per_unit, premium = self._money(instrument_type, quantity, multiplier, parsed_occ)

        opened = self.date("opened")
        if opened is None and not self.errors_on("opened"):
            self.warnings.append(WARNING_OPENED_BLANK)
        status, closed, closed_quantity, close_price = self._close_fields(quantity, multiplier, opened)
        underlying_stop = self.decimal("underlying_stop")
        if underlying_stop is not None and underlying_stop <= 0:
            self.fail("underlying_stop", "underlying_stop must be positive, got %s" % underlying_stop)
        note = self.text("note")
        tags: FrozenSet[str] = frozenset()
        if note:
            tags, tag_errors = extract_tags(note)
            for message in tag_errors:
                self.fail("note", message)

        if self.errors or instrument_type is None or quantity is None or total is None or symbol is None \
                or account is None or status is None:
            return None
        return PositionRow(
            row_number=self.row_number,
            account=account,
            trade_id=trade_id,
            group_id=trade_id or self.group_key,
            instrument_type=instrument_type,
            symbol=symbol,
            occ=parsed_occ,
            quantity=quantity,
            multiplier=multiplier,
            cost_basis_total=total,
            cost_basis_per_unit=per_unit,
            open_price_per_share=premium,
            opened=opened,
            status=status,
            closed=closed,
            closed_quantity=closed_quantity,
            close_price_per_unit=close_price,
            underlying_stop=underlying_stop,
            note=note or "",
            tags=tags,
            warnings=tuple(self.warnings),
        )

    def errors_on(self, column: str) -> bool:
        return any(error.field == column for error in self.errors)

    # -- identity -------------------------------------------------------------------------
    def _trade_id(self) -> Optional[str]:
        value = self.text("trade_id")
        if not value:
            return None
        if not TRADE_ID_RE.match(value):
            self.fail("trade_id", "%r does not match AG-YYYYMMDD-NN (eight digits, then two or three digits)" % value)
            return None
        return value

    def _account(self) -> Optional[str]:
        value = self.text("account")
        if value is None:
            return None
        if not value:
            return "default"
        if len(value) > MAX_ACCOUNT_LEN:
            self.fail("account", "account is %d characters (limit %d)" % (len(value), MAX_ACCOUNT_LEN))
            return None
        return value

    def _symbol(self) -> Optional[str]:
        value = self.text("symbol")
        if value is None:
            return None
        if len(value) > MAX_SYMBOL_LEN:
            self.fail("symbol", "symbol is %d characters (limit %d)" % (len(value), MAX_SYMBOL_LEN))
            return None
        return value.upper()

    def _instrument_type(self) -> Optional[str]:
        value = self.text("instrument_type")
        if value is None:
            return None
        if not value:
            self.fail("instrument_type", "instrument_type is required (%s)" % "|".join(INSTRUMENT_TYPES))
            return None
        lowered = value.lower()
        if lowered not in INSTRUMENT_TYPES:
            self.fail("instrument_type", "instrument_type %r is not one of %s" % (value, ", ".join(INSTRUMENT_TYPES)))
            return None
        return lowered

    def _quantity(self, instrument_type: Optional[str]) -> Optional[Decimal]:
        if not self.present("quantity"):
            self.fail("quantity", "quantity is required (signed; negative is short)")
            return None
        value = self.decimal("quantity")
        if value is None:
            return None
        if value == 0:
            self.fail("quantity", "quantity must be non-zero: a zero-quantity position is meaningless")
            return None
        if instrument_type == INSTRUMENT_OPTION and value != value.to_integral_value():
            self.fail("quantity", "option quantity must be a whole number of contracts, got %s" % value)
            return None
        return value

    def _right(self) -> Optional[str]:
        value = self.text("option_right")
        if not value:
            return None
        upper = value.upper()
        if upper not in occ.RIGHTS:
            self.fail("option_right", "option_right %r must be CALL or PUT" % value)
            return None
        return upper

    def _strike(self) -> Optional[Decimal]:
        value = self.decimal("strike")
        if value is None:
            return None
        if value <= 0:
            self.fail("strike", "strike must be positive, got %s (a zero strike is economically invalid)" % value)
            return None
        if value >= MAX_STRIKE:
            self.fail("strike", "strike %s exceeds the OCC maximum 99999.999" % value)
            return None
        if value != value.quantize(occ.STRIKE_QUANTUM):
            self.fail("strike", "strike %s has more than three decimal places" % value)
            return None
        return value

    # -- instrument -----------------------------------------------------------------------
    def _option_instrument(self, symbol: Optional[str], occ_text: Optional[str], expiration: Optional[datetime.date],
                           right: Optional[str], strike: Optional[Decimal]) -> Tuple[Optional[OccSymbol], Optional[str]]:
        parsed: Optional[OccSymbol] = None
        if occ_text:
            if len(occ_text) > MAX_OCC_SYMBOL_LEN:
                self.fail("occ_symbol", "occ_symbol is %d characters (limit %d)" % (len(occ_text), MAX_OCC_SYMBOL_LEN))
                return None, symbol
            try:
                parsed = occ.parse(occ_text)
            except OccParseError as exc:
                self.fail("occ_symbol", "OCC symbol %r is malformed: %s" % (occ_text, exc))
                return None, symbol
            if expiration is not None and expiration != parsed.expiration:
                self.fail("expiration", "expiration %s disagrees with occ_symbol (%s)" % (expiration, parsed.expiration))
            if right is not None and right != parsed.right:
                self.fail("option_right", "option_right %s disagrees with occ_symbol (%s)" % (right, parsed.right))
            if strike is not None and strike != parsed.strike:
                self.fail("strike", "strike %s disagrees with occ_symbol (%s)" % (strike, parsed.strike))
        elif occ_text == "":
            if not symbol:
                if symbol == "":
                    self.fail("symbol", "both symbol and occ_symbol are blank: an option row needs occ_symbol, or "
                                        "symbol with expiration, option_right and strike")
                return None, symbol
            missing = [name for name, value in (("expiration", expiration), ("option_right", right),
                                                ("strike", strike)) if value is None and not self.errors_on(name)]
            if missing or any(self.errors_on(name) for name in ("expiration", "option_right", "strike")):
                if missing:
                    self.fail(missing[0], "option row without occ_symbol is missing %s (give occ_symbol or all of "
                                          "expiration, option_right, strike)" % ", ".join(missing))
                return None, symbol
            try:
                canonical = synthesize_occ_symbol(symbol, expiration, right, strike)  # type: ignore[arg-type]
                parsed = occ.parse(canonical)
            except ValueError as exc:  # OccParseError is a ValueError too
                self.fail("strike" if "strike" in str(exc) else "symbol",
                          "cannot build the OCC symbol from the component columns: %s" % exc)
                return None, symbol
        else:
            return None, symbol  # the occ_symbol cell failed the guard; its error is recorded

        if symbol:
            if symbol not in (parsed.root, parsed.base_root):
                self.fail("symbol", "%s: symbol %s is not the root of %s (%s)"
                          % (MESSAGE_ROOT_MISMATCH, symbol, parsed.compact, parsed.base_root))
                return parsed, None
        if parsed.is_adjusted:
            self.warnings.append(WARNING_ADJUSTED)
        horizon = _years_ahead(self.today, EXPIRATION_HORIZON_YEARS)
        if not EXPIRATION_FLOOR <= parsed.expiration <= horizon:
            self.fail("expiration", "expiration %s is outside %s..%s (today + %d years)"
                      % (parsed.expiration, EXPIRATION_FLOOR, horizon, EXPIRATION_HORIZON_YEARS))
        if parsed.strike >= MAX_STRIKE:
            self.fail("strike", "strike %s exceeds the OCC maximum" % parsed.strike)
        return parsed, parsed.base_root

    def _stock_instrument(self, instrument_type: str, symbol: Optional[str], occ_text: Optional[str],
                          expiration: Optional[datetime.date], right: Optional[str], strike: Optional[Decimal]) -> None:
        if occ_text:
            self.fail("occ_symbol", "occ_symbol on a%s %s row: an option row has instrument_type option"
                      % ("n" if instrument_type[0] in "aeiou" else "", instrument_type))
        for name, value in (("expiration", expiration), ("option_right", right), ("strike", strike)):
            if value is not None:
                self.fail(name, "%s applies to option rows only" % name)
        if symbol == "":
            self.fail("symbol", "symbol is required for %s rows" % instrument_type)

    # -- money ----------------------------------------------------------------------------
    def _money(self, instrument_type: Optional[str], quantity: Optional[Decimal], multiplier: int,
               parsed_occ: Optional[OccSymbol]) -> Tuple[Optional[Decimal], Optional[Decimal], Optional[Decimal]]:
        before = len(self.errors)
        total = self.decimal("cost_basis_total")
        per_unit = self.decimal("cost_basis_per_unit")
        premium = self.decimal("open_price_per_share")
        if instrument_type is None or quantity is None or len(self.errors) > before:
            return None, per_unit, premium
        if instrument_type == INSTRUMENT_OPTION:
            if per_unit is not None:
                self.fail("cost_basis_per_unit", MESSAGE_PER_UNIT_OPTION)
                return None, per_unit, premium
            if premium is not None and premium <= 0:
                self.fail("open_price_per_share", "premium must be positive (the unsigned premium per share), got %s"
                          % premium)
                return None, None, premium
            if total is None and premium is None:
                self.fail("cost_basis_total", "option row needs cost_basis_total or open_price_per_share")
                return None, None, None
            scale = quantity * _SHARES_PER_CONTRACT
            if total is None:
                total = scale * premium  # type: ignore[operator]
            elif premium is not None:
                expected = scale * premium
                tolerance = _CENT * abs(quantity) * _SHARES_PER_CONTRACT
                if abs(total - expected) > tolerance:
                    self.fail("open_price_per_share", "cost_basis_total %s disagrees with open_price_per_share %s "
                              "(quantity %s x %d x %s = %s)" % (total, premium, _plain(quantity), multiplier, premium,
                                                                expected))
                    return None, None, premium
            if not self._sign_ok(total, quantity):
                return None, None, premium
            entry = per_share(abs(total), abs(quantity) * _SHARES_PER_CONTRACT)
            if parsed_occ is not None and parsed_occ.is_put and entry > parsed_occ.strike:
                self.fail("cost_basis_total" if self.present("cost_basis_total") else "open_price_per_share",
                          "%s: %s per share against strike %s" % (MESSAGE_PREMIUM_EXCEEDS_STRIKE, entry,
                                                                   parsed_occ.strike))
                return None, None, premium
            return total, None, premium
        # stock-like
        if premium is not None:
            self.fail("open_price_per_share", "open_price_per_share applies to option rows; a %s row gives "
                      "cost_basis_total or cost_basis_per_unit" % instrument_type)
            return None, per_unit, premium
        if total is None and per_unit is None:
            self.fail("cost_basis_total", "%s row needs cost_basis_total or cost_basis_per_unit" % instrument_type)
            return None, None, None
        if total is None:
            total = quantity * per_unit  # type: ignore[operator]
        elif per_unit is not None and total != quantity * per_unit:
            self.warnings.append("cost_basis_total %s differs from quantity x cost_basis_per_unit = %s; total used"
                                 % (total, quantity * per_unit))
        if not self._sign_ok(total, quantity):
            return None, per_unit, None
        return total, per_unit, None

    def _sign_ok(self, total: Decimal, quantity: Decimal) -> bool:
        if total == 0 or (total > 0) != (quantity > 0):
            self.fail("cost_basis_total", "%s (quantity %s, cost_basis_total %s)" % (MESSAGE_SIGN, _plain(quantity),
                                                                                    total))
            return False
        return True

    # -- status and the close fields --------------------------------------------------------
    def _close_fields(self, quantity: Optional[Decimal], multiplier: int, opened: Optional[datetime.date]
                      ) -> Tuple[Optional[str], Optional[datetime.date], Optional[Decimal], Optional[Decimal]]:
        value = self.text("status")
        status: Optional[str]
        if value is None:
            status = None
        elif not value:
            status = STATUS_OPEN
        elif value.lower() in STATUSES:
            status = value.lower()
        else:
            self.fail("status", "status %r must be open or closed" % value)
            status = None
        closed = self.date("closed")
        closed_quantity = self.decimal("closed_quantity")
        close_price = self.decimal("close_price_per_unit")
        if close_price is not None and close_price < 0:
            self.fail("close_price_per_unit", "close_price_per_unit is unsigned per share (0 = expired worthless), got %s"
                      % close_price)
            close_price = None
        if closed_quantity is not None:
            if closed_quantity <= 0:
                self.fail("closed_quantity", "closed_quantity must be positive (unsigned), got %s" % closed_quantity)
                closed_quantity = None
            elif quantity is not None and closed_quantity > abs(quantity):
                self.fail("closed_quantity", "closed_quantity %s exceeds |quantity| %s" % (_plain(closed_quantity),
                                                                                           _plain(quantity)))
                closed_quantity = None
            elif multiplier == OPTION_MULTIPLIER and closed_quantity != closed_quantity.to_integral_value():
                self.fail("closed_quantity", "closed_quantity must be a whole number of contracts, got %s"
                          % closed_quantity)
                closed_quantity = None
        if closed is not None and opened is not None and closed < opened:
            self.fail("closed", "closed %s is before opened %s" % (closed, opened))
        if status is None or quantity is None:
            return status, closed, closed_quantity, close_price
        if status == STATUS_CLOSED:
            if closed is None and not self.errors_on("closed"):
                self.fail("closed", "status closed needs the closed date")
            if close_price is None and not self.errors_on("close_price_per_unit"):
                self.fail("close_price_per_unit", "status closed needs close_price_per_unit (0 = expired worthless)")
            if closed_quantity is None:
                if not self.errors_on("closed_quantity"):
                    closed_quantity = abs(quantity)
            elif closed_quantity != abs(quantity):
                self.fail("closed_quantity", "status closed but closed_quantity %s is below |quantity| %s: a partial "
                          "close keeps status open" % (_plain(closed_quantity), _plain(quantity)))
            return status, closed, closed_quantity, close_price
        # open
        if closed_quantity is not None:
            if closed_quantity == abs(quantity):
                self.fail("closed_quantity", "closed_quantity covers the whole position: set status = closed")
            if closed is None and close_price is None:
                self.fail("closed_quantity", "closed_quantity needs the closed date and close_price_per_unit")
            elif closed is None:
                if not self.errors_on("closed"):
                    self.fail("closed", "closed_quantity needs the closed date")
            elif close_price is None and not self.errors_on("close_price_per_unit"):
                self.fail("close_price_per_unit", "closed_quantity needs close_price_per_unit (0 = expired worthless)")
        else:
            if closed is not None:
                self.fail("closed", "closed date without closed_quantity: a partial close needs closed_quantity and "
                                    "close_price_per_unit; a full close sets status = closed")
            if close_price is not None:
                self.fail("close_price_per_unit", "close_price_per_unit without closed_quantity: a partial close needs "
                                                  "closed_quantity and closed; a full close sets status = closed")
        return status, closed, closed_quantity, close_price


# --------------------------------------------------------------------------------------------
# The file
# --------------------------------------------------------------------------------------------


def _physical_lines(text: str) -> List[str]:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _check_header(header: Sequence[str], line_number: int) -> List[RowError]:
    errors: List[RowError] = []
    seen: List[str] = []
    duplicates: List[str] = []
    for name in header:
        if name in seen and name not in duplicates:
            duplicates.append(name)
        seen.append(name)
    for name in duplicates:
        errors.append(RowError(line_number, "duplicate_column", "column %r appears more than once in the header" % name))
    suggested: List[str] = []
    for name in header:
        if not name or name in KNOWN_COLUMNS:
            continue
        best: Optional[str] = None
        best_distance: Optional[int] = None
        for known in KNOWN_COLUMNS:
            distance = damerau_levenshtein(name, known)
            if best_distance is None or distance < best_distance:
                best, best_distance = known, distance
        if best is not None and best_distance is not None and best_distance <= DID_YOU_MEAN_DISTANCE:
            errors.append(RowError(line_number, "did_you_mean", "unknown column %r: did you mean %r?" % (name, best)))
            suggested.append(best)
    missing = [name for name in REQUIRED_COLUMNS if name not in header and name not in suggested]
    if not ({"symbol", "occ_symbol"} & set(header)) and not ({"symbol", "occ_symbol"} & set(suggested)):
        missing.append("symbol or occ_symbol")
    if missing:
        errors.append(RowError(line_number, "header", "missing required column(s): %s" % ", ".join(missing)))
    return errors


def _parse(text: str, path: str, sha256: str, today: Optional[datetime.date]) -> PositionsFile:
    if today is None:
        today = state.utc_now().date()
    if text.startswith("﻿"):
        text = text[1:]
    kept = [(number, line) for number, line in enumerate(_physical_lines(text), 1)
            if line.strip() and not line.lstrip().startswith("#")]
    if not kept:
        return PositionsFile([], [RowError(1, "header", "no header row (the file is empty or holds only comments)")],
                             {}, sha256, 0, [], path)
    header_number, header_line = kept[0]
    try:
        header_cells = next(csv.reader([header_line + "\n"]))
    except (csv.Error, StopIteration) as exc:
        return PositionsFile([], [RowError(header_number, "header", "cannot read the header line: %s" % exc)], {},
                             sha256, 0, [], path)
    header = [cell.strip().lower() for cell in header_cells]
    file_errors = _check_header(header, header_number)
    if file_errors:
        return PositionsFile([], file_errors, {}, sha256, 0, header, path)

    quarantined: Dict[str, List[RowError]] = {}
    parsed: List[PositionRow] = []
    row_count = 0

    def record_errors(key: str, errors: Iterable[RowError]) -> None:
        quarantined.setdefault(key, []).extend(errors)

    feed_numbers: List[int] = []
    feed_lines: List[str] = []
    for number, line in kept[1:]:
        if "\x00" in line:
            row_count += 1
            record_errors("ROW-%d" % number, [RowError(number, "row", "line contains a null byte: rejected (invisible "
                                                                   "characters can hide a formula-injection prefix)")])
            continue
        feed_numbers.append(number)
        feed_lines.append(line + "\n")
    reader = csv.reader(feed_lines)
    while True:
        start = reader.line_num
        try:
            record = next(reader)
        except StopIteration:
            break
        except csv.Error as exc:
            where = feed_numbers[start] if start < len(feed_numbers) else header_number
            file_errors.append(RowError(where, "csv", "malformed CSV: %s" % exc))
            break
        number = feed_numbers[start]
        if all(not cell.strip() for cell in record):
            continue
        row_count += 1
        if row_count > MAX_ROWS:
            file_errors.append(RowError(number, "row_limit", "more than %d data rows (the limit); split the file"
                                        % MAX_ROWS))
            break
        if len(record) > len(header):
            record_errors("ROW-%d" % number, [RowError(number, "row", "row has %d cells but the header has %d: an "
                                                                   "unquoted comma?" % (len(record), len(header)))])
            continue
        cells = dict(zip(header, list(record) + [""] * (len(header) - len(record))))
        item = _Record(cells, number, today)
        row = item.parse()
        if item.errors:
            record_errors(item.group_key, item.errors)
        elif row is not None:
            parsed.append(row)
    if file_errors:
        return PositionsFile([], file_errors, {}, sha256, row_count, header, path)
    rows = [row for row in parsed if row.group_id not in quarantined]
    return PositionsFile(rows, [], quarantined, sha256, row_count, header, path)


def parse_positions_text(text: str, path: str = TEXT_PATH, *, today: Optional[datetime.date] = None) -> PositionsFile:
    """Parse ``positions.csv`` text held in memory; ``sha256`` is that of its UTF-8 bytes."""
    if not isinstance(text, str):
        raise TypeError("positions text must be str, got %s" % type(text).__name__)
    return _parse(text, path, hashlib.sha256(text.encode("utf-8")).hexdigest(), today)


def parse_positions_bytes(data: bytes, path: str = TEXT_PATH, *, today: Optional[datetime.date] = None) -> PositionsFile:
    """Parse the raw bytes of ``positions.csv`` (what a tool has already read and snapshotted, so one read
    of the file serves the snapshot, the table and the rows); ``sha256`` is that of the bytes. Invalid UTF-8
    is the file error ``encoding``."""
    if not isinstance(data, (bytes, bytearray)):
        raise TypeError("positions bytes must be bytes, got %s" % type(data).__name__)
    data = bytes(data)
    sha256 = hashlib.sha256(data).hexdigest()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        return PositionsFile([], [RowError(1, "encoding", "positions.csv is not valid UTF-8: %s" % exc)], {}, sha256,
                             0, [], path)
    return _parse(text, path, sha256, today)


def load_positions(path: str, *, today: Optional[datetime.date] = None) -> PositionsFile:
    """Read and parse ``positions.csv`` at ``path`` (module notes): :func:`parse_positions_bytes` of the file's
    bytes. Invalid UTF-8 is the file error ``encoding``; an unreadable file raises ``OSError`` for the caller
    to map."""
    with open(path, "rb") as handle:
        data = handle.read()
    return parse_positions_bytes(data, path, today=today)


__all__ = [
    "CORE_COLUMNS",
    "DID_YOU_MEAN_DISTANCE",
    "EXTENSION_COLUMNS",
    "FILE_ERROR_FIELDS",
    "INDEX_ROOTS",
    "INSTRUMENT_TYPES",
    "KNOWN_COLUMNS",
    "KNOWN_TAGS",
    "MAX_ACCOUNT_LEN",
    "MAX_OCC_SYMBOL_LEN",
    "MAX_ROWS",
    "MAX_SYMBOL_LEN",
    "OPTION_MULTIPLIER",
    "PositionRow",
    "PositionsFile",
    "REQUIRED_COLUMNS",
    "RowError",
    "SIDE_LONG",
    "SIDE_SHORT",
    "STATUSES",
    "STATUS_CLOSED",
    "STATUS_OPEN",
    "STOCK_LIKE_TYPES",
    "STOCK_MULTIPLIER",
    "TRADE_ID_RE",
    "WARNING_ADJUSTED",
    "WARNING_OPENED_BLANK",
    "damerau_levenshtein",
    "extract_tags",
    "load_positions",
    "parse_positions_bytes",
    "parse_positions_text",
    "per_share",
    "pro_rata",
    "synthesize_occ_symbol",
]
