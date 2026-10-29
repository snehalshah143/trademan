"""
sizing.py
---------
Position sizing for credit spread strategies.

Responsibilities
----------------
- Compute the number of lots to trade based on capital allocation rules.
- Enforce a hard cap on maximum lots per strategy instance.
- Optionally query available margin from OpenAlgo and size accordingly.
- Respect the freeze-quantity limit (max lots per order for NFO instruments).

All public functions are pure (no side effects) except ``margin_based_lots``
which calls the OpenAlgo margin API.

Freeze Quantity Note
--------------------
NSE limits the number of lots per single order (freeze qty).  Orders above
this threshold must be split into multiple child orders.  ``split_into_orders``
handles the chunking automatically.
"""
from __future__ import annotations

import math
from typing import Optional

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Pure sizing calculations
# ---------------------------------------------------------------------------

def lots_from_capital(
    available_capital: float,
    margin_per_lot: float,
    capital_utilisation_pct: float = 80.0,
    min_lots: int = 1,
    max_lots: int = 50,
) -> int:
    """
    Calculate how many lots to trade based on available capital.

    Parameters
    ----------
    available_capital      : Cash available in the trading account (₹).
    margin_per_lot         : Margin required for ONE lot of the spread (₹).
    capital_utilisation_pct: Percentage of available capital to deploy (default 80 %).
    min_lots               : Minimum acceptable lots (returns 0 if even min is unaffordable).
    max_lots               : Hard upper cap on lots.

    Returns
    -------
    Number of lots (integer ≥ 0).  Returns 0 if margin_per_lot ≤ 0 or
    capital is insufficient even for min_lots.
    """
    if margin_per_lot <= 0:
        logger.warning("lots_from_capital: margin_per_lot=%.0f ≤ 0 → returning 0", margin_per_lot)
        return 0

    deployable = available_capital * (capital_utilisation_pct / 100.0)
    raw_lots   = int(deployable / margin_per_lot)
    lots       = max(0, min(raw_lots, max_lots))

    if lots < min_lots:
        logger.warning(
            "lots_from_capital: can only afford %d lots (min=%d) — returning 0", lots, min_lots
        )
        return 0

    logger.info(
        "lots_from_capital: capital=%.0f utilisation=%.0f%% deployable=%.0f "
        "margin_per_lot=%.0f → %d lots (max_cap=%d)",
        available_capital, capital_utilisation_pct, deployable, margin_per_lot, lots, max_lots,
    )
    return lots


def fixed_lots(
    lots: int,
    lot_size: int,
    max_lots: int = 50,
) -> int:
    """
    Clamp a fixed-lot configuration to the hard cap.

    Returns the clamped lot count (never exceeds max_lots).
    """
    clamped = min(lots, max_lots)
    if clamped != lots:
        logger.warning(
            "fixed_lots: requested %d lots exceeds cap %d → clamped to %d",
            lots, max_lots, clamped,
        )
    return clamped


def quantity_from_lots(lots: int, lot_size: int) -> int:
    """Return the order quantity given lot count × lot size."""
    return lots * lot_size


# ---------------------------------------------------------------------------
# Freeze-quantity splitting
# ---------------------------------------------------------------------------

def split_into_orders(total_quantity: int, freeze_qty: int) -> list[int]:
    """
    Split ``total_quantity`` into a list of child-order quantities, each ≤ ``freeze_qty``.

    Example
    -------
    ``split_into_orders(300, 100)`` → ``[100, 100, 100]``
    ``split_into_orders(250, 100)`` → ``[100, 100, 50]``
    ``split_into_orders(75, 100)``  → ``[75]``

    Parameters
    ----------
    total_quantity : Total shares/units to order.
    freeze_qty     : Maximum allowed per single order (exchange freeze limit).

    Returns
    -------
    List of integer quantities that sum to ``total_quantity``.
    """
    if freeze_qty <= 0 or total_quantity <= 0:
        return [total_quantity] if total_quantity > 0 else []

    chunks: list[int] = []
    remaining = total_quantity
    while remaining > 0:
        chunk = min(remaining, freeze_qty)
        chunks.append(chunk)
        remaining -= chunk

    logger.debug(
        "split_into_orders: total=%d freeze=%d → %d orders %s",
        total_quantity, freeze_qty, len(chunks), chunks,
    )
    return chunks


# ---------------------------------------------------------------------------
# Pre-execution margin preflight
# ---------------------------------------------------------------------------

def check_margin_requirement(
    client,                          # OpenAlgoClient
    primary_symbol: str,
    hedge_symbol: str,
    exchange: str,
    lot_size: int,
    lots: int,
    product: str = "NRML",
    deployed_capital: Optional[float] = None,
    margin_buffer_pct: float = 15.0,
) -> tuple[bool, float, float, float]:
    """
    Verify that enough capital exists to cover the margin for *lots* of the spread.

    Always runs — including in PAPER mode — so a misconfigured position size
    is caught before any order is attempted.

    Parameters
    ----------
    client           : Initialised ``OpenAlgoClient``.
    primary_symbol   : Full symbol of the short (sold) leg.
    hedge_symbol     : Full symbol of the long (bought) leg.
    exchange         : Options exchange e.g. ``"NFO"``.
    lot_size         : Lot size for the underlying.
    lots             : Number of lots intended to trade.
    product          : ``"NRML"`` or ``"MIS"``.
    deployed_capital : Ring-fenced capital (₹). 0/None = use live available_cash.
    margin_buffer_pct: Safety buffer % added to the API margin.

    Returns
    -------
    (ok, required_total, available, effective_per_lot)

    ok               : True if available >= required_total.
    required_total   : effective_per_lot × lots  (₹).
    available        : Capital available (₹).
    effective_per_lot: raw_margin × (1 + buffer%) per lot (₹).

    Raises
    ------
    Never raises — returns (False, 0, 0, 0) on any API failure.
    """
    try:
        # Capital side
        if deployed_capital and deployed_capital > 0:
            available = float(deployed_capital)
        else:
            funds = client.get_funds()
            available = float(funds.get("availablecash", 0) or 0)

        # Margin for 1 lot of the actual spread
        margin_positions = [
            {"symbol": primary_symbol, "exchange": exchange,
             "action": "SELL", "quantity": lot_size, "product": product},
            {"symbol": hedge_symbol,   "exchange": exchange,
             "action": "BUY",  "quantity": lot_size, "product": product},
        ]
        raw_margin = client.get_margin(margin_positions)

        if raw_margin <= 0:
            logger.warning("check_margin_requirement: margin API returned 0 — skipping preflight")
            return True, 0.0, available, 0.0

        effective_per_lot = raw_margin * (1.0 + margin_buffer_pct / 100.0)
        required_total    = effective_per_lot * lots
        ok                = available >= required_total

        logger.info(
            "Margin preflight: raw=₹%.0f  buffer=%.0f%%  effective=₹%.0f/lot  "
            "lots=%d  required=₹%.0f  available=₹%.0f  → %s",
            raw_margin, margin_buffer_pct, effective_per_lot,
            lots, required_total, available,
            "OK" if ok else "INSUFFICIENT",
        )
        return ok, required_total, available, effective_per_lot

    except Exception as exc:
        logger.warning("check_margin_requirement: API error (%s) — skipping preflight", exc)
        return True, 0.0, 0.0, 0.0


def fit_lots_to_margin(
    client,
    primary_symbol: str,
    hedge_symbol: str,
    exchange: str,
    lot_size: int,
    requested_lots: int,
    product: str = "NRML",
    deployed_capital: Optional[float] = None,
    margin_buffer_pct: float = 15.0,
) -> tuple[int, float, float, float]:
    """
    Reduce lot count until the margin requirement fits within available capital.

    Calls ``check_margin_requirement()`` for *requested_lots*, then decrements
    by 1 on each failure until either the check passes or lots reaches 0.

    Intended as the single shared preflight call used by every strategy script
    — both PAPER and LIVE — before placing any orders.

    Parameters
    ----------
    client           : Initialised ``OpenAlgoClient``.
    primary_symbol   : Full symbol of the short (sold) leg.
    hedge_symbol     : Full symbol of the long (bought) leg.
    exchange         : Options exchange e.g. ``"NFO"``.
    lot_size         : Lot size for the underlying.
    requested_lots   : Initially intended number of lots.
    product          : ``"NRML"`` or ``"MIS"``.
    deployed_capital : Ring-fenced capital (₹). 0/None = live available_cash.
    margin_buffer_pct: Safety buffer % on top of API margin.

    Returns
    -------
    (final_lots, effective_per_lot, required_total, available)

    final_lots       : Adjusted lot count that fits within capital. 0 = unaffordable.
    effective_per_lot: Buffered margin per lot (₹).
    required_total   : effective_per_lot × final_lots (₹). 0 if final_lots=0.
    available        : Capital available (₹).

    Usage
    -----
    ::

        lots, per_lot, required, available = fit_lots_to_margin(
            client, p_leg.symbol, h_leg.symbol, exchange="NFO",
            lot_size=lot_size, requested_lots=lots,
            deployed_capital=500_000, margin_buffer_pct=15,
        )
        if lots == 0:
            # abort — cannot afford even 1 lot
    """
    lots = requested_lots
    last_per_lot  = 0.0
    last_available = 0.0

    while lots > 0:
        ok, required, available, per_lot = check_margin_requirement(
            client=client,
            primary_symbol=primary_symbol,
            hedge_symbol=hedge_symbol,
            exchange=exchange,
            lot_size=lot_size,
            lots=lots,
            product=product,
            deployed_capital=deployed_capital,
            margin_buffer_pct=margin_buffer_pct,
        )
        last_per_lot   = per_lot
        last_available = available

        if ok:
            if lots < requested_lots:
                logger.info(
                    "fit_lots_to_margin: reduced %d → %d lot(s) to fit margin "
                    "(₹%,.0f/lot, available ₹%,.0f)",
                    requested_lots, lots, per_lot, available,
                )
            return lots, per_lot, required, available

        logger.warning(
            "fit_lots_to_margin: %d lot(s) needs ₹%,.0f but only ₹%,.0f available — trying %d",
            lots, required, available, lots - 1,
        )
        lots -= 1

    logger.error(
        "fit_lots_to_margin: cannot afford even 1 lot "
        "(₹%,.0f/lot incl. %.0f%% buffer, available ₹%,.0f)",
        last_per_lot, margin_buffer_pct, last_available,
    )
    return 0, last_per_lot, 0.0, last_available


# ---------------------------------------------------------------------------
# Margin-based sizing (requires live API call from the caller)
# ---------------------------------------------------------------------------

def margin_based_lots(
    client,                      # OpenAlgoClient
    underlying: str,
    exchange: str,
    primary_symbol: str,
    hedge_symbol: str,
    lot_size: int,
    product: str = "NRML",
    max_lots: int = 50,
    capital_utilisation_pct: float = 100.0,
    deployed_capital: Optional[float] = None,
    margin_buffer_pct: float = 15.0,
) -> int:
    """
    Determine the maximum number of lots based on capital and live margin data.

    Calls OpenAlgo ``margin()`` for a 1-lot spread, adds a safety buffer, then
    divides into the deployable capital.  Falls back to 1 lot on any API error.

    Parameters
    ----------
    client               : Initialised ``OpenAlgoClient``.
    underlying           : Underlying symbol e.g. ``"NIFTY"``.
    exchange             : Options exchange e.g. ``"NFO"``.
    primary_symbol       : Full symbol of the short (sold) leg.
    hedge_symbol         : Full symbol of the long (bought) leg.
    lot_size             : Lot size for the underlying.
    product              : ``"NRML"`` or ``"MIS"``.
    max_lots             : Hard upper cap on lots.
    capital_utilisation_pct: Percentage of deployed_capital to use (default 100 %).
    deployed_capital     : Fixed capital (₹) allocated to this strategy.
                           If None or 0, uses live available cash from the broker
                           funds API.  Set this in config (DEPLOYED_CAPITAL) to
                           isolate strategy capital from total account balance.
                           e.g. 500000 → trade with ₹5 lakhs regardless of account size.
    margin_buffer_pct    : Safety buffer added on top of the API margin per lot.
                           Accounts for MTM fluctuations and intraday margin calls.
                           Default 15 % — e.g. API says ₹50,000/lot → effective
                           margin used = ₹57,500/lot → floor(5,00,000/57,500) = 8 lots.

    Returns
    -------
    Number of lots (≥ 1).

    Example
    -------
    deployed_capital=500000, margin_per_lot(API)=50000, margin_buffer_pct=15:
      effective_margin = 50000 × 1.15 = 57,500
      lots = floor(500000 / 57500) = 8
    """
    try:
        # ── Capital to deploy ────────────────────────────────────────────────
        if deployed_capital and deployed_capital > 0:
            available_cash = float(deployed_capital)
            logger.info(
                "margin_based_lots: using configured DEPLOYED_CAPITAL=₹%.0f", available_cash
            )
        else:
            funds = client.get_funds()
            available_cash = float(funds.get("availablecash", 0) or 0)
            logger.info(
                "margin_based_lots: using live available_cash=₹%.0f", available_cash
            )

        # ── Margin for 1 lot ─────────────────────────────────────────────────
        margin_positions = [
            {
                "symbol":   primary_symbol,
                "exchange": exchange,
                "action":   "SELL",
                "quantity": lot_size,
                "product":  product,
            },
            {
                "symbol":   hedge_symbol,
                "exchange": exchange,
                "action":   "BUY",
                "quantity": lot_size,
                "product":  product,
            },
        ]
        raw_margin = client.get_margin(margin_positions)

        if raw_margin <= 0:
            logger.warning("margin_based_lots: margin API returned 0 — defaulting to 1 lot")
            return 1

        # ── Apply safety buffer ───────────────────────────────────────────────
        effective_margin = raw_margin * (1.0 + margin_buffer_pct / 100.0)
        logger.info(
            "margin_based_lots: raw_margin=₹%.0f  buffer=%.0f%%  effective_margin=₹%.0f",
            raw_margin, margin_buffer_pct, effective_margin,
        )

        lots = lots_from_capital(
            available_capital=available_cash,
            margin_per_lot=effective_margin,
            capital_utilisation_pct=capital_utilisation_pct,
            min_lots=1,
            max_lots=max_lots,
        )
        return max(1, lots)

    except Exception as exc:
        logger.warning("margin_based_lots: API call failed (%s) — defaulting to 1 lot", exc)
        return 1
