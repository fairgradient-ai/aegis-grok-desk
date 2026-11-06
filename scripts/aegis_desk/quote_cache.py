"""Quote cache and market-data budget: TTL serving, batching, the desk-wide 60/min ledger.

New for Aegis Desk (plan sections 4 and 9, skill 4); standard library only. The cache sits between
the tools and the GET-only client: it serves a quote from ``data/cache/quotes.json`` while the
copy is younger than the desk's TTL, fetches the rest in batches of at most :data:`MAX_BATCH`
symbols, keeps every request receipt in ``data/cache/ledger.jsonl`` and refuses to fetch when the
rolling 60-second window would breach the vendor's limit minus a headroom. No wall clock is read
here: ``now`` is always a parameter, so a replayed instant reproduces a run exactly.

Files (under ``paths.cache_dir``, written through :mod:`aegis_desk.state`)
-----------------------------------------------------------------------
* ``quotes.json``: ``{"schema": 1, "quotes": {SYMBOL: {"fetched_utc": ISO-8601 UTC, "quote": <raw
  vendor dict>}}}``, written atomically under ``state.locked(paths.lock_path)`` after a re-read, so
  two overlapping runs merge instead of clobbering each other. The raw vendor dict is stored (never
  a re-serialised ``Quote``) so a reload goes through ``Quote.from_vendor`` exactly like a fetch.
* ``ledger.jsonl``: one line per request attempt, success or failure: ``{"utc", "path",
  "symbols" (count), "status", "http_status", "ratelimit_available"}``. It is appended by the
  :func:`ledger_hook` the tool passes to the client as ``on_request``, so the client's own view of
  every attempt (batches it split itself included) is what the budget counts. The ledger is
  desk-wide: every tool of the desk root shares the window.
* ``chains/<SYMBOL>__<YYYY-MM-DD>.json``: ``{"schema": 1, "symbol", "expiration", "fetched_utc",
  "options": [<raw vendor dict>, ...]}``; chains are fetched on demand only (``--chain``) and
  capped per run.
* ``history/<SYMBOL>__<START>__<END>.json``: ``{"schema": 1, "symbol", "start", "end", "fetched_utc",
  "bars": [<raw vendor dict>, ...]}`` (PR 2b): the daily bars ``csv_recon`` settles expired legs
  against. A cached answer with bars that was fetched on a later ET date than ``end`` is final
  (closed days never change) and served without any TTL; otherwise the quote TTL applies, an
  empty answer included. History is fetched on non-trading days too (the post-expiry Saturday
  routine), and a history request counts against ``per_run_max_requests`` like a chain.
* ``expirations/<SYMBOL>.json``: ``{"schema": 1, "symbol", "fetched_utc", "dates": [YYYY-MM-DD, ...]}`` (PR 3b): the
  listed expirations of one underlying (:func:`get_expirations`), the list the Structuring Desk picks its candidate
  expirations from. An expiration list does not change within a session, so a copy fetched on the ET date of ``now``
  is served without a refetch whatever its age (a copy stamped in the future is not trusted); an expirations request
  counts against ``per_run_max_requests`` but never against the chain cap.

Rules
-----
* A symbol is upper-cased and stripped; one that fails :data:`SYMBOL_RE` (the client's pattern) is
  reported ``fetch_failed:bad_symbol`` on its own and nothing is sent for it, so a typo never sinks
  the batch it travels with.
* A cached quote is served when ``0 <= now - fetched_utc <= ttl`` and ``force`` is false; a copy
  stamped in the future is not trusted. A garbled or corrupt cache file or entry is a warning and a
  refetch (``state.read_json`` quarantines corrupt files), never a silent miss. ``force`` asks for
  a fresher copy and never throws a valid one away: when the forced refetch cannot happen or fails
  (budget, cap, a client error, a vendor that omits the symbol) a copy still inside the TTL is
  served from the cache with a warning, and on a non-trading day ``force`` is ignored.
* The rest are fetched in request order, ``MAX_BATCH`` symbols per request, while the per-run cap
  (``per_run_max_requests``, counted in the caller's :class:`RunCounters`) allows and
  :func:`budget_left` is positive (re-read before every batch). Refused batches are reported per
  symbol: ``per_run_cap`` or ``quota_guard``. The budget counts the ledger records inside the
  60-second window (every tool of the desk) plus this run's own receipts, which a live run's window
  cannot see: ``now`` is read once at the start of a run while the transport stamps each receipt
  with the wall clock, so the run's receipts fall after ``now``. A :func:`ledger_hook` built with
  the run records every receipt on it and :func:`requests_counted` adds the ones the window
  misses, each counted once.
* A request counts only when the client handed it to the transport: a refusal before anything is
  sent (``bad_symbol``, ``refused_path``, ``refused_host``) produces no receipt and is not a
  request made.
* A ``quota`` error marks the batch and every remaining symbol ``quota_guard`` and stops fetching
  for the whole run: every later batch, call and chain fetch of the same :class:`RunCounters`
  reports ``quota_guard`` without calling the client (``RunCounters.stop_reason``). A credential
  or configuration error (``auth``, ``no_token``, ``token_file_mode``, ``refused_*``) marks its
  batch ``fetch_failed:<status>`` and stops the run the same way (it would fail the same way and
  spend budget). Any other client error (``network``, ``timeout``, ``http``, ``decode``,
  ``bad_request``) marks its batch ``fetch_failed:<status>`` and the next batch is still tried.
  An expired cache copy of a symbol that could not be refreshed is left OUT of ``quotes`` (stale is
  UNAVAILABLE for rules) and offered in ``stale`` so a tool can display it under a "stale" label.
  A symbol the vendor neither matched nor listed as unmatched is ``fetch_failed:not_returned``.
* ``allow_fetch=False`` (the tools pass this on weekends and holidays, plan section 8: one "NYSE
  closed" line, no fetch) serves any cached copy whatever its age, with a warning when the TTL has
  passed, and reports the rest ``market_closed``.
* The client is duck-typed to the brief's interface (``quotes(symbols) -> QuotesResult(quotes,
  unmatched, receipt)``, ``option_chain(symbol, expiration) -> ChainResult(options, receipt)``,
  ``option_expirations(symbol) -> ExpirationsResult(dates, receipt)``, errors carrying ``.status``),
  so the unit tests run with a fake and this module never imports the
  network client at import time. :func:`open_market_data` (the plumbing both tools share) imports
  ``aegis_desk.tradier_client`` lazily when it builds the real client.

Tool plumbing (shared by ``scripts/quote_cache.py`` and ``scripts/quote_qc.py``)
------------------------------------------------------------------------------
:func:`open_market_data` resolves the desk root, loads ``desk.toml``, classifies ``now``
(``market_clock.session_at``), refuses ``host = "production"`` with :data:`PRODUCTION_REFUSED_MESSAGE`
(plan section 10: production is outside the no-trade guarantee until the market-data proxy), builds
the client with a :func:`ledger_hook` wired to the run's :class:`RunCounters` (offline: the recorded
fixture set, ``regular`` by default; online: the token from the configured environment variable or
file, a missing token being :data:`NO_TOKEN_MESSAGE`) and creates the runtime directories. :class:`MarketDataRefused` is the exit-20 family; ``DeskPathsError`` and
``DeskConfigError`` are exit 1; ``TableExpiredError`` is exit 20.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from . import desk_config, desk_paths, market_clock, state
from .desk_config import DeskConfig
from .desk_paths import DeskPaths
from .market_clock import SessionInfo
from .market_types import Bar, MarketTypesError, Quote, RequestReceipt

UTC = datetime.timezone.utc  # noqa: UP017 - datetime.UTC needs 3.11; the floor is 3.9

SCHEMA: int = 1
QUOTES_FILE: str = "quotes.json"
LEDGER_FILE: str = "ledger.jsonl"
CHAINS_SUBDIR: str = "chains"
HISTORY_SUBDIR: str = "history"
EXPIRATIONS_SUBDIR: str = "expirations"
_ISO_DAY_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$")
MAX_BATCH: int = 50
"""Symbols per quotes request (equals ``tradier_client.MAX_BATCH``; asserted by the tool tests)."""
SYMBOL_RE = re.compile(r"^[A-Za-z0-9.$^:_-]{1,32}$")
"""Accepted symbol shape (equals ``tradier_client.SYMBOL_RE``; asserted by the tool tests): a symbol
failing it is ``fetch_failed:bad_symbol`` on its own and nothing is sent for it."""
LEDGER_TAIL_BYTES: int = 65536
"""How much of the ledger's tail :func:`requests_in_window` reads: hundreds of records, far more
than one minute of requests can hold."""
LOCK_TIMEOUT_S: float = state.DEFAULT_LOCK_TIMEOUT_S
WINDOW_SECONDS: int = 60
DEFAULT_FIXTURE_SET: str = "regular"

SERVED_CACHE: str = "cache"
SERVED_FETCH: str = "fetch"
REASON_UNMATCHED: str = "unmatched"
REASON_QUOTA_GUARD: str = "quota_guard"
REASON_PER_RUN_CAP: str = "per_run_cap"
REASON_MARKET_CLOSED: str = "market_closed"
REASON_NO_BARS: str = "no_bars"
"""A history answer without bars (the vendor's ``{"history": null}``): served, cached under the TTL."""
FETCH_FAILED_PREFIX: str = "fetch_failed:"
REASON_NOT_RETURNED: str = FETCH_FAILED_PREFIX + "not_returned"
REASON_BAD_SYMBOL: str = FETCH_FAILED_PREFIX + "bad_symbol"
STATUS_QUOTA: str = "quota"
NO_SEND_STATUSES: Tuple[str, ...] = ("bad_symbol", "refused_path", "refused_host")
"""Client errors raised before anything is sent (no receipt): never counted as a request made."""
STOP_STATUSES: Tuple[str, ...] = ("auth", "no_token", "token_file_mode", "refused_redirect", "refused_host",
                                  "refused_path")
"""Client errors that would recur on the next batch: the run stops fetching after one of them."""

PRODUCTION_REFUSED_MESSAGE: str = ("production market data is not supported before the market-data proxy (Stage 3); "
                                   "use the sandbox host")
NO_TOKEN_MESSAGE: str = ("no market-data token: add the Tradier sandbox token through the secret card "
                         "(or the acknowledged token file)")
REFUSED_PRODUCTION: str = "production_host"
REFUSED_NO_TOKEN: str = "no_token"


class MarketDataRefused(Exception):
    """The desk cannot use market data as configured; ``status`` names why (``production_host``,
    ``no_token``, ``token_file_mode``). Tools map it to exit 20. The message never carries a token."""

    def __init__(self, status: str, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(message)


# --------------------------------------------------------------------------------------------
# Configuration and results
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CacheConfig:
    """TTL and budget policy; :meth:`from_desk` takes the TTL from ``desk.toml``."""

    ttl_min: int
    per_run_max_requests: int = 20
    per_run_max_chains: int = 4
    minute_budget: int = 60
    minute_headroom: int = 5

    @classmethod
    def from_desk(cls, cfg: DeskConfig) -> "CacheConfig":
        return cls(ttl_min=cfg.market_data.quote_ttl_min)


@dataclass
class RunCounters:
    """Per-run state shared across the calls of one tool run.

    ``requests`` and ``chains`` count what was handed to the transport (the per-run caps);
    ``receipt_stamps`` holds the receipt instant of every request of the run, recorded by
    :func:`ledger_hook` when it is built with the run, so the budget guard can count the run's own
    requests even when the transport stamped them after ``now`` (module notes); ``stop_reason``
    records a ``quota`` or credential/configuration error that stops every later fetch of the run,
    quotes and chains alike.
    """

    requests: int = 0
    chains: int = 0
    receipt_stamps: List[datetime.datetime] = field(default_factory=list)
    stop_reason: Optional[str] = None


@dataclass(frozen=True)
class CacheResult:
    """What :func:`get_quotes` served. ``served_from`` is ``"cache"`` or ``"fetch"`` per symbol;
    ``missing`` maps a symbol to its reason; ``fetched_utc`` is the cache stamp of each served
    quote; ``stale`` holds expired copies of missing symbols for display only."""

    quotes: Dict[str, Quote]
    served_from: Dict[str, str]
    missing: Dict[str, str]
    requests_made: int
    receipt_utc: Optional[datetime.datetime]
    warnings: List[str]
    fetched_utc: Dict[str, datetime.datetime] = field(default_factory=dict)
    stale: Dict[str, Quote] = field(default_factory=dict)


@dataclass(frozen=True)
class ChainCacheResult:
    """What :func:`get_chain` served; ``missing_reason`` is set (and ``options`` empty) when the
    chain could not be served."""

    symbol: str
    expiration: datetime.date
    options: List[Quote]
    served_from: Optional[str]
    missing_reason: Optional[str]
    requests_made: int
    receipt_utc: Optional[datetime.datetime]
    warnings: List[str]
    fetched_utc: Optional[datetime.datetime] = None


# --------------------------------------------------------------------------------------------
# Paths, ledger, budget
# --------------------------------------------------------------------------------------------


def quotes_path(paths: DeskPaths) -> str:
    return os.path.join(paths.cache_dir, QUOTES_FILE)


def ledger_path(paths: DeskPaths) -> str:
    return os.path.join(paths.cache_dir, LEDGER_FILE)


def chain_path(paths: DeskPaths, symbol: str, expiration: datetime.date) -> str:
    return os.path.join(paths.cache_dir, CHAINS_SUBDIR, "%s__%s.json" % (symbol.upper(), expiration.isoformat()))


def ledger_record(receipt: RequestReceipt) -> Dict[str, Any]:
    """The ledger line for one receipt: exactly the six documented keys."""
    return {
        "utc": receipt.receipt_utc,
        "path": receipt.path,
        "symbols": receipt.symbols_count,
        "status": receipt.status,
        "http_status": receipt.http_status,
        "ratelimit_available": receipt.ratelimit_available,
    }


def ledger_hook(paths: DeskPaths, run: Optional[RunCounters] = None) -> Callable[[RequestReceipt], None]:
    """The ``on_request`` callback for the client: appends :func:`ledger_record` to the ledger and,
    when ``run`` is given, records the receipt's instant on it so :func:`requests_counted` can
    count this run's requests even when the window at ``now`` cannot see them."""
    path = ledger_path(paths)

    def record(receipt: RequestReceipt) -> None:
        state.append_jsonl(path, ledger_record(receipt))
        if run is not None:
            run.receipt_stamps.append(receipt.receipt_utc)

    return record


def _parse_utc(text: Any) -> Optional[datetime.datetime]:
    if not isinstance(text, str) or not text:
        return None
    try:
        moment = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def _tail_lines(path: str) -> List[str]:
    """Complete lines from the last :data:`LEDGER_TAIL_BYTES` of ``path`` (``[]`` when missing)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as handle:
            offset = max(0, size - LEDGER_TAIL_BYTES)
            starts_at_line = True
            if offset > 0:
                handle.seek(offset - 1)
                starts_at_line = handle.read(1) == b"\n"
            raw = handle.read()
    except FileNotFoundError:
        return []
    text = raw.decode("utf-8", errors="replace")
    lines = text.split("\n")
    if not starts_at_line:
        lines = lines[1:]  # a mid-file read begins with the fragment of a line
    if lines and lines[-1] != "":
        lines = lines[:-1]  # an unterminated last line is not yet durable
    return [line for line in lines if line.strip()]


def requests_in_window(paths: DeskPaths, now: datetime.datetime, seconds: int = WINDOW_SECONDS) -> int:
    """Ledger records with ``now - seconds < utc <= now`` (every attempt counts, failures too).

    Reads only the ledger's tail; lines that are not JSON objects with a readable ``utc`` are
    skipped (a quota violation is caught by the client's ``quota`` status, so under-counting a
    damaged line is safe; the budget guard is a courtesy to the vendor's limit, not the proof).
    """
    now = _require_utc(now)
    window_start = now - datetime.timedelta(seconds=seconds)
    count = 0
    for line in _tail_lines(ledger_path(paths)):
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue
        stamp = _parse_utc(record.get("utc"))
        if stamp is not None and window_start < stamp <= now:
            count += 1
    return count


def requests_counted(paths: DeskPaths, now: datetime.datetime, run: Optional[RunCounters] = None,
                     seconds: int = WINDOW_SECONDS) -> int:
    """What the budget guard counts at ``now``: the ledger records inside the window (every tool of
    the desk) plus this run's own requests the window cannot see.

    A live run reads ``now`` once at start while the transport stamps each receipt with the wall
    clock, so the run's own receipts fall after ``now`` and :func:`requests_in_window` alone never
    sees them (the guard would let a run spend its whole per-run cap on top of a full window).
    With ``run`` wired into :func:`ledger_hook`, every receipt of this run is counted exactly once:
    those the window already holds are not added again.
    """
    now = _require_utc(now)
    in_window = requests_in_window(paths, now, seconds)
    if run is None:
        return in_window
    window_start = now - datetime.timedelta(seconds=seconds)
    visible = sum(1 for stamp in run.receipt_stamps if window_start < stamp <= now)
    return max(0, in_window - visible) + len(run.receipt_stamps)


def budget_left(paths: DeskPaths, now: datetime.datetime, cfg: CacheConfig, run: Optional[RunCounters] = None) -> int:
    """``minute_budget - minute_headroom - requests_counted``; may be negative."""
    return cfg.minute_budget - cfg.minute_headroom - requests_counted(paths, now, run)


# --------------------------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------------------------


def _require_utc(value: Any, what: str = "now") -> datetime.datetime:
    if not isinstance(value, datetime.datetime):
        raise ValueError("%s must be an aware datetime, got %s" % (what, type(value).__name__))
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        raise ValueError("%s must be timezone-aware (UTC); a naive datetime is ambiguous" % what)
    return value.astimezone(UTC)


def _normalise(symbols: Any) -> List[str]:
    if isinstance(symbols, str) or not isinstance(symbols, (list, tuple)):
        raise ValueError("symbols must be a list or tuple of symbol strings")
    seen: Dict[str, None] = {}
    for symbol in symbols:
        if not isinstance(symbol, str) or not symbol.strip():
            raise ValueError("every symbol must be a non-blank string, got %r" % (symbol,))
        seen.setdefault(symbol.strip().upper(), None)
    return list(seen)


def _client_error_status(exc: BaseException) -> Optional[str]:
    """The client's error status (``TradierError.status``) or ``None`` for anything else (a bug)."""
    status = getattr(exc, "status", None)
    return status if isinstance(status, str) and status else None


def _one_line(exc: BaseException) -> str:
    return " ".join(str(exc).split())[:200]


def _chunks(items: List[str], size: int) -> List[List[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _load_document(path: str, warnings: List[str], what: str) -> Optional[Dict[str, Any]]:
    """The JSON object at ``path`` or ``None`` (missing, quarantined as corrupt, or not schema 1)."""
    document, status = state.read_json(path)
    if status == state.STATUS_CORRUPT:
        warnings.append("%s quarantined (corrupt JSON): refetching" % what)
        return None
    if status == state.STATUS_MISSING:
        return None
    if not isinstance(document, dict) or document.get("schema") != SCHEMA:
        warnings.append("%s ignored: unexpected shape (not schema %d)" % (what, SCHEMA))
        return None
    return document


def _decode_entry(symbol: str, entry: Any, warnings: List[str]) -> Optional[Tuple[Quote, datetime.datetime]]:
    """``(quote, fetched_utc)`` from a cache entry, or ``None`` (with a warning) when it is garbled."""
    if entry is None:
        return None
    if not isinstance(entry, dict) or not isinstance(entry.get("quote"), dict):
        warnings.append("%s cache entry ignored: not an object with a quote; refetching" % symbol)
        return None
    stamp = _parse_utc(entry.get("fetched_utc"))
    if stamp is None:
        warnings.append("%s cache entry ignored: unreadable fetched_utc; refetching" % symbol)
        return None
    try:
        quote = Quote.from_vendor(entry["quote"])
    except MarketTypesError as exc:
        warnings.append("%s cache entry ignored (%s); refetching" % (symbol, _one_line(exc)))
        return None
    if quote.symbol.upper() != symbol:
        warnings.append("%s cache entry ignored: holds %s; refetching" % (symbol, quote.symbol))
        return None
    return quote, stamp


def _store_quotes(paths: DeskPaths, entries: Dict[str, Dict[str, Any]], warnings: List[str]) -> None:
    """Merge ``entries`` into ``quotes.json`` under the desk lock (re-read first, write atomically)."""
    path = quotes_path(paths)
    try:
        with state.locked(paths.lock_path, timeout_s=LOCK_TIMEOUT_S):
            document, status = state.read_json(path)
            if not isinstance(document, dict) or document.get("schema") != SCHEMA or not isinstance(
                    document.get("quotes"), dict):
                document = {"schema": SCHEMA, "quotes": {}}
            document["quotes"].update(entries)
            state.atomic_write_json(path, document)
    except state.LockBusy as exc:
        warnings.append("quote cache not written: %s" % _one_line(exc))


def _store_chain(paths: DeskPaths, symbol: str, expiration: datetime.date, now: datetime.datetime,
                 options: List[Quote], warnings: List[str]) -> None:
    path = chain_path(paths, symbol, expiration)
    raws = [quote.raw for quote in options if isinstance(quote.raw, dict) and quote.raw]
    if len(raws) != len(options):
        warnings.append("chain %s %s not cached: %d contract(s) carry no vendor record"
                        % (symbol, expiration.isoformat(), len(options) - len(raws)))
        return
    document = {"schema": SCHEMA, "symbol": symbol, "expiration": expiration.isoformat(),
                "fetched_utc": state.format_utc(now), "options": raws}
    try:
        with state.locked(paths.lock_path, timeout_s=LOCK_TIMEOUT_S):
            state.atomic_write_json(path, document)
    except state.LockBusy as exc:
        warnings.append("chain cache not written: %s" % _one_line(exc))


def _age_text(now: datetime.datetime, stamp: datetime.datetime) -> str:
    return "%d min" % max(0, int((now - stamp).total_seconds() // 60))


# --------------------------------------------------------------------------------------------
# Quotes
# --------------------------------------------------------------------------------------------


def get_quotes(symbols: Sequence[str], *, now: datetime.datetime, cfg: CacheConfig, client: Any, paths: DeskPaths,
               force: bool = False, allow_fetch: bool = True, run: Optional[RunCounters] = None) -> CacheResult:
    """Serve ``symbols`` from the cache or the client (module notes); never reads the wall clock.

    ``run`` carries the per-run state (requests, chains, the receipts a wired :func:`ledger_hook`
    recorded, a stop reason) across the calls of one tool run; a fresh counter is used when it is
    omitted. Symbols are upper-cased, stripped and de-duplicated; request order is kept in
    ``quotes``; a symbol failing :data:`SYMBOL_RE` is ``fetch_failed:bad_symbol`` on its own and
    nothing is sent for it. ``OSError`` from the cache directory propagates (a wrong or unwritable
    desk root is the tool's exit 1); a busy lock is a warning (the quotes are still served).
    """
    now = _require_utc(now)
    run = RunCounters() if run is None else run
    wanted = _normalise(symbols)
    warnings: List[str] = []
    document = _load_document(quotes_path(paths), warnings, "quote cache")
    cached: Dict[str, Any] = {}
    if document is not None:
        if isinstance(document.get("quotes"), dict):
            cached = document["quotes"]
        else:
            warnings.append("quote cache ignored: unexpected shape (quotes is not an object)")
    ttl = datetime.timedelta(minutes=cfg.ttl_min)

    quotes: Dict[str, Quote] = {}
    served_from: Dict[str, str] = {}
    fetched_utc: Dict[str, datetime.datetime] = {}
    stale: Dict[str, Quote] = {}
    missing: Dict[str, str] = {}
    fallback: Dict[str, Tuple[Quote, datetime.datetime]] = {}
    to_fetch: List[str] = []
    for symbol in wanted:
        if not SYMBOL_RE.fullmatch(symbol):
            missing[symbol] = REASON_BAD_SYMBOL
            warnings.append("%s is not a valid symbol (%s): nothing sent" % (symbol, SYMBOL_RE.pattern))
            continue
        decoded = _decode_entry(symbol, cached.get(symbol), warnings)
        if decoded is not None:
            quote, stamp = decoded
            within_ttl = datetime.timedelta(0) <= now - stamp <= ttl
            if (within_ttl and not force) or not allow_fetch:
                quotes[symbol] = quote
                served_from[symbol] = SERVED_CACHE
                fetched_utc[symbol] = stamp
                if not within_ttl:
                    warnings.append("%s served from a cache copy %s old (TTL %d min): no fetch on a non-trading day"
                                    % (symbol, _age_text(now, stamp), cfg.ttl_min))
                continue
            if within_ttl:
                fallback[symbol] = (quote, stamp)  # a forced refetch: this copy stays valid if it cannot happen
            else:
                stale[symbol] = quote
        to_fetch.append(symbol)

    requests_made = 0
    receipt_utc: Optional[datetime.datetime] = None
    if not allow_fetch:
        for symbol in to_fetch:
            missing[symbol] = REASON_MARKET_CLOSED
        return CacheResult(quotes, served_from, missing, 0, None, warnings, fetched_utc, stale)

    new_entries: Dict[str, Dict[str, Any]] = {}
    cap_warned = budget_warned = stop_warned = False
    chunks = _chunks(to_fetch, MAX_BATCH)
    for index, chunk in enumerate(chunks):
        if run.stop_reason is not None:
            if not stop_warned:
                warnings.append("fetching stopped for this run (%s): %d symbol(s) not fetched"
                                % (run.stop_reason, sum(len(rest) for rest in chunks[index:])))
                stop_warned = True
            for symbol in chunk:
                missing[symbol] = run.stop_reason
            continue
        if run.requests >= cfg.per_run_max_requests:
            if not cap_warned:
                warnings.append("per-run request cap reached (%d): %d symbol(s) not fetched"
                                % (cfg.per_run_max_requests, sum(len(rest) for rest in chunks[index:])))
                cap_warned = True
            for symbol in chunk:
                missing[symbol] = REASON_PER_RUN_CAP
            continue
        if budget_left(paths, now, cfg, run) <= 0:
            if not budget_warned:
                warnings.append("60-second request budget exhausted (%d allowed minus %d headroom): no fetch"
                                % (cfg.minute_budget, cfg.minute_headroom))
                budget_warned = True
            for symbol in chunk:
                missing[symbol] = REASON_QUOTA_GUARD
            continue
        try:
            result = client.quotes(chunk)
        except Exception as exc:  # noqa: BLE001 - only the client's own errors are handled
            status = _client_error_status(exc)
            if status is None:
                raise
            if status not in NO_SEND_STATUSES:  # a refusal before sending is not a request made
                run.requests += 1
                requests_made += 1
            reason = REASON_QUOTA_GUARD if status == STATUS_QUOTA else FETCH_FAILED_PREFIX + status
            warnings.append("fetch failed for %d symbol(s): %s" % (len(chunk), _one_line(exc)))
            for symbol in chunk:
                missing[symbol] = reason
            if status == STATUS_QUOTA or status in STOP_STATUSES:
                run.stop_reason = reason
            continue
        run.requests += 1
        requests_made += 1
        receipt = getattr(result, "receipt", None)
        if isinstance(receipt, RequestReceipt):
            receipt_utc = receipt.receipt_utc
        matched = {quote.symbol.upper(): quote for quote in dict(result.quotes).values()}
        unmatched = {str(item).upper() for item in list(result.unmatched)}
        for symbol in chunk:
            quote = matched.get(symbol)
            if quote is not None:
                quotes[symbol] = quote
                served_from[symbol] = SERVED_FETCH
                fetched_utc[symbol] = now
                stale.pop(symbol, None)
                if isinstance(quote.raw, dict) and quote.raw:
                    new_entries[symbol] = {"fetched_utc": state.format_utc(now), "quote": quote.raw}
                else:
                    warnings.append("%s not cached: the client returned no vendor record" % symbol)
            elif symbol in unmatched:
                missing[symbol] = REASON_UNMATCHED
                stale.pop(symbol, None)  # the symbol no longer exists (an expired contract)
            else:
                missing[symbol] = REASON_NOT_RETURNED
                warnings.append("%s absent from the vendor response (neither matched nor unmatched)" % symbol)
    for symbol, (quote, stamp) in fallback.items():
        reason = missing.get(symbol)
        if reason is None or reason == REASON_UNMATCHED:
            continue  # refreshed, or the symbol no longer exists
        del missing[symbol]
        quotes[symbol] = quote
        served_from[symbol] = SERVED_CACHE
        fetched_utc[symbol] = stamp
        warnings.append("%s served from its cache copy %s old (TTL %d min): the forced refetch did not happen (%s)"
                        % (symbol, _age_text(now, stamp), cfg.ttl_min, reason))
    if new_entries:
        _store_quotes(paths, new_entries, warnings)
    ordered = {symbol: quotes[symbol] for symbol in wanted if symbol in quotes}
    return CacheResult(ordered, served_from, missing, requests_made, receipt_utc, warnings, fetched_utc, stale)


# --------------------------------------------------------------------------------------------
# Chains
# --------------------------------------------------------------------------------------------


def _load_chain(paths: DeskPaths, symbol: str, expiration: datetime.date,
                warnings: List[str]) -> Optional[Tuple[List[Quote], datetime.datetime]]:
    label = "chain cache %s %s" % (symbol, expiration.isoformat())
    document = _load_document(chain_path(paths, symbol, expiration), warnings, label)
    if document is None:
        return None
    stamp = _parse_utc(document.get("fetched_utc"))
    raws = document.get("options")
    if (document.get("symbol") != symbol or document.get("expiration") != expiration.isoformat()
            or stamp is None or not isinstance(raws, list)):
        warnings.append("%s ignored: unexpected contents; refetching" % label)
        return None
    options: List[Quote] = []
    for raw in raws:
        try:
            options.append(Quote.from_vendor(raw))
        except MarketTypesError as exc:
            warnings.append("%s ignored (%s); refetching" % (label, _one_line(exc)))
            return None
    return options, stamp


def get_chain(symbol: str, expiration: datetime.date, *, now: datetime.datetime, cfg: CacheConfig, client: Any,
              paths: DeskPaths, force: bool = False, allow_fetch: bool = True,
              run: Optional[RunCounters] = None) -> ChainCacheResult:
    """Serve one option chain from ``chains/`` or the client, with the same TTL, budget guard,
    ``force`` and stop semantics and error vocabulary as :func:`get_quotes` plus the per-run chain
    cap (``per_run_max_chains``). A chain fetch also counts against ``per_run_max_requests``. An
    empty chain (the vendor's ``{"options": null}``: expired or unlisted) is served and cached with
    a warning."""
    now = _require_utc(now)
    if not isinstance(expiration, datetime.date) or isinstance(expiration, datetime.datetime):
        raise ValueError("expiration must be a datetime.date, got %r" % (expiration,))
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-blank string, got %r" % (symbol,))
    symbol = symbol.strip().upper()
    run = RunCounters() if run is None else run
    warnings: List[str] = []
    label = "chain %s %s" % (symbol, expiration.isoformat())
    if not SYMBOL_RE.fullmatch(symbol):
        warnings.append("%s is not a valid symbol (%s): nothing sent" % (symbol, SYMBOL_RE.pattern))
        return ChainCacheResult(symbol, expiration, [], None, REASON_BAD_SYMBOL, 0, None, warnings)
    ttl = datetime.timedelta(minutes=cfg.ttl_min)
    cached = _load_chain(paths, symbol, expiration, warnings)
    fallback: Optional[Tuple[List[Quote], datetime.datetime]] = None
    if cached is not None:
        options, stamp = cached
        within_ttl = datetime.timedelta(0) <= now - stamp <= ttl
        if (within_ttl and not force) or not allow_fetch:
            if not within_ttl:
                warnings.append("%s served from a cache copy %s old (TTL %d min): no fetch on a non-trading day"
                                % (label, _age_text(now, stamp), cfg.ttl_min))
            return ChainCacheResult(symbol, expiration, options, SERVED_CACHE, None, 0, None, warnings, stamp)
        if within_ttl:
            fallback = cached  # a forced refetch: this copy stays valid if it cannot happen

    def not_fetched(reason: str, requests_made: int = 0) -> ChainCacheResult:
        if fallback is not None:
            options, stamp = fallback
            warnings.append("%s served from its cache copy %s old (TTL %d min): the forced refetch did not happen (%s)"
                            % (label, _age_text(now, stamp), cfg.ttl_min, reason))
            return ChainCacheResult(symbol, expiration, options, SERVED_CACHE, None, requests_made, None, warnings, stamp)
        return ChainCacheResult(symbol, expiration, [], None, reason, requests_made, None, warnings)

    if not allow_fetch:
        return not_fetched(REASON_MARKET_CLOSED)
    if run.stop_reason is not None:
        warnings.append("%s not fetched: fetching stopped for this run (%s)" % (label, run.stop_reason))
        return not_fetched(run.stop_reason)
    if run.chains >= cfg.per_run_max_chains or run.requests >= cfg.per_run_max_requests:
        warnings.append("per-run cap reached (%d chains, %d requests): %s not fetched"
                        % (cfg.per_run_max_chains, cfg.per_run_max_requests, label))
        return not_fetched(REASON_PER_RUN_CAP)
    if budget_left(paths, now, cfg, run) <= 0:
        warnings.append("60-second request budget exhausted (%d allowed minus %d headroom): chain not fetched"
                        % (cfg.minute_budget, cfg.minute_headroom))
        return not_fetched(REASON_QUOTA_GUARD)
    try:
        result = client.option_chain(symbol, expiration)
    except Exception as exc:  # noqa: BLE001 - only the client's own errors are handled
        status = _client_error_status(exc)
        if status is None:
            raise
        sent = status not in NO_SEND_STATUSES  # a refusal before sending is not a request made
        if sent:
            run.requests += 1
            run.chains += 1
        reason = REASON_QUOTA_GUARD if status == STATUS_QUOTA else FETCH_FAILED_PREFIX + status
        if status == STATUS_QUOTA or status in STOP_STATUSES:
            run.stop_reason = reason
        warnings.append("chain fetch failed for %s %s: %s" % (symbol, expiration.isoformat(), _one_line(exc)))
        return not_fetched(reason, 1 if sent else 0)
    run.requests += 1
    run.chains += 1
    receipt = getattr(result, "receipt", None)
    receipt_utc = receipt.receipt_utc if isinstance(receipt, RequestReceipt) else None
    options = list(result.options)
    if not options:
        warnings.append("%s is empty: no listed contracts (expired or not listed)" % label)
    _store_chain(paths, symbol, expiration, now, options, warnings)
    return ChainCacheResult(symbol, expiration, options, SERVED_FETCH, None, 1, receipt_utc, warnings, now)


# --------------------------------------------------------------------------------------------
# Tool plumbing shared by scripts/quote_cache.py and scripts/quote_qc.py
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class MarketData:
    """Everything a market-data tool needs after :func:`open_market_data`."""

    paths: DeskPaths
    cfg: DeskConfig
    cache: CacheConfig
    client: Any
    session: SessionInfo
    now: datetime.datetime
    bound_min: int
    prior_close_utc: datetime.datetime
    fixture_set: Optional[str]
    run: RunCounters


def build_client(cfg: DeskConfig, paths: DeskPaths, *, offline: bool, fixture_set: str, timeout_s: float,
                 now: datetime.datetime, user_agent: str, run: Optional[RunCounters] = None) -> Any:
    """The ``TradierClient`` for ``cfg`` (module notes); raises :class:`MarketDataRefused`.

    The production host is refused before the network module is even imported. Offline, the
    client serves ``paths.tradier_fixtures_dir/<fixture_set>`` with ``now`` as every receipt's
    instant and needs no token. Online, the token comes from ``load_token`` (environment variable
    first, then the file); a missing token is ``no_token``, a loosely-permissioned file
    ``token_file_mode`` (the client's message, which never contains the token). The client's
    ``on_request`` is :func:`ledger_hook` wired to ``run`` when one is given, so the budget guard
    counts this run's own requests.
    """
    market = cfg.market_data
    if market.host == "production":
        raise MarketDataRefused(REFUSED_PRODUCTION, PRODUCTION_REFUSED_MESSAGE)
    from . import tradier_client  # the only network module; imported here so the cache's own tests need no client

    hook = ledger_hook(paths, run)
    if offline:
        transport = tradier_client.OfflineTransport(paths.tradier_fixtures_dir, fixture_set=fixture_set, now=now)
        return tradier_client.TradierClient(market.host, None, timeout_s=timeout_s, user_agent=user_agent,
                                            transport=transport, on_request=hook)
    try:
        token = tradier_client.load_token(market.token_env, market.token_file)
    except tradier_client.TradierError as exc:
        if exc.status == REFUSED_NO_TOKEN:
            raise MarketDataRefused(REFUSED_NO_TOKEN, NO_TOKEN_MESSAGE) from None
        raise MarketDataRefused(exc.status, str(exc)) from None
    return tradier_client.TradierClient(market.host, token, timeout_s=timeout_s, user_agent=user_agent,
                                        on_request=hook)


def open_market_data(desk_root: str, *, now: datetime.datetime, offline: bool, fixture_set: Optional[str],
                     timeout_s: float, user_agent: str) -> MarketData:
    """Resolve the desk, load and check ``desk.toml``, classify ``now``, build the client, create
    the runtime directories (module notes). Raises ``DeskPathsError``, ``DeskConfigError``,
    ``market_clock.TableExpiredError`` or :class:`MarketDataRefused`; ``OSError`` when a runtime
    directory cannot be created (a file in its place); nothing is written before every check has
    passed. ``MarketData.fixture_set`` names the set an offline run serves (``regular`` when none
    was asked for) and is ``None`` online."""
    now = _require_utc(now)
    paths = desk_paths.for_root(desk_root)
    cfg = desk_config.load_desk_config(paths.desk_toml)
    session = market_clock.session_at(now)
    prior_close = market_clock.previous_close_utc(now)
    run = RunCounters()
    chosen_set = fixture_set or DEFAULT_FIXTURE_SET
    client = build_client(cfg, paths, offline=offline, fixture_set=chosen_set, timeout_s=timeout_s, now=now,
                          user_agent=user_agent, run=run)
    desk_paths.ensure_runtime_dirs(paths)
    return MarketData(paths, cfg, CacheConfig.from_desk(cfg), client, session, now,
                      desk_config.freshness_bound_min(cfg), prior_close, chosen_set if offline else None, run)


def source_label(cfg: DeskConfig) -> str:
    """The envelope's ``source``: ``tradier:sandbox``."""
    return "%s:%s" % (cfg.market_data.vendor, cfg.market_data.host)


def footer_line(cfg: DeskConfig, as_of: datetime.datetime) -> str:
    """``Tradier sandbox, delayed 15 min, as of HH:MM UTC`` (the fixed data footer)."""
    market = cfg.market_data
    return "%s %s, delayed %d min, as of %s UTC" % (market.vendor.capitalize(), market.host, market.feed_delay_min,
                                                  as_of.astimezone(UTC).strftime("%H:%M"))


def format_money(value: Optional[Decimal]) -> str:
    """A price for prose: at least two decimals (``648.1`` -> ``648.10``), never fewer digits than
    the vendor sent (``3.425`` stays), ``-`` for ``None``."""
    if value is None:
        return "-"
    quantised = value.quantize(Decimal("0.01"))
    return str(quantised) if quantised == value else str(value)


def age_minutes(now: datetime.datetime, stamp: Optional[datetime.datetime]) -> Optional[int]:
    """Whole minutes from ``stamp`` to ``now`` (floored, never negative); ``None`` when undated."""
    if stamp is None:
        return None
    return max(0, int((now - stamp).total_seconds() // 60))


# --------------------------------------------------------------------------------------------
# History (PR 2b): daily bars for the settlement inference of csv_recon
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HistoryCacheResult:
    """What :func:`get_history` served; ``missing_reason`` is set when the bars could not be served
    (``no_bars`` means the vendor answered without bars, served and cached under the TTL)."""

    symbol: str
    start: datetime.date
    end: datetime.date
    bars: List[Bar]
    served_from: Optional[str]
    missing_reason: Optional[str]
    requests_made: int
    receipt_utc: Optional[datetime.datetime]
    warnings: List[str]
    fetched_utc: Optional[datetime.datetime] = None


def history_path(paths: DeskPaths, symbol: str, start: datetime.date, end: datetime.date) -> str:
    return os.path.join(paths.cache_dir, HISTORY_SUBDIR,
                        "%s__%s__%s.json" % (symbol.strip().upper(), start.isoformat(), end.isoformat()))


def _require_date(value: Any, what: str) -> datetime.date:
    if not isinstance(value, datetime.date) or isinstance(value, datetime.datetime):
        raise ValueError("%s must be a datetime.date, got %r" % (what, value))
    return value


def _load_history(paths: DeskPaths, symbol: str, start: datetime.date, end: datetime.date,
                  warnings: List[str]) -> Optional[Tuple[List[Bar], datetime.datetime]]:
    label = "history cache %s %s..%s" % (symbol, start.isoformat(), end.isoformat())
    document = _load_document(history_path(paths, symbol, start, end), warnings, label)
    if document is None:
        return None
    stamp = _parse_utc(document.get("fetched_utc"))
    raws = document.get("bars")
    if (document.get("symbol") != symbol or document.get("start") != start.isoformat()
            or document.get("end") != end.isoformat() or stamp is None or not isinstance(raws, list)):
        warnings.append("%s ignored: unexpected contents; refetching" % label)
        return None
    bars: List[Bar] = []
    for raw in raws:
        try:
            bars.append(Bar.from_vendor(raw))
        except MarketTypesError as exc:
            warnings.append("%s ignored (%s); refetching" % (label, _one_line(exc)))
            return None
    return bars, stamp


def _store_history(paths: DeskPaths, symbol: str, start: datetime.date, end: datetime.date, now: datetime.datetime,
                   bars: List[Bar], warnings: List[str]) -> None:
    raws = [bar.raw for bar in bars if isinstance(bar.raw, dict) and bar.raw]
    if len(raws) != len(bars):
        warnings.append("history %s %s..%s not cached: %d bar(s) carry no vendor record"
                        % (symbol, start.isoformat(), end.isoformat(), len(bars) - len(raws)))
        return
    document = {"schema": SCHEMA, "symbol": symbol, "start": start.isoformat(), "end": end.isoformat(),
                "fetched_utc": state.format_utc(now), "bars": raws}
    try:
        with state.locked(paths.lock_path, timeout_s=LOCK_TIMEOUT_S):
            state.atomic_write_json(history_path(paths, symbol, start, end), document)
    except state.LockBusy as exc:
        warnings.append("history cache not written: %s" % _one_line(exc))


def get_history(symbol: str, start: datetime.date, end: datetime.date, *, now: datetime.datetime, cfg: CacheConfig,
                client: Any, paths: DeskPaths, run: Optional[RunCounters] = None) -> HistoryCacheResult:
    """Serve the daily bars of ``symbol`` from ``start`` to ``end`` (inclusive) from ``history/``
    or the client (module notes).

    A cached answer with bars is final when it was fetched on a later ET date than ``end``
    (every session of the range had closed: closed days never change) and is served without any
    TTL; any other cached answer (a range that reaches today, or an empty answer) follows the
    quote TTL and an expired copy is refetched, never served. There is no ``allow_fetch``: history
    is fetched on non-trading days too. The budget guard, the per-run request cap (a history
    request counts against ``per_run_max_requests``; it is not a chain), the run's stop reason,
    the ``quota`` / credential stop rules and the no-send statuses are those of :func:`get_chain`
    and :func:`get_quotes`. An empty answer is ``bars == []`` with ``missing_reason``
    ``no_bars`` and is cached. ``OSError`` from the cache directory propagates.
    """
    now = _require_utc(now)
    start = _require_date(start, "start")
    end = _require_date(end, "end")
    if end < start:
        raise ValueError("end %s is before start %s" % (end.isoformat(), start.isoformat()))
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-blank string, got %r" % (symbol,))
    symbol = symbol.strip().upper()
    run = RunCounters() if run is None else run
    warnings: List[str] = []
    label = "history %s %s..%s" % (symbol, start.isoformat(), end.isoformat())
    if not SYMBOL_RE.fullmatch(symbol):
        warnings.append("%s is not a valid symbol (%s): nothing sent" % (symbol, SYMBOL_RE.pattern))
        return HistoryCacheResult(symbol, start, end, [], None, REASON_BAD_SYMBOL, 0, None, warnings)
    ttl = datetime.timedelta(minutes=cfg.ttl_min)
    cached = _load_history(paths, symbol, start, end, warnings)
    if cached is not None:
        bars, stamp = cached
        final = bool(bars) and end < market_clock.to_et(stamp).date()
        within_ttl = datetime.timedelta(0) <= now - stamp <= ttl
        if final or within_ttl:
            if not bars:
                warnings.append("%s: the cached answer holds no bars" % label)
            return HistoryCacheResult(symbol, start, end, bars, SERVED_CACHE, None if bars else REASON_NO_BARS,
                                      0, None, warnings, stamp)

    def not_fetched(reason: str, requests_made: int = 0) -> HistoryCacheResult:
        return HistoryCacheResult(symbol, start, end, [], None, reason, requests_made, None, warnings)

    if run.stop_reason is not None:
        warnings.append("%s not fetched: fetching stopped for this run (%s)" % (label, run.stop_reason))
        return not_fetched(run.stop_reason)
    if run.requests >= cfg.per_run_max_requests:
        warnings.append("per-run request cap reached (%d): %s not fetched" % (cfg.per_run_max_requests, label))
        return not_fetched(REASON_PER_RUN_CAP)
    if budget_left(paths, now, cfg, run) <= 0:
        warnings.append("60-second request budget exhausted (%d allowed minus %d headroom): history not fetched"
                        % (cfg.minute_budget, cfg.minute_headroom))
        return not_fetched(REASON_QUOTA_GUARD)
    try:
        result = client.history(symbol, start, end)
    except Exception as exc:  # noqa: BLE001 - only the client's own errors are handled
        status = _client_error_status(exc)
        if status is None:
            raise
        sent = status not in NO_SEND_STATUSES  # a refusal before sending is not a request made
        if sent:
            run.requests += 1
        reason = REASON_QUOTA_GUARD if status == STATUS_QUOTA else FETCH_FAILED_PREFIX + status
        if status == STATUS_QUOTA or status in STOP_STATUSES:
            run.stop_reason = reason
        warnings.append("history fetch failed for %s: %s" % (label, _one_line(exc)))
        return not_fetched(reason, 1 if sent else 0)
    run.requests += 1
    receipt = getattr(result, "receipt", None)
    receipt_utc = receipt.receipt_utc if isinstance(receipt, RequestReceipt) else None
    bars = list(result.bars)
    if not bars:
        warnings.append("%s is empty: the vendor returned no bars" % label)
    _store_history(paths, symbol, start, end, now, bars, warnings)
    return HistoryCacheResult(symbol, start, end, bars, SERVED_FETCH, None if bars else REASON_NO_BARS, 1, receipt_utc,
                              warnings, now)

# --------------------------------------------------------------------------------------------
# Expirations (PR 3b): the listed expirations of one underlying, for the Structuring Desk
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class ExpirationsCacheResult:
    """What :func:`get_expirations` served; ``missing_reason`` is set (and ``dates`` empty) when the list could not be
    served. ``dates`` keep the vendor's order; an empty list served is the vendor's empty answer."""

    symbol: str
    dates: List[datetime.date]
    served_from: Optional[str]
    missing_reason: Optional[str]
    requests_made: int
    receipt_utc: Optional[datetime.datetime]
    warnings: List[str]
    fetched_utc: Optional[datetime.datetime] = None


def expirations_path(paths: DeskPaths, symbol: str) -> str:
    """``data/cache/expirations/<SYMBOL>.json`` under the desk's cache directory."""
    return os.path.join(paths.cache_dir, EXPIRATIONS_SUBDIR, "%s.json" % symbol.strip().upper())


def _load_expirations(paths: DeskPaths, symbol: str,
                      warnings: List[str]) -> Optional[Tuple[List[datetime.date], datetime.datetime]]:
    label = "expirations cache %s" % symbol
    document = _load_document(expirations_path(paths, symbol), warnings, label)
    if document is None:
        return None
    stamp = _parse_utc(document.get("fetched_utc"))
    raws = document.get("dates")
    dates: List[datetime.date] = []
    readable = document.get("symbol") == symbol and stamp is not None and isinstance(raws, list)
    for raw in raws if readable else []:
        try:
            if not isinstance(raw, str) or not _ISO_DAY_RE.match(raw):
                raise ValueError("not YYYY-MM-DD")
            dates.append(datetime.date.fromisoformat(raw))
        except ValueError:
            readable = False
            break
    if not readable or stamp is None:
        warnings.append("%s ignored: unexpected contents; refetching" % label)
        return None
    return dates, stamp


def _store_expirations(paths: DeskPaths, symbol: str, now: datetime.datetime, dates: List[datetime.date],
                       warnings: List[str]) -> None:
    document = {"schema": SCHEMA, "symbol": symbol, "fetched_utc": state.format_utc(now),
                "dates": [day.isoformat() for day in dates]}
    try:
        with state.locked(paths.lock_path, timeout_s=LOCK_TIMEOUT_S):
            state.atomic_write_json(expirations_path(paths, symbol), document)
    except state.LockBusy as exc:
        warnings.append("expirations cache not written: %s" % _one_line(exc))


def get_expirations(symbol: str, *, now: datetime.datetime, cfg: CacheConfig, client: Any, paths: DeskPaths,
                    force: bool = False, allow_fetch: bool = True,
                    run: Optional[RunCounters] = None) -> ExpirationsCacheResult:
    """Serve the listed expirations of ``symbol`` from ``expirations/`` or the client (module notes).

    A cached copy fetched on the ET date of ``now`` (and not after ``now``) is served without a refetch: an
    expiration list does not change within a session, so the quote TTL does not apply. ``force`` asks for a fresh
    list and keeps that copy when the refetch cannot happen or fails; ``allow_fetch=False`` (a non-trading day) serves
    any cached copy, with a warning when it is from another ET date, and reports ``market_closed`` without one. The
    budget guard, the run's stop reason, the ``quota`` / credential stop rules, the no-send statuses and the warning
    words are :func:`get_chain`'s; the request counts against ``per_run_max_requests`` and never against
    ``per_run_max_chains``; every request the client sent is in the ledger through the client's :func:`ledger_hook`.
    An empty answer is served and cached with a warning. ``OSError`` from the cache directory propagates.
    """
    now = _require_utc(now)
    if not isinstance(symbol, str) or not symbol.strip():
        raise ValueError("symbol must be a non-blank string, got %r" % (symbol,))
    symbol = symbol.strip().upper()
    run = RunCounters() if run is None else run
    warnings: List[str] = []
    label = "expirations %s" % symbol
    if not SYMBOL_RE.fullmatch(symbol):
        warnings.append("%s is not a valid symbol (%s): nothing sent" % (symbol, SYMBOL_RE.pattern))
        return ExpirationsCacheResult(symbol, [], None, REASON_BAD_SYMBOL, 0, None, warnings)
    cached = _load_expirations(paths, symbol, warnings)
    fallback: Optional[Tuple[List[datetime.date], datetime.datetime]] = None
    if cached is not None:
        dates, stamp = cached
        same_day = stamp <= now and market_clock.to_et(stamp).date() == market_clock.to_et(now).date()
        if (same_day and not force) or not allow_fetch:
            if not same_day:
                warnings.append("%s served from a cache copy %s old (fetched on %s ET): no fetch on a non-trading day"
                                % (label, _age_text(now, stamp), market_clock.to_et(stamp).date().isoformat()))
            return ExpirationsCacheResult(symbol, dates, SERVED_CACHE, None, 0, None, warnings, stamp)
        if same_day:
            fallback = cached  # a forced refetch: this copy stays valid if it cannot happen

    def not_fetched(reason: str, requests_made: int = 0) -> ExpirationsCacheResult:
        if fallback is not None:
            dates, stamp = fallback
            warnings.append("%s served from its cache copy %s old (fetched today): the forced refetch did not happen "
                            "(%s)" % (label, _age_text(now, stamp), reason))
            return ExpirationsCacheResult(symbol, dates, SERVED_CACHE, None, requests_made, None, warnings, stamp)
        return ExpirationsCacheResult(symbol, [], None, reason, requests_made, None, warnings)

    if not allow_fetch:
        return not_fetched(REASON_MARKET_CLOSED)
    if run.stop_reason is not None:
        warnings.append("%s not fetched: fetching stopped for this run (%s)" % (label, run.stop_reason))
        return not_fetched(run.stop_reason)
    if run.requests >= cfg.per_run_max_requests:
        warnings.append("per-run request cap reached (%d): %s not fetched" % (cfg.per_run_max_requests, label))
        return not_fetched(REASON_PER_RUN_CAP)
    if budget_left(paths, now, cfg, run) <= 0:
        warnings.append("60-second request budget exhausted (%d allowed minus %d headroom): expirations not fetched"
                        % (cfg.minute_budget, cfg.minute_headroom))
        return not_fetched(REASON_QUOTA_GUARD)
    try:
        result = client.option_expirations(symbol)
    except Exception as exc:  # noqa: BLE001 - only the client's own errors are handled
        status = _client_error_status(exc)
        if status is None:
            raise
        sent = status not in NO_SEND_STATUSES  # a refusal before sending is not a request made
        if sent:
            run.requests += 1
        reason = REASON_QUOTA_GUARD if status == STATUS_QUOTA else FETCH_FAILED_PREFIX + status
        if status == STATUS_QUOTA or status in STOP_STATUSES:
            run.stop_reason = reason
        warnings.append("expirations fetch failed for %s: %s" % (symbol, _one_line(exc)))
        return not_fetched(reason, 1 if sent else 0)
    run.requests += 1
    receipt = getattr(result, "receipt", None)
    receipt_utc = receipt.receipt_utc if isinstance(receipt, RequestReceipt) else None
    dates = list(result.dates)
    if not dates:
        warnings.append("%s is empty: no listed expirations" % label)
    _store_expirations(paths, symbol, now, dates, warnings)
    return ExpirationsCacheResult(symbol, dates, SERVED_FETCH, None, 1, receipt_utc, warnings, now)

__all__ = [
    "CHAINS_SUBDIR",
    "CacheConfig",
    "CacheResult",
    "ChainCacheResult",
    "DEFAULT_FIXTURE_SET",
    "EXPIRATIONS_SUBDIR",
    "ExpirationsCacheResult",
    "FETCH_FAILED_PREFIX",
    "HISTORY_SUBDIR",
    "HistoryCacheResult",
    "LEDGER_FILE",
    "LEDGER_TAIL_BYTES",
    "LOCK_TIMEOUT_S",
    "MAX_BATCH",
    "MarketData",
    "MarketDataRefused",
    "NO_SEND_STATUSES",
    "NO_TOKEN_MESSAGE",
    "PRODUCTION_REFUSED_MESSAGE",
    "QUOTES_FILE",
    "REASON_BAD_SYMBOL",
    "REASON_MARKET_CLOSED",
    "REASON_NO_BARS",
    "REASON_NOT_RETURNED",
    "REASON_PER_RUN_CAP",
    "REASON_QUOTA_GUARD",
    "REASON_UNMATCHED",
    "REFUSED_NO_TOKEN",
    "REFUSED_PRODUCTION",
    "RunCounters",
    "SCHEMA",
    "SERVED_CACHE",
    "SERVED_FETCH",
    "STOP_STATUSES",
    "SYMBOL_RE",
    "WINDOW_SECONDS",
    "age_minutes",
    "budget_left",
    "build_client",
    "chain_path",
    "expirations_path",
    "footer_line",
    "format_money",
    "get_chain",
    "get_expirations",
    "get_history",
    "get_quotes",
    "history_path",
    "ledger_hook",
    "ledger_path",
    "ledger_record",
    "open_market_data",
    "quotes_path",
    "requests_counted",
    "requests_in_window",
    "source_label",
]
