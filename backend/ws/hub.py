"""
WebSocket connection manager.
Maintains the set of active connections and broadcasts messages to all of them.
"""
import logging
from typing import Set

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections: Set[WebSocket] = set()

    async def connect(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections.add(websocket)
        logger.debug("WS client connected. Total: %d", len(self._connections))

    def disconnect(self, websocket: WebSocket) -> None:
        self._connections.discard(websocket)
        logger.debug("WS client disconnected. Total: %d", len(self._connections))

    async def broadcast(self, message: str) -> None:
        """Send a text message to every connected client. Dead connections are removed."""
        dead: list[WebSocket] = []
        for ws in list(self._connections):
            try:
                await ws.send_text(message)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    @property
    def connection_count(self) -> int:
        return len(self._connections)


# Module-level singleton — imported by endpoint.py and future services
hub = ConnectionManager()
