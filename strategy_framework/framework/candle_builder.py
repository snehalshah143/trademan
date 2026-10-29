"""
candle_builder.py
-----------------
Aggregates real-time LTP ticks into completed 1-minute OHLCV candles.

Design
------
- ``tick(price, ts)`` is called on every LTP update (from the WebSocket callback).
  Returns a completed ``CandleBar`` when the current minute boundary is crossed.
  Returns ``None`` while the bar is still building.
- ``seed_from_history(bars)`` bootstraps the history list from a prior
  ``client.history()`` REST call so indicators have enough data before
  the first live candle closes.
- Pure Python/stdlib — no I/O, no threading.  The caller decides when to
  call ``tick()`` and what to do with closed bars.

Thread safety
-------------
``CandleBuilder`` is NOT thread-safe.  It should be called from a single
WebSocket-callback thread (or protected with a lock externally).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

import numpy as np

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class CandleBar:
    """A completed 1-minute OHLCV bar."""

    ts: datetime    # bar open-time (minute-truncated)
    open: float
    high: float
    low: float
    close: float
    volume: float   # tick count (or volume when available)

    def __repr__(self) -> str:
        return (
            f"CandleBar({self.ts.strftime('%H:%M')} "
            f"O={self.open:.2f} H={self.high:.2f} "
            f"L={self.low:.2f} C={self.close:.2f})"
        )


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------

class CandleBuilder:
    """
    Converts LTP ticks into OHLCV candles of any fixed-minute interval.

    Parameters
    ----------
    symbol         : Label used for log messages (e.g. ``"NIFTY"``).
    interval_minutes: Candle width in minutes (1 = 1m, 5 = 5m, 15 = 15m, etc.).
    max_history    : Maximum completed bars to retain in memory.  Default 500.
    """

    def __init__(self, symbol: str, interval_minutes: int = 1, max_history: int = 500) -> None:
        self.symbol = symbol
        self._interval = max(1, interval_minutes)
        self._max_history = max_history
        self._history: list[CandleBar] = []
        self._current: Optional[dict] = None    # live (open) bar accumulator

    # -----------------------------------------------------------------------
    # Warm-up
    # -----------------------------------------------------------------------

    def seed_from_history(self, bars: list[dict]) -> list[CandleBar]:
        """
        Seed history from a list of historical OHLCV bar dicts.

        Accepted dict keys (OpenAlgo history format):
            ``timestamp`` or ``ts`` or ``time`` (str or datetime)
            ``open``, ``high``, ``low``, ``close``, ``volume``

        Returns the parsed list of ``CandleBar`` objects (oldest first).
        """
        result: list[CandleBar] = []

        for b in bars:
            raw_ts = b.get("timestamp") or b.get("ts") or b.get("time")
            if isinstance(raw_ts, str):
                try:
                    parsed_ts: datetime = datetime.fromisoformat(raw_ts)
                except ValueError:
                    parsed_ts = datetime.now()
            elif isinstance(raw_ts, (int, float)):
                # Unix epoch
                parsed_ts = datetime.fromtimestamp(raw_ts)
            elif isinstance(raw_ts, datetime):
                parsed_ts = raw_ts
            else:
                parsed_ts = datetime.now()

            bar = CandleBar(
                ts=parsed_ts,
                open=float(b.get("open", 0) or 0),
                high=float(b.get("high", 0) or 0),
                low=float(b.get("low", 0) or 0),
                close=float(b.get("close", 0) or 0),
                volume=float(b.get("volume", 0) or 0),
            )
            result.append(bar)

        self._history = result[-self._max_history:]
        logger.info(
            "CandleBuilder[%s]: seeded %d historical bars (oldest=%s newest=%s)",
            self.symbol,
            len(self._history),
            self._history[0].ts.strftime("%Y-%m-%d %H:%M") if self._history else "–",
            self._history[-1].ts.strftime("%Y-%m-%d %H:%M") if self._history else "–",
        )
        return list(self._history)

    # -----------------------------------------------------------------------
    # Tick ingestion
    # -----------------------------------------------------------------------

    def tick(self, price: float, ts: Optional[datetime] = None) -> Optional[CandleBar]:
        """
        Process a single LTP tick.

        Parameters
        ----------
        price : Latest traded price.
        ts    : Tick timestamp (defaults to ``datetime.now()``).

        Returns
        -------
        A completed ``CandleBar`` when the minute boundary is crossed (the
        bar that JUST closed).  ``None`` while the current bar is still open.
        """
        if ts is None:
            ts = datetime.now()

        # Truncate to the N-minute boundary
        # e.g. interval=15: 09:23 → 09:15,  09:37 → 09:30
        aligned_minute = (ts.minute // self._interval) * self._interval
        bar_ts = ts.replace(minute=aligned_minute, second=0, microsecond=0)

        if self._current is None:
            # First tick — open the first bar
            self._current = _make_bar(bar_ts, price)
            return None

        if bar_ts == self._current["ts"]:
            # Same minute — update the live bar in-place
            _update_bar(self._current, price)
            return None

        # ── Minute boundary crossed: close the current bar ──────────────────
        closed = _finalize(self._current)
        self._history.append(closed)
        if len(self._history) > self._max_history:
            self._history.pop(0)

        logger.debug(
            "CandleBuilder[%s]: %s O=%.2f H=%.2f L=%.2f C=%.2f",
            self.symbol,
            closed.ts.strftime("%H:%M"),
            closed.open, closed.high, closed.low, closed.close,
        )

        # Open a new bar for the current minute
        self._current = _make_bar(bar_ts, price)
        return closed

    # -----------------------------------------------------------------------
    # Accessors (read-only views of history)
    # -----------------------------------------------------------------------

    @property
    def last_closed(self) -> Optional[CandleBar]:
        """Most recently completed bar, or ``None`` before any bar closes."""
        return self._history[-1] if self._history else None

    @property
    def closed_count(self) -> int:
        """Number of completed bars held in history."""
        return len(self._history)

    @property
    def live_bar(self) -> Optional[dict]:
        """Raw dict of the currently open (incomplete) bar, or ``None``."""
        return dict(self._current) if self._current else None

    def get_history(self, n: Optional[int] = None) -> list[CandleBar]:
        """
        Return the last *n* completed bars (all bars if *n* is ``None``).
        The list is a shallow copy — safe to iterate without holding a lock.
        """
        if n is None:
            return list(self._history)
        return list(self._history[-n:])

    # ── Numpy array helpers for indicator functions ──────────────────────────

    def closes(self, n: Optional[int] = None) -> np.ndarray:
        """Numpy array of close prices (oldest→newest)."""
        return np.array([b.close for b in self.get_history(n)], dtype=float)

    def highs(self, n: Optional[int] = None) -> np.ndarray:
        """Numpy array of high prices."""
        return np.array([b.high for b in self.get_history(n)], dtype=float)

    def lows(self, n: Optional[int] = None) -> np.ndarray:
        """Numpy array of low prices."""
        return np.array([b.low for b in self.get_history(n)], dtype=float)

    def opens(self, n: Optional[int] = None) -> np.ndarray:
        """Numpy array of open prices."""
        return np.array([b.open for b in self.get_history(n)], dtype=float)

    def volumes(self, n: Optional[int] = None) -> np.ndarray:
        """Numpy array of volume values."""
        return np.array([b.volume for b in self.get_history(n)], dtype=float)


# ---------------------------------------------------------------------------
# Private helpers (module-level for speed)
# ---------------------------------------------------------------------------

def _make_bar(ts: datetime, price: float) -> dict:
    return {"ts": ts, "open": price, "high": price, "low": price, "close": price, "volume": 1.0}


def _update_bar(bar: dict, price: float) -> None:
    if price > bar["high"]:
        bar["high"] = price
    if price < bar["low"]:
        bar["low"] = price
    bar["close"] = price
    bar["volume"] += 1.0


def _finalize(bar: dict) -> CandleBar:
    return CandleBar(
        ts=bar["ts"],
        open=bar["open"],
        high=bar["high"],
        low=bar["low"],
        close=bar["close"],
        volume=bar["volume"],
    )
