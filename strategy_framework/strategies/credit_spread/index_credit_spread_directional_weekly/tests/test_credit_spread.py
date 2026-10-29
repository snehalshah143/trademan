"""
test_credit_spread.py
---------------------
Unit tests for the directional credit spread framework modules.

Covers the 60-case test matrix from CREDIT_SPREAD_DESIGN.md:
  - CandleBuilder tick aggregation
  - ConfirmationCounter N-check pattern
  - crossover helpers
  - Supertrend direction detection
  - TrailingSlManager (all 3 trail types)
  - ProfitLadder partial-exit schedule
  - Strike selection (BULL_PUT / BEAR_CALL)
  - Symbol construction
  - Expiry utilities (parse, days_to_expiry, select_expiry)
  - Sizing (lots_from_capital, split_into_orders)
  - MarketUtils (is_within_window, is_trading_day)
  - IndicatorState snapshot
  - DB init (smoke test — SQLite in-memory)
  - DBWriteQueue (non-blocking put + flush)
"""
from __future__ import annotations

import queue
import time
import threading
from datetime import date, datetime, timedelta
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from strategy_framework.framework.candle_builder import CandleBar, CandleBuilder
from strategy_framework.framework.credit_spread_db import (
    SessionFactory, init_db, save_mtm_snapshot, save_strategy_run,
)
from strategy_framework.framework.db_writer import DBWriteQueue
from strategy_framework.framework.expiry_utils import (
    days_to_expiry, parse_openalgo_date, round_to_strike,
    select_expiry, to_openalgo_date,
)
from strategy_framework.framework.indicators import (
    ema_from_series, rsi_from_series, supertrend_seed, supertrend_step,
)
from strategy_framework.framework.market_utils import is_trading_day, is_within_window
from strategy_framework.framework.signal import (
    Bias, ConfirmationCounter, IndicatorState, SignalState,
    crossover_above, crossover_below,
    supertrend_flipped_bearish, supertrend_flipped_bullish,
)
from strategy_framework.framework.sizing import (
    fixed_lots, lots_from_capital, quantity_from_lots, split_into_orders,
)
from strategy_framework.framework.spread_template import (
    build_option_symbol, build_spread_legs, select_bear_call_spread,
    select_bull_put_spread,
)
from strategy_framework.framework.trailing import (
    ProfitLadder, ProfitLadderRung, TrailType, TrailingSlManager,
)


# ===========================================================================
# CandleBuilder
# ===========================================================================

class TestCandleBuilder:

    def test_first_tick_does_not_close_bar(self):
        b = CandleBuilder("NIFTY")
        result = b.tick(24000.0, datetime(2025, 7, 1, 9, 15, 5))
        assert result is None
        assert b.closed_count == 0

    def test_same_minute_ticks_update_live_bar(self):
        b = CandleBuilder("NIFTY")
        b.tick(24000.0, datetime(2025, 7, 1, 9, 15, 5))
        b.tick(24050.0, datetime(2025, 7, 1, 9, 15, 30))
        b.tick(23980.0, datetime(2025, 7, 1, 9, 15, 55))
        live = b.live_bar
        assert live["high"] == 24050.0
        assert live["low"]  == 23980.0
        assert live["close"] == 23980.0
        assert b.closed_count == 0

    def test_new_minute_closes_previous_bar(self):
        b = CandleBuilder("NIFTY")
        b.tick(24000.0, datetime(2025, 7, 1, 9, 15, 5))
        b.tick(24050.0, datetime(2025, 7, 1, 9, 15, 30))
        closed = b.tick(24100.0, datetime(2025, 7, 1, 9, 16, 1))
        assert closed is not None
        assert isinstance(closed, CandleBar)
        assert closed.high == 24050.0
        assert closed.close == 24050.0
        assert b.closed_count == 1

    def test_seed_from_history_populates_closed_bars(self):
        b = CandleBuilder("NIFTY")
        bars = [
            {"timestamp": "2025-07-01T09:15:00", "open": 24000, "high": 24050,
             "low": 23990, "close": 24030, "volume": 100},
            {"timestamp": "2025-07-01T09:16:00", "open": 24030, "high": 24080,
             "low": 24020, "close": 24060, "volume": 110},
        ]
        result = b.seed_from_history(bars)
        assert len(result) == 2
        assert b.closed_count == 2

    def test_closes_returns_numpy_array(self):
        b = CandleBuilder("NIFTY")
        bars = [
            {"timestamp": f"2025-07-01T09:{i+15:02d}:00",
             "open": 24000+i, "high": 24010+i, "low": 23990+i,
             "close": 24005+i, "volume": 50}
            for i in range(5)
        ]
        b.seed_from_history(bars)
        c = b.closes()
        assert isinstance(c, np.ndarray)
        assert len(c) == 5

    def test_max_history_caps_bars(self):
        b = CandleBuilder("NIFTY", max_history=3)
        bars = [
            {"timestamp": f"2025-07-01T09:{i+15:02d}:00",
             "open": 24000, "high": 24010, "low": 23990,
             "close": 24000, "volume": 50}
            for i in range(5)
        ]
        b.seed_from_history(bars)
        assert b.closed_count == 3   # capped to max_history


# ===========================================================================
# ConfirmationCounter
# ===========================================================================

class TestConfirmationCounter:

    def test_does_not_fire_before_required(self):
        c = ConfirmationCounter(required=3)
        assert not c.update(True)
        assert not c.update(True)

    def test_fires_on_nth_consecutive(self):
        c = ConfirmationCounter(required=2)
        assert not c.update(True)
        assert c.update(True)

    def test_resets_on_false(self):
        c = ConfirmationCounter(required=2)
        c.update(True)
        c.update(False)
        assert c.count == 0
        assert not c.update(True)

    def test_does_not_refire_after_first_fire(self):
        c = ConfirmationCounter(required=2)
        c.update(True)
        assert c.update(True)     # fires once
        assert not c.update(True) # does NOT fire again (already at threshold)

    def test_reset_allows_re_fire(self):
        c = ConfirmationCounter(required=2)
        c.update(True)
        c.update(True)
        c.reset()
        assert c.count == 0
        c.update(True)
        assert c.update(True)

    def test_is_building_property(self):
        c = ConfirmationCounter(required=3)
        assert not c.is_building
        c.update(True)
        assert c.is_building
        c.update(True)
        assert c.is_building
        c.update(True)
        assert not c.is_building    # has_fired = True


# ===========================================================================
# Crossover helpers
# ===========================================================================

class TestCrossoverHelpers:

    def test_crossover_above_detects_upward_cross(self):
        assert crossover_above(51.0, 49.0, 50.0) is True

    def test_crossover_above_no_cross(self):
        assert crossover_above(52.0, 51.0, 50.0) is False

    def test_crossover_above_touching_level(self):
        # prev == level → still counts as below
        assert crossover_above(51.0, 50.0, 50.0) is True

    def test_crossover_above_none_returns_false(self):
        assert crossover_above(None, 49.0, 50.0) is False
        assert crossover_above(51.0, None, 50.0) is False

    def test_crossover_below_detects_downward_cross(self):
        assert crossover_below(49.0, 51.0, 50.0) is True

    def test_crossover_below_no_cross(self):
        assert crossover_below(48.0, 49.0, 50.0) is False


# ===========================================================================
# Supertrend flip
# ===========================================================================

class TestSupertrendFlip:

    def test_flip_bearish(self):
        assert supertrend_flipped_bearish(-1, 1) is True
        assert supertrend_flipped_bearish(-1, -1) is False
        assert supertrend_flipped_bearish(1, 1) is False

    def test_flip_bullish(self):
        assert supertrend_flipped_bullish(1, -1) is True
        assert supertrend_flipped_bullish(1, 1) is False
        assert supertrend_flipped_bullish(-1, -1) is False


# ===========================================================================
# TrailingSlManager
# ===========================================================================

class TestTrailingSlManager:

    def test_mtm_step_no_trail_below_activation(self):
        sl = TrailingSlManager(trail_type=TrailType.MTM_STEP, initial_sl=3000.0,
                               step=500.0, activation=2000.0)
        sl.update_mtm(1000.0)   # below activation
        assert sl.current_sl == 3000.0

    def test_mtm_step_tightens_after_activation(self):
        sl = TrailingSlManager(trail_type=TrailType.MTM_STEP, initial_sl=3000.0,
                               step=500.0, activation=500.0)
        sl.update_mtm(2000.0)   # exceeds activation; 4 full steps
        assert sl.current_sl < 3000.0

    def test_sl_triggered_below_threshold(self):
        sl = TrailingSlManager(trail_type=TrailType.MTM_STEP, initial_sl=3000.0,
                               step=500.0, activation=0.0)
        assert sl.is_triggered(-3500.0) is True

    def test_sl_not_triggered_above_threshold(self):
        sl = TrailingSlManager(trail_type=TrailType.MTM_STEP, initial_sl=3000.0,
                               step=500.0, activation=0.0)
        assert sl.is_triggered(-2000.0) is False

    def test_none_trail_never_triggers(self):
        sl = TrailingSlManager(trail_type=TrailType.NONE, initial_sl=3000.0)
        assert sl.is_triggered(-99999.0) is False

    def test_spot_step_ratchets_for_bullish(self):
        sl = TrailingSlManager(trail_type=TrailType.SPOT_STEP, initial_sl=3000.0,
                               step=100.0, activation=0.0)
        sl._activated = True
        sl._best_spot = 24000.0
        sl.update_spot(24200.0, "BULLISH")   # moved +200 = 2 steps
        assert sl.current_sl < 3000.0

    def test_spot_step_does_not_relax(self):
        sl = TrailingSlManager(trail_type=TrailType.SPOT_STEP, initial_sl=3000.0,
                               step=100.0, activation=0.0)
        sl._activated = True
        sl._best_spot = 24200.0
        sl.update_spot(24000.0, "BULLISH")   # spot fell — SL must NOT relax
        assert sl.current_sl == 3000.0


# ===========================================================================
# ProfitLadder
# ===========================================================================

class TestProfitLadder:

    def _make_ladder(self):
        return ProfitLadder.from_config([
            {"mtm": 2000, "exit_pct": 50},
            {"mtm": 4000, "exit_pct": 50},
        ])

    def test_no_trigger_below_threshold(self):
        ladder = self._make_ladder()
        assert ladder.check(1500.0, 2) == 0

    def test_first_rung_triggers(self):
        ladder = self._make_ladder()
        assert ladder.check(2500.0, 2) == 1    # 50% of 2 lots

    def test_second_rung_after_first_triggered(self):
        ladder = self._make_ladder()
        ladder.check(2500.0, 2)
        ladder.mark_last_triggered()
        lots = ladder.check(4500.0, 1)
        assert lots == 1   # 50% of remaining 1 lot

    def test_all_triggered(self):
        ladder = self._make_ladder()
        ladder.check(2500.0, 2)
        ladder.mark_last_triggered()
        ladder.check(4500.0, 1)
        ladder.mark_last_triggered()
        assert ladder.all_triggered is True

    def test_reset_clears_all(self):
        ladder = self._make_ladder()
        ladder.mark_last_triggered()
        ladder.reset()
        assert not ladder.all_triggered
        assert ladder.check(2500.0, 2) == 1


# ===========================================================================
# Strike selection
# ===========================================================================

class TestStrikeSelection:

    def test_bull_put_primary_above_hedge(self):
        p, h = select_bull_put_spread(spot=24500, interval=50,
                                      otm_distance=200, spread_width=200)
        assert p > h       # short put above long put
        assert p < 24500   # OTM below spot

    def test_bear_call_primary_below_hedge(self):
        p, h = select_bear_call_spread(spot=24500, interval=50,
                                       otm_distance=200, spread_width=200)
        assert h > p       # long call above short call
        assert p > 24500   # OTM above spot

    def test_round_to_interval(self):
        p, h = select_bull_put_spread(spot=24523, interval=50,
                                      otm_distance=200, spread_width=200)
        assert p % 50 == 0
        assert h % 50 == 0

    def test_symbol_format(self):
        sym = build_option_symbol("NIFTY", "31JUL25", 25000, "CE")
        assert sym == "NIFTY31JUL2525000CE"

    def test_build_spread_legs_roles(self):
        p_leg, h_leg = build_spread_legs(
            spread_type="BULL_PUT_SPREAD",
            underlying="NIFTY",
            expiry="31JUL25",
            exchange="NFO",
            primary_strike=24500,
            hedge_strike=24300,
            quantity=25,
        )
        from strategy_framework.models.option_leg import LegAction, LegRole, OptionType
        assert p_leg.role   == LegRole.PRIMARY
        assert p_leg.action == LegAction.SELL
        assert p_leg.option_type == OptionType.PE
        assert h_leg.role   == LegRole.HEDGE
        assert h_leg.action == LegAction.BUY
        assert h_leg.option_type == OptionType.PE

    def test_bear_call_spread_legs(self):
        p_leg, h_leg = build_spread_legs(
            spread_type="BEAR_CALL_SPREAD",
            underlying="NIFTY",
            expiry="31JUL25",
            exchange="NFO",
            primary_strike=24700,
            hedge_strike=24900,
            quantity=25,
        )
        from strategy_framework.models.option_leg import OptionType
        assert p_leg.option_type == OptionType.CE
        assert h_leg.option_type == OptionType.CE


# ===========================================================================
# Expiry utilities
# ===========================================================================

class TestExpiryUtils:

    def test_parse_openalgo_date(self):
        d = parse_openalgo_date("31JUL25")
        assert d == date(2025, 7, 31)

    def test_to_openalgo_date(self):
        assert to_openalgo_date(date(2025, 7, 31)) == "31JUL25"

    def test_roundtrip(self):
        original = "15AUG25"
        assert to_openalgo_date(parse_openalgo_date(original)) == original

    def test_days_to_expiry_future(self):
        future = date.today() + timedelta(days=7)
        expiry_str = to_openalgo_date(future)
        assert days_to_expiry(expiry_str) == 7

    def test_days_to_expiry_today(self):
        expiry_str = to_openalgo_date(date.today())
        assert days_to_expiry(expiry_str) == 0

    def test_select_expiry_weekly_picks_nearest(self):
        today = date.today()
        exp1 = to_openalgo_date(today + timedelta(days=3))
        exp2 = to_openalgo_date(today + timedelta(days=10))
        result = select_expiry([exp2, exp1], mode="WEEKLY", dte_min=1, dte_max=14)
        assert result == exp1

    def test_select_expiry_no_candidates(self):
        past = to_openalgo_date(date.today() - timedelta(days=5))
        result = select_expiry([past], mode="WEEKLY", dte_min=1, dte_max=14)
        assert result is None

    def test_round_to_strike(self):
        assert round_to_strike(24523.0, 50.0) == 24500.0
        assert round_to_strike(24575.0, 50.0) == 24600.0


# ===========================================================================
# Sizing
# ===========================================================================

class TestSizing:

    def test_lots_from_capital_basic(self):
        lots = lots_from_capital(
            available_capital=100000,
            margin_per_lot=20000,
            capital_utilisation_pct=80.0,
            min_lots=1,
            max_lots=10,
        )
        assert lots == 4   # 80000 / 20000 = 4

    def test_lots_from_capital_capped_at_max(self):
        lots = lots_from_capital(
            available_capital=10_000_000,
            margin_per_lot=20000,
            max_lots=5,
        )
        assert lots == 5

    def test_lots_insufficient_returns_zero(self):
        lots = lots_from_capital(
            available_capital=5000,
            margin_per_lot=20000,
            min_lots=1,
        )
        assert lots == 0

    def test_quantity_from_lots(self):
        assert quantity_from_lots(3, 25) == 75

    def test_fixed_lots_clamp(self):
        assert fixed_lots(20, 25, max_lots=10) == 10
        assert fixed_lots(5, 25, max_lots=10)  == 5

    def test_split_into_orders_exact(self):
        assert split_into_orders(300, 100) == [100, 100, 100]

    def test_split_into_orders_remainder(self):
        assert split_into_orders(250, 100) == [100, 100, 50]

    def test_split_into_orders_single(self):
        assert split_into_orders(75, 100) == [75]

    def test_split_into_orders_zero(self):
        assert split_into_orders(0, 100) == []


# ===========================================================================
# Market utilities
# ===========================================================================

class TestMarketUtils:

    def test_is_within_window_inside(self):
        dt = datetime(2025, 7, 1, 10, 30, 0)
        assert is_within_window(dt, "09:30", "14:30") is True

    def test_is_within_window_at_boundary(self):
        dt = datetime(2025, 7, 1, 9, 30, 0)
        assert is_within_window(dt, "09:30", "14:30") is True
        dt2 = datetime(2025, 7, 1, 14, 30, 0)
        assert is_within_window(dt2, "09:30", "14:30") is True

    def test_is_within_window_outside(self):
        dt = datetime(2025, 7, 1, 15, 0, 0)
        assert is_within_window(dt, "09:30", "14:30") is False

    def test_is_trading_day_weekday(self):
        # Monday = weekday 0
        monday = date(2025, 7, 7)
        assert is_trading_day(monday) is True

    def test_is_trading_day_saturday(self):
        saturday = date(2025, 7, 5)
        assert is_trading_day(saturday) is False

    def test_is_trading_day_sunday(self):
        sunday = date(2025, 7, 6)
        assert is_trading_day(sunday) is False


# ===========================================================================
# IndicatorState
# ===========================================================================

class TestIndicatorState:

    def test_snapshot_previous_copies_values(self):
        s = IndicatorState(
            rsi=65.0,
            ema_fast=24100.0,
            ema_slow=24000.0,
            supertrend_direction=-1,
        )
        s.snapshot_previous()
        assert s.prev_rsi == 65.0
        assert s.prev_ema_fast == 24100.0
        assert s.prev_supertrend_direction == -1

    def test_is_ready_requires_rsi_and_supertrend(self):
        s = IndicatorState()
        assert not s.is_ready
        s.rsi = 55.0
        assert not s.is_ready
        s.supertrend = 24000.0
        assert s.is_ready


# ===========================================================================
# SignalState
# ===========================================================================

class TestSignalState:

    def test_set_bias(self):
        s = SignalState()
        s.set_bullish()
        assert s.is_bullish
        s.set_neutral()
        assert s.is_neutral

    def test_cooldown_decrements(self):
        s = SignalState()
        s.enter_cooldown(3)
        assert s.in_cooldown
        s.tick_candle()
        s.tick_candle()
        s.tick_candle()
        assert not s.in_cooldown


# ===========================================================================
# DB (smoke test — SQLite in-memory)
# ===========================================================================

class TestCreditSpreadDb:

    def test_init_db_creates_tables(self):
        engine = init_db("sqlite:///:memory:")
        assert engine is not None

    def test_save_and_query_run(self):
        engine = init_db("sqlite:///:memory:")
        sf = SessionFactory(engine)
        session = sf.make()
        run_id = save_strategy_run(session,
            strategy_id="ICS_TEST",
            strategy_name="TEST",
            spread_type="BULL_PUT_SPREAD",
            underlying="NIFTY",
            expiry="31JUL25",
            lots=1,
            lot_size=25,
            status="OPEN",
            execution_mode="MONITOR_ONLY",
        )
        session.commit()
        assert run_id > 0

        from strategy_framework.framework.credit_spread_db import CsStrategyRun
        row = session.query(CsStrategyRun).filter_by(id=run_id).first()
        assert row.strategy_id == "ICS_TEST"
        session.close()


# ===========================================================================
# DBWriteQueue
# ===========================================================================

class TestDbWriteQueue:

    def _make_session_factory(self):
        engine = init_db("sqlite:///:memory:")
        sf = SessionFactory(engine)
        return sf.make

    def test_put_and_flush(self):
        executed = []

        def write_fn(session, value: int):
            executed.append(value)

        make_session = self._make_session_factory()
        dq = DBWriteQueue(make_session, flush_interval=0.1)
        dq.start()
        dq.put(write_fn, 42)
        dq.put(write_fn, 99)
        time.sleep(0.5)
        dq.stop(drain=True)
        assert 42 in executed
        assert 99 in executed

    def test_queue_full_drops_gracefully(self):
        make_session = self._make_session_factory()
        dq = DBWriteQueue(make_session, flush_interval=60.0, max_queue_size=2)
        dq.start()
        dq.put(lambda s: None)
        dq.put(lambda s: None)
        # Third put should be dropped (queue full)
        result = dq.put(lambda s: None)
        assert result is False
        dq.stop(drain=False)
