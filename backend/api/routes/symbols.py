"""
Symbol endpoints.

GET  /api/v1/symbols/search?q=NIFTY&exchange=NFO
  → proxies to adapter.search_symbols()

POST /api/v1/symbols/subscribe
  → registers symbols with LTPService for real-time tracking
  → in WS mode: subscribes them to OpenAlgo WS live
  → in mock mode: seeds prices so tick loop starts emitting them
"""
from typing import List

from fastapi import APIRouter, Query
from pydantic import BaseModel

from adapters.adapter_factory import get_adapter

router = APIRouter(prefix="/symbols", tags=["symbols"])


class SymbolResult(BaseModel):
    symbol:   str
    exchange: str


class SubscribeRequest(BaseModel):
    symbols: List[str]


@router.get("/list", response_model=List[SymbolResult])
async def list_symbols(exchange: str = Query(default="NFO")):
    """
    Return the symbol list for an exchange.
    Served from Redis cache (warm on startup).  Falls back to live adapter fetch
    and re-populates the cache on a cache miss.
    """
    from services.redis_service import redis_service

    exchange = exchange.strip().upper()

    # 1. Try Redis cache first (warm from startup task)
    cached = await redis_service.get_symbol_list(exchange)
    if cached:
        return [SymbolResult(symbol=r["symbol"], exchange=r["exchange"]) for r in cached]

    # 2. Cache miss — fetch from adapter, populate cache, return
    adapter = get_adapter()
    if hasattr(adapter, "list_symbols"):
        results = await adapter.list_symbols(exchange)
    else:
        results = await adapter.search_symbols("", exchange)

    if results:
        await redis_service.set_symbol_list(exchange, results)

    return [SymbolResult(symbol=r["symbol"], exchange=r["exchange"]) for r in results]


@router.get("/search", response_model=List[SymbolResult])
async def search_symbols(
    q:        str = Query(default="", min_length=0),
    exchange: str = Query(default="NFO"),
):
    """
    Search for tradable symbols matching ``q`` on ``exchange``.
    Returns an empty list if ``q`` is fewer than 2 characters.
    """
    if len(q.strip()) < 2:
        return []
    adapter = get_adapter()
    results = await adapter.search_symbols(q.strip().upper(), exchange.upper())
    return [SymbolResult(symbol=r["symbol"], exchange=r["exchange"]) for r in results]


@router.post("/subscribe")
async def subscribe_symbols(body: SubscribeRequest):
    """
    Register symbols with LTPService for real-time tracking.

    Call this on app startup with all strategy leg symbols so the
    WebSocket subscription and candle builder cover those instruments.
    """
    from services.ltp.ltp_service import ltp_service
    symbols = [s.strip().upper() for s in body.symbols if s.strip()]
    if not symbols:
        return {"subscribed": 0}
    await ltp_service.add_symbols(symbols)
    return {"subscribed": len(symbols)}
