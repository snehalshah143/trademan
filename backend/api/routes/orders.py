"""
Order history endpoints.

GET /api/v1/orders             — list orders from DB (newest first)
GET /api/v1/orders/{order_id}  — single order + live broker status refresh
"""
import uuid
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.adapter_factory import get_adapter
from core.database import get_db
from models.relational import Order, OrderStatus

router = APIRouter(prefix="/orders", tags=["orders"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id:               uuid.UUID
    strategy_id:      uuid.UUID
    leg_id:           Optional[uuid.UUID]
    broker_order_id:  Optional[str]
    symbol:           str
    action:           str
    quantity:         int
    order_type:       str
    price:            Optional[float]
    status:           str
    filled_price:     Optional[float]
    created_at:       datetime
    updated_at:       datetime


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[OrderOut])
async def list_orders(
    strategy_id: Optional[uuid.UUID] = Query(default=None),
    limit: int = Query(default=100, le=500),
    db: AsyncSession = Depends(get_db),
):
    """Return all orders from DB, newest first.  Filter by strategy_id if provided."""
    stmt = select(Order).order_by(Order.created_at.desc()).limit(limit)
    if strategy_id:
        stmt = stmt.where(Order.strategy_id == strategy_id)
    result = await db.execute(stmt)
    return result.scalars().all()


@router.get("/{order_id}", response_model=OrderOut)
async def get_order(order_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    """
    Return a single order.  If it has a broker_order_id and is not yet terminal,
    fetch the live status from the broker and update the DB record.
    """
    result = await db.execute(select(Order).where(Order.id == order_id))
    order = result.scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Order not found")

    # Refresh from broker if non-terminal and we have a broker ID
    terminal = {OrderStatus.FILLED, OrderStatus.REJECTED, OrderStatus.CANCELLED}
    if order.broker_order_id and order.status not in terminal:
        try:
            adapter = get_adapter()
            live = await adapter.get_order_status(order.broker_order_id)
            new_status = live.get("status", order.status)
            if new_status != order.status:
                order.status = new_status
                if live.get("filled_price") is not None:
                    order.filled_price = live["filled_price"]
                await db.flush()
        except Exception:
            pass  # best-effort — return stale DB value if broker unreachable

    return order
