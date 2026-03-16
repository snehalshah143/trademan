"""
CandleBuilder — converts raw LTP ticks into OHLCV candles for 5m / 15m / 75m.

Market hours: 09:15–15:30 IST.  First candle of the day always starts at 09:15.
Closed candles are persisted to DB via CandleRepository and delivered to registered
on-close callbacks (indicator engine will subscribe in Phase 5).
"""
import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from core.database import AsyncSessionLocal
from services.candle_repository import CandleRepository

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN  = time(9, 15)
MARKET_CLOSE = time(15, 30)

# Timeframe name → minutes
TIMEFRAMES: Dict[str, int] = {"5m": 5, "15m": 15, "75m": 75}


# ── Candle state ──────────────────────────────────────────────────────────────

@dataclass
class CandleState:
    symbol:    str
    timeframe: str
    ts:        datetime   # candle open timestamp (IST-aware)
    end_ts:    datetime   # exclusive close boundary
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    int = 0


# ── Boundary helpers ──────────────────────────────────────────────────────────

def _candle_start(ts: datetime, minutes: int) -> datetime:
    """Return the candle-open time that ``ts`` belongs to (IST)."""
    ts_ist = ts.astimezone(IST)
    market_open = ts_ist.replace(hour=9, minute=15, second=0, microsecond=0)
    delta_secs = (ts_ist - market_open).total_seconds()
    if delta_secs < 0:
        delta_secs = 0  # before market open — clamp to first candle
    candle_idx = int(delta_secs // (minutes * 60))
    return market_open + timedelta(minutes=candle_idx * minutes)


def _in_market_hours(ts: datetime) -> bool:
    t = ts.astimezone(IST).time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


# ── CandleBuilder ─────────────────────────────────────────────────────────────

class CandleBuilder:
    """
    Stateful candle assembler.  One instance shared for all symbols.

    Thread-safety: designed for single-threaded asyncio use only.
    """

    def __init__(self) -> None:
        # (symbol, timeframe) → live CandleState
        self._live: Dict[Tuple[str, str], CandleState] = {}
        self._callbacks: List[Callable[[CandleState], Awaitable[None]]] = []

    def register_on_close(
        self, callback: Callable[[CandleState], Awaitable[None]]
    ) -> None:
        """Register an async callback invoked whenever a candle closes."""
        self._callbacks.append(callback)

    async def ingest(
        self,
        symbol: str,
        ltp: float,
        volume: int,
        ts: datetime,
    ) -> None:
        """
        Process a single tick.  Updates all three timeframe candles.
        Closes and persists a candle when the period boundary is crossed.
        """
        if not _in_market_hours(ts):
            return

        for tf_name, tf_minutes in TIMEFRAMES.items():
            await self._update(symbol, tf_name, tf_minutes, ltp, volume, ts)

    async def _update(
        self,
        symbol: str,
        tf_name: str,
        tf_minutes: int,
        ltp: float,
        volume: int,
        ts: datetime,
    ) -> None:
        key = (symbol, tf_name)
        start = _candle_start(ts, tf_minutes)
        end   = start + timedelta(minutes=tf_minutes)

        existing = self._live.get(key)

        if existing is None:
            # First tick for this symbol+timeframe
            self._live[key] = CandleState(
                symbol=symbol, timeframe=tf_name,
                ts=start, end_ts=end,
                open=ltp, high=ltp, low=ltp, close=ltp, volume=volume,
            )
            return

        if ts >= existing.end_ts:
            # Candle period rolled over — close existing, open new
            closed = existing
            self._live[key] = CandleState(
                symbol=symbol, timeframe=tf_name,
                ts=start, end_ts=end,
                open=ltp, high=ltp, low=ltp, close=ltp, volume=volume,
            )
            await self._on_close(closed)
        else:
            # Update live candle
            existing.high   = max(existing.high, ltp)
            existing.low    = min(existing.low, ltp)
            existing.close  = ltp
            existing.volume += volume

    async def _on_close(self, candle: CandleState) -> None:
        """Persist closed candle to DB, then fire registered callbacks."""
        try:
            async with AsyncSessionLocal() as session:
                repo = CandleRepository(session)
                await repo.insert_candle(
                    symbol=candle.symbol,
                    timeframe=candle.timeframe,
                    ts=candle.ts,
                    o=candle.open,
                    h=candle.high,
                    l=candle.low,
                    c=candle.close,
                    vol=candle.volume,
                )
                await session.commit()
            logger.debug(
                "[CandleBuilder] closed %s %s  O=%s H=%s L=%s C=%s",
                candle.symbol, candle.timeframe,
                candle.open, candle.high, candle.low, candle.close,
            )
        except Exception as exc:
            logger.error("[CandleBuilder] persist error: %s", exc)

        for cb in self._callbacks:
            try:
                await cb(candle)
            except Exception as exc:
                logger.warning("[CandleBuilder] callback error: %s", exc)

    def get_live(self, symbol: str, timeframe: str) -> Optional[CandleState]:
        """Return the currently-open (unconfirmed) candle, or None."""
        return self._live.get((symbol, timeframe))
