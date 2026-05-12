"""
RollingWindow — per-(symbol, timeframe) in-memory price state.

Design:
  - Pre-allocated NumPy ring buffers: no list growth, no GC pressure.
  - Incremental RSI state stored here (avg_gain, avg_loss) for O(1) updates.
  - EMA state stored per-period dict for O(1) updates.
  - RSI value series stored as deque for chained EMA(RSI) computation.
  - All access is single-threaded (asyncio event loop) — no locks.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

# Maximum candles to keep per window.
# 300 covers 5 days of 1m candles (375 per day) with room to spare.
WINDOW_CAPACITY = 300


@dataclass
class RollingWindow:
    """
    Holds the last WINDOW_CAPACITY candles for one (symbol, timeframe) pair.

    Ring-buffer layout:
      _pos  = next write index (wraps at WINDOW_CAPACITY)
      _count = how many slots are filled (caps at WINDOW_CAPACITY)

    closes() returns closes in chronological order (oldest → newest).
    """
    symbol:    str
    timeframe: str

    # OHLCV ring buffers — pre-allocated, never reallocated
    _closes:  np.ndarray = field(default_factory=lambda: np.zeros(WINDOW_CAPACITY, dtype=np.float64))
    _highs:   np.ndarray = field(default_factory=lambda: np.zeros(WINDOW_CAPACITY, dtype=np.float64))
    _lows:    np.ndarray = field(default_factory=lambda: np.zeros(WINDOW_CAPACITY, dtype=np.float64))
    _volumes: np.ndarray = field(default_factory=lambda: np.zeros(WINDOW_CAPACITY, dtype=np.float64))
    _pos:     int = 0
    _count:   int = 0

    # ── Incremental RSI(14) state ─────────────────────────────────────────────
    rsi_avg_gain: float = 0.0
    rsi_avg_loss: float = 0.0
    rsi_seeded:   bool  = False     # True once first 14 candles have been seen

    # RSI value ring (maxlen=50) — for EMA-of-RSI(14) calculation
    rsi_values: deque = field(default_factory=lambda: deque(maxlen=50))

    # ── Incremental EMA state per period ─────────────────────────────────────
    # period → current EMA value (None = not yet seeded)
    _ema_close: dict[int, float | None]     = field(default_factory=dict)
    _ema_rsi:   dict[int, float | None]     = field(default_factory=dict)

    # ── Session VWAP accumulators (reset each day) ────────────────────────────
    _vwap_pv:  float = 0.0     # Σ(price × volume)
    _vwap_vol: float = 0.0     # Σ(volume)

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def count(self) -> int:
        return self._count

    def append(self, close: float, high: float, low: float, volume: int) -> None:
        """Add one completed candle. O(1)."""
        idx = self._pos
        self._closes[idx]  = close
        self._highs[idx]   = high
        self._lows[idx]    = low
        self._volumes[idx] = volume
        self._pos   = (self._pos + 1) % WINDOW_CAPACITY
        self._count = min(self._count + 1, WINDOW_CAPACITY)
        # Accumulate VWAP
        self._vwap_pv  += close * volume
        self._vwap_vol += volume

    def reset_session(self) -> None:
        """Call at market open each day to reset session-based indicators."""
        self._vwap_pv  = 0.0
        self._vwap_vol = 0.0

    # ── Chronological array views (oldest → newest) ───────────────────────────

    def closes(self) -> np.ndarray:
        return self._ordered(self._closes)

    def highs(self) -> np.ndarray:
        return self._ordered(self._highs)

    def lows(self) -> np.ndarray:
        return self._ordered(self._lows)

    def volumes(self) -> np.ndarray:
        return self._ordered(self._volumes)

    def _ordered(self, arr: np.ndarray) -> np.ndarray:
        if self._count < WINDOW_CAPACITY:
            return arr[:self._count].copy()
        # Ring buffer: stitch from current write position
        return np.roll(arr, -self._pos)

    # ── Session VWAP ──────────────────────────────────────────────────────────

    @property
    def session_vwap(self) -> float | None:
        if self._vwap_vol < 1e-10:
            return None
        return self._vwap_pv / self._vwap_vol

    # ── EMA accessors ─────────────────────────────────────────────────────────

    def get_ema_close(self, period: int) -> float | None:
        return self._ema_close.get(period)

    def set_ema_close(self, period: int, value: float) -> None:
        self._ema_close[period] = value

    def get_ema_rsi(self, period: int) -> float | None:
        return self._ema_rsi.get(period)

    def set_ema_rsi(self, period: int, value: float) -> None:
        self._ema_rsi[period] = value
