"""
helper.py
---------
Pure utility functions shared across the framework and all strategies.

All functions here are stateless and side-effect free — safe to call from
business logic and unit tests alike.
"""

import math
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Optional

from .constants import IST_TIMEZONE, ROUND_BASE_100, ROUND_BASE_50


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def now_ist() -> datetime:
    """Return current datetime in IST."""
    return datetime.now(ZoneInfo(IST_TIMEZONE))


def today_ist() -> date:
    """Return current date in IST."""
    return now_ist().date()


def is_after_time(time_str: str) -> bool:
    """
    Return True if the current IST time is at or after *time_str* (``HH:MM``).

    Example::

        is_after_time("09:30")  # True after 9:30 AM IST
    """
    now = now_ist()
    hh, mm = map(int, time_str.split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return now >= target


def is_before_time(time_str: str) -> bool:
    """Return True if the current IST time is strictly before *time_str*."""
    return not is_after_time(time_str)


def seconds_until(time_str: str) -> float:
    """
    Seconds until the next occurrence of *time_str* (``HH:MM``) today in IST.

    Returns a negative value if the time has already passed today.
    """
    now = now_ist()
    hh, mm = map(int, time_str.split(":"))
    target = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return (target - now).total_seconds()


# ---------------------------------------------------------------------------
# Strike / rounding helpers
# ---------------------------------------------------------------------------

def round_up_to(value: float, base: int) -> int:
    """
    Round *value* **up** to the nearest multiple of *base*.

    Examples::

        round_up_to(740, 100)  → 800
        round_up_to(510, 50)   → 550
        round_up_to(600, 50)   → 600   (already a multiple)
    """
    return int(math.ceil(value / base) * base)


def round_to_nearest(value: float, base: int) -> int:
    """
    Round *value* to the **nearest** multiple of *base*.

    Examples::

        round_to_nearest(24982, 50)  → 25000
        round_to_nearest(24960, 50)  → 24950
    """
    return int(round(value / base) * base)


def find_atm_strike(spot: float, strike_interval: int = 50) -> float:
    """
    Return the nearest ATM strike to *spot*.

    NIFTY uses 50-point intervals by default.

    Examples::

        find_atm_strike(24982)  → 25000.0
        find_atm_strike(24940)  → 24950.0
    """
    return float(round_to_nearest(spot, strike_interval))


def calculate_wing_strike(center: float, wing_distance: float, side: str) -> float:
    """
    Calculate the hedge wing strike.

    Parameters
    ----------
    center        : Center (short straddle) strike
    wing_distance : Distance from center to wing
    side          : ``"upper"`` for call wing, ``"lower"`` for put wing

    Examples::

        calculate_wing_strike(25000, 800, "upper")  → 25800.0
        calculate_wing_strike(25000, 800, "lower")  → 24200.0
    """
    if side == "upper":
        return center + wing_distance
    return center - wing_distance


# ---------------------------------------------------------------------------
# Format helpers
# ---------------------------------------------------------------------------

def format_inr(value: float) -> str:
    """
    Format a float as an Indian Rupee string.

    Examples::

        format_inr(5250)    → "₹5,250"
        format_inr(-820.5)  → "₹-821"
    """
    return f"₹{int(value):,}"


def format_strike(strike: float) -> str:
    """Return strike as a clean integer string."""
    return str(int(strike))


# ---------------------------------------------------------------------------
# OpenAlgo expiry format
# ---------------------------------------------------------------------------

def expiry_to_openalgo_fmt(expiry_date: date) -> str:
    """
    Convert a Python ``date`` to OpenAlgo expiry format ``DDMMMYY``.

    Example::

        expiry_to_openalgo_fmt(date(2025, 7, 31))  → "31JUL25"
    """
    return expiry_date.strftime("%d%b%y").upper()


def openalgo_fmt_to_date(expiry_str: str) -> date:
    """
    Parse an OpenAlgo expiry string into a Python ``date``.

    Handles both formats returned by different OpenAlgo endpoints:
      - ``DDMMMYY``   e.g. ``31JUL25``   (option chain / symbol format)
      - ``DD-MON-YY`` e.g. ``04-AUG-26`` (expiry list API format)
    """
    for fmt in ("%d%b%y", "%d-%b-%y"):
        try:
            return datetime.strptime(expiry_str, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Cannot parse expiry date string: '{expiry_str}'")


# ---------------------------------------------------------------------------
# Option symbol builder
# ---------------------------------------------------------------------------

def build_option_symbol(
    underlying: str,
    expiry: str,
    strike: float,
    option_type: str,
) -> str:
    """
    Build a fully qualified OpenAlgo option symbol.

    Parameters
    ----------
    underlying  : e.g. ``"NIFTY"``
    expiry      : OpenAlgo format e.g. ``"31JUL25"``
    strike      : e.g. ``25000.0``
    option_type : ``"CE"`` or ``"PE"``

    Returns
    -------
    str — e.g. ``"NIFTY31JUL2525000CE"``
    """
    return f"{underlying}{expiry}{int(strike)}{option_type}"


# ---------------------------------------------------------------------------
# Safe numeric conversion
# ---------------------------------------------------------------------------

def safe_float(value, default: float = 0.0) -> float:
    """Convert *value* to float, returning *default* on failure."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value, default: int = 0) -> int:
    """Convert *value* to int, returning *default* on failure."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default
