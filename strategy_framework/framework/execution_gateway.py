"""
execution_gateway.py
--------------------
Routes order execution to either the PaperEngine or OpenAlgo (LIVE).

Responsibility
--------------
- Be the single point through which ALL orders pass.
- In PAPER mode  → delegate to PaperEngine; return a virtual order ID.
- In LIVE mode   → call OpenAlgoClient; poll for fill; return broker order ID.
- Enforce the global BUY-first / verify / SELL-second rule.
- Handle order retries (MAX_ORDER_RETRY).
- Raise ``ExecutionError`` on unrecoverable failures (after all retries).

Business logic NEVER calls OpenAlgoClient directly — it always goes through
this gateway.
"""

from __future__ import annotations

import time
from typing import Optional

from ..framework.openalgo_client import OpenAlgoClient, OpenAlgoError
from ..models.option_leg import LegAction, LegStatus, OptionLeg
from ..models.strategy_context import StrategyContext
from ..paper.paper_engine import PaperEngine
from ..utils.constants import (
    EXECUTION_MODE_LIVE,
    EXECUTION_MODE_PAPER,
    FILLED_STATUS,
    ORDER_FILL_TIMEOUT_SECONDS,
    ORDER_POLL_INTERVAL_SECONDS,
    TERMINAL_ORDER_STATUSES,
)
from ..utils.logger import get_logger

logger = get_logger(__name__)


class ExecutionError(Exception):
    """Raised when an order cannot be filled after all retries."""


class OrderResult:
    """Result of a single order placement attempt."""

    def __init__(
        self,
        order_id: str,
        symbol: str,
        action: str,
        fill_price: float,
        filled_qty: int,
        status: str,
    ) -> None:
        self.order_id = order_id
        self.symbol = symbol
        self.action = action
        self.fill_price = fill_price
        self.filled_qty = filled_qty
        self.status = status
        self.success = status == FILLED_STATUS

    def __str__(self) -> str:
        return (
            f"OrderResult(id={self.order_id} {self.action} {self.symbol} "
            f"qty={self.filled_qty} price={self.fill_price:.2f} status={self.status})"
        )


class ExecutionGateway:
    """
    Routes orders to PAPER or LIVE execution.

    Parameters
    ----------
    mode        : ``"PAPER"`` or ``"LIVE"``
    client      : ``OpenAlgoClient`` instance (used in LIVE mode)
    paper       : ``PaperEngine`` instance (used in PAPER mode)
    max_retry   : Maximum order placement attempts on failure
    poll_interval : Seconds between order status polls (LIVE mode)
    fill_timeout  : Seconds to wait for a fill before giving up (LIVE mode)
    """

    def __init__(
        self,
        mode: str,
        client: OpenAlgoClient,
        paper: PaperEngine,
        max_retry: int = 3,
        poll_interval: float = ORDER_POLL_INTERVAL_SECONDS,
        fill_timeout: float = ORDER_FILL_TIMEOUT_SECONDS,
    ) -> None:
        if mode not in (EXECUTION_MODE_PAPER, EXECUTION_MODE_LIVE):
            raise ValueError(f"Invalid execution mode: {mode!r}")

        self.mode = mode
        self._client = client
        self._paper = paper
        self._max_retry = max_retry
        self._poll_interval = poll_interval
        self._fill_timeout = fill_timeout

        logger.info(f"ExecutionGateway initialised — mode={mode}")

    @property
    def is_paper(self) -> bool:
        return self.mode == EXECUTION_MODE_PAPER

    # ---------------------------------------------------------------------------
    # Public API — used by deployment, adjustment, exit logic
    # ---------------------------------------------------------------------------

    def execute_leg(
        self,
        leg: OptionLeg,
        strategy_id: str,
        context: StrategyContext,
    ) -> OrderResult:
        """
        Place an order for *leg* and wait for fill confirmation.

        Updates ``leg.order_id``, ``leg.entry_price``, ``leg.status`` in-place.

        Raises ``ExecutionError`` if the order cannot be filled after retries.
        """
        ltp = self._get_ltp_for_leg(leg, context)

        for attempt in range(1, self._max_retry + 1):
            try:
                result = self._place_single(leg, strategy_id, ltp)
                if result.success:
                    leg.order_id = result.order_id
                    leg.entry_price = result.fill_price
                    leg.status = LegStatus.OPEN
                    from datetime import datetime
                    leg.entry_time = datetime.now()
                    logger.info(f"Leg filled (attempt {attempt}): {result}")
                    return result
                else:
                    logger.warning(
                        f"Order not filled (attempt {attempt}/{self._max_retry}): "
                        f"{result.status} — {leg.symbol}"
                    )
            except (OpenAlgoError, Exception) as exc:
                logger.warning(f"Order attempt {attempt} failed: {exc}")

            if attempt < self._max_retry:
                time.sleep(self._poll_interval)

        leg.status = LegStatus.FAILED
        raise ExecutionError(
            f"Failed to fill {leg.action.value} {leg.symbol} after {self._max_retry} attempts."
        )

    def execute_legs_buy_first(
        self,
        legs: list[OptionLeg],
        strategy_id: str,
        context: StrategyContext,
    ) -> list[OrderResult]:
        """
        Execute a list of legs following the global BUY-first rule.

        Sequence:
        1. Execute all BUY legs; verify each fill.
        2. If any BUY fails → raise ExecutionError (caller must handle cleanup).
        3. Execute all SELL legs; verify each fill.

        Returns a list of OrderResult in execution order.
        """
        buy_legs = [leg for leg in legs if leg.action == LegAction.BUY]
        sell_legs = [leg for leg in legs if leg.action == LegAction.SELL]

        results: list[OrderResult] = []

        # Step 1: BUY legs
        logger.info(f"Executing {len(buy_legs)} BUY leg(s) …")
        for leg in buy_legs:
            result = self.execute_leg(leg, strategy_id, context)
            results.append(result)

        # Step 2: SELL legs (only if all BUYs succeeded)
        logger.info(f"Executing {len(sell_legs)} SELL leg(s) …")
        for leg in sell_legs:
            result = self.execute_leg(leg, strategy_id, context)
            results.append(result)

        return results

    def close_leg(
        self,
        leg: OptionLeg,
        strategy_id: str,
        context: StrategyContext,
    ) -> OrderResult:
        """
        Close (exit) *leg* by placing the opposite-direction order.

        For a SELL leg → place BUY to close.
        For a BUY leg  → place SELL to close.

        Updates ``leg.exit_price``, ``leg.exit_time``, ``leg.status`` in-place.
        """
        close_action = LegAction.BUY if leg.is_short else LegAction.SELL
        ltp = self._get_ltp_for_leg(leg, context)

        close_leg = OptionLeg(
            role=leg.role,
            symbol=leg.symbol,
            exchange=leg.exchange,
            strike=leg.strike,
            option_type=leg.option_type,
            action=close_action,
            quantity=leg.quantity,
            product=leg.product,
            expiry=leg.expiry,
        )

        result = self.execute_leg(close_leg, strategy_id, context)
        if result.success:
            leg.exit_price = result.fill_price
            from datetime import datetime
            leg.exit_time = datetime.now()
            leg.status = LegStatus.CLOSED

        return result

    # ---------------------------------------------------------------------------
    # Internal — single order placement
    # ---------------------------------------------------------------------------

    def _place_single(
        self,
        leg: OptionLeg,
        strategy_id: str,
        ltp: float,
    ) -> OrderResult:
        """Place one order and wait for fill. Mode-aware."""
        if self.is_paper:
            return self._place_paper(leg, strategy_id, ltp)
        return self._place_live(leg, strategy_id)

    def _place_paper(self, leg: OptionLeg, strategy_id: str, ltp: float) -> OrderResult:
        """Simulate the order in PaperEngine and return immediate fill."""
        order_id = self._paper.place_order(
            strategy=strategy_id,
            symbol=leg.symbol,
            action=leg.action.value,
            exchange=leg.exchange,
            quantity=leg.quantity,
            ltp=ltp,
            product=leg.product,
        )
        return OrderResult(
            order_id=order_id,
            symbol=leg.symbol,
            action=leg.action.value,
            fill_price=ltp,
            filled_qty=leg.quantity,
            status=FILLED_STATUS,
        )

    def _place_live(self, leg: OptionLeg, strategy_id: str) -> OrderResult:
        """Place a real order via OpenAlgo and poll until filled or timeout."""
        order_id = self._client.place_order(
            strategy=strategy_id,
            symbol=leg.symbol,
            action=leg.action.value,
            exchange=leg.exchange,
            quantity=leg.quantity,
            product=leg.product,
        )

        # Poll for fill
        fill_price, filled_qty, status = self._poll_for_fill(order_id, strategy_id)
        return OrderResult(
            order_id=order_id,
            symbol=leg.symbol,
            action=leg.action.value,
            fill_price=fill_price,
            filled_qty=filled_qty,
            status=status,
        )

    def _poll_for_fill(
        self, order_id: str, strategy_id: str
    ) -> tuple[float, int, str]:
        """
        Poll ``orderstatus`` until the order reaches a terminal state or times out.

        Returns (fill_price, filled_qty, status).
        """
        deadline = time.time() + self._fill_timeout
        while time.time() < deadline:
            try:
                data = self._client.get_order_status(order_id=order_id, strategy=strategy_id)
                status = str(data.get("order_status", "")).lower()
                if status in TERMINAL_ORDER_STATUSES:
                    fill_price = float(data.get("price", 0.0))
                    filled_qty = int(data.get("quantity", 0))
                    logger.debug(
                        f"Order {order_id} terminal: status={status} "
                        f"price={fill_price} qty={filled_qty}"
                    )
                    return fill_price, filled_qty, status
            except OpenAlgoError as exc:
                logger.warning(f"orderstatus poll error for {order_id}: {exc}")

            time.sleep(self._poll_interval)

        logger.error(f"Order {order_id} did not reach terminal status within {self._fill_timeout}s")
        return 0.0, 0, "timeout"

    # ---------------------------------------------------------------------------
    # LTP lookup
    # ---------------------------------------------------------------------------

    def _get_ltp_for_leg(self, leg: OptionLeg, context: StrategyContext) -> float:
        """
        Return the current LTP for *leg.symbol* from the strategy context.

        Falls back to 0.0 with a warning if the symbol is not in the chain.
        """
        entry = context.get_chain_entry(leg.strike)
        if entry:
            ltp = entry.ce_ltp if leg.option_type.value == "CE" else entry.pe_ltp
            if ltp is not None:
                return ltp

        logger.warning(
            f"LTP not found in option chain for {leg.symbol} (strike={leg.strike}). "
            f"Using 0.0 — order may be placed at wrong price."
        )
        return 0.0
