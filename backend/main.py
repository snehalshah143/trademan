"""
TRADEMAN backend entry point.

Lifespan order:
  startup:  init_db → redis connect → ltp start → mtm start → redis→WS bridge
  shutdown: mtm stop → ltp stop → redis disconnect
"""
import asyncio
import json
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings
from core.database import init_db

logger = logging.getLogger(__name__)

# ── Background Redis → WS broadcaster ─────────────────────────────────────────

_redis_ws_task: asyncio.Task | None = None


async def _start_redis_ws_bridge() -> None:
    """Subscribe to ltp:ticks pub/sub and broadcast every message to WS clients."""
    from services.redis_service import redis_service
    from ws.hub import hub

    async def _relay(tick: dict) -> None:
        await hub.broadcast(json.dumps(tick))

    global _redis_ws_task
    _redis_ws_task = await redis_service.subscribe_ticks(_relay)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ── Startup ───────────────────────────────────────────────────────────────
    logger.info("[TRADEMAN] starting — adapter=%s debug=%s", settings.broker_adapter, settings.debug)
    print(f"[TRADEMAN] Starting up — adapter={settings.broker_adapter}, debug={settings.debug}")

    await init_db()

    from services.redis_service import redis_service
    await redis_service.connect()

    from services.ltp.ltp_service import ltp_service
    await ltp_service.start()

    from services.mtm_tracker import mtm_tracker
    await mtm_tracker.start()

    await _start_redis_ws_bridge()

    # ── Instrument sync (non-blocking background task) ─────────────────────
    async def _run_instrument_sync() -> None:
        try:
            from core.database import AsyncSessionLocal
            from services.instrument_sync_service import instrument_sync_service
            async with AsyncSessionLocal() as db:
                result = await instrument_sync_service.sync_all(db)
                logger.info("[TRADEMAN] Instrument sync complete: %s", result)
        except Exception as exc:
            logger.warning("[TRADEMAN] Instrument sync failed (non-fatal): %s", exc)

    import asyncio as _asyncio
    _asyncio.create_task(_run_instrument_sync())

    yield

    # ── Shutdown ──────────────────────────────────────────────────────────────
    logger.info("[TRADEMAN] shutting down")
    print("[TRADEMAN] Shutting down")

    from services.mtm_tracker import mtm_tracker as _mtm
    await _mtm.stop()

    from services.ltp.ltp_service import ltp_service as _ltp
    await _ltp.stop()

    if _redis_ws_task and not _redis_ws_task.done():
        _redis_ws_task.cancel()
        try:
            await _redis_ws_task
        except asyncio.CancelledError:
            pass

    from services.redis_service import redis_service as _redis
    await _redis.disconnect()


# ── App ───────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="TRADEMAN API",
    description="F&O Strategy and Position Management Platform",
    version="0.3.0",
    debug=settings.debug,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
from api.routes import strategies, alerts, positions, orders, instruments
from api.routes import settings as settings_router
from ws.endpoint import router as ws_router

app.include_router(strategies.router,       prefix="/api/v1")
app.include_router(alerts.router,           prefix="/api/v1")
app.include_router(positions.router,        prefix="/api/v1")
app.include_router(orders.router,           prefix="/api/v1")
app.include_router(settings_router.router,  prefix="/api/v1")
app.include_router(instruments.router,      prefix="/api")
app.include_router(ws_router)


# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
@app.get("/api/health", tags=["system"])
async def health():
    """Health check — reports adapter connection status and Redis availability."""
    from adapters.adapter_factory import get_adapter
    from services.ltp.ltp_service import ltp_service
    from services.redis_service import redis_service

    return {
        "status":           "ok",
        "broker_connected": ltp_service.is_connected,
        "adapter":          settings.broker_adapter,
        "redis":            redis_service.is_available,
    }
