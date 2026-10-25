"""
Alert Template CRUD + apply endpoints.

GET    /api/v1/alert-templates              — list all user templates
POST   /api/v1/alert-templates              — create template
GET    /api/v1/alert-templates/{id}         — get one
PUT    /api/v1/alert-templates/{id}         — update (name/description/alerts)
DELETE /api/v1/alert-templates/{id}         — delete
POST   /api/v1/alert-templates/{id}/apply   — apply to a strategy (creates N AlertRuleBuilder rows)
"""
import copy
import uuid
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from alerts.models import AlertTemplate, AlertRuleBuilder


# ── Leg-ID remapping helper ────────────────────────────────────────────────────

def _remap_leg_ids(tree: dict, leg_map: dict) -> dict:
    """
    Deep-copy the condition tree and replace placeholder leg_ids
    (leg_1, leg_2, …) with the actual leg UUIDs of the target strategy.
    Works recursively through nested groups.
    """
    if not tree or not leg_map:
        return tree
    tree = copy.deepcopy(tree)
    for cond in tree.get("conditions", []):
        if isinstance(cond, dict) and cond.get("leg_id") in leg_map:
            cond["leg_id"] = leg_map[cond["leg_id"]]
    tree["groups"] = [_remap_leg_ids(g, leg_map) for g in tree.get("groups", [])]
    return tree

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/alert-templates", tags=["alert-templates"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class AlertDefIn(BaseModel):
    """One alert inside a template — mirrors AlertRuleBuilderIn minus strategy_id."""
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


class AlertTemplateIn(BaseModel):
    name: str
    description: str | None = None
    alerts: list[AlertDefIn] = []


class AlertTemplateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    template_id: str
    name: str
    description: str | None
    alerts: list
    created_at: datetime
    updated_at: datetime


class ApplyBody(BaseModel):
    strategy_id: str
    # Optional explicit mapping: {"leg_1": "actual-uuid", ...}
    # If supplied, used directly (frontend has legIndex; backend does not).
    # If omitted, backend derives the mapping via DB sort.
    leg_overrides: dict[str, str] | None = None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_or_404(template_id: str, db: AsyncSession) -> AlertTemplate:
    result = await db.execute(
        select(AlertTemplate).where(AlertTemplate.template_id == template_id)
    )
    t = result.scalar_one_or_none()
    if t is None:
        raise HTTPException(status_code=404, detail="Template not found")
    return t


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=list[AlertTemplateOut])
async def list_templates(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(AlertTemplate).order_by(AlertTemplate.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=AlertTemplateOut, status_code=status.HTTP_201_CREATED)
async def create_template(body: AlertTemplateIn, db: AsyncSession = Depends(get_db)):
    t = AlertTemplate(
        template_id=str(uuid.uuid4()),
        name=body.name,
        description=body.description,
        alerts=[a.model_dump() for a in body.alerts],
    )
    db.add(t)
    await db.flush()
    await db.refresh(t)
    return t


@router.get("/{template_id}", response_model=AlertTemplateOut)
async def get_template(template_id: str, db: AsyncSession = Depends(get_db)):
    return await _get_or_404(template_id, db)


@router.put("/{template_id}", response_model=AlertTemplateOut)
async def update_template(
    template_id: str,
    body: AlertTemplateIn,
    db: AsyncSession = Depends(get_db),
):
    t = await _get_or_404(template_id, db)
    t.name = body.name
    if body.description is not None:
        t.description = body.description
    t.alerts = [a.model_dump() for a in body.alerts]
    t.updated_at = datetime.now(tz=timezone.utc)
    await db.flush()
    await db.refresh(t)
    return t


@router.delete("/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_template(template_id: str, db: AsyncSession = Depends(get_db)):
    t = await _get_or_404(template_id, db)
    await db.delete(t)


@router.post("/{template_id}/apply", status_code=status.HTTP_201_CREATED)
async def apply_template(
    template_id: str,
    body: ApplyBody,
    db: AsyncSession = Depends(get_db),
):
    """
    Create an AlertRuleBuilder row for every alert defined in the template,
    associated with the given strategy_id.  Returns {created: N}.
    """
    import traceback as _tb

    t = await _get_or_404(template_id, db)
    if not t.alerts:
        return {"created": 0}

    # Build leg_map: {leg_1: actual_uuid, leg_2: actual_uuid, …} from strategy legs.
    # Priority: use frontend-supplied leg_overrides (has legIndex) → DB sort fallback.
    leg_map: dict[str, str] = {}
    if body.leg_overrides:
        # Frontend computed the correct BUY-first sorted mapping — use it directly.
        leg_map = body.leg_overrides
    else:
        # Fallback: build mapping from DB legs using smart type detection.
        try:
            from models.relational import StrategyLeg
            strategy_uuid = uuid.UUID(body.strategy_id)
            legs_result = await db.execute(
                select(StrategyLeg)
                .where(StrategyLeg.strategy_id == strategy_uuid)
                .order_by(StrategyLeg.strike)
            )
            strategy_legs = legs_result.scalars().all()

            def _infer(symbol: str, stored: str | None) -> str:
                """Smart instrument type: stored field wins, else symbol suffix."""
                if stored and stored != "EQ":
                    return stored
                s = symbol.upper()
                if s.endswith("CE"):  return "CE"
                if s.endswith("PE"):  return "PE"
                if "FUT" in s:        return "FUT"
                return "EQ"

            # ── Positional slots: BUY-first → strike asc sort
            sorted_legs = sorted(
                strategy_legs,
                key=lambda l: (0 if l.action == "BUY" else 1, l.strike or 0),
            )
            for i, leg in enumerate(sorted_legs, start=1):
                leg_map[f"leg_{i}"] = str(leg.id)

            # ── Role-based slots: matched by action + inferred type
            _ROLE_SLOTS = {
                "leg_buy_call":  ("BUY",  "CE"),
                "leg_sell_call": ("SELL", "CE"),
                "leg_buy_put":   ("BUY",  "PE"),
                "leg_sell_put":  ("SELL", "PE"),
                "leg_buy_fut":   ("BUY",  "FUT"),
                "leg_sell_fut":  ("SELL", "FUT"),
            }
            for slot_id, (action, opt_type) in _ROLE_SLOTS.items():
                matched = next(
                    (l for l in sorted_legs
                     if l.action == action and _infer(l.symbol, l.option_type) == opt_type),
                    None,
                )
                if matched:
                    leg_map[slot_id] = str(matched.id)
        except (ValueError, Exception) as exc:
            logger.warning("[AlertTemplates] leg lookup failed for strategy %s: %s", body.strategy_id, exc)
            # Proceed with empty leg_map — condition_tree placeholders stay as-is

    created = 0
    for alert_def in t.alerts:
        try:
            raw_tree = alert_def.get("condition_tree") or {
                "id": str(uuid.uuid4()), "op": "AND", "conditions": [], "groups": []
            }
            condition_tree = _remap_leg_ids(raw_tree, leg_map)

            row = AlertRuleBuilder(
                alert_id=str(uuid.uuid4()),
                strategy_id=body.strategy_id,
                name=alert_def.get("name") or "Alert",
                description=alert_def.get("description"),
                is_active=bool(alert_def.get("is_active", True)),
                trigger_once=bool(alert_def.get("trigger_once", False)),
                cooldown_secs=int(alert_def.get("cooldown_secs", 0)),
                notify_popup=bool(alert_def.get("notify_popup", True)),
                notify_telegram=bool(alert_def.get("notify_telegram", False)),
                notify_email=bool(alert_def.get("notify_email", False)),
                notify_webhook=bool(alert_def.get("notify_webhook", False)),
                notify_sound=bool(alert_def.get("notify_sound", False)),
                webhook_url=alert_def.get("webhook_url"),
                telegram_chat_id=alert_def.get("telegram_chat_id"),
                condition_tree=condition_tree,
            )
            db.add(row)
            created += 1
        except Exception as exc:
            logger.error("[AlertTemplates] skipping alert_def due to error: %s\n%s",
                         exc, _tb.format_exc())

    if created == 0 and t.alerts:
        raise HTTPException(status_code=500, detail="Failed to build any alert rules from template")

    await db.commit()

    # Reload alert pipeline so new rules are evaluated immediately
    try:
        from alerts.alert_pipeline import alert_pipeline
        await alert_pipeline.reload()
    except Exception:
        pass

    logger.info("[AlertTemplates] applied %d alerts from template %s to strategy %s",
                created, template_id, body.strategy_id)
    return {"created": created}
