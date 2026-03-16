"""
LTPService — single asyncio task that drives all real-time market data.

Flow:
  1. adapter.subscribe_ws(_on_tick)  →  ticks via WS callback
  2. _on_tick: update Redis LTP cache + publish ltp:ticks + feed CandleBuilder
  3. Staleness watchdog: if no tick for > ltp_stale_threshold_seconds → broadcast STALE_WARNING
  4. Fallback: if subscribe_ws raises, fall back to polling adapter.get_ltp()

Singleton ``ltp_service`` at module bottom.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Dict, Optional, Set

from adapters.adapter_factory import get_adapter
from core.config import settings
from services.ltp.candle_builder import CandleBuilder
from services.redis_service import redis_service
from ws.hub import hub

logger = logging.getLogger(__name__)


class LTPService:
    def __init__(self) -> None:
        self._candle_builder = CandleBuilder()
        self._running: bool = False
        self._tasks: list[asyncio.Task] = []
        self._last_tick_ts: Optional[datetime] = None
        self._ws_mode: bool = True          # False when falling back to polling
        self._poll_symbols: Set[str] = set()

    # ── Public interface ──────────────────────────────────────────────────────

    @property
    def is_connected(self) -> bool:
        return get_adapter().is_connected

    @property
    def candle_builder(self) -> CandleBuilder:
        return self._candle_builder

    async def start(self) -> None:
        adapter = get_adapter()
        await adapter.connect()
        self._running = True

        # Seed default F&O underlyings for WS subscription and poll fallback
        _default_symbols = ["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX", "MIDCPNIFTY"]
        for sym in _default_symbols:
            self._poll_symbols.add(sym)
        if hasattr(adapter, "set_symbols"):
            adapter.set_symbols(_default_symbols)

        try:
            await adapter.subscribe_ws(self._on_tick)
            self._ws_mode = True
            logger.info("[LTPService] WS subscription started")
        except Exception as exc:
            logger.warning("[LTPService] WS failed (%s) — switching to poll mode", exc)
            self._ws_mode = False
            self._tasks.append(asyncio.create_task(self._poll_loop()))

        # Staleness watchdog always runs
        self._tasks.append(asyncio.create_task(self._staleness_watchdog()))

    async def stop(self) -> None:
        self._running = False
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        adapter = get_adapter()
        await adapter.disconnect()
        logger.info("[LTPService] stopped")

    def add_poll_symbol(self, symbol: str) -> None:
        """Register a symbol for poll-mode fallback (also seeds mock adapter)."""
        self._poll_symbols.add(symbol)

    # ── Tick handler ──────────────────────────────────────────────────────────

    async def _on_tick(self, tick: Dict) -> None:
        symbol = tick.get("symbol")
        if not symbol:
            return

        try:
            ltp = float(tick["ltp"])
        except (KeyError, TypeError, ValueError):
            return

        self._last_tick_ts = datetime.now(timezone.utc)

        # Redis LTP cache + tick detail
        await redis_service.set_ltp(symbol, ltp)
        await redis_service.set_tick_detail(
            symbol,
            ltp,
            float(tick.get("change", 0.0)),
            tick.get("ts", self._last_tick_ts.isoformat()),
        )

        # Pub/Sub fan-out
        await redis_service.publish_tick(tick)

        # Candle builder
        ts_raw = tick.get("ts")
        try:
            ts = datetime.fromisoformat(ts_raw) if ts_raw else self._last_tick_ts
        except ValueError:
            ts = self._last_tick_ts

        await self._candle_builder.ingest(symbol, ltp, 0, ts)

        # Optional raw-tick archival
        if settings.store_ticks:
            await self._archive_tick(symbol, ltp, float(tick.get("change", 0.0)), ts)

    async def _archive_tick(
        self, symbol: str, ltp: float, change: float, ts: datetime
    ) -> None:
        from core.database import AsyncSessionLocal
        from models.timeseries import LTPTick

        try:
            async with AsyncSessionLocal() as session:
                session.add(
                    LTPTick(symbol=symbol, ltp=ltp, change=change, ts=ts)
                )
                await session.commit()
        except Exception as exc:
            logger.warning("[LTPService] tick archive error: %s", exc)

    # ── Staleness watchdog ────────────────────────────────────────────────────

    async def _staleness_watchdog(self) -> None:
        check_interval = max(1.0, settings.ltp_stale_threshold_seconds / 2)
        while self._running:
            await asyncio.sleep(check_interval)
            if self._last_tick_ts is None:
                continue
            age = (datetime.now(timezone.utc) - self._last_tick_ts).total_seconds()
            if age > settings.ltp_stale_threshold_seconds:
                logger.warning("[LTPService] stale — last tick %.1fs ago", age)
                await hub.broadcast(
                    json.dumps({"type": "STALE_WARNING", "age_seconds": round(age, 1)})
                )

    # ── Poll-mode fallback ────────────────────────────────────────────────────

    async def _poll_loop(self) -> None:
        """Used when WS subscription is unavailable.  Polls adapter.get_ltp()."""
        adapter = get_adapter()
        while self._running:
            for symbol in list(self._poll_symbols):
                try:
                    ltp = await adapter.get_ltp(symbol)
                    tick = {
                        "symbol": symbol,
                        "ltp":    ltp,
                        "change": 0.0,
                        "ts":     datetime.now(timezone.utc).isoformat(),
                    }
                    await self._on_tick(tick)
                except Exception as exc:
                    logger.warning("[LTPService] poll error %s: %s", symbol, exc)
            await asyncio.sleep(settings.ltp_poll_interval_seconds)


# ── Singleton ─────────────────────────────────────────────────────────────────
ltp_service = LTPService()
