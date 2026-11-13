"""GET-only Alpaca market-data client: the desk's single network module (plan sections 3, 9, 10).

This module is the only file in the repository that may import ``urllib.request``,
``http.client``, ``socket`` and ``ssl`` (``scripts/check_no_write_paths.py`` proves it by AST),
and it is the first layer of the no-trade guarantee: every request it can make is a ``GET`` to
one of seven allow-listed read-only paths on one of two pinned hosts of Alpaca's PAPER
environment. The proof is in the shape of the code, so the shape is deliberate:

* Hosts. The only host kind is ``paper``: market data on :data:`DATA_HOST`
  (``data.alpaca.markets``) and three read-only paths of the paper trading API on
  :data:`PAPER_HOST` (``paper-api.alpaca.markets``): :data:`PATH_CLOCK`, :data:`PATH_CALENDAR`
  and :data:`PATH_OPTION_CONTRACTS`. That host also serves orders, positions and the account;
  none of those paths is reachable here. The live trading host :data:`LIVE_HOST` is refused
  (``refused_host``): the desk's only credential is a paper key pair, which cannot reach a live
  account, and the client never points at one.
* The URL is built here from the host kind, an allow-listed path (:func:`host_for_path` names
  the one host each path lives on) and ``urllib.parse.urlencode`` of validated parameters;
  nothing accepts a URL from outside, and :meth:`AlpacaClient.url_for` re-parses what it built
  and refuses anything whose scheme, host or path differs from the pin (``refused_host`` /
  ``refused_path``). The option-chain path carries the underlying in its last segment
  (``/v1beta1/options/snapshots/{UNDERLYING}``); that segment must match :data:`UNDERLYING_RE`.
* ``urllib.request.Request(url, headers=..., method="GET")`` with the literal method and no
  body; the opener is an explicit :class:`urllib.request.OpenerDirector` holding exactly the
  HTTP or HTTPS handler the host kind needs, the default error handler and processor,
  :class:`RefuseRedirects` and the unknown-scheme handler: no proxy, file, FTP, data, cookie or
  authentication handlers, so the environment's proxy variables are ignored and a 3xx answer
  is an error (``refused_redirect``) instead of a second request to wherever ``Location``
  points. HTTPS uses ``ssl.create_default_context()`` (certificate and hostname verification
  on, TLS 1.2 minimum). A 200 body larger than :data:`MAX_RESPONSE_BYTES` is ``decode``.
* The transport method is named ``fetch``, not ``send``: the gate rejects every ``.send(...)``
  call in a module that imports ``socket`` or ``http.client`` (a raw socket or connection
  write), and this module imports both to classify ``socket.timeout`` (not a ``TimeoutError``
  before Python 3.10) and ``http.client.HTTPException`` (a half-read response) as explicit
  statuses. :class:`Transport` documents the contract; :class:`UrllibTransport` is the live
  one, :class:`OfflineTransport` replays the recorded fixture sets without a socket or a key.

Credential. Alpaca authenticates with two headers, ``APCA-API-KEY-ID`` and
``APCA-API-SECRET-KEY``. :class:`Credential` holds both in slots, prints as
``Credential(source=env)`` and :meth:`Credential.auth_headers` is its only accessor.
:func:`load_credential` reads the two environment variables named in ``desk.toml``
(``key_env``, ``secret_env``), or, when neither is set, a credentials file with no group or other
permission bits holding the key ID on line 1 and the secret on line 2.

Statuses (:class:`AlpacaError`; ``str(err)`` is ``"<status>: <message>"``)
-------------------------------------------------------------------------
``no_credential`` (neither variable nor the file holds a usable pair, or only one of the two
variables is set), ``credential_file_mode`` (the file is not a regular file or has group/other
permission bits), ``auth`` (HTTP 401/403: a wrong, deleted or live key on the paper host),
``subscription`` (any error answer whose body says "subscription does not permit", in any case:
the plan does not cover the data asked for), ``quota`` (HTTP 429; the ``X-RateLimit-*`` headers
are on the receipt), ``bad_request`` (HTTP 400 or 422), ``http`` (any other non-200 answer),
``network`` (the connection failed: refused, reset, DNS, TLS, a half-read response),
``timeout`` (no answer within ``timeout_s``), ``decode`` (a 200 whose body is not UTF-8 JSON of
the expected shape, carries a field that is present but unreadable, a contract whose fields
disagree with its OCC symbol, a repeated page token, or echoes the credential), ``incomplete``
(a paginated answer still had pages after :data:`MAX_PAGES`; nothing partial is returned),
``refused_redirect`` (any 3xx), ``refused_host`` (an unknown host kind, the live host, or a built
URL that does not point at the pinned host), ``refused_path`` (a path outside
:data:`ALLOWED_PATHS`, or a malformed underlying segment), ``bad_symbol`` (a symbol failing
:data:`SYMBOL_RE`; nothing is sent). No retries happen here: the cache decides.

Message hygiene (brief non-negotiable 3). An error message never carries request headers, the
URL query, the key ID, the secret or more than the first :data:`MAX_MESSAGE_CHARS` printable
characters of a response body; the key ID and the secret are each replaced by ``[redacted]`` if
they somehow appear in a body or a reason string, and a non-200 body whose decoded JSON carries
either value (written in ``\\u`` escapes the raw replacement cannot see) is withheld whole
(:data:`BODY_WITHHELD`). A 200 whose body carries EITHER value, raw or
inside a decoded JSON string (a vendor echoing a header), is refused as ``decode`` before the
caller sees the document, so nothing downstream (the cache, the ledger, a tool's output) can ever
hold it.

Receipts. Every request handed to a transport, whether it succeeds or fails, produces exactly
one :class:`aegis_desk.market_types.RequestReceipt` passed to the ``on_request`` hook (the
cache's ledger): the concrete path, the symbol count, the transport's ``receipt_utc``, the
outcome status, the HTTP status when there was an answer, the rate-limit headers when present
(``X-RateLimit-Limit`` -> ``ratelimit_allowed``, ``-Remaining`` -> ``ratelimit_available``, their
difference -> ``ratelimit_used``, ``-Reset`` epoch seconds -> ``ratelimit_expiry_utc``) and the
elapsed milliseconds. A request that is never sent (``bad_symbol``, ``refused_path``) produces
no receipt; each page of a paginated answer is its own request and its own receipt, so pages
count toward the cache's request budget. A decode failure after a 200 is one receipt with status
``decode``, so the ledger counts the request once.

Normalisation (consumers never see an Alpaca shape). Each answer becomes the desk's normalised
records, built into the shared ``market_types`` types with their ``from_record`` constructors:

* Quotes (``quotes``): equities go to :data:`PATH_STOCK_QUOTES` with ``feed=<stock_feed>``
  (``iex``), OCC option symbols to :data:`PATH_OPTION_QUOTES` with ``feed=<options_feed>``
  (``indicative`` by default, always sent explicitly), each in batches of :data:`MAX_BATCH`. An
  Alpaca quote carries one stamp ``t`` (RFC 3339, nanoseconds), so ``bid_date`` and
  ``ask_date`` are both ``t`` in epoch milliseconds; ``bp``/``ap``/``bs``/``as``/``bx``/``ax``
  become ``bid``/``ask``/``bidsize``/``asksize``/``bidexch``/``askexch``; ``type`` is ``stock``
  or ``option``. An option's ``underlying`` (its OCC root unless the root is adjusted),
  ``strike``, ``expiration_date``, ``option_type`` and ``root_symbol`` come from the OCC symbol;
  ``contract_size`` is not published on this path (``None``). Alpaca echoes no list of unknown
  symbols, so ``unmatched`` is always empty and a requested symbol it did not quote is in
  ``not_returned`` (reported, never silently dropped).
* Chains (``option_chain``): the contract listing (:data:`PATH_OPTION_CONTRACTS`, ``status``
  active, one expiration, every root) gives each contract's ``underlying``, ``root_symbol``,
  ``strike``, ``option_type``, ``open_interest`` and ``contract_size`` (the deliverable ``size``,
  or ``None`` when ``size`` and ``multiplier`` disagree, so a non-standard contract is never
  priced as standard), cross-checked against its OCC symbol; the snapshots
  (``/v1beta1/options/snapshots/{UNDERLYING}``) give the latest quote (both stamps from its ``t``)
  and the latest trade (``last``, ``last_volume``, ``trade_date``). A listed contract without a
  snapshot is an option with no quote (undated); a snapshot without a listing keeps its OCC
  fields and ``contract_size`` ``None``. Vendor Greeks and implied volatility are ignored (the
  desk computes its own). Entries are in symbol order.
* Expirations (``option_expirations``): the distinct ``expiration_date`` values of the active
  call contracts expiring from ``start`` through ``start`` + ``window_days``
  (:data:`EXPIRATIONS_WINDOW_DAYS`), sorted. Alpaca has no separate expirations endpoint, and its
  listing without an upper bound stops at the coming weekend, so the window is always explicit.
* Strikes (``option_strikes``): the distinct strikes of one expiration's contract listing.
* History (``history``): :data:`PATH_STOCK_BARS` with ``timeframe=1Day`` (or ``1Week`` /
  ``1Month``), ``adjustment=raw``, ``feed=<stock_feed>``; a bar's ``date`` is the America/New_York
  date of its ``t``.
* Minute bars (``minute_bars``): the same path with ``timeframe=1Min`` between two UTC instants; a bar's
  ``start_utc`` is its ``t`` (the start of its minute) and its ``close`` the last trade of that minute on the stock
  feed. The Structuring Desk reads the stock's price at an option quote's own stamp from them, when the option was
  quoted more than 60 seconds before the stock (the indicative feed updates a thinly traded option less often).
* Clock (``clock``): :data:`PATH_CLOCK` on the paper host; ``state`` is ``open`` or ``closed``
  from ``is_open``, ``date`` the ET date of ``timestamp``, ``next_change`` the ET ``HH:MM`` of
  ``next_close`` (open) or ``next_open`` (closed) and ``next_state`` the state it leads to.
* Calendar (``calendar``): :data:`PATH_CALENDAR` for one month; Alpaca lists trading days only,
  so every listed day is ``open`` (pre-market ``session_open``-``open``, regular
  ``open``-``close``, post-market ``close``-``session_close``) and every other date of the month
  is ``closed``.

Clock. The client itself never reads the wall clock. :class:`UrllibTransport` stamps each
receipt with :func:`aegis_desk.state.utc_now` (and measures elapsed time with
``time.monotonic``); :class:`OfflineTransport` stamps with its injected ``now`` (default: the
fixture set's ``clock.json`` timestamp, the vendor clock as read at the recorded fetch), so
offline runs are reproducible to the second.

Numbers. Bodies are parsed with plain ``json.loads`` (``NaN``/``Infinity`` rejected) and the
shared ``market_types`` constructors turn each JSON number into ``Decimal(str(value))``, the
shortest round-trip text of the parsed float (``648.1`` for the vendor's ``648.10``, numerically
equal); a record stays JSON-native, which is what the cache stores. Strikes from an OCC symbol
are written as their plain decimal text (``"97.5"``).

Offline fixtures. :class:`OfflineTransport` maps a request to ``<fixture_dir>/<set>/<file>`` by
:func:`fixture_file_name`, a deterministic function of the path and the query:

* ``stock_quotes__<SYMBOLS>.json`` and ``option_quotes__<SYMBOLS>.json``: the ``symbols``
  parameter upper-cased, sorted and comma-joined (the ``feed`` is not part of the key);
* ``snapshots__<UNDERLYING>__<expiration_date>.json`` (``snapshots__<UNDERLYING>.json`` with no
  ``expiration_date``);
* ``contracts__<UNDERLYING>__<expiration_date>.json`` for one expiration, else
  ``contracts__<UNDERLYING>__<expiration_date_gte>__<expiration_date_lte>.json`` (the
  ``underlying_symbols`` parameter upper-cased; ``status``, ``type`` and ``limit`` are not part of
  the key);
* ``bars__<SYMBOL>__<start>__<end>.json``;
* ``clock.json`` and ``calendar__<start>__<end>.json``;
* a ``page_token`` adds ``__page-<token>`` before ``.json`` (the token must match
  ``[A-Za-z0-9_-]{1,64}``).

A missing file is ``AlpacaError("network")`` with the message
``no offline fixture for <path>?<key>``, the key being the file name after its first ``__``.
"""

from __future__ import annotations

import calendar as calendar_module
import datetime
import http.client
import json
import os
import re
import socket
import ssl
import stat
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field, replace
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple

from . import market_clock, occ
from .market_types import (
    Bar,
    CalendarDay,
    ClockInfo,
    MarketTypesError,
    Quote,
    RequestReceipt,
    from_epoch_s,
    from_rfc3339,
    parse_hhmm,
    parse_iso_date,
    to_decimal,
    to_epoch_ms,
    to_int,
    to_str,
)
from .state import utc_now

# --------------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------------

DATA_HOST: str = "data.alpaca.markets"
"""Alpaca's market-data host (stocks and options; paper and live keys alike)."""
PAPER_HOST: str = "paper-api.alpaca.markets"
"""Alpaca's paper trading host: only :data:`PAPER_PATHS` are reachable on it."""
LIVE_HOST: str = "api.alpaca.markets"
"""Alpaca's live trading host: refused, never pinned (named only for the refusal and its tests)."""
LOOPBACK_HOST: str = "127.0.0.1"
"""Host of the ``loopback:<port>`` kind: plain HTTP for the fake-server tests only."""

HOST_KIND_PAPER: str = "paper"
HOST_KINDS: Tuple[str, ...] = (HOST_KIND_PAPER,)
"""The one HTTPS host kind; anything else but ``loopback:<port>`` is ``refused_host``."""
LIVE_HOST_KIND: str = "live"
LOOPBACK_PREFIX: str = "loopback:"

PATH_STOCK_QUOTES: str = "/v2/stocks/quotes/latest"
PATH_STOCK_BARS: str = "/v2/stocks/bars"
PATH_OPTION_SNAPSHOTS: str = "/v1beta1/options/snapshots/{underlying}"
"""A template: the last segment is the underlying (:func:`snapshots_path`)."""
PATH_OPTION_QUOTES: str = "/v1beta1/options/quotes/latest"
PATH_CLOCK: str = "/v2/clock"
PATH_CALENDAR: str = "/v2/calendar"
PATH_OPTION_CONTRACTS: str = "/v2/options/contracts"
DATA_PATHS: Tuple[str, ...] = (PATH_STOCK_QUOTES, PATH_STOCK_BARS, PATH_OPTION_SNAPSHOTS, PATH_OPTION_QUOTES)
"""The market-data paths, all on :data:`DATA_HOST`."""
PAPER_PATHS: Tuple[str, ...] = (PATH_CLOCK, PATH_CALENDAR, PATH_OPTION_CONTRACTS)
"""The only paper-host paths: the clock, the calendar and the option-contract listing (all reads)."""
ALLOWED_PATHS: Tuple[str, ...] = DATA_PATHS + PAPER_PATHS
"""Every path the client can request (the snapshot path as its template)."""
SNAPSHOTS_PREFIX: str = "/v1beta1/options/snapshots/"

UNDERLYING_RE = re.compile(r"^[A-Z][A-Z0-9]{0,9}(?:\.[A-Z0-9]{1,4})?\Z")
"""The underlying segment of the snapshot path (``SPY``, ``BRK.B``): no slash, no dot-segment."""

RATE_LIMIT_PER_MIN: int = 200
"""Alpaca's market-data budget per minute on the free plan (the cache keeps headroom under it)."""

SYMBOL_RE = re.compile(r"^[A-Za-z0-9.$^:_-]{1,32}$")
"""Accepted symbol shape (equities, OCC option symbols, index roots); anything else is ``bad_symbol``."""

MAX_BATCH: int = 50
"""Symbols per latest-quotes request (Alpaca allows up to 100 option symbols); longer lists are
sent in consecutive batches."""

SNAPSHOTS_PAGE_LIMIT: int = 1000
"""``limit`` of each snapshot page (Alpaca's maximum)."""
CONTRACTS_PAGE_LIMIT: int = 10000
"""``limit`` of each contract-listing page (Alpaca's maximum)."""
BARS_PAGE_LIMIT: int = 10000
"""``limit`` of each bars page (Alpaca's maximum)."""
MAX_PAGES: int = 10
"""Pages followed through ``next_page_token`` per call; more is ``incomplete`` (no partial answer)."""
EXPIRATIONS_WINDOW_DAYS: int = 400
"""Default window of :meth:`AlpacaClient.option_expirations` (expirations up to ``start`` + this)."""

OPTIONS_FEED_INDICATIVE: str = "indicative"
OPTIONS_FEEDS: Tuple[str, ...] = (OPTIONS_FEED_INDICATIVE, "opra")
"""``indicative``: the free feed (15 minutes delayed, not exchange quotes); ``opra``: subscribers."""
DEFAULT_OPTIONS_FEED: str = OPTIONS_FEED_INDICATIVE
STOCK_FEEDS: Tuple[str, ...] = ("iex",)
"""A paper-only account is entitled to the IEX stock feed only."""
DEFAULT_STOCK_FEED: str = "iex"

DEFAULT_TIMEOUT_S: float = 10.0
MAX_RESPONSE_BYTES: int = 16 * 1024 * 1024
"""A 200 body larger than this is ``decode`` (no unbounded buffering on the shared computer)."""

MAX_ERROR_BODY_BYTES: int = 64 * 1024
"""How much of an error body is read: only its first printable characters reach a message."""

MAX_MESSAGE_CHARS: int = 200
"""Printable characters of a body that may appear in an error message."""

REDACTED: str = "[redacted]"
BODY_WITHHELD: str = "(body withheld: it echoes the credential)"
"""A non-200 body whose decoded JSON carries the key ID or the secret (in ``\\u`` escapes raw redaction misses)."""
STATUS_OK: str = "ok"
STATUSES: Tuple[str, ...] = (
    "no_credential", "credential_file_mode", "auth", "subscription", "quota", "http", "network",
    "timeout", "decode", "incomplete", "refused_redirect", "refused_host", "refused_path",
    "bad_symbol", "bad_request",
)
"""Every value ``AlpacaError.status`` (and a failed receipt's ``status``) can take."""

HEADER_KEY_ID: str = "APCA-API-KEY-ID"
HEADER_SECRET: str = "APCA-API-SECRET-KEY"
HEADER_ACCEPT: str = "application/json"
CREDENTIAL_SOURCES: Tuple[str, ...] = ("env", "file")
HISTORY_INTERVALS: Tuple[str, ...] = ("daily", "weekly", "monthly")
HISTORY_TIMEFRAMES: Dict[str, str] = {"daily": "1Day", "weekly": "1Week", "monthly": "1Month"}
MINUTE_TIMEFRAME: str = "1Min"
"""The ``timeframe`` of :meth:`AlpacaClient.minute_bars`."""
DEFAULT_FIXTURE_SET: str = "regular"
SUBSCRIPTION_MARKER: str = "subscription does not permit"

_FIXTURE_SET_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,40}$")
_PAGE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}\Z")
_HHMM_COMPACT_RE = re.compile(r"^(\d{2})(\d{2})$")
_GROUP_OTHER_BITS = 0o077


# --------------------------------------------------------------------------------------------
# Text hygiene
# --------------------------------------------------------------------------------------------


def _visible_ascii(value: str, allow_space: bool) -> bool:
    low = 0x20 if allow_space else 0x21
    return bool(value) and all(low <= ord(ch) <= 0x7E for ch in value)


def _printable_excerpt(text: str, limit: int = MAX_MESSAGE_CHARS) -> str:
    """Printable characters only, whitespace collapsed, cut at ``limit`` (then ``...``)."""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in text)
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > limit:
        return cleaned[:limit] + "..."
    return cleaned


def _bytes_text(body: bytes) -> str:
    return body.decode("utf-8", errors="replace")


def _json_mentions(value: Any, needle: str) -> bool:
    """True when ``needle`` occurs in any string of a decoded JSON value, keys included (an
    echo written as ``\\uXXXX`` escapes is invisible in the raw bytes and visible here)."""
    if isinstance(value, str):
        return needle in value
    if isinstance(value, dict):
        return any(_json_mentions(key, needle) or _json_mentions(item, needle) for key, item in value.items())
    if isinstance(value, list):
        return any(_json_mentions(item, needle) for item in value)
    return False


# --------------------------------------------------------------------------------------------
# Credential
# --------------------------------------------------------------------------------------------


def _clean_secret(value: Any, what: str) -> str:
    if not isinstance(value, str):
        raise TypeError("%s must be text, got %s" % (what, type(value).__name__))
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("%s is blank" % what)
    if not _visible_ascii(cleaned, allow_space=False):
        raise ValueError("%s contains characters that cannot travel in an HTTP header" % what)
    return cleaned


class Credential:
    """An Alpaca paper key pair reachable only through :meth:`auth_headers`.

    Both values live in slots (no instance ``__dict__`` to dump); ``repr``/``str`` print
    ``Credential(source=env)`` or ``Credential(source=file)``. Programming errors (a blank value,
    the same text twice, an unknown source, non-text) raise ``ValueError``/``TypeError``; the
    absent-credential cases are :func:`load_credential`'s ``AlpacaError`` statuses.
    """

    __slots__ = ("_key_id", "_secret", "_source")

    def __init__(self, key_id: str, secret: str, source: str) -> None:
        if source not in CREDENTIAL_SOURCES:
            raise ValueError("credential source must be one of %s, got %r" % (", ".join(CREDENTIAL_SOURCES), source))
        cleaned_key = _clean_secret(key_id, "key ID")
        cleaned_secret = _clean_secret(secret, "secret")
        if cleaned_key == cleaned_secret:
            raise ValueError("the key ID and the secret are the same text")
        self._key_id = cleaned_key
        self._secret = cleaned_secret
        self._source = source

    @property
    def source(self) -> str:
        """``"env"`` or ``"file"``: where :func:`load_credential` found the pair."""
        return self._source

    def auth_headers(self) -> Dict[str, str]:
        """The two authentication headers (``APCA-API-KEY-ID``, ``APCA-API-SECRET-KEY``); the only accessor."""
        return {HEADER_KEY_ID: self._key_id, HEADER_SECRET: self._secret}

    def __repr__(self) -> str:
        return "Credential(source=%s)" % self._source

    def __str__(self) -> str:
        return self.__repr__()


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


class AlpacaError(Exception):
    """A market-data failure with an explicit ``status`` (module notes); never a quiet negative.

    ``http_status`` is the HTTP status when the vendor answered, ``path`` the concrete
    allow-listed path (never the query). ``message`` is already redacted and bounded by the client.
    """

    def __init__(self, status: str, message: str = "", *, http_status: Optional[int] = None,
                 path: Optional[str] = None) -> None:
        if status not in STATUSES:
            raise ValueError("unknown AlpacaError status %r" % (status,))
        self.status = status
        self.message = message if message else status
        self.http_status = http_status
        self.path = path
        super().__init__("%s: %s" % (self.status, self.message))

    def __str__(self) -> str:
        return "%s: %s" % (self.status, self.message)


class TransportError(AlpacaError):
    """A transport-level failure (``network``, ``timeout``, an oversize body as ``decode``) that
    carries the instant it was observed, so the client can stamp the failure's receipt without
    reading a clock."""

    def __init__(self, status: str, message: str, *, receipt_utc: datetime.datetime,
                 elapsed_ms: Optional[int] = None) -> None:
        super().__init__(status, message)
        self.receipt_utc = receipt_utc
        self.elapsed_ms = elapsed_ms


class _BodyTooLarge(Exception):
    """Internal: a 200 body exceeded :data:`MAX_RESPONSE_BYTES`."""


# --------------------------------------------------------------------------------------------
# Hosts and paths
# --------------------------------------------------------------------------------------------


def snapshots_path(underlying: str) -> str:
    """The concrete option-chain path of ``underlying`` (``refused_path`` when the segment is malformed)."""
    if not isinstance(underlying, str) or not UNDERLYING_RE.match(underlying):
        raise AlpacaError("refused_path", "underlying %r is not a valid option-chain path segment" % (underlying,),
                          path=SNAPSHOTS_PREFIX + (underlying if isinstance(underlying, str) else ""))
    return SNAPSHOTS_PREFIX + underlying


def _path_template(path: Any) -> Optional[str]:
    """The allow-list entry ``path`` instantiates, or ``None`` (never raises)."""
    if not isinstance(path, str):
        return None
    if path in ALLOWED_PATHS and path != PATH_OPTION_SNAPSHOTS:
        return path
    if path.startswith(SNAPSHOTS_PREFIX) and UNDERLYING_RE.match(path[len(SNAPSHOTS_PREFIX):]):
        return PATH_OPTION_SNAPSHOTS
    return None


def host_for_path(path: str) -> str:
    """:data:`DATA_HOST` or :data:`PAPER_HOST`: the one pinned host an allow-listed path lives on."""
    template = _path_template(path)
    if template is None:
        raise AlpacaError("refused_path", "path %r is not an allow-listed read-only endpoint" % (path,), path=path)
    return PAPER_HOST if template in PAPER_PATHS else DATA_HOST


def _resolve_host(host_kind: Any) -> Tuple[str, str, str]:
    """``(scheme, data netloc, paper netloc)`` for a host kind; anything unknown is ``refused_host``."""
    if isinstance(host_kind, str) and host_kind == HOST_KIND_PAPER:
        return "https", DATA_HOST, PAPER_HOST
    if isinstance(host_kind, str) and host_kind.startswith(LOOPBACK_PREFIX):
        port_text = host_kind[len(LOOPBACK_PREFIX):]
        if port_text.isdigit() and 1 <= int(port_text) <= 65535:
            netloc = "%s:%d" % (LOOPBACK_HOST, int(port_text))
            return "http", netloc, netloc
    if host_kind in (LIVE_HOST_KIND, LIVE_HOST):
        raise AlpacaError("refused_host", "the live trading host %s is refused: the desk holds a paper key pair "
                                          "only" % LIVE_HOST)
    raise AlpacaError("refused_host", "host kind %r is not paper or loopback:<port>" % (host_kind,))


# --------------------------------------------------------------------------------------------
# Transports
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TransportResponse:
    """What a transport returns for an answered request: status, lower-cased headers, body,
    the receipt instant and the elapsed milliseconds (``None`` when not measured)."""

    status: int
    headers: Dict[str, str]
    body: bytes
    receipt_utc: datetime.datetime
    elapsed_ms: Optional[int] = None


class Transport(Protocol):
    """The transport contract: one GET of a URL the client built, with the client's headers.

    Returns a :class:`TransportResponse` for any HTTP answer (the client classifies the status)
    and raises :class:`TransportError` when there was no answer (``network``, ``timeout``).
    """

    def fetch(self, url: str, headers: Dict[str, str], timeout_s: float) -> TransportResponse:
        ...


class RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect handler that never follows: a 3xx becomes an ``HTTPError`` the client maps to
    ``refused_redirect``, and no second request is made."""

    def redirect_request(self, req: urllib.request.Request, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:  # noqa: D401 - urllib hook signature
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def _lower_headers(pairs: Iterable[Tuple[Any, Any]]) -> Dict[str, str]:
    return {str(name).lower(): str(value) for name, value in pairs}


def _read_capped(stream: Any) -> bytes:
    chunk = stream.read(MAX_RESPONSE_BYTES + 1)
    if len(chunk) > MAX_RESPONSE_BYTES:
        raise _BodyTooLarge()
    return chunk


def _read_error_body(error: urllib.error.HTTPError) -> bytes:
    stream = error.fp
    if stream is None:
        return b""
    try:
        return stream.read(MAX_ERROR_BODY_BYTES)
    except (OSError, http.client.HTTPException):
        return b""
    finally:
        try:
            stream.close()
        except OSError:  # pragma: no cover - closing a dead socket
            pass


def _elapsed_ms(started: float) -> int:
    return int(round((time.monotonic() - started) * 1000.0))


class UrllibTransport:
    """The live transport: ``urllib.request`` over an explicit opener (module notes).

    ``scheme`` is ``"https"`` (the Alpaca hosts) or ``"http"`` (the loopback fake server). The
    opener and the TLS context are built on first use, so constructing a client performs no I/O.
    """

    def __init__(self, scheme: str = "https") -> None:
        if scheme not in ("https", "http"):
            raise ValueError("scheme must be https or http, got %r" % (scheme,))
        self._scheme = scheme
        self._opener: Optional[urllib.request.OpenerDirector] = None
        self._context: Optional[ssl.SSLContext] = None

    @property
    def scheme(self) -> str:
        return self._scheme

    @property
    def tls_context(self) -> Optional[ssl.SSLContext]:
        """The verifying TLS context (built on first access); ``None`` for the plain-HTTP scheme."""
        self._director()
        return self._context

    def _director(self) -> urllib.request.OpenerDirector:
        if self._opener is None:
            director = urllib.request.OpenerDirector()
            if self._scheme == "https":
                context = ssl.create_default_context()
                context.minimum_version = ssl.TLSVersion.TLSv1_2
                self._context = context
                director.add_handler(urllib.request.HTTPSHandler(context=context))
            else:
                director.add_handler(urllib.request.HTTPHandler())
            director.add_handler(urllib.request.HTTPDefaultErrorHandler())
            director.add_handler(urllib.request.HTTPErrorProcessor())
            director.add_handler(RefuseRedirects())
            director.add_handler(urllib.request.UnknownHandler())
            self._opener = director
        return self._opener

    def fetch(self, url: str, headers: Dict[str, str], timeout_s: float) -> TransportResponse:
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        opener = self._director()
        started = time.monotonic()
        try:
            with opener.open(request, timeout=timeout_s) as response:
                status = int(response.status)
                header_map = _lower_headers(response.getheaders())
                body = _read_capped(response)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
            header_map = _lower_headers(exc.headers.items() if exc.headers is not None else [])
            body = _read_error_body(exc)
        except (socket.timeout, TimeoutError) as exc:
            raise TransportError("timeout", "no response within %.1f s" % timeout_s,
                                 receipt_utc=utc_now(), elapsed_ms=_elapsed_ms(started)) from exc
        except urllib.error.URLError as exc:
            reason = exc.reason
            if isinstance(reason, (socket.timeout, TimeoutError)):
                raise TransportError("timeout", "no response within %.1f s" % timeout_s,
                                     receipt_utc=utc_now(), elapsed_ms=_elapsed_ms(started)) from exc
            raise TransportError("network", _printable_excerpt(str(reason)) or "connection failed",
                                 receipt_utc=utc_now(), elapsed_ms=_elapsed_ms(started)) from exc
        except http.client.HTTPException as exc:
            raise TransportError("network", _printable_excerpt("%s: %s" % (type(exc).__name__, exc)),
                                 receipt_utc=utc_now(), elapsed_ms=_elapsed_ms(started)) from exc
        except _BodyTooLarge:
            raise TransportError("decode", "response body exceeds %d bytes" % MAX_RESPONSE_BYTES,
                                 receipt_utc=utc_now(), elapsed_ms=_elapsed_ms(started)) from None
        except OSError as exc:
            raise TransportError("network", _printable_excerpt("%s: %s" % (type(exc).__name__, exc)),
                                 receipt_utc=utc_now(), elapsed_ms=_elapsed_ms(started)) from exc
        return TransportResponse(status, header_map, body, utc_now(), _elapsed_ms(started))


def _symbols_key(params: Dict[str, str]) -> str:
    symbols = [s.strip().upper() for s in params.get("symbols", "").split(",") if s.strip()]
    return ",".join(sorted(symbols))


def fixture_file_name(path: str, params: Dict[str, str]) -> str:
    """The offline fixture file for an allow-listed request (module notes on the naming rule).

    ``params`` holds one value per query parameter. An unknown path or a page token outside
    ``[A-Za-z0-9_-]{1,64}`` raises ``ValueError``.
    """
    template = _path_template(path)
    if template == PATH_STOCK_QUOTES:
        stem = "stock_quotes__%s" % _symbols_key(params)
    elif template == PATH_OPTION_QUOTES:
        stem = "option_quotes__%s" % _symbols_key(params)
    elif template == PATH_OPTION_SNAPSHOTS:
        stem = "snapshots__%s" % path[len(SNAPSHOTS_PREFIX):].upper()
        if params.get("expiration_date"):
            stem += "__%s" % params["expiration_date"]
    elif template == PATH_OPTION_CONTRACTS:
        underlying = params.get("underlying_symbols", "").upper()
        if params.get("expiration_date"):
            stem = "contracts__%s__%s" % (underlying, params["expiration_date"])
        else:
            stem = "contracts__%s__%s__%s" % (underlying, params.get("expiration_date_gte", ""),
                                              params.get("expiration_date_lte", ""))
    elif template == PATH_STOCK_BARS and params.get("timeframe") == MINUTE_TIMEFRAME:
        stem = "minute_bars__%s__%s__%s" % (params.get("symbols", "").upper(), _compact_instant(params.get("start", "")),
                                            _compact_instant(params.get("end", "")))
    elif template == PATH_STOCK_BARS:
        stem = "bars__%s__%s__%s" % (params.get("symbols", "").upper(), params.get("start", ""), params.get("end", ""))
    elif template == PATH_CLOCK:
        stem = "clock"
    elif template == PATH_CALENDAR:
        stem = "calendar__%s__%s" % (params.get("start", ""), params.get("end", ""))
    else:
        raise ValueError("no offline fixture naming rule for path %r" % (path,))
    token = params.get("page_token")
    if token is not None:
        if not _PAGE_TOKEN_RE.match(token):
            raise ValueError("offline page token %r must match %s" % (token, _PAGE_TOKEN_RE.pattern))
        stem += "__page-%s" % token
    return stem + ".json"


def _compact_instant(text: str) -> str:
    """An RFC 3339 instant without ``-`` and ``:`` (``2026-11-03T19:31:00Z`` -> ``20261103T193100Z``), so a minute-bar
    fixture name holds no colon."""
    return text.replace("-", "").replace(":", "")


def _fixture_key(name: str) -> str:
    """The ``<key>`` part of a fixture file name (``stock_quotes__SPY,WMT.json`` -> ``SPY,WMT``)."""
    stem = name[:-len(".json")] if name.endswith(".json") else name
    return stem.split("__", 1)[1] if "__" in stem else ""


def _clock_instant(set_dir: str, fixture_set: str) -> datetime.datetime:
    path = os.path.join(set_dir, "clock.json")
    try:
        with open(path, "rb") as handle:
            document = json.loads(handle.read().decode("utf-8"))
        stamp = _parse_clock(document).timestamp_utc
    except (OSError, ValueError) as exc:  # MarketTypesError is a ValueError
        raise ValueError("OfflineTransport(%s) needs now=: %s is missing or unreadable (%s)"
                         % (fixture_set, path, exc)) from None
    if stamp is None:
        raise ValueError("OfflineTransport(%s) needs now=: %s carries no timestamp" % (fixture_set, path))
    return stamp


class OfflineTransport:
    """Replays recorded responses from ``<fixture_dir>/<fixture_set>/`` (module notes).

    Needs no credential and opens no socket. ``now`` is the receipt instant of every answer;
    when it is not given, the set's ``clock.json`` timestamp (the vendor clock at the recorded
    fetch) is used, so an offline run is deterministic either way. The set name must be a plain
    identifier (no path separators) and its directory must exist (``ValueError`` otherwise).
    """

    def __init__(self, fixture_dir: str, fixture_set: str = DEFAULT_FIXTURE_SET, *,
                 now: Optional[datetime.datetime] = None) -> None:
        if not isinstance(fixture_dir, str) or not fixture_dir:
            raise ValueError("fixture_dir must be a non-empty path, got %r" % (fixture_dir,))
        if not isinstance(fixture_set, str) or not _FIXTURE_SET_RE.match(fixture_set):
            raise ValueError("fixture set name %r must match %s" % (fixture_set, _FIXTURE_SET_RE.pattern))
        set_dir = os.path.join(fixture_dir, fixture_set)
        if not os.path.isdir(set_dir):
            raise ValueError("no offline fixture set %r under %s" % (fixture_set, fixture_dir))
        if now is None:
            now = _clock_instant(set_dir, fixture_set)
        elif not isinstance(now, datetime.datetime) or now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("now must be an aware datetime, got %r" % (now,))
        self._fixture_dir = fixture_dir
        self._fixture_set = fixture_set
        self._set_dir = set_dir
        self._now = now

    @property
    def fixture_dir(self) -> str:
        return self._fixture_dir

    @property
    def fixture_set(self) -> str:
        return self._fixture_set

    @property
    def set_dir(self) -> str:
        return self._set_dir

    @property
    def receipt_utc(self) -> datetime.datetime:
        """The instant stamped on every answer."""
        return self._now

    def fetch(self, url: str, headers: Dict[str, str], timeout_s: float) -> TransportResponse:
        parts = urllib.parse.urlsplit(url)
        params = {
            key: values[0]
            for key, values in urllib.parse.parse_qs(parts.query, keep_blank_values=True).items()
        }
        try:
            name = fixture_file_name(parts.path, params)
        except ValueError:
            raise TransportError("network", "no offline fixture for %s" % parts.path,
                                 receipt_utc=self._now, elapsed_ms=0) from None
        try:
            with open(os.path.join(self._set_dir, name), "rb") as handle:
                body = handle.read()
        except FileNotFoundError:
            key = _fixture_key(name)
            where = "%s?%s" % (parts.path, key) if key else parts.path
            raise TransportError("network", "no offline fixture for %s" % where,
                                 receipt_utc=self._now, elapsed_ms=0) from None
        return TransportResponse(200, {}, body, self._now, 0)


# --------------------------------------------------------------------------------------------
# Results
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class QuotesResult:
    """``quotes`` keyed by symbol (OCC option symbols in their compact form); ``unmatched`` is
    always empty (Alpaca echoes no list of unknown symbols; kept so callers read one shape);
    ``not_returned`` the requested symbols the vendor did not quote (an incomplete answer,
    reported and never silently dropped); ``receipt`` is the last request's receipt and
    ``receipts`` every batch's, in order."""

    quotes: Dict[str, Quote]
    unmatched: List[str]
    receipt: RequestReceipt
    receipts: Tuple[RequestReceipt, ...] = ()
    not_returned: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ChainResult:
    """One expiration's options (symbol order); ``raw`` holds the vendor's ``option_contracts``
    list and merged ``snapshots`` map; ``receipts`` every request (listing pages, then snapshot
    pages) and ``receipt`` the last."""

    options: List[Quote]
    receipt: RequestReceipt
    raw: Dict[str, Any]
    receipts: Tuple[RequestReceipt, ...] = ()


@dataclass(frozen=True)
class ExpirationsResult:
    dates: List[datetime.date]
    receipt: RequestReceipt
    raw: Dict[str, Any]
    receipts: Tuple[RequestReceipt, ...] = ()


@dataclass(frozen=True)
class StrikesResult:
    strikes: List[Decimal]
    receipt: RequestReceipt
    raw: Dict[str, Any]
    receipts: Tuple[RequestReceipt, ...] = ()


@dataclass(frozen=True)
class HistoryResult:
    bars: List[Bar]
    receipt: RequestReceipt
    raw: Dict[str, Any]
    receipts: Tuple[RequestReceipt, ...] = ()


@dataclass(frozen=True)
class MinuteBar:
    """One one-minute bar: ``start_utc`` (the start of its minute, aware UTC) and its ``close`` (the minute's last
    trade on the stock feed); ``raw`` is the record :meth:`from_record` reads back from the cache."""

    start_utc: datetime.datetime
    close: Optional[Decimal]
    volume: Optional[int] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_record(cls, raw: Dict[str, Any]) -> "MinuteBar":
        if not isinstance(raw, dict):
            raise MarketTypesError("minute bar", "expected an object, got %s" % type(raw).__name__)
        start = from_rfc3339(raw.get("t"), "t")
        if start is None:
            raise MarketTypesError("t", "missing")
        return cls(start_utc=start, close=to_decimal(raw.get("close"), "close"), volume=to_int(raw.get("volume"), "volume"),
                   raw=dict(raw))


@dataclass(frozen=True)
class MinuteBarsResult:
    bars: List[MinuteBar]
    receipt: RequestReceipt
    raw: Dict[str, Any]
    receipts: Tuple[RequestReceipt, ...] = ()


@dataclass(frozen=True)
class ClockResult:
    """The market clock (module notes, "Clock"), read on the paper host."""

    date: datetime.date
    state: str
    description: Optional[str]
    timestamp_utc: Optional[datetime.datetime]
    next_change: Optional[str]
    next_state: Optional[str]
    receipt: RequestReceipt
    raw: Dict[str, Any]


@dataclass(frozen=True)
class CalendarResult:
    days: List[CalendarDay]
    month: Optional[int]
    year: Optional[int]
    receipt: RequestReceipt
    raw: Any


# --------------------------------------------------------------------------------------------
# Normalisation (a present-but-garbled field is a MarketTypesError)
# --------------------------------------------------------------------------------------------


def _object_field(document: Any, key: str, *, required: bool = True) -> Dict[str, Any]:
    """``document[key]`` as a dict; ``null`` (or absent when not ``required``) is ``{}``."""
    if not isinstance(document, dict):
        raise MarketTypesError(key, "the response is not a JSON object")
    if key not in document:
        if required:
            raise MarketTypesError(key, "missing from the response")
        return {}
    value = document[key]
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise MarketTypesError(key, "expected an object or null, got %s" % type(value).__name__)
    return value


def _list_field(document: Any, key: str) -> List[Any]:
    """``document[key]`` as a list (``null`` is ``[]``)."""
    if not isinstance(document, dict):
        raise MarketTypesError(key, "the response is not a JSON object")
    if key not in document:
        raise MarketTypesError(key, "missing from the response")
    value = document[key]
    if value is None:
        return []
    if not isinstance(value, list):
        raise MarketTypesError(key, "expected a list or null, got %s" % type(value).__name__)
    return value


def _entry(value: Any, what: str) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise MarketTypesError(what, "expected a JSON object, got %s" % type(value).__name__)
    return value


def _stamp_ms(value: Any, field_name: str) -> Optional[int]:
    instant = from_rfc3339(value, field_name)
    return None if instant is None else to_epoch_ms(instant)


def _plain_decimal_text(value: Decimal) -> str:
    """``Decimal("105.000")`` -> ``"105"``, ``Decimal("97.500")`` -> ``"97.5"`` (no exponent)."""
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def _quote_fields(quote: Dict[str, Any], where: str) -> Dict[str, Any]:
    """The record fields of one Alpaca quote object (``t`` stamps both sides)."""
    stamp = _stamp_ms(quote.get("t"), where + ".t")
    fields: Dict[str, Any] = {
        "bid": quote.get("bp"), "ask": quote.get("ap"), "bidsize": quote.get("bs"), "asksize": quote.get("as"),
        "bidexch": quote.get("bx"), "askexch": quote.get("ax"), "bid_date": stamp, "ask_date": stamp,
    }
    return fields


def _occ_fields(symbol: str) -> Tuple[occ.OccSymbol, Dict[str, Any]]:
    try:
        parsed = occ.parse(symbol)
    except (occ.OccParseError, TypeError) as exc:
        raise MarketTypesError("symbol", "%r is not an OCC option symbol (%s)" % (symbol, exc)) from None
    fields: Dict[str, Any] = {
        "symbol": parsed.compact, "type": "option",
        "underlying": None if parsed.is_adjusted else parsed.root,
        "strike": _plain_decimal_text(parsed.strike),
        "expiration_date": parsed.expiration.isoformat(),
        "option_type": "call" if parsed.is_call else "put",
        "root_symbol": parsed.root,
    }
    return parsed, fields


def _stock_record(symbol: str, quote: Any) -> Dict[str, Any]:
    record: Dict[str, Any] = {"symbol": symbol.strip().upper(), "type": "stock"}
    record.update(_quote_fields(_entry(quote, "quotes.%s" % symbol), "quotes.%s" % symbol))
    return record


def _option_record(symbol: str, quote: Any) -> Dict[str, Any]:
    _parsed, record = _occ_fields(symbol)
    record.update(_quote_fields(_entry(quote, "quotes.%s" % symbol), "quotes.%s" % symbol))
    return record


def _contract_fields(contract: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """The OCC symbol and record fields of one listed contract, cross-checked against the symbol."""
    symbol = to_str(contract.get("symbol"), "option_contracts.symbol")
    if symbol is None or not symbol.strip():
        raise MarketTypesError("option_contracts.symbol", "missing or blank")
    parsed, record = _occ_fields(symbol.strip())
    strike = to_decimal(contract.get("strike_price"), "strike_price")
    expiration = parse_iso_date(contract.get("expiration_date"), "expiration_date")
    right = to_str(contract.get("type"), "type")
    if right is not None and right not in ("call", "put"):
        raise MarketTypesError("type", "%r is not call or put" % (right,))
    if ((strike is not None and strike != parsed.strike) or (expiration is not None and expiration != parsed.expiration)
            or (right is not None and right != record["option_type"])):
        raise MarketTypesError("option_contracts", "contract %s: strike, expiration or type disagree with its OCC "
                                                   "symbol" % parsed.compact)
    size = to_int(contract.get("size"), "size")
    multiplier = to_int(contract.get("multiplier"), "multiplier")
    if size is not None and multiplier is not None and size != multiplier:
        contract_size: Optional[int] = None  # a non-standard deliverable: never priced as standard
    else:
        contract_size = size if size is not None else multiplier
    underlying = to_str(contract.get("underlying_symbol"), "underlying_symbol")
    root = to_str(contract.get("root_symbol"), "root_symbol")
    record.update({
        "description": to_str(contract.get("name"), "name"),
        "underlying": underlying.strip().upper() if underlying else record["underlying"],
        "root_symbol": root.strip().upper() if root else record["root_symbol"],
        "open_interest": to_int(contract.get("open_interest"), "open_interest"),
        "contract_size": contract_size,
    })
    if strike is not None:
        record["strike"] = _plain_decimal_text(strike)
    return parsed.compact, record


def _snapshot_fields(symbol: str, snapshot: Any) -> Dict[str, Any]:
    where = "snapshots.%s" % symbol
    snapshot = _entry(snapshot, where)
    fields: Dict[str, Any] = {}
    latest_quote = snapshot.get("latestQuote")
    if latest_quote is not None:
        fields.update(_quote_fields(_entry(latest_quote, where + ".latestQuote"), where + ".latestQuote"))
    latest_trade = snapshot.get("latestTrade")
    if latest_trade is not None:
        trade = _entry(latest_trade, where + ".latestTrade")
        fields.update({"last": trade.get("p"), "last_volume": trade.get("s"),
                       "trade_date": _stamp_ms(trade.get("t"), where + ".latestTrade.t")})
    return fields


def _parse_quote_map(document: Any, builder: Callable[[str, Any], Dict[str, Any]]) -> Dict[str, Quote]:
    quotes: Dict[str, Quote] = {}
    for symbol, item in _object_field(document, "quotes").items():
        record = builder(symbol, item)
        quote = Quote.from_record(record)
        quotes[quote.symbol] = quote
    return quotes


def _parse_stock_quotes(document: Any) -> Dict[str, Quote]:
    return _parse_quote_map(document, _stock_record)


def _parse_option_quotes(document: Any) -> Dict[str, Quote]:
    return _parse_quote_map(document, _option_record)


def _parse_contracts_page(document: Any) -> List[Dict[str, Any]]:
    return [_entry(item, "option_contracts") for item in _list_field(document, "option_contracts")]


def _parse_snapshots_page(document: Any) -> Dict[str, Any]:
    return dict(_object_field(document, "snapshots"))


def _bars_page_parser(symbol: str) -> Callable[[Any], List[Dict[str, Any]]]:
    def parse(document: Any) -> List[Dict[str, Any]]:
        bars = _object_field(document, "bars")
        items = bars.get(symbol)
        if items is None:
            return []
        if not isinstance(items, list):
            raise MarketTypesError("bars.%s" % symbol, "expected a list, got %s" % type(items).__name__)
        return [_entry(item, "bars.%s" % symbol) for item in items]
    return parse


def _et_date(value: Any, field_name: str) -> datetime.date:
    instant = from_rfc3339(value, field_name)
    if instant is None:
        raise MarketTypesError(field_name, "missing")
    try:
        return market_clock.to_et(instant).date()
    except ValueError as exc:
        raise MarketTypesError(field_name, str(exc)) from None


def _bar_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    return {"date": _et_date(raw.get("t"), "bars.t").isoformat(), "open": raw.get("o"), "high": raw.get("h"),
            "low": raw.get("l"), "close": raw.get("c"), "volume": raw.get("v")}


def _minute_bar_record(raw: Dict[str, Any]) -> Dict[str, Any]:
    start = from_rfc3339(raw.get("t"), "bars.t")
    if start is None:
        raise MarketTypesError("bars.t", "missing")
    return {"t": _rfc3339_utc(start), "close": raw.get("c"), "volume": raw.get("v")}


def _rfc3339_utc(moment: datetime.datetime) -> str:
    """``YYYY-MM-DDTHH:MM:SSZ`` of an aware instant (whole seconds: Alpaca's bars are stamped on the minute)."""
    if moment.tzinfo is None:
        raise ValueError("expected an aware datetime, got %r" % (moment,))
    return moment.astimezone(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _et_hhmm(value: Any, field_name: str) -> Optional[str]:
    instant = from_rfc3339(value, field_name)
    if instant is None:
        return None
    try:
        return market_clock.to_et(instant).strftime("%H:%M")
    except ValueError as exc:
        raise MarketTypesError(field_name, str(exc)) from None


def _clock_record(document: Any) -> Dict[str, Any]:
    if not isinstance(document, dict):
        raise MarketTypesError("clock", "the response is not a JSON object")
    is_open = document.get("is_open")
    if not isinstance(is_open, bool):
        raise MarketTypesError("is_open", "expected true or false, got %r" % (is_open,))
    instant = from_rfc3339(document.get("timestamp"), "timestamp")
    if instant is None:
        raise MarketTypesError("timestamp", "missing")
    try:
        day = market_clock.to_et(instant).date()
    except ValueError as exc:
        raise MarketTypesError("timestamp", str(exc)) from None
    epoch_s = (instant - datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc)) // datetime.timedelta(seconds=1)
    if is_open:
        next_change, next_state = _et_hhmm(document.get("next_close"), "next_close"), "closed"
    else:
        next_change, next_state = _et_hhmm(document.get("next_open"), "next_open"), "open"
    return {"date": day.isoformat(), "state": "open" if is_open else "closed", "description": None,
            "timestamp": epoch_s, "next_change": next_change, "next_state": next_state}


def _parse_clock(document: Any) -> ClockInfo:
    return ClockInfo.from_record(_clock_record(document))


def _session_hhmm(value: Any, field_name: str) -> Optional[str]:
    """``"0400"`` or ``"04:00"`` -> ``"04:00"`` (validated); ``None`` for absent."""
    text = to_str(value, field_name)
    if text is None or not text.strip():
        return None
    match = _HHMM_COMPACT_RE.match(text.strip())
    hhmm = "%s:%s" % (match.group(1), match.group(2)) if match else text.strip()
    parse_hhmm(hhmm, field_name)
    return hhmm


def _window(start: Optional[str], end: Optional[str]) -> Optional[Dict[str, str]]:
    if start is None or end is None:
        return None
    return {"start": start, "end": end}


def _calendar_parser(month: int, year: int) -> Callable[[Any], List[CalendarDay]]:
    def parse(document: Any) -> List[CalendarDay]:
        if not isinstance(document, list):
            raise MarketTypesError("calendar", "expected a JSON list of trading days")
        listed: Dict[datetime.date, Dict[str, Any]] = {}
        for item in document:
            entry = _entry(item, "calendar")
            day = parse_iso_date(entry.get("date"), "date")
            if day is None:
                raise MarketTypesError("date", "missing")
            if (day.year, day.month) != (year, month):
                raise MarketTypesError("date", "%s is outside the requested month %04d-%02d" % (day, year, month))
            session_open = _session_hhmm(entry.get("session_open"), "session_open")
            regular_open = _session_hhmm(entry.get("open"), "open")
            regular_close = _session_hhmm(entry.get("close"), "close")
            session_close = _session_hhmm(entry.get("session_close"), "session_close")
            if regular_open is None or regular_close is None:
                raise MarketTypesError("open", "a trading day needs open and close")
            listed[day] = {"date": day.isoformat(), "status": "open",
                           "premarket": _window(session_open, regular_open),
                           "open": _window(regular_open, regular_close),
                           "postmarket": _window(regular_close, session_close)}
        days: List[CalendarDay] = []
        for number in range(1, calendar_module.monthrange(year, month)[1] + 1):
            day = datetime.date(year, month, number)
            record = listed.get(day, {"date": day.isoformat(), "status": "closed"})
            days.append(CalendarDay.from_record(record))
        return days
    return parse


def _decode_json(body: bytes, *, allow_list: bool = False) -> Any:
    """UTF-8 JSON object (or list, where allowed); ``ValueError`` (with a body-free reason) otherwise."""
    text = body.decode("utf-8")
    document = json.loads(text, parse_constant=_reject_constant)
    if isinstance(document, list) and allow_list:
        return document
    if not isinstance(document, dict):
        raise ValueError("top-level JSON is not a JSON object")
    return document


def _reject_constant(name: str) -> Any:
    raise ValueError("non-finite number %s" % name)


def _header_int(headers: Dict[str, str], name: str) -> Optional[int]:
    value = headers.get(name)
    if value is None:
        return None
    try:
        return int(value.strip())
    except ValueError:
        return None


def _classify(status: int, body_text: str) -> Optional[str]:
    """The error status of an answer (``None`` for 200)."""
    if status == 200:
        return None
    if 300 <= status < 400:
        return "refused_redirect"
    if SUBSCRIPTION_MARKER in body_text.lower():
        return "subscription"
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "quota"
    if status in (400, 422):
        return "bad_request"
    return "http"


def _iso_date(value: Any, name: str) -> str:
    if isinstance(value, datetime.datetime) or not isinstance(value, datetime.date):
        raise ValueError("%s must be a datetime.date, got %s" % (name, type(value).__name__))
    return value.isoformat()


def _validate_symbol(raw: Any) -> str:
    if not isinstance(raw, str):
        raise AlpacaError("bad_symbol", "symbol must be text, got %s" % type(raw).__name__)
    text = raw.strip()
    if not SYMBOL_RE.fullmatch(text):
        raise AlpacaError("bad_symbol", "symbol %r does not match %s" % (raw, SYMBOL_RE.pattern))
    return text.upper()


def _option_symbol(symbol: str) -> Optional[str]:
    """The compact OCC form when ``symbol`` is an option symbol, else ``None``."""
    parsed, _error = occ.try_parse(symbol)
    return None if parsed is None else parsed.compact


# --------------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------------


class AlpacaClient:
    """GET-only client for the allow-listed Alpaca paper endpoints (module notes).

    ``host_kind`` is ``"paper"`` (HTTPS to the two pinned hosts) or ``"loopback:<port>"`` (plain
    HTTP to 127.0.0.1 for both, for the fake-server tests). ``credential`` may be ``None`` only
    with an :class:`OfflineTransport`. ``options_feed`` is one of :data:`OPTIONS_FEEDS`,
    ``stock_feed`` one of :data:`STOCK_FEEDS`. The constructor performs no I/O. ``on_request``
    receives one :class:`RequestReceipt` per request handed to the transport.
    """

    __slots__ = ("_host_kind", "_scheme", "_data_netloc", "_paper_netloc", "_credential", "_options_feed",
                 "_stock_feed", "_timeout_s", "_user_agent", "_transport", "_on_request")

    def __init__(self, host_kind: str, credential: Optional[Credential], *,
                 options_feed: str = DEFAULT_OPTIONS_FEED, stock_feed: str = DEFAULT_STOCK_FEED,
                 timeout_s: float = DEFAULT_TIMEOUT_S, user_agent: str, transport: Optional[Transport] = None,
                 on_request: Optional[Callable[[RequestReceipt], None]] = None) -> None:
        scheme, data_netloc, paper_netloc = _resolve_host(host_kind)
        if credential is not None and not isinstance(credential, Credential):
            raise TypeError("credential must be a Credential (never raw key text), got %s" % type(credential).__name__)
        if options_feed not in OPTIONS_FEEDS:
            raise ValueError("options_feed must be one of %s, got %r" % (", ".join(OPTIONS_FEEDS), options_feed))
        if stock_feed not in STOCK_FEEDS:
            raise ValueError("stock_feed must be one of %s, got %r" % (", ".join(STOCK_FEEDS), stock_feed))
        if transport is None:
            transport = UrllibTransport(scheme)
        if credential is None and not isinstance(transport, OfflineTransport):
            raise AlpacaError("no_credential", "a paper key pair is required for the %s host; only an "
                                               "OfflineTransport runs without one" % host_kind)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not timeout_s > 0:
            raise ValueError("timeout_s must be a positive number of seconds, got %r" % (timeout_s,))
        if not isinstance(user_agent, str) or not user_agent.strip() or not _visible_ascii(user_agent, allow_space=True):
            raise ValueError("user_agent must be non-blank printable ASCII, got %r" % (user_agent,))
        if on_request is not None and not callable(on_request):
            raise TypeError("on_request must be callable, got %s" % type(on_request).__name__)
        self._host_kind = host_kind
        self._scheme = scheme
        self._data_netloc = data_netloc
        self._paper_netloc = paper_netloc
        self._credential = credential
        self._options_feed = options_feed
        self._stock_feed = stock_feed
        self._timeout_s = float(timeout_s)
        self._user_agent = user_agent
        self._transport = transport
        self._on_request = on_request

    def __repr__(self) -> str:
        return "AlpacaClient(host_kind=%r, data_host=%r, paper_host=%r, credential=%r, transport=%s)" % (
            self._host_kind, self._data_netloc, self._paper_netloc, self._credential, type(self._transport).__name__)

    # -- read-only configuration ----------------------------------------------------------
    @property
    def host_kind(self) -> str:
        return self._host_kind

    @property
    def data_host(self) -> str:
        """The pinned market-data ``netloc``."""
        return self._data_netloc

    @property
    def paper_host(self) -> str:
        """The pinned paper-API ``netloc`` (clock, calendar and contract listing only)."""
        return self._paper_netloc

    @property
    def scheme(self) -> str:
        return self._scheme

    @property
    def credential(self) -> Optional[Credential]:
        return self._credential

    @property
    def options_feed(self) -> str:
        return self._options_feed

    @property
    def stock_feed(self) -> str:
        return self._stock_feed

    @property
    def timeout_s(self) -> float:
        return self._timeout_s

    @property
    def user_agent(self) -> str:
        return self._user_agent

    @property
    def transport(self) -> Transport:
        return self._transport

    # -- request building ---------------------------------------------------------------------
    def _netloc_for(self, path: str) -> str:
        return self._paper_netloc if host_for_path(path) == PAPER_HOST else self._data_netloc

    def url_for(self, path: str, params: Dict[str, str]) -> str:
        """The URL of an allow-listed path with ``params`` url-encoded, re-checked against the pin."""
        netloc = self._netloc_for(path)
        for key, value in params.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("query parameters must be text, got %r=%r" % (key, value))
        query = urllib.parse.urlencode(list(params.items()))
        url = "%s://%s%s" % (self._scheme, netloc, path)
        if query:
            url = url + "?" + query
        parts = urllib.parse.urlsplit(url)
        if (parts.scheme != self._scheme or parts.netloc != netloc or parts.path != path
                or _path_template(parts.path) is None):
            raise AlpacaError("refused_host", "built URL does not point at the pinned host and an "
                                              "allow-listed path", path=path)
        return url

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self._credential is not None:
            headers.update(self._credential.auth_headers())
        headers["Accept"] = HEADER_ACCEPT
        headers["User-Agent"] = self._user_agent
        return headers

    # -- hygiene --------------------------------------------------------------------------------
    def _secret_values(self) -> List[str]:
        if self._credential is None:
            return []
        values = list(self._credential.auth_headers().values())
        return sorted(values, key=len, reverse=True)  # the longer first, should one contain the other

    def _redact(self, text: str) -> str:
        for value in self._secret_values():
            text = text.replace(value, REDACTED)
        return text

    def _clean(self, text: str) -> str:
        """Redacted, printable and bounded: the only way vendor text reaches a message."""
        return _printable_excerpt(self._redact(text))

    def _echoes_credential(self, body: bytes, document: Any) -> bool:
        """True when a 200 body carries the key ID or the secret, raw or inside a decoded JSON
        string (module notes: such an answer is refused so the cache can never store it)."""
        text = _bytes_text(body)
        return any(value in text or _json_mentions(document, value) for value in self._secret_values())

    def _http_message(self, status: int, body: bytes) -> str:
        """``HTTP <status>: <excerpt>`` of a non-200 body. Raw redaction cannot see a value written as JSON ``\\u``
        escapes, so a body whose decoded JSON carries the key ID or the secret is withheld whole."""
        try:
            document: Any = json.loads(_bytes_text(body))
        except ValueError:
            document = None
        if document is not None and any(_json_mentions(document, value) for value in self._secret_values()):
            return "HTTP %d: %s" % (status, BODY_WITHHELD)
        excerpt = self._clean(_bytes_text(body))
        if excerpt:
            return "HTTP %d: %s" % (status, excerpt)
        return "HTTP %d (empty body)" % status

    def _notify(self, receipt: RequestReceipt) -> None:
        if self._on_request is not None:
            self._on_request(receipt)

    @staticmethod
    def _ratelimit(headers: Dict[str, str]) -> Tuple[Optional[int], Optional[int], Optional[int],
                                                      Optional[datetime.datetime]]:
        allowed = _header_int(headers, "x-ratelimit-limit")
        available = _header_int(headers, "x-ratelimit-remaining")
        used = allowed - available if allowed is not None and available is not None else None
        reset = _header_int(headers, "x-ratelimit-reset")
        expiry: Optional[datetime.datetime] = None
        if reset is not None:
            try:
                expiry = from_epoch_s(reset, "x-ratelimit-reset")
            except MarketTypesError:
                expiry = None
        return allowed, used, available, expiry

    # -- the one exchange -----------------------------------------------------------------
    def _exchange(self, path: str, params: Dict[str, str], symbols_count: int, *,
                  allow_list: bool = False) -> Tuple[Any, RequestReceipt]:
        """GET ``path`` once; returns ``(document, receipt)`` on a decoded 200 WITHOUT notifying
        (the caller notifies once it knows the final status); notifies and raises otherwise."""
        url = self.url_for(path, params)
        headers = self._headers()
        try:
            response = self._transport.fetch(url, headers, self._timeout_s)
        except TransportError as exc:
            self._notify(RequestReceipt(path, symbols_count, exc.receipt_utc, exc.status,
                                        http_status=None, elapsed_ms=exc.elapsed_ms))
            raise AlpacaError(exc.status, self._redact(exc.message), path=path) from exc
        allowed, used, available, expiry = self._ratelimit(response.headers)
        receipt = RequestReceipt(
            path=path,
            symbols_count=symbols_count,
            receipt_utc=response.receipt_utc,
            status=STATUS_OK,
            http_status=response.status,
            ratelimit_allowed=allowed,
            ratelimit_used=used,
            ratelimit_available=available,
            ratelimit_expiry_utc=expiry,
            elapsed_ms=response.elapsed_ms,
        )
        failure = _classify(response.status, _bytes_text(response.body))
        if failure is not None:
            self._notify(replace(receipt, status=failure))
            if failure == "refused_redirect":
                message = "HTTP %d redirect refused: the client follows no redirects" % response.status
            else:
                message = self._http_message(response.status, response.body)
            raise AlpacaError(failure, message, http_status=response.status, path=path)
        try:
            document = _decode_json(response.body, allow_list=allow_list)
        except ValueError as exc:  # UnicodeDecodeError and JSONDecodeError included
            self._notify(replace(receipt, status="decode"))
            raise AlpacaError(
                "decode", "HTTP 200 body is not JSON (%s): %s" % (
                    self._clean(str(exc)), self._clean(_bytes_text(response.body)) or "(empty body)"),
                http_status=response.status, path=path) from exc
        if self._echoes_credential(response.body, document):
            self._notify(replace(receipt, status="decode"))
            raise AlpacaError("decode", "HTTP 200 body echoes the credential; the answer is refused so that "
                                        "nothing downstream (cache, ledger, output) can carry it",
                              http_status=response.status, path=path)
        return document, receipt

    def get_json(self, path: str, params: Optional[Dict[str, str]] = None, *,
                 symbols_count: int = 0) -> Tuple[Any, RequestReceipt]:
        """One allow-listed GET returning the decoded JSON object and its receipt (notified)."""
        document, receipt = self._exchange(path, dict(params or {}), symbols_count)
        self._notify(receipt)
        return document, receipt

    def _complete(self, path: str, params: Dict[str, str], symbols_count: int, parse: Callable[[Any], Any], *,
                  allow_list: bool = False) -> Tuple[Any, RequestReceipt, Any]:
        """Exchange, convert with ``parse`` and record exactly one receipt with the final status."""
        document, receipt = self._exchange(path, params, symbols_count, allow_list=allow_list)
        try:
            value = parse(document)
            if isinstance(document, dict):
                token = document.get("next_page_token")
                if token is not None and not isinstance(token, str):
                    raise MarketTypesError("next_page_token", "expected text or null, got %s" % type(token).__name__)
        except MarketTypesError as exc:
            self._notify(replace(receipt, status="decode"))
            raise AlpacaError("decode", self._clean("%s: %s" % (exc.field, exc.message)),
                              http_status=receipt.http_status, path=path) from exc
        self._notify(receipt)
        return value, receipt, document

    def _paged(self, path: str, params: Dict[str, str], symbols_count: int,
               parse: Callable[[Any], Any]) -> Tuple[List[Any], List[RequestReceipt], List[Any]]:
        """Follow ``next_page_token`` up to :data:`MAX_PAGES` pages (each its own request and receipt)."""
        values: List[Any] = []
        receipts: List[RequestReceipt] = []
        documents: List[Any] = []
        seen: List[str] = []
        page_params = dict(params)
        while True:
            value, receipt, document = self._complete(path, page_params, symbols_count, parse)
            values.append(value)
            receipts.append(receipt)
            documents.append(document)
            token = document.get("next_page_token") if isinstance(document, dict) else None
            if not token:
                return values, receipts, documents
            if token in seen:
                raise AlpacaError("decode", "next_page_token repeats an earlier page; the answer is refused",
                                  http_status=receipt.http_status, path=path)
            if len(receipts) >= MAX_PAGES:
                raise AlpacaError("incomplete", "the answer has more than %d pages; nothing partial is returned"
                                  % MAX_PAGES, http_status=receipt.http_status, path=path)
            seen.append(token)
            page_params = dict(params)
            page_params["page_token"] = token

    # -- endpoints ----------------------------------------------------------------------------
    @staticmethod
    def _normalise_symbols(symbols: Iterable[str]) -> List[str]:
        if isinstance(symbols, str):
            symbols = [symbols]  # a lone string is one symbol, not its characters
        ordered: List[str] = []
        for raw in symbols:
            symbol = _validate_symbol(raw)
            compact = _option_symbol(symbol)
            if compact is not None:
                symbol = compact
            if symbol not in ordered:
                ordered.append(symbol)
        if not ordered:
            raise ValueError("at least one symbol is required")
        return ordered

    def quotes(self, symbols: Iterable[str]) -> QuotesResult:
        """Latest quotes for ``symbols`` (validated, upper-cased, OCC symbols compacted,
        de-duplicated): equities first, then options, each in consecutive batches of
        :data:`MAX_BATCH`. A failing batch raises after its receipt is recorded. A symbol the
        vendor did not quote is listed in ``not_returned``."""
        requested = self._normalise_symbols(symbols)
        stocks = [symbol for symbol in requested if _option_symbol(symbol) is None]
        options = [symbol for symbol in requested if _option_symbol(symbol) is not None]
        quotes: Dict[str, Quote] = {}
        receipts: List[RequestReceipt] = []
        for path, group, feed, parse in ((PATH_STOCK_QUOTES, stocks, self._stock_feed, _parse_stock_quotes),
                                         (PATH_OPTION_QUOTES, options, self._options_feed, _parse_option_quotes)):
            for start in range(0, len(group), MAX_BATCH):
                batch = group[start:start + MAX_BATCH]
                params: Dict[str, str] = {"symbols": ",".join(batch), "feed": feed}
                batch_quotes, receipt, _document = self._complete(path, params, len(batch), parse)
                quotes.update(batch_quotes)
                receipts.append(receipt)
        quoted = {symbol.upper() for symbol in quotes}
        not_returned = tuple(symbol for symbol in requested if symbol not in quoted)
        return QuotesResult(quotes, [], receipts[-1], tuple(receipts), not_returned)

    def _contracts(self, params: Dict[str, str]) -> Tuple[List[Dict[str, Any]], List[RequestReceipt]]:
        pages, receipts, _documents = self._paged(PATH_OPTION_CONTRACTS, params, 1, _parse_contracts_page)
        contracts: List[Dict[str, Any]] = []
        for page in pages:
            contracts.extend(page)
        return contracts, receipts

    def option_chain(self, symbol: str, expiration: datetime.date) -> ChainResult:
        """One expiration's chain: the contract listing, then the snapshots (module notes)."""
        underlying = _validate_symbol(symbol)
        path = snapshots_path(underlying)
        day = _iso_date(expiration, "expiration")
        contracts, receipts = self._contracts({"underlying_symbols": underlying, "expiration_date": day,
                                               "status": "active", "limit": str(CONTRACTS_PAGE_LIMIT)})
        pages, snapshot_receipts, _documents = self._paged(
            path, {"feed": self._options_feed, "expiration_date": day, "limit": str(SNAPSHOTS_PAGE_LIMIT)}, 1,
            _parse_snapshots_page)
        receipts.extend(snapshot_receipts)
        snapshots: Dict[str, Any] = {}
        for page in pages:
            snapshots.update(page)
        try:
            records: Dict[str, Dict[str, Any]] = {}
            for contract in contracts:
                compact, record = _contract_fields(contract)
                records[compact] = record
            for raw_symbol, snapshot in snapshots.items():
                parsed, base = _occ_fields(str(raw_symbol))
                record = records.setdefault(parsed.compact, base)
                record.update(_snapshot_fields(parsed.compact, snapshot))
            options = []
            for compact in sorted(records):
                quote = Quote.from_record(records[compact])
                if quote.expiration_date != expiration:
                    raise MarketTypesError("expiration_date", "%s does not expire on %s" % (compact, day))
                options.append(quote)
        except MarketTypesError as exc:
            # Every request was already recorded ok; the merged answer is refused as a whole.
            raise AlpacaError("decode", self._clean("%s: %s" % (exc.field, exc.message)),
                              http_status=receipts[-1].http_status, path=path) from exc
        raw = {"option_contracts": contracts, "snapshots": snapshots}
        return ChainResult(options, receipts[-1], raw, tuple(receipts))

    def option_expirations(self, symbol: str, start: datetime.date, *,
                           window_days: int = EXPIRATIONS_WINDOW_DAYS) -> ExpirationsResult:
        """The distinct expirations of ``symbol``'s active contracts from ``start`` (the desk's ET
        date) through ``start + window_days``, sorted (module notes)."""
        underlying = _validate_symbol(symbol)
        first = _iso_date(start, "start")
        if isinstance(window_days, bool) or not isinstance(window_days, int) or not 1 <= window_days <= 1100:
            raise ValueError("window_days must be an integer 1-1100, got %r" % (window_days,))
        last = (start + datetime.timedelta(days=window_days)).isoformat()
        contracts, receipts = self._contracts({"underlying_symbols": underlying, "status": "active", "type": "call",
                                               "expiration_date_gte": first, "expiration_date_lte": last,
                                               "limit": str(CONTRACTS_PAGE_LIMIT)})
        dates = set()
        try:
            for contract in contracts:
                day = parse_iso_date(contract.get("expiration_date"), "expiration_date")
                if day is None:
                    raise MarketTypesError("expiration_date", "blank expiration date")
                dates.add(day)
        except MarketTypesError as exc:
            raise AlpacaError("decode", self._clean("%s: %s" % (exc.field, exc.message)),
                              http_status=receipts[-1].http_status, path=PATH_OPTION_CONTRACTS) from exc
        return ExpirationsResult(sorted(dates), receipts[-1], {"option_contracts": contracts}, tuple(receipts))

    def option_strikes(self, symbol: str, expiration: datetime.date) -> StrikesResult:
        """The distinct strikes of one expiration's contract listing, as ``Decimal``, ascending."""
        underlying = _validate_symbol(symbol)
        day = _iso_date(expiration, "expiration")
        contracts, receipts = self._contracts({"underlying_symbols": underlying, "expiration_date": day,
                                               "status": "active", "limit": str(CONTRACTS_PAGE_LIMIT)})
        strikes = set()
        try:
            for contract in contracts:
                strike = to_decimal(contract.get("strike_price"), "strike_price")
                if strike is None:
                    raise MarketTypesError("strike_price", "blank strike")
                strikes.add(strike)
        except MarketTypesError as exc:
            raise AlpacaError("decode", self._clean("%s: %s" % (exc.field, exc.message)),
                              http_status=receipts[-1].http_status, path=PATH_OPTION_CONTRACTS) from exc
        return StrikesResult(sorted(strikes), receipts[-1], {"option_contracts": contracts}, tuple(receipts))

    def history(self, symbol: str, start: datetime.date, end: datetime.date, *,
                interval: str = "daily") -> HistoryResult:
        """Daily (or weekly/monthly) bars from ``start`` to ``end``, raw (unadjusted), on the stock feed."""
        if interval not in HISTORY_INTERVALS:
            raise ValueError("interval must be one of %s, got %r" % (", ".join(HISTORY_INTERVALS), interval))
        start_text = _iso_date(start, "start")
        end_text = _iso_date(end, "end")
        if end < start:
            raise ValueError("end %s is before start %s" % (end_text, start_text))
        ticker = _validate_symbol(symbol)
        params: Dict[str, str] = {"symbols": ticker, "timeframe": HISTORY_TIMEFRAMES[interval], "start": start_text,
                                  "end": end_text, "adjustment": "raw", "feed": self._stock_feed,
                                  "limit": str(BARS_PAGE_LIMIT)}
        pages, receipts, documents = self._paged(PATH_STOCK_BARS, params, 1, _bars_page_parser(ticker))
        try:
            bars = [Bar.from_record(_bar_record(item)) for page in pages for item in page]
        except MarketTypesError as exc:
            raise AlpacaError("decode", self._clean("%s: %s" % (exc.field, exc.message)),
                              http_status=receipts[-1].http_status, path=PATH_STOCK_BARS) from exc
        return HistoryResult(bars, receipts[-1], {"pages": documents}, tuple(receipts))

    def minute_bars(self, symbol: str, start: datetime.datetime, end: datetime.datetime) -> MinuteBarsResult:
        """One-minute bars of ``symbol`` from ``start`` to ``end`` (aware instants), raw, on the stock feed."""
        start_text = _rfc3339_utc(start)
        end_text = _rfc3339_utc(end)
        if end < start:
            raise ValueError("end %s is before start %s" % (end_text, start_text))
        ticker = _validate_symbol(symbol)
        params: Dict[str, str] = {"symbols": ticker, "timeframe": MINUTE_TIMEFRAME, "start": start_text,
                                  "end": end_text, "adjustment": "raw", "feed": self._stock_feed,
                                  "limit": str(BARS_PAGE_LIMIT)}
        pages, receipts, documents = self._paged(PATH_STOCK_BARS, params, 1, _bars_page_parser(ticker))
        try:
            bars = [MinuteBar.from_record(_minute_bar_record(item)) for page in pages for item in page]
        except MarketTypesError as exc:
            raise AlpacaError("decode", self._clean("%s: %s" % (exc.field, exc.message)),
                              http_status=receipts[-1].http_status, path=PATH_STOCK_BARS) from exc
        return MinuteBarsResult(bars, receipts[-1], {"pages": documents}, tuple(receipts))

    def clock(self) -> ClockResult:
        """The market clock on the paper host (module notes)."""
        info, receipt, document = self._complete(PATH_CLOCK, {}, 0, _parse_clock)
        return ClockResult(info.date, info.state, info.description, info.timestamp_utc,
                           info.next_change, info.next_state, receipt, document)

    def calendar(self, month: int, year: int) -> CalendarResult:
        """One month of the trading calendar (``month`` 1-12, ``year`` four digits); module notes."""
        if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
            raise ValueError("month must be an integer 1-12, got %r" % (month,))
        if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 9999:
            raise ValueError("year must be a four-digit integer, got %r" % (year,))
        last = calendar_module.monthrange(year, month)[1]
        params: Dict[str, str] = {"start": "%04d-%02d-01" % (year, month), "end": "%04d-%02d-%02d" % (year, month, last)}
        days, receipt, document = self._complete(PATH_CALENDAR, params, 0, _calendar_parser(month, year),
                                                 allow_list=True)
        return CalendarResult(days, month, year, receipt, document)


# --------------------------------------------------------------------------------------------
# Credential loading
# --------------------------------------------------------------------------------------------


def _env_value(name: str) -> Optional[str]:
    if not name:
        return None
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return value


def load_credential(key_env: str, secret_env: str, credentials_file: Optional[str]) -> Credential:
    """The paper key pair: both environment variables (stripped; empty counts as absent), else
    the first two lines of ``credentials_file`` (key ID, then secret; a regular file with no
    group/other permission bits, else ``credential_file_mode``). One variable set without the
    other is ``no_credential`` (never half from each source); nothing usable is ``no_credential``.
    No message ever repeats a value."""
    key_value = _env_value(key_env)
    secret_value = _env_value(secret_env)
    if key_value is not None or secret_value is not None:
        if key_value is None or secret_value is None:
            present, missing = (key_env, secret_env) if key_value is not None else (secret_env, key_env)
            raise AlpacaError("no_credential", "environment variable %s is set but %s is unset or empty: set both, "
                                               "or neither and use the credentials file" % (present, missing))
        try:
            return Credential(key_value, secret_value, "env")
        except (ValueError, TypeError):
            raise AlpacaError("no_credential", "environment variables %s and %s do not hold a usable key pair (each "
                                               "must be visible ASCII without spaces, and the two must differ)"
                              % (key_env, secret_env)) from None
    if credentials_file:
        try:
            info = os.stat(credentials_file)
        except FileNotFoundError:
            info = None
        except OSError as exc:
            raise AlpacaError("no_credential", "credentials file %s cannot be read: %s"
                              % (credentials_file, exc.strerror)) from None
        if info is not None:
            mode = stat.S_IMODE(info.st_mode)
            if not stat.S_ISREG(info.st_mode):
                raise AlpacaError("credential_file_mode", "credentials file %s is not a regular file" % credentials_file)
            if mode & _GROUP_OTHER_BITS:
                raise AlpacaError("credential_file_mode", "credentials file %s has mode %04o; it must have no group "
                                  "or other permission bits (chmod 600)" % (credentials_file, mode))
            try:
                with open(credentials_file, "rb") as handle:
                    first_line = handle.readline()
                    second_line = handle.readline()
                key_text = first_line.decode("utf-8").strip()
                secret_text = second_line.decode("utf-8").strip()
            except OSError as exc:
                raise AlpacaError("no_credential", "credentials file %s cannot be read: %s"
                                  % (credentials_file, exc.strerror)) from None
            except UnicodeDecodeError:
                key_text, secret_text = "", ""
            if key_text or secret_text:
                try:
                    return Credential(key_text, secret_text, "file")
                except (ValueError, TypeError):
                    raise AlpacaError("no_credential", "credentials file %s does not hold a usable key pair (the key "
                                      "ID on line 1 and the secret on line 2, each visible ASCII without spaces)"
                                      % credentials_file) from None
    where = []
    if key_env or secret_env:
        where.append("environment variables %s and %s are unset or empty" % (key_env, secret_env))
    if credentials_file:
        where.append("credentials file %s is missing or empty" % credentials_file)
    raise AlpacaError("no_credential", "no market-data key pair: %s" % ("; ".join(where) or "no source configured"))


__all__ = [
    "ALLOWED_PATHS",
    "AlpacaClient",
    "AlpacaError",
    "BARS_PAGE_LIMIT",
    "BODY_WITHHELD",
    "Bar",
    "CONTRACTS_PAGE_LIMIT",
    "CREDENTIAL_SOURCES",
    "CalendarResult",
    "ChainResult",
    "ClockResult",
    "Credential",
    "DATA_HOST",
    "DATA_PATHS",
    "DEFAULT_FIXTURE_SET",
    "DEFAULT_OPTIONS_FEED",
    "DEFAULT_STOCK_FEED",
    "DEFAULT_TIMEOUT_S",
    "EXPIRATIONS_WINDOW_DAYS",
    "ExpirationsResult",
    "HEADER_ACCEPT",
    "HEADER_KEY_ID",
    "HEADER_SECRET",
    "HISTORY_INTERVALS",
    "HISTORY_TIMEFRAMES",
    "HOST_KINDS",
    "HOST_KIND_PAPER",
    "HistoryResult",
    "LIVE_HOST",
    "LIVE_HOST_KIND",
    "LOOPBACK_HOST",
    "LOOPBACK_PREFIX",
    "MAX_BATCH",
    "MAX_ERROR_BODY_BYTES",
    "MAX_MESSAGE_CHARS",
    "MAX_PAGES",
    "MAX_RESPONSE_BYTES",
    "MINUTE_TIMEFRAME",
    "MinuteBar",
    "MinuteBarsResult",
    "OPTIONS_FEEDS",
    "OPTIONS_FEED_INDICATIVE",
    "OfflineTransport",
    "PAPER_HOST",
    "PAPER_PATHS",
    "PATH_CALENDAR",
    "PATH_CLOCK",
    "PATH_OPTION_CONTRACTS",
    "PATH_OPTION_QUOTES",
    "PATH_OPTION_SNAPSHOTS",
    "PATH_STOCK_BARS",
    "PATH_STOCK_QUOTES",
    "Quote",
    "QuotesResult",
    "RATE_LIMIT_PER_MIN",
    "REDACTED",
    "RefuseRedirects",
    "RequestReceipt",
    "SNAPSHOTS_PAGE_LIMIT",
    "SNAPSHOTS_PREFIX",
    "STATUSES",
    "STATUS_OK",
    "STOCK_FEEDS",
    "SUBSCRIPTION_MARKER",
    "SYMBOL_RE",
    "StrikesResult",
    "Transport",
    "TransportError",
    "TransportResponse",
    "UNDERLYING_RE",
    "UrllibTransport",
    "fixture_file_name",
    "host_for_path",
    "load_credential",
    "snapshots_path",
]
