"""
base_strategy.py
----------------
Abstract base class that every concrete strategy must implement.

The framework's MonitoringEngine calls ``strategy.evaluate(context)`` each
cycle.  This contract defines exactly what the framework expects from a
strategy, and nothing more.  Business logic lives entirely inside the
concrete strategy — never in the framework.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from ..models.strategy_context import StrategyContext
from ..models.strategy_state import StrategyState


class BaseStrategy(ABC):
    """
    Abstract base for all strategies.

    Concrete strategies (IronFlyStrategy, IronCondorStrategy, etc.) must
    implement all abstract methods.

    The framework guarantees:
    - ``evaluate(context)`` is called once per monitoring cycle.
    - ``context`` is a fully populated ``StrategyContext`` snapshot.
    - All I/O has already been performed — no OpenAlgo calls needed inside evaluate.
    - ``state`` is the mutable strategy state shared between cycles.
    """

    def __init__(self, state: StrategyState, config: Any, gateway: Any) -> None:
        """
        Parameters
        ----------
        state   : Mutable ``StrategyState`` instance (persists across cycles)
        config  : ``Config`` object exposing all settings
        gateway : ``ExecutionGateway`` for placing / closing orders
        """
        self.state = state
        self.config = config
        self.gateway = gateway

    # ---------------------------------------------------------------------------
    # Required interface
    # ---------------------------------------------------------------------------

    @abstractmethod
    def evaluate(self, context: StrategyContext) -> None:
        """
        Evaluate the strategy for this monitoring cycle.

        The implementation must:
        - Read ALL decisions from *context* (no external I/O inside this method).
        - Use ``self.gateway`` for any order placement.
        - Update ``self.state`` to reflect any changes.
        - Log everything important.
        - Be idempotent — calling twice must not double-apply actions.

        Parameters
        ----------
        context : Immutable snapshot of market + account data for this cycle.
        """

    @abstractmethod
    def is_deployment_due(self, context: StrategyContext) -> bool:
        """
        Return True if this is the right time to deploy a fresh strategy.

        Typical implementation: first trading day of the month, after
        DEPLOYMENT_TIME, and no existing positions.
        """

    @abstractmethod
    def deploy(self, context: StrategyContext) -> None:
        """
        Deploy a fresh strategy instance.

        Must be idempotent: verify no positions exist before placing orders.
        """

    @abstractmethod
    def should_exit(self, context: StrategyContext) -> tuple[bool, str]:
        """
        Return ``(True, reason)`` if the strategy should be exited immediately,
        or ``(False, "")`` to continue monitoring.

        Reasons include MTM lock breach, expiry day exit, etc.
        """

    @abstractmethod
    def exit_all(self, context: StrategyContext, reason: str) -> None:
        """
        Exit all open legs of the strategy.

        Must handle partial exits gracefully (some legs may already be closed).
        """

    # ---------------------------------------------------------------------------
    # Optional hooks (default no-ops — concrete strategies override as needed)
    # ---------------------------------------------------------------------------

    def on_start(self) -> None:
        """Called once when the strategy is first started / resumed."""

    def on_cycle_start(self, context: StrategyContext) -> None:
        """Called at the beginning of each monitoring cycle, before evaluate()."""

    def on_cycle_end(self, context: StrategyContext) -> None:
        """Called at the end of each monitoring cycle, after evaluate()."""

    def on_stop(self) -> None:
        """Called when the monitoring loop is shutting down cleanly."""

    # ---------------------------------------------------------------------------
    # Framework helpers available to all strategies
    # ---------------------------------------------------------------------------

    @property
    def strategy_id(self) -> str:
        """Unique strategy tag used to label all orders."""
        return self.state.strategy_id

    @property
    def strategy_name(self) -> str:
        return self.state.strategy_name

    def is_paused(self) -> bool:
        from ..models.strategy_state import StrategyPhase
        return self.state.phase == StrategyPhase.PAUSED

    def pause(self, reason: str) -> None:
        """Transition the strategy to PAUSED state."""
        self.state.pause(reason)
