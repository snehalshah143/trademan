"""
base_strategy.py
----------------
Abstract base class for all strategies in the framework.

Architecture
------------
Every strategy is made of two kinds of code:

  FIXED  (lives here / in runners)
    - WebSocket wiring, candle building, indicator warm-up
    - Idempotency check, sizing, margin preflight
    - Order execution, DB persistence, notifier calls
    - Monitoring loop, heartbeat, MTM snapshots
    - Trailing SL machinery, profit ladder
    - Expiry-day and market-close forced exits

  PLUGGABLE  (lives in the concrete strategy class)
    - evaluate_signal()  — what conditions define an entry signal
    - select_legs()      — what instruments / strikes / legs to build
    - check_exit()       — when and why to close the position

How to build a new strategy
----------------------------
1. Subclass ``BaseStrategy``.
2. Implement the three abstract methods below.
3. Wire up via the runners::

    from strategy_framework.framework.entry_runner import EntryRunner
    from strategy_framework.framework.monitoring_runner import MonitoringRunner

    strategy = MyStrategy(config)
    EntryRunner(strategy, config).run()        # execution script
    MonitoringRunner(strategy, config).run()   # monitoring script

That's it.  No boilerplate to copy.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from .config_loader import Config
    from .signal import Bias, IndicatorState
    from .trailing import TrailingSlManager
    from ..models.option_leg import OptionLeg


# ---------------------------------------------------------------------------
# Data transfer object — result of leg selection
# ---------------------------------------------------------------------------

@dataclass
class StrategyLegsResult:
    """
    All information about the legs selected for a strategy entry.

    For a 2-leg spread: ``all_legs`` has 2 items.
    For a 4-leg iron fly: ``all_legs`` has 4 items.
    The runner will execute them in the order provided.

    Attributes
    ----------
    spread_type  : Human-readable label (e.g. "BULL_PUT_SPREAD", "IRON_FLY").
    all_legs     : All OptionLeg objects in desired execution order.
    net_premium  : Expected net credit collected per lot at current LTPs.
    max_profit   : Maximum possible profit (₹), or None if not applicable.
    max_loss     : Maximum possible loss (₹, positive number), or None.
    notes        : Optional free-text for DB storage.
    """
    spread_type : str
    all_legs    : list["OptionLeg"]
    net_premium : float
    max_profit  : Optional[float] = None
    max_loss    : Optional[float] = None
    notes       : Optional[str]  = None


# ---------------------------------------------------------------------------
# Monitoring cycle snapshot passed to check_exit()
# ---------------------------------------------------------------------------

@dataclass
class CycleState:
    """
    Snapshot of the current monitoring cycle — passed to ``check_exit()``.

    Using a dataclass keeps the ``check_exit`` signature stable: new fields
    can be added here without breaking existing strategy implementations.

    Attributes
    ----------
    mtm          : Current unrealized P&L (₹).
    max_mtm      : Best MTM seen so far this session.
    spot         : Underlying spot price.
    bias         : Entry bias string ("BULLISH" or "BEARISH").
    legs         : All position legs.
    trailing_sl  : Live TrailingSlManager (read current_sl, is_triggered, etc.).
    st_engine    : Live StTrailEngine (direction, value, just_flipped).
    dte          : Calendar days to expiry (0 = expiry day).
    now          : Current IST datetime.
    cycle        : Monitoring cycle counter (1-based).
    indicators   : Latest indicator snapshot (may be None if not yet warmed up).
    """
    mtm         : float
    max_mtm     : float
    spot        : float
    bias        : str
    legs        : list["OptionLeg"]
    trailing_sl : "TrailingSlManager"
    st_engine   : Any           # StTrailEngine — typed as Any to avoid circular import
    dte         : int
    now         : datetime
    cycle       : int
    indicators  : Optional["IndicatorState"] = None


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class BaseStrategy(ABC):
    """
    Abstract base for all strategies.

    Subclasses must implement exactly three methods:

    evaluate_signal(indicators) → Bias
        Called on every M15 candle close.  Return BULLISH, BEARISH, or NEUTRAL.

    select_legs(bias, spot, expiry, exchange, interval, quantity) → StrategyLegsResult
        Called once when the signal is confirmed.  Return the legs to trade.

    check_exit(state) → Optional[str]
        Called every monitoring cycle.  Return an exit-reason string to trigger
        a full exit, or None to keep monitoring.

    The framework handles everything else.
    """

    def __init__(self, config: "Config") -> None:
        self.config = config
        self._indicator_engine = self._build_indicator_engine()

    # ── Must implement ────────────────────────────────────────────────────────

    @abstractmethod
    def _build_indicator_engine(self) -> Any:
        """
        Return an indicator engine with two methods:
          - ``seed(builder: CandleBuilder)``   — warm-up from history
          - ``update(builder: CandleBuilder)`` — incremental update per closed bar
          - ``.state`` property                — returns IndicatorState
        """

    @abstractmethod
    def evaluate_signal(self, indicators: "IndicatorState") -> "Bias":
        """
        Compute directional bias from the current indicator snapshot.

        Returns
        -------
        Bias.BULLISH / Bias.BEARISH → triggers entry confirmation counter.
        Bias.NEUTRAL                → resets counter.
        """

    @abstractmethod
    def select_legs(
        self,
        bias: "Bias",
        spot: float,
        expiry: str,
        exchange: str,
        interval: float,
        quantity: int,
    ) -> StrategyLegsResult:
        """
        Build and return all legs for entry given the confirmed bias.

        Parameters
        ----------
        bias     : Confirmed signal direction.
        spot     : Current underlying spot price.
        expiry   : Selected expiry string (e.g. "03NOV26").
        exchange : Options exchange (e.g. "NFO").
        interval : Strike interval (e.g. 50.0 for NIFTY).
        quantity : Total quantity per leg (lots × lot_size).

        Returns
        -------
        StrategyLegsResult with all legs populated.
        """

    @abstractmethod
    def check_exit(self, state: CycleState) -> Optional[str]:
        """
        Evaluate whether the position should be closed this cycle.

        Called after MTM is computed and trailing SL is updated.
        The runner already handles SL breach (via TrailingSlManager) — this
        method is for strategy-specific exits such as:
          - Supertrend flip against the position bias
          - RSI mean-reversion threshold
          - Profit target
          - Custom time-based rules

        Returns
        -------
        Exit-reason string (e.g. "ST_FLIP", "TARGET", "CUSTOM") or None.
        """

    # ── Provided — indicator engine delegation ────────────────────────────────

    def seed_indicators(self, builder: Any) -> None:
        """Warm up indicators from candle history."""
        self._indicator_engine.seed(builder)

    def update_indicators(self, builder: Any) -> None:
        """Incremental indicator update after each M15 candle close."""
        self._indicator_engine.update(builder)

    @property
    def indicators(self) -> "IndicatorState":
        """Current indicator snapshot."""
        return self._indicator_engine.state

    # ── Optional hooks (override to add custom behaviour) ─────────────────────

    def on_signal_first_match(self, bias: "Bias", indicators: "IndicatorState") -> None:
        """Called the first bar a signal condition is satisfied (before N-check)."""

    def on_signal_confirmed(self, bias: "Bias", indicators: "IndicatorState") -> None:
        """Called when the N-check counter fires (signal is confirmed)."""

    def on_entry_complete(self, result: StrategyLegsResult, trade_mode: str) -> None:
        """Called after all entry orders are confirmed and filled."""

    def on_exit_complete(
        self, legs: list["OptionLeg"], reason: str, trade_mode: str
    ) -> None:
        """Called after all exit orders are confirmed and filled."""
