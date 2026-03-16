"""
Quote proxy — GET /api/quote?symbol=NIFTY&exchange=NSE_INDEX

Proxies to OpenAlgo /api/v1/quotes with the stored API key.
Computes day's change and changePct from previous close.
"""
import logging

import httpx
from fastapi import APIRouter, HTTPException, Query

from core.config import settings

logger = logging.getLogger(__name__)

router = APIRouter(tags=["quotes"])


@router.get("/quote")
async def get_quote(
    symbol: str = Query(..., description="Instrument symbol, e.g. NIFTY"),
    exchange: str = Query("NSE_INDEX", description="NSE_INDEX | BSE_INDEX | NFO | BSE"),
):
    """
    Proxy to OpenAlgo quotes API.
    Returns ltp, prev_close, change (ltp - prev_close), changePct.
    """
    host = settings.openalgo_host.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            resp = await client.post(
                f"{host}/api/v1/quotes",
                json={
                    "apikey":   settings.openalgo_api_key,
                    "symbol":   symbol,
                    "exchange": exchange,
                },
            )
            data = resp.json()
    except httpx.ConnectError:
        raise HTTPException(status_code=503, detail="OpenAlgo unreachable")
    except Exception as exc:
        logger.warning("[quotes] %s/%s error: %s", symbol, exchange, exc)
        raise HTTPException(status_code=503, detail=str(exc))

    if data.get("status") != "success":
        raise HTTPException(
            status_code=404,
            detail=data.get("message") or "Quote not available",
        )

    d          = data.get("data", {})
    ltp        = float(d.get("ltp", 0) or 0)
    prev_close = float(d.get("close", 0) or d.get("prev_close", 0) or 0)
    change     = round(ltp - prev_close, 2) if prev_close else 0.0
    change_pct = round((change / prev_close) * 100, 2) if prev_close else 0.0

    return {
        "symbol":     symbol,
        "exchange":   exchange,
        "ltp":        ltp,
        "prev_close": prev_close,
        "change":     change,
        "changePct":  change_pct,
        "open":       float(d.get("open", 0) or 0),
        "high":       float(d.get("high", 0) or 0),
        "low":        float(d.get("low", 0) or 0),
    }
