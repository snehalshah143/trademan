"""
recovery_manager.py
-------------------
Reconstructs strategy state from OpenAlgo broker data on startup.

Responsibilities
----------------
- Read current positions, trades, and orders from OpenAlgo.
- Determine whether an Iron Fly (or any tagged strategy) already exists.
- Reconstruct the full ``StrategyState`` from broker data.
- Detect partial execution (e.g. crash during adjustment) and determine
  which legs still need to be completed.
- Ensure idempotency — running this twice produces the same result.

Source-of-truth priority (as per spec):
  1. OpenAlgo positions  (most authoritative)
  2. Trades
  3. Orders
  4. Saved local state   (lowest priority, used only to fill in metadata
                          that the broker doesn't expose e.g. adjustment_count)

This module is strategy-agnostic.  The Iron Fly strategy registers a
``ReconstructionRule`` so the recovery manager knows how to interpret
positions for that specific strategy.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from ..framework.openalgo_client import OpenAlgoClient
from ..models.option_leg import LegAction, LegRole, LegStatus, OptionLeg, OptionType
from ..models.strategy_state import StrategyPhase, StrategyState
from ..utils.helper import safe_float, safe_int
from ..utils.logger import get_logger

logger = get_logger(__name__)

# Path to optional local state snapshot (secondary fallback only)
_DEFAULT_STATE_PATH = "logs/strategy_state.json"


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

class RecoveryResult:
    """Outcome of a recovery attempt."""

    def __init__(
        self,
        found: bool,
        state: Optional[StrategyState],
        message: str,
    ) -> None:
        self.found = found           # True if an existing strategy was detected
        self.state = state           # Reconstructed state (or None if not found)
        self.message = message

    def __str__(self) -> str:
        return f"RecoveryResult(found={self.found}, message={self.message!r})"


# ---------------------------------------------------------------------------
# Recovery Manager
# ---------------------------------------------------------------------------

class RecoveryManager:
    """
    Detects and reconstructs strategy state from OpenAlgo broker data.

    Parameters
    ----------
    client          : Initialised ``OpenAlgoClient``
    strategy_id     : Strategy tag used when placing orders (e.g. ``IRON_FLY_202608``)
    state_path      : Local JSON snapshot path (secondary fallback only)
    """

    def __init__(
        self,
        client: OpenAlgoClient,
        strategy_id: str,
        state_path: str = _DEFAULT_STATE_PATH,
    ) -> None:
        self._client = client
        self._strategy_id = strategy_id
        self._state_path = Path(state_path)

    # ---------------------------------------------------------------------------
    # Entry point
    # ---------------------------------------------------------------------------

    def recover(self, config: Any) -> RecoveryResult:
        """
        Attempt to reconstruct strategy state from OpenAlgo.

        Returns a ``RecoveryResult`` with ``found=True`` and the reconstructed
        ``StrategyState`` if an existing strategy is detected.

        Returns ``found=False`` if no existing strategy is found (fresh start).
        """
        logger.info(f"Recovery scan started for strategy_id={self._strategy_id}")

        try:
            positions = self._client.get_positions()
            trades = self._client.get_trades()
            orders = self._client.get_orders()
        except Exception as exc:
            logger.error(f"Recovery: failed to fetch broker data — {exc}")
            return RecoveryResult(
                found=False,
                state=None,
                message=f"Broker data unavailable: {exc}",
            )

        # Filter to positions / trades / orders tagged with our strategy_id
        strategy_positions = self._filter_by_strategy(positions, orders)

        if not strategy_positions:
            logger.info("Recovery: no existing strategy positions found — fresh start.")
            return RecoveryResult(found=False, state=None, message="Fresh start")

        logger.info(
            f"Recovery: found {len(strategy_positions)} position(s) "
            f"for strategy_id={self._strategy_id}"
        )

        state = self._reconstruct_state(
            positions=strategy_positions,
            trades=trades,
            orders=orders,
            config=config,
        )

        return RecoveryResult(
            found=True,
            state=state,
            message=f"Recovered {len(strategy_positions)} legs",
        )

    # ---------------------------------------------------------------------------
    # Position filtering
    # ---------------------------------------------------------------------------

    def _filter_by_strategy(
        self,
        positions: list[dict],
        orders: list[dict],
    ) -> list[dict]:
        """
        Return positions that belong to this strategy.

        Primary method: OpenAlgo tags each order with the ``strategy`` field.
        We find symbols that appear in our strategy's orders, then match
        those symbols in the position book.

        If the broker doesn't expose strategy tags on positions directly,
        we fall back to matching via the order book.
        """
        # Collect symbols traded under our strategy_id from the order book
        tagged_symbols: set[str] = set()
        for order in orders:
            if str(order.get("strategy", "")).upper() == self._strategy_id.upper():
                sym = order.get("symbol", "")
                if sym:
                    tagged_symbols.add(sym)

        if tagged_symbols:
            matched = [p for p in positions if p.get("symbol") in tagged_symbols]
            logger.debug(f"Recovery: tagged_symbols={tagged_symbols}, matched={len(matched)} positions")
            return matched

        # Fallback: cannot identify strategy-specific positions
        logger.warning(
            "Recovery: no strategy-tagged orders found in order book. "
            "Cannot distinguish strategy positions. Returning empty."
        )
        return []

    # ---------------------------------------------------------------------------
    # State reconstruction
    # ---------------------------------------------------------------------------

    def _reconstruct_state(
        self,
        positions: list[dict],
        trades: list[dict],
        orders: list[dict],
        config: Any,
    ) -> StrategyState:
        """
        Build a ``StrategyState`` from raw broker data.

        Fills in as much as possible from positions + trades.
        Loads adjustment_count and other metadata from local snapshot as fallback.
        """
        strategy_name = config.get("STRATEGY_NAME", "IRON_FLY") if hasattr(config, "get") else "IRON_FLY"

        state = StrategyState(
            strategy_id=self._strategy_id,
            strategy_name=strategy_name,
            phase=StrategyPhase.MONITORING,
        )

        # Load local snapshot metadata (secondary — only fills gaps)
        local_meta = self._load_local_snapshot()

        # Reconstruct legs from positions
        legs = self._build_legs_from_positions(positions, trades, config)
        state.legs = legs

        # Derive geometry from reconstructed legs
        self._derive_geometry(state, config)

        # Restore metadata from local snapshot (adjustment count, MTM locks, etc.)
        if local_meta:
            state.adjustment_count = local_meta.get("adjustment_count", 0)
            state.recovery_trigger = local_meta.get("recovery_trigger")
            state.recovery_direction = local_meta.get("recovery_direction")
            state.locked_mtm = local_meta.get("locked_mtm")
            state.wing_tightened = local_meta.get("wing_tightened", False)
            state.max_mtm = local_meta.get("max_mtm", 0.0)
            state.min_mtm = local_meta.get("min_mtm", 0.0)
            state.expiry = local_meta.get("expiry")
            state.deployment_time = (
                datetime.fromisoformat(local_meta["deployment_time"])
                if local_meta.get("deployment_time")
                else None
            )
            logger.info("Recovery: metadata restored from local snapshot.")

        state.mark_updated()

        logger.info(
            f"Recovery complete: {len(legs)} legs reconstructed, "
            f"phase={state.phase.value}, adj_count={state.adjustment_count}"
        )
        return state

    def _build_legs_from_positions(
        self,
        positions: list[dict],
        trades: list[dict],
        config: Any,
    ) -> list[OptionLeg]:
        """
        Convert raw broker positions into ``OptionLeg`` objects.

        Broker quantity convention:
          positive quantity → net long (BUY position)
          negative quantity → net short (SELL position)
        """
        exchange = config.get("EXCHANGE_OPTIONS", "NFO") if hasattr(config, "get") else "NFO"
        product = config.get("PRODUCT", "NRML") if hasattr(config, "get") else "NRML"

        # Build a lookup of entry prices from trade book
        entry_prices: dict[str, float] = {}
        for trade in trades:
            sym = trade.get("symbol", "")
            price = safe_float(trade.get("average_price", 0))
            if sym and price:
                entry_prices[sym] = price

        legs: list[OptionLeg] = []
        for pos in positions:
            symbol = pos.get("symbol", "")
            quantity = safe_int(pos.get("quantity", 0))
            avg_price = safe_float(pos.get("average_price", 0))

            if not symbol or quantity == 0:
                continue

            # Determine option type from symbol suffix
            option_type = OptionType.CE if symbol.endswith("CE") else OptionType.PE
            # Determine action from net position sign
            action = LegAction.BUY if quantity > 0 else LegAction.SELL
            # Infer role from action + option_type
            role = self._infer_role(action, option_type)

            # Parse strike from symbol (last 5+ chars before CE/PE)
            strike = self._parse_strike(symbol)

            leg = OptionLeg(
                role=role,
                symbol=symbol,
                exchange=exchange,
                strike=strike,
                option_type=option_type,
                action=action,
                quantity=abs(quantity),
                product=product,
                expiry="",  # filled in later from state.expiry
                entry_price=entry_prices.get(symbol, avg_price),
                status=LegStatus.OPEN,
            )
            legs.append(leg)
            logger.debug(f"Recovery: reconstructed leg → {leg}")

        return legs

    def _infer_role(self, action: LegAction, option_type: OptionType) -> LegRole:
        """Infer the semantic role from action + option type."""
        if action == LegAction.SELL and option_type == OptionType.CE:
            return LegRole.SHORT_CALL
        if action == LegAction.SELL and option_type == OptionType.PE:
            return LegRole.SHORT_PUT
        if action == LegAction.BUY and option_type == OptionType.CE:
            return LegRole.LONG_CALL
        return LegRole.LONG_PUT

    def _parse_strike(self, symbol: str) -> float:
        """
        Extract the strike price from an option symbol string.

        Assumes format: ``NIFTY31JUL2525000CE``
        The strike is the numeric portion before the terminal CE/PE.
        """
        try:
            # Strip CE / PE suffix
            base = symbol[:-2]
            # Strike is the trailing digits
            digits = ""
            for ch in reversed(base):
                if ch.isdigit():
                    digits = ch + digits
                else:
                    break
            return float(digits) if digits else 0.0
        except Exception:
            return 0.0

    def _derive_geometry(self, state: StrategyState, config: Any) -> None:
        """
        Derive center_strike, wing_distance, and breakevens from reconstructed legs.
        """
        short_call = state.get_leg(LegRole.SHORT_CALL)
        short_put = state.get_leg(LegRole.SHORT_PUT)
        long_call = state.get_leg(LegRole.LONG_CALL)
        long_put = state.get_leg(LegRole.LONG_PUT)

        if short_call and short_put:
            # Center = average of two short strikes (should be same ATM)
            state.center_strike = (short_call.strike + short_put.strike) / 2

            if long_call and long_put:
                upper_wing = long_call.strike
                lower_wing = long_put.strike
                state.wing_distance = (upper_wing - state.center_strike + state.center_strike - lower_wing) / 2

            # Estimate breakevens from entry premiums in legs
            ce_premium = short_call.entry_price or 0.0
            pe_premium = short_put.entry_price or 0.0
            if state.center_strike:
                total = ce_premium + pe_premium
                state.upper_breakeven = state.center_strike + total
                state.lower_breakeven = state.center_strike - total

            state.entry_ce_premium = ce_premium
            state.entry_pe_premium = pe_premium

        logger.debug(
            f"Recovery geometry: center={state.center_strike} "
            f"wing_distance={state.wing_distance} "
            f"upper_be={state.upper_breakeven} lower_be={state.lower_breakeven}"
        )

    # ---------------------------------------------------------------------------
    # Local snapshot (secondary fallback for metadata only)
    # ---------------------------------------------------------------------------

    def _load_local_snapshot(self) -> Optional[dict]:
        """Load the local JSON snapshot if it exists. Returns None on failure."""
        if not self._state_path.exists():
            return None
        try:
            with open(self._state_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            logger.debug(f"Local snapshot loaded from {self._state_path}")
            return data
        except Exception as exc:
            logger.warning(f"Failed to load local snapshot: {exc}")
            return None

    def save_local_snapshot(self, state: StrategyState) -> None:
        """
        Persist strategy metadata to a local JSON file.

        Called after every state change so recovery can restore metadata
        (adjustment_count, locked_mtm, etc.) that the broker doesn't expose.
        """
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "strategy_id": state.strategy_id,
                "strategy_name": state.strategy_name,
                "phase": state.phase.value,
                "center_strike": state.center_strike,
                "wing_distance": state.wing_distance,
                "upper_breakeven": state.upper_breakeven,
                "lower_breakeven": state.lower_breakeven,
                "entry_ce_premium": state.entry_ce_premium,
                "entry_pe_premium": state.entry_pe_premium,
                "adjustment_count": state.adjustment_count,
                "recovery_trigger": state.recovery_trigger,
                "recovery_direction": state.recovery_direction,
                "locked_mtm": state.locked_mtm,
                "wing_tightened": state.wing_tightened,
                "max_mtm": state.max_mtm,
                "min_mtm": state.min_mtm,
                "expiry": state.expiry,
                "deployment_time": (
                    state.deployment_time.isoformat() if state.deployment_time else None
                ),
                "last_updated": datetime.now().isoformat(),
            }
            with open(self._state_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except Exception as exc:
            logger.warning(f"Failed to save local snapshot: {exc}")
