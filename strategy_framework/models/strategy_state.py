"""
strategy_state.py
-----------------
Persistent strategy state that survives restarts.

This is the *ground truth* held in memory (and optionally persisted to a
lightweight JSON snapshot).  On startup the RecoveryManager rebuilds this
object from OpenAlgo positions / trades / orders so local storage is only
used as a secondary fallback.

The state is intentionally flat and serialisable — no nested objects that
are hard to reconstruct from raw broker data.
"""

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
from typing import Optional

from .option_leg import OptionLeg, LegStatus


class StrategyPhase(str, Enum):
    """
    High-level lifecycle phase of the strategy.

    IDLE            → waiting for first trading day of the month
    DEPLOYING       → deployment orders in flight
    MONITORING      → deployed and healthy, watching for adjustment triggers
    ADJUSTING       → adjustment orders in flight
    REALIGNING      → wing-realignment orders in flight (post-recovery trigger)
    TIGHTENING      → wing-tightening orders in flight
    EXITING         → exit orders in flight (MTM-lock exit or expiry exit)
    EXITED          → all legs closed; waiting for next cycle
    PAUSED          → critical error; human intervention required
    """

    IDLE = "IDLE"
    DEPLOYING = "DEPLOYING"
    MONITORING = "MONITORING"
    ADJUSTING = "ADJUSTING"
    REALIGNING = "REALIGNING"
    TIGHTENING = "TIGHTENING"
    EXITING = "EXITING"
    EXITED = "EXITED"
    PAUSED = "PAUSED"


@dataclass
class StrategyState:
    """
    Complete runtime state of a strategy instance.

    Designed to be reconstructable purely from OpenAlgo broker data so that
    a restart never loses context.

    Attributes
    ----------
    strategy_id         : Unique ID tagging all orders e.g. ``IRON_FLY_202608``
    strategy_name       : Human-readable name e.g. ``IRON_FLY``
    phase               : Current lifecycle phase
    legs                : The four option legs (or fewer during deployment)
    center_strike       : ATM strike at which the short straddle was sold
    wing_distance       : Distance (in points) from center to hedge wings
    upper_breakeven     : Spot level above which the position starts losing
    lower_breakeven     : Spot level below which the position starts losing
    entry_ce_premium    : Short CE premium collected at entry
    entry_pe_premium    : Short PE premium collected at entry
    deployment_time     : When the strategy was first deployed this month
    expiry              : Monthly expiry string e.g. ``31JUL25``
    adjustment_count    : Number of adjustments performed this cycle
    adjustment_price    : Spot at which the most recent adjustment was made
    recovery_trigger    : Spot level that must be breached before wing realignment
    recovery_direction  : ``UP`` or ``DOWN`` — which direction recovery trigger applies
    max_mtm             : Highest MTM seen during this cycle
    min_mtm             : Lowest MTM seen during this cycle
    locked_mtm          : Current locked profit floor (MTM protection)
    wing_tightened      : True if wings have already been tightened this cycle
    last_updated        : Timestamp of the last state update
    paused_reason       : Human-readable reason when phase is PAUSED
    """

    strategy_id: str
    strategy_name: str

    phase: StrategyPhase = field(default=StrategyPhase.IDLE)

    # Legs — populated progressively during deployment / recovery
    legs: list[OptionLeg] = field(default_factory=list)

    # Iron Fly geometry
    center_strike: Optional[float] = field(default=None)
    wing_distance: Optional[float] = field(default=None)
    upper_breakeven: Optional[float] = field(default=None)
    lower_breakeven: Optional[float] = field(default=None)

    # Entry premiums (used to detect premium-doubling adjustment trigger)
    entry_ce_premium: Optional[float] = field(default=None)
    entry_pe_premium: Optional[float] = field(default=None)

    # Deployment metadata
    deployment_time: Optional[datetime] = field(default=None)
    expiry: Optional[str] = field(default=None)

    # Adjustment tracking
    adjustment_count: int = field(default=0)
    adjustment_price: Optional[float] = field(default=None)
    recovery_trigger: Optional[float] = field(default=None)
    recovery_direction: Optional[str] = field(default=None)  # "UP" or "DOWN"

    # Trailing recovery trigger — tracks the best spot seen since the last adjustment
    # Bullish (UP): tracks rolling high → trigger trails below the high
    # Bearish (DOWN): tracks rolling low  → trigger trails above the low
    recovery_high_water: Optional[float] = field(default=None)
    recovery_low_water: Optional[float] = field(default=None)

    # MTM tracking
    max_mtm: float = field(default=0.0)
    min_mtm: float = field(default=0.0)
    locked_mtm: Optional[float] = field(default=None)

    # Wing tightening
    wing_tightened: bool = field(default=False)

    # Metadata
    last_updated: Optional[datetime] = field(default=None)
    paused_reason: Optional[str] = field(default=None)

    # ---------------------------------------------------------------------------
    # Convenience accessors
    # ---------------------------------------------------------------------------

    def get_leg(self, role) -> Optional[OptionLeg]:
        """Return the first leg matching *role*, or None."""
        for leg in self.legs:
            if leg.role == role:
                return leg
        return None

    @property
    def open_legs(self) -> list[OptionLeg]:
        """All legs that are currently open (filled, not closed)."""
        return [leg for leg in self.legs if leg.status == LegStatus.OPEN]

    @property
    def is_deployed(self) -> bool:
        """True when all four legs are open and strategy is actively monitoring."""
        return len(self.open_legs) == 4

    @property
    def current_mtm(self) -> float:
        """Live unrealized P&L across all open legs."""
        return sum(leg.unrealized_pnl for leg in self.open_legs)

    def update_mtm_highwater(self) -> None:
        """Refresh max / min MTM based on current MTM."""
        mtm = self.current_mtm
        if mtm > self.max_mtm:
            self.max_mtm = mtm
        if mtm < self.min_mtm:
            self.min_mtm = mtm

    def mark_updated(self) -> None:
        """Stamp the last_updated timestamp."""
        self.last_updated = datetime.now()

    def pause(self, reason: str) -> None:
        """Transition to PAUSED phase with a human-readable reason."""
        self.phase = StrategyPhase.PAUSED
        self.paused_reason = reason
        self.mark_updated()

    def __str__(self) -> str:
        return (
            f"[{self.strategy_id}] phase={self.phase.value} "
            f"center={self.center_strike} wings±{self.wing_distance} "
            f"adj={self.adjustment_count} MTM={self.current_mtm:.0f} "
            f"max={self.max_mtm:.0f} locked={self.locked_mtm}"
        )
