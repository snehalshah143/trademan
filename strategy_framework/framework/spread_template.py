"""
spread_template.py
------------------
Strike selection and leg-role assignment for directional credit spreads.

A directional credit spread consists of exactly two legs:
  PRIMARY (SHORT) — the sold (short) option that collects premium.
  HEDGE   (LONG)  — the bought (long) option that caps maximum loss.

Supported spread types
----------------------
BULL_PUT_SPREAD   — SHORT PUT (lower) + LONG PUT (even lower)
                    Used when bias is BULLISH.
                    Max profit when spot stays above the short strike.

BEAR_CALL_SPREAD  — SHORT CALL (higher) + LONG CALL (even higher)
                    Used when bias is BEARISH.
                    Max profit when spot stays below the short strike.

Functions
---------
select_bull_put_spread(spot, interval, otm_distance, spread_width)
    Return (primary_strike, hedge_strike) for a bull put spread.

select_bear_call_spread(spot, interval, otm_distance, spread_width)
    Return (primary_strike, hedge_strike) for a bear call spread.

build_spread_legs(...)
    Build the two ``OptionLeg`` objects for a spread, ready to submit.
"""
from __future__ import annotations

from typing import Optional

from ..framework.expiry_utils import ceil_to_strike, floor_to_strike
from ..models.option_leg import LegAction, LegRole, LegStatus, OptionLeg, OptionType
from ..utils.helper import build_option_symbol  # canonical; re-exported here for backward compat
from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Strike selection
# ---------------------------------------------------------------------------

def select_bull_put_spread(
    spot: float,
    interval: float,
    otm_distance: float,
    spread_width: float,
) -> tuple[float, float]:
    """
    Select strikes for a BULL PUT SPREAD.

    Parameters
    ----------
    spot          : Current spot (index) price.
    interval      : Strike interval (e.g. 50 for NIFTY).
    otm_distance  : Points below spot for the short (PRIMARY) put strike.
                    0   = sell the strike immediately at or below spot.
                    +ve = sell further OTM (safer, less premium).
                    -ve = sell ITM (aggressive, more premium collected).
                         e.g. -50 with spot=24820 → sells 24850 PE (ITM)
    spread_width  : Points between short and long (HEDGE) put strikes.

    Returns
    -------
    (primary_strike, hedge_strike) where primary > hedge.

    Logic
    -----
    Primary = floor(spot - otm_distance, interval)
      → always snaps DOWN so the short put is at or below target (OTM for puts)
      → OTM_DISTANCE= 0,  spot=24820 → floor(24820/50)*50 = 24800
      → OTM_DISTANCE=+50, spot=24820 → floor(24770/50)*50 = 24750
      → OTM_DISTANCE=-50, spot=24820 → floor(24870/50)*50 = 24850  (ITM)
    Hedge = floor(primary - spread_width, interval)
      → always further OTM (lower) than primary
    """
    primary_strike = floor_to_strike(spot - otm_distance, interval)
    hedge_strike   = floor_to_strike(primary_strike - spread_width, interval)
    logger.info(
        "select_bull_put_spread: spot=%.0f SHORT_PUT=%.0f LONG_PUT=%.0f",
        spot, primary_strike, hedge_strike,
    )
    return primary_strike, hedge_strike


def select_bear_call_spread(
    spot: float,
    interval: float,
    otm_distance: float,
    spread_width: float,
) -> tuple[float, float]:
    """
    Select strikes for a BEAR CALL SPREAD.

    Parameters
    ----------
    spot          : Current spot (index) price.
    interval      : Strike interval (e.g. 50 for NIFTY).
    otm_distance  : Points above spot for the short (PRIMARY) call strike.
                    0   = sell the strike immediately at or above spot.
                    +ve = sell further OTM (safer, less premium).
                    -ve = sell ITM (aggressive, more premium collected).
                         e.g. -50 with spot=24820 → sells 24800 CE (ITM)
    spread_width  : Points between short and long (HEDGE) call strikes.

    Returns
    -------
    (primary_strike, hedge_strike) where primary < hedge.

    Logic
    -----
    Primary = ceil(spot + otm_distance, interval)
      → always snaps UP so the short call is at or above target (OTM for calls)
      → OTM_DISTANCE= 0,  spot=24820 → ceil(24820/50)*50 = 24850
      → OTM_DISTANCE=+50, spot=24820 → ceil(24870/50)*50 = 24900
      → OTM_DISTANCE=-50, spot=24820 → ceil(24770/50)*50 = 24800  (ITM)
    Hedge = ceil(primary + spread_width, interval)
      → always further OTM (higher) than primary
    """
    primary_strike = ceil_to_strike(spot + otm_distance, interval)
    hedge_strike   = ceil_to_strike(primary_strike + spread_width, interval)
    logger.info(
        "select_bear_call_spread: spot=%.0f SHORT_CALL=%.0f LONG_CALL=%.0f",
        spot, primary_strike, hedge_strike,
    )
    return primary_strike, hedge_strike


# ---------------------------------------------------------------------------
# Leg builder
# ---------------------------------------------------------------------------

def build_spread_legs(
    spread_type: str,
    underlying: str,
    expiry: str,
    exchange: str,
    primary_strike: float,
    hedge_strike: float,
    quantity: int,
    product: str = "NRML",
) -> tuple[OptionLeg, OptionLeg]:
    """
    Build two ``OptionLeg`` objects for a credit spread.

    Parameters
    ----------
    spread_type    : ``"BULL_PUT_SPREAD"`` or ``"BEAR_CALL_SPREAD"``.
    underlying     : Underlying symbol e.g. ``"NIFTY"``.
    expiry         : OpenAlgo expiry string e.g. ``"31JUL25"``.
    exchange       : Exchange code e.g. ``"NFO"``.
    primary_strike : Short (sold) strike.
    hedge_strike   : Long (bought) strike.
    quantity       : Order quantity (lots × lot_size).
    product        : Broker product code (``"NRML"`` or ``"MIS"``).

    Returns
    -------
    (primary_leg, hedge_leg) — both in PENDING status, no order IDs.
    """
    spread_type = spread_type.upper()

    if spread_type == "BULL_PUT_SPREAD":
        primary_opt_type = OptionType.PE
        hedge_opt_type   = OptionType.PE
        primary_role     = LegRole.PRIMARY
        hedge_role       = LegRole.HEDGE
    elif spread_type == "BEAR_CALL_SPREAD":
        primary_opt_type = OptionType.CE
        hedge_opt_type   = OptionType.CE
        primary_role     = LegRole.PRIMARY
        hedge_role       = LegRole.HEDGE
    else:
        raise ValueError(f"Unknown spread_type: {spread_type!r}. "
                         "Expected 'BULL_PUT_SPREAD' or 'BEAR_CALL_SPREAD'.")

    primary_symbol = build_option_symbol(underlying, expiry, primary_strike, primary_opt_type.value)
    hedge_symbol   = build_option_symbol(underlying, expiry, hedge_strike,   hedge_opt_type.value)

    primary_leg = OptionLeg(
        role=primary_role,
        symbol=primary_symbol,
        exchange=exchange,
        strike=primary_strike,
        option_type=primary_opt_type,
        action=LegAction.SELL,
        quantity=quantity,
        product=product,
        expiry=expiry,
    )

    hedge_leg = OptionLeg(
        role=hedge_role,
        symbol=hedge_symbol,
        exchange=exchange,
        strike=hedge_strike,
        option_type=hedge_opt_type,
        action=LegAction.BUY,
        quantity=quantity,
        product=product,
        expiry=expiry,
    )

    logger.info(
        "build_spread_legs[%s]: PRIMARY=%s HEDGE=%s qty=%d",
        spread_type, primary_symbol, hedge_symbol, quantity,
    )
    return primary_leg, hedge_leg


# ---------------------------------------------------------------------------
# Spread metrics
# ---------------------------------------------------------------------------

def max_profit(primary_leg: OptionLeg, hedge_leg: OptionLeg) -> float:
    """
    Theoretical maximum profit for a credit spread.

    = net premium collected (primary entry price − hedge entry price) × quantity

    Returns 0 if either entry price is unavailable.
    """
    if primary_leg.entry_price is None or hedge_leg.entry_price is None:
        return 0.0
    net_credit = primary_leg.entry_price - hedge_leg.entry_price
    return net_credit * primary_leg.quantity


def max_loss(
    primary_leg: OptionLeg,
    hedge_leg: OptionLeg,
    primary_strike: float,
    hedge_strike: float,
) -> float:
    """
    Theoretical maximum loss for a credit spread.

    = (spread_width - net_credit) × quantity

    Returns 0 if entry prices are unavailable.
    """
    if primary_leg.entry_price is None or hedge_leg.entry_price is None:
        return 0.0
    spread_width = abs(primary_strike - hedge_strike)
    net_credit   = primary_leg.entry_price - hedge_leg.entry_price
    return (spread_width - net_credit) * primary_leg.quantity


def spread_net_premium(primary_leg: OptionLeg, hedge_leg: OptionLeg) -> Optional[float]:
    """Net premium collected (positive = credit received)."""
    if primary_leg.entry_price is None or hedge_leg.entry_price is None:
        return None
    return primary_leg.entry_price - hedge_leg.entry_price


def breakeven_strike(
    spread_type: str,
    primary_strike: float,
    net_premium: float,
) -> float:
    """
    Breakeven spot at expiry.

    BULL_PUT_SPREAD : primary_strike - net_premium
    BEAR_CALL_SPREAD: primary_strike + net_premium
    """
    if spread_type.upper() == "BULL_PUT_SPREAD":
        return primary_strike - net_premium
    return primary_strike + net_premium
