"""
In-memory cache for monitored positions.
Tracks live prices and computes real-time MTM without DB queries on every tick.
"""
from __future__ import annotations
import logging
from typing import TypedDict

logger = logging.getLogger(__name__)


class LegState(TypedDict):
    leg_id: str
    instrument: str
    underlying: str
    side: str          # BUY | SELL
    quantity: int
    lot_size: int
    entry_price: float
    current_price: float
    pnl: float
    premium_change_pct: float


class PositionState(TypedDict):
    monitor_id: str
    name: str
    underlying: str
    status: str
    legs: list[LegState]


class MonitorCache:
    def __init__(self) -> None:
        # monitor_id → PositionState
        self._positions: dict[str, PositionState] = {}
        # instrument → list of (monitor_id, leg_id)
        self._symbol_index: dict[str, list[tuple[str, str]]] = {}

    # ── Load ──────────────────────────────────────────────────────────────────

    async def reload(self) -> None:
        """Load all ACTIVE monitored positions from DB into memory."""
        from core.database import AsyncSessionLocal
        from monitors.models import MonitoredPosition
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(MonitoredPosition)
                    .where(MonitoredPosition.status == "ACTIVE")
                    .options(selectinload(MonitoredPosition.legs))
                )
                positions = result.scalars().all()

            self._positions = {}
            self._symbol_index = {}

            for pos in positions:
                self._add_position(pos)

            logger.info("[MonitorCache] loaded %d active positions", len(self._positions))
        except Exception as exc:
            logger.warning("[MonitorCache] reload failed: %s", exc)

    async def reload_position(self, monitor_id: str) -> None:
        """Reload a single position — called after add/update/status-change."""
        from core.database import AsyncSessionLocal
        from monitors.models import MonitoredPosition
        from sqlalchemy import select
        from sqlalchemy.orm import selectinload

        # Remove old entry first
        self._remove_position(monitor_id)

        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(MonitoredPosition)
                    .where(MonitoredPosition.monitor_id == monitor_id)
                    .options(selectinload(MonitoredPosition.legs))
                )
                pos = result.scalar_one_or_none()

            if pos and pos.status == "ACTIVE":
                self._add_position(pos)
        except Exception as exc:
            logger.warning("[MonitorCache] reload_position failed: %s", exc)

    def _add_position(self, pos) -> None:
        legs: list[LegState] = []
        for leg in pos.legs:
            legs.append(LegState(
                leg_id=leg.leg_id,
                instrument=leg.instrument,
                underlying=leg.underlying,
                side=leg.side,
                quantity=leg.quantity,
                lot_size=leg.lot_size,
                entry_price=leg.entry_price,
                current_price=leg.current_price or leg.entry_price,
                pnl=leg.pnl or 0.0,
                premium_change_pct=leg.premium_change_pct or 0.0,
            ))
            # Build symbol index
            instr = leg.instrument
            if instr not in self._symbol_index:
                self._symbol_index[instr] = []
            self._symbol_index[instr].append((pos.monitor_id, leg.leg_id))

        self._positions[pos.monitor_id] = PositionState(
            monitor_id=pos.monitor_id,
            name=pos.name,
            underlying=pos.underlying,
            status=pos.status,
            legs=legs,
        )

    def _remove_position(self, monitor_id: str) -> None:
        pos = self._positions.pop(monitor_id, None)
        if not pos:
            return
        for leg in pos["legs"]:
            instr = leg["instrument"]
            if instr in self._symbol_index:
                self._symbol_index[instr] = [
                    (m, l) for (m, l) in self._symbol_index[instr] if m != monitor_id
                ]
                if not self._symbol_index[instr]:
                    del self._symbol_index[instr]

    # ── Price update ──────────────────────────────────────────────────────────

    def update_price(self, monitor_id: str, leg_id: str, ltp: float) -> None:
        """Update a leg's current price and recalculate PnL."""
        pos = self._positions.get(monitor_id)
        if not pos:
            return
        for leg in pos["legs"]:
            if leg["leg_id"] == leg_id:
                leg["current_price"] = ltp
                entry = leg["entry_price"] or ltp
                qty = leg["quantity"] * leg["lot_size"]
                if leg["side"] == "SELL":
                    leg["pnl"] = (entry - ltp) * qty
                else:
                    leg["pnl"] = (ltp - entry) * qty
                leg["premium_change_pct"] = (
                    ((ltp - entry) / entry * 100) if entry else 0.0
                )
                break

    def get_legs_for_symbol(self, symbol: str) -> list[tuple[str, str]]:
        """Returns list of (monitor_id, leg_id) that have this instrument."""
        return self._symbol_index.get(symbol, [])

    def get_mtm_data(self, monitor_id: str) -> dict | None:
        pos = self._positions.get(monitor_id)
        if not pos:
            return None
        total_mtm = sum(l["pnl"] for l in pos["legs"])
        total_entry_value = sum(l["entry_price"] * l["quantity"] * l["lot_size"] for l in pos["legs"])
        mtm_pct = (total_mtm / total_entry_value * 100) if total_entry_value else 0.0
        return {
            "monitor_id": monitor_id,
            "name": pos["name"],
            "underlying": pos["underlying"],
            "total_mtm": round(total_mtm, 2),
            "total_mtm_pct": round(mtm_pct, 2),
            "legs": [
                {
                    "leg_id": l["leg_id"],
                    "instrument": l["instrument"],
                    "side": l["side"],
                    "entry_price": l["entry_price"],
                    "current_price": l["current_price"],
                    "pnl": round(l["pnl"], 2),
                    "premium_change_pct": round(l["premium_change_pct"], 2),
                }
                for l in pos["legs"]
            ],
        }

    def get_evaluation_context(self, monitor_id: str) -> dict | None:
        """Build evaluation context compatible with rule_evaluator.py key format."""
        pos = self._positions.get(monitor_id)
        if not pos:
            return None
        total_mtm = sum(l["pnl"] for l in pos["legs"])
        total_entry = sum(l["entry_price"] * l["quantity"] * l["lot_size"] for l in pos["legs"])
        pnl_pct = (total_mtm / total_entry * 100) if total_entry else 0.0

        leg_ltps:          dict[str, float] = {}
        leg_entry_prices:  dict[str, float] = {}
        leg_quantities:    dict[str, int]   = {}
        leg_sides:         dict[str, str]   = {}

        for l in pos["legs"]:
            lid = l["leg_id"]
            leg_ltps[lid]          = l["current_price"]
            leg_entry_prices[lid]  = l["entry_price"]
            leg_quantities[lid]    = l["quantity"] * l["lot_size"]
            leg_sides[lid]         = l["side"]

        return {
            "position_id":       monitor_id,
            "mtm":               total_mtm,
            "pnl_pct":           pnl_pct,
            "spot":              0.0,
            "leg_ltps":          leg_ltps,
            "leg_entry_prices":  leg_entry_prices,
            "leg_quantities":    leg_quantities,
            "leg_sides":         leg_sides,
            "indicators":        {},
        }

    def get_all_monitor_ids(self) -> list[str]:
        return list(self._positions.keys())


monitor_cache = MonitorCache()
