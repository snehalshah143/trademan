"""
CRUD endpoints for AlertRule — alerts attached to monitored positions.
All routes under /api/v1/monitor-alerts
"""
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, func, delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from alerts.models import AlertRule, AlertHistory

logger = logging.getLogger(__name__)
router = APIRouter(tags=["monitor-alerts"])


# ── Schemas ────────────────────────────────────────────────────────────────────

class AlertRuleIn(BaseModel):
    position_id: str
    position_type: str = "MONITORED"
    strategy_name: str = ""
    underlying: str = ""
    name: str
    description: Optional[str] = None
    is_active: bool = True
    trigger_once: bool = False
    cooldown_secs: int = 60
    notify_popup: bool = True
    notify_telegram: bool = False
    notify_email: bool = False
    notify_webhook: bool = False
    notify_sound: bool = False
    webhook_url: Optional[str] = None
    telegram_chat_id: Optional[str] = None
    condition_tree: dict


class AlertRuleOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    alert_id: str
    position_id: str
    position_type: str
    strategy_name: str
    underlying: str
    name: str
    description: Optional[str]
    is_active: bool
    trigger_once: bool
    cooldown_secs: int
    triggered_count: int
    last_triggered: Optional[datetime]
    notify_popup: bool
    notify_telegram: bool
    notify_email: bool
    notify_webhook: bool
    notify_sound: bool
    webhook_url: Optional[str]
    telegram_chat_id: Optional[str]
    condition_tree: dict
    created_at: datetime
    updated_at: datetime


class AlertHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    history_id: str
    alert_id: str
    position_id: str
    alert_name: str
    strategy_name: str
    underlying: str
    fired_at: datetime
    condition_summary: str
    context_snapshot: dict
    notifications_sent: dict


class AlertStatsOut(BaseModel):
    total_alerts: int
    active_alerts: int
    fired_today: int
    fired_this_week: int
    by_scope: dict


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/monitor-alerts", response_model=list[AlertRuleOut])
async def list_monitor_alerts(
    position_id: Optional[str] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AlertRule).order_by(AlertRule.created_at)
    if position_id:
        stmt = stmt.where(AlertRule.position_id == position_id)
    if is_active is not None:
        stmt = stmt.where(AlertRule.is_active == is_active)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/monitor-alerts/stats", response_model=AlertStatsOut)
async def get_monitor_alert_stats(db: AsyncSession = Depends(get_db)):
    now = datetime.now(tz=timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())

    total = (await db.execute(select(func.count()).select_from(AlertRule))).scalar_one()
    active = (await db.execute(
        select(func.count()).select_from(AlertRule).where(AlertRule.is_active == True)
    )).scalar_one()
    fired_today = (await db.execute(
        select(func.count()).select_from(AlertHistory)
        .where(AlertHistory.fired_at >= today_start)
    )).scalar_one()
    fired_week = (await db.execute(
        select(func.count()).select_from(AlertHistory)
        .where(AlertHistory.fired_at >= week_start)
    )).scalar_one()

    all_rules = (await db.execute(select(AlertRule))).scalars().all()
    scope_counts: dict[str, int] = {"STRATEGY": 0, "LEG": 0, "SPOT": 0, "INDICATOR": 0, "MIXED": 0}
    for rule in all_rules:
        scopes = _extract_scopes(rule.condition_tree)
        if len(scopes) > 1:
            scope_counts["MIXED"] += 1
        elif len(scopes) == 1:
            s = next(iter(scopes))
            if s in scope_counts:
                scope_counts[s] += 1

    return AlertStatsOut(
        total_alerts=total,
        active_alerts=active,
        fired_today=fired_today,
        fired_this_week=fired_week,
        by_scope=scope_counts,
    )


def _extract_scopes(tree: dict) -> set[str]:
    scopes: set[str] = set()
    for c in tree.get("conditions", []):
        scopes.add(c.get("scope", ""))
    for g in tree.get("groups", []):
        scopes |= _extract_scopes(g)
    return scopes


@router.get("/monitor-alerts/history", response_model=list[AlertHistoryOut])
async def get_monitor_alert_history(
    position_id: Optional[str] = Query(default=None),
    alert_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AlertHistory).order_by(AlertHistory.fired_at.desc()).limit(limit)
    if position_id:
        stmt = stmt.where(AlertHistory.position_id == position_id)
    if alert_id:
        stmt = stmt.where(AlertHistory.alert_id == alert_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.delete("/monitor-alerts/history", status_code=status.HTTP_204_NO_CONTENT)
async def clear_monitor_alert_history(db: AsyncSession = Depends(get_db)):
    await db.execute(sql_delete(AlertHistory))
    await db.commit()


MONITOR_ALERT_TEMPLATES = [
    {
        "template_id": "iron_condor_protection",
        "name": "Iron Condor — Standard protection",
        "description": "3 alerts for typical Iron Condor protection",
        "alert_count": 3,
        "scopes": ["STRATEGY", "LEG", "SPOT"],
        "preview": ["Strategy MTM <= -3000", "Leg premium >= 100%", "Spot cross below Supertrend"],
        "alerts": [
            {"name": "MTM Stop Loss", "trigger_once": False, "cooldown_secs": 60,
             "notify_popup": True, "notify_telegram": False, "notify_sound": False, "notify_email": False, "notify_webhook": False,
             "condition_tree": {"id": "root", "op": "AND",
               "conditions": [{"id": "c1", "scope": "STRATEGY", "metric": "MTM", "operator": "LTE", "value": -3000, "leg_id": None, "params": {}}],
               "groups": []}},
            {"name": "Leg Premium Warning", "trigger_once": False, "cooldown_secs": 120,
             "notify_popup": True, "notify_telegram": True, "notify_sound": False, "notify_email": False, "notify_webhook": False,
             "condition_tree": {"id": "root", "op": "OR",
               "conditions": [{"id": "c1", "scope": "LEG", "metric": "PREMIUM_CHANGE", "operator": "GTE", "value": 100, "leg_id": None, "params": {}}],
               "groups": []}},
            {"name": "Trend Break", "trigger_once": False, "cooldown_secs": 300,
             "notify_popup": True, "notify_telegram": False, "notify_sound": False, "notify_email": False, "notify_webhook": False,
             "condition_tree": {"id": "root", "op": "AND",
               "conditions": [{"id": "c1", "scope": "SPOT", "metric": "SPOT_VS_SUPERTREND", "operator": "CROSS_BELOW", "value": None, "leg_id": None, "params": {}}],
               "groups": []}},
        ],
    },
    {
        "template_id": "straddle_profit_lock",
        "name": "Straddle — Profit lock",
        "description": "2 alerts for straddle profit and loss management",
        "alert_count": 2,
        "scopes": ["STRATEGY"],
        "preview": ["Strategy MTM >= 2000", "Strategy MTM <= -1500"],
        "alerts": [
            {"name": "Profit Target", "trigger_once": True, "cooldown_secs": 0,
             "notify_popup": True, "notify_telegram": False, "notify_sound": True, "notify_email": False, "notify_webhook": False,
             "condition_tree": {"id": "root", "op": "AND",
               "conditions": [{"id": "c1", "scope": "STRATEGY", "metric": "MTM", "operator": "GTE", "value": 2000, "leg_id": None, "params": {}}],
               "groups": []}},
            {"name": "Stop Loss", "trigger_once": False, "cooldown_secs": 60,
             "notify_popup": True, "notify_telegram": True, "notify_sound": True, "notify_email": False, "notify_webhook": False,
             "condition_tree": {"id": "root", "op": "AND",
               "conditions": [{"id": "c1", "scope": "STRATEGY", "metric": "MTM", "operator": "LTE", "value": -1500, "leg_id": None, "params": {}}],
               "groups": []}},
        ],
    },
    {
        "template_id": "bull_call_spread",
        "name": "Bull Call Spread — Leg monitor",
        "description": "2 alerts for bull call spread monitoring",
        "alert_count": 2,
        "scopes": ["LEG", "STRATEGY"],
        "preview": ["Buy leg premium drop >= 50%", "Strategy PnL >= 50%"],
        "alerts": [
            {"name": "Buy Leg Premium Drop", "trigger_once": False, "cooldown_secs": 120,
             "notify_popup": True, "notify_telegram": False, "notify_sound": False, "notify_email": False, "notify_webhook": False,
             "condition_tree": {"id": "root", "op": "AND",
               "conditions": [{"id": "c1", "scope": "LEG", "metric": "PREMIUM_CHANGE", "operator": "LTE", "value": -50, "leg_id": None, "params": {}}],
               "groups": []}},
            {"name": "Profit Target", "trigger_once": True, "cooldown_secs": 0,
             "notify_popup": True, "notify_telegram": False, "notify_sound": True, "notify_email": False, "notify_webhook": False,
             "condition_tree": {"id": "root", "op": "AND",
               "conditions": [{"id": "c1", "scope": "STRATEGY", "metric": "PNL_PCT", "operator": "GTE", "value": 50, "leg_id": None, "params": {}}],
               "groups": []}},
        ],
    },
]


@router.get("/monitor-alerts/templates")
async def get_monitor_alert_templates():
    return MONITOR_ALERT_TEMPLATES


class FromTemplateBody(BaseModel):
    template_id: str
    position_id: str
    strategy_name: str = ""
    underlying: str = ""
    customizations: dict = {}


@router.post("/monitor-alerts/from-template", status_code=status.HTTP_201_CREATED)
async def create_from_template(
    body: FromTemplateBody,
    db: AsyncSession = Depends(get_db),
):
    tpl = next((t for t in MONITOR_ALERT_TEMPLATES if t["template_id"] == body.template_id), None)
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")

    created = 0
    for a in tpl["alerts"]:
        row = AlertRule(
            alert_id=str(uuid.uuid4()),
            position_id=body.position_id,
            position_type="MONITORED",
            strategy_name=body.strategy_name,
            underlying=body.underlying,
            name=a["name"],
            description=None,
            is_active=True,
            trigger_once=a.get("trigger_once", False),
            cooldown_secs=a.get("cooldown_secs", 60),
            notify_popup=a.get("notify_popup", True),
            notify_telegram=a.get("notify_telegram", False),
            notify_email=a.get("notify_email", False),
            notify_webhook=a.get("notify_webhook", False),
            notify_sound=a.get("notify_sound", False),
            webhook_url=None,
            telegram_chat_id=None,
            condition_tree=a["condition_tree"],
        )
        db.add(row)
        created += 1

    await db.commit()
    return {"created": created, "position_id": body.position_id}


@router.post("/monitor-alerts", response_model=AlertRuleOut, status_code=status.HTTP_201_CREATED)
async def create_monitor_alert(
    body: AlertRuleIn,
    db: AsyncSession = Depends(get_db),
):
    row = AlertRule(alert_id=str(uuid.uuid4()), **body.model_dump())
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.get("/monitor-alerts/{alert_id}", response_model=AlertRuleOut)
async def get_monitor_alert(alert_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlertRule).where(AlertRule.alert_id == alert_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    return row


@router.put("/monitor-alerts/{alert_id}", response_model=AlertRuleOut)
async def update_monitor_alert(
    alert_id: str,
    body: AlertRuleIn,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(AlertRule).where(AlertRule.alert_id == alert_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    for field, value in body.model_dump().items():
        setattr(row, field, value)
    row.updated_at = datetime.now(tz=timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


@router.patch("/monitor-alerts/{alert_id}/toggle", response_model=AlertRuleOut)
async def toggle_monitor_alert(alert_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlertRule).where(AlertRule.alert_id == alert_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    row.is_active = not row.is_active
    row.updated_at = datetime.now(tz=timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


@router.patch("/monitor-alerts/{alert_id}/reset", response_model=AlertRuleOut)
async def reset_monitor_alert(alert_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlertRule).where(AlertRule.alert_id == alert_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    row.triggered_count = 0
    row.last_triggered = None
    row.updated_at = datetime.now(tz=timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/monitor-alerts/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_monitor_alert(alert_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(AlertRule).where(AlertRule.alert_id == alert_id))
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    await db.delete(row)
    await db.commit()
