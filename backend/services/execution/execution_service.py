"""
ExecutionService — order placement following the mandatory BUY-first sequencing.

ENTRY:
  1. Place all BUY legs simultaneously (asyncio.gather)
  2. Poll fills every 500 ms, 30-second timeout
  3. If any BUY rejected → abort, do NOT place SELL legs
  4. Place all SELL legs simultaneously
  5. Persist order records + set strategy status → ACTIVE

EXIT (full or by leg_ids):
  1. Exit SELL legs simultaneously (buy back)
  2. Poll fills
  3. Exit BUY legs simultaneously (sell out)
  4. Set strategy status → CLOSED (or PARTIAL_EXIT)
"""
import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from adapters.adapter_factory import get_adapter
from core.database import AsyncSessionLocal
from models.relational import (
    LegStatus,
    Order,
    OrderStatus,
    OrderType,
    Strategy,
    StrategyLeg,
    StrategyStatus,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL = 0.5      # seconds between order-status polls
FILL_TIMEOUT  = 30.0     # seconds before we give up


@dataclass
class ExecutionResult:
    success: bool
    error:   Optional[str] = None
    filled_orders: List[Dict] = field(default_factory=list)


class ExecutionService:
    def __init__(self) -> None:
        self._adapter = get_adapter()

    # ── Entry ─────────────────────────────────────────────────────────────────

    async def execute_entry(self, strategy_id: uuid.UUID) -> ExecutionResult:
        """Place entry orders per the BUY-first protocol."""
        async with AsyncSessionLocal() as session:
            strategy = await self._load_strategy(session, strategy_id)
            if strategy is None:
                return ExecutionResult(success=False, error="Strategy not found")

            buy_legs  = [l for l in strategy.legs if l.action == "BUY"]
            sell_legs = [l for l in strategy.legs if l.action == "SELL"]

            # Step 1: Place all BUY legs simultaneously
            logger.info("[Execution] entry %s — placing %d BUY legs", strategy_id, len(buy_legs))
            buy_ids = await asyncio.gather(
                *[self._place(leg) for leg in buy_legs],
                return_exceptions=True,
            )

            # Step 2: Poll BUY fills
            buy_map = {
                leg.id: oid
                for leg, oid in zip(buy_legs, buy_ids)
                if isinstance(oid, str)
            }
            fills = await self._poll_fills(buy_map)

            # Step 3: Abort if any BUY rejected
            rejected = [k for k, v in fills.items() if v["status"] != "filled"]
            if rejected:
                logger.error("[Execution] BUY rejected for legs %s — aborting", rejected)
                return ExecutionResult(
                    success=False,
                    error=f"BUY legs rejected: {rejected}",
                )

            # Step 4: Place all SELL legs simultaneously
            logger.info("[Execution] entry %s — placing %d SELL legs", strategy_id, len(sell_legs))
            sell_ids = await asyncio.gather(
                *[self._place(leg) for leg in sell_legs],
                return_exceptions=True,
            )
            sell_map = {
                leg.id: oid
                for leg, oid in zip(sell_legs, sell_ids)
                if isinstance(oid, str)
            }
            sell_fills = await self._poll_fills(sell_map)
            fills.update(sell_fills)

            # Step 5: Persist to DB
            all_orders = await self._persist_entry(session, strategy, fills)
            strategy.status = StrategyStatus.ACTIVE
            await session.commit()

            logger.info("[Execution] entry %s complete — %d orders", strategy_id, len(all_orders))
            return ExecutionResult(success=True, filled_orders=all_orders)

    # ── Exit ──────────────────────────────────────────────────────────────────

    async def execute_exit(
        self,
        strategy_id: uuid.UUID,
        leg_ids: Optional[List[uuid.UUID]] = None,
    ) -> ExecutionResult:
        """Place exit orders per the SELL-exit-first protocol."""
        async with AsyncSessionLocal() as session:
            strategy = await self._load_strategy(session, strategy_id)
            if strategy is None:
                return ExecutionResult(success=False, error="Strategy not found")

            filled_legs = [l for l in strategy.legs if l.status == LegStatus.FILLED]
            if leg_ids:
                filled_legs = [l for l in filled_legs if l.id in leg_ids]

            # Reverse: SELL legs exit as BUY-back, BUY legs exit as SELL-out
            sell_legs = [l for l in filled_legs if l.action == "SELL"]  # exit → BUY back
            buy_legs  = [l for l in filled_legs if l.action == "BUY"]   # exit → SELL out

            all_fills: Dict[uuid.UUID, Dict] = {}

            # Step 1: Exit SELL legs (buy back) simultaneously
            if sell_legs:
                exit_sell_ids = await asyncio.gather(
                    *[self._place_exit(leg) for leg in sell_legs],
                    return_exceptions=True,
                )
                exit_sell_map = {
                    leg.id: oid
                    for leg, oid in zip(sell_legs, exit_sell_ids)
                    if isinstance(oid, str)
                }
                all_fills.update(await self._poll_fills(exit_sell_map))

            # Step 2: Exit BUY legs (sell out) simultaneously
            if buy_legs:
                exit_buy_ids = await asyncio.gather(
                    *[self._place_exit(leg) for leg in buy_legs],
                    return_exceptions=True,
                )
                exit_buy_map = {
                    leg.id: oid
                    for leg, oid in zip(buy_legs, exit_buy_ids)
                    if isinstance(oid, str)
                }
                all_fills.update(await self._poll_fills(exit_buy_map))

            # Persist
            orders = await self._persist_exit(session, strategy, filled_legs, all_fills)
            is_full_exit = leg_ids is None or len(leg_ids) == len(
                [l for l in strategy.legs if l.status == LegStatus.FILLED]
            )
            strategy.status = StrategyStatus.CLOSED if is_full_exit else "partial_exit"
            await session.commit()

            logger.info("[Execution] exit %s complete — %d orders", strategy_id, len(orders))
            return ExecutionResult(success=True, filled_orders=orders)

    # ── Internal helpers ──────────────────────────────────────────────────────

    async def _place(self, leg: StrategyLeg) -> str:
        return await self._adapter.place_order(
            symbol=leg.symbol,
            action=leg.action,
            quantity=leg.quantity,
        )

    async def _place_exit(self, leg: StrategyLeg) -> str:
        exit_action = "BUY" if leg.action == "SELL" else "SELL"
        return await self._adapter.place_order(
            symbol=leg.symbol,
            action=exit_action,
            quantity=leg.quantity,
        )

    async def _poll_fills(
        self,
        order_map: Dict[uuid.UUID, str],   # leg_id → broker_order_id
    ) -> Dict[uuid.UUID, Dict]:
        """Poll until all orders settle or 30-second timeout."""
        deadline  = asyncio.get_event_loop().time() + FILL_TIMEOUT
        results: Dict[uuid.UUID, Dict] = {}
        pending  = dict(order_map)

        while pending and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(POLL_INTERVAL)
            for leg_id, broker_id in list(pending.items()):
                status = await self._adapter.get_order_status(broker_id)
                if status["status"] in ("filled", "rejected", "cancelled"):
                    results[leg_id] = {**status, "broker_order_id": broker_id}
                    del pending[leg_id]

        # Timeout
        for leg_id, broker_id in pending.items():
            results[leg_id] = {
                "status":        "timeout",
                "filled_price":  None,
                "message":       f"30-second fill timeout for {broker_id}",
                "broker_order_id": broker_id,
            }
        return results

    async def _persist_entry(
        self,
        session,
        strategy: Strategy,
        fills: Dict[uuid.UUID, Dict],
    ) -> List[Dict]:
        orders = []
        for leg in strategy.legs:
            fill = fills.get(leg.id, {})
            broker_id    = fill.get("broker_order_id")
            fill_price   = fill.get("filled_price")
            fill_status  = fill.get("status", "pending")

            # Update leg
            leg.broker_order_id = broker_id
            leg.entry_price     = fill_price
            leg.status          = LegStatus.FILLED if fill_status == "filled" else leg.status

            # Create Order record
            order = Order(
                strategy_id=strategy.id,
                leg_id=leg.id,
                broker_order_id=broker_id,
                symbol=leg.symbol,
                action=leg.action,
                quantity=leg.quantity,
                order_type=OrderType.MARKET,
                status=OrderStatus.FILLED if fill_status == "filled" else fill_status,
                filled_price=fill_price,
            )
            session.add(order)
            orders.append({"leg_id": str(leg.id), "status": fill_status, "fill": fill_price})
        return orders

    async def _persist_exit(
        self,
        session,
        strategy: Strategy,
        exited_legs: List[StrategyLeg],
        fills: Dict[uuid.UUID, Dict],
    ) -> List[Dict]:
        orders = []
        for leg in exited_legs:
            fill = fills.get(leg.id, {})
            broker_id   = fill.get("broker_order_id")
            fill_price  = fill.get("filled_price")
            fill_status = fill.get("status", "pending")

            leg.exit_price = fill_price
            leg.status     = LegStatus.CANCELLED  # reusing as "exited"

            exit_action = "BUY" if leg.action == "SELL" else "SELL"
            order = Order(
                strategy_id=strategy.id,
                leg_id=leg.id,
                broker_order_id=broker_id,
                symbol=leg.symbol,
                action=exit_action,
                quantity=leg.quantity,
                order_type=OrderType.MARKET,
                status=OrderStatus.FILLED if fill_status == "filled" else fill_status,
                filled_price=fill_price,
            )
            session.add(order)
            orders.append({"leg_id": str(leg.id), "status": fill_status, "fill": fill_price})
        return orders

    @staticmethod
    async def _load_strategy(session, strategy_id: uuid.UUID) -> Optional[Strategy]:
        result = await session.execute(
            select(Strategy)
            .options(selectinload(Strategy.legs))
            .where(Strategy.id == strategy_id)
        )
        return result.scalar_one_or_none()
