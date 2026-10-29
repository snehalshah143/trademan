"""
future_leg.py
-------------
Represents a single futures leg in any directional strategy.

Used by commodity, equity, and currency futures strategies.
Contains no strategy-specific logic — just data + computed properties.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class FutureDirection(str, Enum):
    """Direction of the futures position."""
    LONG  = "LONG"   # BUY futures (profit when price rises)
    SHORT = "SHORT"  # SELL futures (profit when price falls)


class FutureStatus(str, Enum):
    """Lifecycle status of the futures leg."""
    PENDING = "PENDING"   # not yet sent
    OPEN    = "OPEN"      # order placed, fill confirmed
    CLOSED  = "CLOSED"    # position squared off
    FAILED  = "FAILED"    # order rejected or timed out


@dataclass
class FutureLeg:
    """
    A single futures leg.

    Attributes
    ----------
    symbol      : Fully qualified OpenAlgo futures symbol  e.g. ``CRUDEOIL17NOV25``
    exchange    : Exchange where the future is listed  e.g. ``MCX``, ``NFO``
    direction   : LONG (BUY) or SHORT (SELL)
    quantity    : Order quantity (in contracts / lots × lot_size)
    lot_size    : Contracts per lot  e.g. 100 for Crudeoil
    product     : Broker product type  e.g. ``MIS``, ``NRML``
    expiry      : Expiry date string in OpenAlgo format  e.g. ``17NOV25``
    entry_price : Average fill price at entry; None until filled
    current_ltp : Latest market price; updated every monitoring cycle
    order_id    : Broker order ID returned by OpenAlgo on placement
    status      : Current lifecycle status
    entry_time  : Wall-clock timestamp when entry fill was confirmed
    exit_price  : Average fill price on close; None if still open
    exit_time   : Wall-clock timestamp when exit fill was confirmed
    realized_pnl: Realized P&L after close; None if still open
    """

    symbol:      str
    exchange:    str
    direction:   FutureDirection
    quantity:    int
    lot_size:    int
    product:     str
    expiry:      str

    entry_price:  Optional[float]   = field(default=None)
    current_ltp:  Optional[float]   = field(default=None)
    order_id:     Optional[str]     = field(default=None)
    status:       FutureStatus      = field(default=FutureStatus.PENDING)
    entry_time:   Optional[datetime] = field(default=None)
    exit_price:   Optional[float]   = field(default=None)
    exit_time:    Optional[datetime] = field(default=None)
    realized_pnl: Optional[float]   = field(default=None)

    # -----------------------------------------------------------------------
    # Computed properties
    # -----------------------------------------------------------------------

    @property
    def lots(self) -> int:
        """Number of lots (quantity ÷ lot_size)."""
        return self.quantity // self.lot_size if self.lot_size else self.quantity

    @property
    def is_open(self) -> bool:
        return self.status == FutureStatus.OPEN

    @property
    def is_long(self) -> bool:
        return self.direction == FutureDirection.LONG

    @property
    def unrealized_pnl(self) -> float:
        """
        Unrealized P&L based on current LTP.
          LONG  : (current_ltp - entry_price) × quantity
          SHORT : (entry_price - current_ltp) × quantity
        Returns 0.0 if any price is unavailable.
        """
        if self.entry_price is None or self.current_ltp is None:
            return 0.0
        diff = self.current_ltp - self.entry_price
        return diff * self.quantity if self.is_long else -diff * self.quantity

    @property
    def computed_realized_pnl(self) -> float:
        """
        Realized P&L after the leg is closed.
        Returns 0.0 if not yet closed.
        """
        if self.entry_price is None or self.exit_price is None:
            return 0.0
        diff = self.exit_price - self.entry_price
        return diff * self.quantity if self.is_long else -diff * self.quantity

    # -----------------------------------------------------------------------
    # Representation
    # -----------------------------------------------------------------------

    def __str__(self) -> str:
        price = f"@ {self.entry_price:.2f}" if self.entry_price else "pending"
        ltp   = f"LTP {self.current_ltp:.2f}" if self.current_ltp else "no LTP"
        return (
            f"{self.direction.value} {self.symbol} ({self.lots} lot{'s' if self.lots != 1 else ''})"
            f" {price} | {ltp} | {self.status.value}"
        )
