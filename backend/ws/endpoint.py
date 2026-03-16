"""
WebSocket route — /ws/market

On connect: sends a LTP_BATCH snapshot from the Redis cache so the client
gets current prices immediately (even after market close).
Subsequent ticks arrive via the Redis pub/sub → hub broadcast relay in main.py.
"""
import json
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ws.hub import hub

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/market")
async def market_websocket(websocket: WebSocket) -> None:
    await hub.connect(websocket)

    # Send cached LTP snapshot so the client gets prices immediately
    try:
        from services.redis_service import redis_service
        ltps = await redis_service._client.hgetall("market:ltp")
        if ltps:
            ticks = []
            for symbol, ltp_str in ltps.items():
                try:
                    detail = await redis_service._client.hgetall(f"market:tick:{symbol}")
                    ltp    = float(ltp_str)
                    change = float(detail.get("change", 0))
                    prev   = ltp - change
                    change_pct = round((change / prev) * 100, 4) if prev else 0.0
                    ticks.append({
                        "symbol":    symbol,
                        "ltp":       ltp,
                        "change":    change,
                        "changePct": change_pct,
                        "timestamp": detail.get("ts"),
                    })
                except Exception:
                    pass
            if ticks:
                await websocket.send_text(json.dumps({
                    "type":    "LTP_BATCH",
                    "payload": {"ticks": ticks},
                }))
    except Exception as exc:
        logger.warning("WS snapshot error: %s", exc)

    try:
        while True:
            data = await websocket.receive_text()
            logger.debug("WS recv: %s", data)
    except WebSocketDisconnect:
        hub.disconnect(websocket)
