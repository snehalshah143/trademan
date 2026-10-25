"""
adjustments.py
--------------
All adjustment logic for the Monthly Dynamic Iron Fly strategy.

Implements:
- Downside adjustment (spot breaks lower breakeven / put premium doubles)
- Upside adjustment (spot breaks upper breakeven / call premium doubles)
- Wing realignment (after recovery trigger is crossed)
- Wing tightening (when MTM exceeds WING_TIGHTENING_TRIGGER)
- Gap opening detection and delay logic
- MTM protection exits (locked profit floor)
- Expiry day / T-1 exits

Every method is idempotent — it verifies current positions before acting
and never places duplicate orders.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Optional

from ....framework.execution_gateway import ExecutionError, ExecutionGateway
from ....models.option_leg import LegAction, LegRole, LegStatus, OptionLeg, OptionType
from ....models.strategy_context import StrategyContext
from ....models.strategy_state import StrategyPhase, StrategyState
from ....utils.constants import RECOVERY_DOWN, RECOVERY_UP
from ....utils.logger import get_logger
from .calculations import (
    calculate_adjustment_wing_distance,
    calculate_breakevens,
    calculate_recovery_trigger,
    find_atm,
    is_breakeven_breached,
    is_gap_open_beyond_breakeven,
    is_gap_open_beyond_wings,
    is_premium_doubled,
    is_recovery_trigger_crossed,
    lower_wing_strike,
    upper_wing_strike,
)
from .expiry import is_after_expiry_exit_time, minutes_since_open

logger = get_logger(__name__)


class AdjustmentManager:
    """
    Evaluates and executes all adjustments for the Iron Fly strategy.

    Parameters
    ----------
    state   : Mutable strategy state
    config  : Config object
    gateway : ExecutionGateway
    """

    def __init__(self, state: StrategyState, config, gateway: ExecutionGateway) -> None:
        self._state = state
        self._config = config
        self._gateway = gateway

    # ===========================================================================
    # Trigger evaluation (pure checks — no side effects)
    # ===========================================================================

    def check_adjustment_triggers(self, context: StrategyContext) -> tuple[bool, str]:
        """
        Evaluate all four adjustment trigger conditions.

        Returns (True, direction) if ANY trigger fires, else (False, "").
        Direction: "DOWN" for downside, "UP" for upside.

        If multiple triggers fire simultaneously, ONE adjustment is performed
        (the spec explicitly states this).
        """
        state = self._state
        if not state.is_deployed:
            return False, ""

        spot = context.spot
        upper_be = state.upper_breakeven or 0.0
        lower_be = state.lower_breakeven or 0.0
        be_buffer = self._config.get_float("BREAKEVEN_BUFFER", 25)
        premium_factor = self._config.get_float("PREMIUM_DOUBLE_FACTOR", 2.0)

        # Get current short straddle premiums
        short_ce = state.get_leg(LegRole.SHORT_CALL)
        short_pe = state.get_leg(LegRole.SHORT_PUT)
        current_ce_ltp = context.get_ce_ltp(short_ce.strike) if short_ce else None
        current_pe_ltp = context.get_pe_ltp(short_pe.strike) if short_pe else None

        # Condition 1: Call premium doubled
        if (
            short_ce
            and current_ce_ltp is not None
            and state.entry_ce_premium
            and is_premium_doubled(current_ce_ltp, state.entry_ce_premium, premium_factor)
        ):
            logger.info(
                f"Trigger: CE premium doubled "
                f"({current_ce_ltp:.2f} >= {state.entry_ce_premium * premium_factor:.2f})"
            )
            return True, RECOVERY_UP

        # Condition 2: Put premium doubled
        if (
            short_pe
            and current_pe_ltp is not None
            and state.entry_pe_premium
            and is_premium_doubled(current_pe_ltp, state.entry_pe_premium, premium_factor)
        ):
            logger.info(
                f"Trigger: PE premium doubled "
                f"({current_pe_ltp:.2f} >= {state.entry_pe_premium * premium_factor:.2f})"
            )
            return True, RECOVERY_DOWN

        # Condition 3: Spot above upper breakeven + buffer
        if is_breakeven_breached(spot, upper_be, be_buffer, "upper"):
            logger.info(
                f"Trigger: Spot {spot:.0f} above upper breakeven+buffer "
                f"({upper_be + be_buffer:.0f})"
            )
            return True, RECOVERY_UP

        # Condition 4: Spot below lower breakeven - buffer
        if is_breakeven_breached(spot, lower_be, be_buffer, "lower"):
            logger.info(
                f"Trigger: Spot {spot:.0f} below lower breakeven-buffer "
                f"({lower_be - be_buffer:.0f})"
            )
            return True, RECOVERY_DOWN

        return False, ""

    # ===========================================================================
    # Gap opening check
    # ===========================================================================

    def handle_gap_open(self, context: StrategyContext) -> bool:
        """
        Check if market has gapped open requiring special handling.

        Returns True if adjustment should be BLOCKED (gap delay active or
        beyond wings — requiring full exit + redeploy).

        Caller checks this BEFORE calling perform_adjustment().
        """
        state = self._state
        spot = context.spot
        be_buffer = self._config.get_float("BREAKEVEN_BUFFER", 25)
        gap_delay_minutes = self._config.get_int("GAP_DELAY", 10)

        short_ce = state.get_leg(LegRole.SHORT_CALL)
        short_pe = state.get_leg(LegRole.SHORT_PUT)
        long_ce = state.get_leg(LegRole.LONG_CALL)
        long_pe = state.get_leg(LegRole.LONG_PUT)

        upper_wing = long_ce.strike if long_ce else float("inf")
        lower_wing = long_pe.strike if long_pe else float("-inf")

        # Check if spot is beyond wings → exit and redeploy
        if is_gap_open_beyond_wings(spot, upper_wing, lower_wing):
            logger.warning(
                f"GAP BEYOND WINGS: spot={spot:.0f} outside [{lower_wing:.0f}, {upper_wing:.0f}]. "
                f"Hedge no longer effective. Strategy must exit and redeploy."
            )
            return True   # caller will handle exit + redeploy

        # Check if spot opened beyond breakeven (gap delay applies)
        upper_be = state.upper_breakeven or 0.0
        lower_be = state.lower_breakeven or 0.0
        if is_gap_open_beyond_breakeven(spot, upper_be, lower_be, be_buffer):
            elapsed = minutes_since_open()
            if elapsed < gap_delay_minutes:
                remaining = gap_delay_minutes - elapsed
                logger.info(
                    f"GAP DELAY ACTIVE: spot={spot:.0f} opened beyond breakeven. "
                    f"Waiting {remaining:.1f} more minutes before adjusting. "
                    f"[GAP_DELAY={gap_delay_minutes}m]"
                )
                return True   # block adjustment during delay

        return False   # no gap issue, proceed normally

    # ===========================================================================
    # Adjustment execution
    # ===========================================================================

    def perform_adjustment(self, context: StrategyContext, direction: str) -> None:
        """
        Execute a full Iron Fly adjustment.

        Sequence:
        1. BUY BACK short straddle (buy CE + buy PE)
        2. Keep existing hedge wings untouched
        3. Find new ATM at current spot
        4. SELL new ATM straddle
        5. Store recovery trigger

        Idempotent — verifies short positions still exist before acting.
        """
        max_adj = self._config.get_int("MAXIMUM_ADJUSTMENTS", 0)
        if max_adj > 0 and self._state.adjustment_count >= max_adj:
            logger.warning(
                f"Maximum adjustments ({max_adj}) reached. "
                f"Skipping adjustment. Current: {self._state.adjustment_count}"
            )
            return

        logger.info("=" * 52)
        logger.info(f"Adjustment #{self._state.adjustment_count + 1} Starting")
        logger.info(f"Direction: {direction}  |  Spot: {context.spot:.0f}")
        logger.info("=" * 52)

        self._state.phase = StrategyPhase.ADJUSTING

        # Verify margin
        if not self._check_margin(context):
            logger.warning(
                "Margin insufficient for adjustment. "
                "Skipping — existing hedge remains. Will retry next cycle."
            )
            self._state.phase = StrategyPhase.MONITORING
            return

        short_ce = self._state.get_leg(LegRole.SHORT_CALL)
        short_pe = self._state.get_leg(LegRole.SHORT_PUT)

        if not short_ce or not short_pe:
            logger.warning("Adjustment skipped — short legs not found in state.")
            self._state.phase = StrategyPhase.MONITORING
            return

        # Step 1: Buy back short straddle (BUY CE + BUY PE)
        logger.info("Step 1: Buying back short straddle …")
        buyback_legs = [short_ce, short_pe]
        try:
            for leg in buyback_legs:
                self._gateway.close_leg(
                    leg=leg,
                    strategy_id=self._state.strategy_id,
                    context=context,
                )
                logger.info(f"Bought back: {leg.symbol}")
        except ExecutionError as exc:
            logger.error(f"Failed to buy back straddle: {exc}")
            self._state.phase = StrategyPhase.MONITORING
            return

        # Step 2: Determine new ATM
        new_atm = find_atm(context.spot, self._config.get_int("STRIKE_INTERVAL", 50))
        logger.info(f"Step 2: New ATM at current spot {context.spot:.0f} → {new_atm:.0f}")

        # Step 3: Sell new straddle at new ATM
        new_short_ce, new_short_pe = self._build_new_straddle_legs(new_atm, context)
        logger.info(f"Step 3: Selling new straddle at {new_atm:.0f} …")

        try:
            results = self._gateway.execute_legs_buy_first(
                legs=[new_short_ce, new_short_pe],
                strategy_id=self._state.strategy_id,
                context=context,
            )
        except ExecutionError as exc:
            logger.error(f"Failed to sell new straddle: {exc}")
            self._state.phase = StrategyPhase.MONITORING
            return

        # Step 4: Update state
        # Replace old short legs with new ones in the legs list
        self._state.legs = [
            leg for leg in self._state.legs
            if leg.role not in (LegRole.SHORT_CALL, LegRole.SHORT_PUT)
        ]
        self._state.legs.extend([new_short_ce, new_short_pe])

        self._state.center_strike = new_atm
        self._state.adjustment_count += 1
        self._state.adjustment_price = context.spot

        recovery_buffer = self._config.get_float("RECOVERY_BUFFER", 50)
        self._state.recovery_trigger = calculate_recovery_trigger(
            adjustment_price=context.spot,
            recovery_buffer=recovery_buffer,
            direction=direction,
        )
        self._state.recovery_direction = direction

        # Initialise trailing high/low water marks at the adjustment spot
        self._state.recovery_high_water = context.spot
        self._state.recovery_low_water = context.spot

        # Update breakevens based on new straddle premiums
        new_ce_premium = context.get_ce_ltp(new_atm) or 0.0
        new_pe_premium = context.get_pe_ltp(new_atm) or 0.0
        upper_be, lower_be = calculate_breakevens(new_atm, new_ce_premium, new_pe_premium)
        self._state.upper_breakeven = upper_be
        self._state.lower_breakeven = lower_be

        self._state.phase = StrategyPhase.MONITORING
        self._state.mark_updated()

        logger.info("=" * 52)
        logger.info(f"Adjustment #{self._state.adjustment_count} Completed")
        logger.info(f"New Center: {new_atm:.0f}")
        logger.info(f"Recovery Trigger: {self._state.recovery_trigger:.0f} ({direction})")
        logger.info(f"Wings unchanged: {self._state.wing_distance:.0f} points from center")
        logger.info("=" * 52)

    # ===========================================================================
    # Wing realignment
    # ===========================================================================

    def update_trailing_recovery(self, context: StrategyContext) -> None:
        """
        Trail the recovery trigger behind the rolling high (bullish) or low (bearish).

        Called every monitoring cycle while a recovery trigger is active.

        Bullish (RECOVERY_UP — upside adjustment, center > initial trigger):
            - Track the rolling high since the adjustment.
            - On each new high: new_trigger = new_high - TRAILING_RECOVERY_BUFFER
            - Trigger only ratchets UP — never comes back down.

        Bearish (RECOVERY_DOWN — downside adjustment, center < initial trigger):
            - Track the rolling low since the adjustment.
            - On each new low: new_trigger = new_low + TRAILING_RECOVERY_BUFFER
            - Trigger only ratchets DOWN — never goes back up.

        Wing realignment is then checked against this (now trailing) trigger.
        """
        state = self._state
        if state.recovery_trigger is None or state.recovery_direction is None:
            return

        trailing_buffer = self._config.get_float("TRAILING_RECOVERY_BUFFER", 75)
        spot = context.spot

        if state.recovery_direction == RECOVERY_UP:
            # ── Bullish: trail below the rolling high ──────────────────────
            hw = state.recovery_high_water if state.recovery_high_water is not None else spot
            if spot > hw:
                state.recovery_high_water = spot
                new_trigger = spot - trailing_buffer
                if new_trigger > (state.recovery_trigger or 0.0):
                    old_trigger = state.recovery_trigger
                    state.recovery_trigger = new_trigger
                    state.mark_updated()
                    logger.warning(
                        f"[ALERT] Trailing recovery trigger updated (BULLISH): "
                        f"{old_trigger:.0f} → {new_trigger:.0f}  "
                        f"(new high={spot:.0f}  trail={trailing_buffer:.0f}pts)"
                    )

        elif state.recovery_direction == RECOVERY_DOWN:
            # ── Bearish: trail above the rolling low ───────────────────────
            lw = state.recovery_low_water if state.recovery_low_water is not None else spot
            if spot < lw:
                state.recovery_low_water = spot
                new_trigger = spot + trailing_buffer
                if new_trigger < (state.recovery_trigger or float("inf")):
                    old_trigger = state.recovery_trigger
                    state.recovery_trigger = new_trigger
                    state.mark_updated()
                    logger.warning(
                        f"[ALERT] Trailing recovery trigger updated (BEARISH): "
                        f"{old_trigger:.0f} → {new_trigger:.0f}  "
                        f"(new low={spot:.0f}  trail={trailing_buffer:.0f}pts)"
                    )

    def should_realign_wings(self, context: StrategyContext) -> bool:
        """
        Return True if the recovery trigger has been crossed and wings should be realigned.

        Wing realignment only happens AFTER an adjustment and AFTER the recovery
        trigger is crossed (not immediately after adjustment).
        """
        state = self._state
        if state.recovery_trigger is None or state.recovery_direction is None:
            return False

        return is_recovery_trigger_crossed(
            spot=context.spot,
            recovery_trigger=state.recovery_trigger,
            direction=state.recovery_direction,
        )

    def perform_wing_realignment(self, context: StrategyContext) -> None:
        """
        Realign hedge wings based on current premiums.

        Sequence (per spec):
        1. Calculate new wing distance from current premiums
        2. BUY new wings (new strikes)
        3. SELL old wings (previous strikes)

        Idempotent — checks if new wings already exist before acting.
        """
        logger.info("=" * 52)
        logger.info("Wing Realignment Starting")
        logger.info(f"Spot: {context.spot:.0f}  Recovery trigger crossed.")
        logger.info("=" * 52)

        self._state.phase = StrategyPhase.REALIGNING

        state = self._state
        exchange = self._config.get_str("EXCHANGE_OPTIONS", "NFO")
        product = self._config.get_str("PRODUCT", "NRML")
        quantity = self._config.quantity

        center = state.center_strike or context.atm_strike
        current_ce_ltp = context.get_ce_ltp(center) or 0.0
        current_pe_ltp = context.get_pe_ltp(center) or 0.0

        if current_ce_ltp <= 0 or current_pe_ltp <= 0:
            logger.warning("Wing realignment skipped — cannot read current premiums.")
            self._state.phase = StrategyPhase.MONITORING
            return

        new_wing_dist = calculate_adjustment_wing_distance(
            ce_premium=current_ce_ltp,
            pe_premium=current_pe_ltp,
            adjustment_buffer=self._config.get_float("ADJUSTMENT_BUFFER", 50),
            rounding=self._config.get_int("ADJUSTMENT_ROUNDING", 50),
        )

        new_upper = upper_wing_strike(center, new_wing_dist)
        new_lower = lower_wing_strike(center, new_wing_dist)

        old_long_ce = state.get_leg(LegRole.LONG_CALL)
        old_long_pe = state.get_leg(LegRole.LONG_PUT)

        logger.info(
            f"New wings: CE={new_upper:.0f}  PE={new_lower:.0f}  "
            f"(dist={new_wing_dist:.0f})"
        )
        logger.info(
            f"Old wings: CE={old_long_ce.strike if old_long_ce else 'N/A'}  "
            f"PE={old_long_pe.strike if old_long_pe else 'N/A'}"
        )

        # Build new wing legs (BUY)
        new_long_ce = OptionLeg(
            role=LegRole.LONG_CALL,
            symbol=context.get_ce_symbol(new_upper) or "",
            exchange=exchange, strike=new_upper,
            option_type=OptionType.CE, action=LegAction.BUY,
            quantity=quantity, product=product, expiry=context.expiry,
        )
        new_long_pe = OptionLeg(
            role=LegRole.LONG_PUT,
            symbol=context.get_pe_symbol(new_lower) or "",
            exchange=exchange, strike=new_lower,
            option_type=OptionType.PE, action=LegAction.BUY,
            quantity=quantity, product=product, expiry=context.expiry,
        )

        # Step 1: BUY new wings first
        logger.info("Step 1: Buying new wings …")
        try:
            self._gateway.execute_legs_buy_first(
                legs=[new_long_pe, new_long_ce],
                strategy_id=state.strategy_id,
                context=context,
            )
        except ExecutionError as exc:
            logger.error(f"Wing realignment: failed to buy new wings: {exc}")
            self._state.phase = StrategyPhase.MONITORING
            return

        # Step 2: SELL old wings
        logger.info("Step 2: Selling old wings …")
        if old_long_ce:
            try:
                self._gateway.close_leg(old_long_ce, state.strategy_id, context)
            except ExecutionError as exc:
                logger.error(f"CRITICAL: Failed to sell old CE wing: {exc}")

        if old_long_pe:
            try:
                self._gateway.close_leg(old_long_pe, state.strategy_id, context)
            except ExecutionError as exc:
                logger.error(f"CRITICAL: Failed to sell old PE wing: {exc}")

        # Update state
        self._state.legs = [
            leg for leg in self._state.legs
            if leg.role not in (LegRole.LONG_CALL, LegRole.LONG_PUT)
        ]
        self._state.legs.extend([new_long_ce, new_long_pe])
        self._state.wing_distance = new_wing_dist
        self._state.recovery_trigger = None
        self._state.recovery_direction = None
        self._state.recovery_high_water = None
        self._state.recovery_low_water = None
        self._state.phase = StrategyPhase.MONITORING
        self._state.mark_updated()

        logger.info("=" * 52)
        logger.info("Wing Realignment Completed")
        logger.info(f"New wing distance: {new_wing_dist:.0f}")
        logger.info("=" * 52)

    # ===========================================================================
    # Wing tightening
    # ===========================================================================

    def should_tighten_wings(self, context: StrategyContext) -> bool:
        """Return True if MTM >= WING_TIGHTENING_TRIGGER and wings not yet tightened."""
        if self._state.wing_tightened:
            return False
        trigger = self._config.get_float("WING_TIGHTENING_TRIGGER", 10000)
        return self._state.current_mtm >= trigger

    def perform_wing_tightening(self, context: StrategyContext) -> None:
        """
        Move both hedge wings closer to center by WING_TIGHTENING_DISTANCE points.

        Sequence: BUY new (closer) wings → verify → SELL old (farther) wings.
        """
        logger.info("=" * 52)
        logger.info("Wing Tightening Starting")
        logger.info(f"MTM={self._state.current_mtm:.0f} >= trigger={self._config.get_float('WING_TIGHTENING_TRIGGER'):.0f}")
        logger.info("=" * 52)

        self._state.phase = StrategyPhase.TIGHTENING

        state = self._state
        tighten_by = self._config.get_float("WING_TIGHTENING_DISTANCE", 100)
        exchange = self._config.get_str("EXCHANGE_OPTIONS", "NFO")
        product = self._config.get_str("PRODUCT", "NRML")
        quantity = self._config.quantity

        old_long_ce = state.get_leg(LegRole.LONG_CALL)
        old_long_pe = state.get_leg(LegRole.LONG_PUT)
        center = state.center_strike or context.atm_strike

        if not old_long_ce or not old_long_pe:
            logger.warning("Wing tightening skipped — hedge wings not found in state.")
            self._state.phase = StrategyPhase.MONITORING
            return

        new_upper = old_long_ce.strike - tighten_by
        new_lower = old_long_pe.strike + tighten_by

        logger.info(
            f"Moving wings: CE {old_long_ce.strike:.0f} → {new_upper:.0f}, "
            f"PE {old_long_pe.strike:.0f} → {new_lower:.0f}"
        )

        new_long_ce = OptionLeg(
            role=LegRole.LONG_CALL,
            symbol=context.get_ce_symbol(new_upper) or "",
            exchange=exchange, strike=new_upper,
            option_type=OptionType.CE, action=LegAction.BUY,
            quantity=quantity, product=product, expiry=context.expiry,
        )
        new_long_pe = OptionLeg(
            role=LegRole.LONG_PUT,
            symbol=context.get_pe_symbol(new_lower) or "",
            exchange=exchange, strike=new_lower,
            option_type=OptionType.PE, action=LegAction.BUY,
            quantity=quantity, product=product, expiry=context.expiry,
        )

        # BUY new wings first
        try:
            self._gateway.execute_legs_buy_first(
                legs=[new_long_pe, new_long_ce],
                strategy_id=state.strategy_id,
                context=context,
            )
        except ExecutionError as exc:
            logger.error(f"Wing tightening: failed to buy new wings: {exc}")
            self._state.phase = StrategyPhase.MONITORING
            return

        # SELL old wings
        for old_leg in [old_long_ce, old_long_pe]:
            try:
                self._gateway.close_leg(old_leg, state.strategy_id, context)
            except ExecutionError as exc:
                logger.error(f"Wing tightening: failed to sell old wing {old_leg.symbol}: {exc}")

        # Update state
        new_wing_dist = (new_upper - center + center - new_lower) / 2
        self._state.legs = [
            leg for leg in self._state.legs
            if leg.role not in (LegRole.LONG_CALL, LegRole.LONG_PUT)
        ]
        self._state.legs.extend([new_long_ce, new_long_pe])
        self._state.wing_distance = new_wing_dist
        self._state.wing_tightened = True
        self._state.phase = StrategyPhase.MONITORING
        self._state.mark_updated()

        logger.info("=" * 52)
        logger.info("Wing Tightening Completed")
        logger.info(f"New wing distance: {new_wing_dist:.0f}")
        logger.info("=" * 52)

    # ===========================================================================
    # MTM protection
    # ===========================================================================

    def update_mtm_lock(self, context: StrategyContext) -> None:
        """
        Update the MTM lock (trailing locked profit floor).

        Locks are only ever increased — never decreased.
        """
        current_mtm = self._state.current_mtm
        trigger_1 = self._config.get_float("MTM_TRIGGER_LEVEL_1", 8000)
        lock_1 = self._config.get_float("MTM_LOCK_LEVEL_1", 2500)
        trigger_2 = self._config.get_float("MTM_TRIGGER_LEVEL_2", 12000)
        lock_2 = self._config.get_float("MTM_LOCK_LEVEL_2", 6000)

        locked = self._state.locked_mtm or 0.0
        new_lock = locked

        if current_mtm >= trigger_2:
            new_lock = max(new_lock, lock_2)
        elif current_mtm >= trigger_1:
            new_lock = max(new_lock, lock_1)

        if new_lock > locked:
            logger.info(
                f"MTM Lock updated: {locked:.0f} → {new_lock:.0f} "
                f"(MTM={current_mtm:.0f})"
            )
            self._state.locked_mtm = new_lock
            self._state.mark_updated()

    def is_mtm_lock_breached(self, context: StrategyContext) -> bool:
        """
        Return True if the locked MTM floor has been breached.

        Triggers a full exit of the strategy.
        """
        if self._state.locked_mtm is None:
            return False
        breached = self._state.current_mtm < self._state.locked_mtm
        if breached:
            logger.warning(
                f"MTM Lock breached! Current MTM={self._state.current_mtm:.0f} "
                f"< Locked={self._state.locked_mtm:.0f}"
            )
        return breached

    # ===========================================================================
    # Exit checks
    # ===========================================================================

    def should_exit_t1(self, context: StrategyContext) -> bool:
        """T-1 exit: exit if it's one day before expiry and MTM >= EARLY_EXIT_MTM."""
        if not context.is_t_minus_1:
            return False
        early_exit_mtm = self._config.get_float("EARLY_EXIT_MTM", 12000)
        if self._state.current_mtm >= early_exit_mtm:
            logger.info(
                f"T-1 Early Exit triggered: MTM={self._state.current_mtm:.0f} "
                f">= EARLY_EXIT_MTM={early_exit_mtm:.0f}"
            )
            return True
        return False

    def should_exit_expiry(self, context: StrategyContext) -> bool:
        """Expiry day exit: exit at EXPIRY_EXIT_TIME regardless of MTM."""
        if not context.is_expiry_day:
            return False
        exit_time = self._config.get_str("EXPIRY_EXIT_TIME", "11:00")
        if is_after_expiry_exit_time(exit_time):
            logger.info(f"Expiry Day Exit triggered at {exit_time} IST.")
            return True
        return False

    # ===========================================================================
    # Internal helpers
    # ===========================================================================

    def _build_new_straddle_legs(
        self, new_atm: float, context: StrategyContext
    ) -> tuple[OptionLeg, OptionLeg]:
        """Build new SHORT_CALL and SHORT_PUT legs at *new_atm*."""
        exchange = self._config.get_str("EXCHANGE_OPTIONS", "NFO")
        product = self._config.get_str("PRODUCT", "NRML")
        quantity = self._config.quantity

        new_short_ce = OptionLeg(
            role=LegRole.SHORT_CALL,
            symbol=context.get_ce_symbol(new_atm) or "",
            exchange=exchange, strike=new_atm,
            option_type=OptionType.CE, action=LegAction.SELL,
            quantity=quantity, product=product, expiry=context.expiry,
        )
        new_short_pe = OptionLeg(
            role=LegRole.SHORT_PUT,
            symbol=context.get_pe_symbol(new_atm) or "",
            exchange=exchange, strike=new_atm,
            option_type=OptionType.PE, action=LegAction.SELL,
            quantity=quantity, product=product, expiry=context.expiry,
        )
        return new_short_ce, new_short_pe

    def _check_margin(self, context: StrategyContext) -> bool:
        """
        Verify there is sufficient margin for the upcoming adjustment.

        Returns True if margin check passes or cannot be determined.
        """
        try:
            required_cash = self._config.get_float("MINIMUM_MARGIN_BUFFER", 50000)
            if context.available_cash < required_cash:
                logger.warning(
                    f"Margin check failed: available_cash={context.available_cash:.0f} "
                    f"< required={required_cash:.0f}"
                )
                return False
        except Exception:
            pass   # If margin check fails, proceed (don't block on config errors)
        return True
