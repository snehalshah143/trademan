"""
test_crudeoil_intraday_future_long_directional.py
-------------------------------------------------
Tests for the Crudeoil Intraday Futures Directional strategy.

TODO: Fill in tests once entry/exit rules are confirmed.

Run from trademan/ root:
    python -m pytest strategy_framework/strategies/directional/commodity/crudeoil_intraday_future_long_directional/tests/ -v
"""
import pytest
from datetime import datetime
from pathlib import Path
import sys

_framework_root = Path(__file__).resolve().parent.parent.parent.parent.parent.parent.parent
if str(_framework_root) not in sys.path:
    sys.path.insert(0, str(_framework_root))

from strategy_framework.models.future_leg import FutureDirection, FutureLeg, FutureStatus


# ---------------------------------------------------------------------------
# FutureLeg model tests
# ---------------------------------------------------------------------------

class TestFutureLeg:

    def test_long_leg_creation(self):
        leg = FutureLeg(
            symbol="CRUDEOIL17NOV25", exchange="MCX",
            direction=FutureDirection.LONG,
            quantity=100, lot_size=100,
            product="MIS", expiry="17NOV25",
            entry_price=6000.0,
        )
        assert leg.is_long is True
        assert leg.lots == 1
        assert leg.status == FutureStatus.PENDING

    def test_short_leg_creation(self):
        leg = FutureLeg(
            symbol="CRUDEOIL17NOV25", exchange="MCX",
            direction=FutureDirection.SHORT,
            quantity=200, lot_size=100,
            product="MIS", expiry="17NOV25",
            entry_price=6000.0,
        )
        assert leg.is_long is False
        assert leg.lots == 2

    def test_long_unrealized_pnl_profit(self):
        leg = FutureLeg(
            symbol="CRUDEOIL17NOV25", exchange="MCX",
            direction=FutureDirection.LONG,
            quantity=100, lot_size=100,
            product="MIS", expiry="17NOV25",
            entry_price=6000.0, current_ltp=6100.0,
            status=FutureStatus.OPEN,
        )
        assert leg.unrealized_pnl == pytest.approx(100.0 * 100)   # 100pt × 100qty = ₹10000

    def test_long_unrealized_pnl_loss(self):
        leg = FutureLeg(
            symbol="CRUDEOIL17NOV25", exchange="MCX",
            direction=FutureDirection.LONG,
            quantity=100, lot_size=100,
            product="MIS", expiry="17NOV25",
            entry_price=6000.0, current_ltp=5950.0,
            status=FutureStatus.OPEN,
        )
        assert leg.unrealized_pnl == pytest.approx(-50.0 * 100)   # -50pt × 100qty = -₹5000

    def test_short_unrealized_pnl_profit(self):
        leg = FutureLeg(
            symbol="CRUDEOIL17NOV25", exchange="MCX",
            direction=FutureDirection.SHORT,
            quantity=100, lot_size=100,
            product="MIS", expiry="17NOV25",
            entry_price=6000.0, current_ltp=5900.0,
            status=FutureStatus.OPEN,
        )
        # SHORT profits when price falls
        assert leg.unrealized_pnl == pytest.approx(100.0 * 100)

    def test_computed_realized_pnl_long(self):
        leg = FutureLeg(
            symbol="CRUDEOIL17NOV25", exchange="MCX",
            direction=FutureDirection.LONG,
            quantity=100, lot_size=100,
            product="MIS", expiry="17NOV25",
            entry_price=6000.0, exit_price=6150.0,
            status=FutureStatus.CLOSED,
        )
        assert leg.computed_realized_pnl == pytest.approx(150.0 * 100)

    def test_unrealized_pnl_missing_prices(self):
        leg = FutureLeg(
            symbol="CRUDEOIL17NOV25", exchange="MCX",
            direction=FutureDirection.LONG,
            quantity=100, lot_size=100,
            product="MIS", expiry="17NOV25",
        )
        assert leg.unrealized_pnl == 0.0

    def test_str_representation(self):
        leg = FutureLeg(
            symbol="CRUDEOIL17NOV25", exchange="MCX",
            direction=FutureDirection.LONG,
            quantity=100, lot_size=100,
            product="MIS", expiry="17NOV25",
            entry_price=6000.0,
        )
        s = str(leg)
        assert "LONG" in s
        assert "CRUDEOIL17NOV25" in s


# ---------------------------------------------------------------------------
# Signal evaluation tests (TODO: fill in once entry rules are confirmed)
# ---------------------------------------------------------------------------

class TestSignalEvaluation:
    """
    TODO: Add tests for _evaluate_signal() once the actual entry conditions
    are provided by the user.

    Example structure:
        def test_bullish_signal_fires_when_conditions_met(self):
            ind = IndicatorState()
            ind.close = 6100.0
            ind.ema_slow = 6000.0
            ...
            bias = _evaluate_signal(ind, "LONG")
            assert bias == Bias.BULLISH
    """
    pass


# ---------------------------------------------------------------------------
# Paper exit executor tests
# ---------------------------------------------------------------------------

class TestPaperExitExecutor:

    def _make_open_leg(self, entry_price: float, direction: FutureDirection, current_ltp: float) -> FutureLeg:
        leg = FutureLeg(
            symbol="CRUDEOIL17NOV25", exchange="MCX",
            direction=direction,
            quantity=100, lot_size=100,
            product="MIS", expiry="17NOV25",
            entry_price=entry_price, current_ltp=current_ltp,
            status=FutureStatus.OPEN,
        )
        return leg

    def test_paper_exit_long_profitable(self):
        from strategy_framework.strategies.directional.commodity.crudeoil_intraday_future_long_directional.monitoring_crudeoil_intraday_future_long_directional import (
            PaperExitExecutor,
        )
        executor = PaperExitExecutor()
        leg = self._make_open_leg(6000.0, FutureDirection.LONG, 6200.0)
        executor.fill_leg(leg)

        assert leg.status == FutureStatus.CLOSED
        assert leg.exit_price == pytest.approx(6200.0)
        assert leg.realized_pnl == pytest.approx(200.0 * 100)   # ₹20000 profit

    def test_paper_exit_long_loss(self):
        from strategy_framework.strategies.directional.commodity.crudeoil_intraday_future_long_directional.monitoring_crudeoil_intraday_future_long_directional import (
            PaperExitExecutor,
        )
        executor = PaperExitExecutor()
        leg = self._make_open_leg(6000.0, FutureDirection.LONG, 5900.0)
        executor.fill_leg(leg)

        assert leg.status == FutureStatus.CLOSED
        assert leg.realized_pnl == pytest.approx(-100.0 * 100)  # -₹10000

    def test_paper_exit_short_profitable(self):
        from strategy_framework.strategies.directional.commodity.crudeoil_intraday_future_long_directional.monitoring_crudeoil_intraday_future_long_directional import (
            PaperExitExecutor,
        )
        executor = PaperExitExecutor()
        leg = self._make_open_leg(6000.0, FutureDirection.SHORT, 5800.0)
        executor.fill_leg(leg)

        assert leg.status == FutureStatus.CLOSED
        assert leg.realized_pnl == pytest.approx(200.0 * 100)   # ₹20000 profit

    def test_paper_exit_skips_closed_leg(self):
        from strategy_framework.strategies.directional.commodity.crudeoil_intraday_future_long_directional.monitoring_crudeoil_intraday_future_long_directional import (
            PaperExitExecutor,
        )
        executor = PaperExitExecutor()
        leg = self._make_open_leg(6000.0, FutureDirection.LONG, 6200.0)
        leg.status = FutureStatus.CLOSED
        leg.exit_price = 6100.0

        executor.fill_leg(leg)
        assert leg.exit_price == pytest.approx(6100.0)   # unchanged


# ---------------------------------------------------------------------------
# DB tests (TODO: fill in once DB schema is finalised)
# ---------------------------------------------------------------------------

class TestDirectionalFuturesDB:
    """
    TODO: Add DB round-trip tests once entry/exit rules are confirmed.

    Example structure:
        def test_save_and_recover_run(self, tmp_path):
            from strategy_framework.framework.directional_futures_db import init_db, SessionFactory, ...
            engine = init_db(f"sqlite:///{tmp_path}/test.db")
            sf = SessionFactory(engine)
            ...
    """
    pass
