"""
WebSocket route — /ws/market

Clients connect here to receive real-time market data (LTP, MTM) pushed by the backend.
In Phase 3 this handler will subscribe to the Redis pub/sub channel and relay ticks.
"""
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ws.hub import hub

logger = logging.getLogger(__name__)

router = APIRouter(tags=["websocket"])


@router.websocket("/ws/market")
async def market_websocket(websocket: WebSocket) -> None:
    await hub.connect(websocket)
    try:
        while True:
            # Keep the connection alive; accept client pings / control frames.
            # Phase 3 will replace this loop with a Redis subscription fan-out.
            data = await websocket.receive_text()
            logger.debug("WS recv: %s", data)
    except WebSocketDisconnect:
        hub.disconnect(websocket)
