"""
test_paper_engine.py
--------------------
Unit tests for PaperEngine — virtual order execution and MTM tracking.
"""

import pytest
from unittest.mock import MagicMock, patch
from paper.paper_engine import PaperEngine


@pytest.fixture
def engine(tmp_path):
    """Create a PaperEngine with a temporary snapshot path."""
    return PaperEngine(snapshot_path=str(tmp_path / "paper_state.json"))


class TestPaperEngineOrderPlacement:
    def test_place_buy_order_returns_order_id(self, engine):
        order_id = engine.place_order(
            strategy="TEST", symbol="NIFTY30JUN2525000CE",
            action="BUY", exchange="NFO", quantity=75, ltp=330.0,
        )
        assert order_id.startswith("PO-")

    def test_place_sell_order_creates_short_position(self, engine):
        engine.place_order(
            strategy="TEST", symbol="NIFTY30JUN2525000CE",
            action="SELL", exchange="NFO", quantity=75, ltp=330.0,
        )
        positions = engine.get_positions()
        assert len(positions) == 1
        assert positions[0]["quantity"] == -75   # short position

    def test_place_buy_order_creates_long_position(self, engine):
        engine.place_order(
            strategy="TEST", symbol="NIFTY30JUN2524200PE",
            action="BUY", exchange="NFO", quantity=75, ltp=100.0,
        )
        positions = engine.get_positions()
        assert len(positions) == 1
        assert positions[0]["quantity"] == 75   # long position

    def test_buy_back_short_removes_position(self, engine):
        # Sell first
        engine.place_order(
            strategy="TEST", symbol="NIFTY30JUN2525000CE",
            action="SELL", exchange="NFO", quantity=75, ltp=330.0,
        )
        # Buy back
        engine.place_order(
            strategy="TEST", symbol="NIFTY30JUN2525000CE",
            action="BUY", exchange="NFO", quantity=75, ltp=280.0,
        )
        assert len(engine.get_positions()) == 0

    def test_multiple_orders_accumulate_positions(self, engine):
        engine.place_order("TEST", "NIFTY30JUN2524200PE", "BUY", "NFO", 75, 100.0)
        engine.place_order("TEST", "NIFTY30JUN2525800CE", "BUY", "NFO", 75, 90.0)
        engine.place_order("TEST", "NIFTY30JUN2525000PE", "SELL", "NFO", 75, 310.0)
        engine.place_order("TEST", "NIFTY30JUN2525000CE", "SELL", "NFO", 75, 330.0)
        assert len(engine.get_positions()) == 4


class TestPaperEngineMTM:
    def test_initial_mtm_is_zero(self, engine):
        assert engine.current_mtm == 0.0

    def test_short_position_profit_when_ltp_falls(self, engine):
        # Sell at 330 → LTP falls to 200 → profit = (330 - 200) * 75 = 9750
        engine.place_order("TEST", "NIFTY30JUN2525000CE", "SELL", "NFO", 75, 330.0)
        engine.update_ltps({"NIFTY30JUN2525000CE": 200.0})
        assert engine.current_mtm == pytest.approx(9750.0)

    def test_short_position_loss_when_ltp_rises(self, engine):
        # Sell at 330 → LTP rises to 400 → loss = (330 - 400) * 75 = -5250
        engine.place_order("TEST", "NIFTY30JUN2525000CE", "SELL", "NFO", 75, 330.0)
        engine.update_ltps({"NIFTY30JUN2525000CE": 400.0})
        assert engine.current_mtm == pytest.approx(-5250.0)

    def test_long_position_profit_when_ltp_rises(self, engine):
        # Buy at 100 → LTP rises to 150 → profit = (150 - 100) * 75 = 3750
        engine.place_order("TEST", "NIFTY30JUN2524200PE", "BUY", "NFO", 75, 100.0)
        engine.update_ltps({"NIFTY30JUN2524200PE": 150.0})
        assert engine.current_mtm == pytest.approx(3750.0)

    def test_max_mtm_updated(self, engine):
        engine.place_order("TEST", "NIFTY30JUN2525000CE", "SELL", "NFO", 75, 330.0)
        engine.update_ltps({"NIFTY30JUN2525000CE": 200.0})
        engine.update_mtm_highwater()
        assert engine.max_mtm == pytest.approx(9750.0)
        # MTM falls
        engine.update_ltps({"NIFTY30JUN2525000CE": 300.0})
        engine.update_mtm_highwater()
        # Max should not decrease
        assert engine.max_mtm == pytest.approx(9750.0)

    def test_min_mtm_updated_on_loss(self, engine):
        engine.place_order("TEST", "NIFTY30JUN2525000CE", "SELL", "NFO", 75, 330.0)
        engine.update_ltps({"NIFTY30JUN2525000CE": 500.0})
        engine.update_mtm_highwater()
        assert engine.min_mtm == pytest.approx(-12750.0)


class TestPaperEngineViews:
    def test_get_orders_returns_all_placed(self, engine):
        engine.place_order("TEST", "NIFTY30JUN2525000CE", "SELL", "NFO", 75, 330.0)
        engine.place_order("TEST", "NIFTY30JUN2525000PE", "SELL", "NFO", 75, 310.0)
        orders = engine.get_orders()
        assert len(orders) == 2

    def test_get_trades_mirrors_orders(self, engine):
        engine.place_order("TEST", "NIFTY30JUN2525000CE", "SELL", "NFO", 75, 330.0)
        trades = engine.get_trades()
        assert len(trades) == 1
        assert trades[0]["average_price"] == 330.0


class TestPaperEngineSnapshot:
    def test_snapshot_saved_and_loaded(self, tmp_path):
        path = str(tmp_path / "state.json")
        engine1 = PaperEngine(snapshot_path=path)
        engine1.place_order("TEST", "NIFTY30JUN2525000CE", "SELL", "NFO", 75, 330.0)

        # Create new engine — should restore from snapshot
        engine2 = PaperEngine(snapshot_path=path)
        positions = engine2.get_positions()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "NIFTY30JUN2525000CE"
