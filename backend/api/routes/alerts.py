"""
Alert event endpoints.

GET    /api/v1/alerts              — list alert events (newest first, optional filter)
POST   /api/v1/alerts/{id}/dismiss — mark an alert as dismissed
"""
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from models.relational import AlertEvent

router = APIRouter(prefix="/alerts", tags=["alerts"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class AlertEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    strategy_id: Optional[uuid.UUID]
    rule_id: Optional[str]
    symbol: Optional[str]
    message: str
    severity: str
    triggered_at: datetime
    dismissed: bool
    dismissed_at: Optional[datetime]


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[AlertEventOut])
async def list_alerts(
    dismissed: Optional[bool] = Query(default=None, description="Filter by dismissed state"),
    strategy_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AlertEvent).order_by(AlertEvent.triggered_at.desc()).limit(limit)
    if dismissed is not None:
        stmt = stmt.where(AlertEvent.dismissed == dismissed)
    if strategy_id is not None:
        stmt = stmt.where(AlertEvent.strategy_id == strategy_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/{alert_id}/dismiss", response_model=AlertEventOut)
async def dismiss_alert(alert_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlertEvent).where(AlertEvent.id == alert_id))
    alert = result.scalar_one_or_none()
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    alert.dismissed = True
    alert.dismissed_at = datetime.now(tz=timezone.utc)
    await db.flush()
    await db.refresh(alert)
    return alert
