"""
test_calculations.py
--------------------
Unit tests for Iron Fly pure calculation functions.

All tests are deterministic — no I/O, no OpenAlgo calls, no mocking required.
"""

import pytest
from strategies.iron_fly.strategy.calculations import (
    calculate_adjustment_wing_distance,
    calculate_breakevens,
    calculate_fresh_wing_distance,
    calculate_recovery_trigger,
    find_atm,
    is_breakeven_breached,
    is_gap_open_beyond_breakeven,
    is_gap_open_beyond_wings,
    is_premium_doubled,
    is_recovery_trigger_crossed,
    lower_wing_strike,
    upper_wing_strike,
)


# ---------------------------------------------------------------------------
# ATM finding
# ---------------------------------------------------------------------------

class TestFindAtm:
    def test_rounds_to_nearest_50(self):
        assert find_atm(24982) == 25000.0

    def test_rounds_down(self):
        assert find_atm(24960) == 24950.0

    def test_rounds_up(self):
        assert find_atm(24975) == 25000.0

    def test_already_on_strike(self):
        assert find_atm(25000) == 25000.0

    def test_banknifty_interval(self):
        assert find_atm(52150, strike_interval=100) == 52200.0

    def test_low_spot(self):
        assert find_atm(24410) == 24400.0


# ---------------------------------------------------------------------------
# Fresh wing distance
# ---------------------------------------------------------------------------

class TestCalculateFreshWingDistance:
    def test_spec_example(self):
        # CE=330, PE=310, buffer=100 → 740 → rounds up to 800
        result = calculate_fresh_wing_distance(330, 310, 100, 100)
        assert result == 800.0

    def test_already_multiple_of_100(self):
        # 200 + 200 + 100 = 500 → already multiple of 100
        result = calculate_fresh_wing_distance(200, 200, 100, 100)
        assert result == 500.0

    def test_rounds_up_not_nearest(self):
        # 150 + 150 + 100 = 400 → already 400
        result = calculate_fresh_wing_distance(150, 150, 100, 100)
        assert result == 400.0

    def test_one_above_boundary(self):
        # 300 + 200 + 100 = 600 → already 600
        result = calculate_fresh_wing_distance(300, 200, 100, 100)
        assert result == 600.0

    def test_partial_over_boundary(self):
        # 301 + 200 + 100 = 601 → rounds up to 700
        result = calculate_fresh_wing_distance(301, 200, 100, 100)
        assert result == 700.0


# ---------------------------------------------------------------------------
# Adjustment wing distance
# ---------------------------------------------------------------------------

class TestCalculateAdjustmentWingDistance:
    def test_spec_example(self):
        # CE=260, PE=250, buffer=50 → 560 → rounds up to 600
        result = calculate_adjustment_wing_distance(260, 250, 50, 50)
        assert result == 600.0

    def test_already_multiple_of_50(self):
        result = calculate_adjustment_wing_distance(200, 200, 50, 50)
        assert result == 450.0

    def test_rounds_up(self):
        # 200 + 201 + 50 = 451 → rounds up to 500
        result = calculate_adjustment_wing_distance(200, 201, 50, 50)
        assert result == 500.0


# ---------------------------------------------------------------------------
# Breakevens
# ---------------------------------------------------------------------------

class TestCalculateBreakevens:
    def test_spec_example(self):
        upper, lower = calculate_breakevens(25000, 330, 310)
        assert upper == 25640.0
        assert lower == 24360.0

    def test_symmetric_premiums(self):
        upper, lower = calculate_breakevens(25000, 300, 300)
        assert upper == 25600.0
        assert lower == 24400.0

    def test_zero_premiums(self):
        upper, lower = calculate_breakevens(25000, 0, 0)
        assert upper == 25000.0
        assert lower == 25000.0


# ---------------------------------------------------------------------------
# Wing strikes
# ---------------------------------------------------------------------------

class TestWingStrikes:
    def test_upper_wing(self):
        assert upper_wing_strike(25000, 800) == 25800.0

    def test_lower_wing(self):
        assert lower_wing_strike(25000, 800) == 24200.0

    def test_zero_distance(self):
        assert upper_wing_strike(25000, 0) == 25000.0
        assert lower_wing_strike(25000, 0) == 25000.0


# ---------------------------------------------------------------------------
# Recovery trigger
# ---------------------------------------------------------------------------

class TestRecoveryTrigger:
    def test_downside_trigger(self):
        # spec example: adj_price=24460, buffer=50 → 24510
        result = calculate_recovery_trigger(24460, 50, "DOWN")
        assert result == 24510.0

    def test_upside_trigger(self):
        result = calculate_recovery_trigger(25540, 50, "UP")
        assert result == 25490.0


# ---------------------------------------------------------------------------
# Premium doubling
# ---------------------------------------------------------------------------

class TestIsPremiumDoubled:
    def test_exactly_doubled(self):
        assert is_premium_doubled(660, 330, 2.0) is True

    def test_just_below_doubled(self):
        assert is_premium_doubled(659, 330, 2.0) is False

    def test_well_above_doubled(self):
        assert is_premium_doubled(800, 330, 2.0) is True

    def test_zero_entry_premium(self):
        assert is_premium_doubled(100, 0, 2.0) is False

    def test_custom_factor(self):
        assert is_premium_doubled(500, 300, 1.5) is True   # 300*1.5=450 ≤ 500
        assert is_premium_doubled(449, 300, 1.5) is False


# ---------------------------------------------------------------------------
# Breakeven breach
# ---------------------------------------------------------------------------

class TestIsBreakevenBreached:
    def test_upper_breached(self):
        # breakeven=25640, buffer=25 → trigger at 25665
        assert is_breakeven_breached(25670, 25640, 25, "upper") is True

    def test_upper_not_breached(self):
        assert is_breakeven_breached(25655, 25640, 25, "upper") is False

    def test_upper_exactly_at_threshold(self):
        assert is_breakeven_breached(25665, 25640, 25, "upper") is True

    def test_lower_breached(self):
        # breakeven=24360, buffer=25 → trigger at 24335
        assert is_breakeven_breached(24330, 24360, 25, "lower") is True

    def test_lower_not_breached(self):
        assert is_breakeven_breached(24340, 24360, 25, "lower") is False


# ---------------------------------------------------------------------------
# Recovery trigger crossed
# ---------------------------------------------------------------------------

class TestIsRecoveryTriggerCrossed:
    def test_down_trigger_crossed(self):
        # direction DOWN: spot must rise above trigger
        assert is_recovery_trigger_crossed(24520, 24510, "DOWN") is True

    def test_down_trigger_not_crossed(self):
        assert is_recovery_trigger_crossed(24505, 24510, "DOWN") is False

    def test_down_trigger_at_exact(self):
        assert is_recovery_trigger_crossed(24510, 24510, "DOWN") is True

    def test_up_trigger_crossed(self):
        # direction UP: spot must fall below trigger
        assert is_recovery_trigger_crossed(25480, 25490, "UP") is True

    def test_up_trigger_not_crossed(self):
        assert is_recovery_trigger_crossed(25500, 25490, "UP") is False


# ---------------------------------------------------------------------------
# Gap open checks
# ---------------------------------------------------------------------------

class TestGapOpenChecks:
    def test_beyond_upper_wing(self):
        assert is_gap_open_beyond_wings(26000, 25800, 24200) is True

    def test_beyond_lower_wing(self):
        assert is_gap_open_beyond_wings(24100, 25800, 24200) is True

    def test_inside_wings(self):
        assert is_gap_open_beyond_wings(25000, 25800, 24200) is False

    def test_at_wing_boundary(self):
        assert is_gap_open_beyond_wings(25800, 25800, 24200) is False   # exact = NOT beyond

    def test_gap_beyond_breakeven_upper(self):
        assert is_gap_open_beyond_breakeven(25670, 25640, 24360, 25) is True

    def test_gap_beyond_breakeven_lower(self):
        assert is_gap_open_beyond_breakeven(24330, 25640, 24360, 25) is True

    def test_no_gap(self):
        assert is_gap_open_beyond_breakeven(25000, 25640, 24360, 25) is False
