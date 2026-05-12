"""
Strategy CRUD endpoints.

GET    /api/v1/strategies          — list all strategies
POST   /api/v1/strategies          — create a strategy
GET    /api/v1/strategies/{id}     — get one strategy
PATCH  /api/v1/strategies/{id}     — partial update
DELETE /api/v1/strategies/{id}     — delete
"""
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from core.database import get_db
from models.relational import Strategy, StrategyLeg

router = APIRouter(prefix="/strategies", tags=["strategies"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class LegOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    symbol: str
    action: str
    quantity: int
    option_type: Optional[str]
    strike: Optional[float]
    expiry: Optional[str]
    entry_price: Optional[float]
    exit_price: Optional[float]
    status: str
    broker_order_id: Optional[str]


class StrategyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    description: Optional[str]
    status: str
    underlying: Optional[str]
    expiry: Optional[str]
    created_at: datetime
    updated_at: datetime
    legs: List[LegOut] = []


class LegCreate(BaseModel):
    id: Optional[str] = None          # client-provided leg UUID — preserves frontend ID
    symbol: str
    action: str
    quantity: int = 1
    option_type: Optional[str] = None
    strike: Optional[float] = None
    expiry: Optional[str] = None
    entry_price: Optional[float] = None


class StrategyCreate(BaseModel):
    id: Optional[str] = None          # client-provided UUID — preserves frontend ID
    name: str
    description: Optional[str] = None
    status: Optional[str] = None
    underlying: Optional[str] = None
    expiry: Optional[str] = None
    legs: List[LegCreate] = []


class StrategyPatch(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[str] = None
    underlying: Optional[str] = None
    expiry: Optional[str] = None


# ── Helpers ───────────────────────────────────────────────────────────────────

async def _get_or_404(strategy_id: uuid.UUID, db: AsyncSession) -> Strategy:
    result = await db.execute(
        select(Strategy)
        .options(selectinload(Strategy.legs))
        .where(Strategy.id == strategy_id)
    )
    strategy = result.scalar_one_or_none()
    if strategy is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Strategy not found")
    return strategy


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[StrategyOut])
async def list_strategies(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Strategy).options(selectinload(Strategy.legs)).order_by(Strategy.created_at.desc())
    )
    return result.scalars().all()


@router.post("", response_model=StrategyOut, status_code=status.HTTP_201_CREATED)
async def create_strategy(body: StrategyCreate, db: AsyncSession = Depends(get_db)):
    import json as _json

    # Use client-provided UUID if given — this ensures the frontend's strategy ID
    # matches what the backend and alert engine use (prevents UUID mismatch).
    try:
        strategy_id = uuid.UUID(body.id) if body.id else uuid.uuid4()
    except (ValueError, AttributeError):
        strategy_id = uuid.uuid4()

    # Idempotent: if strategy already exists, return it — but if it has no legs
    # and the request brings a description JSON with legs, create the missing legs.
    # This handles the case where the strategy was created before leg parsing was
    # wired up, or when the first POST failed silently (fire-and-forget in frontend).
    existing_res = await db.execute(
        select(Strategy).options(selectinload(Strategy.legs)).where(Strategy.id == strategy_id)
    )
    existing = existing_res.scalar_one_or_none()
    if existing is not None:
        if existing.legs:
            return existing
        # Strategy exists but has no legs — try to create them from description JSON
        desc_src = body.description or existing.description
        if desc_src:
            try:
                desc = _json.loads(desc_src)
                for fl in desc.get("legs", []):
                    inst = fl.get("instrument", {})
                    if not inst.get("symbol"):
                        continue
                    inst_type = inst.get("instrumentType", "")
                    opt_type = inst_type if inst_type in ("CE", "PE", "FUT") else None
                    try:
                        leg_id = uuid.UUID(fl.get("id")) if fl.get("id") else uuid.uuid4()
                    except (ValueError, AttributeError):
                        leg_id = uuid.uuid4()
                    existing.legs.append(StrategyLeg(
                        id=leg_id,
                        symbol=inst.get("symbol", ""),
                        action=fl.get("side", "BUY"),
                        quantity=int(fl.get("quantity", 1)),
                        option_type=opt_type,
                        strike=inst.get("strike"),
                        expiry=inst.get("expiry"),
                        entry_price=fl.get("entryPrice"),
                        status="filled",
                    ))
                if body.description and not existing.description:
                    existing.description = body.description
                await db.flush()
            except Exception:
                pass
        return await _get_or_404(strategy_id, db)

    strategy = Strategy(
        id=strategy_id,
        name=body.name,
        description=body.description,
        status=body.status or "active",
        underlying=body.underlying,
        expiry=body.expiry,
    )

    # Build legs — explicit legs list takes priority
    legs_to_create = body.legs

    # If no explicit legs, parse them from the description JSON (frontend saves full strategy blob)
    if not legs_to_create and body.description:
        try:
            desc = _json.loads(body.description)
            for fl in desc.get("legs", []):
                inst = fl.get("instrument", {})
                inst_type = inst.get("instrumentType", "")
                # Only set option_type for CE/PE/FUT — not for EQ
                opt_type = inst_type if inst_type in ("CE", "PE", "FUT") else None
                legs_to_create.append(LegCreate(
                    id=fl.get("id"),           # preserve frontend leg UUID
                    symbol=inst.get("symbol", ""),
                    action=fl.get("side", "BUY"),
                    quantity=int(fl.get("quantity", 1)),
                    option_type=opt_type,
                    strike=inst.get("strike"),
                    expiry=inst.get("expiry"),
                    entry_price=fl.get("entryPrice"),
                ))
        except Exception:
            pass

    for leg_data in legs_to_create:
        try:
            leg_id = uuid.UUID(leg_data.id) if leg_data.id else uuid.uuid4()
        except (ValueError, AttributeError):
            leg_id = uuid.uuid4()
        strategy.legs.append(
            StrategyLeg(
                id=leg_id,
                symbol=leg_data.symbol,
                action=leg_data.action,
                quantity=leg_data.quantity,
                option_type=leg_data.option_type,
                strike=leg_data.strike,
                expiry=leg_data.expiry,
                entry_price=leg_data.entry_price,
                status="filled",
            )
        )

    db.add(strategy)
    await db.flush()

    # Subscribe leg symbols to real-time price feed immediately —
    # this is the Day-1 requirement: every leg symbol must have live prices flowing.
    try:
        from services.ltp.ltp_service import ltp_service
        syms = [l.symbol for l in strategy.legs if l.symbol]
        if strategy.underlying and strategy.underlying not in syms:
            syms.append(strategy.underlying)
        if syms:
            await ltp_service.add_symbols(syms)
        # Rebuild dependency graph so any alert rules on this strategy are wired up
        from alerts.alert_pipeline import alert_pipeline
        await alert_pipeline.reload()
    except Exception:
        pass

    # Re-fetch with legs eagerly loaded
    return await _get_or_404(strategy_id, db)


@router.get("/{strategy_id}", response_model=StrategyOut)
async def get_strategy(strategy_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    return await _get_or_404(strategy_id, db)


@router.patch("/{strategy_id}", response_model=StrategyOut)
async def update_strategy(
    strategy_id: uuid.UUID,
    body: StrategyPatch,
    db: AsyncSession = Depends(get_db),
):
    import json as _json
    strategy = await _get_or_404(strategy_id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(strategy, field, value)

    # If description was updated and strategy has no legs, create them from JSON
    if body.description and not strategy.legs:
        try:
            desc = _json.loads(body.description)
            for fl in desc.get("legs", []):
                inst = fl.get("instrument", {})
                if not inst.get("symbol"):
                    continue
                inst_type = inst.get("instrumentType", "")
                opt_type = inst_type if inst_type in ("CE", "PE", "FUT") else None
                try:
                    leg_id = uuid.UUID(fl.get("id")) if fl.get("id") else uuid.uuid4()
                except (ValueError, AttributeError):
                    leg_id = uuid.uuid4()
                strategy.legs.append(StrategyLeg(
                    id=leg_id,
                    symbol=inst.get("symbol", ""),
                    action=fl.get("side", "BUY"),
                    quantity=int(fl.get("quantity", 1)),
                    option_type=opt_type,
                    strike=inst.get("strike"),
                    expiry=inst.get("expiry"),
                    entry_price=fl.get("entryPrice"),
                    status="filled",
                ))
        except Exception:
            pass

    await db.flush()

    # Subscribe any new leg symbols to real-time price feed
    try:
        from services.ltp.ltp_service import ltp_service
        updated = await _get_or_404(strategy_id, db)
        syms = [l.symbol for l in updated.legs if l.symbol]
        if updated.underlying and updated.underlying not in syms:
            syms.append(updated.underlying)
        if syms:
            await ltp_service.add_symbols(syms)
        from alerts.alert_pipeline import alert_pipeline
        await alert_pipeline.reload()
        return updated
    except Exception:
        pass

    return await _get_or_404(strategy_id, db)


@router.delete("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_strategy(strategy_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    from sqlalchemy import update as sa_update
    from alerts.models import AlertRuleBuilder

    strategy = await _get_or_404(strategy_id, db)
    strategy_name = strategy.name

    # Void all alerts for this strategy BEFORE deleting — preserves history.
    # Sets void=True, is_active=False, clears strategy_id (anticipating SET NULL FK),
    # and caches strategy_name so the UI can display "was for <name>".
    await db.execute(
        sa_update(AlertRuleBuilder)
        .where(AlertRuleBuilder.strategy_id == str(strategy_id))
        .values(
            void=True,
            is_active=False,
            strategy_name=strategy_name,
            # strategy_id: PostgreSQL FK SET NULL handles it automatically;
            # SQLite leaves a dangling ref (FK unenforced) — void=True is the
            # reliable signal the strategy is gone on both databases.
        )
    )
    await db.flush()

    await db.delete(strategy)
    # reload alert engine so voided rules are removed from evaluation
    try:
        from alerts.alert_pipeline import alert_pipeline
        await alert_pipeline.reload()
    except Exception:
        pass
