"""
option_leg.py
-------------
Represents a single option leg in any strategy.

Used by both the framework (execution, recovery) and concrete strategies
(Iron Fly, Iron Condor, etc.) without any strategy-specific logic.
"""

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Optional


class LegAction(str, Enum):
    """Direction of the leg from the strategy's perspective."""
    BUY = "BUY"
    SELL = "SELL"


class OptionType(str, Enum):
    """Option instrument type."""
    CE = "CE"
    PE = "PE"


class LegRole(str, Enum):
    """
    Semantic role of the leg within the strategy.
    Allows the strategy to refer to legs by role rather than index.
    """
    SHORT_CALL = "SHORT_CALL"
    SHORT_PUT = "SHORT_PUT"
    LONG_CALL = "LONG_CALL"       # upper hedge wing
    LONG_PUT = "LONG_PUT"         # lower hedge wing
    # Credit spread generic roles
    PRIMARY = "PRIMARY"           # the short (sold) leg — bears premium risk
    HEDGE = "HEDGE"               # the long (bought) leg — caps max loss


class LegStatus(str, Enum):
    """Execution status of the leg."""
    PENDING = "PENDING"           # not yet sent
    OPEN = "OPEN"                 # order placed, fill confirmed
    CLOSED = "CLOSED"             # exited / bought back
    FAILED = "FAILED"             # order rejected or timed out


@dataclass
class OptionLeg:
    """
    A single option leg.

    Attributes
    ----------
    role        : Semantic role within the strategy (SHORT_CALL, LONG_PUT, etc.)
    symbol      : Fully qualified OpenAlgo option symbol e.g. ``NIFTY30JUN2525000CE``
    exchange    : Exchange where the option is listed (e.g. ``NFO``)
    strike      : Strike price as a float (e.g. 25000.0)
    option_type : CE or PE
    action      : BUY or SELL (from the strategy's perspective at entry)
    quantity    : Order quantity (in shares / contracts, not lots)
    product     : Broker product type (e.g. ``NRML``, ``MIS``)
    expiry      : Expiry date string in OpenAlgo format e.g. ``30JUN25``
    entry_price : Average fill price at entry; None until filled
    current_ltp : Latest market price; updated every monitoring cycle
    order_id    : Broker order ID returned by OpenAlgo on placement
    status      : Current lifecycle status of this leg
    entry_time  : Wall-clock timestamp when the fill was confirmed
    exit_price  : Average fill price when the leg was closed; None if still open
    exit_time   : Wall-clock timestamp when the exit fill was confirmed
    """

    role: LegRole
    symbol: str
    exchange: str
    strike: float
    option_type: OptionType
    action: LegAction
    quantity: int
    product: str
    expiry: str

    entry_price: Optional[float] = field(default=None)
    current_ltp: Optional[float] = field(default=None)
    order_id: Optional[str] = field(default=None)
    status: LegStatus = field(default=LegStatus.PENDING)
    entry_time: Optional[datetime] = field(default=None)
    exit_price: Optional[float] = field(default=None)
    exit_time: Optional[datetime] = field(default=None)

    # ---------------------------------------------------------------------------
    # Computed properties
    # ---------------------------------------------------------------------------

    @property
    def is_open(self) -> bool:
        """True if the leg has been filled and not yet closed."""
        return self.status == LegStatus.OPEN

    @property
    def is_short(self) -> bool:
        """True if this is a SELL leg (we collected premium)."""
        return self.action == LegAction.SELL

    @property
    def is_long(self) -> bool:
        """True if this is a BUY leg (we paid premium)."""
        return self.action == LegAction.BUY

    @property
    def unrealized_pnl(self) -> float:
        """
        Unrealized P&L for this leg based on current LTP.

        For a SELL leg  : pnl = (entry_price - current_ltp) * quantity
        For a BUY leg   : pnl = (current_ltp - entry_price) * quantity
        Returns 0.0 if either price is unavailable.
        """
        if self.entry_price is None or self.current_ltp is None:
            return 0.0
        if self.is_short:
            return (self.entry_price - self.current_ltp) * self.quantity
        return (self.current_ltp - self.entry_price) * self.quantity

    @property
    def realized_pnl(self) -> float:
        """
        Realized P&L after the leg is closed.

        Returns 0.0 if the leg has not been closed yet.
        """
        if self.entry_price is None or self.exit_price is None:
            return 0.0
        if self.is_short:
            return (self.entry_price - self.exit_price) * self.quantity
        return (self.exit_price - self.entry_price) * self.quantity

    # ---------------------------------------------------------------------------
    # Representation
    # ---------------------------------------------------------------------------

    def __str__(self) -> str:
        price_str = f"@ {self.entry_price:.2f}" if self.entry_price else "pending"
        ltp_str = f"LTP {self.current_ltp:.2f}" if self.current_ltp else "no LTP"
        return (
            f"{self.action.value} {self.symbol} ({self.role.value}) "
            f"{price_str} | {ltp_str} | {self.status.value}"
        )
