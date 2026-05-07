"""
BrokerAdapter — abstract base class for all broker integrations.

Business logic must NEVER call OpenAlgo or any broker directly.
All market-data and order operations go through this interface.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Callable, Coroutine, Dict, List, Optional


@dataclass
class BrokerConfig:
    """Connection parameters for a broker adapter."""
    host:    str = "localhost:5000"
    ws_host: str = "localhost:8765"
    api_key: str = ""


class BrokerAdapter(ABC):
    """Abstract broker adapter.  Implement for each supported broker."""

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection (REST auth, WS handshake, etc.)."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Tear down connections gracefully."""

    @property
    @abstractmethod
    def is_connected(self) -> bool:
        """True when the adapter has a live, authenticated connection."""

    # ── Market data ───────────────────────────────────────────────────────────

    @abstractmethod
    async def get_ltp(self, symbol: str) -> float:
        """Fetch last traded price for a single symbol (REST poll)."""

    @abstractmethod
    async def get_ltp_bulk(self, symbols: List[str]) -> Dict[str, float]:
        """Fetch LTPs for many symbols in one call.  Returns {symbol: ltp}."""

    @abstractmethod
    async def subscribe_ws(
        self,
        callback: Callable[[Dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        """
        Start real-time tick streaming.
        Starts a background task; calls callback(tick) on each incoming tick.

        tick schema::

            {
                "symbol": "NIFTY",
                "ltp":    22500.50,
                "change": 12.50,
                "ts":     "2024-01-25T09:30:00+05:30"   # ISO-8601
            }

        Returns immediately — does NOT block.
        """

    # ── Order management ──────────────────────────────────────────────────────

    @abstractmethod
    async def place_order(
        self,
        symbol: str,
        action: str,                    # "BUY" | "SELL"
        quantity: int,
        order_type: str = "MARKET",     # "MARKET" | "LIMIT" | "SL" | "SL-M"
        price: Optional[float] = None,
        exchange: str = "NFO",
        product: str = "MIS",
    ) -> str:
        """
        Place an order.  Returns the broker's order-ID string.
        Raises on immediate rejection.
        """

    @abstractmethod
    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        """
        Fetch current order status.

        Returns dict with at minimum::

            {
                "status":       "open" | "filled" | "rejected" | "cancelled" | "pending",
                "filled_price": float | None,
                "message":      str
            }
        """

    # ── Account info ──────────────────────────────────────────────────────────

    @abstractmethod
    async def get_positions(self) -> List[Dict[str, Any]]:
        """
        Fetch open positions from the broker.

        Each dict has at minimum::

            {
                "symbol":    str,
                "exchange":  str,
                "qty":       int,   # positive = net long, negative = net short
                "buy_avg":   float,
                "sell_avg":  float,
                "pnl":       float,
                "product":   str,
            }
        """

    @abstractmethod
    async def get_funds(self) -> Dict[str, Any]:
        """
        Fetch account fund details.

        Returns::

            {
                "available": float,
                "used":      float,
                "total":     float,
            }
        """

    @abstractmethod
    async def search_symbols(self, query: str, exchange: str) -> List[Dict[str, Any]]:
        """
        Search tradable symbols in the broker's instrument master.

        Returns list of dicts::

            [{"symbol": str, "exchange": str}, ...]

        At minimum 2 characters required in query.
        """
