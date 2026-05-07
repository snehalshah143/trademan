"""
RedisService — all Redis operations for TRADEMAN.

Redis key schema (from CLAUDE.md):
  market:ltp                   HASH  {symbol: price}
  market:tick:{symbol}         HASH  {ltp, change, ts}
  ltp:ticks                    CHANNEL (pub/sub)
  indicator:{symbol}:{tf}      HASH  {indicator: value, ...}
  signal:{symbol}:state        STRING  BUY|NEUTRAL
  signal:{symbol}:cooldown     STRING  1  EX {seconds}
  strategy:{id}:mtm            STRING  {float}

Singleton ``redis_service`` is created at module load time.
Call ``await redis_service.connect()`` during application startup.
"""
import asyncio
import json
import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional

import redis.asyncio as aioredis

from core.config import settings

logger = logging.getLogger(__name__)


class RedisService:
    def __init__(self) -> None:
        self._client: Optional[aioredis.Redis] = None

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Connect to Redis.  Logs a warning (non-fatal) if Redis is unavailable."""
        try:
            self._client = aioredis.Redis.from_url(
                settings.redis_url,
                decode_responses=True,
                socket_connect_timeout=3,
            )
            await self._client.ping()
            logger.info("[RedisService] connected to %s", settings.redis_url)
        except Exception as exc:
            logger.warning(
                "[RedisService] Redis unavailable (%s) — running without cache", exc
            )
            self._client = None

    async def disconnect(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None
            logger.info("[RedisService] disconnected")

    @property
    def is_available(self) -> bool:
        return self._client is not None

    # ── Internal helper ───────────────────────────────────────────────────────

    async def _run(self, coro: Any) -> Any:
        """Execute a Redis coroutine; swallow + log errors, return None on failure."""
        if not self.is_available:
            return None
        try:
            return await coro
        except Exception as exc:
            logger.warning("[RedisService] error: %s", exc)
            return None

    # ── LTP cache ─────────────────────────────────────────────────────────────

    async def set_ltp(self, symbol: str, ltp: float) -> None:
        """Write a single LTP into the market:ltp hash."""
        await self._run(self._client.hset("market:ltp", symbol, str(ltp)))

    async def get_ltp(self, symbol: str) -> Optional[float]:
        """Read a single LTP from the market:ltp hash."""
        val = await self._run(self._client.hget("market:ltp", symbol))
        return float(val) if val is not None else None

    async def set_ltp_batch(self, ltp_map: Dict[str, float]) -> None:
        """Write many LTPs at once.  Uses hmset for Redis 3.x compat."""
        if not ltp_map:
            return
        # hmset works on Redis 3.x and 4.x+ (deprecated in 4+ but still present)
        await self._run(
            self._client.hmset("market:ltp", {k: str(v) for k, v in ltp_map.items()})
        )

    async def get_ltp_batch(self, symbols: List[str]) -> Dict[str, float]:
        """Read many LTPs in a single HMGET call.  Missing symbols are omitted."""
        if not symbols or not self.is_available:
            return {}
        try:
            values = await self._client.hmget("market:ltp", symbols)
            return {
                sym: float(val)
                for sym, val in zip(symbols, values)
                if val is not None
            }
        except Exception as exc:
            logger.warning("[RedisService] get_ltp_batch error: %s", exc)
            return {}

    async def set_tick_detail(self, symbol: str, ltp: float, change: float, ts: str) -> None:
        """Write per-symbol tick detail to market:tick:{symbol}.  Uses hmset for Redis 3.x compat."""
        await self._run(
            self._client.hmset(
                f"market:tick:{symbol}",
                {"ltp": str(ltp), "change": str(change), "ts": ts},
            )
        )

    # ── Pub/Sub ───────────────────────────────────────────────────────────────

    async def publish_tick(self, tick_data: Dict[str, Any]) -> None:
        """Publish a tick dict to the ltp:ticks channel."""
        await self._run(self._client.publish("ltp:ticks", json.dumps(tick_data)))

    async def subscribe_ticks(
        self,
        callback: Callable[[Dict[str, Any]], Awaitable[None]],
    ) -> Optional[asyncio.Task]:
        """
        Subscribe to ltp:ticks channel.  Launches a background asyncio.Task that
        reads messages and calls ``await callback(tick_dict)`` for each one.

        Returns the Task so the caller can cancel it on shutdown.
        Returns None if Redis is unavailable.
        """
        if not self.is_available:
            logger.warning("[RedisService] subscribe_ticks: Redis not available")
            return None

        async def _reader() -> None:
            pubsub = self._client.pubsub()
            await pubsub.subscribe("ltp:ticks")
            logger.info("[RedisService] subscribed to ltp:ticks")
            try:
                async for message in pubsub.listen():
                    if message.get("type") == "message":
                        try:
                            tick = json.loads(message["data"])
                            await callback(tick)
                        except Exception as exc:
                            logger.warning("[RedisService] tick dispatch error: %s", exc)
            except asyncio.CancelledError:
                pass
            finally:
                await pubsub.unsubscribe("ltp:ticks")
                await pubsub.aclose()

        return asyncio.create_task(_reader())

    # ── Indicator state ───────────────────────────────────────────────────────

    async def save_indicator_state(
        self, symbol: str, tf: str, state: Dict[str, Any]
    ) -> None:
        """Persist rolling indicator values for a symbol+timeframe.  hmset for Redis 3.x compat."""
        if not state:
            return
        await self._run(
            self._client.hmset(
                f"indicator:{symbol}:{tf}",
                {k: str(v) for k, v in state.items()},
            )
        )

    async def load_indicator_state(self, symbol: str, tf: str) -> Dict[str, str]:
        """Load indicator state dict.  Returns empty dict if not found."""
        result = await self._run(self._client.hgetall(f"indicator:{symbol}:{tf}"))
        return result or {}

    # ── Signal cooldown ───────────────────────────────────────────────────────

    async def set_cooldown(self, key: str, seconds: int) -> None:
        """Set a SETEX cooldown key.  ``key`` should include strategy/rule context."""
        if seconds <= 0:
            return
        await self._run(self._client.set(f"signal:{key}:cooldown", "1", ex=seconds))

    async def is_in_cooldown(self, key: str) -> bool:
        """Return True if the cooldown key still exists (has not expired)."""
        result = await self._run(self._client.exists(f"signal:{key}:cooldown"))
        return bool(result)

    # ── Symbol list cache ─────────────────────────────────────────────────────

    async def get_symbol_list(self, exchange: str) -> Optional[List[Dict[str, Any]]]:
        """Return cached symbol list for an exchange, or None if not cached."""
        raw = await self._run(self._client.get(f"symbols:list:{exchange}"))
        if raw is None:
            return None
        try:
            import json as _json
            return _json.loads(raw)
        except Exception:
            return None

    async def set_symbol_list(
        self, exchange: str, symbols: List[Dict[str, Any]], ttl: int = 86400
    ) -> None:
        """Cache symbol list for an exchange with a TTL (default 24 h)."""
        import json as _json
        await self._run(
            self._client.set(f"symbols:list:{exchange}", _json.dumps(symbols), ex=ttl)
        )

    # ── MTM hot cache ─────────────────────────────────────────────────────────

    async def set_strategy_mtm(self, strategy_id: str, mtm: float) -> None:
        await self._run(self._client.set(f"strategy:{strategy_id}:mtm", str(mtm)))

    async def get_strategy_mtm(self, strategy_id: str) -> Optional[float]:
        val = await self._run(self._client.get(f"strategy:{strategy_id}:mtm"))
        return float(val) if val is not None else None


# ── Singleton ─────────────────────────────────────────────────────────────────
redis_service = RedisService()
