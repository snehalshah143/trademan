"""
CRUD endpoints for AlertRuleBuilder — the nested condition-tree alert system.
All routes under /api/v1/alert-rules
"""
import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from alerts.models import AlertRuleBuilder

logger = logging.getLogger(__name__)
router = APIRouter(tags=["alert-rules"])


# ── Pydantic schemas ───────────────────────────────────────────────────────────

class ConditionIn(BaseModel):
    id: str
    scope: str
    metric: str
    operator: str
    value: float | None = None
    leg_id: str | None = None
    params: dict = {}


class ConditionGroupIn(BaseModel):
    id: str
    op: str = "AND"
    conditions: list[ConditionIn] = []
    groups: list["ConditionGroupIn"] = []


ConditionGroupIn.model_rebuild()


class AlertRuleBuilderIn(BaseModel):
    strategy_id: str
    name: str
    description: str | None = None
    is_active: bool = True
    trigger_once: bool = False
    cooldown_secs: int = 0
    notify_popup: bool = True
    notify_telegram: bool = False
    notify_email: bool = False
    notify_webhook: bool = False
    notify_sound: bool = False
    webhook_url: str | None = None
    telegram_chat_id: str | None = None
    condition_tree: dict


class AlertRuleBuilderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    alert_id: str
    strategy_id: str
    name: str
    description: str | None
    is_active: bool
    trigger_once: bool
    cooldown_secs: int
    triggered_count: int
    last_triggered: datetime | None
    notify_popup: bool
    notify_telegram: bool
    notify_email: bool
    notify_webhook: bool
    notify_sound: bool
    webhook_url: str | None
    telegram_chat_id: str | None
    condition_tree: dict
    created_at: datetime
    updated_at: datetime


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/alert-rules", response_model=list[AlertRuleBuilderOut])
async def list_alert_rules(
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AlertRuleBuilder)
        .where(AlertRuleBuilder.strategy_id == strategy_id)
        .order_by(AlertRuleBuilder.created_at)
    )
    return result.scalars().all()


@router.post("/alert-rules", response_model=AlertRuleBuilderOut, status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    body: AlertRuleBuilderIn,
    db: AsyncSession = Depends(get_db),
):
    row = AlertRuleBuilder(
        alert_id=str(uuid.uuid4()),
        **body.model_dump(),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await _reload_engine()
    return row


@router.put("/alert-rules/{alert_id}", response_model=AlertRuleBuilderOut)
async def update_alert_rule(
    alert_id: str,
    body: AlertRuleBuilderIn,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AlertRuleBuilder).where(AlertRuleBuilder.alert_id == alert_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert rule not found")

    for field, value in body.model_dump().items():
        setattr(row, field, value)
    row.updated_at = datetime.now(tz=timezone.utc)

    await db.commit()
    await db.refresh(row)
    await _reload_engine()
    return row


@router.delete("/alert-rules/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_alert_rule(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AlertRuleBuilder).where(AlertRuleBuilder.alert_id == alert_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    await db.delete(row)
    await db.commit()
    await _reload_engine()


@router.patch("/alert-rules/{alert_id}/toggle", response_model=AlertRuleBuilderOut)
async def toggle_alert_rule(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AlertRuleBuilder).where(AlertRuleBuilder.alert_id == alert_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert rule not found")

    row.is_active = not row.is_active
    row.updated_at = datetime.now(tz=timezone.utc)
    await db.commit()
    await db.refresh(row)
    await _reload_engine()
    return row


async def _reload_engine() -> None:
    try:
        from alerts.alert_engine import alert_engine
        await alert_engine.reload()
    except Exception as exc:
        logger.warning("[alert_rules] engine reload failed: %s", exc)
