"""
CandleRepository — DB operations for candles and MTM snapshots.

Accepts an injected AsyncSession so the caller controls the transaction.
"""
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.timeseries import Candle, MTMSnapshot


class CandleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ── Candle operations ─────────────────────────────────────────────────────

    async def insert_candle(
        self,
        symbol: str,
        timeframe: str,
        ts: datetime,
        o: float,
        h: float,
        l: float,
        c: float,
        vol: int = 0,
    ) -> Candle:
        """Insert a single closed candle.  Caller commits."""
        candle = Candle(
            ts=ts,
            symbol=symbol,
            timeframe=timeframe,
            open=o,
            high=h,
            low=l,
            close=c,
            volume=vol,
        )
        self._session.add(candle)
        return candle

    async def bulk_insert_candles(self, candles: List[Dict]) -> None:
        """
        Bulk-insert a list of candle dicts (for warm-up seeding).

        Each dict must have: symbol, timeframe, ts, open, high, low, close, volume.
        """
        for c in candles:
            self._session.add(
                Candle(
                    ts=c["ts"],
                    symbol=c["symbol"],
                    timeframe=c["timeframe"],
                    open=c["open"],
                    high=c["high"],
                    low=c["low"],
                    close=c["close"],
                    volume=c.get("volume", 0),
                )
            )

    async def get_candles_for_warmup(
        self,
        symbol: str,
        timeframe: str,
        n_candles: int = 60,
    ) -> List[Candle]:
        """
        Return the most recent ``n_candles`` closed candles for a symbol+timeframe,
        ordered oldest-first (chronological) — suitable for indicator warm-up.
        """
        sub = (
            select(Candle)
            .where(Candle.symbol == symbol, Candle.timeframe == timeframe)
            .order_by(Candle.ts.desc())
            .limit(n_candles)
            .subquery()
        )
        result = await self._session.execute(
            select(Candle)
            .where(Candle.id.in_(select(sub.c.id)))
            .order_by(Candle.ts.asc())
        )
        return list(result.scalars().all())

    # ── MTM snapshot operations ───────────────────────────────────────────────

    async def insert_mtm_snapshot(
        self,
        strategy_id: uuid.UUID,
        mtm: float,
        pnl: float = 0.0,
        ts: Optional[datetime] = None,
    ) -> MTMSnapshot:
        """Insert a single MTM snapshot.  ``pnl`` is stored as both realized and unrealized
        (split is computed by the MTM tracker in Phase 3+)."""
        snap = MTMSnapshot(
            strategy_id=strategy_id,
            mtm=mtm,
            realized_pnl=pnl,
            unrealized_pnl=mtm - pnl,
        )
        if ts is not None:
            snap.ts = ts
        self._session.add(snap)
        return snap

    async def get_mtm_history(
        self,
        strategy_id: uuid.UUID,
        since: datetime,
    ) -> List[MTMSnapshot]:
        """Return all MTM snapshots for a strategy after ``since``, oldest-first."""
        result = await self._session.execute(
            select(MTMSnapshot)
            .where(
                MTMSnapshot.strategy_id == strategy_id,
                MTMSnapshot.ts >= since,
            )
            .order_by(MTMSnapshot.ts.asc())
        )
        return list(result.scalars().all())
