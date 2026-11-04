"""Strict reader for ``desk.toml``, the owner's configuration (plan section 8).

Standard library only; the file is parsed with :func:`aegis_desk.toml_compat.load`'s reader
(the vendored strict TOML reader, never ``tomllib``), so TOML floats arrive as ``Decimal``,
local times as ``datetime.time`` and local dates as ``datetime.date``. Everything the reader
does not expect is an error: :class:`DeskConfigError` (a ``ValueError``) carries ``path`` and a
one-line ``reason``, and ``str(error)`` is ``"<path>: <reason>"``.

Rules
-----
* Unknown keys and unknown sections are errors that name the key and the section.
* ``machine_role`` anywhere in the document is an error: "machine_role is derived from the
  environment, never configured" (plan section 8: the file may only make the role stricter,
  and a configured role could loosen it).
* ``[desk]``: ``timezone`` exactly ``"America/New_York"`` (the clock is pure ET arithmetic),
  ``engagement`` (string), ``cadence_profile`` (``lean`` | ``active``), ``chatty_or_quiet``
  (``chatty`` | ``quiet``).
* ``[owner]``: ``broker_exercise_cutoff_et`` (TOML local time), ``approval_level`` (integer 0-5),
  ``declared_equity`` (number > 0, stored as ``Decimal``), ``attention_thresholds`` (inline table
  ``pl_day_pct``, ``pl_trade_abs``, both numbers stored as ``Decimal``), ``disclosures_acknowledged``
  (list of strings), and at least one ``[[owner.accounts]]`` entry with ``name`` (non-empty
  string) and ``type`` (``cash`` | ``margin`` | ``ira``).
* ``[market_data]``: ``vendor`` ``"tradier"``, ``host`` (``sandbox`` | ``production``; the
  reader ACCEPTS production, the tools refuse it until the market-data proxy exists),
  ``token_env`` (string, default ``TRADIER_API_TOKEN``), ``token_file`` (string, optional,
  default ``/workspace/.aegis-secrets/tradier.token``), ``feed_delay_min`` (integer >= 0; the
  sandbox feed is 15 minutes delayed, so the sandbox host requires >= 15), ``quote_ttl_min``
  (integer 1-60), ``grace_min`` (integer 0-30), ``pin_band`` (number >= 0, ``Decimal``).
* ``[rates]``: ``risk_free_rate`` (number, kept as ``Decimal`` and also as the float
  ``risk_free_rate_f`` the pricing engine takes), ``source`` (string), ``as_of`` (TOML local date).
* ``[levels]``: ``order_shape_enabled`` (boolean; default policy false, flipped only after
  counsel sign-off), and the optional ``penny_classes`` and ``penny_all_classes`` (lists of OCC
  roots, each a capital letter then up to five capitals or digits, no duplicates, the two lists
  disjoint; absent is empty): the roots ``aegis_desk.levels`` rounds on the Penny Interval grid
  (0.01 below 3.00, 0.05 at or above) or on 0.01 at every price. A root in neither list uses the
  standard grid (0.05 below 3.00, 0.10 at or above), whose prices are valid on every class's grid.

Integers where a decimal is allowed (``declared_equity = 250000``) are accepted and converted
exactly; a boolean is never a number; a string is never a number, time or date.

:func:`freshness_bound_min` is ``feed_delay_min + quote_ttl_min + grace_min`` (about 25 minutes
with the defaults): the regular-hours bound on ``receipt_utc - min(bid_date, ask_date)``.
``sha256`` is the hex digest of the file's bytes, compared by the governance check against the
last owner-acknowledged hash.
"""

from __future__ import annotations

import datetime
import hashlib
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

from . import toml_compat
from .toml_compat import TomlError

TIMEZONE: str = "America/New_York"
CADENCE_PROFILES: Tuple[str, ...] = ("lean", "active")
CHATTY_OR_QUIET: Tuple[str, ...] = ("chatty", "quiet")
ACCOUNT_TYPES: Tuple[str, ...] = ("cash", "margin", "ira")
VENDOR: str = "tradier"
HOSTS: Tuple[str, ...] = ("sandbox", "production")
DEFAULT_TOKEN_ENV: str = "TRADIER_API_TOKEN"
DEFAULT_TOKEN_FILE: str = "/workspace/.aegis-secrets/tradier.token"
SANDBOX_MIN_FEED_DELAY_MIN: int = 15
"""The sandbox feed is delayed 15 minutes (plan section 3); a smaller declared delay is a lie."""
QUOTE_TTL_RANGE: Tuple[int, int] = (1, 60)
GRACE_RANGE: Tuple[int, int] = (0, 30)
APPROVAL_LEVEL_RANGE: Tuple[int, int] = (0, 5)
MACHINE_ROLE_KEY: str = "machine_role"
MACHINE_ROLE_MESSAGE: str = "machine_role is derived from the environment, never configured"
SECTIONS: Tuple[str, ...] = ("desk", "owner", "market_data", "rates", "levels")

_DESK_KEYS = ("timezone", "engagement", "cadence_profile", "chatty_or_quiet")
_OWNER_KEYS = ("broker_exercise_cutoff_et", "approval_level", "declared_equity", "attention_thresholds",
               "disclosures_acknowledged", "accounts")
_ATTENTION_KEYS = ("pl_day_pct", "pl_trade_abs")
_ACCOUNT_KEYS = ("name", "type")
_MARKET_DATA_KEYS = ("vendor", "host", "token_env", "token_file", "feed_delay_min", "quote_ttl_min",
                     "grace_min", "pin_band")
_RATES_KEYS = ("risk_free_rate", "source", "as_of")
_LEVELS_KEYS = ("order_shape_enabled", "penny_classes", "penny_all_classes")
OCC_ROOT_RE = re.compile(r"^[A-Z][A-Z0-9]{0,5}\Z")
"""An OCC root as ``[levels] penny_classes`` and ``penny_all_classes`` name it (``WMT``, ``SPXW``, ``BRKB1``)."""


class DeskConfigError(ValueError):
    """``desk.toml`` cannot be used; ``path`` names the file and ``reason`` is one line."""

    def __init__(self, path: str, reason: str) -> None:
        self.path = path
        self.reason = " ".join(str(reason).split())
        super().__init__("%s: %s" % (path, self.reason))


# --------------------------------------------------------------------------------------------
# Sections
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DeskSection:
    timezone: str
    engagement: str
    cadence_profile: str
    chatty_or_quiet: str


@dataclass(frozen=True)
class AccountEntry:
    name: str
    type: str


@dataclass(frozen=True)
class AttentionThresholds:
    pl_day_pct: Decimal
    pl_trade_abs: Decimal


@dataclass(frozen=True)
class OwnerSection:
    broker_exercise_cutoff_et: datetime.time
    approval_level: int
    declared_equity: Decimal
    attention_thresholds: AttentionThresholds
    disclosures_acknowledged: List[str]
    accounts: List[AccountEntry]


@dataclass(frozen=True)
class MarketDataSection:
    vendor: str
    host: str
    token_env: str
    token_file: str
    feed_delay_min: int
    quote_ttl_min: int
    grace_min: int
    pin_band: Decimal


@dataclass(frozen=True)
class RatesSection:
    risk_free_rate: Decimal
    risk_free_rate_f: float
    source: str
    as_of: datetime.date


@dataclass(frozen=True)
class LevelsSection:
    order_shape_enabled: bool
    penny_classes: Tuple[str, ...] = ()
    penny_all_classes: Tuple[str, ...] = ()


@dataclass(frozen=True)
class DeskConfig:
    """The parsed ``desk.toml``: five frozen sections plus the file's SHA-256 and path."""

    desk: DeskSection
    owner: OwnerSection
    market_data: MarketDataSection
    rates: RatesSection
    levels: LevelsSection
    sha256: str
    path: str


def freshness_bound_min(cfg: DeskConfig) -> int:
    """``feed_delay_min + quote_ttl_min + grace_min``: the regular-hours freshness bound in minutes."""
    data = cfg.market_data
    return data.feed_delay_min + data.quote_ttl_min + data.grace_min


# --------------------------------------------------------------------------------------------
# Reader
# --------------------------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return _is_int(value) or isinstance(value, Decimal)


def _as_decimal(value: Any) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(value)


class _Reader:
    """One parse of one document; every ``fail`` carries the file path."""

    def __init__(self, path: str) -> None:
        self.path = path

    def fail(self, reason: str) -> DeskConfigError:
        return DeskConfigError(self.path, reason)

    # -- structure ---------------------------------------------------------------------------
    def refuse_machine_role(self, node: Any, where: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key == MACHINE_ROLE_KEY:
                    raise self.fail("%s (found at %s)" % (MACHINE_ROLE_MESSAGE, where + key))
                self.refuse_machine_role(value, where + key + ".")
        elif isinstance(node, list):
            for index, value in enumerate(node):
                self.refuse_machine_role(value, "%s[%d]." % (where[:-1], index) if where.endswith(".") else where)

    def check_keys(self, table: Dict[str, Any], allowed: Tuple[str, ...], where: str) -> None:
        for key in table:
            if key not in allowed:
                raise self.fail("unknown key '%s' in section %s (allowed: %s)" % (key, where, ", ".join(allowed)))

    def section(self, document: Dict[str, Any], name: str) -> Dict[str, Any]:
        if name not in document:
            raise self.fail("missing section [%s]" % name)
        table = document[name]
        if not isinstance(table, dict):
            raise self.fail("section [%s] must be a table, not a %s value" % (name, type(table).__name__))
        return table

    def require(self, table: Dict[str, Any], key: str, where: str) -> Any:
        if key not in table:
            raise self.fail("missing key '%s' in section %s" % (key, where))
        return table[key]

    # -- typed values --------------------------------------------------------------------------
    def string(self, table: Dict[str, Any], key: str, where: str, non_empty: bool = True) -> str:
        value = self.require(table, key, where)
        if not isinstance(value, str) or (non_empty and not value.strip()):
            raise self.fail("%s %s must be a %sstring (got %r)" % (where, key, "non-empty " if non_empty else "", value))
        return value

    def choice(self, table: Dict[str, Any], key: str, where: str, choices: Tuple[str, ...]) -> str:
        value = self.require(table, key, where)
        if not isinstance(value, str) or value not in choices:
            raise self.fail("%s %s must be one of %s (got %r)" % (where, key, ", ".join(choices), value))
        return value

    def integer(self, table: Dict[str, Any], key: str, where: str, low: Optional[int], high: Optional[int]) -> int:
        value = self.require(table, key, where)
        if not _is_int(value):
            raise self.fail("%s %s must be an integer (got %r)" % (where, key, value))
        if low is not None and high is not None and not low <= value <= high:
            raise self.fail("%s %s must be between %d and %d (got %d)" % (where, key, low, high, value))
        if low is not None and high is None and value < low:
            raise self.fail("%s %s must be >= %d (got %d)" % (where, key, low, value))
        return value

    def number(self, table: Dict[str, Any], key: str, where: str, minimum: Optional[Decimal] = None,
               exclusive: bool = False) -> Decimal:
        value = self.require(table, key, where)
        if not _is_number(value):
            raise self.fail("%s %s must be a number (got %r)" % (where, key, value))
        result = _as_decimal(value)
        if minimum is not None:
            if exclusive and not result > minimum:
                raise self.fail("%s %s must be a number > %s (got %s)" % (where, key, minimum, result))
            if not exclusive and not result >= minimum:
                raise self.fail("%s %s must be a number >= %s (got %s)" % (where, key, minimum, result))
        return result

    def boolean(self, table: Dict[str, Any], key: str, where: str) -> bool:
        value = self.require(table, key, where)
        if not isinstance(value, bool):
            raise self.fail("%s %s must be a boolean (true or false), got %r" % (where, key, value))
        return value

    def local_time(self, table: Dict[str, Any], key: str, where: str) -> datetime.time:
        value = self.require(table, key, where)
        if not isinstance(value, datetime.time) or value.tzinfo is not None:
            raise self.fail("%s %s must be a TOML local time such as 16:30:00 (got %r)" % (where, key, value))
        return value

    def local_date(self, table: Dict[str, Any], key: str, where: str) -> datetime.date:
        value = self.require(table, key, where)
        if not isinstance(value, datetime.date) or isinstance(value, datetime.datetime):
            raise self.fail("%s %s must be a TOML local date (YYYY-MM-DD), got %r" % (where, key, value))
        return value

    # -- sections ------------------------------------------------------------------------------
    def desk(self, document: Dict[str, Any]) -> DeskSection:
        where = "[desk]"
        table = self.section(document, "desk")
        self.check_keys(table, _DESK_KEYS, where)
        timezone = self.string(table, "timezone", where)
        if timezone != TIMEZONE:
            raise self.fail('%s timezone must be exactly "%s" (got %r)' % (where, TIMEZONE, timezone))
        return DeskSection(
            timezone=timezone,
            engagement=self.string(table, "engagement", where),
            cadence_profile=self.choice(table, "cadence_profile", where, CADENCE_PROFILES),
            chatty_or_quiet=self.choice(table, "chatty_or_quiet", where, CHATTY_OR_QUIET),
        )

    def owner(self, document: Dict[str, Any]) -> OwnerSection:
        where = "[owner]"
        table = self.section(document, "owner")
        self.check_keys(table, _OWNER_KEYS, where)
        cutoff = self.local_time(table, "broker_exercise_cutoff_et", where)
        level = self.integer(table, "approval_level", where, APPROVAL_LEVEL_RANGE[0], APPROVAL_LEVEL_RANGE[1])
        equity = self.number(table, "declared_equity", where, Decimal(0), exclusive=True)
        thresholds = self.require(table, "attention_thresholds", where)
        if not isinstance(thresholds, dict):
            raise self.fail("%s attention_thresholds must be an inline table { pl_day_pct = ..., pl_trade_abs = ... }"
                            " (got %r)" % (where, thresholds))
        inner = "[owner] attention_thresholds"
        self.check_keys(thresholds, _ATTENTION_KEYS, inner)
        attention = AttentionThresholds(
            pl_day_pct=self.number(thresholds, "pl_day_pct", inner),
            pl_trade_abs=self.number(thresholds, "pl_trade_abs", inner),
        )
        disclosures = self.require(table, "disclosures_acknowledged", where)
        if not isinstance(disclosures, list) or not all(isinstance(item, str) for item in disclosures):
            raise self.fail("%s disclosures_acknowledged must be a list of strings (got %r)" % (where, disclosures))
        accounts_raw = table.get("accounts")
        entries = "[[owner.accounts]]"
        if accounts_raw is None or (isinstance(accounts_raw, list) and not accounts_raw):
            raise self.fail("%s needs at least one entry (name and type)" % entries)
        if not isinstance(accounts_raw, list) or not all(isinstance(item, dict) for item in accounts_raw):
            raise self.fail("%s must be an array of tables (one [[owner.accounts]] block per account)" % entries)
        accounts: List[AccountEntry] = []
        for index, entry in enumerate(accounts_raw, 1):
            label = "%s entry %d" % (entries, index)
            self.check_keys(entry, _ACCOUNT_KEYS, label)
            accounts.append(AccountEntry(
                name=self.string(entry, "name", label),
                type=self.choice(entry, "type", label, ACCOUNT_TYPES),
            ))
        return OwnerSection(
            broker_exercise_cutoff_et=cutoff,
            approval_level=level,
            declared_equity=equity,
            attention_thresholds=attention,
            disclosures_acknowledged=list(disclosures),
            accounts=accounts,
        )

    def market_data(self, document: Dict[str, Any]) -> MarketDataSection:
        where = "[market_data]"
        table = self.section(document, "market_data")
        self.check_keys(table, _MARKET_DATA_KEYS, where)
        vendor = self.string(table, "vendor", where)
        if vendor != VENDOR:
            raise self.fail('%s vendor must be "%s" (got %r)' % (where, VENDOR, vendor))
        host = self.choice(table, "host", where, HOSTS)
        token_env = self.string(table, "token_env", where) if "token_env" in table else DEFAULT_TOKEN_ENV
        token_file = self.string(table, "token_file", where) if "token_file" in table else DEFAULT_TOKEN_FILE
        feed_delay = self.integer(table, "feed_delay_min", where, 0, None)
        if host == "sandbox" and feed_delay < SANDBOX_MIN_FEED_DELAY_MIN:
            raise self.fail("%s feed_delay_min must be >= %d on the sandbox host (its feed is delayed %d minutes); got %d"
                            % (where, SANDBOX_MIN_FEED_DELAY_MIN, SANDBOX_MIN_FEED_DELAY_MIN, feed_delay))
        return MarketDataSection(
            vendor=vendor,
            host=host,
            token_env=token_env,
            token_file=token_file,
            feed_delay_min=feed_delay,
            quote_ttl_min=self.integer(table, "quote_ttl_min", where, QUOTE_TTL_RANGE[0], QUOTE_TTL_RANGE[1]),
            grace_min=self.integer(table, "grace_min", where, GRACE_RANGE[0], GRACE_RANGE[1]),
            pin_band=self.number(table, "pin_band", where, Decimal(0)),
        )

    def rates(self, document: Dict[str, Any]) -> RatesSection:
        where = "[rates]"
        table = self.section(document, "rates")
        self.check_keys(table, _RATES_KEYS, where)
        rate = self.number(table, "risk_free_rate", where)
        return RatesSection(
            risk_free_rate=rate,
            risk_free_rate_f=float(rate),
            source=self.string(table, "source", where),
            as_of=self.local_date(table, "as_of", where),
        )

    def roots(self, table: Dict[str, Any], key: str, where: str) -> Tuple[str, ...]:
        """An optional list of OCC roots (absent: empty); anything else names the key and the section."""
        if key not in table:
            return ()
        value = table[key]
        shape = "a list of OCC roots (a capital letter, then up to five capitals or digits)"
        if not isinstance(value, list):
            raise self.fail("%s %s must be %s, got %r" % (where, key, shape, value))
        roots: List[str] = []
        for item in value:
            if not isinstance(item, str) or not OCC_ROOT_RE.match(item):
                raise self.fail("%s %s must be %s: %r is not a root" % (where, key, shape, item))
            if item in roots:
                raise self.fail("%s %s names %s twice (a duplicate)" % (where, key, item))
            roots.append(item)
        return tuple(roots)

    def levels(self, document: Dict[str, Any]) -> LevelsSection:
        where = "[levels]"
        table = self.section(document, "levels")
        self.check_keys(table, _LEVELS_KEYS, where)
        enabled = self.boolean(table, "order_shape_enabled", where)
        penny = self.roots(table, "penny_classes", where)
        penny_all = self.roots(table, "penny_all_classes", where)
        both = [root for root in penny if root in penny_all]
        if both:
            raise self.fail("%s penny_classes and penny_all_classes both name %s: a root has one tick class"
                            % (where, ", ".join(both)))
        return LevelsSection(order_shape_enabled=enabled, penny_classes=penny, penny_all_classes=penny_all)

    def parse(self, document: Dict[str, Any], sha256: str) -> DeskConfig:
        self.refuse_machine_role(document, "")
        for key, value in document.items():
            if key in SECTIONS:
                continue
            if isinstance(value, dict):
                raise self.fail("unknown section [%s] (expected only %s)" % (key, ", ".join("[%s]" % s for s in SECTIONS)))
            raise self.fail("unknown top-level key '%s' (expected only the sections %s)"
                            % (key, ", ".join("[%s]" % s for s in SECTIONS)))
        return DeskConfig(
            desk=self.desk(document),
            owner=self.owner(document),
            market_data=self.market_data(document),
            rates=self.rates(document),
            levels=self.levels(document),
            sha256=sha256,
            path=self.path,
        )


def _parse_text(text: str, path: str, sha256: str) -> DeskConfig:
    reader = _Reader(path)
    try:
        document = toml_compat.loads(text)
    except TomlError as exc:
        position = ""
        if exc.line is not None:
            position = " at line %d" % exc.line
            if exc.col is not None:
                position += ", column %d" % exc.col
        raise reader.fail("invalid TOML%s: %s" % (position, exc.message)) from None
    return reader.parse(document, sha256)


def parse_desk_toml_text(text: str, path: str = "<text>") -> DeskConfig:
    """Parse ``desk.toml`` text held in memory; ``sha256`` is that of its UTF-8 bytes."""
    if not isinstance(text, str):
        raise DeskConfigError(path, "desk.toml text must be a str, got %s" % type(text).__name__)
    return _parse_text(text, path, hashlib.sha256(text.encode("utf-8")).hexdigest())


def load_desk_config(path: str) -> DeskConfig:
    """Read and validate ``desk.toml`` at ``path`` (module notes); errors are :class:`DeskConfigError`."""
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError as exc:
        raise DeskConfigError(path, "cannot read desk.toml: %s" % (exc.strerror or exc)) from None
    sha256 = hashlib.sha256(data).hexdigest()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise DeskConfigError(path, "desk.toml is not valid UTF-8: %s" % exc) from None
    return _parse_text(text, path, sha256)


__all__ = [
    "ACCOUNT_TYPES",
    "APPROVAL_LEVEL_RANGE",
    "AccountEntry",
    "AttentionThresholds",
    "CADENCE_PROFILES",
    "CHATTY_OR_QUIET",
    "DEFAULT_TOKEN_ENV",
    "DEFAULT_TOKEN_FILE",
    "DeskConfig",
    "DeskConfigError",
    "DeskSection",
    "GRACE_RANGE",
    "HOSTS",
    "LevelsSection",
    "MACHINE_ROLE_KEY",
    "MACHINE_ROLE_MESSAGE",
    "MarketDataSection",
    "OCC_ROOT_RE",
    "OwnerSection",
    "QUOTE_TTL_RANGE",
    "RatesSection",
    "SANDBOX_MIN_FEED_DELAY_MIN",
    "SECTIONS",
    "TIMEZONE",
    "VENDOR",
    "freshness_bound_min",
    "load_desk_config",
    "parse_desk_toml_text",
]
