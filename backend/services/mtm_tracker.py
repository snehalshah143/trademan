"""
MTMTracker — background asyncio task that snapshots MTM every N seconds.

For each ACTIVE strategy:
  1. Load legs from DB
  2. Fetch current LTP for each leg symbol from Redis
  3. Compute MTM
  4. INSERT mtm_snapshots row
  5. SET strategy:{id}:mtm in Redis hot cache

Singleton ``mtm_tracker`` at module bottom.
"""
import asyncio
import logging
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from core.config import settings
from core.database import AsyncSessionLocal
from models.relational import Strategy, StrategyStatus
from services.candle_repository import CandleRepository
from services.redis_service import redis_service

logger = logging.getLogger(__name__)


class MTMTracker:
    def __init__(self) -> None:
        self._task: Optional[asyncio.Task] = None
        self._running: bool = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run())
        logger.info("[MTMTracker] started (interval=%.1fs)", settings.mtm_snapshot_interval_seconds)

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[MTMTracker] stopped")

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        while self._running:
            await asyncio.sleep(settings.mtm_snapshot_interval_seconds)
            try:
                await self._snapshot()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("[MTMTracker] snapshot error: %s", exc, exc_info=True)

    async def _snapshot(self) -> None:
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Strategy)
                .options(selectinload(Strategy.legs))
                .where(Strategy.status == StrategyStatus.ACTIVE)
            )
            strategies = result.scalars().all()

            if not strategies:
                return

            repo = CandleRepository(session)

            for strategy in strategies:
                mtm = await self._compute_mtm(strategy)
                await repo.insert_mtm_snapshot(strategy.id, mtm)
                await redis_service.set_strategy_mtm(str(strategy.id), mtm)
                logger.debug("[MTMTracker] %s MTM=%.2f", strategy.id, mtm)

            await session.commit()

    # ── MTM computation ───────────────────────────────────────────────────────

    @staticmethod
    async def _compute_mtm(strategy: Strategy) -> float:
        """Sum P&L across all filled legs using Redis LTP cache."""
        mtm = 0.0
        for leg in strategy.legs:
            if leg.entry_price is None:
                continue
            ltp = await redis_service.get_ltp(leg.symbol)
            if ltp is None:
                # No LTP in Redis — skip this leg (happens if Redis is down)
                continue
            if leg.action == "BUY":
                mtm += (ltp - leg.entry_price) * leg.quantity
            else:
                mtm += (leg.entry_price - ltp) * leg.quantity
        return round(mtm, 2)


# ── Singleton ─────────────────────────────────────────────────────────────────
mtm_tracker = MTMTracker()
