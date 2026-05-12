"""
IndicatorCache — global in-memory store for computed indicator values.

KEY DESIGN DECISION:
  Indicators are GLOBAL, not per-strategy.
  If 100 strategies watch NIFTY M15 RSI(14):
    - RSI is computed ONCE by the NIFTY SymbolWorker
    - Stored here as "NIFTY:15m:RSI_14" → 58.3
    - Read by all 100 strategies with O(1) dict lookup
    - Zero duplicate computation

Key format:  "{symbol}:{timeframe}:{indicator}"
Examples:
  "NIFTY:15m:RSI_14"
  "NIFTY:15m:EMA_9"
  "NIFTY:15m:EMA_RSI_9_14"    ← EMA(9) of RSI(14)
  "NIFTY:15m:EMA_RSI_21_14"
  "NIFTY:15m:MACD_HIST"
  "NIFTY:15m:ATR_14"
  "NIFTY:15m:BB_UPPER_20"
  "NIFTY:15m:BB_LOWER_20"
  "NIFTY:15m:VWAP"
  "NIFTY:15m:ADX_14"
  "NIFTY:15m:STOCH_K_14"
  "NIFTY:15m:STOCH_D_14"

Access is single-threaded (asyncio event loop).  No locks required.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class IndicatorCache:
    """
    Global shared indicator value store.
    Written by SymbolWorkers on candle close.
    Read by EvaluationWorkers when building EvaluationContext.
    """

    def __init__(self) -> None:
        # current value
        self._values: dict[str, float] = {}
        # previous value — needed for CROSS_ABOVE / CROSS_BELOW
        self._prev:   dict[str, float] = {}

    # ── Write path (called by SymbolWorker) ──────────────────────────────────

    def update(self, symbol: str, timeframe: str, indicator: str,
               value: float) -> tuple[float, float] | None:
        """
        Store a new indicator value.
        Returns (prev_value, new_value) if the value changed, None if identical.
        """
        key = f"{symbol}:{timeframe}:{indicator}"
        prev = self._values.get(key)

        self._prev[key]   = prev if prev is not None else value
        self._values[key] = value

        # Suppress no-change events to avoid spurious condition evaluations
        if prev is not None and abs(prev - value) < 1e-9:
            return None

        return (self._prev[key], value)

    # ── Read path (called by EvaluationWorkers, O(1)) ────────────────────────

    def get(self, symbol: str, timeframe: str, indicator: str) -> float | None:
        return self._values.get(f"{symbol}:{timeframe}:{indicator}")

    def get_prev(self, symbol: str, timeframe: str, indicator: str) -> float | None:
        return self._prev.get(f"{symbol}:{timeframe}:{indicator}")

    def get_by_key(self, key: str) -> float | None:
        return self._values.get(key)

    def get_prev_by_key(self, key: str) -> float | None:
        return self._prev.get(key)

    def snapshot_for_symbol(self, symbol: str, timeframe: str) -> dict[str, float]:
        """
        Return all indicator values for (symbol, timeframe) as a flat dict.
        Used by EvaluationWorkers when building the full EvaluationContext.
        Key format in returned dict: "{INDICATOR}" (prefix stripped).
        e.g. {"RSI_14": 58.3, "EMA_9": 24300.5, ...}
        """
        prefix = f"{symbol}:{timeframe}:"
        return {
            k[len(prefix):]: v
            for k, v in self._values.items()
            if k.startswith(prefix)
        }

    def all_keys(self) -> list[str]:
        return list(self._values.keys())

    def __len__(self) -> int:
        return len(self._values)


# Singleton — shared across the entire process
indicator_cache = IndicatorCache()
