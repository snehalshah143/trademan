"""
MockAdapter — simulates a broker for local development and testing.

Generates random price movements for a set of default symbols.
Orders are "filled" instantly at LTP ± small slippage.
"""
import asyncio
import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

from adapters.broker_adapter import BrokerAdapter
from core.config import settings

logger = logging.getLogger(__name__)

# ── Default seed prices for common F&O underlyings ────────────────────────────
_SEED_PRICES: Dict[str, float] = {
    "NIFTY":     22_500.0,
    "BANKNIFTY": 48_000.0,
    "FINNIFTY":  21_500.0,
}
_MAX_DELTA = 8.0          # max ± price movement per tick
_SLIPPAGE  = 0.002        # ± 0.2% fill slippage


class MockAdapter(BrokerAdapter):
    """Simulates a broker.  No real orders placed.  Safe for dev/CI."""

    def __init__(self) -> None:
        self._connected: bool = False
        self._prices: Dict[str, float] = dict(_SEED_PRICES)
        self._ws_task: Optional[asyncio.Task] = None
        self._mock_fills: Dict[str, Dict[str, Any]] = {}

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        self._connected = True
        logger.info("[MockAdapter] connected")

    async def disconnect(self) -> None:
        self._connected = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        logger.info("[MockAdapter] disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Market data ───────────────────────────────────────────────────────────

    async def get_ltp(self, symbol: str) -> float:
        if symbol not in self._prices:
            self._prices[symbol] = round(random.uniform(50.0, 500.0), 2)
        # Nudge price slightly on each poll
        delta = random.uniform(-_MAX_DELTA * 0.5, _MAX_DELTA * 0.5)
        self._prices[symbol] = max(0.05, round(self._prices[symbol] + delta, 2))
        return self._prices[symbol]

    async def get_ltp_bulk(self, symbols: List[str]) -> Dict[str, float]:
        return {s: await self.get_ltp(s) for s in symbols}

    async def subscribe_ws(
        self,
        callback: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        """Starts a background task that fires synthetic ticks every poll interval."""
        self._ws_task = asyncio.create_task(self._tick_loop(callback))
        logger.info("[MockAdapter] WS simulation started")

    async def _tick_loop(
        self,
        callback: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        while True:
            await asyncio.sleep(settings.ltp_poll_interval_seconds)
            for symbol in list(self._prices.keys()):
                delta = random.uniform(-_MAX_DELTA, _MAX_DELTA)
                new_price = max(0.05, round(self._prices[symbol] + delta, 2))
                self._prices[symbol] = new_price
                tick: Dict[str, Any] = {
                    "symbol": symbol,
                    "ltp":    new_price,
                    "change": round(delta, 2),
                    "ts":     datetime.now(timezone.utc).isoformat(),
                }
                try:
                    await callback(tick)
                except Exception as exc:
                    logger.warning("[MockAdapter] tick callback error: %s", exc)

    # ── Order management ──────────────────────────────────────────────────────

    async def place_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        order_type: str = "MARKET",
        price: Optional[float] = None,
        exchange: str = "NFO",
        product: str = "MIS",
    ) -> str:
        broker_order_id = f"MOCK-{uuid.uuid4().hex[:8].upper()}"
        ltp = await self.get_ltp(symbol)
        slippage = random.uniform(-_SLIPPAGE, _SLIPPAGE)
        fill_price = round(ltp * (1 + slippage), 2)
        self._mock_fills[broker_order_id] = {
            "status":       "filled",
            "filled_price": fill_price,
            "message":      "Mock fill",
        }
        logger.debug(
            "[MockAdapter] %s %s %s@%s → order %s filled at %s",
            action, quantity, symbol, order_type, broker_order_id, fill_price,
        )
        return broker_order_id

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        return self._mock_fills.get(
            order_id,
            {"status": "pending", "filled_price": None, "message": "Unknown order"},
        )

    # ── Account info ──────────────────────────────────────────────────────────

    async def get_positions(self) -> List[Dict[str, Any]]:
        """Return empty positions list — mock has no real account."""
        return []

    async def get_funds(self) -> Dict[str, Any]:
        """Return mock fund details."""
        return {"available": 1_000_000.0, "used": 0.0, "total": 1_000_000.0}
