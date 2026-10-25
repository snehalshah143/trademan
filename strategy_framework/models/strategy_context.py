"""
strategy_context.py
-------------------
Immutable snapshot of all market and account data for one monitoring cycle.

The MonitoringEngine builds a StrategyContext once per cycle by collecting
all OpenAlgo data upfront.  Business logic receives this object and makes
*all* decisions from it — never calling OpenAlgo a second time within the
same cycle.  This keeps business logic pure, testable, and free from I/O.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass(frozen=True)
class OptionChainEntry:
    """
    Data for one strike in the option chain (both CE and PE sides).

    Mirrors the structure returned by ``api.optionchain()``.
    """

    strike: float
    ce_symbol: Optional[str]
    ce_ltp: Optional[float]
    ce_label: Optional[str]      # ATM / ITM1 / OTM1 etc.
    pe_symbol: Optional[str]
    pe_ltp: Optional[float]
    pe_label: Optional[str]


@dataclass(frozen=True)
class StrategyContext:
    """
    Single-cycle snapshot passed to ``strategy.evaluate(context)``.

    All fields are read-only (``frozen=True``).  The strategy must never
    modify this object; it should derive all decisions from the data here.

    Attributes
    ----------
    timestamp       : Wall-clock time when this context was assembled
    spot            : NIFTY (or configured underlying) spot price
    atm_strike      : Nearest ATM strike from the option chain
    expiry          : Monthly expiry string (e.g. ``31JUL25``)
    option_chain    : List of OptionChainEntry objects (full or partial chain)
    positions       : Raw positionbook response ``data`` list from OpenAlgo
    trades          : Raw tradebook response ``data`` list from OpenAlgo
    orders          : Raw orderbook response ``data.orders`` list from OpenAlgo
    available_cash  : Available cash / margin from ``funds()``
    m2m_unrealized  : Broker-level unrealised M2M from ``funds()``
    m2m_realized    : Broker-level realised M2M from ``funds()``
    config          : Full configuration dict loaded from ``settings.yaml``
    strategy_state  : Current StrategyState (mutable reference; read-only here)
    is_t_minus_1    : True if today is one trading day before expiry
    is_expiry_day   : True if today is the expiry day
    """

    timestamp: datetime
    spot: float
    atm_strike: float
    expiry: str

    option_chain: list[OptionChainEntry]

    # Raw broker data (as returned by OpenAlgo — lists of dicts)
    positions: list[dict[str, Any]]
    trades: list[dict[str, Any]]
    orders: list[dict[str, Any]]

    # Account / margin
    available_cash: float
    m2m_unrealized: float
    m2m_realized: float

    # Configuration and state
    config: dict[str, Any]
    strategy_state: Any           # StrategyState — not typed here to avoid circular import

    # Expiry flags
    is_t_minus_1: bool
    is_expiry_day: bool

    # ---------------------------------------------------------------------------
    # Convenience lookups (computed on access, not stored)
    # ---------------------------------------------------------------------------

    def get_chain_entry(self, strike: float) -> Optional[OptionChainEntry]:
        """Return the OptionChainEntry for *strike*, or None if not found."""
        for entry in self.option_chain:
            if abs(entry.strike - strike) < 0.01:
                return entry
        return None

    def get_ce_ltp(self, strike: float) -> Optional[float]:
        """Return the CE LTP for *strike*, or None."""
        entry = self.get_chain_entry(strike)
        return entry.ce_ltp if entry else None

    def get_pe_ltp(self, strike: float) -> Optional[float]:
        """Return the PE LTP for *strike*, or None."""
        entry = self.get_chain_entry(strike)
        return entry.pe_ltp if entry else None

    def get_ce_symbol(self, strike: float) -> Optional[str]:
        """Return the CE option symbol for *strike*, or None."""
        entry = self.get_chain_entry(strike)
        return entry.ce_symbol if entry else None

    def get_pe_symbol(self, strike: float) -> Optional[str]:
        """Return the PE option symbol for *strike*, or None."""
        entry = self.get_chain_entry(strike)
        return entry.pe_symbol if entry else None

    @property
    def atm_ce_ltp(self) -> Optional[float]:
        """LTP of the ATM call."""
        return self.get_ce_ltp(self.atm_strike)

    @property
    def atm_pe_ltp(self) -> Optional[float]:
        """LTP of the ATM put."""
        return self.get_pe_ltp(self.atm_strike)

    @property
    def atm_total_premium(self) -> Optional[float]:
        """Sum of ATM CE + PE premiums; None if either is unavailable."""
        ce = self.atm_ce_ltp
        pe = self.atm_pe_ltp
        if ce is None or pe is None:
            return None
        return ce + pe

    def position_exists(self, symbol: str) -> bool:
        """True if *symbol* appears in the current position book."""
        return any(p.get("symbol") == symbol for p in self.positions)

    def get_position_quantity(self, symbol: str) -> int:
        """
        Return the net position quantity for *symbol*.
        Positive = long, negative = short (broker convention).
        Returns 0 if not found.
        """
        for p in self.positions:
            if p.get("symbol") == symbol:
                return int(p.get("quantity", 0))
        return 0
