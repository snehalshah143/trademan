"""
iron_fly_strategy.py
--------------------
Monthly Dynamic Iron Fly — main strategy class.

This class orchestrates all Iron Fly business logic by composing the
Deployer, AdjustmentManager, and ExpiryManager.  It implements the
``BaseStrategy`` contract required by the framework's MonitoringEngine.

Evaluation flow each cycle:
1. Check for gap-open scenarios (beyond wings → exit + redeploy)
2. Check exit conditions (MTM lock, T-1, expiry day)
3. Update MTM lock levels
4. Check wing tightening
5. Check wing realignment (if recovery trigger crossed)
6. Check adjustment triggers
7. Deploy if first trading day and not yet deployed

All decisions are made from the ``StrategyContext`` — no direct OpenAlgo calls.
"""

from __future__ import annotations

import logging
from datetime import datetime

from ....base.base_strategy import BaseStrategy
from ....framework.execution_gateway import ExecutionGateway
from ....models.option_leg import LegRole, LegStatus
from ....models.strategy_context import StrategyContext
from ....models.strategy_state import StrategyPhase, StrategyState
from ....utils.constants import HEARTBEAT_SEPARATOR
from ....utils.helper import format_inr, now_ist
from ....utils.logger import get_logger
from .adjustments import AdjustmentManager
from .deployment import DeploymentError, IronFlyDeployer
from .expiry import (
    is_after_deployment_time,
    is_first_trading_day_of_month,
    should_wait_for_next_month,
)

logger = get_logger(__name__)


class IronFlyStrategy(BaseStrategy):
    """
    Monthly Dynamic Iron Fly Strategy.

    Implements ``BaseStrategy`` and is driven by ``MonitoringEngine``.
    All business logic is encapsulated here; the framework only calls
    ``evaluate(context)`` each cycle.
    """

    def __init__(self, state: StrategyState, config, gateway: ExecutionGateway) -> None:
        super().__init__(state, config, gateway)

        self._deployer = IronFlyDeployer(state, config, gateway)
        self._adj_manager = AdjustmentManager(state, config, gateway)

        logger.info(
            f"IronFlyStrategy initialised — id={state.strategy_id} "
            f"mode={config.get('EXECUTION_MODE', 'PAPER')}"
        )

    # ---------------------------------------------------------------------------
    # BaseStrategy interface
    # ---------------------------------------------------------------------------

    def evaluate(self, context: StrategyContext) -> None:
        """
        Main evaluation logic — called once per monitoring cycle.

        All decisions are derived from *context*.  No I/O inside this method.
        """
        state = self.state

        # ---------------------------------------------------------------------------
        # Case 1: Strategy not deployed yet
        # ---------------------------------------------------------------------------
        if not state.is_deployed and state.phase == StrategyPhase.IDLE:
            if self.is_deployment_due(context):
                logger.info("Deployment conditions met — deploying Iron Fly …")
                self.deploy(context)
            else:
                self._log_waiting(context)
            return

        # ---------------------------------------------------------------------------
        # Case 2: Strategy exited — waiting for next month
        # ---------------------------------------------------------------------------
        if state.phase == StrategyPhase.EXITED:
            if is_first_trading_day_of_month() and is_after_deployment_time(
                self.config.get("DEPLOYMENT_TIME", "09:30")
            ):
                logger.info("New month — resetting strategy for fresh deployment.")
                self._reset_for_new_month()
            else:
                logger.info("Strategy exited. Waiting for first trading day of next month.")
            return

        # ---------------------------------------------------------------------------
        # Case 3: Strategy is deploying or in another transition — wait
        # ---------------------------------------------------------------------------
        if state.phase in (StrategyPhase.DEPLOYING, StrategyPhase.ADJUSTING,
                           StrategyPhase.REALIGNING, StrategyPhase.TIGHTENING,
                           StrategyPhase.EXITING):
            logger.info(f"Strategy in transition phase: {state.phase.value}. Waiting …")
            return

        # ---------------------------------------------------------------------------
        # Case 4: Strategy is actively monitoring
        # ---------------------------------------------------------------------------
        if not state.is_deployed:
            logger.warning("Strategy phase=MONITORING but not fully deployed. Skipping cycle.")
            return

        # Update LTPs for all open legs from the option chain
        self._update_leg_ltps(context)
        state.update_mtm_highwater()

        # --- 4a: Gap-open check (runs first, before any other logic) ---
        gap_handled = self._handle_gap_open_scenario(context)
        if gap_handled:
            return   # Gap scenario was handled — restart cycle

        # --- 4b: Exit checks ---
        should_exit, reason = self.should_exit(context)
        if should_exit:
            self.exit_all(context, reason)
            return

        # --- 4c: Update MTM lock levels ---
        self._adj_manager.update_mtm_lock(context)

        # --- 4d: Wing tightening ---
        if self._adj_manager.should_tighten_wings(context):
            self._adj_manager.perform_wing_tightening(context)
            return

        # --- 4e: Update trailing recovery trigger (runs every cycle in recovery) ---
        self._adj_manager.update_trailing_recovery(context)

        # --- 4f: Wing realignment (after recovery trigger crossed) ---
        if self._adj_manager.should_realign_wings(context):
            self._adj_manager.perform_wing_realignment(context)
            return

        # --- 4g: Adjustment trigger check ---
        triggered, direction = self._adj_manager.check_adjustment_triggers(context)
        if triggered:
            # Check gap delay before adjusting
            gap_blocks = self._adj_manager.handle_gap_open(context)
            if gap_blocks:
                return   # Gap delay active — skip this cycle

            self._adj_manager.perform_adjustment(context, direction)
            return

        logger.debug("No action required this cycle.")

    def is_deployment_due(self, context: StrategyContext) -> bool:
        """
        Return True if all deployment conditions are met:
        - First trading day of the month
        - After DEPLOYMENT_TIME
        - No existing positions
        - Strategy in IDLE state
        """
        if self.state.phase != StrategyPhase.IDLE:
            return False
        if not is_first_trading_day_of_month():
            return False
        deployment_time = self.config.get("DEPLOYMENT_TIME", "09:30")
        if not is_after_deployment_time(deployment_time):
            return False
        if context.positions:
            logger.warning("Deployment skipped — existing positions detected.")
            return False
        return True

    def deploy(self, context: StrategyContext) -> None:
        """Deploy a fresh Iron Fly. Delegates to IronFlyDeployer."""
        try:
            self._deployer.deploy(context)
        except DeploymentError as exc:
            logger.error(f"Deployment failed: {exc}")
            self.state.pause(f"Deployment failed: {exc}")

    def should_exit(self, context: StrategyContext) -> tuple[bool, str]:
        """Check all exit conditions in priority order."""
        adj = self._adj_manager

        # MTM lock breach
        if adj.is_mtm_lock_breached(context):
            return True, f"MTM lock breached (locked={self.state.locked_mtm:.0f}, current={self.state.current_mtm:.0f})"

        # T-1 early exit
        if adj.should_exit_t1(context):
            return True, f"T-1 early exit (MTM={self.state.current_mtm:.0f})"

        # Expiry day exit
        if adj.should_exit_expiry(context):
            return True, "Expiry day exit"

        return False, ""

    def exit_all(self, context: StrategyContext, reason: str) -> None:
        """
        Exit all open legs.

        Sequence (per global BUY-first rule for exit):
        SELL legs (short positions): buy them back first
        BUY legs (long positions): sell them out second
        """
        logger.info("=" * 52)
        logger.info(f"EXIT ALL — Reason: {reason}")
        logger.info("=" * 52)

        self.state.phase = StrategyPhase.EXITING
        open_legs = self.state.open_legs

        if not open_legs:
            logger.info("Exit: no open legs to close.")
            self.state.phase = StrategyPhase.EXITED
            return

        # Short legs (SELL positions) → buy back first
        short_legs = [leg for leg in open_legs if leg.is_short]
        long_legs = [leg for leg in open_legs if leg.is_long]

        for leg in short_legs:
            try:
                self.gateway.close_leg(leg, self.strategy_id, context)
                logger.info(f"Closed short leg: {leg.symbol}")
            except Exception as exc:
                logger.error(f"Failed to close short leg {leg.symbol}: {exc}")

        for leg in long_legs:
            try:
                self.gateway.close_leg(leg, self.strategy_id, context)
                logger.info(f"Closed long leg: {leg.symbol}")
            except Exception as exc:
                logger.error(f"Failed to close long leg {leg.symbol}: {exc}")

        self.state.phase = StrategyPhase.EXITED
        self.state.mark_updated()

        logger.info("=" * 52)
        logger.info(f"EXIT COMPLETE — Final MTM: {format_inr(self.state.current_mtm)}")
        logger.info("=" * 52)

    # ---------------------------------------------------------------------------
    # Hooks
    # ---------------------------------------------------------------------------

    def on_start(self) -> None:
        logger.info(f"IronFlyStrategy started — id={self.strategy_id}")
        logger.info(f"Mode: {self.config.get('EXECUTION_MODE', 'PAPER')}")
        logger.info(f"State: {self.state.phase.value}")

    def on_cycle_end(self, context: StrategyContext) -> None:
        """Print paper dashboard if in PAPER mode."""
        if self.config.get("EXECUTION_MODE", "PAPER") == "PAPER":
            if hasattr(self.gateway, "_paper"):
                self.gateway._paper.print_dashboard(self.state)

    def on_stop(self) -> None:
        logger.info(f"IronFlyStrategy stopped. Final state: {self.state.phase.value}")

    # ---------------------------------------------------------------------------
    # Internal helpers
    # ---------------------------------------------------------------------------

    def _update_leg_ltps(self, context: StrategyContext) -> None:
        """Update current_ltp on all open legs from the option chain."""
        for leg in self.state.open_legs:
            entry = context.get_chain_entry(leg.strike)
            if entry:
                ltp = entry.ce_ltp if leg.option_type.value == "CE" else entry.pe_ltp
                if ltp is not None:
                    leg.current_ltp = ltp

    def _handle_gap_open_scenario(self, context: StrategyContext) -> bool:
        """
        Handle gap-open beyond wings scenario.

        If spot has gapped beyond the hedge wings:
        - Exit the entire strategy
        - Redeploy a fresh Iron Fly at the current ATM
        Returns True if a gap scenario was handled.
        """
        state = self.state
        long_ce = state.get_leg(LegRole.LONG_CALL)
        long_pe = state.get_leg(LegRole.LONG_PUT)

        if not long_ce or not long_pe:
            return False

        from .calculations import is_gap_open_beyond_wings
        if is_gap_open_beyond_wings(context.spot, long_ce.strike, long_pe.strike):
            logger.critical(
                f"CRITICAL: Spot {context.spot:.0f} has gapped beyond wings "
                f"[{long_pe.strike:.0f}, {long_ce.strike:.0f}]. "
                f"Exiting and redeploying …"
            )
            self.exit_all(context, "Gap beyond wings — hedge ineffective")
            # Redeploy immediately after exit
            self.state.phase = StrategyPhase.IDLE
            self.deploy(context)
            return True

        return False

    def _reset_for_new_month(self) -> None:
        """Reset state for a new monthly deployment cycle."""
        logger.info("Resetting strategy for new monthly cycle …")
        self.state.legs = []
        self.state.center_strike = None
        self.state.wing_distance = None
        self.state.upper_breakeven = None
        self.state.lower_breakeven = None
        self.state.entry_ce_premium = None
        self.state.entry_pe_premium = None
        self.state.deployment_time = None
        self.state.adjustment_count = 0
        self.state.recovery_trigger = None
        self.state.recovery_direction = None
        self.state.locked_mtm = None
        self.state.wing_tightened = False
        self.state.max_mtm = 0.0
        self.state.min_mtm = 0.0
        self.state.phase = StrategyPhase.IDLE
        self.state.paused_reason = None
        self.state.mark_updated()

    def _log_waiting(self, context: StrategyContext) -> None:
        """Log the waiting state when deployment conditions are not yet met."""
        from .expiry import is_after_deployment_time
        dep_time = self.config.get("DEPLOYMENT_TIME", "09:30")
        after_time = is_after_deployment_time(dep_time)
        first_day = is_first_trading_day_of_month()

        logger.info(
            f"Waiting to deploy: "
            f"first_trading_day={first_day} "
            f"after_{dep_time}={after_time} "
            f"spot={context.spot:.0f}"
        )

    def validate_state(self, context: StrategyContext) -> list[str]:
        """
        Validate the current strategy state after every execution.

        Returns a list of validation errors (empty = all good).
        """
        errors = []
        state = self.state

        if not state.is_deployed:
            return errors   # Nothing to validate if not deployed

        open_legs = state.open_legs
        if len(open_legs) != 4:
            errors.append(f"Expected 4 open legs, found {len(open_legs)}")

        for role in (LegRole.SHORT_CALL, LegRole.SHORT_PUT, LegRole.LONG_CALL, LegRole.LONG_PUT):
            if not state.get_leg(role):
                errors.append(f"Missing leg: {role.value}")

        if state.center_strike is None:
            errors.append("center_strike is None")

        if state.wing_distance is None:
            errors.append("wing_distance is None")

        if errors:
            for err in errors:
                logger.error(f"State validation failed: {err}")

        return errors
