"""
expiry.py
---------
Expiry management for the Monthly Dynamic Iron Fly strategy.

Responsibilities
----------------
- Determine if today is the first trading day of the month.
- Determine if today is the expiry day.
- Determine if tomorrow is the expiry day (T-1 check).
- Read and refresh the monthly expiry from OpenAlgo.
- Identify whether a gap-opening has occurred at the start of the session.

All logic here is date/time arithmetic — no OpenAlgo calls.
OpenAlgo calls are handled by ContextBuilder; this module consumes the result.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from ....utils.constants import IST_TIMEZONE
from ....utils.helper import now_ist, openalgo_fmt_to_date, today_ist
from ....utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# First trading day detection
# ---------------------------------------------------------------------------

def is_first_trading_day_of_month() -> bool:
    """
    Return True if today is the first trading day (Mon–Fri) of the calendar month.

    Logic:
    - Find the first calendar day of the current month.
    - Walk forward until the first weekday (Mon=0 … Fri=4).
    - Return True if today equals that date.

    Note: This does not account for exchange holidays.  For production use,
    pass the holiday list from OpenAlgo's market_holidays endpoint and extend
    this function accordingly.
    """
    today = today_ist()
    first_of_month = today.replace(day=1)

    # Walk to the first weekday
    first_trading = first_of_month
    while first_trading.weekday() >= 5:   # 5=Sat, 6=Sun
        first_trading += timedelta(days=1)

    return today == first_trading


def first_trading_day_of_month(reference: Optional[date] = None) -> date:
    """
    Return the first trading day (Mon–Fri) of the month containing *reference*.

    Defaults to the current IST month if *reference* is None.
    """
    ref = reference or today_ist()
    first = ref.replace(day=1)
    while first.weekday() >= 5:
        first += timedelta(days=1)
    return first


# ---------------------------------------------------------------------------
# Expiry helpers
# ---------------------------------------------------------------------------

def is_expiry_day(expiry_str: str) -> bool:
    """Return True if today (IST) is the expiry date."""
    try:
        exp_date = openalgo_fmt_to_date(expiry_str)
        return today_ist() == exp_date
    except ValueError:
        return False


def is_t_minus_1(expiry_str: str) -> bool:
    """
    Return True if today is one trading day before the expiry date.

    T-1 = the last weekday before the expiry date.
    """
    try:
        exp_date = openalgo_fmt_to_date(expiry_str)
        t1 = exp_date - timedelta(days=1)
        while t1.weekday() >= 5:
            t1 -= timedelta(days=1)
        return today_ist() == t1
    except ValueError:
        return False


def days_to_expiry(expiry_str: str) -> int:
    """Return calendar days remaining to expiry. Negative if expired."""
    try:
        exp_date = openalgo_fmt_to_date(expiry_str)
        return (exp_date - today_ist()).days
    except ValueError:
        return -999


# ---------------------------------------------------------------------------
# Market session helpers
# ---------------------------------------------------------------------------

def is_market_open() -> bool:
    """
    Return True if the current IST time is within normal market hours.

    NSE market hours: 09:15 – 15:30 IST, Monday–Friday.
    """
    now = now_ist()
    if now.weekday() >= 5:
        return False
    market_open = now.replace(hour=9, minute=15, second=0, microsecond=0)
    market_close = now.replace(hour=15, minute=30, second=0, microsecond=0)
    return market_open <= now <= market_close


def is_after_deployment_time(deployment_time_str: str) -> bool:
    """
    Return True if the current IST time is at or after *deployment_time_str*.

    Example::

        is_after_deployment_time("09:30")  → True at 09:31 IST
    """
    now = now_ist()
    hh, mm = map(int, deployment_time_str.split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return now >= target


def is_after_expiry_exit_time(exit_time_str: str) -> bool:
    """
    Return True if the current IST time is at or after the expiry exit time.

    Example::

        is_after_expiry_exit_time("11:00")  → True at 11:01 IST
    """
    return is_after_deployment_time(exit_time_str)


def minutes_since_open() -> float:
    """Return the number of minutes elapsed since 09:15 IST today."""
    now = now_ist()
    open_time = now.replace(hour=9, minute=15, second=0, microsecond=0)
    delta = now - open_time
    return delta.total_seconds() / 60.0


# ---------------------------------------------------------------------------
# Monthly cycle
# ---------------------------------------------------------------------------

def should_wait_for_next_month(deployment_time: Optional[datetime], expiry_str: str) -> bool:
    """
    Return True if the strategy has already exited this month and should
    wait for the first trading day of the next calendar month.

    Condition: deployment happened this month AND expiry day has passed.
    """
    if deployment_time is None:
        return False
    today = today_ist()
    if deployment_time.date().month != today.month:
        return False
    # Deployed this month — check if expiry has passed
    return days_to_expiry(expiry_str) < 0
