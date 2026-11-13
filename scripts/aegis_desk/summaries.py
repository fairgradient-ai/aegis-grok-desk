"""The Morning Desk Sheet and the EOD Desk Brief: one linted message each, composed from the desk's own records.

Plan section 11 (the 07:30 ``preopen`` and the 17:10 ``eod-brief`` routines), PR 2d decisions D1-13 to D1-15 with the
addendum's overrides O3, O5, O12 and O15; standard library only, no I/O, no clock. ``scripts/eod_summary.py`` reads
the records and hands them over (:class:`MorningInputs`, :class:`BriefInputs`); this module only composes, so every
line here is a function of its inputs and the model that relays the message never does arithmetic: every number is
printed by one formatter of :mod:`aegis_desk.alerts` (``fmt_money`` on the record's own Decimal, ``fmt_count``,
``fmt_days``, ``fmt_minutes``, ``fmt_reason`` for a sentence the desk's own code wrote) and NOTHING is summed: a
total the records do not hold is not printed (the brief's per-trade day P/L is the snapshot's own per-leg values).

Records and their labels (:func:`make_artifact` checks each one's shape and its own date)
-----------------------------------------------------------------------------------------
* ``book snapshot`` ``data/book/<D>.json`` (brief): the snapshot of record ``book.py --snapshot`` writes, read back
  through ``exit_rules.book_from_json``; its date is the ET date of ``generated_at``.
* ``reconciliation`` ``data/positions/<D>.recon.json``: ``csv_recon.py``'s record (decision D1-11); only
  ``payload.verdict``, ``payload.et_date`` (its date), ``payload.counts.edits``, ``payload.counts.unavailable`` and
  ``generated_at`` are read, never an edit's CSV line or any text, which carry owner-typed cells.
* ``expiry docket`` ``reports/expiry/<D>.week.json``: ``expiry_checklist.py --week``'s record (D1-12); its date is
  ``payload.today``; the legs' ``trade_id``, ``symbol``, ``side``, ``contracts``, ``underlying``, ``structure``,
  ``last_trading_day``, ``dte_trading`` and ``act_by_text`` are read.
* ``levels sheet`` ``levels/<previous trading day>.json`` (morning): ``trigger_levels.py``'s payload; its date is
  ``et_date``; each group's ``price_levels`` and ``resting_eligible`` counts are read.
* ``doctor result`` ``state/doctor.json`` (morning): ``desk_doctor.py``'s record (D2-12); its date is the ET date of
  ``run_utc`` (the record's instant); ``mode``, ``verdict`` and each check's ``id`` and ``status`` are read, never a
  reason.

A record is ``ok``; ``missing`` (``UNAVAILABLE: <label> <path> missing``); ``late`` (``UNAVAILABLE: <label> is from
<date> (upstream late)``: the expected file is absent but an older one of the same kind exists, or the file's own
date is not the expected one); ``corrupt`` (``UNAVAILABLE: <label> <path> cannot be read (moved aside)``: not JSON,
quarantined by the state layer) or ``invalid`` (``UNAVAILABLE: <label> <path> cannot be read (not the record the
desk writes)``). The EOD brief reads the expiry docket only when it is there: an absent one prints ``expiry docket:
none recorded today (<path> missing)`` and stays available.

The blocks (exact shapes; decisions D1-14, O5, O12)
---------------------------------------------------
Both messages are a desk view in markdown a chat renders as one bubble: a ``###`` title, then bold section titles a
blank line apart, each with ``- `` items whose details are nested ``  - `` sub-items, then the records read in
italics (``*read from <path>, <path>*``); no table. A gain carries its ``+`` (:meth:`Composer.signed`).
Morning (``### MORNING DESK SHEET <D>``), one ``**Desk status**`` section: ``- **Doctor** <PASS|FAIL>, <n>
warnings[: <check ids>][; <m> failures: <check ids>] (<mode> check at HH:MM UTC)``; ``- **positions.csv**
<VERDICT>, <n> proposed edits (`csv_recon.py --diff` lists them)[, <u> unavailable items (`csv_recon.py` shows
them)]``; ``- **Open signals** <n>`` and a sub-item per open signal; ``- **Expiry docket** <n> contracts due[, <u>
unavailable (`expiry_checklist.py --week` shows why)]`` with a ``<trade_id> <OCC symbol> <long|short> <contracts> |
<UNDERLYING> <structure words> | last trading day <date> (<n> trading days) | <act-by text>`` sub-item per contract
(the legs of one group can act by different times: a short leg by the session close, a long one by the broker cutoff,
so each names its contract); on an early-close day (O5) ``- **Early close** today at 13:00 ET: <n> contracts on their
last trading day; <act-by text>``; and ``- **Last night's levels**`` with a ``<trade_id> <r> resting-eligible of <p>
price levels`` sub-item per group.
Brief (``### EOD DESK BRIEF <D>``): ``**P/L**`` with ``- **Unrealized** <m> at the mids (<m> at liquidation)``, ``-
**Today** <m>`` (a null day prints the book's own reason, ``unavailable: ...``) and ``- **Realized** <m> this year
(<m> today, <m> this month)`` (a null prints ``unavailable``) and, when the book's totals leave groups out, ``- totals
without <id> (data unavailable); the day total also without <id> (an open leg has no day P/L)``, the ids read from the
book's own exclusion warnings, never their reasons, which may carry an account name the owner typed; ``**By trade**``
with, per open group in snapshot order (O12), ``- **<UNDERLYING> <structure words>** <m> at the mids (<m> at
liquidation)`` and the sub-item ``*<trade_id or ROW-n> | today <day>[ | data unavailable]*``, whose day is the open
leg's own day P/L, or each open leg's with its row (``today +$84.00 (row 3), -$50.00 (row 4)``: the snapshot keeps no
group total and the desk never adds one; ``today unavailable`` when no open leg has one), and ``- **<id>**
quarantined: <the desk's own words>`` for a quarantined group; ``**Desk status**`` with ``- **Signals today** <n>
(<a> EXIT SIGNAL, <b> WATCH, <c> UNAVAILABLE)``, the open signals, the reconciliation and docket items, ``-
**Routines** <ran> of <expected> ran today`` (``, none late or missed`` when so) with the sub-items ``late: <id> HH:MM
ET (<n> min late)``, ``missed: <id> HH:MM ET`` and ``pending: <id> HH:MM ET`` of the lists that are not empty (the
day audit of ``aegis_desk.routines``, the brief's own routine excluded, O15), ``- **Incidents today** <n> (<routine>
<step>, ...); details in `incidents/``` when the day's ``incidents/`` holds files (names only, never their contents;
O8: a failed 10:05 levels refresh is an incident file the brief names); and ``**To do**`` with one item per task (each
open signal ``not acknowledged (`ack.py show <SIG>`)``, the proposed CSV edits, CONFIG CHANGED pending, the calendar
findings), or ``**To do** nothing pending``. An open signal reads ``<SIG> | <trade_id> <structure words> | <EXIT
SIGNAL|WATCH|UNAVAILABLE> | your plan: <plan words> | first posted <date> HH:MM UTC | <posts> | not acknowledged`` (or
``snoozed until <date> HH:MM UTC``), the posts being ``posted <p> of 4 times; next re-post <k> runs after the first
post``, ``...; the re-post ladder is done``, ``posted <p> times; no re-post ladder for WATCH`` or ``posted <p> times;
re-posted on every run until acknowledged``. A journal with unreadable lines adds an item ``<file> has lines that do
not parse: they were skipped (the file is kept as it is)``; an UNAVAILABLE line is an item of the section it stands
for.

The message ends with a blank line and the fixed footer (``alerts.fmt_footer``) at ``alerts.book_as_of`` of the snapshot when one was
read, else the OLDEST data instant of the records read (the reconciliation's and the docket's ``payload.as_of``,
the levels sheet's ``book.as_of``; never a run instant such as an envelope's ``generated_at`` or the doctor's
``run_utc``, which is no market data), else the run instant: a sheet built from several records never claims
fresher data than its stalest one. :class:`Summary` carries the strict
emitted document (``alert_lint.EMITTED_DOCUMENT_KEYS``) and :meth:`Summary.lint` checks the text in strict mode;
its ``exit_code`` is 20 when any UNAVAILABLE line was printed, else 0 (never 10: the sheet and the brief are
information, not signals).
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import alert_lint, alerts, doctor, exit_rules, market_clock, occ, recon, routines, signals

MODE_MORNING: str = "morning"
MODE_BRIEF: str = "brief"
MODES: Tuple[str, ...] = (MODE_MORNING, MODE_BRIEF)
MORNING_HEAD: str = "MORNING DESK SHEET"
BRIEF_HEAD: str = "EOD DESK BRIEF"
TITLE_MARK: str = "### "
"""The message's first line is a markdown title (``### EOD DESK BRIEF 2026-11-03``): the desk view of a chat."""
SOURCE_SEPARATOR: str = " | "
SUB_ITEM: str = "  - "
"""A detail under an item (an open signal, a docket contract, a routine list, a trade's id): a nested list item."""
"""Between a block's words and the record it was read from (``**doctor** PASS, 0 warnings | quick check at 12:31 UTC |
state/doctor.json``)."""

LABEL_BOOK: str = "book snapshot"
LABEL_RECON: str = "reconciliation"
LABEL_DOCKET: str = "expiry docket"
LABEL_LEVELS: str = "levels sheet"
LABEL_DOCTOR: str = "doctor result"
LABEL_MANIFEST: str = "routine manifest"
LABELS: Tuple[str, ...] = (LABEL_BOOK, LABEL_RECON, LABEL_DOCKET, LABEL_LEVELS, LABEL_DOCTOR, LABEL_MANIFEST)

ARTIFACT_OK: str = "ok"
ARTIFACT_MISSING: str = "missing"
ARTIFACT_LATE: str = "late"
ARTIFACT_CORRUPT: str = "corrupt"
ARTIFACT_INVALID: str = "invalid"
ARTIFACT_STATUSES: Tuple[str, ...] = (ARTIFACT_OK, ARTIFACT_MISSING, ARTIFACT_LATE, ARTIFACT_CORRUPT, ARTIFACT_INVALID)
READ_OK: str = "ok"
READ_MISSING: str = "missing"
READ_CORRUPT: str = "corrupt"
"""The read statuses :func:`make_artifact` takes: ``state.read_json``'s ``ok``, ``missing`` and ``corrupt``."""

MISSING_LINE: str = "UNAVAILABLE: %s %s missing"
LATE_LINE: str = "UNAVAILABLE: %s is from %s (upstream late)"
CORRUPT_LINE: str = "UNAVAILABLE: %s %s cannot be read (moved aside)"
INVALID_LINE: str = "UNAVAILABLE: %s %s cannot be read (%s)"
INVALID_WHY: str = "not the record the desk writes"
DOCKET_NONE_LINE: str = "- **Expiry docket** none recorded today (%s missing)"
LEVELS_NONE_YET_LINE: str = "- **Last night's levels** none yet (the end-of-day level sheet has not run on this desk)"
"""The morning sheet's levels line when no level sheet was ever written (a new desk's first mornings): the expected
state, said plainly instead of an UNAVAILABLE line."""
PARTIAL_JOURNAL_LINE: str = "%s has lines that do not parse: they were skipped (the file is kept as it is)"
EARLY_CLOSE_NO_DOCKET: str = "the expiry docket is unavailable, so the contracts on their last trading day are unknown"
TODO_HEAD: str = "**To do**"
TODO_NOTHING: str = "**To do** nothing pending"
TOTALS_WITHOUT: str = "totals without %s"
DAY_TOTAL_WITHOUT: str = "the day total also without %s"
EXCLUDED_UNAVAILABLE: str = "%s (data unavailable)"
EXCLUDED_DAY: str = "%s (an open leg has no day P/L)"
INCIDENTS_LINE: str = "- **Incidents today** %s (%s); details in `incidents/`"
INCIDENTS_TODO: str = "`incidents/`: %s today"
_EXCLUDED_RE = re.compile(r"^(?P<group>\S+) excluded from the (?P<what>portfolio totals|day P/L total): ")
LADDER_POSTS: int = 1 + len(signals.LADDER_RUN_OFFSETS)
"""The posts of a full EXIT_SIGNAL ladder: the first post and the re-posts of ``signals.LADDER_RUN_OFFSETS`` (4)."""
CHECK_STATUSES: Tuple[str, ...] = doctor.STATUSES
SIGNAL_VERDICT_ORDER: Tuple[str, ...] = (alerts.VERDICT_EXIT_SIGNAL, alerts.VERDICT_WATCH, alerts.VERDICT_UNAVAILABLE)
PORTFOLIO_STRUCTURE: str = alerts.PORTFOLIO_TRADE
_DATE_TEXT_RE = re.compile(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
_CONTRACTS_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)?\Z")
DOCKET_SIDES: Tuple[str, ...] = ("long", "short")
_UTC = datetime.timezone.utc


# --------------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Artifact:
    """One record the message reads (module notes): its ``label``, its desk-root relative ``path``, its ``status``
    (:data:`ARTIFACT_STATUSES`), the parsed ``document`` when ``ok`` (a book dict for the snapshot, the JSON for the
    others), the date a ``late`` record is from, its ``instant`` (the footer candidate: the instant of its data,
    ``None`` for the doctor's record), whether the expected file was ``present`` and, for ``invalid``, ``why`` it cannot
    be used."""

    label: str
    path: str
    status: str
    document: Any = None
    found_date: Optional[datetime.date] = None
    instant: Optional[datetime.datetime] = None
    present: bool = False
    why: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == ARTIFACT_OK

    def line(self) -> Optional[str]:
        """The record's UNAVAILABLE line, or ``None`` when it is ``ok``."""
        if self.status == ARTIFACT_OK:
            return None
        if self.status == ARTIFACT_MISSING:
            return MISSING_LINE % (self.label, self.path)
        if self.status == ARTIFACT_LATE:
            return LATE_LINE % (self.label, self.found_date.isoformat() if self.found_date else "an earlier day")
        if self.status == ARTIFACT_CORRUPT:
            return CORRUPT_LINE % (self.label, self.path)
        return INVALID_LINE % (self.label, self.path, self.why or INVALID_WHY)


def _instant(value: Any) -> Optional[datetime.datetime]:
    if isinstance(value, datetime.datetime):
        return value.astimezone(_UTC) if value.tzinfo is not None and value.utcoffset() is not None else None
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(_UTC) if parsed.tzinfo is not None and parsed.utcoffset() is not None else None


def _date(value: Any) -> Optional[datetime.date]:
    if isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
        return value
    if not isinstance(value, str) or not _DATE_TEXT_RE.match(value):
        return None
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _count(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _whole(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _text(value: Any, optional: bool = False) -> bool:
    return (optional and value is None) or (isinstance(value, str) and bool(value))


def book_instant(document: Mapping[str, Any]) -> datetime.datetime:
    """The footer instant of a book document: ``alerts.book_as_of`` (its newest quote as-of, else ``generated_at``)."""
    return alerts.book_as_of(document)


def _read_book(raw: Any) -> Optional[Tuple[Any, datetime.date, Optional[datetime.datetime]]]:
    try:
        document = exit_rules.book_from_json(raw)
        return document, market_clock.to_et(document["generated_at"]).date(), book_instant(document)
    except (exit_rules.BookFormatError, KeyError, TypeError, ValueError):
        return None


def _envelope_payload(raw: Any) -> Optional[Tuple[Dict[str, Any], datetime.datetime]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("payload"), dict):
        return None
    generated = _instant(raw.get("generated_at"))
    return (raw["payload"], generated) if generated is not None else None


def _read_recon(raw: Any) -> Optional[Tuple[Any, datetime.date, Optional[datetime.datetime]]]:
    found = _envelope_payload(raw)
    if found is None:
        return None
    payload, _generated = found
    counts = payload.get("counts")
    day = _date(payload.get("et_date"))
    if day is None or payload.get("verdict") not in recon.VERDICTS or not isinstance(counts, dict):
        return None
    if not (_count(counts.get("edits")) and _count(counts.get("unavailable"))):
        return None
    return raw, day, _instant(payload.get("as_of"))


def _occ_symbol(value: Any) -> bool:
    """A compact OCC symbol as the expiry tools write it (``SPY261120P00590000``)."""
    if not _text(value):
        return False
    try:
        return occ.parse(value).compact == value
    except ValueError:
        return False


def _docket_leg(leg: Any) -> bool:
    return (isinstance(leg, dict) and _text(leg.get("trade_id"), True) and _text(leg.get("group_id"))
            and _text(leg.get("underlying")) and _text(leg.get("structure")) and _occ_symbol(leg.get("symbol"))
            and leg.get("side") in DOCKET_SIDES and isinstance(leg.get("contracts"), str)
            and _CONTRACTS_RE.match(leg["contracts"]) is not None
            and _date(leg.get("last_trading_day")) is not None and _whole(leg.get("dte_trading"))
            and _text(leg.get("act_by_text")))


def _read_docket(raw: Any) -> Optional[Tuple[Any, datetime.date, Optional[datetime.datetime]]]:
    found = _envelope_payload(raw)
    if found is None:
        return None
    payload, _generated = found
    counts, legs, day = payload.get("counts"), payload.get("legs"), _date(payload.get("today"))
    if day is None or not isinstance(counts, dict) or not isinstance(legs, list):
        return None
    if not (_count(counts.get("listed")) and _count(counts.get("unavailable"))) or not all(map(_docket_leg, legs)):
        return None
    return raw, day, _instant(payload.get("as_of"))


def _read_levels(raw: Any) -> Optional[Tuple[Any, datetime.date, Optional[datetime.datetime]]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("groups"), list):
        return None
    day = _date(raw.get("et_date"))
    if day is None:
        return None
    for group in raw["groups"]:
        if not (isinstance(group, dict) and _text(group.get("group_id")) and _text(group.get("trade_id"), True)
                and _count(group.get("price_levels")) and _count(group.get("resting_eligible"))):
            return None
    book = raw.get("book")
    return raw, day, _instant(book.get("as_of")) if isinstance(book, dict) else None


def _read_doctor(raw: Any) -> Optional[Tuple[Any, datetime.date, Optional[datetime.datetime]]]:
    if not isinstance(raw, dict) or not isinstance(raw.get("checks"), list):
        return None
    run = _instant(raw.get("run_utc"))
    if run is None or raw.get("mode") not in doctor.RECORD_MODES or raw.get("verdict") not in (doctor.PASS, doctor.FAIL):
        return None
    for item in raw["checks"]:
        if not (isinstance(item, dict) and _text(item.get("id")) and item.get("status") in CHECK_STATUSES):
            return None
    return raw, market_clock.to_et(run).date(), None  # a run instant, no market data: never the footer's


_READERS = {LABEL_BOOK: _read_book, LABEL_RECON: _read_recon, LABEL_DOCKET: _read_docket, LABEL_LEVELS: _read_levels,
            LABEL_DOCTOR: _read_doctor}


def make_artifact(label: str, path: str, raw: Any, read_status: str, expected: datetime.date,
                  older: Optional[datetime.date] = None) -> Artifact:
    """The :class:`Artifact` of a record read with ``read_status`` (:data:`READ_OK`, :data:`READ_MISSING`,
    :data:`READ_CORRUPT`) whose date must be ``expected``; ``older`` is the newest earlier date of a record of the
    same kind (a missing record with an older one is ``late``). ``ValueError`` for an unknown label or status."""
    if label not in _READERS:
        raise ValueError("no reader for the record %r" % (label,))
    if read_status == READ_MISSING:
        if older is not None:
            return Artifact(label, path, ARTIFACT_LATE, found_date=older)
        return Artifact(label, path, ARTIFACT_MISSING)
    if read_status == READ_CORRUPT:
        return Artifact(label, path, ARTIFACT_CORRUPT, present=True)
    if read_status != READ_OK:
        raise ValueError("unknown read status %r" % (read_status,))
    found = _READERS[label](raw)
    if found is None:
        return Artifact(label, path, ARTIFACT_INVALID, present=True)
    document, day, instant = found
    if day != expected:
        return Artifact(label, path, ARTIFACT_LATE, found_date=day, present=True)
    return Artifact(label, path, ARTIFACT_OK, document=document, instant=instant, present=True)


# --------------------------------------------------------------------------------------------
# Inputs and the result
# --------------------------------------------------------------------------------------------


@dataclass
class MorningInputs:
    """What the Morning Desk Sheet reads (module notes); ``early_close_et`` is the session's close on an early-close
    day (13:00 ET), else ``None``; ``notes`` are the partial-journal lines."""

    et_date: datetime.date
    now: datetime.datetime
    doctor: Artifact
    recon: Artifact
    docket: Artifact
    levels: Artifact
    open_signals: Sequence[signals.OpenSignal]
    delay_min: int
    early_close_et: Optional[datetime.time] = None
    notes: Sequence[str] = ()


@dataclass
class BriefInputs:
    """What the EOD Desk Brief reads (module notes): ``signal_records`` are ``journal/signals.jsonl``'s records,
    ``audit`` the routines' day audit (or ``audit_problem``, the manifest that could not be read), ``config_changes``
    ``exit_check.config_changes``' list and ``calendar_findings`` the number of ``calendar_events.freshness``
    findings."""

    et_date: datetime.date
    now: datetime.datetime
    book: Artifact
    recon: Artifact
    docket: Artifact
    signal_records: Sequence[Mapping[str, Any]]
    open_signals: Sequence[signals.OpenSignal]
    delay_min: int
    audit: Optional[routines.DayAudit] = None
    audit_problem: Optional[Artifact] = None
    config_changes: Sequence[Mapping[str, Any]] = ()
    calendar_findings: int = 0
    notes: Sequence[str] = ()
    incidents: Sequence[Tuple[Optional[str], Optional[str]]] = ()
    """The day's ``incidents/`` files as ``(routine id or None, what)``: ``what`` the step slug, ``message``,
    ``crash``, ``<slug> withheld`` or ``dirty checkout``; ``(None, None)`` for a file of another name."""


@dataclass
class Summary:
    """One composed message: its body ``lines`` and ``footer``, the strict emitted document's parts, the footer's
    ``as_of`` and feed delay (``delay_min``) and the records that printed an UNAVAILABLE line."""

    mode: str
    et_date: datetime.date
    lines: List[str]
    footer: str
    emitted_numbers: List[str]
    rule_ids: List[str]
    identifiers: List[str]
    clocks: List[str]
    as_of: datetime.datetime
    delay_min: int
    unavailable: List[Artifact] = field(default_factory=list)

    @property
    def text(self) -> str:
        """The lines, a blank line (so a chat never reads the footer as part of the last list item) and the footer."""
        return "\n".join(self.lines + ["", self.footer])

    @property
    def exit_code(self) -> int:
        """20 when any UNAVAILABLE line was printed, else 0."""
        return 20 if self.unavailable else 0

    def emitted_document(self) -> Dict[str, Any]:
        """The strict emitted document (``alert_lint.EMITTED_DOCUMENT_KEYS``; ``alert_lint.py --emitted-file``)."""
        return {"emitted_numbers": list(self.emitted_numbers), "rule_ids": list(self.rule_ids),
                "identifiers": list(self.identifiers), "clocks": list(self.clocks),
                "as_of": self.as_of.astimezone(_UTC).isoformat()}

    def lint(self) -> alert_lint.LintResult:
        """``alert_lint.check`` of the text against its own emitted document (strict mode)."""
        return alert_lint.check(self.text, self.emitted_numbers, rule_ids=self.rule_ids, identifiers=self.identifiers,
                                clocks=self.clocks, as_of=self.as_of)


# --------------------------------------------------------------------------------------------
# The composer
# --------------------------------------------------------------------------------------------


class Composer:
    """The lines of one message with the numbers, identifiers and clocks its formatters printed."""

    def __init__(self) -> None:
        self.lines: List[str] = []
        self.emitted: List[str] = []
        self.rule_ids: List[str] = []
        self.identifiers: List[str] = []
        self.clocks: List[str] = []
        self.unavailable: List[Artifact] = []
        self.records: List[str] = []

    def add(self, line: str) -> None:
        self.lines.append(line)

    def block(self, *lines: str) -> None:
        """One block of the desk view: a blank line before it (after the title or the previous block), then its
        lines (a bold-labelled line and its ``- `` items); a chat renders each block as a paragraph or a list."""
        if self.lines and self.lines[-1] != "":
            self.lines.append("")
        self.lines.extend(lines)

    def ident(self, text: str) -> str:
        """An identifier the message prints (a trade, group or signal id, an ISO date)."""
        if text not in self.identifiers:
            self.identifiers.append(text)
        return text

    def clock(self, text: str) -> str:
        """``HH:MM UTC`` or ``HH:MM ET`` as printed."""
        if text not in self.clocks:
            self.clocks.append(text)
        return text

    def reason(self, text: str) -> str:
        """A text the desk's own code wrote (a path, an act-by text, a warning): ``alerts.fmt_reason``."""
        return alerts.fmt_reason(text, self.emitted, self.rule_ids, self.identifiers, self.clocks)

    def count(self, value: int) -> str:
        return alerts.fmt_count(value, self.emitted)

    def counted(self, value: int, singular: str, plural: Optional[str] = None) -> str:
        """``1 warning``, ``2 warnings`` (``plural`` when it is not ``singular + "s"``)."""
        return "%s %s" % (self.count(value), singular if value == 1 else (plural or singular + "s"))

    def money(self, value: Any) -> str:
        """``fmt_money`` of the record's own Decimal; ``unavailable`` for a null."""
        return "unavailable" if value is None else alerts.fmt_money(value, self.emitted)

    def signed(self, value: Any) -> str:
        """:meth:`money` with a ``+`` before a gain (``+$1,145.00``, ``-$50.00``, ``$0.00``), the signed text emitted."""
        text = self.money(value)
        if value is not None and text != "unavailable" and not text.startswith("-") and text != "$0.00":
            text = "+" + text
            self.emitted.append(text)
        return text

    def record(self, item: Artifact) -> None:
        """A record the message was composed from (its path, for the ``read from`` line)."""
        if item.path not in self.records:
            self.records.append(item.path)

    def date(self, value: Any) -> str:
        return self.ident(value.isoformat() if isinstance(value, datetime.date) else str(value))

    def utc_minute(self, moment: datetime.datetime) -> str:
        """``YYYY-MM-DD HH:MM UTC`` of an instant (its date an identifier, its clock a clock)."""
        utc = moment.astimezone(_UTC)
        return "%s %s" % (self.ident(utc.date().isoformat()), self.clock(utc.strftime("%H:%M UTC")))

    def unavailable_item(self, item: Artifact) -> str:
        """The record's UNAVAILABLE line as an item, recorded as unavailable."""
        self.unavailable.append(item)
        return "- " + self.reason(item.line() or "")

    def finish(self, mode: str, et_date: datetime.date, as_of: datetime.datetime, delay_min: int) -> Summary:
        footer = alerts.fmt_footer(as_of, delay_min, self.emitted, self.clocks)
        return Summary(mode, et_date, list(self.lines), footer, list(self.emitted), list(self.rule_ids),
                       list(self.identifiers), list(self.clocks), as_of.astimezone(_UTC), delay_min,
                       list(self.unavailable))


def bold_label(text: str) -> str:
    """A block's or an item's label in bold: ``**Today**``."""
    return "**%s**" % text


def _them(count: int) -> str:
    return "it" if count == 1 else "them"


def _words(structure: Any) -> str:
    if structure == PORTFOLIO_STRUCTURE:
        return alerts.PORTFOLIO_WORDS
    return alerts.STRUCTURE_WORDS.get(str(structure), str(structure).replace("_", " "))


def _oldest(items: Sequence[Artifact], fallback: datetime.datetime) -> datetime.datetime:
    """The oldest data instant of the records read (module notes), else ``fallback``."""
    stamps = [item.instant for item in items if item.ok and item.instant is not None]
    return min(stamps) if stamps else fallback


# --------------------------------------------------------------------------------------------
# Items (each a ``- `` line, with ``  - `` sub-items for the details)
# --------------------------------------------------------------------------------------------


def doctor_line(c: Composer, item: Artifact) -> str:
    """``- **Doctor** <PASS|FAIL>, <n> warnings[: ids][; <m> failures: ids] (<mode> check at HH:MM UTC)``: the
    record's verdict and check ids, never a reason."""
    record = item.document
    run = _instant(record["run_utc"])
    checks = record["checks"]
    warned = [c.reason(check["id"]) for check in checks if check["status"] == doctor.WARN]
    failed = [c.reason(check["id"]) for check in checks if check["status"] == doctor.FAIL]
    text = "- **Doctor** %s, %s" % (record["verdict"], c.counted(len(warned), "warning"))
    if warned:
        text += ": " + ", ".join(warned)
    if failed:
        text += "; %s: %s" % (c.counted(len(failed), "failure"), ", ".join(failed))
    c.record(item)
    return text + " (%s check at %s)" % (record["mode"], c.clock(run.strftime("%H:%M UTC")))


def recon_line(c: Composer, item: Artifact) -> str:
    """``- **positions.csv** <VERDICT>, <n> proposed edits (`csv_recon.py --diff` lists them)``."""
    payload = item.document["payload"]
    edits, unavailable = payload["counts"]["edits"], payload["counts"]["unavailable"]
    text = "- **positions.csv** %s, %s (`csv_recon.py --diff` lists %s)" % (
        payload["verdict"], c.counted(edits, "proposed edit"), _them(edits))
    if unavailable:
        text += ", %s (`csv_recon.py` shows %s)" % (c.counted(unavailable, "unavailable item"), _them(unavailable))
    c.record(item)
    return text


def signal_line(c: Composer, item: signals.OpenSignal) -> str:
    """One open signal (module notes): id, subject, verdict, plan, first post, ladder position, ack state."""
    segments = [c.ident(item.signal_id), "%s %s" % (c.ident(item.trade_id), _words(item.structure)),
                alerts.VERDICT_WORDS.get(item.verdict, item.verdict)]
    if item.plan in alerts.PLAN_WORDS:
        segments.append("your plan: %s" % alerts.PLAN_WORDS[item.plan])
    segments.append("first posted %s" % c.utc_minute(item.posted_at))
    if item.verdict == alerts.VERDICT_UNAVAILABLE:
        segments.append("posted %s; re-posted on every run until acknowledged" % c.counted(item.posts, "time"))
    elif item.verdict != alerts.VERDICT_EXIT_SIGNAL:
        segments.append("posted %s; no re-post ladder for %s" % (c.counted(item.posts, "time"),
                                                                 alerts.VERDICT_WORDS.get(item.verdict, item.verdict)))
    elif item.next_repost_after_runs is None:
        segments.append("posted %s of %s times; the re-post ladder is done" % (c.count(item.posts),
                                                                              c.count(LADDER_POSTS)))
    else:
        segments.append("posted %s of %s times; next re-post %s after the first post" % (
            c.count(item.posts), c.count(LADDER_POSTS), c.counted(item.next_repost_after_runs, "run")))
    if item.ack_state == signals.ACK_STATE_SNOOZED:
        until = _instant(item.snoozed_until)
        segments.append("snoozed until %s" % c.utc_minute(until) if until is not None else "snoozed (until unknown)")
    else:
        segments.append("not acknowledged")
    return " | ".join(segments)


def open_signal_lines(c: Composer, items: Sequence[signals.OpenSignal]) -> List[str]:
    """``- **Open signals** <n>`` and one sub-item per open signal."""
    return ["- **Open signals** %s" % c.count(len(items))] + [SUB_ITEM + signal_line(c, item) for item in items]


def docket_lines(c: Composer, item: Artifact) -> List[str]:
    """``- **Expiry docket** <n> contracts due`` and one sub-item per listed contract (module notes)."""
    payload = item.document["payload"]
    listed, unavailable = payload["counts"]["listed"], payload["counts"]["unavailable"]
    head = "- **Expiry docket** %s due" % c.counted(listed, "contract")
    if unavailable:
        head += ", %s unavailable (`expiry_checklist.py --week` shows why)" % c.count(unavailable)
    lines = [head]
    for leg in payload["legs"]:
        # Each line names its contract, side and size (the checklist's leg head): the legs of one group can act by
        # different times (a short leg by the session close, a long one by the broker cutoff; FAI-689 review L1-1).
        lines.append("%s%s %s | %s %s | last trading day %s (%s) | %s" % (
            SUB_ITEM, c.ident(leg["trade_id"] or leg["group_id"]),
            c.reason("%s %s %s" % (leg["symbol"], leg["side"], leg["contracts"])), c.reason(leg["underlying"]),
            _words(leg["structure"]), c.date(leg["last_trading_day"]),
            alerts.fmt_days(leg["dte_trading"], c.emitted, "trading"), c.reason(leg["act_by_text"])))
    c.record(item)
    return lines


def early_close_line(c: Composer, close_et: datetime.time, item: Artifact, et_date: datetime.date) -> str:
    """O5: ``- **Early close** today at 13:00 ET: <n> contracts on their last trading day; <act-by text>``."""
    head = "- **Early close** today at %s" % c.clock(close_et.strftime("%H:%M ET"))
    if not item.ok:
        return "%s: %s" % (head, EARLY_CLOSE_NO_DOCKET)
    legs = [leg for leg in item.document["payload"]["legs"] if leg["last_trading_day"] == et_date.isoformat()]
    if not legs:
        return "%s: no contract on its last trading day" % head
    texts: List[str] = []
    for leg in legs:
        if leg["act_by_text"] not in texts:
            texts.append(leg["act_by_text"])
    return "%s: %s on %s last trading day; %s" % (head, c.counted(len(legs), "contract"),
                                                 "its" if len(legs) == 1 else "their",
                                                 "; ".join(c.reason(text) for text in texts))


def levels_lines(c: Composer, item: Artifact) -> List[str]:
    """``- **Last night's levels**`` and one ``<trade_id> <r> resting-eligible of <p> price levels`` sub-item per
    group (``- **Last night's levels** no open group`` for none)."""
    groups = item.document["groups"]
    subs = ["%s%s %s resting-eligible of %s" % (SUB_ITEM, c.ident(group["trade_id"] or group["group_id"]),
                                               c.count(group["resting_eligible"]),
                                               c.counted(group["price_levels"], "price level"))
            for group in groups]
    c.record(item)
    return ["- **Last night's levels**" + ("" if subs else " no open group")] + subs


def excluded_groups(document: Mapping[str, Any]) -> Tuple[List[str], List[str]]:
    """``(left out of the portfolio totals, left out of the day P/L total)``: the group ids the book's own exclusion
    warnings name (``<group> excluded from the portfolio totals: ...``), each a group of the snapshot, in warning
    order; the reasons are never read (a grouping reason can carry an account name the owner typed)."""
    known = {str(group.get("group_id")) for group in document.get("groups") or [] if isinstance(group, Mapping)}
    totals: List[str] = []
    day: List[str] = []
    for warning in document.get("warnings") or []:
        match = _EXCLUDED_RE.match(warning) if isinstance(warning, str) else None
        if match is None or match.group("group") not in known:
            continue
        into = totals if match.group("what") == "portfolio totals" else day
        if match.group("group") not in into:
            into.append(match.group("group"))
    return totals, [group_id for group_id in day if group_id not in totals]


DAY_PL_PREFIX: str = "day P/L "


def pl_lines(c: Composer, item: Artifact) -> List[str]:
    """The brief's P/L block from the snapshot's portfolio fields, each printed as it is (never summed): ``**P/L**``,
    then the ``Unrealized``, ``Today`` and ``Realized`` items (a gain carries its ``+``) and, when the book's totals
    leave groups out, the item that names them (module notes)."""
    document = item.document
    portfolio = document["portfolio"]
    day = portfolio.get("day_pl")
    if day is None:
        warning = next((text for text in document.get("warnings") or []
                        if isinstance(text, str) and text.startswith("day P/L unavailable")), None)
        day_text = c.reason(warning[len(DAY_PL_PREFIX):]) if warning else "unavailable"
    else:
        day_text = c.signed(day)
    lines = [PL_HEAD,
             "- **Unrealized** %s at the mids (%s at liquidation)" % (c.signed(portfolio.get("unrealized_pl_mid")),
                                                                    c.signed(portfolio.get("unrealized_pl_liq"))),
             "- **Today** %s" % day_text,
             "- **Realized** %s this year (%s today, %s this month)" % (
                 c.signed(portfolio.get("realized_pl_ytd")), c.signed(portfolio.get("realized_pl_today")),
                 c.signed(portfolio.get("realized_pl_mtd")))]
    totals, day_only = excluded_groups(document)
    pieces: List[str] = []
    if totals:
        pieces.append(TOTALS_WITHOUT % ", ".join(EXCLUDED_UNAVAILABLE % c.ident(group_id) for group_id in totals))
    if day_only and day is not None:
        pieces.append(DAY_TOTAL_WITHOUT % ", ".join(EXCLUDED_DAY % c.ident(group_id) for group_id in day_only))
    if pieces:
        lines.append("- " + "; ".join(pieces))
    c.record(item)
    return lines


PL_HEAD: str = "**P/L**"
BY_TRADE_HEAD: str = "**By trade**"
STATUS_HEAD: str = "**Desk status**"


def _quarantine_line(c: Composer, group_id: str, reasons: Sequence[Any]) -> str:
    words = "; ".join(c.reason(alerts.quarantine_words(str(reason))) for reason in reasons) or alerts.GROUP_WORDS
    return "- **%s** quarantined: %s" % (c.ident(group_id), words)


def group_lines(c: Composer, item: Artifact) -> List[str]:
    """O12: ``**By trade**`` and, per open group of the snapshot in its order, ``- **<UNDERLYING> <structure words>**
    <unrealized at the mids> (<at liquidation>)`` with the sub-item ``*<trade id> | today <day>*`` (and ``| data
    unavailable`` when it is not ok); a quarantined group in the desk's own words. No block when the snapshot holds
    no open or quarantined group."""
    document = item.document
    legs = {leg.get("row_number"): leg for leg in document.get("legs") or []}
    quarantined = [entry for entry in (document["portfolio"].get("quarantined_groups") or [])
                   if isinstance(entry, Mapping)]
    by_id = {entry.get("group_id"): entry for entry in quarantined}
    lines: List[str] = []
    printed: List[Any] = []
    for group in document.get("groups") or []:
        group_id = group.get("group_id")
        if group_id in by_id:
            lines.append(_quarantine_line(c, str(group_id), by_id[group_id].get("reasons") or []))
            printed.append(group_id)
            continue
        open_legs = [legs[row] for row in group.get("legs") or [] if row in legs and legs[row].get("status") == "open"]
        if not open_legs:
            continue
        if len(open_legs) == 1 or all(leg.get("day_pl") is None for leg in open_legs):
            day = c.signed(open_legs[0].get("day_pl") if len(open_legs) == 1 else None)
        else:
            day = ", ".join("%s (row %s)" % (c.signed(leg.get("day_pl")), c.count(leg["row_number"]))
                            for leg in open_legs)
        lines.append("- **%s %s** %s at the mids (%s at liquidation)" % (
            c.reason(str(group.get("underlying"))), _words(group.get("structure")),
            c.signed(group.get("unrealized_pl_mid")), c.signed(group.get("unrealized_pl_liq"))))
        sub = "%s | today %s" % (c.ident(str(group.get("trade_id") or group_id)), day)
        if group.get("data_quality") != "ok":
            sub += " | data %s" % group.get("data_quality")
        lines.append("%s*%s*" % (SUB_ITEM, sub))
    for entry in quarantined:
        if entry.get("group_id") not in printed:
            lines.append(_quarantine_line(c, str(entry.get("group_id")), entry.get("reasons") or []))
    return [BY_TRADE_HEAD] + lines if lines else []


def signals_today(records: Sequence[Mapping[str, Any]], et_date: datetime.date) -> Dict[str, int]:
    """``{count, EXIT_SIGNAL, WATCH, UNAVAILABLE}``: the ``signals.jsonl`` records (with a well-formed signal id and
    ``posted_at``) whose ``posted_at`` falls on the ET date, counted (records, not amounts)."""
    today: List[str] = []
    for record in records:
        posted = _instant(record.get("posted_at"))
        if posted is None or signals.parse_signal_id(record.get("signal_id")) is None:
            continue
        if market_clock.to_et(posted).date() == et_date:
            today.append(str(record.get("verdict")))
    counts = {"count": len(today)}
    counts.update({verdict: today.count(verdict) for verdict in SIGNAL_VERDICT_ORDER})
    return counts


def signals_today_line(c: Composer, records: Sequence[Mapping[str, Any]], et_date: datetime.date) -> str:
    """``- **Signals today** <n> (<a> EXIT SIGNAL, <b> WATCH, <c> UNAVAILABLE)``: the records posted on the ET date."""
    counts = signals_today(records, et_date)
    words = ", ".join("%s %s" % (c.count(counts[verdict]), alerts.VERDICT_WORDS[verdict])
                      for verdict in SIGNAL_VERDICT_ORDER)
    return "- **Signals today** %s (%s)" % (c.count(counts["count"]), words)


def routines_lines(c: Composer, audit: routines.DayAudit) -> List[str]:
    """``- **Routines** <ran> of <expected> ran today`` (``, none late or missed`` when so) and the sub-items
    ``late: ...``, ``missed: ...`` and ``pending: ...`` of the lists that are not empty (O15)."""

    def name(slot: routines.SlotAudit) -> str:
        return "%s %s" % (c.reason(slot.routine_id), c.clock("%s ET" % slot.scheduled_et))

    head = "- **Routines** %s of %s ran today" % (c.count(len(audit.ran)), c.count(len(audit.expected)))
    if not audit.late and not audit.missed:
        head += ", none late or missed"
    lines = [head]
    if audit.late:
        lines.append("%slate: %s" % (SUB_ITEM, ", ".join(
            "%s (%s late)" % (name(slot), alerts.fmt_minutes(slot.lateness_min, c.emitted)) for slot in audit.late)))
    if audit.missed:
        lines.append("%smissed: %s" % (SUB_ITEM, ", ".join(name(slot) for slot in audit.missed)))
    if audit.pending:
        lines.append("%spending: %s" % (SUB_ITEM, ", ".join(name(slot) for slot in audit.pending)))
    return lines


def incidents_line(c: Composer, incidents: Sequence[Tuple[Optional[str], Optional[str]]]) -> str:
    """``- **Incidents today** <n> (<routine> <what>, ..., <k> other files); details in `incidents/```: names only."""
    named: List[str] = []
    others = 0
    for routine_id, what in incidents:
        if what is None:
            others += 1
        else:
            named.append(c.reason(what if routine_id is None else "%s %s" % (routine_id, what)))
    if others:
        named.append(c.counted(others, "other file"))
    return INCIDENTS_LINE % (c.count(len(incidents)), ", ".join(named))


def todo_lines(c: Composer, inputs: BriefInputs) -> List[str]:
    """The owner's to-do list (module notes): ``**To do**`` and one item each, or ``**To do** nothing pending``."""
    items: List[str] = []
    for item in inputs.open_signals:
        signal_id = c.ident(item.signal_id)
        items.append("%s not acknowledged (`ack.py show %s`)" % (signal_id, signal_id))
    if inputs.recon.ok:
        edits = inputs.recon.document["payload"]["counts"]["edits"]
        if edits:
            items.append("positions.csv: %s pending (`csv_recon.py --diff` lists %s)" % (
                c.counted(edits, "proposed edit"), _them(edits)))
    if inputs.config_changes:
        labels = [alerts.CONFIG_FILE_LABELS.get(str(change.get("name")), str(change.get("name")))
                  for change in inputs.config_changes]
        items.append("CONFIG CHANGED pending: %s (%s)" % (", ".join(labels), alerts.ACKNOWLEDGE_CONFIG))
    if inputs.calendar_findings:
        items.append("research/calendar.toml: %s (`calendar_check.py` lists %s)" % (
            c.counted(inputs.calendar_findings, "finding"), _them(inputs.calendar_findings)))
    if inputs.incidents:
        items.append(INCIDENTS_TODO % c.counted(len(inputs.incidents), "file"))
    return [TODO_HEAD] + ["- " + item for item in items] if items else [TODO_NOTHING]


def records_line(c: Composer) -> Optional[str]:
    """``*read from <path>, <path>*``: the records the message was composed from, in italics under it."""
    if not c.records:
        return None
    return "*read from %s*" % ", ".join(c.reason(path) for path in c.records)


# --------------------------------------------------------------------------------------------
# The two messages
# --------------------------------------------------------------------------------------------


def morning_sheet(inputs: MorningInputs) -> Summary:
    """The Morning Desk Sheet (module notes): one ``**Desk status**`` block of the doctor, the reconciliation, the
    standing signals, the docket (and the early-close line) and last night's levels, then the records it read."""
    c = Composer()
    c.add("%s%s %s" % (TITLE_MARK, MORNING_HEAD, c.date(inputs.et_date)))
    items: List[str] = []
    items.append(doctor_line(c, inputs.doctor) if inputs.doctor.ok else c.unavailable_item(inputs.doctor))
    items.append(recon_line(c, inputs.recon) if inputs.recon.ok else c.unavailable_item(inputs.recon))
    items.extend(open_signal_lines(c, inputs.open_signals))
    if inputs.docket.ok:
        items.extend(docket_lines(c, inputs.docket))
    else:
        items.append(c.unavailable_item(inputs.docket))
    if inputs.early_close_et is not None:
        items.append(early_close_line(c, inputs.early_close_et, inputs.docket, inputs.et_date))
    if inputs.levels.ok:
        items.extend(levels_lines(c, inputs.levels))
    elif inputs.levels.status == ARTIFACT_MISSING and inputs.levels.found_date is None:
        items.append(LEVELS_NONE_YET_LINE)  # no level sheet of any date yet: a new desk, not a failure
    else:
        items.append(c.unavailable_item(inputs.levels))
    items.extend("- " + c.reason(note) for note in inputs.notes)
    c.block(STATUS_HEAD, *items)
    sources = records_line(c)
    if sources:
        c.block(sources)
    records = [inputs.doctor, inputs.recon, inputs.docket, inputs.levels]
    return c.finish(MODE_MORNING, inputs.et_date, _oldest(records, inputs.now), inputs.delay_min)


def eod_brief(inputs: BriefInputs) -> Summary:
    """The EOD Desk Brief (module notes): the P/L and by-trade blocks, the ``**Desk status**`` block (signals, the
    reconciliation, the docket, the routines, incidents, notes), the owner's to-do list and the records it read."""
    c = Composer()
    c.add("%s%s %s" % (TITLE_MARK, BRIEF_HEAD, c.date(inputs.et_date)))
    if inputs.book.ok:
        c.block(*pl_lines(c, inputs.book))
        trades = group_lines(c, inputs.book)
        if trades:
            c.block(*trades)
    else:
        c.block(PL_HEAD, c.unavailable_item(inputs.book))
    items = [signals_today_line(c, inputs.signal_records, inputs.et_date)]
    items.extend(open_signal_lines(c, inputs.open_signals))
    items.append(recon_line(c, inputs.recon) if inputs.recon.ok else c.unavailable_item(inputs.recon))
    if inputs.docket.ok:
        items.extend(docket_lines(c, inputs.docket))
    elif not inputs.docket.present:
        items.append(DOCKET_NONE_LINE % c.reason(inputs.docket.path))
    else:
        items.append(c.unavailable_item(inputs.docket))
    if inputs.audit is not None:
        items.extend(routines_lines(c, inputs.audit))
    elif inputs.audit_problem is not None:
        items.append(c.unavailable_item(inputs.audit_problem))
    if inputs.incidents:
        items.append(incidents_line(c, inputs.incidents))
    items.extend("- " + c.reason(note) for note in inputs.notes)
    c.block(STATUS_HEAD, *items)
    c.block(*todo_lines(c, inputs))
    sources = records_line(c)
    if sources:
        c.block(sources)
    delay = inputs.delay_min
    if inputs.book.ok:
        as_of = inputs.book.instant or book_instant(inputs.book.document)
        sources_doc = inputs.book.document.get("sources") or []
        source_delay = sources_doc[0].get("delay_min") if sources_doc and isinstance(sources_doc[0], Mapping) else None
        if isinstance(source_delay, int) and not isinstance(source_delay, bool) and source_delay >= 0:
            delay = source_delay
    else:
        as_of = _oldest([inputs.recon, inputs.docket], inputs.now)
    return c.finish(MODE_BRIEF, inputs.et_date, as_of, delay)


__all__ = [
    "ARTIFACT_CORRUPT",
    "ARTIFACT_INVALID",
    "ARTIFACT_LATE",
    "ARTIFACT_MISSING",
    "ARTIFACT_OK",
    "ARTIFACT_STATUSES",
    "Artifact",
    "BRIEF_HEAD",
    "BriefInputs",
    "CHECK_STATUSES",
    "CORRUPT_LINE",
    "Composer",
    "DOCKET_NONE_LINE",
    "DOCKET_SIDES",
    "DAY_TOTAL_WITHOUT",
    "EARLY_CLOSE_NO_DOCKET",
    "EXCLUDED_DAY",
    "EXCLUDED_UNAVAILABLE",
    "INCIDENTS_LINE",
    "INCIDENTS_TODO",
    "INVALID_LINE",
    "INVALID_WHY",
    "LABELS",
    "LABEL_BOOK",
    "LABEL_DOCKET",
    "LABEL_DOCTOR",
    "LABEL_LEVELS",
    "LABEL_MANIFEST",
    "LABEL_RECON",
    "LADDER_POSTS",
    "LATE_LINE",
    "MISSING_LINE",
    "MODES",
    "MODE_BRIEF",
    "MODE_MORNING",
    "MORNING_HEAD",
    "MorningInputs",
    "PARTIAL_JOURNAL_LINE",
    "PORTFOLIO_STRUCTURE",
    "READ_CORRUPT",
    "READ_MISSING",
    "READ_OK",
    "SIGNAL_VERDICT_ORDER",
    "Summary",
    "TODO_HEAD",
    "PL_HEAD",
    "STATUS_HEAD",
    "SUB_ITEM",
    "records_line",
    "TODO_NOTHING",
    "TITLE_MARK",
    "SOURCE_SEPARATOR",
    "BY_TRADE_HEAD",
    "DAY_PL_PREFIX",
    "TOTALS_WITHOUT",
    "bold_label",
    "book_instant",
    "docket_lines",
    "doctor_line",
    "early_close_line",
    "eod_brief",
    "excluded_groups",
    "group_lines",
    "incidents_line",
    "levels_lines",
    "make_artifact",
    "morning_sheet",
    "open_signal_lines",
    "pl_lines",
    "recon_line",
    "routines_lines",
    "signal_line",
    "signals_today",
    "signals_today_line",
    "todo_lines",
]
