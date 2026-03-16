"""
Unit tests for AlertService rule evaluation.

Strategies are built in memory (no DB insert required for evaluation logic).
AlertEvent inserts go through the patched AsyncSessionLocal → in-memory SQLite.
"""
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from models.relational import (
    AlertEvent,
    AlertSeverity,
    Strategy,
    StrategyAutomationConfig,
    StrategyLeg,
    StrategyStatus,
)
from services.alert.alert_service import AlertService

_svc = AlertService()


# ── Strategy builder ──────────────────────────────────────────────────────────

def _strategy(rules: list, *, sell_entry: float = 100.0, buy_entry: float = 50.0, qty: int = 50) -> Strategy:
    """
    Build a transient Strategy with one SELL leg and one BUY leg.

    initial_credit = sell_entry * qty  (premium received on SELL leg)
    """
    s = Strategy(name="Test", underlying="NIFTY", status=StrategyStatus.ACTIVE)
    s.id = uuid.uuid4()

    sell_leg = StrategyLeg(
        strategy_id=s.id,
        symbol="NIFTY_PE",
        action="SELL",
        quantity=qty,
        entry_price=sell_entry,
        status="filled",
    )
    sell_leg.id = uuid.uuid4()

    buy_leg = StrategyLeg(
        strategy_id=s.id,
        symbol="NIFTY_CE",
        action="BUY",
        quantity=qty,
        entry_price=buy_entry,
        status="filled",
    )
    buy_leg.id = uuid.uuid4()

    s.legs.append(sell_leg)
    s.legs.append(buy_leg)

    cfg = StrategyAutomationConfig(strategy_id=s.id, enabled=True, alert_rules={"rules": rules})
    cfg.id = uuid.uuid4()
    s.automation_config = cfg

    return s


# ── Tests ─────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mtm_target_fires(mock_redis, db_session):
    """MTM_TARGET alert fires when MTM >= target."""
    # sell_entry=100, qty=50 → initial_credit=5000
    # NIFTY_PE LTP=60 → sell MTM = (100-60)*50 = +2000
    # NIFTY_CE LTP=50 → buy  MTM = (50-50)*50  =     0
    # total MTM = 2000 ≥ target=2000 → fires
    strategy = _strategy(
        [{"id": "tgt", "type": "MTM_TARGET", "value": 2000, "cooldown_seconds": 0}],
    )

    await _svc.evaluate(strategy, current_spot=22500.0, ltp_map={"NIFTY_PE": 60.0, "NIFTY_CE": 50.0})

    alerts = (await db_session.execute(
        select(AlertEvent).where(AlertEvent.strategy_id == strategy.id)
    )).scalars().all()
    assert len(alerts) == 1
    assert alerts[0].rule_id == "tgt"
    assert alerts[0].severity == AlertSeverity.INFO


@pytest.mark.asyncio
async def test_mtm_stoploss_fires(mock_redis, db_session):
    """MTM_SL alert fires when MTM <= stop-loss value."""
    # NIFTY_PE LTP=200 → sell MTM = (100-200)*50 = -5000 ≤ -3000 → fires
    strategy = _strategy(
        [{"id": "sl", "type": "MTM_SL", "value": -3000, "cooldown_seconds": 0}],
    )

    await _svc.evaluate(strategy, current_spot=22500.0, ltp_map={"NIFTY_PE": 200.0, "NIFTY_CE": 50.0})

    alerts = (await db_session.execute(
        select(AlertEvent).where(AlertEvent.strategy_id == strategy.id)
    )).scalars().all()
    assert len(alerts) == 1
    assert alerts[0].severity == AlertSeverity.CRITICAL


@pytest.mark.asyncio
async def test_breakeven_proximity_fires(mock_redis, db_session):
    """BE_PROXIMITY alert fires when credit has nearly fully eroded."""
    # sell_entry=100, qty=50 → initial_credit=5000
    # NIFTY_PE LTP=101 → sell MTM = (100-101)*50 =  -50
    # NIFTY_CE LTP=49  → buy  MTM = (49-50)*50   =  -50
    # total MTM = -100
    # credit_erosion_pct = (5000 - (-100)) / 5000 * 100 = 102% ≥ (100-5)=95% → fires
    strategy = _strategy(
        [{"id": "be", "type": "BE_PROXIMITY", "threshold_pct": 5.0, "cooldown_seconds": 0}],
    )

    await _svc.evaluate(strategy, current_spot=22500.0, ltp_map={"NIFTY_PE": 101.0, "NIFTY_CE": 49.0})

    alerts = (await db_session.execute(
        select(AlertEvent).where(AlertEvent.strategy_id == strategy.id)
    )).scalars().all()
    assert len(alerts) == 1
    assert alerts[0].severity == AlertSeverity.WARNING


@pytest.mark.asyncio
async def test_cooldown_prevents_refiring(mock_redis, db_session):
    """An alert must not re-fire while still within its cooldown window."""
    strategy = _strategy(
        [{"id": "tgt2", "type": "MTM_TARGET", "value": 2000, "cooldown_seconds": 300}],
    )
    ltp_map = {"NIFTY_PE": 60.0, "NIFTY_CE": 50.0}   # MTM = 2000

    # First evaluation — alert fires, cooldown set
    await _svc.evaluate(strategy, current_spot=22500.0, ltp_map=ltp_map)

    # Simulate cooldown: next call to is_in_cooldown returns True
    mock_redis.is_in_cooldown = AsyncMock(return_value=True)

    # Second evaluation — must be suppressed by cooldown
    await _svc.evaluate(strategy, current_spot=22500.0, ltp_map=ltp_map)

    alerts = (await db_session.execute(
        select(AlertEvent).where(AlertEvent.strategy_id == strategy.id)
    )).scalars().all()
    assert len(alerts) == 1, "Alert must fire exactly once — second call blocked by cooldown"
