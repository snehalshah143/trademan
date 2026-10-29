"""
expiry_utils.py
---------------
Expiry selection and option-chain metadata helpers.

Functions
---------
select_expiry(expiries, mode, dte_min, dte_max)
    Pick the right expiry from a list based on a selection mode and DTE range.

get_weekly_expiry(expiries)
    Nearest weekly expiry (smallest DTE >= 1).

get_monthly_expiry(expiries)
    Nearest monthly expiry (last Thursday of the nearest month).

days_to_expiry(expiry_str)
    Calendar days remaining until expiry.

fetch_lot_size(client, underlying, exchange)
    Fetch the lot size for an underlying from the option chain.

fetch_strike_interval(client, underlying, exchange, expiry)
    Infer the strike interval from the live option chain.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Optional

from ..framework.openalgo_client import OpenAlgoClient, OpenAlgoError
from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Date parsing / formatting helpers
# ---------------------------------------------------------------------------

_MONTH_MAP = {
    "JAN": 1,  "FEB": 2,  "MAR": 3,  "APR": 4,
    "MAY": 5,  "JUN": 6,  "JUL": 7,  "AUG": 8,
    "SEP": 9,  "OCT": 10, "NOV": 11, "DEC": 12,
}
_MONTH_ABBR = {v: k for k, v in _MONTH_MAP.items()}

_OPENALGO_DATE_RE = re.compile(r"^(\d{2})([A-Z]{3})(\d{2})$")


def parse_openalgo_date(date_str: str) -> date:
    """
    Parse an OpenAlgo expiry string like ``"31JUL25"`` into a ``date`` object.

    Raises ``ValueError`` on bad input.
    """
    m = _OPENALGO_DATE_RE.match(date_str.upper().strip().replace("-", ""))
    if not m:
        raise ValueError(f"Unrecognised OpenAlgo date format: {date_str!r}")
    day, mon, yr = int(m.group(1)), _MONTH_MAP[m.group(2)], 2000 + int(m.group(3))
    return date(yr, mon, day)


def to_openalgo_date(d: date) -> str:
    """Convert a ``date`` to OpenAlgo string format ``"DDMMMYY"``."""
    return f"{d.day:02d}{_MONTH_ABBR[d.month]}{str(d.year)[-2:]}"


def days_to_expiry(expiry_str: str, from_date: Optional[date] = None) -> int:
    """
    Return the number of calendar days from *from_date* (default: today) to *expiry_str*.

    Returns 0 if the expiry is today, negative if already past.
    """
    ref = from_date or date.today()
    return (parse_openalgo_date(expiry_str) - ref).days


# ---------------------------------------------------------------------------
# Expiry selection
# ---------------------------------------------------------------------------

def select_expiry(
    expiries: list[str],
    mode: str = "WEEKLY",
    dte_min: int = 1,
    dte_max: int = 60,
    rollover_hhmm: str = "11:00",
) -> Optional[str]:
    """
    Pick the best expiry from a list of OpenAlgo expiry strings.

    Parameters
    ----------
    expiries      : List of expiry strings e.g. ``["07MAY25", "15MAY25", "29MAY25"]``
    mode          : Selection mode — ``"WEEKLY"`` (nearest), ``"MONTHLY"`` (last of month),
                    ``"NEAREST"`` (smallest DTE within range), or ``"DTE_RANGE"`` (any in range).
    dte_min       : Minimum DTE (inclusive).  Expiries sooner than this are skipped.
    dte_max       : Maximum DTE (inclusive).  Expiries beyond this are skipped.
    rollover_hhmm : IST time at which a DTE=1 expiry is rolled over to the next one.
                    Default ``"11:00"`` — before 11 AM keep DTE=1, at/after 11 AM skip it.

    Rollover logic
    --------------
    - DTE <= 0                          → skip (already expired or today)
    - DTE == 1 AND time < rollover_hhmm → include (enough time to trade today)
    - DTE == 1 AND time >= rollover_hhmm→ skip (too late; use next expiry)
    - DTE >= 2 (up to dte_max)          → always include

    Returns
    -------
    The selected expiry string, or ``None`` if no suitable expiry is found.
    """
    from ..utils.helper import now_ist

    today    = date.today()
    now_time = now_ist().strftime("%H:%M")

    # Parse and filter with rollover-aware DTE logic
    candidates: list[tuple[str, date, int]] = []   # (str, date, dte)
    for exp_str in expiries:
        try:
            d = parse_openalgo_date(exp_str)
        except ValueError:
            continue
        dte = (d - today).days

        if dte <= 0:
            continue                          # expired or today — skip
        if dte == 1 and now_time >= rollover_hhmm:
            logger.debug(
                "select_expiry: skipping DTE=1 expiry %s (time=%s >= rollover=%s)",
                exp_str, now_time, rollover_hhmm,
            )
            continue                          # too late for expiry-day trade
        if dte > dte_max:
            continue                          # beyond the allowed window

        candidates.append((exp_str, d, dte))

    if not candidates:
        logger.warning(
            "select_expiry: no expiries in DTE window [%d, %d] from %d available",
            dte_min, dte_max, len(expiries),
        )
        return None

    candidates.sort(key=lambda x: x[2])   # ascending by DTE

    if mode in ("WEEKLY", "NEAREST"):
        chosen_str, _, chosen_dte = candidates[0]
        logger.info(
            "select_expiry[%s]: chose %s (DTE=%d)", mode, chosen_str, chosen_dte
        )
        return chosen_str

    if mode == "MONTHLY":
        # Monthly = last expiry in the nearest month that has any candidate
        nearest_month = candidates[0][1].month
        nearest_year  = candidates[0][1].year
        month_cands = [c for c in candidates if c[1].month == nearest_month and c[1].year == nearest_year]
        if month_cands:
            chosen_str, _, chosen_dte = max(month_cands, key=lambda x: x[2])
            logger.info(
                "select_expiry[MONTHLY]: chose %s (DTE=%d)", chosen_str, chosen_dte
            )
            return chosen_str

    if mode == "DTE_RANGE":
        # Pick the middle of the range
        mid_idx = len(candidates) // 2
        chosen_str, _, chosen_dte = candidates[mid_idx]
        logger.info(
            "select_expiry[DTE_RANGE]: chose %s (DTE=%d)", chosen_str, chosen_dte
        )
        return chosen_str

    # Fallback: nearest
    return candidates[0][0]


def get_weekly_expiry(expiries: list[str]) -> Optional[str]:
    """Return the nearest weekly expiry with DTE >= 1."""
    return select_expiry(expiries, mode="WEEKLY", dte_min=1, dte_max=14)


def get_monthly_expiry(expiries: list[str]) -> Optional[str]:
    """Return the nearest monthly expiry (last expiry in current/next month)."""
    return select_expiry(expiries, mode="MONTHLY", dte_min=1, dte_max=60)


# ---------------------------------------------------------------------------
# Lot size & strike interval
# ---------------------------------------------------------------------------

def fetch_lot_size(
    client: OpenAlgoClient,
    underlying: str,
    exchange_index: str,
    expiry: Optional[str] = None,
    exchange_options: str = "NFO",
) -> int:
    """
    Fetch the lot size for *underlying* from the option chain.

    Strategy: call ``optionchain()`` and read ``quantity`` from the first
    chain entry that has a non-zero value.  Falls back to 1 on any failure.
    """
    try:
        if expiry is None:
            from ..utils.constants import INSTRUMENT_OPTIONS
            expiries = client.get_expiry_dates(
                symbol=underlying,
                exchange=exchange_options,
                instrumenttype=INSTRUMENT_OPTIONS,
            )
            expiry = expiries[0] if expiries else None

        if expiry is None:
            logger.warning("fetch_lot_size: no expiry available — returning 1")
            return 1

        chain_data = client.get_option_chain(
            underlying=underlying,
            exchange=exchange_index,
            expiry_date=expiry,
            strike_count=5,
        )
        for entry in chain_data.get("chain", []):
            for side in ("ce", "pe"):
                qty = entry.get(side, {}).get("quantity")
                if qty and int(qty) > 0:
                    lot_size = int(qty)
                    logger.info("fetch_lot_size[%s]: lot_size=%d", underlying, lot_size)
                    return lot_size

    except (OpenAlgoError, Exception) as exc:
        logger.warning("fetch_lot_size failed (%s) — returning 1", exc)

    return 1


def fetch_strike_interval(
    client: OpenAlgoClient,
    underlying: str,
    exchange_index: str,
    expiry: str,
) -> float:
    """
    Infer the strike interval from the live option chain.

    Takes the first two strikes in the chain and returns their difference.
    Falls back to 50 (NIFTY default) on failure.
    """
    try:
        chain_data = client.get_option_chain(
            underlying=underlying,
            exchange=exchange_index,
            expiry_date=expiry,
            strike_count=10,
        )
        strikes = sorted({
            float(entry["strike"])
            for entry in chain_data.get("chain", [])
            if entry.get("strike")
        })
        if len(strikes) >= 2:
            interval = strikes[1] - strikes[0]
            logger.info(
                "fetch_strike_interval[%s]: interval=%.0f (from %s/%s)",
                underlying, interval, strikes[0], strikes[1],
            )
            return interval
    except (OpenAlgoError, Exception) as exc:
        logger.warning("fetch_strike_interval failed (%s) — returning 50", exc)

    return 50.0


def round_to_strike(price: float, interval: float) -> float:
    """Round *price* to the nearest valid strike given *interval*."""
    if interval <= 0:
        return price
    return round(price / interval) * interval


def floor_to_strike(price: float, interval: float) -> float:
    """
    Floor *price* DOWN to the nearest valid strike given *interval*.

    Used for PUT strike selection — ensures the strike is always AT OR BELOW
    the target price (i.e. OTM for puts).

    Example: floor_to_strike(24770, 50) → 24750
    """
    import math
    if interval <= 0:
        return price
    return math.floor(price / interval) * interval


def ceil_to_strike(price: float, interval: float) -> float:
    """
    Ceil *price* UP to the nearest valid strike given *interval*.

    Used for CALL strike selection — ensures the strike is always AT OR ABOVE
    the target price (i.e. OTM for calls).

    Example: ceil_to_strike(24820, 50) → 24850
    Example: ceil_to_strike(24870, 50) → 24900
    """
    import math
    if interval <= 0:
        return price
    return math.ceil(price / interval) * interval


def nearest_otm_strike(
    spot: float,
    option_type: str,
    interval: float,
    distance_points: float,
) -> float:
    """
    Return the nearest OTM strike at *distance_points* from *spot*.

    Parameters
    ----------
    option_type      : ``"CE"`` (strike > spot) or ``"PE"`` (strike < spot).
    distance_points  : Desired distance from ATM (e.g. 200 points for a 200-wide spread).
    """
    atm = round_to_strike(spot, interval)
    if option_type.upper() == "CE":
        raw = atm + distance_points
    else:
        raw = atm - distance_points
    return round_to_strike(raw, interval)
