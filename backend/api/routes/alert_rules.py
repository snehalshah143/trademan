"""
CRUD endpoints for AlertRuleBuilder — the nested condition-tree alert system.
All routes under /api/v1/alert-rules
"""
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from alerts.models import AlertRuleBuilder
from models.relational import AlertEvent

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
    timeframe: str | None = None
    lhs_source: str | None = None
    params: list | dict = []     # frontend sends list e.g. [14] for RSI period


class ConditionGroupIn(BaseModel):
    id: str
    op: str = "AND"
    conditions: list[ConditionIn] = []
    groups: list["ConditionGroupIn"] = []


ConditionGroupIn.model_rebuild()


class AlertRuleBuilderIn(BaseModel):
    strategy_id: str | None = None
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
    strategy_id: str | None
    strategy_name: str | None
    void: bool
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
    strategy_id: Optional[str] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    include_void: bool = Query(default=False, description="Include alerts whose strategy was deleted"),
    db: AsyncSession = Depends(get_db),
):
    stmt = select(AlertRuleBuilder).order_by(AlertRuleBuilder.created_at)
    if strategy_id is not None:
        stmt = stmt.where(AlertRuleBuilder.strategy_id == strategy_id)
    if is_active is not None:
        stmt = stmt.where(AlertRuleBuilder.is_active == is_active)
    if not include_void:
        stmt = stmt.where(AlertRuleBuilder.void == False)  # noqa: E712
    result = await db.execute(stmt)
    return result.scalars().all()


@router.post("/alert-rules", response_model=AlertRuleBuilderOut, status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    body: AlertRuleBuilderIn,
    db: AsyncSession = Depends(get_db),
):
    # Snapshot strategy name at creation time so it survives strategy deletion
    strategy_name: str | None = None
    if body.strategy_id:
        import uuid as _uuid
        from models.relational import Strategy
        try:
            _strat_uuid = _uuid.UUID(body.strategy_id)
        except (ValueError, AttributeError):
            _strat_uuid = None
        if _strat_uuid:
            strat_res = await db.execute(
                select(Strategy).where(Strategy.id == _strat_uuid)
            )
            strat = strat_res.scalar_one_or_none()
            if strat:
                strategy_name = strat.name

    data = body.model_dump()
    data["strategy_name"] = strategy_name
    row = AlertRuleBuilder(alert_id=str(uuid.uuid4()), **data)
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


@router.patch("/alert-rules/{alert_id}/reset", response_model=AlertRuleBuilderOut)
async def reset_alert_rule(
    alert_id: str,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(AlertRuleBuilder).where(AlertRuleBuilder.alert_id == alert_id)
    )
    row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Alert rule not found")
    row.triggered_count = 0
    row.last_triggered = None
    row.updated_at = datetime.now(tz=timezone.utc)
    await db.commit()
    await db.refresh(row)
    return row


class AlertStatsOut(BaseModel):
    total_alerts: int
    active_alerts: int
    fired_today: int
    fired_this_week: int
    most_triggered: list[dict]
    by_scope: dict


@router.get("/alert-rules/stats", response_model=AlertStatsOut)
async def get_alert_stats(db: AsyncSession = Depends(get_db)):
    now = datetime.now(tz=timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=now.weekday())

    total_res = await db.execute(select(func.count()).select_from(AlertRuleBuilder))
    total = total_res.scalar_one()

    active_res = await db.execute(
        select(func.count()).select_from(AlertRuleBuilder).where(AlertRuleBuilder.is_active == True)
    )
    active = active_res.scalar_one()

    today_res = await db.execute(
        select(func.count()).select_from(AlertEvent)
        .where(AlertEvent.triggered_at >= today_start)
    )
    fired_today = today_res.scalar_one()

    week_res = await db.execute(
        select(func.count()).select_from(AlertEvent)
        .where(AlertEvent.triggered_at >= week_start)
    )
    fired_week = week_res.scalar_one()

    top_res = await db.execute(
        select(AlertRuleBuilder)
        .where(AlertRuleBuilder.triggered_count > 0)
        .order_by(AlertRuleBuilder.triggered_count.desc())
        .limit(5)
    )
    most_triggered = [
        {"alert_id": r.alert_id, "name": r.name, "count": r.triggered_count}
        for r in top_res.scalars().all()
    ]

    # Scope breakdown — derive from condition_tree JSON
    all_rules_res = await db.execute(select(AlertRuleBuilder))
    all_rules = all_rules_res.scalars().all()
    scope_counts: dict[str, int] = {"STRATEGY": 0, "LEG": 0, "SPOT": 0, "INDICATOR": 0, "MIXED": 0}
    for rule in all_rules:
        scopes = _extract_scopes(rule.condition_tree)
        if len(scopes) > 1:
            scope_counts["MIXED"] += 1
        elif len(scopes) == 1:
            scope = next(iter(scopes))
            if scope in scope_counts:
                scope_counts[scope] += 1

    return AlertStatsOut(
        total_alerts=total,
        active_alerts=active,
        fired_today=fired_today,
        fired_this_week=fired_week,
        most_triggered=most_triggered,
        by_scope=scope_counts,
    )


def _extract_scopes(tree: dict) -> set[str]:
    scopes: set[str] = set()
    for cond in tree.get("conditions", []):
        scopes.add(cond.get("scope", ""))
    for grp in tree.get("groups", []):
        scopes |= _extract_scopes(grp)
    return scopes


# ── Alert History (delegates to AlertEvent table) ──────────────────────────────

class AlertHistoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    strategy_id: Optional[str]
    rule_id: Optional[str]
    symbol: Optional[str]
    message: str
    severity: str
    triggered_at: datetime
    dismissed: bool


@router.get("/alert-rules/history", response_model=list[AlertHistoryOut])
async def get_alert_history(
    strategy_id: Optional[str] = Query(default=None),
    rule_id: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=200),
    db: AsyncSession = Depends(get_db),
):
    stmt = (
        select(AlertEvent)
        .order_by(AlertEvent.triggered_at.desc())
        .limit(limit)
    )
    if strategy_id:
        import uuid as _uuid
        try:
            stmt = stmt.where(AlertEvent.strategy_id == _uuid.UUID(strategy_id))
        except ValueError:
            pass  # invalid UUID — skip filter
    if rule_id:
        stmt = stmt.where(AlertEvent.rule_id == rule_id)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        AlertHistoryOut(
            id=str(r.id),
            strategy_id=str(r.strategy_id) if r.strategy_id else None,
            rule_id=r.rule_id,
            symbol=r.symbol,
            message=r.message,
            severity=r.severity,
            triggered_at=r.triggered_at,
            dismissed=r.dismissed,
        )
        for r in rows
    ]


@router.delete("/alert-rules/history", status_code=status.HTTP_204_NO_CONTENT)
async def clear_alert_history(db: AsyncSession = Depends(get_db)):
    await db.execute(delete(AlertEvent))
    await db.commit()


# ── Templates ──────────────────────────────────────────────────────────────────

ALERT_TEMPLATES = [
    {
        "template_id": "iron_condor_protection",
        "name": "Iron Condor — Standard protection",
        "description": "3 alerts for typical Iron Condor protection",
        "alert_count": 3,
        "scopes": ["STRATEGY", "LEG", "INDICATOR"],
        "preview": [
            "Strategy MTM <= -3000",
            "Leg premium >= 100%",
            "Spot cross below Supertrend",
        ],
        "alerts": [
            {
                "name": "MTM Stop Loss",
                "description": "Strategy MTM crosses stop loss",
                "notify_popup": True, "notify_telegram": False,
                "notify_sound": False, "notify_email": False, "notify_webhook": False,
                "trigger_once": False, "cooldown_secs": 60,
                "condition_tree": {
                    "id": "root", "op": "AND",
                    "conditions": [{"id": "c1", "scope": "STRATEGY", "metric": "MTM", "operator": "LTE", "value": -3000, "leg_id": None, "params": {}}],
                    "groups": [],
                },
            },
            {
                "name": "Leg Premium Warning",
                "description": "Any leg premium doubles",
                "notify_popup": True, "notify_telegram": True,
                "notify_sound": False, "notify_email": False, "notify_webhook": False,
                "trigger_once": False, "cooldown_secs": 120,
                "condition_tree": {
                    "id": "root", "op": "OR",
                    "conditions": [{"id": "c1", "scope": "LEG", "metric": "PREMIUM_CHANGE", "operator": "GTE", "value": 100, "leg_id": None, "params": {}}],
                    "groups": [],
                },
            },
            {
                "name": "Trend Break",
                "description": "Spot crosses below Supertrend",
                "notify_popup": True, "notify_telegram": False,
                "notify_sound": False, "notify_email": False, "notify_webhook": False,
                "trigger_once": False, "cooldown_secs": 300,
                "condition_tree": {
                    "id": "root", "op": "AND",
                    "conditions": [{"id": "c1", "scope": "SPOT", "metric": "SPOT_VS_SUPERTREND", "operator": "CROSS_BELOW", "value": None, "leg_id": None, "params": {}}],
                    "groups": [],
                },
            },
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
            {
                "name": "Profit Target",
                "description": "Lock in profit when MTM reaches target",
                "notify_popup": True, "notify_telegram": False,
                "notify_sound": True, "notify_email": False, "notify_webhook": False,
                "trigger_once": True, "cooldown_secs": 0,
                "condition_tree": {
                    "id": "root", "op": "AND",
                    "conditions": [{"id": "c1", "scope": "STRATEGY", "metric": "MTM", "operator": "GTE", "value": 2000, "leg_id": None, "params": {}}],
                    "groups": [],
                },
            },
            {
                "name": "Stop Loss",
                "description": "Exit when MTM hits stop loss",
                "notify_popup": True, "notify_telegram": True,
                "notify_sound": True, "notify_email": False, "notify_webhook": False,
                "trigger_once": False, "cooldown_secs": 60,
                "condition_tree": {
                    "id": "root", "op": "AND",
                    "conditions": [{"id": "c1", "scope": "STRATEGY", "metric": "MTM", "operator": "LTE", "value": -1500, "leg_id": None, "params": {}}],
                    "groups": [],
                },
            },
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
            {
                "name": "Buy Leg Premium Drop",
                "description": "Alert when buy leg premium falls 50%",
                "notify_popup": True, "notify_telegram": False,
                "notify_sound": False, "notify_email": False, "notify_webhook": False,
                "trigger_once": False, "cooldown_secs": 120,
                "condition_tree": {
                    "id": "root", "op": "AND",
                    "conditions": [{"id": "c1", "scope": "LEG", "metric": "PREMIUM_CHANGE", "operator": "LTE", "value": -50, "leg_id": None, "params": {}}],
                    "groups": [],
                },
            },
            {
                "name": "Profit Target Reached",
                "description": "Alert when PnL hits 50%",
                "notify_popup": True, "notify_telegram": False,
                "notify_sound": True, "notify_email": False, "notify_webhook": False,
                "trigger_once": True, "cooldown_secs": 0,
                "condition_tree": {
                    "id": "root", "op": "AND",
                    "conditions": [{"id": "c1", "scope": "STRATEGY", "metric": "PNL_PCT", "operator": "GTE", "value": 50, "leg_id": None, "params": {}}],
                    "groups": [],
                },
            },
        ],
    },
]


@router.get("/alert-rules/templates")
async def get_alert_templates():
    return ALERT_TEMPLATES


class FromTemplateBody(BaseModel):
    template_id: str
    strategy_id: str
    customizations: dict = {}


@router.post("/alert-rules/from-template", status_code=status.HTTP_201_CREATED)
async def create_from_template(
    body: FromTemplateBody,
    db: AsyncSession = Depends(get_db),
):
    template = next((t for t in ALERT_TEMPLATES if t["template_id"] == body.template_id), None)
    if not template:
        raise HTTPException(status_code=404, detail="Template not found")

    created = []
    for alert_def in template["alerts"]:
        row = AlertRuleBuilder(
            alert_id=str(uuid.uuid4()),
            strategy_id=body.strategy_id,
            name=alert_def["name"],
            description=alert_def.get("description", ""),
            is_active=True,
            trigger_once=alert_def.get("trigger_once", False),
            cooldown_secs=alert_def.get("cooldown_secs", 60),
            notify_popup=alert_def.get("notify_popup", True),
            notify_telegram=alert_def.get("notify_telegram", False),
            notify_email=alert_def.get("notify_email", False),
            notify_webhook=alert_def.get("notify_webhook", False),
            notify_sound=alert_def.get("notify_sound", False),
            webhook_url=None,
            telegram_chat_id=None,
            condition_tree=alert_def["condition_tree"],
        )
        db.add(row)
        created.append(row)

    await db.commit()
    await _reload_engine()
    return {"created": len(created), "strategy_id": body.strategy_id}


async def _reload_engine() -> None:
    try:
        from alerts.alert_pipeline import alert_pipeline
        await alert_pipeline.reload()
    except Exception as exc:
        logger.warning("[alert_rules] engine reload failed: %s", exc)
