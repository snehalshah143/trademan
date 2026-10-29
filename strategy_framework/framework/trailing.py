"""
trailing.py
-----------
Trailing stop-loss and profit-booking logic for credit spread strategies.

Components
----------
TrailType
    Enum for the three supported trailing modes.

TrailingSlManager
    Adjusts the stop-loss floor upward (for profit protection) using one of:
    - MTM_STEP   : ratchets SL up by ``step`` each time MTM crosses a new
                   multiple of ``step``.  Pure MTM-based, no spot dependency.
    - SPOT_STEP  : ratchets SL based on spot price moving *in favour* by
                   ``step``.  Ratchet-only (SL never moves against position).
    - SUPERTREND : SL = current Supertrend line value (updated each bar close).

ProfitLadder
    Configurable partial-exit schedule.  e.g.:
      "Exit 50 % when MTM >= 6000; exit 50 % of remainder when MTM >= 8000."
    ``check(mtm, lots_open)`` returns the number of lots to exit at the
    current MTM level.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Trail types
# ---------------------------------------------------------------------------

class TrailType(str, Enum):
    MTM_STEP   = "MTM_STEP"    # trail SL based on strategy MTM
    SPOT_STEP  = "SPOT_STEP"   # trail SL based on spot price
    SUPERTREND = "SUPERTREND"  # SL = Supertrend line value
    NONE       = "NONE"        # trailing disabled


# ---------------------------------------------------------------------------
# TrailingSlManager
# ---------------------------------------------------------------------------

@dataclass
class TrailingSlManager:
    """
    Manages a ratcheting stop-loss floor.

    The SL is initialised to ``initial_sl`` (e.g. the raw max-loss cap from
    config) and can only move in the *tighter* direction (i.e. it never relaxes
    once locked in).

    Parameters
    ----------
    trail_type  : Which trailing algorithm to apply.
    initial_sl  : Starting SL value (absolute MTM loss, positive number).
                  e.g. ``5000`` means "exit if MTM < -5000".
    step        : For MTM_STEP / SPOT_STEP — the increment / ratchet unit.
    activation  : MTM must reach this level before trailing activates.
                  Prevents premature SL moves during warm-up.
    """

    trail_type: TrailType = TrailType.NONE
    initial_sl: float = 0.0          # positive → loss threshold
    step: float = 0.0
    activation: float = 0.0          # MTM must exceed this before trailing starts

    _sl: float = field(default=0.0, init=False, repr=False)
    _activated: bool = field(default=False, init=False, repr=False)
    _last_spot: Optional[float] = field(default=None, init=False, repr=False)
    _best_spot: Optional[float] = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self._sl = self.initial_sl

    # -----------------------------------------------------------------------
    # Update methods (call once per monitoring cycle / candle close)
    # -----------------------------------------------------------------------

    def update_mtm(self, mtm: float) -> None:
        """
        Call on every monitoring cycle with the current strategy MTM.
        Applicable for ``MTM_STEP`` mode.

        Ratchet rule: SL tightens whenever MTM exceeds a new multiple of ``step``
        above the activation threshold.
        """
        if self.trail_type != TrailType.MTM_STEP:
            return
        if not self._check_activation(mtm):
            return

        # How many full steps above zero has MTM reached?
        # SL = initial_sl - (steps_achieved * step)
        # i.e. for each step of profit, we give back at most step of that profit.
        if self.step <= 0:
            return

        steps = int(mtm // self.step)
        if steps <= 0:
            return

        new_sl = self.initial_sl - steps * self.step
        if new_sl < self._sl:
            logger.info(
                "TrailingSL[MTM_STEP]: SL tightened from %.0f → %.0f (MTM=%.0f steps=%d)",
                self._sl, new_sl, mtm, steps,
            )
            self._sl = new_sl

    def update_spot(self, spot: float, bias: str) -> None:
        """
        Call on every monitoring cycle with the current spot price.
        Applicable for ``SPOT_STEP`` mode.

        Parameters
        ----------
        spot  : Current spot (index) price.
        bias  : ``"BULLISH"`` or ``"BEARISH"`` — direction of the trade.
                Bullish → trail only moves when spot goes up.
                Bearish → trail only moves when spot goes down.
        """
        if self.trail_type != TrailType.SPOT_STEP:
            return
        if self._best_spot is None:
            self._best_spot = spot
            self._last_spot = spot
            return

        # Only tighten in the profitable direction (ratchet — never relax)
        if bias == "BULLISH":
            if spot > self._best_spot:
                moved = spot - self._best_spot
                if moved >= self.step and self.step > 0:
                    ticks = int(moved // self.step)
                    new_sl = self.initial_sl - ticks * self.step
                    if new_sl < self._sl:
                        logger.info(
                            "TrailingSL[SPOT_STEP]: SL tightened from %.0f → %.0f "
                            "(spot=%.0f best=%.0f)",
                            self._sl, new_sl, spot, self._best_spot,
                        )
                        self._sl = new_sl
                self._best_spot = max(self._best_spot, spot)
        else:  # BEARISH
            if spot < self._best_spot:
                moved = self._best_spot - spot
                if moved >= self.step and self.step > 0:
                    ticks = int(moved // self.step)
                    new_sl = self.initial_sl - ticks * self.step
                    if new_sl < self._sl:
                        logger.info(
                            "TrailingSL[SPOT_STEP]: SL tightened from %.0f → %.0f "
                            "(spot=%.0f best=%.0f)",
                            self._sl, new_sl, spot, self._best_spot,
                        )
                        self._sl = new_sl
                self._best_spot = min(self._best_spot, spot)

        self._last_spot = spot

    def update_supertrend(self, supertrend_value: float) -> None:
        """
        Call on each candle close with the latest Supertrend line value.
        Applicable for ``SUPERTREND`` mode.

        The SL is set to the Supertrend value (converted to an MTM equivalent
        by the strategy). This method just stores the raw ST value.
        """
        if self.trail_type != TrailType.SUPERTREND:
            return
        # Store raw value; strategy converts to MTM stop externally
        self._sl = supertrend_value
        logger.debug("TrailingSL[SUPERTREND]: SL updated to %.2f", supertrend_value)

    # -----------------------------------------------------------------------
    # Query
    # -----------------------------------------------------------------------

    def is_triggered(self, mtm: float) -> bool:
        """
        Returns ``True`` if ``mtm`` has breached the current SL floor.

        For MTM_STEP / SPOT_STEP modes:
            triggered when ``mtm < -abs(sl)``
            (sl is stored as a positive loss threshold)
        For SUPERTREND mode:
            the strategy must pass the sign-converted MTM value; same rule applies.
        """
        if self.trail_type == TrailType.NONE:
            return False
        if self.trail_type == TrailType.SUPERTREND:
            # sl holds the raw Supertrend price; caller passes spot instead of mtm
            return mtm < self._sl
        # MTM-based modes: sl is a positive loss amount
        return mtm < -abs(self._sl)

    @property
    def current_sl(self) -> float:
        """The current SL threshold (positive for loss modes, raw price for ST mode)."""
        return self._sl

    @property
    def is_activated(self) -> bool:
        return self._activated

    # -----------------------------------------------------------------------
    # Private helpers
    # -----------------------------------------------------------------------

    def _check_activation(self, mtm: float) -> bool:
        """Activate trailing once MTM exceeds the activation threshold."""
        if not self._activated and mtm >= self.activation:
            self._activated = True
            logger.info(
                "TrailingSL: ACTIVATED at MTM=%.0f (activation=%.0f)",
                mtm, self.activation,
            )
        return self._activated


# ---------------------------------------------------------------------------
# PriceStepTrailSL — price-based stepped trailing SL (futures directional)
# ---------------------------------------------------------------------------

class PriceStepTrailSL:
    """
    Price-based stepped trailing stop-loss for LONG futures directional trades.

    Phase 1 — Before activation:
        SL is fixed at ``entry_price - initial_sl_pts``.

    Phase 2 — First activation (when price rises by ``activation_pts``):
        SL jumps to ``activation_price - trail_offset``.
        high_water is anchored at the activation price.

    Phase 3 — Ratchet (each subsequent ``step_pts`` rise):
        high_water advances by ``step_pts``.
        SL advances by ``step_pts`` (always ``high_water - trail_offset``).

    Example (default params: activation=30, offset=20, step=5):
        Entry  @5000 → SL = 4970  (initial, -30)
        Price  @5030 → SL = 5010  (5030 - 20, first activation)
        Price  @5035 → SL = 5015  (5035 - 20, +5 ratchet)
        Price  @5040 → SL = 5020  (5040 - 20)
        Price  @5041 → SL = 5020  (no ratchet — need full +5 step)

    Notes
    -----
    - SL is tracked as an **absolute price level** (not an MTM amount).
    - is_triggered(price) fires when ``price <= current_sl``.
    - Call update(close) on every closed candle before checking is_triggered.
    """

    def __init__(
        self,
        entry_price:     float,
        initial_sl_pts:  float = 30.0,
        activation_pts:  float = 30.0,
        trail_offset:    float = 20.0,
        step_pts:        float = 5.0,
    ) -> None:
        self._entry             = entry_price
        self._sl                = entry_price - initial_sl_pts   # absolute price SL
        self._activation_price  = entry_price + activation_pts
        self._trail_offset      = trail_offset
        self._step_pts          = step_pts
        self._activated         = False
        self._high_water        = entry_price     # highest price in step_pts increments

    def update(self, current_price: float) -> None:
        """Call on every closed candle. Ratchets SL upward when conditions are met."""
        if not self._activated:
            if current_price >= self._activation_price:
                self._activated  = True
                self._high_water = current_price
                new_sl           = current_price - self._trail_offset
                if new_sl > self._sl:
                    logger.info(
                        "PriceStepTrailSL ACTIVATED: price=%.2f SL %.2f → %.2f",
                        current_price, self._sl, new_sl,
                    )
                    self._sl = new_sl
            return

        # Advance high_water in step_pts multiples
        steps = int((current_price - self._high_water) / self._step_pts)
        if steps > 0:
            self._high_water += steps * self._step_pts
            new_sl = self._high_water - self._trail_offset
            if new_sl > self._sl:
                logger.info(
                    "PriceStepTrailSL: high=%.2f  SL %.2f → %.2f",
                    self._high_water, self._sl, new_sl,
                )
                self._sl = new_sl

    def is_triggered(self, current_price: float) -> bool:
        """True if price has fallen to or below the current SL level."""
        return current_price <= self._sl

    @property
    def current_sl(self) -> float:
        """Current absolute price SL level."""
        return self._sl

    @property
    def is_activated(self) -> bool:
        return self._activated


# ---------------------------------------------------------------------------
# MultiPhaseTrailSL — custom multi-phase trailing SL (futures directional)
# ---------------------------------------------------------------------------

class MultiPhaseTrailSL:
    """
    Multi-phase trailing stop-loss for LONG futures directional trades.

    Phase 0 — Before any trigger:
        SL is fixed at ``entry_price - initial_sl_pts``.

    Phase 1 — When price rises by ``phase1_trigger`` pts from entry:
        SL jumps to ``initial_sl_price + phase1_sl_add``.
        Example: entry=5000, initial_sl=4970, phase1_trigger=30, phase1_sl_add=30
                 → price hits 5030 → SL = 4970 + 30 = 5000

    Phase 2 — When price rises by ``phase2_trigger`` pts from entry:
        SL becomes ``entry + phase2_entry_offset``.
        Thereafter each ``step_pts`` price rise increments SL by ``step_sl_pts``.
        Example: phase2_trigger=50, phase2_entry_offset=25, step_pts=5, step_sl_pts=7
                 → price hits 5050 → SL = 5000 + 25 = 5025
                 → price hits 5055 → SL = 5032  (+7)
                 → price hits 5060 → SL = 5039  (+7)
                 → price hits 5065 → SL = 5046  (+7)

    Example (defaults: initial=30, p1_trigger=30, p1_add=30, p2_trigger=50,
             p2_offset=25, step_pts=5, step_sl_pts=7):
        Entry  @5000 → SL = 4970  (Phase 0, entry-30)
        Price  @5030 → SL = 5000  (Phase 1, initial_sl+30)
        Price  @5050 → SL = 5025  (Phase 2, entry+25)
        Price  @5055 → SL = 5032  (+7 step)
        Price  @5060 → SL = 5039  (+7 step)
        Price  @5065 → SL = 5046  (+7 step)

    Notes
    -----
    - SL tracked as an **absolute price level**.
    - ``is_triggered(price)`` fires when ``price <= current_sl``.
    - Call ``update(close)`` on every closed candle before checking ``is_triggered``.
    - SL can only move upward — the guard ``if new_sl > self._sl`` prevents relaxation.
    - ``step_pts``    : price must rise this many pts to trigger a ratchet step.
    - ``step_sl_pts`` : how many pts SL advances per ratchet step (can differ from step_pts).
    """

    def __init__(
        self,
        entry_price:          float,
        initial_sl_pts:       float = 30.0,
        phase1_trigger_pts:   float = 30.0,
        phase1_sl_add:        float = 30.0,
        phase2_trigger_pts:   float = 50.0,
        phase2_entry_offset:  float = 25.0,
        step_pts:             float = 5.0,
        step_sl_pts:          float = 7.0,
        direction:            str   = "LONG",
    ) -> None:
        self._is_long = direction.upper() != "SHORT"

        self._entry          = entry_price
        self._step_pts       = step_pts
        self._step_sl_pts    = step_sl_pts
        self._phase          = 0
        self._activated      = False

        if self._is_long:
            # LONG: SL is below entry, moves up as price rises
            self._sl             = entry_price - initial_sl_pts          # 4960
            self._initial_sl     = entry_price - initial_sl_pts
            self._phase1_price   = entry_price + phase1_trigger_pts      # 5030
            self._phase1_sl      = self._initial_sl + phase1_sl_add      # 4990
            self._phase2_price   = entry_price + phase2_trigger_pts      # 5050
            self._phase2_base_sl = entry_price + phase2_entry_offset     # 5015
        else:
            # SHORT: SL is above entry, moves down as price falls
            self._sl             = entry_price + initial_sl_pts          # 5040
            self._initial_sl     = entry_price + initial_sl_pts
            self._phase1_price   = entry_price - phase1_trigger_pts      # 4970
            self._phase1_sl      = self._initial_sl - phase1_sl_add      # 5010
            self._phase2_price   = entry_price - phase2_trigger_pts      # 4950
            self._phase2_base_sl = entry_price - phase2_entry_offset     # 4985

    def update(self, current_price: float) -> None:
        """Call on every closed candle. Advances SL through phases when triggered."""
        if self._is_long:
            self._update_long(current_price)
        else:
            self._update_short(current_price)

    def _update_long(self, current_price: float) -> None:
        if self._phase == 0:
            if current_price >= self._phase2_price:
                self._phase = 2; self._activated = True
                self._update_phase2_long(current_price)
            elif current_price >= self._phase1_price:
                self._phase = 1; self._activated = True
                new_sl = self._phase1_sl
                if new_sl > self._sl:
                    logger.info(
                        "[STOPLOSS TRAIL] MultiPhaseTrailSL[LONG] PHASE-1: price=%.2f  SL %.2f → %.2f",
                        current_price, self._sl, new_sl,
                    )
                    self._sl = new_sl
        elif self._phase == 1:
            if current_price >= self._phase2_price:
                self._phase = 2
                self._update_phase2_long(current_price)
        else:
            self._update_phase2_long(current_price)

    def _update_short(self, current_price: float) -> None:
        if self._phase == 0:
            if current_price <= self._phase2_price:
                self._phase = 2; self._activated = True
                self._update_phase2_short(current_price)
            elif current_price <= self._phase1_price:
                self._phase = 1; self._activated = True
                new_sl = self._phase1_sl
                if new_sl < self._sl:
                    logger.info(
                        "[STOPLOSS TRAIL] MultiPhaseTrailSL[SHORT] PHASE-1: price=%.2f  SL %.2f → %.2f",
                        current_price, self._sl, new_sl,
                    )
                    self._sl = new_sl
        elif self._phase == 1:
            if current_price <= self._phase2_price:
                self._phase = 2
                self._update_phase2_short(current_price)
        else:
            self._update_phase2_short(current_price)

    def _update_phase2_long(self, current_price: float) -> None:
        """LONG phase2: SL = phase2_base_sl + N×step_sl_pts for each step_pts rise above phase2_price."""
        steps  = int((current_price - self._phase2_price) / self._step_pts)
        new_sl = self._phase2_base_sl + steps * self._step_sl_pts
        if new_sl > self._sl:
            logger.info(
                "[STOPLOSS TRAIL] MultiPhaseTrailSL[LONG] PHASE-2: price=%.2f  SL %.2f → %.2f  (steps=%d)",
                current_price, self._sl, new_sl, steps,
            )
            self._sl = new_sl

    def _update_phase2_short(self, current_price: float) -> None:
        """SHORT phase2: SL = phase2_base_sl - N×step_sl_pts for each step_pts drop below phase2_price."""
        steps  = int((self._phase2_price - current_price) / self._step_pts)
        new_sl = self._phase2_base_sl - steps * self._step_sl_pts
        if new_sl < self._sl:
            logger.info(
                "[STOPLOSS TRAIL] MultiPhaseTrailSL[SHORT] PHASE-2: price=%.2f  SL %.2f → %.2f  (steps=%d)",
                current_price, self._sl, new_sl, steps,
            )
            self._sl = new_sl

    def is_triggered(self, current_price: float) -> bool:
        """True if price has hit or crossed the current SL level."""
        if self._is_long:
            return current_price <= self._sl
        return current_price >= self._sl

    @property
    def current_sl(self) -> float:
        """Current absolute price SL level."""
        return self._sl

    @property
    def is_activated(self) -> bool:
        """True once phase1 or phase2 has activated."""
        return self._activated


# ---------------------------------------------------------------------------
# ProfitLadder — partial exit schedule
# ---------------------------------------------------------------------------

@dataclass
class ProfitLadderRung:
    """A single rung in the profit ladder."""
    mtm_threshold: float        # MTM level at which this rung triggers
    exit_pct: float             # Percentage of remaining open lots to exit (0-100)
    _triggered: bool = field(default=False, init=False, repr=False)

    @property
    def triggered(self) -> bool:
        return self._triggered

    def mark_triggered(self) -> None:
        self._triggered = True


class ProfitLadder:
    """
    Configurable partial-exit schedule.

    Build from a list of (mtm_threshold, exit_pct) pairs::

        ladder = ProfitLadder.from_config([
            {"mtm": 6000, "exit_pct": 50},
            {"mtm": 8000, "exit_pct": 50},
        ])

    Then on each cycle::

        lots_to_exit = ladder.check(current_mtm, lots_open=2)
        if lots_to_exit:
            execute_partial_exit(lots_to_exit)

    After exiting, call ``ladder.mark_last_triggered()`` so the rung is not
    re-triggered on subsequent cycles.
    """

    def __init__(self, rungs: list[ProfitLadderRung]) -> None:
        # Sort ascending by threshold so we evaluate lowest first
        self._rungs = sorted(rungs, key=lambda r: r.mtm_threshold)

    @classmethod
    def from_config(cls, config_list: list[dict]) -> "ProfitLadder":
        """
        Build a ``ProfitLadder`` from a list of dicts with keys:
            ``mtm``      (float) — MTM level to trigger
            ``exit_pct`` (float) — percentage of remaining lots to exit
        """
        rungs = []
        for item in config_list:
            mtm = float(item.get("mtm", 0))
            pct = float(item.get("exit_pct", 0))
            if mtm > 0 and 0 < pct <= 100:
                rungs.append(ProfitLadderRung(mtm_threshold=mtm, exit_pct=pct))
        return cls(rungs)

    def check(self, mtm: float, lots_open: int) -> int:
        """
        Returns the number of lots to exit at the current MTM level.

        Evaluates all un-triggered rungs in ascending threshold order.
        Only the *first* un-triggered rung that qualifies fires at a time
        (prevents double-exits in a single cycle).

        Parameters
        ----------
        mtm       : Current strategy MTM (profit = positive).
        lots_open : Number of lots currently open.

        Returns
        -------
        Number of lots to exit (0 if no rung triggers).
        """
        if lots_open <= 0:
            return 0

        for rung in self._rungs:
            if rung.triggered:
                continue
            if mtm >= rung.mtm_threshold:
                lots = max(1, round(lots_open * rung.exit_pct / 100.0))
                logger.info(
                    "ProfitLadder: rung triggered at MTM=%.0f threshold=%.0f "
                    "exit_pct=%.0f%% lots_open=%d → lots_to_exit=%d",
                    mtm, rung.mtm_threshold, rung.exit_pct, lots_open, lots,
                )
                return lots
        return 0

    def mark_last_triggered(self) -> None:
        """
        Mark the lowest un-triggered rung that was fired as completed.
        Call this AFTER successfully executing the partial exit.
        """
        for rung in self._rungs:
            if not rung.triggered:
                rung.mark_triggered()
                logger.debug(
                    "ProfitLadder: rung %.0f marked triggered", rung.mtm_threshold
                )
                return

    def reset(self) -> None:
        """Reset all rungs (call at the start of a new strategy cycle)."""
        for rung in self._rungs:
            rung._triggered = False

    @property
    def all_triggered(self) -> bool:
        """True if every rung in the ladder has been executed."""
        return all(r.triggered for r in self._rungs)

    @property
    def next_rung(self) -> Optional[ProfitLadderRung]:
        """The next un-triggered rung, or ``None`` if all are done."""
        for r in self._rungs:
            if not r.triggered:
                return r
        return None
