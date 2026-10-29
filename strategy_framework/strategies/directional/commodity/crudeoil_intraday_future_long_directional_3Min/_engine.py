"""
_engine.py
----------
Triple-timeframe indicator engine for the Crudeoil Intraday Futures 3-Min strategy.

Timeframe split
---------------
  M1  (1-min candles)  : RSI-14, RSI-EMA8, RSI-EMA21, MACD-A(8,21,5), MACD-B(21,34,8),
                          Supertrend(11,2.5), price EMA21/EMA34
  M3  (3-min candles)  : RSI-14, RSI-EMA8, RSI-EMA21, MACD(8,21,5), Supertrend(11,2.5),
                          price EMA8/EMA21
  M15 (15-min candles) : RSI-14, RSI-EMA8, MACD-A(8,21,5), MACD-B(5,11,3), Supertrend(11,2.5)

Evaluation rhythm
-----------------
  Entry and exit conditions are evaluated on every closed 1-min candle.
  M3 and M15 indicators retain their last computed value between their candle closes.

Base condition — LONG (must pass BEFORE C1–C3 are checked):
  Group A — M15 momentum (any 2 of 3 must be true):
    M15-MACD-A(8,21,5)  > M15-MACD-A-Signal
    M15-RSI14           > M15-RSI-EMA8
    M15-MACD-B(5,11,3)  > M15-MACD-B-Signal
  Group B — at least 2 Supertrends bullish (any 2 of 3):
    M15-Supertrend-Direction == -1
    M3-Supertrend-Direction  == -1
    M1-Supertrend-Direction  == -1
  Group C — M3 bullish alignment (any 2 of 3 must be true):
    M3-EMA8(price) > M3-EMA21(price)
    M3-RSI-EMA8    > M3-RSI-EMA21
    M3-MACD(8,21,5) > M3-MACD-Signal

Base condition — SHORT (mirror of LONG, all comparisons reversed):
  Group A : M15 momentum bearish (any 2 of 3 < comparisons)
  Group B : at least 2 Supertrends bearish (+1)
  Group C : M3 bearish alignment (any 2 of 3 < comparisons)

Entry — LONG (ALL C1–C2 must be true on a M1 candle close):
  1. M3-RSI14 > M3-RSI14-EMA21
  2. abs(M3-MACD-Histogram) > 0.95
  (removed: M3-Supertrend — already covered by base Group B)
  (removed: M3-MACD/RSI-EMA momentum — covered by base Group C)
  (removed: M1-RSI14/Supertrend confirmation — covered by base Group B)

Entry — SHORT (mirror, all comparisons reversed)

Exit — LONG (first matching condition):
  1. M3-RSI14 crosses below M3-RSI14-EMA21   (on M3 candle close)
  2. M3-MACD crosses below M3-Signal          (on M3 candle close)
  3. AT LEAST 3 of 4: M1-ST turns bearish(+1)
                      M1-MACD-A(8,21,5) < M1-MACD-A-Signal
                      M1-MACD-B(21,34,8) < M1-MACD-B-Signal
                      M1-RSI-EMA8 < M1-RSI-EMA21
  4. M1-Close < min(last 2 M3-candle lows)   (TEMPORARILY DISABLED)
  5. M1-Close < entry_price - initial_sl_pts  (initial hard SL)
  6. MultiPhaseTrailSL hit

Exit — SHORT (mirror of LONG, all comparisons reversed)
  Condition 3 fires when: M1-ST turns bullish(-1) OR M1-MACD-A > Signal
                          OR M1-MACD-B > Signal   OR M1-RSI-EMA8 > M1-RSI-EMA21

Config keys used
----------------
  RSI_PERIOD, RSI_EMA_FAST_PERIOD, RSI_EMA_SLOW_PERIOD  — M1, M3, M15
  MACD_FAST, MACD_SLOW, MACD_SIGNAL                     — M3 MACD
  SUPERTREND_PERIOD, SUPERTREND_FACTOR                  — M1, M3, M15
  M15_MACD_A_FAST(8), M15_MACD_A_SLOW(21), M15_MACD_A_SIGNAL(5)
  M15_MACD_B_FAST(5), M15_MACD_B_SLOW(11), M15_MACD_B_SIGNAL(3)
  M1_MACD_A_FAST(8),  M1_MACD_A_SLOW(21),  M1_MACD_A_SIGNAL(5)
  M1_MACD_B_FAST(21), M1_MACD_B_SLOW(34),  M1_MACD_B_SIGNAL(8)
"""
from __future__ import annotations

import collections
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

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
from strategy_framework.framework.trailing import MultiPhaseTrailSL, PriceStepTrailSL
from strategy_framework.utils.logger import get_logger

logger = get_logger(__name__)


def _safe_cmp(a, b, is_long: bool) -> bool:
    """
    Directional comparison: ``a > b`` for LONG, ``a < b`` for SHORT.
    Returns False if either value is None.
    Eliminates all ``if is_long: ... else: ...`` comparison blocks.
    """
    if a is None or b is None:
        return False
    return a > b if is_long else a < b


# Exit reason constants
EXIT_RSI_CROSS_BELOW  = "RSI_CROSS_BELOW_EMA21"
EXIT_MACD_CROSS_BELOW = "MACD_CROSS_BELOW_SIGNAL"
EXIT_SWING_LOW_BREAK  = "SWING_LOW_BREAK"
EXIT_INITIAL_SL       = "INITIAL_SL_HIT"
EXIT_SUPERTREND_BREAK = "SUPERTREND_BREAK"
EXIT_TRAILING_SL      = "TRAILING_SL_HIT"
EXIT_M1_MOMENTUM_BREAK = "M1_MOMENTUM_BREAK"
EXIT_PROFIT_TARGET    = "PROFIT_TARGET"


# ---------------------------------------------------------------------------
# Indicator state
# ---------------------------------------------------------------------------

@dataclass
class Coil3MinIndicatorState:
    """
    Live snapshot of all indicator values for the 3-Min strategy.

    M1 indicators (updated on every closed 1-min candle):
      close/high/low, RSI-14, RSI-EMA21, Supertrend(11,2.5)

    M3 indicators (updated only when a 3-min candle closes):
      close/high/low, RSI-14, RSI-EMA8, RSI-EMA21,
      MACD(8,21,5), Supertrend(11,2.5)

    prev_m3_* values are snapshotted before each M3 update for
    crossover detection (MACD cross, RSI cross).
    """

    # ── M1: price ─────────────────────────────────────────────────────────────
    m1_close: Optional[float] = None
    m1_high:  Optional[float] = None
    m1_low:   Optional[float] = None

    # ── M1: RSI-14 ────────────────────────────────────────────────────────────
    m1_rsi:            Optional[float] = None
    m1_rsi_avg_gain:   Optional[float] = None
    m1_rsi_avg_loss:   Optional[float] = None
    m1_rsi_prev_close: Optional[float] = None

    # ── M1: RSI-EMA8 and RSI-EMA21 ────────────────────────────────────────────
    m1_rsi_ema_fast: Optional[float] = None   # EMA(M1-RSI, RSI_EMA_FAST_PERIOD=8)
    m1_rsi_ema_slow: Optional[float] = None   # EMA(M1-RSI, RSI_EMA_SLOW_PERIOD=21)

    # ── M1: price EMAs (for re-entry filter) ──────────────────────────────────
    m1_ema21_price: Optional[float] = None   # EMA(M1-Close, M1_REENTRY_EMA_FAST=21)
    m1_ema34_price: Optional[float] = None   # EMA(M1-Close, M1_REENTRY_EMA_SLOW=34)

    # ── M1: Supertrend(11, 2.5) ─────────────────────────────────────────────────
    m1_supertrend:           Optional[float] = None
    m1_supertrend_direction: Optional[int]   = None   # -1 = bullish, +1 = bearish
    m1_supertrend_state:     Optional[dict]  = None

    # ── M1: MACD-A (fast=8, slow=21, signal=5) — used in exit conditions ──────
    m1_macd_a_state:   Optional[dict]  = None
    m1_macd_a_line:    Optional[float] = None
    m1_macd_a_signal:  Optional[float] = None

    # ── M1: MACD-B (fast=21, slow=34, signal=8) — used in exit conditions ─────
    m1_macd_b_state:   Optional[dict]  = None
    m1_macd_b_line:    Optional[float] = None
    m1_macd_b_signal:  Optional[float] = None

    # ── M3: price ─────────────────────────────────────────────────────────────
    m3_close: Optional[float] = None
    m3_high:  Optional[float] = None
    m3_low:   Optional[float] = None

    # ── M3: RSI-14 ────────────────────────────────────────────────────────────
    m3_rsi:            Optional[float] = None
    m3_rsi_avg_gain:   Optional[float] = None
    m3_rsi_avg_loss:   Optional[float] = None
    m3_rsi_prev_close: Optional[float] = None

    # ── M3: RSI-EMA8 and RSI-EMA21 ────────────────────────────────────────────
    m3_rsi_ema_fast: Optional[float] = None   # EMA(M3-RSI, RSI_EMA_FAST_PERIOD=8)
    m3_rsi_ema_slow: Optional[float] = None   # EMA(M3-RSI, RSI_EMA_SLOW_PERIOD=21)

    # ── M3: price EMAs (for re-entry pullback check) ──────────────────────────
    m3_ema8_price:  Optional[float] = None   # EMA(M3-Close, M3_PRICE_EMA_FAST=8)
    m3_ema21_price: Optional[float] = None   # EMA(M3-Close, M3_PRICE_EMA_SLOW=21)

    # ── M3: MACD(8, 21, 5) ────────────────────────────────────────────────────
    m3_macd_state:  Optional[dict]  = None
    m3_macd_line:   Optional[float] = None
    m3_macd_signal: Optional[float] = None

    # ── M3: Supertrend(11, 2.5) ─────────────────────────────────────────────────
    m3_supertrend:           Optional[float] = None
    m3_supertrend_direction: Optional[int]   = None   # -1 = bullish, +1 = bearish
    m3_supertrend_state:     Optional[dict]  = None

    # ── M15: price ────────────────────────────────────────────────────────────
    m15_close: Optional[float] = None
    m15_high:  Optional[float] = None
    m15_low:   Optional[float] = None

    # ── M15: RSI-14 and RSI-EMA8 ──────────────────────────────────────────────
    m15_rsi:            Optional[float] = None
    m15_rsi_avg_gain:   Optional[float] = None
    m15_rsi_avg_loss:   Optional[float] = None
    m15_rsi_prev_close: Optional[float] = None
    m15_rsi_ema_fast:   Optional[float] = None   # EMA(M15-RSI14, RSI_EMA_FAST_PERIOD=8)

    # ── M15: MACD-A (fast=8, slow=21, signal=5) ───────────────────────────────
    m15_macd_a_state:   Optional[dict]  = None
    m15_macd_a_line:    Optional[float] = None
    m15_macd_a_signal:  Optional[float] = None

    # ── M15: MACD-B (fast=5, slow=11, signal=3) ───────────────────────────────
    m15_macd_b_state:   Optional[dict]  = None
    m15_macd_b_line:    Optional[float] = None
    m15_macd_b_signal:  Optional[float] = None

    # ── M15: Supertrend ────────────────────────────────────────────────────────
    m15_supertrend:           Optional[float] = None
    m15_supertrend_direction: Optional[int]   = None   # -1 = bullish, +1 = bearish
    m15_supertrend_state:     Optional[dict]  = None

    # ── M15: Live fields — updated every M1 close via update_m15_live() ───────
    # One step ahead of anchored state using current M1 price.
    # Anchored state (above) stays frozen between M15 closes — matches chart values.
    # Live fields are used by build_entry_conditions() for real-time Group A evaluation.
    m15_rsi_live:             Optional[float] = None
    m15_rsi_ema_fast_live:    Optional[float] = None
    m15_macd_a_line_live:     Optional[float] = None
    m15_macd_a_signal_live:   Optional[float] = None
    m15_macd_b_line_live:     Optional[float] = None
    m15_macd_b_signal_live:   Optional[float] = None

    # ── M3 previous values (for crossover detection) ─────────────────────────
    # Snapshotted at the start of each update_m3() call.
    prev_m3_rsi:          Optional[float] = None
    prev_m3_rsi_ema_slow: Optional[float] = None
    prev_m3_macd_line:    Optional[float] = None
    prev_m3_macd_signal:  Optional[float] = None

    def snapshot_m3_previous(self) -> None:
        """Call at the START of update_m3() before overwriting M3 values."""
        self.prev_m3_rsi          = self.m3_rsi
        self.prev_m3_rsi_ema_slow = self.m3_rsi_ema_slow
        self.prev_m3_macd_line    = self.m3_macd_line
        self.prev_m3_macd_signal  = self.m3_macd_signal

    @property
    def is_ready(self) -> bool:
        """
        True when all indicators (both timeframes) have at least one computed
        value.  Requires at least 1 live M3 candle close after warm-up (so
        prev_m3_* values are available for crossover detection on exit).
        """
        return (
            # M1
            self.m1_close            is not None
            and self.m1_rsi          is not None
            and self.m1_rsi_ema_slow is not None
            and self.m1_supertrend   is not None
            # M3
            and self.m3_close             is not None
            and self.m3_rsi               is not None
            and self.m3_rsi_ema_fast      is not None
            and self.m3_rsi_ema_slow      is not None
            and self.m3_macd_line         is not None
            and self.m3_macd_signal       is not None
            and self.m3_supertrend        is not None
            # prev M3 available for crossover detection
            and self.prev_m3_rsi          is not None
            # M15
            and self.m15_rsi              is not None
            and self.m15_rsi_ema_fast     is not None
            and self.m15_macd_a_line      is not None
            and self.m15_macd_b_line      is not None
            and self.m15_supertrend       is not None
        )

    # ── M3 crossover helpers (for exit detection) ─────────────────────────────

    @property
    def m3_rsi_crossed_below_ema21(self) -> bool:
        """True on the first evaluation after M3-RSI14 crosses below M3-RSI-EMA21."""
        if None in (
            self.prev_m3_rsi, self.prev_m3_rsi_ema_slow,
            self.m3_rsi,      self.m3_rsi_ema_slow,
        ):
            return False
        return (
            self.prev_m3_rsi >= self.prev_m3_rsi_ema_slow
            and self.m3_rsi  <  self.m3_rsi_ema_slow
        )

    @property
    def m3_rsi_crossed_above_ema21(self) -> bool:
        """True on the first evaluation after M3-RSI14 crosses above M3-RSI-EMA21."""
        if None in (
            self.prev_m3_rsi, self.prev_m3_rsi_ema_slow,
            self.m3_rsi,      self.m3_rsi_ema_slow,
        ):
            return False
        return (
            self.prev_m3_rsi <= self.prev_m3_rsi_ema_slow
            and self.m3_rsi  >  self.m3_rsi_ema_slow
        )

    @property
    def m3_macd_crossed_below_signal(self) -> bool:
        """True on the first evaluation after M3-MACD crosses below M3-Signal."""
        if None in (
            self.prev_m3_macd_line, self.prev_m3_macd_signal,
            self.m3_macd_line,      self.m3_macd_signal,
        ):
            return False
        return (
            self.prev_m3_macd_line >= self.prev_m3_macd_signal
            and self.m3_macd_line  <  self.m3_macd_signal
        )

    @property
    def m3_macd_crossed_above_signal(self) -> bool:
        """True on the first evaluation after M3-MACD crosses above M3-Signal."""
        if None in (
            self.prev_m3_macd_line, self.prev_m3_macd_signal,
            self.m3_macd_line,      self.m3_macd_signal,
        ):
            return False
        return (
            self.prev_m3_macd_line <= self.prev_m3_macd_signal
            and self.m3_macd_line  >  self.m3_macd_signal
        )


# ---------------------------------------------------------------------------
# Dual-timeframe indicator engine
# ---------------------------------------------------------------------------

class Coil3MinIndicatorEngine:
    """
    Dual-timeframe indicator engine.

    M1 (1-min) — RSI-14, EMA(RSI, slow=21), Supertrend(period, factor)
    M3 (3-min) — RSI-14, EMA(RSI, fast=8), EMA(RSI, slow=21),
                  MACD(fast, slow, signal), Supertrend(period, factor)

    Usage pattern
    -------------
    ::

        m1_builder = CandleBuilder(symbol, interval_minutes=1)
        m3_builder = CandleBuilder(symbol, interval_minutes=3)

        engine = Coil3MinIndicatorEngine(config)
        engine.seed(m1_builder, m3_builder)     # warm-up from history

        # On every tick, feed both builders.
        # When a 3-min candle closes:
        engine.update_m3(m3_builder)

        # When a 1-min candle closes:
        engine.update_m1(m1_builder)
        # → evaluate_entry(engine.state) or evaluate_exit(...)
    """

    def __init__(self, config: Config) -> None:
        self.state       = Coil3MinIndicatorState()
        self._rsi_p      = config.get_int("RSI_PERIOD",             14)
        self._rsi_fast_p = config.get_int("RSI_EMA_FAST_PERIOD",     8)
        self._rsi_slow_p = config.get_int("RSI_EMA_SLOW_PERIOD",    21)
        self._macd_fast  = config.get_int("MACD_FAST",               8)
        self._macd_slow  = config.get_int("MACD_SLOW",              21)
        self._macd_sig   = config.get_int("MACD_SIGNAL",             5)
        self._st_p       = config.get_int("SUPERTREND_PERIOD",      11)
        self._st_f       = config.get_float("SUPERTREND_FACTOR",   2.5)
        # price EMAs for re-entry
        self._m3_pe_fast = config.get_int("M3_PRICE_EMA_FAST",       8)
        self._m3_pe_slow = config.get_int("M3_PRICE_EMA_SLOW",      21)
        self._m1_re_fast = config.get_int("M1_REENTRY_EMA_FAST",    21)
        self._m1_re_slow = config.get_int("M1_REENTRY_EMA_SLOW",    34)
        # M15 indicators
        self._m15_macd_a_fast = config.get_int("M15_MACD_A_FAST",    8)
        self._m15_macd_a_slow = config.get_int("M15_MACD_A_SLOW",   21)
        self._m15_macd_a_sig  = config.get_int("M15_MACD_A_SIGNAL",  5)
        self._m15_macd_b_fast = config.get_int("M15_MACD_B_FAST",    5)
        self._m15_macd_b_slow = config.get_int("M15_MACD_B_SLOW",   11)
        self._m15_macd_b_sig  = config.get_int("M15_MACD_B_SIGNAL",  3)
        # M1 MACD (used in exit momentum-break condition)
        self._m1_macd_a_fast  = config.get_int("M1_MACD_A_FAST",    8)
        self._m1_macd_a_slow  = config.get_int("M1_MACD_A_SLOW",   21)
        self._m1_macd_a_sig   = config.get_int("M1_MACD_A_SIGNAL",  5)
        self._m1_macd_b_fast  = config.get_int("M1_MACD_B_FAST",   21)
        self._m1_macd_b_slow  = config.get_int("M1_MACD_B_SLOW",   34)
        self._m1_macd_b_sig   = config.get_int("M1_MACD_B_SIGNAL",  8)

    # ── Warm-up ───────────────────────────────────────────────────────────────

    def seed(
        self,
        m1_builder:  CandleBuilder,
        m3_builder:  CandleBuilder,
        m15_builder: CandleBuilder,
    ) -> None:
        """Seed M1, M3, and M15 indicators from their respective builders."""
        self._seed_m1(m1_builder)
        self._seed_m3(m3_builder)
        self._seed_m15(m15_builder)
        logger.info(
            "Coil3MinIndicatorEngine seeded:\n"
            "  M1 [close=%.2f RSI=%.1f EMA21=%.1f ST=%.2f dir=%s]\n"
            "  M3 [close=%.2f RSI=%.1f EMA8=%.1f EMA21=%.1f MACD=%.4f Sig=%.4f ST=%.2f dir=%s]\n"
            "  M15[close=%.2f RSI=%.1f EMA8=%.1f MACD-A=%.4f Sig-A=%.4f MACD-B=%.4f Sig-B=%.4f ST=%.2f dir=%s]",
            self.state.m1_close or 0,
            self.state.m1_rsi or 0, self.state.m1_rsi_ema_slow or 0,
            self.state.m1_supertrend or 0, self.state.m1_supertrend_direction,
            self.state.m3_close or 0,
            self.state.m3_rsi or 0, self.state.m3_rsi_ema_fast or 0, self.state.m3_rsi_ema_slow or 0,
            self.state.m3_macd_line or 0, self.state.m3_macd_signal or 0,
            self.state.m3_supertrend or 0, self.state.m3_supertrend_direction,
            self.state.m15_close or 0,
            self.state.m15_rsi or 0, self.state.m15_rsi_ema_fast or 0,
            self.state.m15_macd_a_line or 0, self.state.m15_macd_a_signal or 0,
            self.state.m15_macd_b_line or 0, self.state.m15_macd_b_signal or 0,
            self.state.m15_supertrend or 0, self.state.m15_supertrend_direction,
        )

    def _seed_m1(self, builder: CandleBuilder) -> None:
        """Seed M1 indicators: RSI-14, RSI-EMA21, Supertrend(11,2.5)."""
        closes = builder.closes()
        highs  = builder.highs()
        lows   = builder.lows()
        n      = len(closes)

        if n < 2:
            return

        self.state.m1_close = float(closes[-1])
        self.state.m1_high  = float(highs[-1])
        self.state.m1_low   = float(lows[-1])

        # RSI-14
        rsi_state = rsi_seed(closes, self._rsi_p)
        if rsi_state:
            ag, al = rsi_state
            rsi_series: list[float] = []
            for i in range(self._rsi_p, n):
                rval, ag, al = rsi_step(ag, al, closes[i], closes[i - 1], self._rsi_p)
                rsi_series.append(rval)
            if rsi_series:
                self.state.m1_rsi            = rsi_series[-1]
                self.state.m1_rsi_avg_gain   = ag
                self.state.m1_rsi_avg_loss   = al
                self.state.m1_rsi_prev_close = float(closes[-1])
                arr = np.array(rsi_series, dtype=float)
                if len(arr) >= self._rsi_fast_p:
                    self.state.m1_rsi_ema_fast = ema_from_series(arr, self._rsi_fast_p)
                if len(arr) >= self._rsi_slow_p:
                    self.state.m1_rsi_ema_slow = ema_from_series(arr, self._rsi_slow_p)

        # Supertrend(11, 2.5)
        if n >= self._st_p + 2:
            st = supertrend_seed(highs, lows, closes, self._st_p, self._st_f)
            if st:
                self.state.m1_supertrend_state     = st
                self.state.m1_supertrend           = st["supertrend"]
                self.state.m1_supertrend_direction = st["direction"]

        # MACD-A(8, 21, 5) and MACD-B(21, 34, 8) — for exit momentum-break
        ms_a = macd_seed(closes, self._m1_macd_a_fast, self._m1_macd_a_slow, self._m1_macd_a_sig)
        if ms_a:
            self.state.m1_macd_a_state  = ms_a
            self.state.m1_macd_a_line   = ms_a["macd_line"]
            self.state.m1_macd_a_signal = ms_a["signal_line"]
        ms_b = macd_seed(closes, self._m1_macd_b_fast, self._m1_macd_b_slow, self._m1_macd_b_sig)
        if ms_b:
            self.state.m1_macd_b_state  = ms_b
            self.state.m1_macd_b_line   = ms_b["macd_line"]
            self.state.m1_macd_b_signal = ms_b["signal_line"]

        # Price EMAs for re-entry filter
        if n >= self._m1_re_fast:
            self.state.m1_ema21_price = ema_from_series(closes, self._m1_re_fast)
        if n >= self._m1_re_slow:
            self.state.m1_ema34_price = ema_from_series(closes, self._m1_re_slow)

    def _seed_m3(self, builder: CandleBuilder) -> None:
        """Seed M3 indicators: RSI-14, RSI-EMA8, RSI-EMA21, MACD(8,21,5), Supertrend(11,2.5)."""
        closes = builder.closes()
        highs  = builder.highs()
        lows   = builder.lows()
        n      = len(closes)

        if n < 2:
            return

        self.state.m3_close = float(closes[-1])
        self.state.m3_high  = float(highs[-1])
        self.state.m3_low   = float(lows[-1])

        # RSI-14 + RSI-EMA8 + RSI-EMA21
        rsi_state = rsi_seed(closes, self._rsi_p)
        if rsi_state:
            ag, al = rsi_state
            rsi_series: list[float] = []
            for i in range(self._rsi_p, n):
                rval, ag, al = rsi_step(ag, al, closes[i], closes[i - 1], self._rsi_p)
                rsi_series.append(rval)
            if rsi_series:
                self.state.m3_rsi            = rsi_series[-1]
                self.state.m3_rsi_avg_gain   = ag
                self.state.m3_rsi_avg_loss   = al
                self.state.m3_rsi_prev_close = float(closes[-1])
                arr = np.array(rsi_series, dtype=float)
                if len(arr) >= self._rsi_fast_p:
                    self.state.m3_rsi_ema_fast = ema_from_series(arr, self._rsi_fast_p)
                if len(arr) >= self._rsi_slow_p:
                    self.state.m3_rsi_ema_slow = ema_from_series(arr, self._rsi_slow_p)

        # MACD(8, 21, 5)
        ms = macd_seed(closes, self._macd_fast, self._macd_slow, self._macd_sig)
        if ms:
            self.state.m3_macd_state  = ms
            self.state.m3_macd_line   = ms["macd_line"]
            self.state.m3_macd_signal = ms["signal_line"]

        # Supertrend(11, 2.5)
        if n >= self._st_p + 2:
            st = supertrend_seed(highs, lows, closes, self._st_p, self._st_f)
            if st:
                self.state.m3_supertrend_state     = st
                self.state.m3_supertrend           = st["supertrend"]
                self.state.m3_supertrend_direction = st["direction"]
        else:
            logger.warning(
                "M3 builder has %d bars; need %d for Supertrend seed — "
                "Supertrend will build from live 3-min candles.",
                n, self._st_p + 2,
            )

        # Price EMAs for re-entry pullback check
        if n >= self._m3_pe_fast:
            self.state.m3_ema8_price  = ema_from_series(closes, self._m3_pe_fast)
        if n >= self._m3_pe_slow:
            self.state.m3_ema21_price = ema_from_series(closes, self._m3_pe_slow)

    # ── Per-candle incremental updates ───────────────────────────────────────

    def update_m1(self, m1_builder: CandleBuilder) -> None:
        """O(1) update of M1 indicators on each closed 1-min candle."""
        bar = m1_builder.last_closed
        if bar is None:
            return

        self.state.m1_close = bar.close
        self.state.m1_high  = bar.high
        self.state.m1_low   = bar.low

        # RSI-14
        if (self.state.m1_rsi_avg_gain is not None
                and self.state.m1_rsi_avg_loss is not None
                and self.state.m1_rsi_prev_close is not None):
            rval, ag, al = rsi_step(
                self.state.m1_rsi_avg_gain, self.state.m1_rsi_avg_loss,
                bar.close, self.state.m1_rsi_prev_close, self._rsi_p,
            )
            self.state.m1_rsi            = rval
            self.state.m1_rsi_avg_gain   = ag
            self.state.m1_rsi_avg_loss   = al
            self.state.m1_rsi_prev_close = bar.close

            if self.state.m1_rsi_ema_fast is not None:
                self.state.m1_rsi_ema_fast = ema_step(
                    rval, self.state.m1_rsi_ema_fast, self._rsi_fast_p,
                )
            if self.state.m1_rsi_ema_slow is not None:
                self.state.m1_rsi_ema_slow = ema_step(
                    rval, self.state.m1_rsi_ema_slow, self._rsi_slow_p,
                )

        # Supertrend(11, 2.5)
        if self.state.m1_supertrend_state is not None:
            st = supertrend_step(
                self.state.m1_supertrend_state,
                bar.high, bar.low, bar.close,
                self._st_p, self._st_f,
            )
            self.state.m1_supertrend           = st["supertrend"]
            self.state.m1_supertrend_direction = st["direction"]

        # MACD-A(8, 21, 5) and MACD-B(21, 34, 8)
        if self.state.m1_macd_a_state is not None:
            ms = macd_step(
                self.state.m1_macd_a_state, bar.close,
                self._m1_macd_a_fast, self._m1_macd_a_slow, self._m1_macd_a_sig,
            )
            self.state.m1_macd_a_line   = ms["macd_line"]
            self.state.m1_macd_a_signal = ms["signal_line"]
        if self.state.m1_macd_b_state is not None:
            ms = macd_step(
                self.state.m1_macd_b_state, bar.close,
                self._m1_macd_b_fast, self._m1_macd_b_slow, self._m1_macd_b_sig,
            )
            self.state.m1_macd_b_line   = ms["macd_line"]
            self.state.m1_macd_b_signal = ms["signal_line"]

        # Price EMAs for re-entry filter
        if self.state.m1_ema21_price is not None:
            self.state.m1_ema21_price = ema_step(
                bar.close, self.state.m1_ema21_price, self._m1_re_fast,
            )
        if self.state.m1_ema34_price is not None:
            self.state.m1_ema34_price = ema_step(
                bar.close, self.state.m1_ema34_price, self._m1_re_slow,
            )

        logger.debug(
            "M1 | close=%.2f RSI=%.1f EMA21=%.1f ST=%.2f dir=%s",
            self.state.m1_close or 0,
            self.state.m1_rsi or 0,
            self.state.m1_rsi_ema_slow or 0,
            self.state.m1_supertrend or 0,
            self.state.m1_supertrend_direction,
        )

    def update_m3(self, m3_builder: CandleBuilder) -> None:
        """O(1) update of M3 indicators on each closed 3-min candle."""
        bar = m3_builder.last_closed
        if bar is None:
            return

        # Snapshot M3 previous values FIRST for crossover detection
        self.state.snapshot_m3_previous()

        self.state.m3_close = bar.close
        self.state.m3_high  = bar.high
        self.state.m3_low   = bar.low

        # RSI-14
        if (self.state.m3_rsi_avg_gain is not None
                and self.state.m3_rsi_avg_loss is not None
                and self.state.m3_rsi_prev_close is not None):
            rval, ag, al = rsi_step(
                self.state.m3_rsi_avg_gain, self.state.m3_rsi_avg_loss,
                bar.close, self.state.m3_rsi_prev_close, self._rsi_p,
            )
            self.state.m3_rsi            = rval
            self.state.m3_rsi_avg_gain   = ag
            self.state.m3_rsi_avg_loss   = al
            self.state.m3_rsi_prev_close = bar.close

            if self.state.m3_rsi_ema_fast is not None:
                self.state.m3_rsi_ema_fast = ema_step(
                    rval, self.state.m3_rsi_ema_fast, self._rsi_fast_p,
                )
            if self.state.m3_rsi_ema_slow is not None:
                self.state.m3_rsi_ema_slow = ema_step(
                    rval, self.state.m3_rsi_ema_slow, self._rsi_slow_p,
                )

        # MACD(8, 21, 5)
        if self.state.m3_macd_state is not None:
            ms = macd_step(
                self.state.m3_macd_state, bar.close,
                self._macd_fast, self._macd_slow, self._macd_sig,
            )
            self.state.m3_macd_line   = ms["macd_line"]
            self.state.m3_macd_signal = ms["signal_line"]

        # Supertrend(11, 2.5)
        if self.state.m3_supertrend_state is not None:
            st = supertrend_step(
                self.state.m3_supertrend_state,
                bar.high, bar.low, bar.close,
                self._st_p, self._st_f,
            )
            self.state.m3_supertrend           = st["supertrend"]
            self.state.m3_supertrend_direction = st["direction"]

        # Price EMAs for re-entry pullback check
        if self.state.m3_ema8_price is not None:
            self.state.m3_ema8_price  = ema_step(
                bar.close, self.state.m3_ema8_price, self._m3_pe_fast,
            )
        if self.state.m3_ema21_price is not None:
            self.state.m3_ema21_price = ema_step(
                bar.close, self.state.m3_ema21_price, self._m3_pe_slow,
            )

        logger.debug(
            "M3 | close=%.2f RSI=%.1f EMA8=%.1f EMA21=%.1f MACD=%.4f Sig=%.4f ST=%.2f dir=%s",
            self.state.m3_close or 0,
            self.state.m3_rsi or 0,
            self.state.m3_rsi_ema_fast or 0,
            self.state.m3_rsi_ema_slow or 0,
            self.state.m3_macd_line or 0,
            self.state.m3_macd_signal or 0,
            self.state.m3_supertrend or 0,
            self.state.m3_supertrend_direction,
        )

    def _seed_m15(self, builder: CandleBuilder) -> None:
        """Seed M15 indicators: RSI-14, RSI-EMA8, MACD-A(8,21,5), MACD-B(5,11,3), Supertrend(11,2.5)."""
        closes = builder.closes()
        highs  = builder.highs()
        lows   = builder.lows()
        n      = len(closes)

        if n < 2:
            return

        self.state.m15_close = float(closes[-1])
        self.state.m15_high  = float(highs[-1])
        self.state.m15_low   = float(lows[-1])

        # RSI-14 + RSI-EMA8
        rsi_state = rsi_seed(closes, self._rsi_p)
        if rsi_state:
            ag, al = rsi_state
            rsi_series: list[float] = []
            for i in range(self._rsi_p, n):
                rval, ag, al = rsi_step(ag, al, closes[i], closes[i - 1], self._rsi_p)
                rsi_series.append(rval)
            if rsi_series:
                self.state.m15_rsi            = rsi_series[-1]
                self.state.m15_rsi_avg_gain   = ag
                self.state.m15_rsi_avg_loss   = al
                self.state.m15_rsi_prev_close = float(closes[-1])
                arr = np.array(rsi_series, dtype=float)
                if len(arr) >= self._rsi_fast_p:
                    self.state.m15_rsi_ema_fast = ema_from_series(arr, self._rsi_fast_p)

        # MACD-A(8, 21, 5)
        ms_a = macd_seed(closes, self._m15_macd_a_fast, self._m15_macd_a_slow, self._m15_macd_a_sig)
        if ms_a:
            self.state.m15_macd_a_state  = ms_a
            self.state.m15_macd_a_line   = ms_a["macd_line"]
            self.state.m15_macd_a_signal = ms_a["signal_line"]

        # MACD-B(5, 11, 3)
        ms_b = macd_seed(closes, self._m15_macd_b_fast, self._m15_macd_b_slow, self._m15_macd_b_sig)
        if ms_b:
            self.state.m15_macd_b_state  = ms_b
            self.state.m15_macd_b_line   = ms_b["macd_line"]
            self.state.m15_macd_b_signal = ms_b["signal_line"]

        # Supertrend(11, 2.5)
        if n >= self._st_p + 2:
            st = supertrend_seed(highs, lows, closes, self._st_p, self._st_f)
            if st:
                self.state.m15_supertrend_state     = st
                self.state.m15_supertrend           = st["supertrend"]
                self.state.m15_supertrend_direction = st["direction"]
        else:
            logger.warning(
                "M15 builder has %d bars; need %d for Supertrend seed — "
                "Supertrend will build from live 15-min candles.",
                n, self._st_p + 2,
            )

        # Initialise live fields to anchored values so they're ready before first M1 close
        self._sync_m15_live_to_anchored()

    def update_m15(self, m15_builder: CandleBuilder) -> None:
        """O(1) update of M15 indicators on each closed 15-min candle."""
        bar = m15_builder.last_closed
        if bar is None:
            return

        self.state.m15_close = bar.close
        self.state.m15_high  = bar.high
        self.state.m15_low   = bar.low

        # RSI-14 + RSI-EMA8
        if (self.state.m15_rsi_avg_gain is not None
                and self.state.m15_rsi_avg_loss is not None
                and self.state.m15_rsi_prev_close is not None):
            rval, ag, al = rsi_step(
                self.state.m15_rsi_avg_gain, self.state.m15_rsi_avg_loss,
                bar.close, self.state.m15_rsi_prev_close, self._rsi_p,
            )
            self.state.m15_rsi            = rval
            self.state.m15_rsi_avg_gain   = ag
            self.state.m15_rsi_avg_loss   = al
            self.state.m15_rsi_prev_close = bar.close

            if self.state.m15_rsi_ema_fast is not None:
                self.state.m15_rsi_ema_fast = ema_step(
                    rval, self.state.m15_rsi_ema_fast, self._rsi_fast_p,
                )

        # MACD-A(8, 21, 5)
        if self.state.m15_macd_a_state is not None:
            ms = macd_step(
                self.state.m15_macd_a_state, bar.close,
                self._m15_macd_a_fast, self._m15_macd_a_slow, self._m15_macd_a_sig,
            )
            self.state.m15_macd_a_line   = ms["macd_line"]
            self.state.m15_macd_a_signal = ms["signal_line"]

        # MACD-B(5, 11, 3)
        if self.state.m15_macd_b_state is not None:
            ms = macd_step(
                self.state.m15_macd_b_state, bar.close,
                self._m15_macd_b_fast, self._m15_macd_b_slow, self._m15_macd_b_sig,
            )
            self.state.m15_macd_b_line   = ms["macd_line"]
            self.state.m15_macd_b_signal = ms["signal_line"]

        # Supertrend(11, 2.5)
        if self.state.m15_supertrend_state is not None:
            st = supertrend_step(
                self.state.m15_supertrend_state,
                bar.high, bar.low, bar.close,
                self._st_p, self._st_f,
            )
            self.state.m15_supertrend           = st["supertrend"]
            self.state.m15_supertrend_direction = st["direction"]

        # Reset live fields to anchored values on each M15 close
        self._sync_m15_live_to_anchored()

    def _sync_m15_live_to_anchored(self) -> None:
        """Copy anchored M15 values to live fields — called at seed and M15 close."""
        self.state.m15_rsi_live            = self.state.m15_rsi
        self.state.m15_rsi_ema_fast_live   = self.state.m15_rsi_ema_fast
        self.state.m15_macd_a_line_live    = self.state.m15_macd_a_line
        self.state.m15_macd_a_signal_live  = self.state.m15_macd_a_signal
        self.state.m15_macd_b_line_live    = self.state.m15_macd_b_line
        self.state.m15_macd_b_signal_live  = self.state.m15_macd_b_signal

    @staticmethod
    def _macd_step_readonly(state: dict, close: float, fast: int, slow: int, sig: int) -> tuple:
        """Compute one MACD step without mutating state. Returns (macd_line, signal_line)."""
        k_fast = 2.0 / (fast + 1)
        k_slow = 2.0 / (slow + 1)
        k_sig  = 2.0 / (sig  + 1)
        ef  = float(close) * k_fast + state["ema_fast"] * (1.0 - k_fast)
        es  = float(close) * k_slow + state["ema_slow"] * (1.0 - k_slow)
        ml  = ef - es
        sig_val = ml * k_sig + state["sig_ema"] * (1.0 - k_sig)
        return ml, sig_val

    def update_m15_live(self, m1_close: float) -> None:
        """Update live M15 RSI/MACD fields on every closed 1-min candle.

        Computes ONE step forward from the frozen anchored M15 state using the
        current M1 close price. Anchored state is never modified — it only
        updates at M15 candle close via update_m15(). Supertrend is not updated
        here (requires full H/L/C of a closed candle).
        """
        st = self.state

        # Live RSI — pure functional step from anchored, no mutation
        if (st.m15_rsi_avg_gain is not None
                and st.m15_rsi_avg_loss is not None
                and st.m15_rsi_prev_close is not None):
            rval, _, _ = rsi_step(
                st.m15_rsi_avg_gain, st.m15_rsi_avg_loss,
                m1_close, st.m15_rsi_prev_close, self._rsi_p,
            )
            st.m15_rsi_live = rval
            if st.m15_rsi_ema_fast is not None:
                st.m15_rsi_ema_fast_live = ema_step(rval, st.m15_rsi_ema_fast, self._rsi_fast_p)

        # Live MACD-A — read-only step, no mutation
        if st.m15_macd_a_state is not None:
            ml, sig = self._macd_step_readonly(
                st.m15_macd_a_state, m1_close,
                self._m15_macd_a_fast, self._m15_macd_a_slow, self._m15_macd_a_sig,
            )
            st.m15_macd_a_line_live   = ml
            st.m15_macd_a_signal_live = sig

        # Live MACD-B — read-only step, no mutation
        if st.m15_macd_b_state is not None:
            ml, sig = self._macd_step_readonly(
                st.m15_macd_b_state, m1_close,
                self._m15_macd_b_fast, self._m15_macd_b_slow, self._m15_macd_b_sig,
            )
            st.m15_macd_b_line_live   = ml
            st.m15_macd_b_signal_live = sig

        logger.debug(
            "M15 | close=%.2f RSI=%.1f EMA8=%.1f MACD-A=%.4f Sig-A=%.4f "
            "MACD-B=%.4f Sig-B=%.4f ST=%.2f dir=%s",
            self.state.m15_close or 0,
            self.state.m15_rsi or 0,
            self.state.m15_rsi_ema_fast or 0,
            self.state.m15_macd_a_line or 0,
            self.state.m15_macd_a_signal or 0,
            self.state.m15_macd_b_line or 0,
            self.state.m15_macd_b_signal or 0,
            self.state.m15_supertrend or 0,
            self.state.m15_supertrend_direction,
        )


# ---------------------------------------------------------------------------
# Rolling low window  (tracks M3 candle lows)
# ---------------------------------------------------------------------------

class RollingLowWindow:
    """Tracks the minimum low of the last ``n`` closed candles."""

    def __init__(self, n: int = 2) -> None:
        self._window: collections.deque = collections.deque(maxlen=n)
        self._n = n

    def update(self, low: float) -> None:
        self._window.append(low)

    @property
    def is_ready(self) -> bool:
        return len(self._window) >= self._n

    @property
    def min_low(self) -> Optional[float]:
        return float(min(self._window)) if self.is_ready else None

    @property
    def values(self) -> list[float]:
        return list(self._window)


class RollingHighWindow:
    """Tracks the maximum high of the last ``n`` closed candles."""

    def __init__(self, n: int = 2) -> None:
        self._window: collections.deque = collections.deque(maxlen=n)
        self._n = n

    def update(self, high: float) -> None:
        self._window.append(high)

    @property
    def is_ready(self) -> bool:
        return len(self._window) >= self._n

    @property
    def max_high(self) -> Optional[float]:
        return float(max(self._window)) if self.is_ready else None

    @property
    def values(self) -> list[float]:
        return list(self._window)


# ---------------------------------------------------------------------------
# Entry conditions
# ---------------------------------------------------------------------------

@dataclass
class EntryConditions:
    """
    All 5 entry conditions evaluated in one place.
    Both evaluate_entry() and the execution log method use this — no duplication.

    LONG entry — ALL must be true:
      1. M3-RSI14 > M3-RSI14-EMA21             (M3 RSI confirmation)
      2. abs(M3-MACD − M3-Signal) > 0.95       (histogram magnitude filter)
      Note: M3-Supertrend removed — already covered by base Group B (2-of-3 Supertrends).
            M3 momentum (MACD/RSI-EMA) removed — covered by base Group C.
            M1 RSI/Supertrend confirmation removed — covered by base Group B.

    SHORT entry — ALL must be true:
      1. M3-RSI14 < M3-RSI14-EMA21             (M3 RSI confirmation)
      2. abs(M3-MACD − M3-Signal) > 0.95       (histogram magnitude filter — same as LONG)
    """
    # Condition 1
    m3_supertrend_aligned:    bool = False   # M3-ST direction matches trade bias

    # Condition 2 (either sub-condition is enough)
    m3_macd_above_signal:     bool = False   # M3-MACD > Signal  (or < for SHORT)
    m3_rsi_ema_fast_above_slow: bool = False # M3-RSI-EMA8 > EMA21  (or < for SHORT)

    # Condition 3
    m3_rsi_above_ema:         bool = False   # M3-RSI14 > M3-RSI-EMA21  (or < for SHORT)

    # Condition 4 (either sub-condition is enough)
    m1_rsi_above_ema:         bool = False   # M1-RSI14 > M1-RSI-EMA21  (or < for SHORT)
    m1_supertrend_aligned:    bool = False   # M1-ST direction matches trade bias

    # Condition 5
    histogram_strong_enough:  bool = False   # abs(M3-MACD − Signal) > 0.95
    histogram_value:          Optional[float] = None

    # ── M15 base condition (must be met before C1–C3) ─────────────────────
    # Group A: M15 momentum — any 2 of 3 must be true
    m15_macd_a_above_signal:   bool = False   # M15-MACD-A(8,21,5) > Signal
    m15_rsi_above_ema8:        bool = False   # M15-RSI14 > M15-RSI-EMA8
    m15_macd_b_above_signal:   bool = False   # M15-MACD-B(5,11,3) > Signal
    # Group B: at least 2 Supertrends aligned — any 2 of 3 timeframes
    # (reuses m1_supertrend_aligned and m3_supertrend_aligned from C1/C4)
    m15_supertrend_aligned:    bool = False   # M15-ST aligned with bias
    # Group C: M3 alignment — any 2 of 3 must be true
    # (reuses m3_rsi_ema_fast_above_slow and m3_macd_above_signal from C2)
    m3_price_ema8_above_ema21: bool = False   # M3-EMA8(price) > M3-EMA21(price)

    # ── Derived ───────────────────────────────────────────────────────────
    @property
    def m3_momentum_confirmed(self) -> bool:
        return self.m3_macd_above_signal or self.m3_rsi_ema_fast_above_slow

    @property
    def m1_confirmed(self) -> bool:
        return self.m1_rsi_above_ema or self.m1_supertrend_aligned

    @property
    def base_group_a(self) -> bool:
        """M15 momentum — at least 2 of 3 sub-conditions must be true."""
        return sum([
            self.m15_macd_a_above_signal,
            self.m15_rsi_above_ema8,
            self.m15_macd_b_above_signal,
        ]) >= 2

    @property
    def base_group_b(self) -> bool:
        """At least 2 of 3 Supertrends aligned with the trade direction."""
        return sum([
            self.m15_supertrend_aligned,
            self.m3_supertrend_aligned,
            self.m1_supertrend_aligned,
        ]) >= 2

    @property
    def base_group_c(self) -> bool:
        """M3 bullish/bearish alignment — at least 2 of 3 sub-conditions must be true."""
        return sum([
            self.m3_price_ema8_above_ema21,
            self.m3_rsi_ema_fast_above_slow,
            self.m3_macd_above_signal,
        ]) >= 2

    @property
    def base_condition_met(self) -> bool:
        """ALL three base groups must pass before C1–C3 are evaluated."""
        return self.base_group_a and self.base_group_b and self.base_group_c

    @property
    def all_met(self) -> bool:
        return (
            self.base_condition_met          # Group A + B + C (each 2-of-3)
            and self.m3_rsi_above_ema        # C1 (M3-RSI14 vs EMA21)
            and self.histogram_strong_enough # C2 (histogram magnitude)
        )


def build_entry_conditions(ind: Coil3MinIndicatorState, direction: str) -> EntryConditions:
    """
    Compute all entry conditions for the given direction and return an EntryConditions instance.
    Called by evaluate_entry() and the execution log method — single source of truth.
    """
    ec      = EntryConditions()
    is_long = direction.upper() != "SHORT"
    st_bias = -1 if is_long else 1

    ec.m3_supertrend_aligned      = ind.m3_supertrend_direction == st_bias
    ec.m3_macd_above_signal       = _safe_cmp(ind.m3_macd_line,    ind.m3_macd_signal,  is_long)
    ec.m3_rsi_ema_fast_above_slow = _safe_cmp(ind.m3_rsi_ema_fast, ind.m3_rsi_ema_slow, is_long)
    ec.m3_rsi_above_ema           = _safe_cmp(ind.m3_rsi,          ind.m3_rsi_ema_slow, is_long)
    ec.m1_rsi_above_ema           = _safe_cmp(ind.m1_rsi,          ind.m1_rsi_ema_slow, is_long)
    ec.m1_supertrend_aligned      = ind.m1_supertrend_direction == st_bias

    if ind.m3_macd_line is not None and ind.m3_macd_signal is not None:
        ec.histogram_value         = ind.m3_macd_line - ind.m3_macd_signal
        ec.histogram_strong_enough = abs(ec.histogram_value) > 0.95

    # Base condition — Group A: M15 momentum (2-of-3)
    # Uses live fields (updated every M1 close) so Group A reflects current price,
    # not the frozen M15 candle close value from up to 15 minutes ago.
    ec.m15_macd_a_above_signal = _safe_cmp(ind.m15_macd_a_line_live, ind.m15_macd_a_signal_live, is_long)
    ec.m15_rsi_above_ema8      = _safe_cmp(ind.m15_rsi_live,         ind.m15_rsi_ema_fast_live,  is_long)
    ec.m15_macd_b_above_signal = _safe_cmp(ind.m15_macd_b_line_live, ind.m15_macd_b_signal_live, is_long)
    # Base condition — Group B: Supertrend (M15; M3/M1 reuse ec.m3_supertrend_aligned / ec.m1_supertrend_aligned)
    ec.m15_supertrend_aligned    = ind.m15_supertrend_direction == st_bias
    # Base condition — Group C: M3 price EMA alignment
    ec.m3_price_ema8_above_ema21 = _safe_cmp(ind.m3_ema8_price, ind.m3_ema21_price, is_long)

    return ec


def evaluate_entry(ind: Coil3MinIndicatorState, direction: str = "LONG") -> bool:
    """Return True when all 5 entry conditions are met. See EntryConditions for details."""
    if not ind.is_ready:
        return False
    return build_entry_conditions(ind, direction).all_met


# ---------------------------------------------------------------------------
# Exit condition evaluator
# ---------------------------------------------------------------------------

def evaluate_exit(
    ind:            Coil3MinIndicatorState,
    close:          float,
    m3_low_window:  RollingLowWindow,
    entry_price:    float,
    initial_sl_pts: float,
    step_trail:     "MultiPhaseTrailSL | PriceStepTrailSL",
    direction:      str = "LONG",
    m3_high_window: Optional["RollingHighWindow"] = None,
) -> Optional[str]:
    """
    Evaluate all exit conditions after each closed 1-min candle.
    Returns the first matching exit reason string, or None.

    LONG exit conditions (priority order):
      1. M3-RSI14 crosses below M3-RSI14-EMA21   (on M3 close — immediate)
      2. M3-MACD crosses below M3-Signal           (on M3 close — immediate)
      3. AT LEAST 3 of 4: M1-ST turns bearish(+1), M1-MACD-A(8,21,5) < Signal,
                          M1-MACD-B(21,34,8) < Signal, M1-RSI-EMA8 < M1-RSI-EMA21
         SUPPRESSED if M3 trend confirmed bullish:
           M3-RSI-EMA8 > M3-RSI-EMA21 AND M3-RSI > M3-RSI-EMA8 AND M3-MACD > M3-Signal
         *** TEMPORARILY DISABLED — observing exit behaviour ***
      4. M1-Close < min(last 2 M3-candle lows)    (TEMPORARILY DISABLED)
      5. M1-Close < entry_price - initial_sl_pts  (initial hard SL)
      6. MultiPhaseTrailSL hit

    SHORT exit conditions (priority order):
      1. M3-RSI14 crosses above M3-RSI14-EMA21   (on M3 close — immediate)
      2. M3-MACD crosses above M3-Signal           (on M3 close — immediate)
      3. AT LEAST 3 of 4: M1-ST turns bullish(-1), M1-MACD-A(8,21,5) > Signal,
                          M1-MACD-B(21,34,8) > Signal, M1-RSI-EMA8 > M1-RSI-EMA21
         SUPPRESSED if M3 trend confirmed bearish:
           M3-RSI-EMA8 < M3-RSI-EMA21 AND M3-RSI < M3-RSI-EMA8 AND M3-MACD < M3-Signal
         *** TEMPORARILY DISABLED — observing exit behaviour ***
      4. M1-Close > max(last 2 M3-candle highs)   (TEMPORARILY DISABLED)
      5. M1-Close > entry_price + initial_sl_pts  (initial hard SL)
      6. MultiPhaseTrailSL hit

    ``step_trail.update(close)`` is always called to keep trail state current.
    """
    # Always update step trail regardless of exit path
    step_trail.update(close)

    is_long = direction.upper() != "SHORT"

    if not ind.is_ready:
        # Before full indicator warm-up: only enforce initial hard SL
        if is_long and close < entry_price - initial_sl_pts:
            return EXIT_INITIAL_SL
        if not is_long and close > entry_price + initial_sl_pts:
            return EXIT_INITIAL_SL
        return None

    # 1. M3-RSI14 crossover (immediate — fires on M3 candle close)
    if is_long and ind.m3_rsi_crossed_below_ema21:
        return EXIT_RSI_CROSS_BELOW
    if not is_long and ind.m3_rsi_crossed_above_ema21:
        return EXIT_RSI_CROSS_BELOW

    # 2. M3-MACD crossover (immediate — fires on M3 candle close)
    if is_long and ind.m3_macd_crossed_below_signal:
        return EXIT_MACD_CROSS_BELOW
    if not is_long and ind.m3_macd_crossed_above_signal:
        return EXIT_MACD_CROSS_BELOW

    # 3. M1 momentum weakening — TEMPORARILY DISABLED (observing exit behaviour)
    #    Re-enable when ready:
    #
    #    Suppressed when M3 trend is strongly confirmed in trade direction:
    #      LONG suppress : M3-RSI-EMA8 > EMA21 AND M3-RSI > EMA8 AND M3-MACD > Signal
    #      SHORT suppress: M3-RSI-EMA8 < EMA21 AND M3-RSI < EMA8 AND M3-MACD < Signal
    #
    # m3_trend_confirmed = (
    #     _safe_cmp(ind.m3_rsi_ema_fast, ind.m3_rsi_ema_slow, is_long)
    #     and _safe_cmp(ind.m3_rsi,       ind.m3_rsi_ema_fast, is_long)
    #     and _safe_cmp(ind.m3_macd_line, ind.m3_macd_signal,  is_long)
    # )
    # if not m3_trend_confirmed:
    #     m1_weakness_count = sum([
    #         ind.m1_supertrend_direction == (1 if is_long else -1),
    #         _safe_cmp(ind.m1_macd_a_signal, ind.m1_macd_a_line, is_long),
    #         _safe_cmp(ind.m1_macd_b_signal, ind.m1_macd_b_line, is_long),
    #         _safe_cmp(ind.m1_rsi_ema_slow,  ind.m1_rsi_ema_fast, is_long),
    #     ])
    #     if m1_weakness_count >= 3:
    #         return EXIT_M1_MOMENTUM_BREAK

    # 4. Swing level break — TEMPORARILY DISABLED
    # if is_long:
    #     if m3_low_window.is_ready and m3_low_window.min_low is not None:
    #         if close < m3_low_window.min_low:
    #             return EXIT_SWING_LOW_BREAK
    # else:
    #     if m3_high_window is not None and m3_high_window.is_ready:
    #         if m3_high_window.max_high is not None and close > m3_high_window.max_high:
    #             return EXIT_SWING_LOW_BREAK

    # 5. Initial hard SL
    if is_long and close < entry_price - initial_sl_pts:
        return EXIT_INITIAL_SL
    if not is_long and close > entry_price + initial_sl_pts:
        return EXIT_INITIAL_SL

    # 6. Multi-phase trailing SL
    if step_trail.is_triggered(close):
        return EXIT_TRAILING_SL

    return None


# ---------------------------------------------------------------------------
# Re-entry condition evaluator  (after profit booking)
# ---------------------------------------------------------------------------

def evaluate_reentry(ind: Coil3MinIndicatorState, direction: str = "LONG") -> bool:
    """
    Return True when re-entry conditions are met after a profit booking.
    Evaluated on every closed 1-min candle.

    ALL conditions must be true simultaneously:

    LONG re-entry:
      0. M15 Group A met (2-of-3 M15 momentum)  [Groups B/C not required for re-entry]
      1. M3-EMA8(price) > M3-EMA21(price)          [M3 price EMAs bullish]
      2. M3-Close <= M3-EMA8(price)                 [pullback to 8-EMA from above]
      3. M3-Supertrend-Direction == -1              [M3 trend still bullish]
      4. M3-MACD > M3-Signal  OR  M3-RSI-EMA8 > M3-RSI-EMA21  [M3 momentum]
      5. M3-RSI14 > M3-RSI14-EMA21                 [M3 RSI confirmation]
      6. M1-EMA21(price) > M1-EMA34(price)          [1-min EMA filter]

    SHORT re-entry (all reversed):
      0. M15 Group A met (2-of-3 M15 momentum)
      1. M3-EMA8(price) < M3-EMA21(price)
      2. M3-Close >= M3-EMA8(price)                 [bounce up to 8-EMA from below]
      3. M3-Supertrend-Direction == +1
      4. M3-MACD < M3-Signal  OR  M3-RSI-EMA8 < M3-RSI-EMA21
      5. M3-RSI14 < M3-RSI14-EMA21
      6. M1-EMA21(price) < M1-EMA34(price)
    """
    if not ind.is_ready:
        return False

    if None in (ind.m3_ema8_price, ind.m3_ema21_price,
                ind.m3_close, ind.m1_ema21_price, ind.m1_ema34_price):
        return False

    is_long = direction.upper() != "SHORT"
    st_bias = -1 if is_long else 1

    # 0. M15 Group A must be met (2-of-3 M15 momentum; Groups B/C not required for re-entry)
    if not build_entry_conditions(ind, direction).base_group_a:
        return False

    # 1. M3 price EMA alignment: EMA8 > EMA21 (LONG) or EMA8 < EMA21 (SHORT)
    if not _safe_cmp(ind.m3_ema8_price, ind.m3_ema21_price, is_long):
        return False

    # 2. M3-Close pulled back to EMA8 from above (LONG: <=) or bounced to EMA8 from below (SHORT: >=)
    pullback_ok = (ind.m3_close <= ind.m3_ema8_price if is_long
                   else ind.m3_close >= ind.m3_ema8_price)
    if not pullback_ok:
        return False

    # 3. M3 Supertrend still aligned with trade direction
    if ind.m3_supertrend_direction != st_bias:
        return False

    # 4. M3 momentum — MACD or RSI-EMA cross (either is enough)
    macd_ok = _safe_cmp(ind.m3_macd_line,    ind.m3_macd_signal,  is_long)
    ema_ok  = _safe_cmp(ind.m3_rsi_ema_fast, ind.m3_rsi_ema_slow, is_long)
    if not (macd_ok or ema_ok):
        return False

    # 5. M3 RSI above its EMA21 (LONG) or below (SHORT)
    if not _safe_cmp(ind.m3_rsi, ind.m3_rsi_ema_slow, is_long):
        return False

    # 6. M1 price EMA filter: EMA21 > EMA34 (LONG) or EMA21 < EMA34 (SHORT)
    if not _safe_cmp(ind.m1_ema21_price, ind.m1_ema34_price, is_long):
        return False

    logger.debug(
        "evaluate_reentry[%s]: FIRED — M3[close=%.2f EMA8=%.2f EMA21=%.2f] "
        "M1[EMA21=%.2f EMA34=%.2f]",
        direction,
        ind.m3_close or 0, ind.m3_ema8_price or 0, ind.m3_ema21_price or 0,
        ind.m1_ema21_price or 0, ind.m1_ema34_price or 0,
    )
    return True
