"""
Positions endpoints.

GET /api/v1/positions        — live positions from broker, enriched with strategy_id
GET /api/v1/positions/funds  — account fund summary
"""
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.adapter_factory import get_adapter
from core.database import get_db
from models.relational import Strategy, StrategyLeg

router = APIRouter(prefix="/positions", tags=["positions"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class PositionOut(BaseModel):
    symbol:      str
    exchange:    str
    qty:         int
    buy_avg:     float
    sell_avg:    float
    pnl:         float
    ltp:         float = 0.0
    product:     str
    strategy_id: Optional[uuid.UUID] = None
    leg_id:      Optional[uuid.UUID] = None


class FundsOut(BaseModel):
    available: float
    used:      float
    total:     float


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[PositionOut])
async def get_positions(db: AsyncSession = Depends(get_db)):
    """
    Fetch live open positions from the broker and attach strategy/leg context
    by matching symbol to active strategy legs in the DB.
    """
    adapter = get_adapter()
    raw_positions = await adapter.get_positions()

    if not raw_positions:
        return []

    # Build lookup: symbol → (strategy_id, leg_id) from active strategies
    result = await db.execute(
        select(StrategyLeg, Strategy.id.label("strategy_id"))
        .join(Strategy, Strategy.id == StrategyLeg.strategy_id)
        .where(Strategy.status == "active")
    )
    rows = result.all()
    symbol_map: Dict[str, Dict[str, Any]] = {}
    for leg, strategy_id in rows:
        symbol_map[leg.symbol] = {
            "strategy_id": strategy_id,
            "leg_id":      leg.id,
        }

    enriched = []
    for p in raw_positions:
        ctx = symbol_map.get(p["symbol"], {})
        enriched.append(
            PositionOut(
                symbol=p["symbol"],
                exchange=p.get("exchange", "NFO"),
                qty=int(p.get("qty", 0)),
                buy_avg=float(p.get("buy_avg", 0)),
                sell_avg=float(p.get("sell_avg", 0)),
                pnl=float(p.get("pnl", 0)),
                ltp=float(p.get("ltp", 0)),
                product=p.get("product", "MIS"),
                strategy_id=ctx.get("strategy_id"),
                leg_id=ctx.get("leg_id"),
            )
        )
    return enriched


@router.get("/funds", response_model=FundsOut)
async def get_funds():
    """Return account fund summary from the broker."""
    adapter = get_adapter()
    funds = await adapter.get_funds()
    return FundsOut(
        available=funds.get("available", 0.0),
        used=funds.get("used", 0.0),
        total=funds.get("total", 0.0),
    )
