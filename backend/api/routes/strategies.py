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

    # Idempotent: if a strategy with this ID already exists just return it
    existing_res = await db.execute(
        select(Strategy).options(selectinload(Strategy.legs)).where(Strategy.id == strategy_id)
    )
    existing = existing_res.scalar_one_or_none()
    if existing is not None:
        return existing

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
        strategy.legs.append(
            StrategyLeg(
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
    strategy = await _get_or_404(strategy_id, db)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(strategy, field, value)
    await db.flush()
    return await _get_or_404(strategy_id, db)


@router.delete("/{strategy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_strategy(strategy_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    strategy = await _get_or_404(strategy_id, db)
    await db.delete(strategy)
