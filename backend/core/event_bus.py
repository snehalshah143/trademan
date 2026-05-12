"""
Internal Event Bus — typed events that flow through the alert pipeline.

Event flow:
  TickEvent
    → CandleCloseEvent  (when a candle period boundary is crossed)
      → IndicatorUpdateEvent  (when an indicator value changes)
        → EvaluationRequest  (dispatched to the correct symbol worker)
          → AlertFiredEvent  (when a condition tree evaluates to True)
            → NotificationWorker  (persist + notify)

All events are frozen dataclasses (hashable, immutable, zero-copy safe).
All queues are asyncio.Queue — no threading, no locks.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from typing import FrozenSet


# ── Events ────────────────────────────────────────────────────────────────────

@dataclass(slots=True, frozen=True)
class TickEvent:
    symbol:    str
    ltp:       float
    change:    float
    volume:    int
    ts:        datetime


@dataclass(slots=True, frozen=True)
class CandleCloseEvent:
    symbol:    str
    timeframe: str      # "1m" | "5m" | "15m" | "75m" | "1d"
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    int
    ts:        datetime  # candle open timestamp (IST-aware)


@dataclass(slots=True, frozen=True)
class IndicatorUpdateEvent:
    """Emitted whenever a computed indicator value changes."""
    symbol:     str
    timeframe:  str
    indicator:  str      # canonical key e.g. "RSI_14", "EMA_9", "EMA_RSI_9_14"
    value:      float
    prev_value: float    # needed for CROSS_ABOVE / CROSS_BELOW edge detection


@dataclass(slots=True, frozen=True)
class EvaluationRequest:
    """Sent to a SymbolWorker's evaluation queue when a dependency changes."""
    strategy_id:   str
    trigger_key:   str          # what changed — e.g. "NIFTY:15m:RSI_14"
    trigger_value: float
    prev_value:    float
    priority:      int = 0      # 1 = price-driven (higher priority)


@dataclass(slots=True, frozen=True)
class AlertFiredEvent:
    alert_id:    str
    strategy_id: str
    rule_name:   str
    ctx:         dict            # EvaluationContext snapshot at fire time
    fired_at:    datetime


# ── Queue factory ─────────────────────────────────────────────────────────────

def make_queue(maxsize: int = 4096) -> asyncio.Queue:
    """
    Create a bounded asyncio queue.  Bounded queues provide backpressure:
    producers call put_nowait() and catch QueueFull — they never block the
    tick ingestion path.
    """
    return asyncio.Queue(maxsize=maxsize)
