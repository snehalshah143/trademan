"""
CandleBuilder — converts raw LTP ticks into OHLCV candles.

Supported timeframes:
  Intraday : 1m, 3m, 5m, 15m, 75m
  Calendar : 1d (session 09:15–15:30), 1w (Mon–Fri week), 1M (calendar month)

Market hours: 09:15–15:30 IST.  First candle of the day always starts at 09:15.
Closed candles are persisted to DB via CandleRepository and delivered to registered
on-close callbacks (indicator engine will subscribe in Phase 5).
"""
import logging
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Awaitable, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from core.database import AsyncSessionLocal
from services.candle_repository import CandleRepository

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN  = time(9, 15)
MARKET_CLOSE = time(15, 30)

# Minute-based intraday timeframes: name → period in minutes
_MINUTE_TFS: Dict[str, int] = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "75m": 75}

# All supported timeframes in iteration order
TIMEFRAMES: Tuple[str, ...] = ("1m", "3m", "5m", "15m", "75m", "1d", "1w", "1M")


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

def _candle_bounds(ts: datetime, tf_name: str) -> Tuple[datetime, datetime]:
    """Return (candle_open_ts, candle_end_ts) for the given tick and timeframe."""
    ts_ist = ts.astimezone(IST)

    if tf_name == "1d":
        # One candle per session: 09:15 → 15:30
        start = ts_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        end   = start + timedelta(minutes=375)

    elif tf_name == "1w":
        # Monday of the current calendar week at 09:15
        days_since_monday = ts_ist.weekday()          # Mon=0 … Sun=6
        start = (ts_ist - timedelta(days=days_since_monday)).replace(
            hour=9, minute=15, second=0, microsecond=0
        )
        end = start + timedelta(days=7)               # next Monday 09:15

    elif tf_name == "1M":
        # 1st of the current month at 09:15
        start = ts_ist.replace(day=1, hour=9, minute=15, second=0, microsecond=0)
        if ts_ist.month == 12:
            end = start.replace(year=ts_ist.year + 1, month=1)
        else:
            end = start.replace(month=ts_ist.month + 1)

    else:
        # Minute-based: 1m, 3m, 5m, 15m, 75m — all anchored to 09:15
        minutes = _MINUTE_TFS[tf_name]
        market_open = ts_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        delta_secs  = max((ts_ist - market_open).total_seconds(), 0.0)
        candle_idx  = int(delta_secs // (minutes * 60))
        start = market_open + timedelta(minutes=candle_idx * minutes)
        end   = start + timedelta(minutes=minutes)

    return start, end


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
        Process a single tick.  Updates all timeframe candles.
        Closes and persists a candle when the period boundary is crossed.
        """
        if not _in_market_hours(ts):
            return

        for tf_name in TIMEFRAMES:
            await self._update(symbol, tf_name, ltp, volume, ts)

    async def _update(
        self,
        symbol: str,
        tf_name: str,
        ltp: float,
        volume: int,
        ts: datetime,
    ) -> None:
        key = (symbol, tf_name)
        start, end = _candle_bounds(ts, tf_name)

        existing = self._live.get(key)

        if existing is None:
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
