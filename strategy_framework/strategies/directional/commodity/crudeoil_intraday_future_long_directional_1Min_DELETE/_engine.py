"""
_engine.py
----------
Shared indicator engine, signal state, and rolling-window helpers for the
Crudeoil Intraday Futures Directional strategy.

Imported by both the execution script (entry evaluation) and the monitoring
script (exit evaluation).  Contains NO broker calls, DB writes, or I/O.

Components
----------
CoilIndicatorState
    Live snapshot of all computed indicator values.

CoilIndicatorEngine
    Seeds from history and applies incremental O(1) updates per closed candle.

RollingLowWindow
    Tracks the minimum low of the last N 1-min candles (used for swing-low exit).

evaluate_entry(ind) -> bool
    All three entry conditions evaluated simultaneously.

evaluate_exit(ind, close, low_window, entry_price, initial_sl_pts,
              step_trail, rsi_exit_counter) -> Optional[str]
    All exit conditions checked in priority order.  Returns the exit reason
    string or None.
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

# ---------------------------------------------------------------------------
# Resolve package root so this file can be run from any working directory
# ---------------------------------------------------------------------------
import sys as _sys
_framework_root = Path(__file__).resolve().parent.parent.parent.parent.parent.parent
if str(_framework_root) not in _sys.path:
    _sys.path.insert(0, str(_framework_root))

from strategy_framework.framework.candle_builder import CandleBuilder
from strategy_framework.framework.config_loader import Config
from strategy_framework.framework.indicators import (
    ema_from_series, ema_step,
    macd_seed, macd_step,
    rsi_seed, rsi_step,
    supertrend_seed, supertrend_step,
)
from strategy_framework.framework.signal import ConfirmationCounter
from strategy_framework.framework.trailing import PriceStepTrailSL
from strategy_framework.utils.logger import get_logger

logger = get_logger(__name__)

# Exit reason string constants (returned by evaluate_exit)
EXIT_RSI_CROSS_BELOW  = "RSI_CROSS_BELOW_EMA21"
EXIT_MACD_CROSS_BELOW = "MACD_CROSS_BELOW_SIGNAL"
EXIT_SWING_LOW_BREAK  = "SWING_LOW_BREAK"
EXIT_INITIAL_SL       = "INITIAL_SL_HIT"
EXIT_SUPERTREND_BREAK = "SUPERTREND_BREAK"
EXIT_TRAILING_SL      = "TRAILING_SL_HIT"


# ---------------------------------------------------------------------------
# Indicator state  (strategy-local — superset of framework IndicatorState)
# ---------------------------------------------------------------------------

@dataclass
class CoilIndicatorState:
    """
    All live indicator values needed by the Crudeoil strategy.

    Updated by CoilIndicatorEngine on every closed 1-min candle.
    """

    # Price snapshot
    close: Optional[float] = None
    high:  Optional[float] = None
    low:   Optional[float] = None

    # RSI-14
    rsi:            Optional[float] = None
    rsi_avg_gain:   Optional[float] = None
    rsi_avg_loss:   Optional[float] = None
    rsi_prev_close: Optional[float] = None

    # RSI EMA series — two periods
    rsi_ema_fast:   Optional[float] = None   # EMA(RSI, RSI_EMA_FAST_PERIOD  e.g. 8)
    rsi_ema_slow:   Optional[float] = None   # EMA(RSI, RSI_EMA_SLOW_PERIOD  e.g. 21)

    # MACD (fast=8, slow=21, signal=5)
    macd_state:   Optional[dict]  = None     # internal state for macd_step
    macd_line:    Optional[float] = None
    macd_signal:  Optional[float] = None

    # Supertrend (period=11, factor=2.0)
    supertrend:           Optional[float] = None
    supertrend_direction: Optional[int]   = None   # -1=bullish, +1=bearish
    supertrend_state:     Optional[dict]  = None   # internal state for supertrend_step

    # Previous candle values (for crossover detection)
    prev_close:       Optional[float] = None
    prev_rsi:         Optional[float] = None
    prev_rsi_ema_slow: Optional[float] = None
    prev_macd_line:   Optional[float] = None
    prev_macd_signal: Optional[float] = None
    prev_supertrend:  Optional[float] = None

    def snapshot_previous(self) -> None:
        """Call at the START of each candle update before overwriting current values."""
        self.prev_close       = self.close
        self.prev_rsi         = self.rsi
        self.prev_rsi_ema_slow = self.rsi_ema_slow
        self.prev_macd_line   = self.macd_line
        self.prev_macd_signal = self.macd_signal
        self.prev_supertrend  = self.supertrend

    @property
    def is_ready(self) -> bool:
        """True when all indicators have at least one full value."""
        return (
            self.close        is not None
            and self.rsi      is not None
            and self.rsi_ema_fast  is not None
            and self.rsi_ema_slow  is not None
            and self.macd_line     is not None
            and self.macd_signal   is not None
            and self.supertrend    is not None
            and self.prev_rsi      is not None   # need prev for crossover detection
        )

    # ── Crossover helpers ──────────────────────────────────────────────────

    @property
    def rsi_crossed_below_ema_slow(self) -> bool:
        """True if RSI14 just crossed from above to below RSI_EMA_SLOW on this bar."""
        if None in (self.prev_rsi, self.prev_rsi_ema_slow, self.rsi, self.rsi_ema_slow):
            return False
        return self.prev_rsi >= self.prev_rsi_ema_slow and self.rsi < self.rsi_ema_slow

    @property
    def macd_crossed_below_signal(self) -> bool:
        """True if MACD line just crossed from above to below MACD Signal on this bar."""
        if None in (self.prev_macd_line, self.prev_macd_signal, self.macd_line, self.macd_signal):
            return False
        return (self.prev_macd_line >= self.prev_macd_signal
                and self.macd_line < self.macd_signal)


# ---------------------------------------------------------------------------
# Indicator engine
# ---------------------------------------------------------------------------

class CoilIndicatorEngine:
    """
    Seeds all indicators from a historical CandleBuilder snapshot and applies
    O(1) incremental updates on each new closed 1-min candle.

    Indicators computed:
      - RSI-14
      - EMA(RSI-14, RSI_EMA_FAST_PERIOD)   e.g. EMA8
      - EMA(RSI-14, RSI_EMA_SLOW_PERIOD)   e.g. EMA21
      - MACD(MACD_FAST, MACD_SLOW, MACD_SIGNAL)   e.g. (8, 21, 5)
      - Supertrend(SUPERTREND_PERIOD, SUPERTREND_FACTOR)   e.g. (11, 2.0)

    Config keys consumed
    --------------------
    RSI_PERIOD          int   (default 14)
    RSI_EMA_FAST_PERIOD int   (default  8)
    RSI_EMA_SLOW_PERIOD int   (default 21)
    MACD_FAST           int   (default  8)
    MACD_SLOW           int   (default 21)
    MACD_SIGNAL         int   (default  5)
    SUPERTREND_PERIOD   int   (default 11)
    SUPERTREND_FACTOR   float (default  2.0)
    """

    def __init__(self, config: Config) -> None:
        self.state        = CoilIndicatorState()
        self._rsi_p       = config.get_int("RSI_PERIOD",           14)
        self._rsi_fast_p  = config.get_int("RSI_EMA_FAST_PERIOD",   8)
        self._rsi_slow_p  = config.get_int("RSI_EMA_SLOW_PERIOD",  21)
        self._macd_fast   = config.get_int("MACD_FAST",             8)
        self._macd_slow   = config.get_int("MACD_SLOW",            21)
        self._macd_sig    = config.get_int("MACD_SIGNAL",           5)
        self._st_p        = config.get_int("SUPERTREND_PERIOD",    11)
        self._st_f        = config.get_float("SUPERTREND_FACTOR",  2.0)

    # ── Warm-up ──────────────────────────────────────────────────────────────

    def seed(self, builder: CandleBuilder) -> None:
        """Full O(N) recalculation from the builder's historical bars."""
        closes = builder.closes()
        highs  = builder.highs()
        lows   = builder.lows()
        n      = len(closes)

        min_bars = self._macd_slow + self._macd_sig + 1
        if n < min_bars:
            logger.warning("CoilIndicatorEngine: only %d bars (need %d) — partial warm-up.", n, min_bars)

        self.state.close = float(closes[-1])
        self.state.high  = float(highs[-1])
        self.state.low   = float(lows[-1])

        # ── RSI ───────────────────────────────────────────────────────────
        rsi_state = rsi_seed(closes, self._rsi_p)
        if rsi_state:
            ag, al     = rsi_state
            rsi_series: list[float] = []
            for i in range(self._rsi_p, n):
                rval, ag, al = rsi_step(ag, al, closes[i], closes[i - 1], self._rsi_p)
                rsi_series.append(rval)
            if rsi_series:
                self.state.rsi            = rsi_series[-1]
                self.state.rsi_avg_gain   = ag
                self.state.rsi_avg_loss   = al
                self.state.rsi_prev_close = float(closes[-1])
                arr = np.array(rsi_series, dtype=float)
                if len(arr) >= self._rsi_fast_p:
                    self.state.rsi_ema_fast = ema_from_series(arr, self._rsi_fast_p)
                if len(arr) >= self._rsi_slow_p:
                    self.state.rsi_ema_slow = ema_from_series(arr, self._rsi_slow_p)

        # ── MACD ──────────────────────────────────────────────────────────
        ms = macd_seed(closes, self._macd_fast, self._macd_slow, self._macd_sig)
        if ms:
            self.state.macd_state  = ms
            self.state.macd_line   = ms["macd_line"]
            self.state.macd_signal = ms["signal_line"]

        # ── Supertrend ────────────────────────────────────────────────────
        if n >= self._st_p + 2:
            st = supertrend_seed(highs, lows, closes, self._st_p, self._st_f)
            if st:
                self.state.supertrend_state     = st
                self.state.supertrend           = st["supertrend"]
                self.state.supertrend_direction = st["direction"]

        logger.info(
            "CoilIndicatorEngine seeded: close=%.2f RSI=%.1f EMA_fast=%.1f EMA_slow=%.1f "
            "MACD=%.4f Sig=%.4f ST=%.2f dir=%s",
            self.state.close or 0,
            self.state.rsi or 0,
            self.state.rsi_ema_fast or 0,
            self.state.rsi_ema_slow or 0,
            self.state.macd_line or 0,
            self.state.macd_signal or 0,
            self.state.supertrend or 0,
            self.state.supertrend_direction,
        )

    # ── Per-candle update ─────────────────────────────────────────────────────

    def update(self, builder: CandleBuilder) -> None:
        """O(1) incremental update using the last closed candle from ``builder``."""
        bar = builder.last_closed
        if bar is None:
            return

        self.state.snapshot_previous()

        self.state.close = bar.close
        self.state.high  = bar.high
        self.state.low   = bar.low

        # ── RSI ───────────────────────────────────────────────────────────
        if (self.state.rsi_avg_gain is not None
                and self.state.rsi_avg_loss is not None
                and self.state.rsi_prev_close is not None):
            rval, ag, al = rsi_step(
                self.state.rsi_avg_gain, self.state.rsi_avg_loss,
                bar.close, self.state.rsi_prev_close, self._rsi_p,
            )
            self.state.rsi            = rval
            self.state.rsi_avg_gain   = ag
            self.state.rsi_avg_loss   = al
            self.state.rsi_prev_close = bar.close

            if self.state.rsi_ema_fast is not None:
                self.state.rsi_ema_fast = ema_step(rval, self.state.rsi_ema_fast, self._rsi_fast_p)
            if self.state.rsi_ema_slow is not None:
                self.state.rsi_ema_slow = ema_step(rval, self.state.rsi_ema_slow, self._rsi_slow_p)

        # ── MACD ──────────────────────────────────────────────────────────
        if self.state.macd_state is not None:
            ms = macd_step(
                self.state.macd_state, bar.close,
                self._macd_fast, self._macd_slow, self._macd_sig,
            )
            self.state.macd_line   = ms["macd_line"]
            self.state.macd_signal = ms["signal_line"]

        # ── Supertrend ────────────────────────────────────────────────────
        if self.state.supertrend_state is not None:
            st = supertrend_step(
                self.state.supertrend_state,
                bar.high, bar.low, bar.close,
                self._st_p, self._st_f,
            )
            self.state.supertrend           = st["supertrend"]
            self.state.supertrend_direction = st["direction"]

        logger.debug(
            "M1 | close=%.2f RSI=%.1f rsi_ef=%.1f rsi_es=%.1f MACD=%.4f Sig=%.4f ST=%.2f dir=%s",
            self.state.close or 0,
            self.state.rsi or 0,
            self.state.rsi_ema_fast or 0,
            self.state.rsi_ema_slow or 0,
            self.state.macd_line or 0,
            self.state.macd_signal or 0,
            self.state.supertrend or 0,
            self.state.supertrend_direction,
        )


# ---------------------------------------------------------------------------
# Rolling low window
# ---------------------------------------------------------------------------

class RollingLowWindow:
    """
    Tracks the minimum low of the last ``n`` closed 1-min candles.

    Used for exit condition: M1 Close < min(last 4 candle lows).
    """

    def __init__(self, n: int = 4) -> None:
        self._window: collections.deque = collections.deque(maxlen=n)
        self._n = n

    def update(self, low: float) -> None:
        """Add the current candle's low to the rolling window."""
        self._window.append(low)

    @property
    def is_ready(self) -> bool:
        return len(self._window) >= self._n

    @property
    def min_low(self) -> Optional[float]:
        """Minimum low over the last n candles, or None if not enough data."""
        return float(min(self._window)) if self.is_ready else None

    @property
    def values(self) -> list[float]:
        return list(self._window)


# ---------------------------------------------------------------------------
# Entry condition evaluator
# ---------------------------------------------------------------------------

def evaluate_entry(ind: CoilIndicatorState) -> bool:
    """
    Return True when ALL three entry conditions are met simultaneously.

    Entry conditions (all required):
      1. M1 Close > M1 Supertrend(11, 2)
      2. M1 MACD(slow=21, fast=8, signal=5) > MACD Signal
         OR M1 RSI14 EMA8 > M1 RSI14 EMA21
      3. M1 RSI14 > M1 RSI14 EMA21
    """
    if not ind.is_ready:
        return False
    if ind.close is None or ind.supertrend is None:
        return False

    # Condition 1: Close above Supertrend (Supertrend is bullish, direction=-1)
    c1 = ind.close > ind.supertrend

    # Condition 2: MACD above Signal  OR  RSI_EMA8 above RSI_EMA21
    c2 = (
        (ind.macd_line  is not None and ind.macd_signal  is not None
         and ind.macd_line > ind.macd_signal)
        or
        (ind.rsi_ema_fast is not None and ind.rsi_ema_slow is not None
         and ind.rsi_ema_fast > ind.rsi_ema_slow)
    )

    # Condition 3: RSI14 above RSI EMA21
    c3 = (ind.rsi is not None and ind.rsi_ema_slow is not None
          and ind.rsi > ind.rsi_ema_slow)

    if c1 and c2 and c3:
        logger.debug(
            "ENTRY conditions met: close=%.2f ST=%.2f MACD=%.4f Sig=%.4f "
            "RSI=%.1f EMA_slow=%.1f",
            ind.close, ind.supertrend or 0,
            ind.macd_line or 0, ind.macd_signal or 0,
            ind.rsi or 0, ind.rsi_ema_slow or 0,
        )
    return c1 and c2 and c3


# ---------------------------------------------------------------------------
# Exit condition evaluator
# ---------------------------------------------------------------------------

def evaluate_exit(
    ind:              CoilIndicatorState,
    close:            float,
    low_window:       RollingLowWindow,
    entry_price:      float,
    initial_sl_pts:   float,
    step_trail:       PriceStepTrailSL,
    rsi_exit_counter: ConfirmationCounter,
) -> Optional[str]:
    """
    Evaluate all exit conditions after each closed 1-min candle.
    Returns the exit reason string or None.

    Call order reflects user-defined priority (indicator exits first, then SL).

    Exit conditions:
      1. RSI14 crosses below RSI_EMA21 AND stays below for 2 consecutive bars
      2. MACD line crosses below MACD Signal (immediate)
      3. M1 Close < minimum of last 4 candle lows (immediate)
      4. M1 Close < entry_price - initial_sl_pts  (initial hard SL)
      5. M1 Close < Supertrend value              (supertrend break)
      6. M1 Close <= PriceStepTrailSL level        (trailing SL hit)

    Notes
    -----
    - ``step_trail.update(close)`` is called here before checking condition 6.
    - ``low_window`` must be updated by the caller BEFORE calling this function
      (so the current candle's low is already in the window).
    """

    # 1. RSI14 crosses below RSI_EMA21 — confirmed over 2 consecutive bars
    rsi_below_ema = (
        ind.rsi is not None and ind.rsi_ema_slow is not None
        and ind.rsi < ind.rsi_ema_slow
    )
    if rsi_exit_counter.update(rsi_below_ema):
        logger.warning(
            "EXIT triggered: RSI cross below EMA21 (confirmed 2 bars). "
            "RSI=%.1f EMA_slow=%.1f",
            ind.rsi or 0, ind.rsi_ema_slow or 0,
        )
        return EXIT_RSI_CROSS_BELOW

    # 2. MACD crosses below Signal (single bar — no confirmation)
    if ind.macd_crossed_below_signal:
        logger.warning(
            "EXIT triggered: MACD cross below Signal. MACD=%.4f Sig=%.4f",
            ind.macd_line or 0, ind.macd_signal or 0,
        )
        return EXIT_MACD_CROSS_BELOW

    # 3. Close breaks below min(last 4 candle lows)
    if low_window.is_ready:
        min_low = low_window.min_low
        if min_low is not None and close < min_low:
            logger.warning(
                "EXIT triggered: close=%.2f < min_4_lows=%.2f",
                close, min_low,
            )
            return EXIT_SWING_LOW_BREAK

    # 4. Initial hard SL (30 points from entry)
    if close < entry_price - initial_sl_pts:
        logger.warning(
            "EXIT triggered: Initial SL hit. close=%.2f entry=%.2f sl=%.2f",
            close, entry_price, entry_price - initial_sl_pts,
        )
        return EXIT_INITIAL_SL

    # 5. Close breaks below Supertrend value
    if ind.supertrend is not None and close < ind.supertrend:
        logger.warning(
            "EXIT triggered: Close=%.2f below Supertrend=%.2f",
            close, ind.supertrend,
        )
        return EXIT_SUPERTREND_BREAK

    # 6. Trailing SL (update first, then check)
    step_trail.update(close)
    if step_trail.is_triggered(close):
        logger.warning(
            "EXIT triggered: Trailing SL. close=%.2f sl=%.2f activated=%s",
            close, step_trail.current_sl, step_trail.is_activated,
        )
        return EXIT_TRAILING_SL

    return None
