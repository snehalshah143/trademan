"""
signal.py
---------
Signal evaluation helpers for directional credit spread strategies.

Components
----------
Bias
    Enum: BULLISH / BEARISH / NEUTRAL.

ConfirmationCounter
    N-check pattern: fires only when a condition is True for *N* consecutive
    candle closes.  Resets to zero on any False reading.

crossover_above / crossover_below
    Stateless functions for detecting value-vs-level crossovers between two
    successive readings.

supertrend_flipped_bullish / supertrend_flipped_bearish
    Convenience wrappers for Supertrend direction-change detection.

SignalState
    Tracks the active directional bias and an optional bar-count cooldown that
    prevents re-entry immediately after a trade is placed.

IndicatorState
    Holds all live indicator values for one symbol/timeframe combination.
    Updated on every closed candle by the strategy's on_candle_closed() hook.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class Bias(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


# ---------------------------------------------------------------------------
# ConfirmationCounter — N-check pattern
# ---------------------------------------------------------------------------

@dataclass
class ConfirmationCounter:
    """
    Fires exactly once when a condition remains True for ``required`` consecutive
    candle closes.

    Rules
    -----
    - ``update(True)``  → increments internal count.
      Returns ``True`` the instant count reaches ``required`` (fires once per crossing).
    - ``update(False)`` → resets count to 0; returns ``False``.
    - After firing, count stays at ``required`` until ``reset()`` is called.
      This means repeated ``update(True)`` calls DO NOT re-fire the counter.

    Usage
    -----
    ::

        counter = ConfirmationCounter(required=2)

        for bar in live_bars:
            condition = rsi > 60 and supertrend_bullish
            if counter.update(condition):
                enter_bull_spread()
                counter.reset()
    """

    required: int = 2
    _count: int = field(default=0, init=False, repr=False)
    _has_fired: bool = field(default=False, init=False, repr=False)

    def update(self, condition: bool) -> bool:
        """
        Feed the latest condition result.

        Returns ``True`` exactly once when the consecutive count first reaches
        ``required``.  Subsequent calls with ``True`` return ``False`` until
        ``reset()`` is called (one fire per crossing).
        """
        if not condition:
            self._count = 0
            self._has_fired = False
            return False

        if self._count < self.required:
            self._count += 1

        if self._count == self.required and not self._has_fired:
            self._has_fired = True
            logger.debug(
                "ConfirmationCounter: FIRED (required=%d consecutive True)", self.required
            )
            return True
        return False

    def reset(self) -> None:
        """Reset the counter to zero (call after acting on the fired signal)."""
        self._count = 0
        self._has_fired = False

    @property
    def count(self) -> int:
        """Current consecutive True count (0 .. required)."""
        return self._count

    @property
    def is_building(self) -> bool:
        """True if at least one True has been seen but the threshold is not yet reached."""
        return 0 < self._count < self.required

    @property
    def has_fired(self) -> bool:
        """True if the counter is at the threshold (fired but not yet reset)."""
        return self._count >= self.required


# ---------------------------------------------------------------------------
# Stateless crossover helpers
# ---------------------------------------------------------------------------

def crossover_above(
    current: Optional[float],
    previous: Optional[float],
    level: float,
) -> bool:
    """
    Returns ``True`` if *current* > *level* AND *previous* <= *level*.

    Detects an upward crossing of *level* between two successive readings.
    Returns ``False`` if either value is ``None``.
    """
    if current is None or previous is None:
        return False
    return previous <= level < current


def crossover_below(
    current: Optional[float],
    previous: Optional[float],
    level: float,
) -> bool:
    """
    Returns ``True`` if *current* < *level* AND *previous* >= *level*.

    Detects a downward crossing of *level* between two successive readings.
    Returns ``False`` if either value is ``None``.
    """
    if current is None or previous is None:
        return False
    return previous >= level > current


def supertrend_flipped_bullish(prev_direction: int, curr_direction: int) -> bool:
    """
    Returns ``True`` if Supertrend just flipped from bearish → bullish.

    Convention (same as ``indicators.supertrend_step``):
        direction == -1  → bullish  (price above Supertrend line)
        direction == +1  → bearish  (price below Supertrend line)
    """
    return prev_direction == 1 and curr_direction == -1


def supertrend_flipped_bearish(prev_direction: int, curr_direction: int) -> bool:
    """
    Returns ``True`` if Supertrend just flipped from bullish → bearish.
    """
    return prev_direction == -1 and curr_direction == 1


# ---------------------------------------------------------------------------
# SignalState — directional bias + cooldown
# ---------------------------------------------------------------------------

@dataclass
class SignalState:
    """
    Tracks the current directional signal bias with a bar-count cooldown.

    After a trade is placed the caller should call ``enter_cooldown(n)`` to
    suppress re-entry signals for the next *n* candle closes.  Call
    ``tick_candle()`` on every closed bar to decrement the cooldown counter.

    Attributes
    ----------
    bias             : Current directional bias.
    bars_in_cooldown : Remaining bars before cooldown expires (0 = not cooling).
    """

    bias: Bias = Bias.NEUTRAL
    bars_in_cooldown: int = 0

    # ── Bias setters ────────────────────────────────────────────────────────

    def set_bullish(self) -> None:
        self.bias = Bias.BULLISH
        logger.debug("SignalState: bias → BULLISH")

    def set_bearish(self) -> None:
        self.bias = Bias.BEARISH
        logger.debug("SignalState: bias → BEARISH")

    def set_neutral(self) -> None:
        self.bias = Bias.NEUTRAL
        logger.debug("SignalState: bias → NEUTRAL")

    # ── Cooldown ────────────────────────────────────────────────────────────

    def enter_cooldown(self, bars: int) -> None:
        """Lock signals for the next *bars* candle closes."""
        self.bars_in_cooldown = bars
        logger.debug("SignalState: cooldown started for %d bars", bars)

    def tick_candle(self) -> None:
        """Decrement the cooldown counter by one (call once per closed candle)."""
        if self.bars_in_cooldown > 0:
            self.bars_in_cooldown -= 1
            if self.bars_in_cooldown == 0:
                logger.debug("SignalState: cooldown expired")

    # ── Properties ──────────────────────────────────────────────────────────

    @property
    def in_cooldown(self) -> bool:
        return self.bars_in_cooldown > 0

    @property
    def is_bullish(self) -> bool:
        return self.bias == Bias.BULLISH

    @property
    def is_bearish(self) -> bool:
        return self.bias == Bias.BEARISH

    @property
    def is_neutral(self) -> bool:
        return self.bias == Bias.NEUTRAL


# ---------------------------------------------------------------------------
# IndicatorState — live indicator snapshot for one symbol
# ---------------------------------------------------------------------------

@dataclass
class IndicatorState:
    """
    Snapshot of all computed indicator values for one symbol/timeframe pair.

    Updated by the strategy's ``on_candle_closed()`` hook after each
    1-minute bar is finalized.

    All values are ``None`` until enough history is available for the
    warm-up calculation.
    """

    # Price
    close: Optional[float] = None          # last candle close (for base condition)

    # RSI
    rsi: Optional[float] = None
    rsi_avg_gain: Optional[float] = None
    rsi_avg_loss: Optional[float] = None
    rsi_prev_close: Optional[float] = None
    rsi_ema: Optional[float] = None        # EMA of RSI series (signal line)

    # EMA (price)
    ema_fast: Optional[float] = None       # e.g. EMA(9)
    ema_slow: Optional[float] = None       # e.g. EMA(21) — used as base condition

    # Supertrend
    supertrend: Optional[float] = None
    supertrend_direction: Optional[int] = None   # -1 = bullish, +1 = bearish
    supertrend_state: Optional[dict] = None      # full state dict for incremental step

    # MACD
    macd_line: Optional[float] = None
    macd_signal: Optional[float] = None
    macd_hist: Optional[float] = None
    macd_state: Optional[dict] = None      # internal state dict for macd_step O(1) updates

    # Second RSI EMA (shorter period — e.g. EMA8 vs EMA21)
    rsi_ema_fast: Optional[float] = None   # e.g. EMA(RSI, 8); rsi_ema holds the slow EMA

    # Previous tick values (for crossover / flip detection)
    prev_close: Optional[float] = None
    prev_rsi: Optional[float] = None
    prev_rsi_ema: Optional[float] = None
    prev_rsi_ema_fast: Optional[float] = None
    prev_supertrend_direction: Optional[int] = None
    prev_ema_fast: Optional[float] = None
    prev_ema_slow: Optional[float] = None
    prev_macd_line: Optional[float] = None
    prev_macd_signal: Optional[float] = None

    def snapshot_previous(self) -> None:
        """
        Copy current values into prev_* fields.
        Call once at the START of each candle-close update before overwriting.
        """
        self.prev_close                = self.close
        self.prev_rsi                  = self.rsi
        self.prev_rsi_ema              = self.rsi_ema
        self.prev_rsi_ema_fast         = self.rsi_ema_fast
        self.prev_supertrend_direction = self.supertrend_direction
        self.prev_ema_fast             = self.ema_fast
        self.prev_ema_slow             = self.ema_slow
        self.prev_macd_line            = self.macd_line
        self.prev_macd_signal          = self.macd_signal

    @property
    def is_ready(self) -> bool:
        """True if the minimum required indicators are available (non-None)."""
        return (
            self.close is not None
            and self.rsi is not None
            and self.rsi_ema is not None
            and self.supertrend is not None
            and self.ema_slow is not None
            and self.prev_supertrend_direction is not None
        )
