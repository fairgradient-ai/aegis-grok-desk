"""GET-only Tradier market-data client: the desk's single network module (plan sections 3, 9, 10).

This module is the only file in the repository that may import ``urllib.request``,
``http.client``, ``socket`` and ``ssl`` (``scripts/check_no_write_paths.py`` proves it by AST),
and it is the first layer of the no-trade guarantee: every request it can make is a ``GET`` to
one of seven allow-listed read-only market-data paths on one pinned host. The proof is in the
shape of the code, so the shape is deliberate:

* The URL is built here from the configured host kind, an allow-listed path and
  ``urllib.parse.urlencode`` of validated parameters; nothing accepts a URL from outside, and
  :meth:`TradierClient.url_for` re-parses what it built and refuses anything whose scheme, host
  or path differs from the pin (``refused_host`` / ``refused_path``).
* ``urllib.request.Request(url, headers=..., method="GET")`` with the literal method and no
  body; the opener is an explicit :class:`urllib.request.OpenerDirector` holding exactly the
  HTTP or HTTPS handler the host kind needs, the default error handler and processor,
  :class:`RefuseRedirects` and the unknown-scheme handler: no proxy, file, FTP, data, cookie or
  authentication handlers, so the environment's proxy variables are ignored and a 3xx answer
  is an error (``refused_redirect``) instead of a second request to wherever ``Location``
  points. HTTPS uses ``ssl.create_default_context()`` (certificate and hostname verification
  on, TLS 1.2 minimum).
* The transport method is named ``fetch``, not ``send``: the gate rejects every ``.send(...)``
  call in a module that imports ``socket`` or ``http.client`` (a raw socket or connection
  write), and this module imports both to classify ``socket.timeout`` (not a ``TimeoutError``
  before Python 3.10) and ``http.client.HTTPException`` (a half-read response) as explicit
  statuses. :class:`Transport` documents the contract; :class:`UrllibTransport` is the live
  one, :class:`OfflineTransport` replays the recorded fixture sets without a socket or a token.

Statuses (:class:`TradierError`; ``str(err)`` is ``"<status>: <message>"``)
---------------------------------------------------------------------------
``no_token`` (nothing in the environment variable or the token file), ``token_file_mode`` (the
token file is not a regular file or has group/other permission bits), ``auth`` (HTTP 401/403),
``quota`` (HTTP 429, or HTTP 400 whose body says "Quota Violation" in any case, the sandbox's
rate-limit fault), ``bad_request`` (any other HTTP 400), ``http`` (any other non-200 answer),
``network`` (the connection failed: refused, reset, DNS, TLS, a half-read response),
``timeout`` (no answer within ``timeout_s``), ``decode`` (a 200 whose body is not UTF-8 JSON,
not a JSON object, or carries a field that is present but unreadable: ``bid: "abc"``, ``NaN``,
a bad date, Greeks that are not an object), ``refused_redirect`` (any 3xx), ``refused_host``
(an unknown host kind, or a built URL that does not point at the pinned host), ``refused_path``
(a path outside :data:`ALLOWED_PATHS`), ``bad_symbol`` (a symbol failing :data:`SYMBOL_RE`;
nothing is sent). No retries happen here: the cache decides.

Message hygiene (brief non-negotiable 3). An error message never carries request headers, the
URL query, the token or more than the first :data:`MAX_MESSAGE_CHARS` printable characters of a
response body; the token (and its ``Bearer`` header value) is replaced by ``[redacted]`` if it
somehow appears in a body or a reason string. A 200 whose body carries the secret, raw or inside
a decoded JSON string (a vendor echoing the ``Authorization`` header), is refused as ``decode``
before the caller sees the document, so nothing downstream (the cache's ``quotes.json``, the
ledger, a tool's output) can ever hold it. :class:`Token` keeps the secret in a slot, prints as
``Token(source=env)``, and :meth:`Token.authorization_header` is its only accessor.

Receipts. Every request handed to a transport, whether it succeeds or fails, produces exactly
one :class:`aegis_desk.market_types.RequestReceipt` passed to the ``on_request`` hook (the
cache's ledger): path, symbol count, the transport's ``receipt_utc``, the outcome status, the
HTTP status when there was an answer, the ``X-Ratelimit-*`` headers when present and the
elapsed milliseconds. A request that is never sent (``bad_symbol``, ``refused_path``) produces
no receipt. A decode failure after a 200 (an unreadable body, or a body that echoes the
credential) is one receipt with status ``decode``, so the ledger counts the request once.

Clock. The client itself never reads the wall clock. :class:`UrllibTransport` stamps each
receipt with :func:`aegis_desk.state.utc_now` (and measures elapsed time with
``time.monotonic``); :class:`OfflineTransport` stamps with its injected ``now`` (default: the
fixture set's ``clock.json`` timestamp, the vendor clock as read at the recorded fetch), so
offline runs are reproducible to the second.

Numbers. Bodies are parsed with plain ``json.loads`` (``NaN``/``Infinity`` rejected) and the
shared ``market_types`` constructors turn each JSON number into ``Decimal(str(value))``, the
shortest round-trip text of the parsed float (``648.1`` for the vendor's ``648.10``, numerically
equal); Greeks stay ``float`` and ``Quote.raw`` stays the JSON-native dict the cache stores.
Epoch milliseconds become aware UTC datetimes through ``market_types.from_epoch_ms``.

Offline fixtures. :class:`OfflineTransport` maps a request to ``<fixture_dir>/<set>/<file>`` by
endpoint and a deterministic key (:func:`fixture_file_name`): ``quotes__<SYMBOLS>.json`` with
the symbols upper-cased, sorted and comma-joined (the ``greeks`` flag is not part of the key),
``chains__<SYMBOL>__<YYYY-MM-DD>.json``, ``expirations__<SYMBOL>.json``,
``strikes__<SYMBOL>__<YYYY-MM-DD>.json``, ``history__<SYMBOL>__<START>__<END>.json``,
``clock.json`` and ``calendar__<YYYY>-<MM>.json``. A missing file is
``TradierError("network")`` with the message ``no offline fixture for <path>?<key>``.
"""

from __future__ import annotations

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
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Any, Callable, Dict, Iterable, List, Optional, Protocol, Tuple

from .market_types import (
    Bar,
    CalendarDay,
    ClockInfo,
    MarketTypesError,
    Quote,
    RequestReceipt,
    from_epoch_ms,
    one_or_many,
    parse_iso_date,
    to_decimal,
    to_int,
    to_str,
)
from .state import utc_now

# --------------------------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------------------------

SANDBOX_HOST: str = "sandbox.tradier.com"
PRODUCTION_HOST: str = "api.tradier.com"
LOOPBACK_HOST: str = "127.0.0.1"
"""Host of the ``loopback:<port>`` kind: plain HTTP for the fake-server tests only."""

HOSTS: Dict[str, str] = {"sandbox": SANDBOX_HOST, "production": PRODUCTION_HOST}
"""The two HTTPS host kinds; the tools refuse ``production`` until the market-data proxy exists."""

LOOPBACK_PREFIX: str = "loopback:"

PATH_QUOTES: str = "/v1/markets/quotes"
PATH_CHAINS: str = "/v1/markets/options/chains"
PATH_EXPIRATIONS: str = "/v1/markets/options/expirations"
PATH_STRIKES: str = "/v1/markets/options/strikes"
PATH_HISTORY: str = "/v1/markets/history"
PATH_CLOCK: str = "/v1/markets/clock"
PATH_CALENDAR: str = "/v1/markets/calendar"
ALLOWED_PATHS: Tuple[str, ...] = (
    PATH_QUOTES, PATH_CHAINS, PATH_EXPIRATIONS, PATH_STRIKES, PATH_HISTORY, PATH_CLOCK, PATH_CALENDAR,
)
"""The only paths the client can request; every one is a read-only market-data endpoint."""

RATE_LIMIT_PER_MIN: int = 60
"""Tradier's market-data budget per minute (the cache keeps headroom under it)."""

SYMBOL_RE = re.compile(r"^[A-Za-z0-9.$^:_-]{1,32}$")
"""Accepted symbol shape (equities, OCC option symbols, index roots); anything else is ``bad_symbol``."""

MAX_BATCH: int = 50
"""Symbols per ``/v1/markets/quotes`` request; longer lists are sent in consecutive batches."""

DEFAULT_TIMEOUT_S: float = 10.0
MAX_RESPONSE_BYTES: int = 16 * 1024 * 1024
"""A 200 body larger than this is ``decode`` (no unbounded buffering on the shared computer)."""

MAX_ERROR_BODY_BYTES: int = 64 * 1024
"""How much of an error body is read: only its first printable characters reach a message."""

MAX_MESSAGE_CHARS: int = 200
"""Printable characters of a body that may appear in an error message."""

REDACTED: str = "[redacted]"
STATUS_OK: str = "ok"
STATUSES: Tuple[str, ...] = (
    "no_token", "token_file_mode", "auth", "quota", "http", "network", "timeout", "decode",
    "refused_redirect", "refused_host", "refused_path", "bad_symbol", "bad_request",
)
"""Every value ``TradierError.status`` (and a failed receipt's ``status``) can take."""

TOKEN_SOURCES: Tuple[str, ...] = ("env", "file")
HISTORY_INTERVALS: Tuple[str, ...] = ("daily", "weekly", "monthly")
DEFAULT_FIXTURE_SET: str = "regular"
HEADER_ACCEPT: str = "application/json"

_FIXTURE_SET_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,40}$")
_BEARER = "Bearer "
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
# Token
# --------------------------------------------------------------------------------------------


class Token:
    """A bearer token whose secret is reachable only through :meth:`authorization_header`.

    The secret lives in a slot (no instance ``__dict__`` to dump); ``repr``/``str`` print
    ``Token(source=env)`` or ``Token(source=file)``. Programming errors (a blank secret, an
    unknown source, non-text) raise ``ValueError``/``TypeError``; the absent-credential cases are
    :func:`load_token`'s ``TradierError`` statuses.
    """

    __slots__ = ("_secret", "_source")

    def __init__(self, secret: str, source: str) -> None:
        if source not in TOKEN_SOURCES:
            raise ValueError("token source must be one of %s, got %r" % (", ".join(TOKEN_SOURCES), source))
        if not isinstance(secret, str):
            raise TypeError("token secret must be text, got %s" % type(secret).__name__)
        cleaned = secret.strip()
        if not cleaned:
            raise ValueError("token secret is blank")
        if not _visible_ascii(cleaned, allow_space=False):
            raise ValueError("token secret contains characters that cannot travel in an HTTP header")
        self._secret = cleaned
        self._source = source

    @property
    def source(self) -> str:
        """``"env"`` or ``"file"``: where :func:`load_token` found the secret."""
        return self._source

    def authorization_header(self) -> str:
        """The ``Authorization`` header value (``Bearer <secret>``); the only accessor."""
        return _BEARER + self._secret

    def __repr__(self) -> str:
        return "Token(source=%s)" % self._source

    def __str__(self) -> str:
        return self.__repr__()


# --------------------------------------------------------------------------------------------
# Errors
# --------------------------------------------------------------------------------------------


class TradierError(Exception):
    """A market-data failure with an explicit ``status`` (module notes); never a quiet negative.

    ``http_status`` is the HTTP status when the vendor answered, ``path`` the allow-listed path
    (never the query). ``message`` is already redacted and bounded by the client.
    """

    def __init__(self, status: str, message: str = "", *, http_status: Optional[int] = None,
                 path: Optional[str] = None) -> None:
        if status not in STATUSES:
            raise ValueError("unknown TradierError status %r" % (status,))
        self.status = status
        self.message = message if message else status
        self.http_status = http_status
        self.path = path
        super().__init__("%s: %s" % (self.status, self.message))

    def __str__(self) -> str:
        return "%s: %s" % (self.status, self.message)


class TransportError(TradierError):
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

    ``scheme`` is ``"https"`` (the Tradier hosts) or ``"http"`` (the loopback fake server). The
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


def fixture_file_name(path: str, params: Dict[str, str]) -> str:
    """The offline fixture file for an allow-listed request (module notes on the key rule).

    ``params`` holds one value per query parameter. Unknown paths raise ``ValueError``.
    """
    if path == PATH_QUOTES:
        symbols = [s.strip().upper() for s in params.get("symbols", "").split(",") if s.strip()]
        return "quotes__%s.json" % ",".join(sorted(symbols))
    if path == PATH_CHAINS:
        return "chains__%s__%s.json" % (params.get("symbol", "").upper(), params.get("expiration", ""))
    if path == PATH_EXPIRATIONS:
        return "expirations__%s.json" % params.get("symbol", "").upper()
    if path == PATH_STRIKES:
        return "strikes__%s__%s.json" % (params.get("symbol", "").upper(), params.get("expiration", ""))
    if path == PATH_HISTORY:
        return "history__%s__%s__%s.json" % (
            params.get("symbol", "").upper(), params.get("start", ""), params.get("end", ""))
    if path == PATH_CLOCK:
        return "clock.json"
    if path == PATH_CALENDAR:
        month = params.get("month", "")
        if month.isdigit():
            month = "%02d" % int(month)
        return "calendar__%s-%s.json" % (params.get("year", ""), month)
    raise ValueError("no offline fixture naming rule for path %r" % (path,))


def _fixture_key(name: str) -> str:
    """The ``<key>`` part of a fixture file name (``quotes__SPY,WMT.json`` -> ``SPY,WMT``)."""
    stem = name[:-len(".json")] if name.endswith(".json") else name
    return stem.split("__", 1)[1] if "__" in stem else ""


def _clock_instant(set_dir: str, fixture_set: str) -> datetime.datetime:
    path = os.path.join(set_dir, "clock.json")
    try:
        with open(path, "rb") as handle:
            document = json.loads(handle.read().decode("utf-8"))
        stamp = ClockInfo.from_vendor(document).timestamp_utc
    except (OSError, ValueError) as exc:  # MarketTypesError is a ValueError
        raise ValueError("OfflineTransport(%s) needs now=: %s is missing or unreadable (%s)"
                         % (fixture_set, path, exc)) from None
    if stamp is None:
        raise ValueError("OfflineTransport(%s) needs now=: %s carries no timestamp" % (fixture_set, path))
    return stamp


class OfflineTransport:
    """Replays recorded responses from ``<fixture_dir>/<fixture_set>/`` (module notes).

    Needs no token and opens no socket. ``now`` is the receipt instant of every answer; when it
    is not given, the set's ``clock.json`` timestamp (the vendor clock at the recorded fetch) is
    used, so an offline run is deterministic either way. The set name must be a plain
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
    """``quotes`` keyed by the vendor's symbol; ``unmatched`` exactly as the vendor echoed them
    (the symbol does not exist or has expired); ``not_returned`` the requested symbols the vendor
    neither quoted nor listed as unmatched (an incomplete answer, reported and never silently
    dropped); ``receipt`` is the last request's receipt and ``receipts`` every batch's, in order."""

    quotes: Dict[str, Quote]
    unmatched: List[str]
    receipt: RequestReceipt
    receipts: Tuple[RequestReceipt, ...] = ()
    not_returned: Tuple[str, ...] = ()


@dataclass(frozen=True)
class ChainResult:
    options: List[Quote]
    receipt: RequestReceipt
    raw: Dict[str, Any]


@dataclass(frozen=True)
class ExpirationsResult:
    dates: List[datetime.date]
    receipt: RequestReceipt
    raw: Dict[str, Any]


@dataclass(frozen=True)
class StrikesResult:
    strikes: List[Decimal]
    receipt: RequestReceipt
    raw: Dict[str, Any]


@dataclass(frozen=True)
class HistoryResult:
    bars: List[Bar]
    receipt: RequestReceipt
    raw: Dict[str, Any]


@dataclass(frozen=True)
class ClockResult:
    """The ``/v1/markets/clock`` answer; ``timestamp`` is epoch SECONDS on this endpoint."""

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
    raw: Dict[str, Any]


# --------------------------------------------------------------------------------------------
# Response parsing (object-or-list shapes; a present-but-garbled field is a MarketTypesError)
# --------------------------------------------------------------------------------------------


def _container(document: Any, key: str) -> Optional[Dict[str, Any]]:
    """``document[key]`` as a dict, ``None`` for ``null``/``"null"``/``""``; anything else raises."""
    if not isinstance(document, dict):
        raise MarketTypesError(key, "the response is not a JSON object")
    if key not in document:
        raise MarketTypesError(key, "missing from the response")
    value = document[key]
    if value is None or (isinstance(value, str) and value in ("null", "")):
        return None
    if not isinstance(value, dict):
        raise MarketTypesError(key, "expected an object or null, got %s" % type(value).__name__)
    return value


def _parse_quotes(document: Any) -> Tuple[Dict[str, Quote], List[str]]:
    container = _container(document, "quotes")
    quotes: Dict[str, Quote] = {}
    unmatched: List[str] = []
    if container is None:
        return quotes, unmatched
    for item in one_or_many(container.get("quote")):
        quote = Quote.from_vendor(item)
        quotes[quote.symbol] = quote
    raw_unmatched = container.get("unmatched_symbols")
    if raw_unmatched is not None:
        if not isinstance(raw_unmatched, dict):
            raise MarketTypesError("unmatched_symbols", "expected an object, got %s" % type(raw_unmatched).__name__)
        for item in one_or_many(raw_unmatched.get("symbol")):
            text = to_str(item, "unmatched_symbols.symbol")
            if text is not None and text.strip() and text.strip() not in unmatched:
                unmatched.append(text.strip())
    return quotes, unmatched


def _parse_chain(document: Any) -> List[Quote]:
    container = _container(document, "options")
    if container is None:
        return []
    return [Quote.from_vendor(item) for item in one_or_many(container.get("option"))]


def _parse_expirations(document: Any) -> List[datetime.date]:
    container = _container(document, "expirations")
    if container is None:
        return []
    if "date" in container:
        items = one_or_many(container.get("date"))
    else:  # the detailed shape (strikes=true / contractSize / expirationType): one object per expiry
        items = []
        for entry in one_or_many(container.get("expiration")):
            if not isinstance(entry, dict):
                raise MarketTypesError("expiration", "expected an object, got %s" % type(entry).__name__)
            items.append(entry.get("date"))
    dates: List[datetime.date] = []
    for item in items:
        day = parse_iso_date(item, "date")
        if day is None:
            raise MarketTypesError("date", "blank expiration date")
        dates.append(day)
    return dates


def _parse_strikes(document: Any) -> List[Decimal]:
    container = _container(document, "strikes")
    if container is None:
        return []
    strikes: List[Decimal] = []
    for item in one_or_many(container.get("strike")):
        value = to_decimal(item, "strike")
        if value is None:
            raise MarketTypesError("strike", "blank strike")
        strikes.append(value)
    return strikes


def _parse_history(document: Any) -> List[Bar]:
    container = _container(document, "history")
    if container is None:
        return []
    return [Bar.from_vendor(item) for item in one_or_many(container.get("day"))]


def _parse_clock(document: Any) -> ClockInfo:
    if not isinstance(document, dict):
        raise MarketTypesError("clock", "the response is not a JSON object")
    if "clock" not in document:
        raise MarketTypesError("clock", "missing from the response")
    return ClockInfo.from_vendor(document)


def _parse_calendar(document: Any) -> Tuple[List[CalendarDay], Optional[int], Optional[int]]:
    container = _container(document, "calendar")
    if container is None:
        return [], None, None
    month = to_int(container.get("month"), "month")
    year = to_int(container.get("year"), "year")
    days_container = container.get("days")
    if days_container is None:
        return [], month, year
    if not isinstance(days_container, dict):
        raise MarketTypesError("days", "expected an object or null, got %s" % type(days_container).__name__)
    days = [CalendarDay.from_vendor(item) for item in one_or_many(days_container.get("day"))]
    return days, month, year


def _decode_json(body: bytes) -> Any:
    """UTF-8 JSON object; ``ValueError`` (with a body-free reason) for anything else."""
    text = body.decode("utf-8")
    document = json.loads(text, parse_constant=_reject_constant)
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


def _is_quota_violation(text: str) -> bool:
    lower = text.lower()
    return "quota violation" in lower or "quotaviolation" in lower


def _classify(status: int) -> Optional[str]:
    """The error status of an HTTP status (``None`` for 200); 400 needs the body, see the client."""
    if status == 200:
        return None
    if 300 <= status < 400:
        return "refused_redirect"
    if status in (401, 403):
        return "auth"
    if status == 429:
        return "quota"
    if status == 400:
        return "bad_request"
    return "http"


def _iso_date(value: Any, name: str) -> str:
    if isinstance(value, datetime.datetime) or not isinstance(value, datetime.date):
        raise ValueError("%s must be a datetime.date, got %s" % (name, type(value).__name__))
    return value.isoformat()


def _validate_symbol(raw: Any) -> str:
    if not isinstance(raw, str):
        raise TradierError("bad_symbol", "symbol must be text, got %s" % type(raw).__name__)
    text = raw.strip()
    if not SYMBOL_RE.fullmatch(text):
        raise TradierError("bad_symbol", "symbol %r does not match %s" % (raw, SYMBOL_RE.pattern))
    return text.upper()


def _resolve_host(host_kind: Any) -> Tuple[str, str]:
    """``(scheme, netloc)`` for a host kind; anything unknown is ``refused_host``."""
    if isinstance(host_kind, str) and host_kind in HOSTS:
        return "https", HOSTS[host_kind]
    if isinstance(host_kind, str) and host_kind.startswith(LOOPBACK_PREFIX):
        port_text = host_kind[len(LOOPBACK_PREFIX):]
        if port_text.isdigit() and 1 <= int(port_text) <= 65535:
            return "http", "%s:%d" % (LOOPBACK_HOST, int(port_text))
    raise TradierError("refused_host",
                       "host kind %r is not sandbox, production or loopback:<port>" % (host_kind,))


# --------------------------------------------------------------------------------------------
# Client
# --------------------------------------------------------------------------------------------


class TradierClient:
    """GET-only client for the allow-listed Tradier market-data endpoints (module notes).

    ``host_kind`` is ``"sandbox"``, ``"production"`` (both HTTPS) or ``"loopback:<port>"``
    (plain HTTP to 127.0.0.1, for the fake-server tests). ``token`` may be ``None`` only with an
    :class:`OfflineTransport`. The constructor performs no I/O. ``on_request`` receives one
    :class:`RequestReceipt` per request handed to the transport.
    """

    __slots__ = ("_host_kind", "_scheme", "_netloc", "_token", "_timeout_s", "_user_agent",
                 "_transport", "_on_request")

    def __init__(self, host_kind: str, token: Optional[Token], *, timeout_s: float = DEFAULT_TIMEOUT_S,
                 user_agent: str, transport: Optional[Transport] = None,
                 on_request: Optional[Callable[[RequestReceipt], None]] = None) -> None:
        scheme, netloc = _resolve_host(host_kind)
        if token is not None and not isinstance(token, Token):
            raise TypeError("token must be a Token (never a raw secret string), got %s" % type(token).__name__)
        if transport is None:
            transport = UrllibTransport(scheme)
        if token is None and not isinstance(transport, OfflineTransport):
            raise TradierError("no_token", "a token is required for the %s host; only an "
                                           "OfflineTransport runs without one" % host_kind)
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not timeout_s > 0:
            raise ValueError("timeout_s must be a positive number of seconds, got %r" % (timeout_s,))
        if not isinstance(user_agent, str) or not user_agent.strip() or not _visible_ascii(user_agent, allow_space=True):
            raise ValueError("user_agent must be non-blank printable ASCII, got %r" % (user_agent,))
        if on_request is not None and not callable(on_request):
            raise TypeError("on_request must be callable, got %s" % type(on_request).__name__)
        self._host_kind = host_kind
        self._scheme = scheme
        self._netloc = netloc
        self._token = token
        self._timeout_s = float(timeout_s)
        self._user_agent = user_agent
        self._transport = transport
        self._on_request = on_request

    def __repr__(self) -> str:
        return "TradierClient(host_kind=%r, host=%r, token=%r, transport=%s)" % (
            self._host_kind, self._netloc, self._token, type(self._transport).__name__)

    # -- read-only configuration ----------------------------------------------------------
    @property
    def host_kind(self) -> str:
        return self._host_kind

    @property
    def host(self) -> str:
        """The pinned ``netloc`` every URL carries."""
        return self._netloc

    @property
    def scheme(self) -> str:
        return self._scheme

    @property
    def token(self) -> Optional[Token]:
        return self._token

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
    def url_for(self, path: str, params: Dict[str, str]) -> str:
        """The URL of an allow-listed path with ``params`` url-encoded, re-checked against the pin."""
        if path not in ALLOWED_PATHS:
            raise TradierError("refused_path", "path %r is not an allow-listed market-data endpoint" % (path,),
                               path=path)
        for key, value in params.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise TypeError("query parameters must be text, got %r=%r" % (key, value))
        query = urllib.parse.urlencode(list(params.items()))
        url = "%s://%s%s" % (self._scheme, self._netloc, path)
        if query:
            url = url + "?" + query
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != self._scheme or parts.netloc != self._netloc or parts.path not in ALLOWED_PATHS:
            raise TradierError("refused_host", "built URL does not point at the pinned host and an "
                                               "allow-listed path", path=path)
        return url

    def _headers(self) -> Dict[str, str]:
        headers: Dict[str, str] = {}
        if self._token is not None:
            headers["Authorization"] = self._token.authorization_header()
        headers["Accept"] = HEADER_ACCEPT
        headers["User-Agent"] = self._user_agent
        return headers

    # -- hygiene --------------------------------------------------------------------------------
    def _redact(self, text: str) -> str:
        if self._token is None:
            return text
        header = self._token.authorization_header()
        secret = header[len(_BEARER):]
        return text.replace(header, REDACTED).replace(secret, REDACTED)

    def _clean(self, text: str) -> str:
        """Redacted, printable and bounded: the only way vendor text reaches a message."""
        return _printable_excerpt(self._redact(text))

    def _echoes_credential(self, body: bytes, document: Any) -> bool:
        """True when a 200 body carries the secret, raw or inside a decoded JSON string (module
        notes: such an answer is refused so the cache can never store it)."""
        if self._token is None:
            return False
        secret = self._token.authorization_header()[len(_BEARER):]
        return secret in _bytes_text(body) or _json_mentions(document, secret)

    def _http_message(self, status: int, body: bytes) -> str:
        excerpt = self._clean(_bytes_text(body))
        if excerpt:
            return "HTTP %d: %s" % (status, excerpt)
        return "HTTP %d (empty body)" % status

    def _notify(self, receipt: RequestReceipt) -> None:
        if self._on_request is not None:
            self._on_request(receipt)

    # -- the one exchange -----------------------------------------------------------------
    def _exchange(self, path: str, params: Dict[str, str], symbols_count: int) -> Tuple[Any, RequestReceipt]:
        """GET ``path`` once; returns ``(document, receipt)`` on a decoded 200 WITHOUT notifying
        (the caller notifies once it knows the final status); notifies and raises otherwise."""
        url = self.url_for(path, params)
        headers = self._headers()
        try:
            response = self._transport.fetch(url, headers, self._timeout_s)
        except TransportError as exc:
            self._notify(RequestReceipt(path, symbols_count, exc.receipt_utc, exc.status,
                                        http_status=None, elapsed_ms=exc.elapsed_ms))
            raise TradierError(exc.status, self._redact(exc.message), path=path) from exc
        receipt = RequestReceipt(
            path=path,
            symbols_count=symbols_count,
            receipt_utc=response.receipt_utc,
            status=STATUS_OK,
            http_status=response.status,
            ratelimit_allowed=_header_int(response.headers, "x-ratelimit-allowed"),
            ratelimit_used=_header_int(response.headers, "x-ratelimit-used"),
            ratelimit_available=_header_int(response.headers, "x-ratelimit-available"),
            ratelimit_expiry_utc=self._ratelimit_expiry(response.headers),
            elapsed_ms=response.elapsed_ms,
        )
        failure = _classify(response.status)
        if failure == "bad_request" and _is_quota_violation(_bytes_text(response.body)):
            failure = "quota"
        if failure is not None:
            self._notify(replace(receipt, status=failure))
            if failure == "refused_redirect":
                message = "HTTP %d redirect refused: the client follows no redirects" % response.status
            else:
                message = self._http_message(response.status, response.body)
            raise TradierError(failure, message, http_status=response.status, path=path)
        try:
            document = _decode_json(response.body)
        except ValueError as exc:  # UnicodeDecodeError and JSONDecodeError included
            self._notify(replace(receipt, status="decode"))
            raise TradierError(
                "decode", "HTTP 200 body is not JSON (%s): %s" % (
                    self._clean(str(exc)), self._clean(_bytes_text(response.body)) or "(empty body)"),
                http_status=response.status, path=path) from exc
        if self._echoes_credential(response.body, document):
            self._notify(replace(receipt, status="decode"))
            raise TradierError("decode", "HTTP 200 body echoes the credential; the answer is refused so that "
                                         "nothing downstream (cache, ledger, output) can carry it",
                               http_status=response.status, path=path)
        return document, receipt

    @staticmethod
    def _ratelimit_expiry(headers: Dict[str, str]) -> Optional[datetime.datetime]:
        raw = _header_int(headers, "x-ratelimit-expiry")
        if raw is None:
            return None
        try:
            return from_epoch_ms(raw, "x-ratelimit-expiry")
        except MarketTypesError:
            return None

    def get_json(self, path: str, params: Optional[Dict[str, str]] = None, *,
                 symbols_count: int = 0) -> Tuple[Any, RequestReceipt]:
        """One allow-listed GET returning the decoded JSON object and its receipt (notified)."""
        document, receipt = self._exchange(path, dict(params or {}), symbols_count)
        self._notify(receipt)
        return document, receipt

    def _complete(self, path: str, params: Dict[str, str], symbols_count: int,
                  parse: Callable[[Any], Any]) -> Tuple[Any, RequestReceipt, Dict[str, Any]]:
        """Exchange, convert with ``parse`` and record exactly one receipt with the final status."""
        document, receipt = self._exchange(path, params, symbols_count)
        try:
            value = parse(document)
        except MarketTypesError as exc:
            self._notify(replace(receipt, status="decode"))
            raise TradierError("decode", self._clean("%s: %s" % (exc.field, exc.message)),
                               http_status=receipt.http_status, path=path) from exc
        self._notify(receipt)
        return value, receipt, document

    # -- endpoints ----------------------------------------------------------------------------
    @staticmethod
    def _normalise_symbols(symbols: Iterable[str]) -> List[str]:
        if isinstance(symbols, str):
            symbols = [symbols]  # a lone string is one symbol, not its characters
        ordered: List[str] = []
        for raw in symbols:
            symbol = _validate_symbol(raw)
            if symbol not in ordered:
                ordered.append(symbol)
        if not ordered:
            raise ValueError("at least one symbol is required")
        return ordered

    def quotes(self, symbols: Iterable[str], *, greeks: bool = False) -> QuotesResult:
        """``/v1/markets/quotes`` for ``symbols`` (validated, upper-cased, de-duplicated, in
        consecutive batches of :data:`MAX_BATCH`). ``greeks=True`` only adds the query flag (the
        sandbox publishes none). A failing batch raises after its receipt is recorded. A symbol
        the vendor neither quoted nor echoed as unmatched is listed in ``not_returned``."""
        requested = self._normalise_symbols(symbols)
        quotes: Dict[str, Quote] = {}
        unmatched: List[str] = []
        receipts: List[RequestReceipt] = []
        for start in range(0, len(requested), MAX_BATCH):
            batch = requested[start:start + MAX_BATCH]
            params: Dict[str, str] = {"symbols": ",".join(batch)}
            if greeks:
                params["greeks"] = "true"
            (batch_quotes, batch_unmatched), receipt, _document = self._complete(
                PATH_QUOTES, params, len(batch), _parse_quotes)
            quotes.update(batch_quotes)
            for symbol in batch_unmatched:
                if symbol not in unmatched:
                    unmatched.append(symbol)
            receipts.append(receipt)
        quoted = {symbol.upper() for symbol in quotes}
        echoed = {symbol.upper() for symbol in unmatched}
        not_returned = tuple(symbol for symbol in requested if symbol not in quoted and symbol not in echoed)
        return QuotesResult(quotes, unmatched, receipts[-1], tuple(receipts), not_returned)

    def option_chain(self, symbol: str, expiration: datetime.date, *, greeks: bool = False) -> ChainResult:
        """``/v1/markets/options/chains``; ``{"options": null}`` is an empty chain."""
        params: Dict[str, str] = {"symbol": _validate_symbol(symbol),
                                  "expiration": _iso_date(expiration, "expiration")}
        if greeks:
            params["greeks"] = "true"
        options, receipt, document = self._complete(PATH_CHAINS, params, 1, _parse_chain)
        return ChainResult(options, receipt, document)

    def option_expirations(self, symbol: str, *, include_all_roots: bool = True) -> ExpirationsResult:
        """``/v1/markets/options/expirations``; ``includeAllRoots=true`` is sent when requested."""
        params: Dict[str, str] = {"symbol": _validate_symbol(symbol)}
        if include_all_roots:
            params["includeAllRoots"] = "true"
        dates, receipt, document = self._complete(PATH_EXPIRATIONS, params, 1, _parse_expirations)
        return ExpirationsResult(dates, receipt, document)

    def option_strikes(self, symbol: str, expiration: datetime.date) -> StrikesResult:
        """``/v1/markets/options/strikes``; strikes are ``Decimal`` from the JSON text."""
        params: Dict[str, str] = {"symbol": _validate_symbol(symbol),
                                  "expiration": _iso_date(expiration, "expiration")}
        strikes, receipt, document = self._complete(PATH_STRIKES, params, 1, _parse_strikes)
        return StrikesResult(strikes, receipt, document)

    def history(self, symbol: str, start: datetime.date, end: datetime.date, *,
                interval: str = "daily") -> HistoryResult:
        """``/v1/markets/history`` daily (or weekly/monthly) bars from ``start`` to ``end``."""
        if interval not in HISTORY_INTERVALS:
            raise ValueError("interval must be one of %s, got %r" % (", ".join(HISTORY_INTERVALS), interval))
        start_text = _iso_date(start, "start")
        end_text = _iso_date(end, "end")
        if end < start:
            raise ValueError("end %s is before start %s" % (end_text, start_text))
        params: Dict[str, str] = {"symbol": _validate_symbol(symbol), "interval": interval,
                                  "start": start_text, "end": end_text}
        bars, receipt, document = self._complete(PATH_HISTORY, params, 1, _parse_history)
        return HistoryResult(bars, receipt, document)

    def clock(self) -> ClockResult:
        """``/v1/markets/clock``: the vendor's session state; ``timestamp`` is epoch seconds."""
        info, receipt, document = self._complete(PATH_CLOCK, {}, 0, _parse_clock)
        return ClockResult(info.date, info.state, info.description, info.timestamp_utc,
                           info.next_change, info.next_state, receipt, document)

    def calendar(self, month: int, year: int) -> CalendarResult:
        """``/v1/markets/calendar`` for one month (``month`` 1-12, ``year`` four digits)."""
        if isinstance(month, bool) or not isinstance(month, int) or not 1 <= month <= 12:
            raise ValueError("month must be an integer 1-12, got %r" % (month,))
        if isinstance(year, bool) or not isinstance(year, int) or not 1900 <= year <= 9999:
            raise ValueError("year must be a four-digit integer, got %r" % (year,))
        params: Dict[str, str] = {"month": "%02d" % month, "year": "%d" % year}
        (days, got_month, got_year), receipt, document = self._complete(PATH_CALENDAR, params, 0, _parse_calendar)
        return CalendarResult(days, got_month, got_year, receipt, document)


# --------------------------------------------------------------------------------------------
# Token loading
# --------------------------------------------------------------------------------------------


def load_token(env_name: str, token_file: Optional[str]) -> Token:
    """The sandbox token: the environment variable first (stripped; empty counts as absent),
    then the first line of ``token_file`` (a regular file with no group/other permission bits,
    else ``token_file_mode``); nothing in either place is ``no_token``."""
    if env_name:
        value = os.environ.get(env_name)
        if value is not None and value.strip():
            try:
                return Token(value, "env")
            except ValueError:
                raise TradierError("no_token", "environment variable %s does not hold a usable token "
                                   "(it must be visible ASCII without spaces)" % env_name) from None
    if token_file:
        try:
            info = os.stat(token_file)
        except FileNotFoundError:
            info = None
        except OSError as exc:
            raise TradierError("no_token", "token file %s cannot be read: %s" % (token_file, exc.strerror)) from None
        if info is not None:
            mode = stat.S_IMODE(info.st_mode)
            if not stat.S_ISREG(info.st_mode):
                raise TradierError("token_file_mode", "token file %s is not a regular file" % token_file)
            if mode & _GROUP_OTHER_BITS:
                raise TradierError("token_file_mode", "token file %s has mode %04o; it must have no group "
                                   "or other permission bits (chmod 600)" % (token_file, mode))
            try:
                with open(token_file, "rb") as handle:
                    first_line = handle.readline()
                text = first_line.decode("utf-8").strip()
            except OSError as exc:
                raise TradierError("no_token", "token file %s cannot be read: %s" % (token_file, exc.strerror)) from None
            except UnicodeDecodeError:
                text = ""
            if text:
                try:
                    return Token(text, "file")
                except ValueError:
                    raise TradierError("no_token", "token file %s does not hold a usable token on its first "
                                       "line (it must be visible ASCII without spaces)" % token_file) from None
    where = []
    if env_name:
        where.append("environment variable %s is unset or empty" % env_name)
    if token_file:
        where.append("token file %s is missing or empty" % token_file)
    raise TradierError("no_token", "no market-data token: %s" % ("; ".join(where) or "no source configured"))


__all__ = [
    "ALLOWED_PATHS",
    "Bar",
    "CalendarResult",
    "ChainResult",
    "ClockResult",
    "DEFAULT_FIXTURE_SET",
    "DEFAULT_TIMEOUT_S",
    "ExpirationsResult",
    "HISTORY_INTERVALS",
    "HOSTS",
    "HistoryResult",
    "LOOPBACK_HOST",
    "MAX_BATCH",
    "MAX_ERROR_BODY_BYTES",
    "MAX_MESSAGE_CHARS",
    "MAX_RESPONSE_BYTES",
    "OfflineTransport",
    "PATH_CALENDAR",
    "PATH_CHAINS",
    "PATH_CLOCK",
    "PATH_EXPIRATIONS",
    "PATH_HISTORY",
    "PATH_QUOTES",
    "PATH_STRIKES",
    "PRODUCTION_HOST",
    "Quote",
    "QuotesResult",
    "RATE_LIMIT_PER_MIN",
    "REDACTED",
    "RefuseRedirects",
    "RequestReceipt",
    "SANDBOX_HOST",
    "STATUSES",
    "STATUS_OK",
    "SYMBOL_RE",
    "StrikesResult",
    "TOKEN_SOURCES",
    "Token",
    "TradierClient",
    "TradierError",
    "Transport",
    "TransportError",
    "TransportResponse",
    "UrllibTransport",
    "fixture_file_name",
    "load_token",
]
