"""OCC/OSI option-symbol parser and renderer (standard library only; no float anywhere).

Ported from Aegis ``services/api/src/aegis_api/instruments/occ_parser.py`` (FairGradient) with
the same field semantics, the same error taxonomy and the same ported test cases; the exercise
and settlement registry of the source is out of scope for the desk (settlement is a property of
the position row and of ``research/calendar.toml``, see plan section 8), so this module parses
the symbol and flags the root, nothing more.

OSI (Options Symbology Initiative) canonical 21-character form
--------------------------------------------------------------
::

    chars  1-6   root symbol, LEFT-justified, space-padded to exactly 6 characters
    chars  7-12  expiration as YYMMDD (century always 20xx: options are future-dated)
    char   13    right: 'C' = call, 'P' = put
    chars  14-21 strike x 1000 as exactly 8 digits, zero-padded, no decimal point

Example: ``"AAPL  240119C00150000"`` is root ``AAPL``, expiring 2024-01-19, a CALL struck at
``Decimal("150.000")``.

Accepted inputs (every one normalises to the canonical form before parsing)
--------------------------------------------------------------------------
* the canonical 21-character form;
* the compact vendor form with no padding (Tradier), e.g. ``"AAPL240119C00150000"``: the OSI
  tail is always exactly 15 characters (6 date + 1 right + 8 strike), so the root is everything
  before the tail (1 to 6 characters) and is padded to 6 -- this is what keeps a digit-bearing
  adjusted root such as ``"AAPL1240119C00150000"`` parseable;
* the Polygon ``"O:"`` prefix on either form, e.g. ``"O:AAPL240119C00150000"``;
* lower-case or mixed-case input: the symbol is upper-cased first, so ``root`` and ``canonical``
  are always upper case and case variants of one contract compare equal.

Leading or trailing whitespace is NOT stripped: a 22-character string is malformed (a trailing
space would otherwise silently shift the fields). Callers that read from CSV strip their cells.

Result (:class:`OccSymbol`, frozen, hashable, usable as a dict key)
-------------------------------------------------------------------
``root`` (upper case, trailing padding removed, digit suffix retained), ``expiration``
(:class:`datetime.date`), ``right`` (the strings ``"CALL"`` / ``"PUT"``, matching
:data:`aegis_desk.bsm.CALL` / :data:`aegis_desk.bsm.PUT`), ``strike`` (:class:`decimal.Decimal`
with exactly three decimal places, e.g. ``Decimal("150.500")``), ``canonical`` (the 21-char
form), ``compact`` (the unpadded form), ``is_adjusted`` and ``base_root``.

Adjusted and mini roots (``is_adjusted``)
-----------------------------------------
The OCC appends a digit to the root after a corporate action (``AAPL1``, ``AAPL2`` ... the
n-th adjustment of that series) and historically used the digit 7 for mini options (10-share
deliverable). The OSI symbol itself cannot distinguish the 7th sequential adjustment from a
mini, and neither case has a standard deliverable: the OCC adjustment memo is the only
authority. The parser therefore follows the Aegis rule exactly: any root with a digit suffix
sets ``is_adjusted=True``, ``base_root`` is the letter-only prefix, and NO multiplier is
asserted -- the parser never guesses economics. Callers that need the deliverable look it up;
the desk's position rows carry their own ``multiplier`` and a ``positions_check`` warning is
raised for adjusted roots.

Strike precision
----------------
The 8-digit field is an integer number of thousandths. ``Decimal(field) / Decimal(1000)`` is
exact and yields exactly three decimal places (``"00150500"`` -> ``Decimal("150.500")``); the
renderer quantizes to ``Decimal("0.001")`` with ``ROUND_HALF_EVEN`` only to produce the field
and raises :class:`OccParseError` when the strike has more than three decimals or does not fit
in eight digits (``>= 100000``), so a symbol is never rendered with silently lost precision.

Errors
------
:class:`OccParseError` (a :class:`ValueError`) carries ``.symbol`` (the offending input) and a
message naming the field: ``length``, ``root``, ``expiration``, ``right`` or ``strike``.
A non-string argument raises :class:`TypeError` (programming error, not data).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

CALL: str = "CALL"
"""``right`` value for a call (identical to :data:`aegis_desk.bsm.CALL`)."""

PUT: str = "PUT"
"""``right`` value for a put (identical to :data:`aegis_desk.bsm.PUT`)."""

RIGHTS: tuple[str, str] = (CALL, PUT)
"""The only ``right`` values an :class:`OccSymbol` carries."""

CANONICAL_LENGTH: int = 21
"""Length of the canonical OSI form."""

ROOT_WIDTH: int = 6
"""Width of the root field in the canonical form."""

TAIL_LENGTH: int = 15
"""Length of the OSI tail: 6 (YYMMDD) + 1 (right) + 8 (strike x 1000)."""

STRIKE_SCALE: Decimal = Decimal(1000)
"""The strike field is the strike multiplied by this, as an integer."""

STRIKE_QUANTUM: Decimal = Decimal("0.001")
"""Strikes carry exactly three decimal places."""

POLYGON_PREFIX: str = "O:"
"""Vendor prefix Polygon puts in front of the OSI symbol (case-insensitive on input)."""

_ADJUSTED_RE = re.compile(r"^([A-Z]+)([0-9]+)$")
_LETTERS_RE = re.compile(r"^[A-Z]+$")
_DIGITS_RE = re.compile(r"^[0-9]+$")


class OccParseError(ValueError):
    """A malformed or economically invalid OCC symbol. ``symbol`` is the offending input."""

    def __init__(self, message: str, symbol: str | None = None) -> None:
        super().__init__(message)
        self.symbol = symbol


@dataclass(frozen=True)
class OccSymbol:
    """A parsed OCC/OSI option symbol (see the module notes for every field's convention)."""

    root: str
    expiration: datetime.date
    right: str
    strike: Decimal
    canonical: str
    compact: str
    is_adjusted: bool
    base_root: str

    @property
    def is_call(self) -> bool:
        return self.right == CALL

    @property
    def is_put(self) -> bool:
        return self.right == PUT


# --------------------------------------------------------------------------------------------
# Normalisation
# --------------------------------------------------------------------------------------------


def normalize(symbol: str) -> str:
    """Upper-case ``symbol``, drop a Polygon prefix and pad a compact form to 21 characters.

    Returns the candidate canonical string; it may still be malformed (wrong length, bad
    fields), which :func:`parse` reports. No field is validated here beyond what is needed to
    locate the root in the compact form: the tail's date digits, the right letter and the
    strike digits must look right for padding to apply, otherwise the string is returned as-is
    (and fails the length check with a message that quotes the original input).
    """
    if not isinstance(symbol, str):
        raise TypeError("symbol must be str, got %s" % type(symbol).__name__)
    text = symbol.upper()
    if text.startswith(POLYGON_PREFIX):
        text = text[len(POLYGON_PREFIX) :]
    if len(text) == CANONICAL_LENGTH:
        return text
    # Compact form: 1..5 root characters followed by the fixed 15-character tail.
    if TAIL_LENGTH < len(text) < CANONICAL_LENGTH:
        root_part = text[:-TAIL_LENGTH]
        tail = text[-TAIL_LENGTH:]
        if (
            _DIGITS_RE.match(tail[:6])
            and tail[6] in ("C", "P")
            and _DIGITS_RE.match(tail[7:])
            and " " not in root_part
        ):
            return root_part.ljust(ROOT_WIDTH) + tail
    return text


# --------------------------------------------------------------------------------------------
# Field parsers (each raises OccParseError naming its field)
# --------------------------------------------------------------------------------------------


def _parse_root(field: str, symbol: str) -> tuple[str, bool, str]:
    root = field.rstrip(" ")
    if not root:
        raise OccParseError("root field is blank in %r" % symbol, symbol)
    if " " in root:
        raise OccParseError(
            "root field %r has leading or embedded spaces (OSI roots are left-justified) in %r"
            % (field, symbol),
            symbol,
        )
    if _LETTERS_RE.match(root):
        return root, False, root
    adjusted = _ADJUSTED_RE.match(root)
    if adjusted:
        return root, True, adjusted.group(1)
    raise OccParseError(
        "root field %r must be letters optionally followed by digits in %r" % (field, symbol),
        symbol,
    )


def _parse_expiration(field: str, symbol: str) -> datetime.date:
    if not _DIGITS_RE.match(field):
        raise OccParseError(
            "expiration field %r is not six digits (YYMMDD) in %r" % (field, symbol), symbol
        )
    year = 2000 + int(field[0:2])  # century forced to 20xx; never strptime's 1969-2068 pivot
    try:
        return datetime.date(year, int(field[2:4]), int(field[4:6]))
    except ValueError as exc:
        raise OccParseError(
            "expiration field %r is not a valid date (%s) in %r" % (field, exc, symbol), symbol
        ) from exc


def _parse_right(char: str, symbol: str) -> str:
    if char == "C":
        return CALL
    if char == "P":
        return PUT
    raise OccParseError("right character must be 'C' or 'P', got %r in %r" % (char, symbol), symbol)


def _parse_strike(field: str, symbol: str) -> Decimal:
    if not _DIGITS_RE.match(field):
        raise OccParseError("strike field %r is not eight digits in %r" % (field, symbol), symbol)
    try:
        thousandths = Decimal(field)
    except InvalidOperation as exc:  # unreachable for an all-digit field; kept for symmetry
        raise OccParseError(
            "strike field %r is not a number in %r" % (field, symbol), symbol
        ) from exc
    if thousandths == 0:
        raise OccParseError("strike of zero is economically invalid in %r" % symbol, symbol)
    # Exact: an integer number of thousandths divided by 1000 has exactly three decimals.
    return (thousandths / STRIKE_SCALE).quantize(STRIKE_QUANTUM)


# --------------------------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------------------------


def parse(symbol: str) -> OccSymbol:
    """Parse any accepted form (module notes) into an :class:`OccSymbol`.

    Raises :class:`TypeError` for a non-string and :class:`OccParseError` for a malformed or
    economically invalid symbol; never returns a partially filled record.
    """
    canonical = normalize(symbol)
    if len(canonical) != CANONICAL_LENGTH:
        raise OccParseError(
            "OCC symbol length must be %d characters after normalisation; got %d from %r"
            % (CANONICAL_LENGTH, len(canonical), symbol),
            symbol,
        )
    root, is_adjusted, base_root = _parse_root(canonical[0:6], symbol)
    expiration = _parse_expiration(canonical[6:12], symbol)
    right = _parse_right(canonical[12], symbol)
    strike = _parse_strike(canonical[13:21], symbol)
    return OccSymbol(
        root=root,
        expiration=expiration,
        right=right,
        strike=strike,
        canonical=canonical,
        compact=root + canonical[6:],
        is_adjusted=is_adjusted,
        base_root=base_root,
    )


def _strike_field(strike: Decimal) -> str:
    if not isinstance(strike, Decimal):
        raise TypeError("strike must be Decimal, got %s" % type(strike).__name__)
    if not strike.is_finite() or strike <= 0:
        raise OccParseError(
            "strike must be a finite positive Decimal, got %s" % strike, str(strike)
        )
    quantized = strike.quantize(STRIKE_QUANTUM, rounding=ROUND_HALF_EVEN)
    if quantized != strike:
        raise OccParseError(
            "strike %s has more than three decimal places and cannot be encoded exactly" % strike,
            str(strike),
        )
    thousandths = int(quantized * STRIKE_SCALE)
    if thousandths > 99999999:
        raise OccParseError(
            "strike %s does not fit the eight-digit OSI field" % strike, str(strike)
        )
    return "%08d" % thousandths


def render(
    root: str, expiration: datetime.date, right: str, strike: Decimal, compact: bool = False
) -> str:
    """Render the canonical 21-character form (or the compact form) from its parts.

    ``root`` is upper-cased and must be 1 to 6 characters of letters optionally followed by
    digits; ``right`` must be ``"CALL"`` or ``"PUT"``; ``expiration`` must fall in 2000-2099;
    ``strike`` is a positive :class:`decimal.Decimal` with at most three decimals below
    100000. Round trip: ``render(*fields(parse(s))) == s`` for every canonical ``s``.
    """
    if not isinstance(root, str):
        raise TypeError("root must be str, got %s" % type(root).__name__)
    root = root.upper()
    if (
        not root
        or len(root) > ROOT_WIDTH
        or not (_LETTERS_RE.match(root) or _ADJUSTED_RE.match(root))
    ):
        raise OccParseError(
            "root %r must be 1-6 characters: letters optionally followed by digits" % root, root
        )
    if not isinstance(expiration, datetime.date) or isinstance(expiration, datetime.datetime):
        raise TypeError("expiration must be datetime.date, got %s" % type(expiration).__name__)
    if not 2000 <= expiration.year <= 2099:
        raise OccParseError(
            "expiration year %d is outside the OSI 20xx century" % expiration.year,
            expiration.isoformat(),
        )
    if right not in RIGHTS:
        raise OccParseError("right must be 'CALL' or 'PUT', got %r" % (right,), str(right))
    tail = "%02d%02d%02d%s%s" % (
        expiration.year - 2000,
        expiration.month,
        expiration.day,
        "C" if right == CALL else "P",
        _strike_field(strike),
    )
    return (root if compact else root.ljust(ROOT_WIDTH)) + tail


def canonical(symbol: str) -> str:
    """The canonical 21-character form of any accepted input (parses and re-renders)."""
    return parse(symbol).canonical


def compact(symbol: str) -> str:
    """The compact (Tradier) form of any accepted input."""
    return parse(symbol).compact


def try_parse(symbol: str) -> tuple[OccSymbol | None, str | None]:
    """``(OccSymbol, None)`` or ``(None, message)``: for validators that collect every error."""
    try:
        return parse(symbol), None
    except OccParseError as exc:
        return None, str(exc)


__all__ = [
    "CALL",
    "CANONICAL_LENGTH",
    "OccParseError",
    "OccSymbol",
    "POLYGON_PREFIX",
    "PUT",
    "RIGHTS",
    "ROOT_WIDTH",
    "STRIKE_QUANTUM",
    "STRIKE_SCALE",
    "TAIL_LENGTH",
    "canonical",
    "compact",
    "normalize",
    "parse",
    "render",
    "try_parse",
]
