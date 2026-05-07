"""
CRUD endpoints for manually monitored positions.
GET/POST/PUT/PATCH/DELETE /api/v1/monitored-positions
"""
import uuid
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.database import get_db
from monitors.models import MonitoredPosition, MonitoredLeg

logger = logging.getLogger(__name__)
router = APIRouter(tags=["monitored-positions"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class LegIn(BaseModel):
    leg_number: int = 1
    instrument: str
    underlying: str
    strike: Optional[float] = None
    option_type: str
    expiry: str
    side: str
    quantity: int = 1
    lot_size: int = 1
    entry_price: float = 0.0


class LegPriceUpdate(BaseModel):
    entry_price: float


class MonitorIn(BaseModel):
    name: str
    strategy_type: str = "CUSTOM"
    underlying: str
    exchange: str = "NFO"
    notes: Optional[str] = None
    legs: list[LegIn] = []


class MonitorUpdate(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None


class StatusUpdate(BaseModel):
    status: str  # ACTIVE | PAUSED | CLOSED


class LegOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    leg_id: str
    monitor_id: str
    leg_number: int
    instrument: str
    underlying: str
    strike: Optional[float]
    option_type: str
    expiry: str
    side: str
    quantity: int
    lot_size: int
    entry_price: float
    current_price: float
    pnl: float
    premium_change_pct: float


class MonitorOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    monitor_id: str
    name: str
    strategy_type: str
    underlying: str
    exchange: str
    status: str
    notes: Optional[str]
    created_at: datetime
    updated_at: datetime
    legs: list[LegOut] = []
    alert_count: int = 0


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _get_alert_count(db: AsyncSession, monitor_id: str) -> int:
    from alerts.models import AlertRule
    result = await db.execute(
        select(AlertRule).where(AlertRule.position_id == monitor_id)
    )
    return len(result.scalars().all())


async def _enrich(pos: MonitoredPosition, db: AsyncSession) -> MonitorOut:
    alert_count = await _get_alert_count(db, pos.monitor_id)
    out = MonitorOut.model_validate(pos)
    out.alert_count = alert_count
    return out


async def _reload_engine(monitor_id: str) -> None:
    try:
        from monitors.monitor_engine import monitor_engine
        await monitor_engine.reload_position(monitor_id)
    except Exception as exc:
        logger.warning("[monitored_positions] engine reload failed: %s", exc)


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/monitored-positions", response_model=list[MonitorOut])
async def list_monitored_positions(
    status: Optional[str] = Query(default=None),
    underlying: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(MonitoredPosition)
        .options(selectinload(MonitoredPosition.legs))
        .order_by(MonitoredPosition.created_at.desc())
    )
    if status:
        stmt = stmt.where(MonitoredPosition.status == status.upper())
    if underlying:
        stmt = stmt.where(MonitoredPosition.underlying == underlying.upper())
    result = await db.execute(stmt)
    positions = result.scalars().all()
    return [await _enrich(p, db) for p in positions]


@router.get("/monitored-positions/{monitor_id}", response_model=MonitorOut)
async def get_monitored_position(
    monitor_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MonitoredPosition)
        .where(MonitoredPosition.monitor_id == monitor_id)
        .options(selectinload(MonitoredPosition.legs))
    )
    pos = result.scalar_one_or_none()
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    return await _enrich(pos, db)


@router.post("/monitored-positions", response_model=MonitorOut, status_code=status.HTTP_201_CREATED)
async def create_monitored_position(
    body: MonitorIn,
    db: AsyncSession = Depends(get_db),
):
    if len(body.legs) > 10:
        raise HTTPException(status_code=400, detail="Maximum 10 legs allowed")

    monitor_id = str(uuid.uuid4())
    pos = MonitoredPosition(
        monitor_id=monitor_id,
        name=body.name,
        strategy_type=body.strategy_type.upper(),
        underlying=body.underlying.upper(),
        exchange=body.exchange.upper(),
        status="ACTIVE",
        notes=body.notes,
    )
    db.add(pos)

    for i, leg_data in enumerate(body.legs):
        leg = MonitoredLeg(
            leg_id=str(uuid.uuid4()),
            monitor_id=monitor_id,
            leg_number=leg_data.leg_number or (i + 1),
            instrument=leg_data.instrument.upper(),
            underlying=leg_data.underlying.upper(),
            strike=leg_data.strike,
            option_type=leg_data.option_type.upper(),
            expiry=leg_data.expiry.upper(),
            side=leg_data.side.upper(),
            quantity=leg_data.quantity,
            lot_size=leg_data.lot_size,
            entry_price=leg_data.entry_price,
            current_price=leg_data.entry_price,
            pnl=0.0,
            premium_change_pct=0.0,
        )
        db.add(leg)

    await db.commit()
    await db.refresh(pos)

    # Load legs for the response
    result = await db.execute(
        select(MonitoredPosition)
        .where(MonitoredPosition.monitor_id == monitor_id)
        .options(selectinload(MonitoredPosition.legs))
    )
    pos = result.scalar_one()
    await _reload_engine(monitor_id)
    return await _enrich(pos, db)


@router.put("/monitored-positions/{monitor_id}", response_model=MonitorOut)
async def update_monitored_position(
    monitor_id: str,
    body: MonitorUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MonitoredPosition)
        .where(MonitoredPosition.monitor_id == monitor_id)
        .options(selectinload(MonitoredPosition.legs))
    )
    pos = result.scalar_one_or_none()
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    if body.name is not None:
        pos.name = body.name
    if body.notes is not None:
        pos.notes = body.notes
    pos.updated_at = datetime.now(tz=timezone.utc)

    await db.commit()
    await db.refresh(pos)
    return await _enrich(pos, db)


@router.patch("/monitored-positions/{monitor_id}/leg/{leg_id}", response_model=LegOut)
async def update_leg_price(
    monitor_id: str,
    leg_id: str,
    body: LegPriceUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MonitoredLeg).where(
            MonitoredLeg.leg_id == leg_id,
            MonitoredLeg.monitor_id == monitor_id,
        )
    )
    leg = result.scalar_one_or_none()
    if not leg:
        raise HTTPException(status_code=404, detail="Leg not found")

    leg.entry_price = body.entry_price
    # Recalculate PnL with existing current_price
    qty = leg.quantity * leg.lot_size
    if leg.side == "SELL":
        leg.pnl = (body.entry_price - leg.current_price) * qty
    else:
        leg.pnl = (leg.current_price - body.entry_price) * qty
    if body.entry_price > 0:
        leg.premium_change_pct = (leg.current_price - body.entry_price) / body.entry_price * 100

    await db.commit()
    await db.refresh(leg)
    await _reload_engine(monitor_id)
    return leg


@router.patch("/monitored-positions/{monitor_id}/status", response_model=MonitorOut)
async def update_monitor_status(
    monitor_id: str,
    body: StatusUpdate,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MonitoredPosition)
        .where(MonitoredPosition.monitor_id == monitor_id)
        .options(selectinload(MonitoredPosition.legs))
    )
    pos = result.scalar_one_or_none()
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    new_status = body.status.upper()
    if new_status not in ("ACTIVE", "PAUSED", "CLOSED"):
        raise HTTPException(status_code=400, detail="Invalid status")

    pos.status = new_status
    pos.updated_at = datetime.now(tz=timezone.utc)
    await db.commit()
    await db.refresh(pos)
    await _reload_engine(monitor_id)
    return await _enrich(pos, db)


@router.delete("/monitored-positions/{monitor_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_monitored_position(
    monitor_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(MonitoredPosition).where(MonitoredPosition.monitor_id == monitor_id)
    )
    pos = result.scalar_one_or_none()
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    # Also delete associated alert rules
    from alerts.models import AlertRule
    from sqlalchemy import delete as sql_delete
    await db.execute(
        sql_delete(AlertRule).where(AlertRule.position_id == monitor_id)
    )

    await db.delete(pos)
    await db.commit()

    # Remove from engine cache
    try:
        from monitors.monitor_cache import monitor_cache
        monitor_cache._remove_position(monitor_id)
    except Exception:
        pass


@router.get("/monitored-positions/{monitor_id}/mtm")
async def get_monitor_mtm(
    monitor_id: str,
    db: AsyncSession = Depends(get_db),
):
    from monitors.monitor_cache import monitor_cache
    mtm = monitor_cache.get_mtm_data(monitor_id)
    if mtm:
        return mtm

    # Fallback: compute from DB
    result = await db.execute(
        select(MonitoredPosition)
        .where(MonitoredPosition.monitor_id == monitor_id)
        .options(selectinload(MonitoredPosition.legs))
    )
    pos = result.scalar_one_or_none()
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")

    total_mtm = sum(l.pnl for l in pos.legs)
    total_entry = sum(l.entry_price * l.quantity * l.lot_size for l in pos.legs)
    mtm_pct = (total_mtm / total_entry * 100) if total_entry else 0.0

    return {
        "monitor_id": monitor_id,
        "total_mtm": round(total_mtm, 2),
        "total_mtm_pct": round(mtm_pct, 2),
        "legs": [
            {
                "leg_id": l.leg_id,
                "instrument": l.instrument,
                "side": l.side,
                "entry_price": l.entry_price,
                "current_price": l.current_price,
                "pnl": round(l.pnl, 2),
                "premium_change_pct": round(l.premium_change_pct, 2),
            }
            for l in pos.legs
        ],
    }
