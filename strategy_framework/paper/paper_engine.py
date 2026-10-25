"""
paper_engine.py
---------------
Virtual trading engine for PAPER mode.

Responsibility
--------------
- Accept order requests and immediately convert them to virtual positions.
- Maintain a virtual order book, position book, and trade book.
- Calculate MTM using live market prices (updated from StrategyContext each cycle).
- Never send any order to OpenAlgo / broker.
- Produce logs identical in structure to LIVE mode (prefixed with [PAPER]).
- Survive restarts via in-memory state (no broker to reconstruct from, so paper
  state is held in memory for the session; a JSON snapshot is written after each
  order for persistence across restarts).

All paper orders are filled *instantly* at the LTP from the option chain.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from ..utils.logger import PaperLogAdapter, get_logger
from ..utils.helper import now_ist, safe_float

_raw_logger = get_logger(__name__)
logger = PaperLogAdapter(_raw_logger, {})

# Path where paper state is persisted across restarts
_DEFAULT_SNAPSHOT_PATH = "logs/paper_state.json"


@dataclass
class VirtualOrder:
    """A simulated order that was immediately filled at LTP."""
    order_id: str
    strategy: str
    symbol: str
    exchange: str
    action: str
    quantity: int
    fill_price: float
    product: str
    timestamp: datetime = field(default_factory=now_ist)
    status: str = "complete"


@dataclass
class VirtualPosition:
    """Aggregate position for one symbol in paper mode."""
    symbol: str
    exchange: str
    product: str
    quantity: int             # positive = long, negative = short
    average_price: float
    current_ltp: float = 0.0

    @property
    def unrealized_pnl(self) -> float:
        return (self.current_ltp - self.average_price) * self.quantity


class PaperEngine:
    """
    Virtual execution engine — mirrors LIVE behaviour without broker calls.

    Parameters
    ----------
    snapshot_path : JSON file where paper state is persisted.
    """

    def __init__(self, snapshot_path: str = _DEFAULT_SNAPSHOT_PATH) -> None:
        self._snapshot_path = Path(snapshot_path)
        self._orders: list[VirtualOrder] = []
        self._positions: dict[str, VirtualPosition] = {}   # symbol → position

        # MTM tracking
        self._session_start_value: Optional[float] = None
        self._max_mtm: float = 0.0
        self._min_mtm: float = 0.0

        self._load_snapshot()

    # ---------------------------------------------------------------------------
    # Order execution
    # ---------------------------------------------------------------------------

    def place_order(
        self,
        strategy: str,
        symbol: str,
        action: str,
        exchange: str,
        quantity: int,
        ltp: float,
        product: str = "NRML",
    ) -> str:
        """
        Simulate an order fill at *ltp*.

        Returns a virtual order ID (UUID prefix "PO-").
        Logs the virtual execution exactly like a live order would.
        """
        order_id = f"PO-{uuid.uuid4().hex[:8].upper()}"
        order = VirtualOrder(
            order_id=order_id,
            strategy=strategy,
            symbol=symbol,
            exchange=exchange,
            action=action,
            quantity=quantity,
            fill_price=ltp,
            product=product,
        )
        self._orders.append(order)
        self._update_position(order)
        self._save_snapshot()

        logger.info(
            f"{action} {symbol} qty={quantity} @ {ltp:.2f} — "
            f"Virtual Order Executed | orderid={order_id}"
        )
        return order_id

    # ---------------------------------------------------------------------------
    # Position management
    # ---------------------------------------------------------------------------

    def _update_position(self, order: VirtualOrder) -> None:
        """Update the virtual position book based on the filled order."""
        sym = order.symbol
        signed_qty = order.quantity if order.action == "BUY" else -order.quantity

        if sym not in self._positions:
            self._positions[sym] = VirtualPosition(
                symbol=sym,
                exchange=order.exchange,
                product=order.product,
                quantity=signed_qty,
                average_price=order.fill_price,
                current_ltp=order.fill_price,
            )
        else:
            pos = self._positions[sym]
            old_qty = pos.quantity
            new_qty = old_qty + signed_qty

            if new_qty == 0:
                # Fully closed — remove position
                del self._positions[sym]
                return

            # Update weighted average price (only when adding to position)
            if (old_qty > 0 and signed_qty > 0) or (old_qty < 0 and signed_qty < 0):
                total_cost = pos.average_price * abs(old_qty) + order.fill_price * abs(signed_qty)
                pos.average_price = total_cost / abs(new_qty)

            pos.quantity = new_qty

    def update_ltps(self, ltp_map: dict[str, float]) -> None:
        """
        Update current LTP for each open virtual position.

        Called every monitoring cycle from the strategy context.
        *ltp_map* is ``{symbol: ltp}`` for all relevant symbols.
        """
        for sym, pos in self._positions.items():
            if sym in ltp_map:
                pos.current_ltp = ltp_map[sym]

    # ---------------------------------------------------------------------------
    # MTM
    # ---------------------------------------------------------------------------

    @property
    def current_mtm(self) -> float:
        """Sum of unrealized P&L across all open virtual positions."""
        return sum(pos.unrealized_pnl for pos in self._positions.values())

    def update_mtm_highwater(self) -> None:
        """Refresh max / min MTM based on current values."""
        mtm = self.current_mtm
        if mtm > self._max_mtm:
            self._max_mtm = mtm
        if mtm < self._min_mtm:
            self._min_mtm = mtm

    @property
    def max_mtm(self) -> float:
        return self._max_mtm

    @property
    def min_mtm(self) -> float:
        return self._min_mtm

    # ---------------------------------------------------------------------------
    # Read-only views (mirror OpenAlgo API response shapes)
    # ---------------------------------------------------------------------------

    def get_positions(self) -> list[dict]:
        """Return virtual positions in OpenAlgo positionbook format."""
        result = []
        for pos in self._positions.values():
            result.append({
                "symbol": pos.symbol,
                "exchange": pos.exchange,
                "product": pos.product,
                "quantity": pos.quantity,
                "average_price": str(pos.average_price),
            })
        return result

    def get_orders(self) -> list[dict]:
        """Return virtual orders in OpenAlgo orderbook format."""
        return [
            {
                "orderid": o.order_id,
                "symbol": o.symbol,
                "exchange": o.exchange,
                "action": o.action,
                "quantity": o.quantity,
                "price": o.fill_price,
                "order_status": o.status,
                "timestamp": o.timestamp.strftime("%d-%b-%Y %H:%M:%S"),
            }
            for o in self._orders
        ]

    def get_trades(self) -> list[dict]:
        """Return virtual trades in OpenAlgo tradebook format."""
        return [
            {
                "orderid": o.order_id,
                "symbol": o.symbol,
                "exchange": o.exchange,
                "action": o.action,
                "quantity": o.quantity,
                "average_price": o.fill_price,
                "timestamp": o.timestamp.strftime("%d-%b-%Y %H:%M:%S"),
            }
            for o in self._orders
        ]

    # ---------------------------------------------------------------------------
    # Dashboard
    # ---------------------------------------------------------------------------

    def print_dashboard(self, strategy_state: object) -> None:
        """Print the paper mode dashboard to the console / log."""
        from ..utils.helper import format_inr
        from ..utils.constants import HEARTBEAT_SEPARATOR

        lines = [
            HEARTBEAT_SEPARATOR,
            "MODE          : PAPER",
            f"Strategy      : {getattr(strategy_state, 'strategy_name', 'N/A')}",
            f"State         : {getattr(strategy_state, 'phase', 'N/A')}",
            f"Center        : {getattr(strategy_state, 'center_strike', 'N/A')}",
            f"Wing Distance : {getattr(strategy_state, 'wing_distance', 'N/A')}",
            f"Adj Count     : {getattr(strategy_state, 'adjustment_count', 0)}",
            f"Current MTM   : {format_inr(self.current_mtm)}",
            f"Maximum MTM   : {format_inr(self.max_mtm)}",
            f"Minimum MTM   : {format_inr(self.min_mtm)}",
            "Open Legs     :",
        ]
        for pos in self._positions.values():
            direction = "LONG " if pos.quantity > 0 else "SHORT"
            lines.append(f"  {direction} {pos.symbol} qty={abs(pos.quantity)} ltp={pos.current_ltp:.2f}")

        lines.append("Heartbeat     : OK")
        lines.append(HEARTBEAT_SEPARATOR)

        for line in lines:
            logger.info(line)

    # ---------------------------------------------------------------------------
    # Persistence — JSON snapshot for restart recovery
    # ---------------------------------------------------------------------------

    def _save_snapshot(self) -> None:
        """Persist virtual positions and orders to JSON."""
        try:
            self._snapshot_path.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "positions": [
                    {
                        "symbol": p.symbol,
                        "exchange": p.exchange,
                        "product": p.product,
                        "quantity": p.quantity,
                        "average_price": p.average_price,
                        "current_ltp": p.current_ltp,
                    }
                    for p in self._positions.values()
                ],
                "orders": [
                    {
                        "order_id": o.order_id,
                        "strategy": o.strategy,
                        "symbol": o.symbol,
                        "exchange": o.exchange,
                        "action": o.action,
                        "quantity": o.quantity,
                        "fill_price": o.fill_price,
                        "product": o.product,
                        "timestamp": o.timestamp.isoformat(),
                        "status": o.status,
                    }
                    for o in self._orders
                ],
                "max_mtm": self._max_mtm,
                "min_mtm": self._min_mtm,
            }
            with open(self._snapshot_path, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
        except Exception as exc:
            logger.warning(f"Failed to save paper snapshot: {exc}")

    def _load_snapshot(self) -> None:
        """Restore virtual state from the JSON snapshot if it exists."""
        if not self._snapshot_path.exists():
            return
        try:
            with open(self._snapshot_path, "r", encoding="utf-8") as fh:
                data = json.load(fh)

            for p in data.get("positions", []):
                self._positions[p["symbol"]] = VirtualPosition(
                    symbol=p["symbol"],
                    exchange=p["exchange"],
                    product=p["product"],
                    quantity=p["quantity"],
                    average_price=p["average_price"],
                    current_ltp=p.get("current_ltp", 0.0),
                )

            for o in data.get("orders", []):
                self._orders.append(VirtualOrder(
                    order_id=o["order_id"],
                    strategy=o["strategy"],
                    symbol=o["symbol"],
                    exchange=o["exchange"],
                    action=o["action"],
                    quantity=o["quantity"],
                    fill_price=o["fill_price"],
                    product=o["product"],
                    timestamp=datetime.fromisoformat(o["timestamp"]),
                    status=o.get("status", "complete"),
                ))

            self._max_mtm = data.get("max_mtm", 0.0)
            self._min_mtm = data.get("min_mtm", 0.0)
            logger.info(
                f"Paper state restored from snapshot: "
                f"{len(self._positions)} positions, {len(self._orders)} orders"
            )
        except Exception as exc:
            logger.warning(f"Failed to load paper snapshot: {exc}")
