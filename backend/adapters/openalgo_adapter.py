"""
OpenAlgoAdapter — full implementation of BrokerAdapter for OpenAlgo.

REST base:  http://{config.host}      (default port 5000)
WebSocket:  ws://{config.ws_host}     (default port 8765)

All errors are caught and returned as safe results — this adapter never raises
unhandled exceptions to callers.
"""
import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional

import httpx
import websockets

from adapters.broker_adapter import BrokerAdapter, BrokerConfig

logger = logging.getLogger(__name__)

# OpenAlgo order-status string → internal canonical status
_STATUS_MAP: Dict[str, str] = {
    "complete":   "filled",
    "filled":     "filled",
    "open":       "open",
    "pending":    "pending",
    "rejected":   "rejected",
    "cancelled":  "cancelled",
    "canceled":   "cancelled",
    "trigger pending": "open",
}

_RECONNECT_DELAY = 5.0      # seconds before WS reconnect attempt


class OpenAlgoAdapter(BrokerAdapter):
    """
    Connects to a running OpenAlgo instance.

    Instantiate via adapter_factory.get_adapter() — do not construct directly
    in business logic.
    """

    def __init__(self, config: Optional[BrokerConfig] = None) -> None:
        self._config = config or BrokerConfig()
        self._connected: bool = False
        self._client: Optional[httpx.AsyncClient] = None
        self._ws_task: Optional[asyncio.Task] = None
        self._ws_symbols: List[str] = []

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @staticmethod
    def _normalise_host(host: str) -> str:
        """Strip any scheme prefix so we can prepend the correct one."""
        for prefix in ("https://", "http://"):
            if host.lower().startswith(prefix):
                host = host[len(prefix):]
        return host.rstrip("/")

    @staticmethod
    def _normalise_ws_host(host: str) -> str:
        """Strip any ws/wss scheme prefix."""
        for prefix in ("wss://", "ws://"):
            if host.lower().startswith(prefix):
                host = host[len(prefix):]
        return host.rstrip("/")

    async def connect(self) -> None:
        """Create HTTP client and probe the OpenAlgo funds endpoint."""
        rest_host = self._normalise_host(self._config.host)
        self._client = httpx.AsyncClient(
            base_url=f"http://{rest_host}",
            timeout=httpx.Timeout(10.0, connect=5.0),
        )
        try:
            resp = await self._client.post(
                "/api/v1/funds",
                json={"apikey": self._config.api_key},
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("status") == "success":
                self._connected = True
                logger.info("[OpenAlgoAdapter] connected to %s", self._config.host)
            else:
                self._connected = False
                logger.warning(
                    "[OpenAlgoAdapter] connect probe failed: %s", data.get("message", resp.text)
                )
        except Exception as exc:
            self._connected = False
            logger.warning("[OpenAlgoAdapter] connect error: %s", exc)

    async def disconnect(self) -> None:
        self._connected = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._client:
            await self._client.aclose()
            self._client = None
        logger.info("[OpenAlgoAdapter] disconnected")

    @property
    def is_connected(self) -> bool:
        return self._connected

    # ── Symbols for WS subscription ───────────────────────────────────────────

    def set_symbols(self, symbols: List[str]) -> None:
        """Set the symbol list to subscribe to on the next WS connect."""
        self._ws_symbols = list(symbols)

    # ── Market data ───────────────────────────────────────────────────────────

    async def get_ltp(self, symbol: str) -> float:
        """Fetch LTP for a single symbol via REST /api/v1/quotes."""
        result = await self.get_ltp_bulk([symbol])
        return result.get(symbol, 0.0)

    async def get_ltp_bulk(self, symbols: List[str]) -> Dict[str, float]:
        """Fetch LTP for multiple symbols.  Returns {symbol: ltp}."""
        out: Dict[str, float] = {}
        if not self._client:
            return out
        for symbol in symbols:
            try:
                resp = await self._client.post(
                    "/api/v1/quotes",
                    json={"apikey": self._config.api_key, "symbol": symbol, "exchange": "NFO"},
                )
                data = resp.json()
                if data.get("status") == "success":
                    ltp = float(data.get("data", {}).get("ltp", 0))
                    out[symbol] = ltp
            except Exception as exc:
                logger.warning("[OpenAlgoAdapter] get_ltp error for %s: %s", symbol, exc)
        return out

    async def subscribe_ws(
        self,
        callback: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]],
        symbols: Optional[List[str]] = None,
    ) -> None:
        """
        Start WS streaming.  Spawns a background reconnect loop.
        Optionally accepts a symbols list to override self._ws_symbols.
        """
        if symbols:
            self._ws_symbols = list(symbols)
        self._ws_task = asyncio.create_task(self._ws_loop(callback))
        logger.info("[OpenAlgoAdapter] WS task started, symbols=%s", self._ws_symbols)

    async def _ws_loop(
        self,
        callback: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        """Persistent reconnect loop for the OpenAlgo WebSocket."""
        uri = f"ws://{self._normalise_ws_host(self._config.ws_host)}"
        while True:
            try:
                async with websockets.connect(uri, ping_interval=20) as ws:
                    self._connected = True
                    logger.info("[OpenAlgoAdapter] WS connected to %s", uri)

                    # Send subscription message
                    if self._ws_symbols:
                        await ws.send(json.dumps({
                            "action":  "subscribe",
                            "symbols": self._ws_symbols,
                            "mode":    "ltp",
                        }))

                    async for raw in ws:
                        try:
                            data = json.loads(raw)
                            tick = self._normalise_tick(data)
                            if tick:
                                await callback(tick)
                        except Exception as exc:
                            logger.warning("[OpenAlgoAdapter] tick parse error: %s", exc)

            except asyncio.CancelledError:
                logger.info("[OpenAlgoAdapter] WS task cancelled")
                return
            except Exception as exc:
                self._connected = False
                logger.warning(
                    "[OpenAlgoAdapter] WS disconnected (%s) — reconnecting in %.0fs",
                    exc, _RECONNECT_DELAY,
                )
                await asyncio.sleep(_RECONNECT_DELAY)

    @staticmethod
    def _normalise_tick(data: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Map OpenAlgo WS tick to our internal format.  Returns None if not a tick."""
        symbol = data.get("symbol") or data.get("tk")
        if not symbol:
            return None
        ltp_raw = data.get("ltp") or data.get("last_price") or data.get("lp")
        if ltp_raw is None:
            return None
        try:
            ltp = float(ltp_raw)
        except (TypeError, ValueError):
            return None
        return {
            "symbol": symbol,
            "ltp":    ltp,
            "change": float(data.get("change", 0.0) or 0.0),
            "ts":     data.get("ts") or datetime.now(timezone.utc).isoformat(),
        }

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
        """POST /api/v1/placeorder.  Returns broker order ID."""
        if not self._client:
            raise RuntimeError("OpenAlgoAdapter not connected")

        body = {
            "apikey":             self._config.api_key,
            "strategy":           "TRADEMAN",
            "symbol":             symbol,
            "action":             action.upper(),
            "exchange":           exchange,
            "pricetype":          order_type.upper(),
            "product":            product.upper(),
            "quantity":           str(quantity),
            "price":              str(price or 0),
            "trigger_price":      "0",
            "disclosed_quantity": "0",
        }
        try:
            resp = await self._client.post("/api/v1/placeorder", json=body)
            data = resp.json()
            if data.get("status") == "success":
                return str(data["orderid"])
            raise RuntimeError(data.get("message", "Unknown placeorder error"))
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(f"placeorder HTTP error: {exc}") from exc

    async def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """POST /api/v1/cancelorder.  Returns {success, message}."""
        if not self._client:
            return {"success": False, "error": "Not connected"}
        try:
            resp = await self._client.post(
                "/api/v1/cancelorder",
                json={"apikey": self._config.api_key, "strategy": "TRADEMAN", "orderid": order_id},
            )
            data = resp.json()
            return {
                "success": data.get("status") == "success",
                "message": data.get("message", ""),
            }
        except Exception as exc:
            logger.warning("[OpenAlgoAdapter] cancelorder error: %s", exc)
            return {"success": False, "error": str(exc)}

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """POST /api/v1/orderstatus.  Maps to our canonical status dict."""
        if not self._client:
            return {"status": "pending", "filled_price": None, "message": "Not connected"}
        try:
            resp = await self._client.post(
                "/api/v1/orderstatus",
                json={
                    "apikey":    self._config.api_key,
                    "strategy":  "TRADEMAN",
                    "orderid":   order_id,
                },
            )
            data = resp.json()
            if data.get("status") != "success":
                return {
                    "status":       "pending",
                    "filled_price": None,
                    "message":      data.get("message", ""),
                }
            order_data = data.get("data", {})
            raw_status   = str(order_data.get("status", "")).lower()
            filled_price = order_data.get("price") or order_data.get("filled_price")
            return {
                "status":       _STATUS_MAP.get(raw_status, raw_status),
                "filled_price": float(filled_price) if filled_price else None,
                "message":      order_data.get("remarks", ""),
            }
        except Exception as exc:
            logger.warning("[OpenAlgoAdapter] orderstatus error: %s", exc)
            return {"status": "pending", "filled_price": None, "message": str(exc)}

    # ── Account info ──────────────────────────────────────────────────────────

    async def get_positions(self) -> List[Dict[str, Any]]:
        """POST /api/v1/positionbook.  Returns normalised position list."""
        if not self._client:
            return []
        try:
            resp = await self._client.post(
                "/api/v1/positionbook",
                json={"apikey": self._config.api_key},
            )
            data = resp.json()
            if data.get("status") != "success":
                return []
            positions = []
            for p in (data.get("data") or []):
                qty = int(p.get("netqty", 0) or 0)
                if qty == 0:
                    continue
                positions.append({
                    "symbol":   p.get("symbol", ""),
                    "exchange": p.get("exchange", "NFO"),
                    "qty":      qty,
                    "buy_avg":  float(p.get("buyprice", 0) or 0),
                    "sell_avg": float(p.get("sellprice", 0) or 0),
                    "pnl":      float(p.get("pnl", 0) or 0),
                    "product":  p.get("product", "MIS"),
                })
            return positions
        except Exception as exc:
            logger.warning("[OpenAlgoAdapter] positionbook error: %s", exc)
            return []

    async def get_funds(self) -> Dict[str, Any]:
        """POST /api/v1/funds.  Returns {available, used, total}."""
        if not self._client:
            return {"available": 0.0, "used": 0.0, "total": 0.0}
        try:
            resp = await self._client.post(
                "/api/v1/funds",
                json={"apikey": self._config.api_key},
            )
            data = resp.json()
            if data.get("status") != "success":
                return {"available": 0.0, "used": 0.0, "total": 0.0}
            d = data.get("data", {})
            available = float(d.get("availablecash", 0) or 0)
            used      = float(d.get("usedmargin", 0) or 0)
            total     = float(d.get("totalbalance", 0) or available + used)
            return {"available": available, "used": used, "total": total}
        except Exception as exc:
            logger.warning("[OpenAlgoAdapter] funds error: %s", exc)
            return {"available": 0.0, "used": 0.0, "total": 0.0}
