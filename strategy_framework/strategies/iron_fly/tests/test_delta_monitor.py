"""
test_delta_monitor.py
---------------------
Unit tests for the delta monitor — position reading, Greeks aggregation,
position delta calculation.
"""

import json
import pytest
from unittest.mock import MagicMock, patch
from monitoring.delta_monitor import (
    DeltaSnapshot,
    GreeksFetcher,
    LegGreeks,
    PositionReader,
)


# ---------------------------------------------------------------------------
# LegGreeks — position delta calculation
# ---------------------------------------------------------------------------

class TestLegGreeks:
    def test_short_call_position_delta_negative(self):
        leg = LegGreeks(
            symbol="NIFTY31JUL2525000CE", role="SHORT_CALL",
            action="SELL", quantity=75, strike=25000, option_type="CE",
            raw_delta=0.42,
        )
        # SELL 75 × delta 0.42 → position delta = -31.5
        assert leg.position_delta == pytest.approx(-31.5)
        assert leg.signed_quantity == -75

    def test_short_put_position_delta_positive(self):
        # PE delta is negative by convention (put loses value as spot rises)
        leg = LegGreeks(
            symbol="NIFTY31JUL2525000PE", role="SHORT_PUT",
            action="SELL", quantity=75, strike=25000, option_type="PE",
            raw_delta=-0.38,
        )
        # SELL 75 with raw_delta -0.38 → signed_qty=-75 → position_delta = (-0.38) × (-75) = +28.5
        assert leg.position_delta == pytest.approx(28.5)

    def test_long_call_position_delta_positive(self):
        leg = LegGreeks(
            symbol="NIFTY31JUL2525800CE", role="LONG_CALL",
            action="BUY", quantity=75, strike=25800, option_type="CE",
            raw_delta=0.18,
        )
        assert leg.position_delta == pytest.approx(13.5)
        assert leg.signed_quantity == 75

    def test_long_put_position_delta_negative(self):
        leg = LegGreeks(
            symbol="NIFTY31JUL2524200PE", role="LONG_PUT",
            action="BUY", quantity=75, strike=24200, option_type="PE",
            raw_delta=-0.12,
        )
        assert leg.position_delta == pytest.approx(-9.0)

    def test_position_theta_scaled_by_quantity(self):
        leg = LegGreeks(
            symbol="NIFTY31JUL2525000CE", role="SHORT_CALL",
            action="SELL", quantity=75, strike=25000, option_type="CE",
            theta=-5.0,
        )
        # SELL → signed_qty = -75 → position_theta = -5.0 × -75 = 375 (theta positive for seller)
        assert leg.position_theta == pytest.approx(375.0)


# ---------------------------------------------------------------------------
# DeltaSnapshot — net Greeks
# ---------------------------------------------------------------------------

class TestDeltaSnapshot:
    def _make_iron_fly_legs(self) -> list[LegGreeks]:
        return [
            LegGreeks("NIFTY31JUL2525000CE", "SHORT_CALL", "SELL", 75, 25000, "CE",
                      raw_delta=0.42, gamma=0.001, theta=-5.0, vega=10.0),
            LegGreeks("NIFTY31JUL2525000PE", "SHORT_PUT",  "SELL", 75, 25000, "PE",
                      raw_delta=-0.38, gamma=0.001, theta=-4.5, vega=9.5),
            LegGreeks("NIFTY31JUL2525800CE", "LONG_CALL",  "BUY",  75, 25800, "CE",
                      raw_delta=0.18, gamma=0.0005, theta=-2.0, vega=5.0),
            LegGreeks("NIFTY31JUL2524200PE", "LONG_PUT",   "BUY",  75, 24200, "PE",
                      raw_delta=-0.12, gamma=0.0005, theta=-1.5, vega=4.5),
        ]

    def test_net_delta_of_balanced_iron_fly(self):
        from datetime import datetime
        snap = DeltaSnapshot(timestamp=datetime.now(), spot=25000.0, legs=self._make_iron_fly_legs())
        # SHORT_CALL: 0.42 × -75 = -31.5
        # SHORT_PUT : -0.38 × -75 = +28.5
        # LONG_CALL : 0.18 × 75  = +13.5
        # LONG_PUT  : -0.12 × 75 = -9.0
        # Net = -31.5 + 28.5 + 13.5 - 9.0 = +1.5
        assert snap.net_delta == pytest.approx(1.5)

    def test_net_theta_positive_for_short_straddle(self):
        from datetime import datetime
        snap = DeltaSnapshot(timestamp=datetime.now(), spot=25000.0, legs=self._make_iron_fly_legs())
        # Sellers benefit from theta decay → net theta should be positive
        assert snap.net_theta > 0


# ---------------------------------------------------------------------------
# PositionReader — paper mode
# ---------------------------------------------------------------------------

class TestPositionReaderPaper:
    def test_reads_positions_from_snapshot(self, tmp_path):
        snapshot = {
            "positions": [
                {"symbol": "NIFTY31JUL2525000CE", "quantity": -75,
                 "exchange": "NFO", "average_price": 330.0, "current_ltp": 300.0},
                {"symbol": "NIFTY31JUL2525000PE", "quantity": -75,
                 "exchange": "NFO", "average_price": 310.0, "current_ltp": 280.0},
                {"symbol": "NIFTY31JUL2525800CE", "quantity": 75,
                 "exchange": "NFO", "average_price": 90.0, "current_ltp": 95.0},
                {"symbol": "NIFTY31JUL2524200PE", "quantity": 75,
                 "exchange": "NFO", "average_price": 80.0, "current_ltp": 75.0},
            ],
            "orders": [], "max_mtm": 0.0, "min_mtm": 0.0,
        }
        snap_file = tmp_path / "paper_state.json"
        snap_file.write_text(json.dumps(snapshot))

        config = MagicMock()
        config.is_paper = True
        config.get_str.return_value = str(snap_file)
        config.exchange_options = "NFO"

        client = MagicMock()
        reader = PositionReader(config, client)
        legs = reader.read_legs()

        assert len(legs) == 4

    def test_sell_position_maps_to_correct_action(self, tmp_path):
        snapshot = {
            "positions": [
                {"symbol": "NIFTY31JUL2525000CE", "quantity": -75,
                 "exchange": "NFO", "average_price": 330.0, "current_ltp": 300.0},
            ],
            "orders": [], "max_mtm": 0.0, "min_mtm": 0.0,
        }
        snap_file = tmp_path / "paper_state.json"
        snap_file.write_text(json.dumps(snapshot))

        config = MagicMock()
        config.is_paper = True
        config.get_str.return_value = str(snap_file)
        config.exchange_options = "NFO"

        reader = PositionReader(config, MagicMock())
        legs = reader.read_legs()

        assert legs[0]["action"] == "SELL"
        assert legs[0]["quantity"] == 75

    def test_empty_snapshot_returns_empty_legs(self, tmp_path):
        snapshot = {"positions": [], "orders": [], "max_mtm": 0.0, "min_mtm": 0.0}
        snap_file = tmp_path / "paper_state.json"
        snap_file.write_text(json.dumps(snapshot))

        config = MagicMock()
        config.is_paper = True
        config.get_str.return_value = str(snap_file)
        config.exchange_options = "NFO"

        reader = PositionReader(config, MagicMock())
        assert reader.read_legs() == []

    def test_missing_snapshot_returns_empty(self, tmp_path):
        config = MagicMock()
        config.is_paper = True
        config.get_str.return_value = str(tmp_path / "nonexistent.json")
        config.exchange_options = "NFO"

        reader = PositionReader(config, MagicMock())
        assert reader.read_legs() == []


# ---------------------------------------------------------------------------
# TimingCollector — expiry parsing and time signals
# ---------------------------------------------------------------------------

class TestTimingCollectorExpiry:
    from monitoring.delta_monitor import TimingCollector

    def _make_collector(self):
        from monitoring.delta_monitor import TimingCollector
        config = MagicMock()
        config.get_str.side_effect = lambda key, default="": {
            "DEPLOYMENT_TIME": "09:30",
            "EXPIRY_EXIT_TIME": "11:00",
        }.get(key, default)
        client = MagicMock()
        client.get_expiry_dates.return_value = []
        return TimingCollector(config, client)

    def test_parse_expiry_from_nifty_ce_symbol(self):
        from monitoring.delta_monitor import TimingCollector
        result = TimingCollector._parse_expiry_from_symbol("NIFTY31JUL2525000CE")
        assert result == "31JUL25"

    def test_parse_expiry_from_nifty_pe_symbol(self):
        from monitoring.delta_monitor import TimingCollector
        result = TimingCollector._parse_expiry_from_symbol("NIFTY28AUG2524200PE")
        assert result == "28AUG25"

    def test_parse_expiry_empty_on_short_symbol(self):
        from monitoring.delta_monitor import TimingCollector
        result = TimingCollector._parse_expiry_from_symbol("NIFTY")
        assert result == ""

    def test_collect_uses_leg_symbols_for_expiry(self):
        col = self._make_collector()
        legs = [{"symbol": "NIFTY31JUL2525000CE", "action": "SELL", "quantity": 75}]
        with patch("monitoring.delta_monitor.now_ist") as mock_now, \
             patch("monitoring.delta_monitor.today_ist") as mock_today:
            from datetime import datetime, date
            mock_now.return_value = datetime(2025, 7, 21, 10, 0, 0)
            mock_today.return_value = date(2025, 7, 21)
            info = col.collect(legs)
        assert info.expiry_str == "31JUL25"
        assert info.days_to_expiry == 10
        assert info.is_expiry_day is False
        assert info.is_t_minus_1 is False

    def test_collect_flags_expiry_day(self):
        col = self._make_collector()
        legs = [{"symbol": "NIFTY31JUL2525000CE"}]
        with patch("monitoring.delta_monitor.now_ist") as mock_now, \
             patch("monitoring.delta_monitor.today_ist") as mock_today:
            from datetime import datetime, date
            mock_now.return_value = datetime(2025, 7, 31, 10, 0, 0)
            mock_today.return_value = date(2025, 7, 31)
            info = col.collect(legs)
        assert info.is_expiry_day is True
        assert info.days_to_expiry == 0

    def test_collect_flags_t_minus_1(self):
        col = self._make_collector()
        legs = [{"symbol": "NIFTY31JUL2525000CE"}]
        with patch("monitoring.delta_monitor.now_ist") as mock_now, \
             patch("monitoring.delta_monitor.today_ist") as mock_today:
            from datetime import datetime, date
            mock_now.return_value = datetime(2025, 7, 30, 10, 0, 0)
            mock_today.return_value = date(2025, 7, 30)
            info = col.collect(legs)
        assert info.is_t_minus_1 is True
        assert info.is_expiry_day is False

    def test_market_open_session(self):
        col = self._make_collector()
        with patch("monitoring.delta_monitor.now_ist") as mock_now, \
             patch("monitoring.delta_monitor.today_ist") as mock_today:
            from datetime import datetime, date
            mock_now.return_value = datetime(2025, 7, 21, 10, 30, 0)   # Monday
            mock_today.return_value = date(2025, 7, 21)
            info = col.collect([])
        assert info.session_status == "MARKET OPEN"
        assert info.is_market_open is True
        assert info.mins_to_deployment is None   # 09:30 already passed

    def test_pre_market_session(self):
        col = self._make_collector()
        with patch("monitoring.delta_monitor.now_ist") as mock_now, \
             patch("monitoring.delta_monitor.today_ist") as mock_today:
            from datetime import datetime, date
            mock_now.return_value = datetime(2025, 7, 21, 8, 45, 0)   # Monday 08:45
            mock_today.return_value = date(2025, 7, 21)
            info = col.collect([])
        assert info.session_status == "PRE-MARKET"
        assert info.is_market_open is False
        assert info.mins_to_deployment is not None
        assert info.mins_to_deployment == pytest.approx(45.0)

    def test_weekend_session(self):
        col = self._make_collector()
        with patch("monitoring.delta_monitor.now_ist") as mock_now, \
             patch("monitoring.delta_monitor.today_ist") as mock_today:
            from datetime import datetime, date
            mock_now.return_value = datetime(2025, 7, 19, 10, 0, 0)   # Saturday
            mock_today.return_value = date(2025, 7, 19)
            info = col.collect([])
        assert info.session_status == "WEEKEND"
        assert info.is_weekend is True
