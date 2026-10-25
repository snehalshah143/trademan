"""
deployment.py
-------------
Iron Fly deployment logic.

Responsibilities
----------------
- Build the four legs of the Iron Fly (2 short + 2 long).
- Verify no duplicate positions exist before deploying (idempotency).
- Execute legs in the mandated order: BUY long wings → verify → SELL short straddle.
- Store initial state after successful deployment.
- Handle partial deployment failures by squaring off already-filled legs.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from ....framework.execution_gateway import ExecutionError, ExecutionGateway
from ....models.option_leg import LegAction, LegRole, LegStatus, OptionLeg, OptionType
from ....models.strategy_context import StrategyContext
from ....models.strategy_state import StrategyPhase, StrategyState
from ....utils.logger import get_logger
from .calculations import (
    calculate_breakevens,
    calculate_fresh_wing_distance,
    find_atm,
    lower_wing_strike,
    upper_wing_strike,
)

logger = get_logger(__name__)


class DeploymentError(Exception):
    """Raised when deployment cannot be completed."""


class IronFlyDeployer:
    """
    Handles the initial deployment of the Iron Fly.

    Parameters
    ----------
    state   : Mutable strategy state
    config  : Config object
    gateway : ExecutionGateway (routes to paper or live)
    """

    def __init__(self, state: StrategyState, config, gateway: ExecutionGateway) -> None:
        self._state = state
        self._config = config
        self._gateway = gateway

    # ---------------------------------------------------------------------------
    # Public entry point
    # ---------------------------------------------------------------------------

    def deploy(self, context: StrategyContext) -> None:
        """
        Deploy a fresh Iron Fly.

        Idempotent — verifies that no strategy positions exist before acting.
        On partial failure, squares off any filled legs and raises DeploymentError.
        """
        logger.info("=" * 52)
        logger.info("Deployment Started")
        logger.info(f"Spot: {context.spot:.0f}  |  ATM: {context.atm_strike:.0f}")
        logger.info(f"Expiry: {context.expiry}")
        logger.info("=" * 52)

        # Idempotency guard
        if self._state.is_deployed:
            logger.warning("Deployment skipped — strategy already fully deployed.")
            return

        if self._positions_already_exist(context):
            logger.warning("Deployment skipped — existing positions detected in broker.")
            return

        self._state.phase = StrategyPhase.DEPLOYING
        self._state.expiry = context.expiry

        # Calculate geometry
        center = find_atm(context.spot, self._config.get_int("STRIKE_INTERVAL", 50))
        ce_premium = context.get_ce_ltp(center) or 0.0
        pe_premium = context.get_pe_ltp(center) or 0.0

        if ce_premium <= 0 or pe_premium <= 0:
            raise DeploymentError(
                f"Invalid ATM premiums at strike {center}: CE={ce_premium} PE={pe_premium}"
            )

        wing_dist = calculate_fresh_wing_distance(
            ce_premium=ce_premium,
            pe_premium=pe_premium,
            fresh_buffer=self._config.get_float("FRESH_WING_BUFFER", 100),
            rounding=self._config.get_int("FRESH_WING_ROUNDING", 100),
        )

        upper_be, lower_be = calculate_breakevens(center, ce_premium, pe_premium)

        logger.info(
            f"Calculated: center={center:.0f}  CE={ce_premium:.2f}  PE={pe_premium:.2f}  "
            f"wing_dist={wing_dist:.0f}  BE=[{lower_be:.0f}, {upper_be:.0f}]"
        )

        # Build legs
        legs = self._build_legs(center, wing_dist, context)

        # Execute: BUY wings first, then SELL straddle
        try:
            results = self._gateway.execute_legs_buy_first(
                legs=legs,
                strategy_id=self._state.strategy_id,
                context=context,
            )
        except ExecutionError as exc:
            logger.error(f"Deployment failed during execution: {exc}")
            self._square_off_filled(legs, context)
            self._state.phase = StrategyPhase.IDLE
            raise DeploymentError(f"Deployment aborted: {exc}") from exc

        # Commit state
        self._state.legs = legs
        self._state.center_strike = center
        self._state.wing_distance = wing_dist
        self._state.upper_breakeven = upper_be
        self._state.lower_breakeven = lower_be
        self._state.entry_ce_premium = ce_premium
        self._state.entry_pe_premium = pe_premium
        self._state.deployment_time = datetime.now()
        self._state.adjustment_count = 0
        self._state.phase = StrategyPhase.MONITORING
        self._state.mark_updated()

        logger.info("=" * 52)
        logger.info("Deployment Completed")
        logger.info(f"Center: {center:.0f}")
        logger.info(f"Wings:  {lower_wing_strike(center, wing_dist):.0f} PE  /  {upper_wing_strike(center, wing_dist):.0f} CE")
        logger.info(f"Breakevens: {lower_be:.0f} — {upper_be:.0f}")
        logger.info(f"Premium collected: {ce_premium + pe_premium:.2f}")
        logger.info("=" * 52)

    # ---------------------------------------------------------------------------
    # Leg construction
    # ---------------------------------------------------------------------------

    def _build_legs(
        self,
        center: float,
        wing_dist: float,
        context: StrategyContext,
    ) -> list[OptionLeg]:
        """
        Construct the four Iron Fly legs.

        Order within the list matters — BUY legs first so
        execute_legs_buy_first() can split them correctly.
        """
        exchange = self._config.get_str("EXCHANGE_OPTIONS", "NFO")
        product = self._config.get_str("PRODUCT", "NRML")
        quantity = self._config.quantity
        expiry = context.expiry

        upper_wing = upper_wing_strike(center, wing_dist)
        lower_wing = lower_wing_strike(center, wing_dist)

        # Symbols from option chain
        long_call_sym = context.get_ce_symbol(upper_wing)
        long_put_sym = context.get_pe_symbol(lower_wing)
        short_call_sym = context.get_ce_symbol(center)
        short_put_sym = context.get_pe_symbol(center)

        # Validate symbols exist
        for sym, label in [
            (long_call_sym, f"Long Call wing @ {upper_wing}"),
            (long_put_sym, f"Long Put wing @ {lower_wing}"),
            (short_call_sym, f"Short Call @ {center}"),
            (short_put_sym, f"Short Put @ {center}"),
        ]:
            if not sym:
                raise DeploymentError(
                    f"Option symbol not found in chain: {label}. "
                    f"Check expiry={expiry} and strike_count config."
                )

        legs = [
            # BUY legs first (per global execution rule)
            OptionLeg(
                role=LegRole.LONG_PUT,
                symbol=long_put_sym,
                exchange=exchange,
                strike=lower_wing,
                option_type=OptionType.PE,
                action=LegAction.BUY,
                quantity=quantity,
                product=product,
                expiry=expiry,
            ),
            OptionLeg(
                role=LegRole.LONG_CALL,
                symbol=long_call_sym,
                exchange=exchange,
                strike=upper_wing,
                option_type=OptionType.CE,
                action=LegAction.BUY,
                quantity=quantity,
                product=product,
                expiry=expiry,
            ),
            # SELL legs after
            OptionLeg(
                role=LegRole.SHORT_PUT,
                symbol=short_put_sym,
                exchange=exchange,
                strike=center,
                option_type=OptionType.PE,
                action=LegAction.SELL,
                quantity=quantity,
                product=product,
                expiry=expiry,
            ),
            OptionLeg(
                role=LegRole.SHORT_CALL,
                symbol=short_call_sym,
                exchange=exchange,
                strike=center,
                option_type=OptionType.CE,
                action=LegAction.SELL,
                quantity=quantity,
                product=product,
                expiry=expiry,
            ),
        ]

        return legs

    # ---------------------------------------------------------------------------
    # Safety helpers
    # ---------------------------------------------------------------------------

    def _positions_already_exist(self, context: StrategyContext) -> bool:
        """
        Return True if any positions tagged to our strategy_id already exist.
        Uses current position book from context.
        """
        if not context.positions:
            return False
        # Conservative check: any non-zero position for a known strategy symbol
        existing_symbols = {p.get("symbol") for p in context.positions if p.get("quantity", 0) != 0}
        if existing_symbols:
            logger.warning(f"Existing positions found: {existing_symbols}")
            return True
        return False

    def _square_off_filled(self, legs: list[OptionLeg], context: StrategyContext) -> None:
        """
        Square off any legs that were successfully filled during a failed deployment.

        Called only when a partial deployment needs to be unwound.
        """
        filled = [leg for leg in legs if leg.status == LegStatus.OPEN]
        if not filled:
            return

        logger.warning(f"Squaring off {len(filled)} filled leg(s) after deployment failure …")
        for leg in filled:
            try:
                self._gateway.close_leg(
                    leg=leg,
                    strategy_id=self._state.strategy_id,
                    context=context,
                )
                logger.info(f"Squared off: {leg.symbol}")
            except Exception as exc:
                logger.error(f"CRITICAL: Failed to square off {leg.symbol} after deployment failure: {exc}")
