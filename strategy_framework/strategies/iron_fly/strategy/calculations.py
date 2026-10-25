"""
calculations.py
---------------
Pure mathematical functions for the Monthly Dynamic Iron Fly strategy.

All functions here are stateless and side-effect free.
They take numbers in, return numbers out — no I/O, no logging, no state.
This makes them trivially testable and reusable.
"""

from __future__ import annotations

import math
from typing import Optional

from ....utils.helper import round_up_to


# ---------------------------------------------------------------------------
# ATM
# ---------------------------------------------------------------------------

def find_atm(spot: float, strike_interval: int = 50) -> float:
    """
    Find the nearest ATM strike for *spot*.

    Rounds to the nearest multiple of *strike_interval*.

    Examples::

        find_atm(24982)  → 25000.0
        find_atm(24960)  → 24950.0
        find_atm(24925)  → 24950.0
    """
    return float(round(spot / strike_interval) * strike_interval)


# ---------------------------------------------------------------------------
# Wing distance — fresh deployment
# ---------------------------------------------------------------------------

def calculate_fresh_wing_distance(
    ce_premium: float,
    pe_premium: float,
    fresh_buffer: float,
    rounding: int,
) -> float:
    """
    Calculate the hedge wing distance for a fresh Iron Fly deployment.

    Formula:
        wing_distance = ceil((ce_premium + pe_premium + fresh_buffer) / rounding) * rounding

    Parameters
    ----------
    ce_premium   : ATM CE premium (LTP) at deployment time
    pe_premium   : ATM PE premium (LTP) at deployment time
    fresh_buffer : Configurable buffer (FRESH_WING_BUFFER, default 100)
    rounding     : Round-up base (FRESH_WING_ROUNDING, default 100)

    Examples::

        calculate_fresh_wing_distance(330, 310, 100, 100)  → 800.0
        calculate_fresh_wing_distance(200, 180, 100, 100)  → 500.0
    """
    raw = ce_premium + pe_premium + fresh_buffer
    return float(round_up_to(raw, rounding))


# ---------------------------------------------------------------------------
# Wing distance — post-adjustment realignment
# ---------------------------------------------------------------------------

def calculate_adjustment_wing_distance(
    ce_premium: float,
    pe_premium: float,
    adjustment_buffer: float,
    rounding: int,
) -> float:
    """
    Calculate wing distance after an adjustment (when realigning wings).

    Formula:
        wing_distance = ceil((ce_premium + pe_premium + adj_buffer) / rounding) * rounding

    Parameters
    ----------
    ce_premium        : Current ATM CE premium (after adjustment, before realignment)
    pe_premium        : Current ATM PE premium (after adjustment, before realignment)
    adjustment_buffer : Configurable buffer (ADJUSTMENT_BUFFER, default 50)
    rounding          : Round-up base (ADJUSTMENT_ROUNDING, default 50)

    Examples::

        calculate_adjustment_wing_distance(260, 250, 50, 50)  → 600.0
        calculate_adjustment_wing_distance(200, 200, 50, 50)  → 450.0
    """
    raw = ce_premium + pe_premium + adjustment_buffer
    return float(round_up_to(raw, rounding))


# ---------------------------------------------------------------------------
# Breakevens
# ---------------------------------------------------------------------------

def calculate_breakevens(
    center_strike: float,
    ce_premium: float,
    pe_premium: float,
) -> tuple[float, float]:
    """
    Calculate upper and lower breakeven levels for a short straddle.

    Formula:
        upper_breakeven = center_strike + ce_premium + pe_premium
        lower_breakeven = center_strike - ce_premium - pe_premium

    Returns
    -------
    (upper_breakeven, lower_breakeven)

    Examples::

        calculate_breakevens(25000, 330, 310)  → (25640.0, 24360.0)
    """
    total_premium = ce_premium + pe_premium
    return center_strike + total_premium, center_strike - total_premium


# ---------------------------------------------------------------------------
# Wing strikes
# ---------------------------------------------------------------------------

def upper_wing_strike(center: float, wing_distance: float) -> float:
    """Upper hedge wing strike = center + wing_distance."""
    return center + wing_distance


def lower_wing_strike(center: float, wing_distance: float) -> float:
    """Lower hedge wing strike = center - wing_distance."""
    return center - wing_distance


# ---------------------------------------------------------------------------
# Recovery trigger
# ---------------------------------------------------------------------------

def calculate_recovery_trigger(
    adjustment_price: float,
    recovery_buffer: float,
    direction: str,
) -> float:
    """
    Calculate the recovery trigger price.

    The recovery trigger is the spot level that must be crossed before
    wing realignment can occur.

    For a downside adjustment: trigger = adjustment_price + recovery_buffer
    For an upside adjustment:  trigger = adjustment_price - recovery_buffer

    Parameters
    ----------
    adjustment_price : Spot price at the time of the adjustment
    recovery_buffer  : Configurable buffer (RECOVERY_BUFFER, default 50)
    direction        : ``"DOWN"`` for downside, ``"UP"`` for upside

    Examples::

        calculate_recovery_trigger(24460, 50, "DOWN")  → 24510.0
        calculate_recovery_trigger(25540, 50, "UP")    → 25490.0
    """
    if direction == "DOWN":
        return adjustment_price + recovery_buffer
    return adjustment_price - recovery_buffer


# ---------------------------------------------------------------------------
# MTM calculations
# ---------------------------------------------------------------------------

def calculate_leg_pnl(
    action: str,
    entry_price: float,
    current_ltp: float,
    quantity: int,
) -> float:
    """
    Calculate unrealized P&L for a single leg.

    For SELL legs: profit when current LTP < entry price.
    For BUY legs:  profit when current LTP > entry price.

    Parameters
    ----------
    action      : ``"BUY"`` or ``"SELL"``
    entry_price : Average fill price at entry
    current_ltp : Current market price
    quantity    : Number of contracts (unsigned)

    Examples::

        calculate_leg_pnl("SELL", 330, 200, 75)   → 9750.0   (profit)
        calculate_leg_pnl("SELL", 330, 400, 75)   → -5250.0  (loss)
        calculate_leg_pnl("BUY",  100, 150, 75)   → 3750.0   (profit)
    """
    if action == "SELL":
        return (entry_price - current_ltp) * quantity
    return (current_ltp - entry_price) * quantity


# ---------------------------------------------------------------------------
# Adjustment trigger checks
# ---------------------------------------------------------------------------

def is_premium_doubled(
    current_premium: float,
    entry_premium: float,
    factor: float = 2.0,
) -> bool:
    """
    Return True if *current_premium* >= *entry_premium* × *factor*.

    Examples::

        is_premium_doubled(660, 330, 2.0)  → True
        is_premium_doubled(659, 330, 2.0)  → False
    """
    if entry_premium <= 0:
        return False
    return current_premium >= entry_premium * factor


def is_breakeven_breached(
    spot: float,
    breakeven: float,
    buffer: float,
    side: str,
) -> bool:
    """
    Return True if *spot* has breached *breakeven* by more than *buffer*.

    Parameters
    ----------
    spot      : Current underlying price
    breakeven : Upper or lower breakeven level
    buffer    : Configurable buffer (BREAKEVEN_BUFFER, default 25)
    side      : ``"upper"`` or ``"lower"``

    Examples::

        is_breakeven_breached(25675, 25640, 25, "upper")  → True
        is_breakeven_breached(25655, 25640, 25, "upper")  → False
        is_breakeven_breached(24335, 24360, 25, "lower")  → True
    """
    if side == "upper":
        return spot >= breakeven + buffer
    return spot <= breakeven - buffer


def is_recovery_trigger_crossed(
    spot: float,
    recovery_trigger: float,
    direction: str,
) -> bool:
    """
    Return True if spot has crossed the recovery trigger.

    For a downside adjustment (direction="DOWN"):
        Wing realignment possible when spot rises ABOVE the trigger.
    For an upside adjustment (direction="UP"):
        Wing realignment possible when spot falls BELOW the trigger.

    Examples::

        is_recovery_trigger_crossed(24520, 24510, "DOWN")  → True
        is_recovery_trigger_crossed(24505, 24510, "DOWN")  → False
        is_recovery_trigger_crossed(25480, 25490, "UP")    → True
    """
    if direction == "DOWN":
        return spot >= recovery_trigger
    return spot <= recovery_trigger


# ---------------------------------------------------------------------------
# Gap-opening check
# ---------------------------------------------------------------------------

def is_gap_open_beyond_wings(
    spot: float,
    upper_wing: float,
    lower_wing: float,
) -> bool:
    """
    Return True if spot has gapped outside the current hedge wings.

    In this scenario the hedge is no longer effective and the strategy must
    exit and redeploy from scratch.
    """
    return spot > upper_wing or spot < lower_wing


def is_gap_open_beyond_breakeven(
    spot: float,
    upper_breakeven: float,
    lower_breakeven: float,
    buffer: float,
) -> bool:
    """
    Return True if spot opened beyond a breakeven + buffer on the gap open check.

    This triggers the GAP_DELAY wait before adjusting.
    """
    return spot >= upper_breakeven + buffer or spot <= lower_breakeven - buffer
