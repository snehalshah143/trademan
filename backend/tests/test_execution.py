"""
Unit tests for ExecutionService — BUY-first entry and SELL-first exit sequencing.

Uses in-memory SQLite (via conftest) and a controlled MockAdapter double.
"""
import uuid
from typing import Any, Dict, List, Optional

import pytest
from sqlalchemy import select

from models.relational import LegStatus, Strategy, StrategyLeg, StrategyStatus
from services.execution.execution_service import ExecutionService


# ── Controlled adapter double ─────────────────────────────────────────────────

class _ControlledAdapter:
    """Records placed orders and simulates instant fills or rejections."""

    def __init__(self, *, reject_actions: Optional[set] = None) -> None:
        self._connected = True
        self._reject_actions: set = reject_actions or set()
        self._orders: Dict[str, Dict] = {}
        self.placed_calls: List[Dict] = []   # [{symbol, action, broker_id}, ...]

    @property
    def is_connected(self) -> bool:
        return self._connected

    async def connect(self) -> None: ...
    async def disconnect(self) -> None: ...
    async def subscribe_ws(self, callback) -> None: ...
    async def get_ltp(self, symbol: str) -> float: return 100.0
    async def get_ltp_bulk(self, symbols): return {s: 100.0 for s in symbols}

    async def place_order(
        self, symbol: str, action: str, quantity: int, **kwargs
    ) -> str:
        broker_id = f"TEST-{uuid.uuid4().hex[:8].upper()}"
        status = "rejected" if action in self._reject_actions else "filled"
        self._orders[broker_id] = {
            "status": status,
            "filled_price": 100.0 if status == "filled" else None,
        }
        self.placed_calls.append({"symbol": symbol, "action": action, "broker_id": broker_id})
        return broker_id

    async def get_order_status(self, order_id: str) -> Dict[str, Any]:
        return self._orders.get(
            order_id, {"status": "pending", "filled_price": None, "message": "Unknown"}
        )

    async def get_positions(self) -> List[Dict[str, Any]]: return []
    async def get_funds(self) -> Dict[str, Any]:
        return {"available": 1_000_000.0, "used": 0.0, "total": 1_000_000.0}


# ── DB seed helpers ───────────────────────────────────────────────────────────

async def _seed_strategy(
    session, *, buy_count: int = 2, sell_count: int = 2, active: bool = False
) -> Strategy:
    strategy = Strategy(
        name="Test",
        status=StrategyStatus.ACTIVE if active else StrategyStatus.DRAFT,
        underlying="NIFTY",
    )
    session.add(strategy)
    await session.flush()   # populate strategy.id before FK inserts

    for i in range(buy_count):
        session.add(StrategyLeg(
            strategy_id=strategy.id,
            symbol=f"BUY_{i}",
            action="BUY",
            quantity=50,
            status=LegStatus.FILLED if active else LegStatus.PENDING,
            entry_price=100.0 if active else None,
        ))

    for i in range(sell_count):
        session.add(StrategyLeg(
            strategy_id=strategy.id,
            symbol=f"SELL_{i}",
            action="SELL",
            quantity=50,
            status=LegStatus.FILLED if active else LegStatus.PENDING,
            entry_price=100.0 if active else None,
        ))

    await session.commit()
    return strategy


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_entry_buy_first_then_sell(db_session, monkeypatch):
    """BUY legs must be placed and fully settled before SELL legs are submitted."""
    adapter = _ControlledAdapter()
    monkeypatch.setattr("services.execution.execution_service.get_adapter", lambda: adapter)

    strategy = await _seed_strategy(db_session, buy_count=2, sell_count=2)
    result = await ExecutionService().execute_entry(strategy.id)

    assert result.success is True
    assert len(adapter.placed_calls) == 4

    buy_idx  = [i for i, c in enumerate(adapter.placed_calls) if c["action"] == "BUY"]
    sell_idx = [i for i, c in enumerate(adapter.placed_calls) if c["action"] == "SELL"]
    assert buy_idx and sell_idx
    assert max(buy_idx) < min(sell_idx), "All BUY orders must precede SELL orders"

    # All legs must have a broker_order_id after successful entry
    legs = (await db_session.execute(
        select(StrategyLeg).where(StrategyLeg.strategy_id == strategy.id)
    )).scalars().all()
    assert all(l.broker_order_id is not None for l in legs)


@pytest.mark.asyncio
async def test_entry_aborts_if_buy_fails(db_session, monkeypatch):
    """When any BUY leg is rejected, SELL legs must never be placed."""
    adapter = _ControlledAdapter(reject_actions={"BUY"})
    monkeypatch.setattr("services.execution.execution_service.get_adapter", lambda: adapter)

    strategy = await _seed_strategy(db_session, buy_count=2, sell_count=2)
    result = await ExecutionService().execute_entry(strategy.id)

    assert result.success is False
    assert "BUY legs rejected" in result.error

    sell_calls = [c for c in adapter.placed_calls if c["action"] == "SELL"]
    assert len(sell_calls) == 0, "SELL legs must never be placed after BUY rejection"

    await db_session.refresh(strategy)
    assert strategy.status != StrategyStatus.ACTIVE


@pytest.mark.asyncio
async def test_exit_sell_first_then_buy(db_session, monkeypatch):
    """SELL-leg exits (buy-backs) must be placed before BUY-leg exits (sell-outs)."""
    adapter = _ControlledAdapter()
    monkeypatch.setattr("services.execution.execution_service.get_adapter", lambda: adapter)

    # 1 SELL leg + 1 BUY leg, both already filled (post-entry state)
    strategy = await _seed_strategy(db_session, buy_count=1, sell_count=1, active=True)
    result = await ExecutionService().execute_exit(strategy.id)

    assert result.success is True
    assert len(adapter.placed_calls) == 2

    # SELL-leg exit → BUY order (cover short) first
    # BUY-leg exit  → SELL order (unwind long) second
    assert adapter.placed_calls[0]["action"] == "BUY",  "SELL-leg exit (buy-back) must be first"
    assert adapter.placed_calls[1]["action"] == "SELL", "BUY-leg exit (sell-out) must be second"


@pytest.mark.asyncio
async def test_partial_exit(db_session, monkeypatch):
    """Providing leg_ids must exit only the specified legs."""
    adapter = _ControlledAdapter()
    monkeypatch.setattr("services.execution.execution_service.get_adapter", lambda: adapter)

    # 2 BUY legs, both filled
    strategy = await _seed_strategy(db_session, buy_count=2, sell_count=0, active=True)

    legs = (await db_session.execute(
        select(StrategyLeg).where(StrategyLeg.strategy_id == strategy.id)
    )).scalars().all()
    target_leg_id = legs[0].id

    result = await ExecutionService().execute_exit(strategy.id, leg_ids=[target_leg_id])

    assert result.success is True
    assert len(adapter.placed_calls) == 1, "Only the specified leg should be exited"
    assert len(result.filled_orders) == 1
