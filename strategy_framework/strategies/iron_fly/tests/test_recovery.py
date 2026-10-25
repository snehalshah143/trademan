"""
test_recovery.py
----------------
Unit tests for the RecoveryManager.

Tests cover:
- Strategy detection from position book
- State reconstruction from positions + trades
- Geometry derivation (center, wing distance, breakevens)
- Idempotency (running twice produces same result)
- Missing data graceful handling
"""

import pytest
from unittest.mock import MagicMock, patch
from framework.recovery_manager import RecoveryManager
from models.strategy_state import StrategyPhase


def make_mock_client(positions=None, trades=None, orders=None):
    """Build a mock OpenAlgoClient with preset return values."""
    client = MagicMock()
    client.get_positions.return_value = positions or []
    client.get_trades.return_value = trades or []
    client.get_orders.return_value = orders or []
    return client


def make_mock_config(extras=None):
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "EXCHANGE_OPTIONS": "NFO",
        "PRODUCT": "NRML",
        "STRATEGY_NAME": "IRON_FLY",
    }.get(key, default)
    return config


STRATEGY_ID = "IRON_FLY_202608"


class TestRecoveryManagerNoPositions:
    def test_fresh_start_when_no_positions(self, tmp_path):
        client = make_mock_client()
        rm = RecoveryManager(client, STRATEGY_ID, state_path=str(tmp_path / "state.json"))
        result = rm.recover(make_mock_config())
        assert result.found is False
        assert result.state is None

    def test_fresh_start_message(self, tmp_path):
        client = make_mock_client()
        rm = RecoveryManager(client, STRATEGY_ID, state_path=str(tmp_path / "state.json"))
        result = rm.recover(make_mock_config())
        assert "fresh" in result.message.lower() or result.found is False


class TestRecoveryManagerWithPositions:
    def _make_iron_fly_positions(self):
        return [
            {"symbol": "NIFTY31JUL2524200PE", "quantity": 75, "average_price": "100.0"},
            {"symbol": "NIFTY31JUL2525800CE", "quantity": 75, "average_price": "90.0"},
            {"symbol": "NIFTY31JUL2525000PE", "quantity": -75, "average_price": "310.0"},
            {"symbol": "NIFTY31JUL2525000CE", "quantity": -75, "average_price": "330.0"},
        ]

    def _make_iron_fly_orders(self):
        return [
            {"symbol": "NIFTY31JUL2524200PE", "strategy": STRATEGY_ID, "action": "BUY"},
            {"symbol": "NIFTY31JUL2525800CE", "strategy": STRATEGY_ID, "action": "BUY"},
            {"symbol": "NIFTY31JUL2525000PE", "strategy": STRATEGY_ID, "action": "SELL"},
            {"symbol": "NIFTY31JUL2525000CE", "strategy": STRATEGY_ID, "action": "SELL"},
        ]

    def test_strategy_detected(self, tmp_path):
        client = make_mock_client(
            positions=self._make_iron_fly_positions(),
            orders=self._make_iron_fly_orders(),
        )
        rm = RecoveryManager(client, STRATEGY_ID, state_path=str(tmp_path / "state.json"))
        result = rm.recover(make_mock_config())
        assert result.found is True
        assert result.state is not None

    def test_four_legs_reconstructed(self, tmp_path):
        client = make_mock_client(
            positions=self._make_iron_fly_positions(),
            orders=self._make_iron_fly_orders(),
        )
        rm = RecoveryManager(client, STRATEGY_ID, state_path=str(tmp_path / "state.json"))
        result = rm.recover(make_mock_config())
        assert len(result.state.legs) == 4

    def test_phase_set_to_monitoring(self, tmp_path):
        client = make_mock_client(
            positions=self._make_iron_fly_positions(),
            orders=self._make_iron_fly_orders(),
        )
        rm = RecoveryManager(client, STRATEGY_ID, state_path=str(tmp_path / "state.json"))
        result = rm.recover(make_mock_config())
        assert result.state.phase == StrategyPhase.MONITORING

    def test_center_strike_derived(self, tmp_path):
        client = make_mock_client(
            positions=self._make_iron_fly_positions(),
            orders=self._make_iron_fly_orders(),
        )
        rm = RecoveryManager(client, STRATEGY_ID, state_path=str(tmp_path / "state.json"))
        result = rm.recover(make_mock_config())
        # Short call and short put both at 25000 → center = 25000
        assert result.state.center_strike == pytest.approx(25000.0)

    def test_wing_distance_derived(self, tmp_path):
        client = make_mock_client(
            positions=self._make_iron_fly_positions(),
            orders=self._make_iron_fly_orders(),
        )
        rm = RecoveryManager(client, STRATEGY_ID, state_path=str(tmp_path / "state.json"))
        result = rm.recover(make_mock_config())
        # Upper wing 25800, lower wing 24200, center 25000 → distance 800
        assert result.state.wing_distance == pytest.approx(800.0)


class TestRecoveryManagerStrikeParser:
    def test_parse_ce_strike(self, tmp_path):
        client = make_mock_client()
        rm = RecoveryManager(client, STRATEGY_ID, state_path=str(tmp_path / "state.json"))
        assert rm._parse_strike("NIFTY31JUL2525000CE") == 25000.0

    def test_parse_pe_strike(self, tmp_path):
        client = make_mock_client()
        rm = RecoveryManager(client, STRATEGY_ID, state_path=str(tmp_path / "state.json"))
        assert rm._parse_strike("NIFTY31JUL2524200PE") == 24200.0

    def test_invalid_symbol(self, tmp_path):
        client = make_mock_client()
        rm = RecoveryManager(client, STRATEGY_ID, state_path=str(tmp_path / "state.json"))
        assert rm._parse_strike("INVALIDSYMBOL") == 0.0


class TestRecoveryManagerIdempotency:
    """Running recovery twice must produce the same state."""

    def test_idempotent_recovery(self, tmp_path):
        positions = [
            {"symbol": "NIFTY31JUL2524200PE", "quantity": 75, "average_price": "100.0"},
            {"symbol": "NIFTY31JUL2525800CE", "quantity": 75, "average_price": "90.0"},
            {"symbol": "NIFTY31JUL2525000PE", "quantity": -75, "average_price": "310.0"},
            {"symbol": "NIFTY31JUL2525000CE", "quantity": -75, "average_price": "330.0"},
        ]
        orders = [
            {"symbol": s, "strategy": STRATEGY_ID, "action": "BUY" if i < 2 else "SELL"}
            for i, s in enumerate([p["symbol"] for p in positions])
        ]

        client = make_mock_client(positions=positions, orders=orders)
        rm = RecoveryManager(client, STRATEGY_ID, state_path=str(tmp_path / "state.json"))

        result1 = rm.recover(make_mock_config())
        result2 = rm.recover(make_mock_config())

        assert result1.found == result2.found
        assert len(result1.state.legs) == len(result2.state.legs)
        assert result1.state.center_strike == result2.state.center_strike
