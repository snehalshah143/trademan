"""
test_expiry.py
--------------
Unit tests for expiry date utilities.
"""

import pytest
from datetime import date, timedelta
from unittest.mock import patch
from strategies.iron_fly.strategy.expiry import (
    days_to_expiry,
    first_trading_day_of_month,
    is_expiry_day,
    is_first_trading_day_of_month,
    is_t_minus_1,
)
from utils.helper import expiry_to_openalgo_fmt, openalgo_fmt_to_date


class TestOpenAlgoDateFormat:
    def test_date_to_openalgo_fmt(self):
        assert expiry_to_openalgo_fmt(date(2025, 7, 31)) == "31JUL25"

    def test_openalgo_fmt_to_date(self):
        assert openalgo_fmt_to_date("31JUL25") == date(2025, 7, 31)

    def test_roundtrip(self):
        d = date(2025, 8, 28)
        assert openalgo_fmt_to_date(expiry_to_openalgo_fmt(d)) == d


class TestFirstTradingDayOfMonth:
    def test_first_day_is_monday(self):
        # A month that starts on Monday
        result = first_trading_day_of_month(date(2025, 9, 1))  # Sep 2025 starts Monday
        assert result == date(2025, 9, 1)

    def test_first_day_is_saturday_skips_to_monday(self):
        # Feb 2025 starts on Saturday → first trading day is Monday 3 Feb
        result = first_trading_day_of_month(date(2025, 2, 1))
        assert result == date(2025, 2, 3)

    def test_first_day_is_sunday_skips_to_monday(self):
        # Jun 2025 starts on Sunday → first trading day is Monday 2 Jun
        result = first_trading_day_of_month(date(2025, 6, 1))
        assert result == date(2025, 6, 2)


class TestIsFirstTradingDayOfMonth:
    def test_first_trading_day_returns_true(self):
        first = first_trading_day_of_month()
        with patch("strategies.iron_fly.strategy.expiry.today_ist", return_value=first):
            assert is_first_trading_day_of_month() is True

    def test_second_day_returns_false(self):
        first = first_trading_day_of_month()
        second = first + timedelta(days=1)
        with patch("strategies.iron_fly.strategy.expiry.today_ist", return_value=second):
            assert is_first_trading_day_of_month() is False


class TestIsExpiryDay:
    def test_expiry_day_returns_true(self):
        with patch("strategies.iron_fly.strategy.expiry.today_ist", return_value=date(2025, 7, 31)):
            assert is_expiry_day("31JUL25") is True

    def test_non_expiry_day_returns_false(self):
        with patch("strategies.iron_fly.strategy.expiry.today_ist", return_value=date(2025, 7, 30)):
            assert is_expiry_day("31JUL25") is False


class TestIsTMinus1:
    def test_t1_day_returns_true(self):
        # 31 JUL 2025 is Thursday (expiry), so T-1 is Wednesday 30 Jul
        with patch("strategies.iron_fly.strategy.expiry.today_ist", return_value=date(2025, 7, 30)):
            assert is_t_minus_1("31JUL25") is True

    def test_expiry_day_is_not_t1(self):
        with patch("strategies.iron_fly.strategy.expiry.today_ist", return_value=date(2025, 7, 31)):
            assert is_t_minus_1("31JUL25") is False

    def test_two_days_before_is_not_t1(self):
        with patch("strategies.iron_fly.strategy.expiry.today_ist", return_value=date(2025, 7, 29)):
            assert is_t_minus_1("31JUL25") is False


class TestDaysToExpiry:
    def test_days_remaining(self):
        with patch("strategies.iron_fly.strategy.expiry.today_ist", return_value=date(2025, 7, 21)):
            assert days_to_expiry("31JUL25") == 10

    def test_expiry_day_zero(self):
        with patch("strategies.iron_fly.strategy.expiry.today_ist", return_value=date(2025, 7, 31)):
            assert days_to_expiry("31JUL25") == 0

    def test_after_expiry_negative(self):
        with patch("strategies.iron_fly.strategy.expiry.today_ist", return_value=date(2025, 8, 1)):
            assert days_to_expiry("31JUL25") == -1
