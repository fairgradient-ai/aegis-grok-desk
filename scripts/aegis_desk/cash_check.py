"""The cash check of a ticket or a proposal against the cash the owner stated (roadmap 2.6, open question 5).

The desk sees no brokerage account, so the only cash it can compare with is a figure the owner states per account in
``desk.toml`` (``[[owner.accounts]]`` ``cash`` and ``cash_as_of``, given together or not at all). This module is the one
place that turns the legs of a ticket or a proposal into the cash they need and compares it with that figure.

What the check compares (:func:`peak_need`)
-------------------------------------------
The cash needed is the peak cash out while the owner works the legs in the desk's fixed order: the most, over the steps
``k``, of the legs ``1..k``'s premium paid less premium received (a buy at its ask, a sell at its bid, times 100 times
the contracts) plus the cash a short put opened secures less the cash a short put closed releases (its strike times
100 times its contracts). A credit roll needs cash too: the WMT roll pays 700.00 for its first leg before the second
leg's credit arrives, so it needs 700.00, not its net credit of 10.00. A margin account is compared with the stated cash
only: the desk models no margin.

What the check says (:func:`check`)
-----------------------------------
UNAVAILABLE (never passed) when the account states no cash, when the legs' cash is unknown (a leg without a usable
quote on the side it needs), when ``cash_as_of`` is after today's ET date, or when it is more than
:data:`CASH_MAX_AGE_TRADING_DAYS` trading days before it (by the holiday table). Otherwise it states both figures and
the figure's age; a shortfall is a failed check item, never a refusal.

Pure: every input is passed in.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Dict, Optional, Sequence, Tuple

from . import market_clock

CASH_MAX_AGE_TRADING_DAYS: int = 5
"""The oldest stated cash figure the check reads, in trading days before today's ET date."""
STATUS_OK: str = "ok"
STATUS_SHORT: str = "short"
STATUS_UNAVAILABLE: str = "unavailable"
REASON_NOT_STATED: str = "no cash stated for this account in desk.toml"
REASON_NO_ACCOUNT: str = "the account is not one of desk.toml's [[owner.accounts]]"
REASON_NO_QUOTE: str = "a leg has no usable quote on the side it needs"
REASON_FUTURE: str = "the stated cash is dated after today"
REASON_STALE: str = "the stated cash is more than %d trading days old" % CASH_MAX_AGE_TRADING_DAYS
_ZERO = Decimal(0)


@dataclass(frozen=True)
class Step:
    """One leg's cash in order: ``premium`` (positive received, negative paid; ``None`` without a usable quote) and
    ``secured`` (the cash a short put opened secures, negative for one closed, zero otherwise)."""

    premium: Optional[Decimal]
    secured: Decimal = _ZERO


@dataclass(frozen=True)
class CashCheck:
    """The check (module notes): ``need`` and ``after_step`` are the peak and the step it falls after; ``cash``,
    ``as_of`` and ``age_trading_days`` the stated figure when there is one."""

    status: str
    reason: Optional[str]
    need: Optional[Decimal]
    after_step: Optional[int]
    steps: int
    cash: Optional[Decimal]
    as_of: Optional[datetime.date]
    age_trading_days: Optional[int]

    @property
    def shortfall(self) -> Optional[Decimal]:
        if self.status != STATUS_SHORT or self.need is None or self.cash is None:
            return None
        return self.need - self.cash

    def as_dict(self) -> Dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "need": self.need, "after_step": self.after_step,
                "steps": self.steps, "cash": self.cash, "as_of": None if self.as_of is None else self.as_of.isoformat(),
                "age_trading_days": self.age_trading_days, "shortfall": self.shortfall}


def peak_need(steps: Sequence[Step]) -> Optional[Tuple[Decimal, int]]:
    """``(the peak cash out, the step it falls after)`` over ``steps`` in order (module notes), or ``None`` when a
    step's premium is unknown. A ticket that never takes cash out needs ``0.00`` after its first step."""
    if not steps:
        raise ValueError("a check needs at least one leg")
    out = _ZERO
    peak, peak_step = _ZERO, 1
    for index, step in enumerate(steps, 1):
        if step.premium is None:
            return None
        out += -step.premium + step.secured
        if out > peak:
            peak, peak_step = out, index
    return peak.quantize(Decimal("0.01")), peak_step


def check(steps: Sequence[Step], account: Any, today: datetime.date,
          extra_holidays: market_clock.ExtraHolidays = ()) -> CashCheck:
    """The cash check of ``steps`` against ``account`` (a ``desk_config.AccountEntry`` or ``None``) on ``today``."""
    count = len(steps)
    found = peak_need(steps)
    need, after = (None, None) if found is None else found
    cash = getattr(account, "cash", None)
    as_of = getattr(account, "cash_as_of", None)

    def unavailable(reason: str, age: Optional[int] = None) -> CashCheck:
        return CashCheck(STATUS_UNAVAILABLE, reason, need, after, count, cash, as_of, age)

    if account is None:
        return unavailable(REASON_NO_ACCOUNT)
    if cash is None or as_of is None:
        return unavailable(REASON_NOT_STATED)
    if found is None:
        return unavailable(REASON_NO_QUOTE)
    if as_of > today:
        return unavailable(REASON_FUTURE)
    age = market_clock.trading_days_between(as_of, today, extra_holidays)
    if age > CASH_MAX_AGE_TRADING_DAYS:
        return unavailable(REASON_STALE, age)
    assert need is not None
    status = STATUS_SHORT if need > cash else STATUS_OK
    return CashCheck(status, None, need, after, count, cash, as_of, age)


__all__ = [
    "CASH_MAX_AGE_TRADING_DAYS",
    "CashCheck",
    "REASON_FUTURE",
    "REASON_NOT_STATED",
    "REASON_NO_ACCOUNT",
    "REASON_NO_QUOTE",
    "REASON_STALE",
    "STATUS_OK",
    "STATUS_SHORT",
    "STATUS_UNAVAILABLE",
    "Step",
    "check",
    "peak_need",
]
