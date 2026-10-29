"""
market_utils.py
---------------
Trading-window and market-session helpers.

Functions
---------
is_market_open(now)
    True if the current IST time is within normal NSE F&O trading hours.

is_within_window(now, start_hhmm, end_hhmm)
    True if *now* is between two HH:MM strings (both inclusive).

is_trading_day(d)
    True if *d* is a weekday (Mon–Fri).  Does NOT check NSE holidays.

minutes_to_close(now)
    Minutes remaining until 15:30 IST (market close).

is_entry_allowed(now, entry_start, entry_end)
    True if a new position may be entered (within entry window AND market open).

is_exit_window(now, exit_start)
    True if the forced-exit countdown has started (after exit_start HH:MM).

NSE_HOLIDAYS (set[date])
    A manually maintained list of NSE trading holidays for the current year.
    Update this each year.  ``is_trading_day()`` checks against this set.
"""
from __future__ import annotations

from datetime import date, datetime, time
from typing import Optional

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# NSE holiday calendar — update annually
# ---------------------------------------------------------------------------

NSE_HOLIDAYS_2025: set[date] = {
    date(2025, 1, 26),  # Republic Day
    date(2025, 2, 26),  # Mahashivratri
    date(2025, 3, 14),  # Holi
    date(2025, 3, 31),  # Id-Ul-Fitr (Ramadan Eid)
    date(2025, 4, 14),  # Dr. Ambedkar Jayanti
    date(2025, 4, 18),  # Good Friday
    date(2025, 5, 1),   # Maharashtra Day
    date(2025, 8, 15),  # Independence Day
    date(2025, 8, 27),  # Ganesh Chaturthi
    date(2025, 10, 2),  # Mahatma Gandhi Jayanti
    date(2025, 10, 24), # Dussehra
    date(2025, 11, 5),  # Diwali (Laxmi Pujan)
    date(2025, 11, 14), # Gurunanak Jayanti
    date(2025, 12, 25), # Christmas
}

NSE_HOLIDAYS_2026: set[date] = {
    date(2026, 1, 26),  # Republic Day (placeholder — update with official list)
    date(2026, 3, 20),  # Holi (approximate)
    date(2026, 4, 3),   # Good Friday (approximate)
    date(2026, 8, 15),  # Independence Day
    date(2026, 10, 2),  # Gandhi Jayanti
    date(2026, 11, 14), # Diwali (approximate)
    date(2026, 12, 25), # Christmas
}

NSE_HOLIDAYS: set[date] = NSE_HOLIDAYS_2025 | NSE_HOLIDAYS_2026


# ---------------------------------------------------------------------------
# Market hours constants (IST)
# ---------------------------------------------------------------------------

# NSE F&O (equity / index derivatives)
MARKET_OPEN_TIME  = time(9, 15)
MARKET_CLOSE_TIME = time(15, 30)

# MCX commodity futures (Crudeoil, Gold, Silver, etc.)
MCX_OPEN_TIME  = time(9, 0)
MCX_CLOSE_TIME = time(23, 15)   # 11:15 PM IST


# ---------------------------------------------------------------------------
# Core helpers
# ---------------------------------------------------------------------------

def _parse_hhmm(hhmm: str) -> time:
    """Parse ``"HH:MM"`` or ``"HHMM"`` into a ``time`` object."""
    hhmm = hhmm.strip().replace(":", "")
    if len(hhmm) != 4:
        raise ValueError(f"Expected HH:MM or HHMM, got {hhmm!r}")
    return time(int(hhmm[:2]), int(hhmm[2:]))


def is_within_window(now: datetime, start_hhmm: str, end_hhmm: str) -> bool:
    """
    Return ``True`` if *now*'s time component falls within [*start_hhmm*, *end_hhmm*] (inclusive).

    Parameters
    ----------
    now        : Current datetime (timezone-aware or naive — only HH:MM used).
    start_hhmm : Window start as ``"HH:MM"`` e.g. ``"09:30"``.
    end_hhmm   : Window end   as ``"HH:MM"`` e.g. ``"15:00"``.
    """
    t     = now.time().replace(second=0, microsecond=0)
    start = _parse_hhmm(start_hhmm)
    end   = _parse_hhmm(end_hhmm)
    return start <= t <= end


def is_mcx_market_open(now: Optional[datetime] = None) -> bool:
    """
    Return ``True`` if the MCX commodity market is currently open.

    Trading hours: 09:00 – 23:15 IST, Monday–Friday.
    Note: MCX may observe different holidays from NSE.  This function uses
    the same ``is_trading_day()`` check (weekday + NSE holidays) as a proxy.
    Update ``NSE_HOLIDAYS`` with MCX-specific closures if needed.
    """
    if now is None:
        from ..utils.helper import now_ist
        now = now_ist()
    if not is_trading_day(now.date()):
        return False
    t = now.time()
    return MCX_OPEN_TIME <= t <= MCX_CLOSE_TIME


def is_market_open(now: Optional[datetime] = None) -> bool:
    """
    Return ``True`` if *now* is within NSE F&O trading hours.

    Trading hours: 09:15 – 15:30 IST, Monday–Friday, non-holiday.
    """
    if now is None:
        from ..utils.helper import now_ist
        now = now_ist()
    if not is_trading_day(now.date()):
        return False
    t = now.time()
    return MARKET_OPEN_TIME <= t <= MARKET_CLOSE_TIME


def is_trading_day(d: Optional[date] = None) -> bool:
    """
    Return ``True`` if *d* is a weekday and not an NSE holiday.

    Does NOT contact any external service — uses the hardcoded ``NSE_HOLIDAYS`` set.
    """
    if d is None:
        d = date.today()
    if d.weekday() >= 5:        # Saturday = 5, Sunday = 6
        return False
    return d not in NSE_HOLIDAYS


def minutes_to_close(now: Optional[datetime] = None) -> int:
    """
    Return the number of minutes remaining until 15:30 IST.

    Returns 0 if the market is already past close (never negative).
    """
    if now is None:
        from ..utils.helper import now_ist
        now = now_ist()
    close_dt = now.replace(hour=15, minute=30, second=0, microsecond=0)
    delta = (close_dt - now).total_seconds()
    return max(0, int(delta // 60))


def is_entry_allowed(
    now: Optional[datetime] = None,
    entry_start_hhmm: str = "09:30",
    entry_end_hhmm: str = "14:30",
) -> bool:
    """
    Return ``True`` if a new position entry is allowed right now.

    Checks:
    1. Market is open (is_market_open).
    2. Current time is within [entry_start, entry_end].
    """
    if now is None:
        from ..utils.helper import now_ist
        now = now_ist()
    if not is_market_open(now):
        return False
    return is_within_window(now, entry_start_hhmm, entry_end_hhmm)


def is_exit_window(
    now: Optional[datetime] = None,
    exit_start_hhmm: str = "15:00",
) -> bool:
    """
    Return ``True`` if it is time to start the forced-exit countdown.

    This is true when:
    - Market is open.
    - Current time is >= *exit_start_hhmm* (typically 15:00).
    """
    if now is None:
        from ..utils.helper import now_ist
        now = now_ist()
    if not is_market_open(now):
        return False
    t     = now.time().replace(second=0, microsecond=0)
    start = _parse_hhmm(exit_start_hhmm)
    return t >= start


def next_trading_day(from_date: Optional[date] = None) -> date:
    """Return the next calendar date that is a trading day."""
    from datetime import timedelta
    d = (from_date or date.today()) + timedelta(days=1)
    while not is_trading_day(d):
        d += timedelta(days=1)
    return d
