"""
monitor.py
----------
MonitoringEngine — the heartbeat of the framework.

Responsibilities
----------------
- Run the monitoring loop on a configurable interval.
- Each cycle: build context → call strategy.evaluate() → log heartbeat.
- Handle exceptions without crashing — log and continue.
- Detect shutdown signals (SIGINT / SIGTERM) and exit cleanly.
- No business logic whatsoever — pure orchestration.
"""

from __future__ import annotations

import signal
import time
from datetime import datetime
from typing import Optional

from ..base.base_strategy import BaseStrategy
from ..framework.context_builder import ContextBuildError, ContextBuilder
from ..framework.openalgo_client import OpenAlgoClient
from ..models.strategy_state import StrategyState
from ..utils.constants import HEARTBEAT_SEPARATOR
from ..utils.helper import format_inr, now_ist
from ..utils.logger import get_logger

logger = get_logger(__name__)


class MonitoringEngine:
    """
    Runs the monitoring loop and orchestrates each evaluation cycle.

    Parameters
    ----------
    strategy        : Concrete strategy instance (must implement BaseStrategy)
    context_builder : Builds StrategyContext from OpenAlgo each cycle
    interval        : Seconds between monitoring cycles
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        context_builder: ContextBuilder,
        interval: int = 180,
    ) -> None:
        self._strategy = strategy
        self._context_builder = context_builder
        self._interval = interval
        self._running = False
        self._cycle_count = 0

        # Register clean shutdown on SIGINT / SIGTERM
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

    # ---------------------------------------------------------------------------
    # Main loop
    # ---------------------------------------------------------------------------

    def run(self) -> None:
        """
        Start the monitoring loop.

        Blocks until ``stop()`` is called or a shutdown signal is received.
        """
        logger.info(
            f"MonitoringEngine starting — strategy={self._strategy.strategy_name} "
            f"interval={self._interval}s"
        )
        self._running = True
        self._strategy.on_start()

        while self._running:
            cycle_start = time.time()
            self._cycle_count += 1

            try:
                self._run_cycle()
            except Exception as exc:
                logger.exception(
                    f"Unhandled exception in monitoring cycle {self._cycle_count}: {exc}"
                )
                # Never crash the loop — log and continue

            # Wait for the remainder of the interval
            elapsed = time.time() - cycle_start
            sleep_time = max(0.0, self._interval - elapsed)
            if self._running:
                logger.debug(f"Cycle {self._cycle_count} complete in {elapsed:.1f}s. Sleeping {sleep_time:.1f}s …")
                time.sleep(sleep_time)

        logger.info("MonitoringEngine stopped.")
        self._strategy.on_stop()

    def stop(self) -> None:
        """Signal the monitoring loop to exit after the current cycle."""
        logger.info("MonitoringEngine: stop requested.")
        self._running = False

    # ---------------------------------------------------------------------------
    # Single cycle
    # ---------------------------------------------------------------------------

    def _run_cycle(self) -> None:
        """Execute one full monitoring cycle."""
        now = now_ist()
        logger.debug(f"=== Monitoring cycle {self._cycle_count} at {now.strftime('%H:%M:%S')} ===")

        # Step 1: Build context (gather all OpenAlgo data)
        try:
            context = self._context_builder.build()
        except ContextBuildError as exc:
            logger.error(f"Context build failed — skipping cycle: {exc}")
            self._log_heartbeat_error(str(exc))
            return

        # Step 2: Notify strategy of cycle start
        try:
            self._strategy.on_cycle_start(context)
        except Exception as exc:
            logger.warning(f"on_cycle_start raised: {exc}")

        # Step 3: Evaluate strategy
        if not self._strategy.is_paused():
            try:
                self._strategy.evaluate(context)
            except Exception as exc:
                logger.exception(f"strategy.evaluate() raised: {exc}")
                # Pause the strategy on unexpected errors to prevent runaway logic
                self._strategy.pause(f"Unhandled error in evaluate(): {exc}")
        else:
            logger.warning(
                f"Strategy is PAUSED ({self._strategy.state.paused_reason}). "
                f"Skipping evaluation. Manual intervention required."
            )

        # Step 4: Notify strategy of cycle end
        try:
            self._strategy.on_cycle_end(context)
        except Exception as exc:
            logger.warning(f"on_cycle_end raised: {exc}")

        # Step 5: Log heartbeat
        self._log_heartbeat(context)

    # ---------------------------------------------------------------------------
    # Heartbeat logging
    # ---------------------------------------------------------------------------

    def _log_heartbeat(self, context) -> None:
        """Log the standard heartbeat block every monitoring cycle."""
        state = self._strategy.state
        now_str = now_ist().strftime("%H:%M")

        open_legs = state.open_legs
        mtm = state.current_mtm
        state.update_mtm_highwater()

        lines = [
            HEARTBEAT_SEPARATOR,
            f"Strategy Alive",
            f"Time         : {now_str}",
            f"Mode         : {self._strategy.config.get('EXECUTION_MODE', 'N/A')}",
            f"State        : {state.phase.value}",
            f"Spot         : {context.spot:.0f}",
            f"Center       : {state.center_strike or 'N/A'}",
            f"Wing Dist    : {state.wing_distance or 'N/A'}",
            f"Expiry       : {context.expiry}",
            f"Adj Count    : {state.adjustment_count}",
            f"Open Legs    : {len(open_legs)}",
            f"Current MTM  : {format_inr(mtm)}",
            f"Maximum MTM  : {format_inr(state.max_mtm)}",
            f"Locked MTM   : {format_inr(state.locked_mtm) if state.locked_mtm else 'None'}",
            f"Margin       : {format_inr(context.available_cash)}",
            f"Heartbeat    : OK",
            HEARTBEAT_SEPARATOR,
        ]

        for line in lines:
            logger.info(line)

    def _log_heartbeat_error(self, error_msg: str) -> None:
        """Log a minimal heartbeat when context building fails."""
        now_str = now_ist().strftime("%H:%M")
        lines = [
            HEARTBEAT_SEPARATOR,
            f"Strategy Alive",
            f"Time         : {now_str}",
            f"State        : CONTEXT_BUILD_FAILED",
            f"Error        : {error_msg}",
            f"Heartbeat    : DEGRADED",
            HEARTBEAT_SEPARATOR,
        ]
        for line in lines:
            logger.warning(line)

    # ---------------------------------------------------------------------------
    # Signal handler
    # ---------------------------------------------------------------------------

    def _handle_shutdown(self, signum: int, frame) -> None:
        logger.info(f"Shutdown signal {signum} received. Stopping after current cycle …")
        self._running = False
