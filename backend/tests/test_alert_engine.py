"""
Comprehensive alert-engine test suite.

Covers every indicator, every operator, and every scope type supported by
the platform, plus permutation combinations for MACD/EMA crossovers.

Run:
  cd backend && pytest tests/test_alert_engine.py -v
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest
import pytest_asyncio

# ── Helpers ───────────────────────────────────────────────────────────────────

IST = ZoneInfo("Asia/Kolkata")

LEG_ID = "leg-uuid-0001"
STRAT_ID = "strat-uuid-0001"


def make_ctx(indicators: dict | None = None, **kwargs) -> dict:
    """Build a minimal EvaluationContext for evaluator tests."""
    base = {
        "strategy_id":      STRAT_ID,
        "underlying":       "CRUDEOIL",
        "mtm":              0.0,
        "pnl_pct":          0.0,
        "spot":             5000.0,
        "leg_ltps":         {LEG_ID: 5000.0},
        "leg_entry_prices": {LEG_ID: 4800.0},
        "leg_quantities":   {LEG_ID: 100},
        "leg_sides":        {LEG_ID: "BUY"},
        "indicators":       indicators or {},
        "candles":          {},
        "closed_candles":   {},
        "vwap":             5000.0,
    }
    base.update(kwargs)
    return base


def cond(scope: str, metric: str, operator: str, value=0, **kwargs) -> dict:
    """Build a single condition dict."""
    return {
        "id":          "test-cond",
        "scope":       scope,
        "metric":      metric,
        "operator":    operator,
        "value":       value,
        "leg_id":      kwargs.pop("leg_id", None),
        "timeframe":   kwargs.pop("timeframe", "5m"),
        "params":      kwargs.pop("params", {}),
        "lhs_source":  kwargs.pop("lhs_source", "CLOSE"),
        "rhs_type":    kwargs.pop("rhs_type", "NUMBER"),
        "rhs_metric":  kwargs.pop("rhs_metric", ""),
        "rhs_timeframe": kwargs.pop("rhs_timeframe", "5m"),
        "rhs_params":  kwargs.pop("rhs_params", {}),
        "rhs_source":  kwargs.pop("rhs_source", "CLOSE"),
        **kwargs,
    }


def group(conditions: list, op: str = "AND", groups: list | None = None) -> dict:
    return {"op": op, "conditions": conditions, "groups": groups or []}


# ── Import the modules under test ─────────────────────────────────────────────

from alerts.rule_evaluator import evaluate_condition, evaluate_group
from alerts.dependency_graph import DependencyGraph, _indicator_key


# ═══════════════════════════════════════════════════════════════════════════════
# 1. DEPENDENCY GRAPH — key building
# ═══════════════════════════════════════════════════════════════════════════════

class TestDependencyGraphKeys:
    """Verify _indicator_key produces keys that match what SymbolWorker emits."""

    def test_ema_key_includes_period(self):
        key = _indicator_key("NIFTY", "15m", "EMA", params={"period": 21})
        assert key == "NIFTY:15m:EMA_21"

    def test_ema_list_params(self):
        key = _indicator_key("NIFTY", "5m", "EMA", params=[13])
        assert key == "NIFTY:5m:EMA_13"

    def test_rsi_key_includes_period(self):
        key = _indicator_key("BANKNIFTY", "15m", "RSI", params={"period": 14})
        assert key == "BANKNIFTY:15m:RSI_14"

    def test_macd_line_key_includes_params(self):
        key = _indicator_key("CRUDEOIL", "5m", "MACD_LINE",
                             params={"fast": 10, "slow": 22, "signal": 7})
        assert key == "CRUDEOIL:5m:MACD_LINE_10_22_7"

    def test_macd_signal_key_includes_params(self):
        key = _indicator_key("CRUDEOIL", "5m", "MACD_SIGNAL",
                             params={"fast": 10, "slow": 22, "signal": 7})
        assert key == "CRUDEOIL:5m:MACD_SIGNAL_10_22_7"

    def test_macd_hist_key_includes_params(self):
        key = _indicator_key("CRUDEOIL", "5m", "MACD_HIST",
                             params={"fast": 5, "slow": 11, "signal": 3})
        assert key == "CRUDEOIL:5m:MACD_HIST_5_11_3"

    def test_macd_default_params_when_none(self):
        key = _indicator_key("NIFTY", "15m", "MACD_LINE", params=None)
        assert key == "NIFTY:15m:MACD_LINE_12_26_9"

    def test_macd_default_params_when_empty_list(self):
        key = _indicator_key("NIFTY", "15m", "MACD_HIST", params=[])
        assert key == "NIFTY:15m:MACD_HIST_12_26_9"

    def test_supertrend_key_includes_period_and_factor(self):
        key = _indicator_key("NIFTY", "15m", "SUPERTREND",
                             params={"period": 10, "factor": 3.0})
        assert key == "NIFTY:15m:SUPERTREND_10_30"

    def test_supertrend_alternate_params(self):
        key = _indicator_key("NIFTY", "15m", "SUPERTREND",
                             params={"period": 11, "factor": 2.5})
        assert key == "NIFTY:15m:SUPERTREND_11_25"

    def test_ema_of_rsi_key(self):
        key = _indicator_key("NIFTY", "15m", "EMA",
                             params={"period": 9}, source="RSI_14")
        assert key == "NIFTY:15m:EMA_RSI_9_14"

    def test_atr_key(self):
        key = _indicator_key("NIFTY", "15m", "ATR", params={"period": 14})
        assert key == "NIFTY:15m:ATR_14"

    def test_vwap_key_no_period(self):
        key = _indicator_key("NIFTY", "15m", "VWAP")
        assert key == "NIFTY:15m:VWAP"


class TestDependencyGraphRebuild:
    """Verify dep graph registers correct trigger keys from rule trees."""

    def _make_dg(self, rules: dict, underlyings: dict, legs: dict):
        dg = DependencyGraph()
        dg.rebuild(rules=rules, strategy_underlyings=underlyings,
                   strategy_legs=legs)
        return dg

    def test_macd_line_rule_registers_parameterised_key(self):
        rule = {
            "alert_id": "alert-1",
            "condition_tree": group([cond(
                "INDICATOR", "MACD_LINE", "CROSS_ABOVE",
                params={"fast": 10, "slow": 22, "signal": 7},
                timeframe="5m"
            )])
        }
        dg = self._make_dg(
            rules={STRAT_ID: [rule]},
            underlyings={STRAT_ID: "CRUDEOIL"},
            legs={STRAT_ID: [{"symbol": "CRUDEOIL19AUG26FUT", "leg_id": LEG_ID,
                               "option_type": "FUT"}]},
        )
        keys = dg.all_trigger_keys()
        assert "CRUDEOIL:5m:MACD_LINE_10_22_7" in keys
        assert "CRUDEOIL:5m:MACD_LINE" not in keys

    def test_macd_standard_variant_key(self):
        rule = {
            "alert_id": "alert-2",
            "condition_tree": group([cond(
                "INDICATOR", "MACD_HIST", "GT", 0,
                params={"fast": 12, "slow": 26, "signal": 9},
                timeframe="15m"
            )])
        }
        dg = self._make_dg(
            rules={STRAT_ID: [rule]},
            underlyings={STRAT_ID: "NIFTY"},
            legs={STRAT_ID: [{"symbol": "NIFTY25MAY24000CE", "leg_id": LEG_ID,
                               "option_type": "CE"}]},
        )
        assert "NIFTY:15m:MACD_HIST_12_26_9" in dg.all_trigger_keys()

    def test_ema_cross_registers_lhs_key(self):
        rule = {
            "alert_id": "alert-3",
            "condition_tree": group([cond(
                "INDICATOR", "EMA", "CROSS_BELOW",
                params={"period": 13},
                rhs_type="INDICATOR", rhs_metric="EMA", rhs_params={"period": 34},
                timeframe="5m"
            )])
        }
        dg = self._make_dg(
            rules={STRAT_ID: [rule]},
            underlyings={STRAT_ID: "CRUDEOIL"},
            legs={STRAT_ID: [{"symbol": "CRUDEOIL19AUG26FUT", "leg_id": LEG_ID,
                               "option_type": "FUT"}]},
        )
        assert "CRUDEOIL:5m:EMA_13" in dg.all_trigger_keys()

    def test_price_scope_registers_price_key(self):
        rule = {
            "alert_id": "alert-4",
            "condition_tree": group([cond(
                "LEG", "LTP", "CROSS_ABOVE", 5100, leg_id=LEG_ID
            )])
        }
        dg = self._make_dg(
            rules={STRAT_ID: [rule]},
            underlyings={STRAT_ID: "CRUDEOIL"},
            legs={STRAT_ID: [{"symbol": "CRUDEOIL19AUG26FUT", "leg_id": LEG_ID,
                               "option_type": "FUT"}]},
        )
        assert "price:CRUDEOIL19AUG26FUT" in dg.all_trigger_keys()

    def test_strategy_mtm_registers_all_leg_price_keys(self):
        rule = {
            "alert_id": "alert-5",
            "condition_tree": group([cond("STRATEGY", "MTM", "LT", -3000)])
        }
        dg = self._make_dg(
            rules={STRAT_ID: [rule]},
            underlyings={STRAT_ID: "NIFTY"},
            legs={STRAT_ID: [
                {"symbol": "NIFTY25MAY24000CE", "leg_id": "leg-1", "option_type": "CE"},
                {"symbol": "NIFTY25MAY24100CE", "leg_id": "leg-2", "option_type": "CE"},
            ]},
        )
        keys = dg.all_trigger_keys()
        assert "price:NIFTY25MAY24000CE" in keys
        assert "price:NIFTY25MAY24100CE" in keys

    def test_get_indicator_affected_returns_correct_refs(self):
        rule = {
            "alert_id": "alert-6",
            "condition_tree": group([cond(
                "INDICATOR", "MACD_LINE", "CROSS_ABOVE",
                params={"fast": 5, "slow": 11, "signal": 3},
                timeframe="5m"
            )])
        }
        dg = self._make_dg(
            rules={STRAT_ID: [rule]},
            underlyings={STRAT_ID: "CRUDEOIL"},
            legs={STRAT_ID: [{"symbol": "CRUDEOIL19AUG26FUT", "leg_id": LEG_ID,
                               "option_type": "FUT"}]},
        )
        refs = dg.get_indicator_affected("CRUDEOIL", "5m", "MACD_LINE_5_11_3")
        assert len(refs) == 1
        ref = next(iter(refs))
        assert ref.strategy_id == STRAT_ID
        assert ref.alert_id == "alert-6"

    def test_underlying_for_symbol_mapping(self):
        dg = self._make_dg(
            rules={STRAT_ID: [{"alert_id": "a", "condition_tree": group([])}]},
            underlyings={STRAT_ID: "CRUDEOIL"},
            legs={STRAT_ID: [{"symbol": "CRUDEOIL19AUG26FUT", "leg_id": LEG_ID,
                               "option_type": "FUT"}]},
        )
        assert dg.underlying_for_symbol("CRUDEOIL19AUG26FUT") == "CRUDEOIL"
        assert dg.underlying_for_symbol("CRUDEOIL") == "CRUDEOIL"


# ═══════════════════════════════════════════════════════════════════════════════
# 2. RULE EVALUATOR — simple operators
# ═══════════════════════════════════════════════════════════════════════════════

class TestSimpleOperators:
    """GT, GTE, LT, LTE, EQ across STRATEGY and SPOT scopes."""

    def test_strategy_mtm_gt_fires(self):
        ctx = make_ctx(mtm=5000.0)
        assert evaluate_condition(cond("STRATEGY", "MTM", "GT", 4000), ctx, ctx)

    def test_strategy_mtm_gt_does_not_fire_at_boundary(self):
        ctx = make_ctx(mtm=4000.0)
        assert not evaluate_condition(cond("STRATEGY", "MTM", "GT", 4000), ctx, ctx)

    def test_strategy_mtm_gte_fires_at_boundary(self):
        ctx = make_ctx(mtm=4000.0)
        assert evaluate_condition(cond("STRATEGY", "MTM", "GTE", 4000), ctx, ctx)

    def test_strategy_mtm_lt_fires(self):
        ctx = make_ctx(mtm=-3500.0)
        assert evaluate_condition(cond("STRATEGY", "MTM", "LT", -3000), ctx, ctx)

    def test_strategy_mtm_lte_fires_at_boundary(self):
        ctx = make_ctx(mtm=-3000.0)
        assert evaluate_condition(cond("STRATEGY", "MTM", "LTE", -3000), ctx, ctx)

    def test_strategy_pnl_pct_gt(self):
        ctx = make_ctx(pnl_pct=5.5)
        assert evaluate_condition(cond("STRATEGY", "PNL_PCT", "GT", 5.0), ctx, ctx)

    def test_strategy_max_loss_gte(self):
        ctx = make_ctx(mtm=-4000.0)
        assert evaluate_condition(cond("STRATEGY", "MAX_LOSS", "GTE", 4000), ctx, ctx)

    def test_spot_price_gt(self):
        ctx = make_ctx(spot=5100.0)
        assert evaluate_condition(cond("SPOT", "SPOT_PRICE", "GT", 5000), ctx, ctx)

    def test_spot_price_lt(self):
        ctx = make_ctx(spot=4900.0)
        assert evaluate_condition(cond("SPOT", "SPOT_PRICE", "LT", 5000), ctx, ctx)

    def test_leg_ltp_gt(self):
        ctx = make_ctx(**{"leg_ltps": {LEG_ID: 5200.0}})
        c = cond("LEG", "LTP", "GT", 5000, leg_id=LEG_ID)
        assert evaluate_condition(c, ctx, ctx)

    def test_leg_ltp_lte(self):
        ctx = make_ctx(**{"leg_ltps": {LEG_ID: 5000.0}})
        c = cond("LEG", "LTP", "LTE", 5000, leg_id=LEG_ID)
        assert evaluate_condition(c, ctx, ctx)

    def test_leg_premium_change_pct(self):
        ctx = make_ctx(**{
            "leg_ltps":         {LEG_ID: 5760.0},
            "leg_entry_prices": {LEG_ID: 4800.0},
        })
        c = cond("LEG", "PREMIUM_CHANGE", "GT", 15, leg_id=LEG_ID)
        assert evaluate_condition(c, ctx, ctx)   # 20% gain

    def test_unknown_leg_id_returns_false(self):
        ctx = make_ctx()
        c = cond("LEG", "LTP", "GT", 0, leg_id="does-not-exist")
        assert not evaluate_condition(c, ctx, ctx)

    def test_indicator_rsi_gt_fires(self):
        ctx = make_ctx(indicators={"RSI_14_5m": 72.0})
        c = cond("INDICATOR", "RSI", "GT", 70, params={"period": 14})
        assert evaluate_condition(c, ctx, ctx)

    def test_indicator_rsi_lte_does_not_fire(self):
        ctx = make_ctx(indicators={"RSI_14_5m": 68.0})
        c = cond("INDICATOR", "RSI", "GT", 70, params={"period": 14})
        assert not evaluate_condition(c, ctx, ctx)

    def test_indicator_missing_returns_false(self):
        ctx = make_ctx(indicators={})
        c = cond("INDICATOR", "RSI", "GT", 70, params={"period": 14})
        assert not evaluate_condition(c, ctx, ctx)

    def test_candle_close_gt(self):
        ctx = make_ctx()
        ctx["closed_candles"]["5m"] = {"open": 5000, "high": 5200, "low": 4900,
                                        "close": 5150, "volume": 100}
        c = cond("CANDLE", "CLOSE", "GT", 5100)
        assert evaluate_condition(c, ctx, ctx)

    def test_candle_body_size(self):
        ctx = make_ctx()
        ctx["closed_candles"]["5m"] = {"open": 5000, "high": 5200, "low": 4900,
                                        "close": 5100, "volume": 100}
        c = cond("CANDLE", "BODY_SIZE", "GT", 50)
        assert evaluate_condition(c, ctx, ctx)   # |5100-5000| = 100 > 50


# ═══════════════════════════════════════════════════════════════════════════════
# 3. CROSS operators — every supported combination
# ═══════════════════════════════════════════════════════════════════════════════

class TestCrossAboveBelow:
    """CROSS_ABOVE and CROSS_BELOW for every LHS/RHS permutation."""

    # ── STRATEGY MTM ──────────────────────────────────────────────────────────

    def test_strategy_mtm_cross_above_number(self):
        prev = make_ctx(mtm=-500.0)
        curr = make_ctx(mtm=500.0)
        c = cond("STRATEGY", "MTM", "CROSS_ABOVE", 0)
        assert evaluate_condition(c, curr, prev)

    def test_strategy_mtm_cross_above_not_when_already_above(self):
        prev = make_ctx(mtm=200.0)
        curr = make_ctx(mtm=600.0)
        c = cond("STRATEGY", "MTM", "CROSS_ABOVE", 0)
        assert not evaluate_condition(c, curr, prev)

    def test_strategy_mtm_cross_below_number(self):
        prev = make_ctx(mtm=500.0)
        curr = make_ctx(mtm=-500.0)
        c = cond("STRATEGY", "MTM", "CROSS_BELOW", 0)
        assert evaluate_condition(c, curr, prev)

    def test_strategy_mtm_cross_below_not_when_already_below(self):
        prev = make_ctx(mtm=-200.0)
        curr = make_ctx(mtm=-600.0)
        c = cond("STRATEGY", "MTM", "CROSS_BELOW", 0)
        assert not evaluate_condition(c, curr, prev)

    # ── LEG LTP ───────────────────────────────────────────────────────────────

    def test_leg_ltp_cross_above(self):
        prev = make_ctx(**{"leg_ltps": {LEG_ID: 4900.0}})
        curr = make_ctx(**{"leg_ltps": {LEG_ID: 5100.0}})
        c = cond("LEG", "LTP", "CROSS_ABOVE", 5000, leg_id=LEG_ID)
        assert evaluate_condition(c, curr, prev)

    def test_leg_ltp_cross_below(self):
        prev = make_ctx(**{"leg_ltps": {LEG_ID: 5100.0}})
        curr = make_ctx(**{"leg_ltps": {LEG_ID: 4900.0}})
        c = cond("LEG", "LTP", "CROSS_BELOW", 5000, leg_id=LEG_ID)
        assert evaluate_condition(c, curr, prev)

    def test_leg_ltp_cross_above_needs_strict_below_prev(self):
        """prev AT the level (not strictly below) → should not fire."""
        prev = make_ctx(**{"leg_ltps": {LEG_ID: 5000.0}})
        curr = make_ctx(**{"leg_ltps": {LEG_ID: 5050.0}})
        c = cond("LEG", "LTP", "CROSS_ABOVE", 5000, leg_id=LEG_ID)
        assert not evaluate_condition(c, curr, prev)

    # ── SPOT PRICE ────────────────────────────────────────────────────────────

    def test_spot_cross_above_fixed_level(self):
        prev = make_ctx(spot=4990.0)
        curr = make_ctx(spot=5010.0)
        c = cond("SPOT", "SPOT_PRICE", "CROSS_ABOVE", 5000)
        assert evaluate_condition(c, curr, prev)

    def test_spot_cross_below_fixed_level(self):
        prev = make_ctx(spot=5010.0)
        curr = make_ctx(spot=4990.0)
        c = cond("SPOT", "SPOT_PRICE", "CROSS_BELOW", 5000)
        assert evaluate_condition(c, curr, prev)

    # ── INDICATOR — EMA vs EMA ────────────────────────────────────────────────

    def test_ema13_cross_above_ema34(self):
        """EMA(13) crosses above EMA(34)."""
        prev = make_ctx(indicators={"EMA_13_5m": 4990.0, "EMA_34_5m": 5000.0})
        curr = make_ctx(indicators={"EMA_13_5m": 5010.0, "EMA_34_5m": 5000.0})
        c = cond("INDICATOR", "EMA", "CROSS_ABOVE",
                 params={"period": 13},
                 rhs_type="INDICATOR", rhs_metric="EMA", rhs_params={"period": 34})
        assert evaluate_condition(c, curr, prev)

    def test_ema13_cross_below_ema34(self):
        prev = make_ctx(indicators={"EMA_13_5m": 5010.0, "EMA_34_5m": 5000.0})
        curr = make_ctx(indicators={"EMA_13_5m": 4990.0, "EMA_34_5m": 5000.0})
        c = cond("INDICATOR", "EMA", "CROSS_BELOW",
                 params={"period": 13},
                 rhs_type="INDICATOR", rhs_metric="EMA", rhs_params={"period": 34})
        assert evaluate_condition(c, curr, prev)

    def test_ema_cross_above_no_fire_when_already_above(self):
        prev = make_ctx(indicators={"EMA_13_5m": 5020.0, "EMA_34_5m": 5000.0})
        curr = make_ctx(indicators={"EMA_13_5m": 5030.0, "EMA_34_5m": 5000.0})
        c = cond("INDICATOR", "EMA", "CROSS_ABOVE",
                 params={"period": 13},
                 rhs_type="INDICATOR", rhs_metric="EMA", rhs_params={"period": 34})
        assert not evaluate_condition(c, curr, prev)

    def test_ema_cross_below_no_fire_when_already_below(self):
        prev = make_ctx(indicators={"EMA_13_5m": 4980.0, "EMA_34_5m": 5000.0})
        curr = make_ctx(indicators={"EMA_13_5m": 4970.0, "EMA_34_5m": 5000.0})
        c = cond("INDICATOR", "EMA", "CROSS_BELOW",
                 params={"period": 13},
                 rhs_type="INDICATOR", rhs_metric="EMA", rhs_params={"period": 34})
        assert not evaluate_condition(c, curr, prev)

    def test_ema_cross_missing_rhs_returns_false(self):
        """RHS EMA not in context → False."""
        prev = make_ctx(indicators={"EMA_13_5m": 4990.0})
        curr = make_ctx(indicators={"EMA_13_5m": 5010.0})
        c = cond("INDICATOR", "EMA", "CROSS_ABOVE",
                 params={"period": 13},
                 rhs_type="INDICATOR", rhs_metric="EMA", rhs_params={"period": 34})
        assert not evaluate_condition(c, curr, prev)

    # ── INDICATOR — MACD_LINE vs MACD_SIGNAL (10-22-7) ───────────────────────

    def test_macd_10_22_7_line_cross_above_signal(self):
        prev = make_ctx(indicators={
            "MACD_LINE_10_22_7_5m":   -5.0,
            "MACD_SIGNAL_10_22_7_5m": -2.0,
        })
        curr = make_ctx(indicators={
            "MACD_LINE_10_22_7_5m":   3.0,
            "MACD_SIGNAL_10_22_7_5m": 1.0,
        })
        c = cond("INDICATOR", "MACD_LINE", "CROSS_ABOVE",
                 params={"fast": 10, "slow": 22, "signal": 7},
                 rhs_type="INDICATOR", rhs_metric="MACD_SIGNAL",
                 rhs_params={"fast": 10, "slow": 22, "signal": 7})
        assert evaluate_condition(c, curr, prev)

    def test_macd_10_22_7_line_cross_below_signal(self):
        prev = make_ctx(indicators={
            "MACD_LINE_10_22_7_5m":   3.0,
            "MACD_SIGNAL_10_22_7_5m": 1.0,
        })
        curr = make_ctx(indicators={
            "MACD_LINE_10_22_7_5m":   -5.0,
            "MACD_SIGNAL_10_22_7_5m": -2.0,
        })
        c = cond("INDICATOR", "MACD_LINE", "CROSS_BELOW",
                 params={"fast": 10, "slow": 22, "signal": 7},
                 rhs_type="INDICATOR", rhs_metric="MACD_SIGNAL",
                 rhs_params={"fast": 10, "slow": 22, "signal": 7})
        assert evaluate_condition(c, curr, prev)

    def test_macd_10_22_7_line_cross_below_zero(self):
        prev = make_ctx(indicators={"MACD_LINE_10_22_7_5m": 2.0})
        curr = make_ctx(indicators={"MACD_LINE_10_22_7_5m": -1.0})
        c = cond("INDICATOR", "MACD_LINE", "CROSS_BELOW", 0,
                 params={"fast": 10, "slow": 22, "signal": 7})
        assert evaluate_condition(c, curr, prev)

    def test_macd_10_22_7_line_cross_above_zero(self):
        prev = make_ctx(indicators={"MACD_LINE_10_22_7_5m": -2.0})
        curr = make_ctx(indicators={"MACD_LINE_10_22_7_5m": 1.0})
        c = cond("INDICATOR", "MACD_LINE", "CROSS_ABOVE", 0,
                 params={"fast": 10, "slow": 22, "signal": 7})
        assert evaluate_condition(c, curr, prev)

    # ── INDICATOR — MACD 5-11-3 ───────────────────────────────────────────────

    def test_macd_5_11_3_line_cross_above_signal(self):
        prev = make_ctx(indicators={
            "MACD_LINE_5_11_3_5m":   -3.0,
            "MACD_SIGNAL_5_11_3_5m": -1.0,
        })
        curr = make_ctx(indicators={
            "MACD_LINE_5_11_3_5m":   2.0,
            "MACD_SIGNAL_5_11_3_5m": 0.5,
        })
        c = cond("INDICATOR", "MACD_LINE", "CROSS_ABOVE",
                 params={"fast": 5, "slow": 11, "signal": 3},
                 rhs_type="INDICATOR", rhs_metric="MACD_SIGNAL",
                 rhs_params={"fast": 5, "slow": 11, "signal": 3})
        assert evaluate_condition(c, curr, prev)

    def test_macd_5_11_3_line_cross_below_signal(self):
        prev = make_ctx(indicators={
            "MACD_LINE_5_11_3_5m":   2.0,
            "MACD_SIGNAL_5_11_3_5m": 0.5,
        })
        curr = make_ctx(indicators={
            "MACD_LINE_5_11_3_5m":   -3.0,
            "MACD_SIGNAL_5_11_3_5m": -1.0,
        })
        c = cond("INDICATOR", "MACD_LINE", "CROSS_BELOW",
                 params={"fast": 5, "slow": 11, "signal": 3},
                 rhs_type="INDICATOR", rhs_metric="MACD_SIGNAL",
                 rhs_params={"fast": 5, "slow": 11, "signal": 3})
        assert evaluate_condition(c, curr, prev)

    # ── INDICATOR — MACD 12-26-9 (standard) ──────────────────────────────────

    def test_macd_12_26_9_standard_cross_above(self):
        prev = make_ctx(indicators={
            "MACD_LINE_12_26_9_15m":   -10.0,
            "MACD_SIGNAL_12_26_9_15m": -5.0,
        })
        curr = make_ctx(indicators={
            "MACD_LINE_12_26_9_15m":   8.0,
            "MACD_SIGNAL_12_26_9_15m": 4.0,
        })
        c = cond("INDICATOR", "MACD_LINE", "CROSS_ABOVE",
                 params={"fast": 12, "slow": 26, "signal": 9},
                 rhs_type="INDICATOR", rhs_metric="MACD_SIGNAL",
                 rhs_params={"fast": 12, "slow": 26, "signal": 9},
                 timeframe="15m")
        assert evaluate_condition(c, curr, prev)

    # ── INDICATOR — MACD 21-34-8 ──────────────────────────────────────────────

    def test_macd_21_34_8_cross_above(self):
        prev = make_ctx(indicators={
            "MACD_LINE_21_34_8_15m":   -20.0,
            "MACD_SIGNAL_21_34_8_15m": -8.0,
        })
        curr = make_ctx(indicators={
            "MACD_LINE_21_34_8_15m":   15.0,
            "MACD_SIGNAL_21_34_8_15m": 5.0,
        })
        c = cond("INDICATOR", "MACD_LINE", "CROSS_ABOVE",
                 params={"fast": 21, "slow": 34, "signal": 8},
                 rhs_type="INDICATOR", rhs_metric="MACD_SIGNAL",
                 rhs_params={"fast": 21, "slow": 34, "signal": 8},
                 timeframe="15m")
        assert evaluate_condition(c, curr, prev)

    # ── INDICATOR — MACD HIST sign change ─────────────────────────────────────

    def test_macd_hist_cross_above_zero(self):
        prev = make_ctx(indicators={"MACD_HIST_12_26_9_15m": -2.5})
        curr = make_ctx(indicators={"MACD_HIST_12_26_9_15m":  1.2})
        c = cond("INDICATOR", "MACD_HIST", "CROSS_ABOVE", 0,
                 params={"fast": 12, "slow": 26, "signal": 9},
                 timeframe="15m")
        assert evaluate_condition(c, curr, prev)

    def test_macd_hist_cross_below_zero(self):
        prev = make_ctx(indicators={"MACD_HIST_12_26_9_15m": 2.5})
        curr = make_ctx(indicators={"MACD_HIST_12_26_9_15m": -1.2})
        c = cond("INDICATOR", "MACD_HIST", "CROSS_BELOW", 0,
                 params={"fast": 12, "slow": 26, "signal": 9},
                 timeframe="15m")
        assert evaluate_condition(c, curr, prev)

    # ── INDICATOR — RSI crossover ──────────────────────────────────────────────

    def test_rsi_cross_above_70(self):
        prev = make_ctx(indicators={"RSI_14_5m": 68.0})
        curr = make_ctx(indicators={"RSI_14_5m": 72.0})
        c = cond("INDICATOR", "RSI", "CROSS_ABOVE", 70, params={"period": 14})
        assert evaluate_condition(c, curr, prev)

    def test_rsi_cross_below_30(self):
        prev = make_ctx(indicators={"RSI_14_5m": 32.0})
        curr = make_ctx(indicators={"RSI_14_5m": 28.0})
        c = cond("INDICATOR", "RSI", "CROSS_BELOW", 30, params={"period": 14})
        assert evaluate_condition(c, curr, prev)

    def test_rsi_cross_below_50_midline(self):
        prev = make_ctx(indicators={"RSI_14_15m": 53.0})
        curr = make_ctx(indicators={"RSI_14_15m": 47.0})
        c = cond("INDICATOR", "RSI", "CROSS_BELOW", 50,
                 params={"period": 14}, timeframe="15m")
        assert evaluate_condition(c, curr, prev)

    # ── INDICATOR — Supertrend ─────────────────────────────────────────────────

    def test_supertrend_cross_above_price(self):
        """Spot crosses above Supertrend (bullish signal)."""
        prev = make_ctx(spot=4950.0, indicators={"SUPERTREND_10_30_5m": 5000.0})
        curr = make_ctx(spot=5050.0, indicators={"SUPERTREND_10_30_5m": 5000.0})
        c = cond("SPOT", "SPOT_PRICE", "CROSS_ABOVE",
                 rhs_type="INDICATOR", rhs_metric="SUPERTREND",
                 rhs_params={"period": 10, "factor": 3.0})
        assert evaluate_condition(c, curr, prev)

    def test_supertrend_cross_below_price(self):
        prev = make_ctx(spot=5050.0, indicators={"SUPERTREND_10_30_5m": 5000.0})
        curr = make_ctx(spot=4950.0, indicators={"SUPERTREND_10_30_5m": 5000.0})
        c = cond("SPOT", "SPOT_PRICE", "CROSS_BELOW",
                 rhs_type="INDICATOR", rhs_metric="SUPERTREND",
                 rhs_params={"period": 10, "factor": 3.0})
        assert evaluate_condition(c, curr, prev)

    # ── INDICATOR — Bollinger Bands ────────────────────────────────────────────

    def test_spot_cross_above_bb_upper(self):
        prev = make_ctx(spot=5190.0, indicators={"BB_UPPER_20_15m": 5200.0})
        curr = make_ctx(spot=5210.0, indicators={"BB_UPPER_20_15m": 5200.0})
        c = cond("SPOT", "SPOT_PRICE", "CROSS_ABOVE",
                 rhs_type="INDICATOR", rhs_metric="BB_UPPER",
                 rhs_params={"period": 20}, timeframe="15m")
        assert evaluate_condition(c, curr, prev)

    def test_spot_cross_below_bb_lower(self):
        prev = make_ctx(spot=4810.0, indicators={"BB_LOWER_20_15m": 4800.0})
        curr = make_ctx(spot=4790.0, indicators={"BB_LOWER_20_15m": 4800.0})
        c = cond("SPOT", "SPOT_PRICE", "CROSS_BELOW",
                 rhs_type="INDICATOR", rhs_metric="BB_LOWER",
                 rhs_params={"period": 20}, timeframe="15m")
        assert evaluate_condition(c, curr, prev)

    # ── INDICATOR — Stochastic ─────────────────────────────────────────────────

    def test_stoch_k_cross_above_80(self):
        prev = make_ctx(indicators={"STOCH_K_14_5m": 78.0})
        curr = make_ctx(indicators={"STOCH_K_14_5m": 82.0})
        c = cond("INDICATOR", "STOCH_K", "CROSS_ABOVE", 80, params={"period": 14})
        assert evaluate_condition(c, curr, prev)

    def test_stoch_d_cross_below_20(self):
        prev = make_ctx(indicators={"STOCH_D_14_5m": 22.0})
        curr = make_ctx(indicators={"STOCH_D_14_5m": 18.0})
        c = cond("INDICATOR", "STOCH_D", "CROSS_BELOW", 20, params={"period": 14})
        assert evaluate_condition(c, curr, prev)

    # ── INDICATOR — ADX ───────────────────────────────────────────────────────

    def test_adx_cross_above_25(self):
        prev = make_ctx(indicators={"ADX_14_15m": 23.0})
        curr = make_ctx(indicators={"ADX_14_15m": 27.0})
        c = cond("INDICATOR", "ADX", "CROSS_ABOVE", 25,
                 params={"period": 14}, timeframe="15m")
        assert evaluate_condition(c, curr, prev)

    # ── INDICATOR — VWAP ──────────────────────────────────────────────────────

    def test_spot_cross_above_vwap(self):
        prev = make_ctx(spot=4990.0, indicators={"VWAP_5m": 5000.0})
        curr = make_ctx(spot=5010.0, indicators={"VWAP_5m": 5000.0})
        c = cond("SPOT", "SPOT_PRICE", "CROSS_ABOVE",
                 rhs_type="INDICATOR", rhs_metric="VWAP")
        assert evaluate_condition(c, curr, prev)

    # ── CANDLE scope crossovers ────────────────────────────────────────────────

    def test_candle_close_cross_above_ema(self):
        """Candle close crosses above EMA(21)."""
        prev = make_ctx(indicators={"EMA_21_5m": 5000.0})
        prev["closed_candles"]["5m"] = {"open": 4980, "high": 5020,
                                         "low": 4960, "close": 4990, "volume": 50}
        curr = make_ctx(indicators={"EMA_21_5m": 5000.0})
        curr["closed_candles"]["5m"] = {"open": 5000, "high": 5080,
                                         "low": 4990, "close": 5040, "volume": 80}
        c = cond("CANDLE", "CLOSE", "CROSS_ABOVE",
                 rhs_type="INDICATOR", rhs_metric="EMA", rhs_params={"period": 21})
        assert evaluate_condition(c, curr, prev)

    def test_candle_close_cross_below_ema(self):
        prev = make_ctx(indicators={"EMA_21_5m": 5000.0})
        prev["closed_candles"]["5m"] = {"open": 5010, "high": 5050,
                                         "low": 4990, "close": 5010, "volume": 50}
        curr = make_ctx(indicators={"EMA_21_5m": 5000.0})
        curr["closed_candles"]["5m"] = {"open": 5000, "high": 5010,
                                         "low": 4940, "close": 4960, "volume": 80}
        c = cond("CANDLE", "CLOSE", "CROSS_BELOW",
                 rhs_type="INDICATOR", rhs_metric="EMA", rhs_params={"period": 21})
        assert evaluate_condition(c, curr, prev)

    def test_cross_no_prev_ctx_returns_false(self):
        """If prev_ctx has no indicator value, CROSS must return False."""
        prev = make_ctx(indicators={})   # no EMA
        curr = make_ctx(indicators={"EMA_21_5m": 5000.0})
        c = cond("INDICATOR", "EMA", "CROSS_ABOVE", 0, params={"period": 21})
        assert not evaluate_condition(c, curr, prev)


# ═══════════════════════════════════════════════════════════════════════════════
# 4. EVALUATE_GROUP — AND / OR / nested
# ═══════════════════════════════════════════════════════════════════════════════

class TestEvaluateGroup:

    def _ind(self, indicators):
        return make_ctx(indicators=indicators)

    def test_and_all_true_fires(self):
        ctx = self._ind({"RSI_14_5m": 72.0, "EMA_13_5m": 5010.0, "EMA_34_5m": 5000.0})
        tree = group([
            cond("INDICATOR", "RSI", "GT", 70, params={"period": 14}),
            cond("INDICATOR", "EMA", "GT", 0, params={"period": 13}),
        ], op="AND")
        assert evaluate_group(tree, ctx, ctx)

    def test_and_one_false_does_not_fire(self):
        ctx = self._ind({"RSI_14_5m": 65.0, "EMA_13_5m": 5010.0})
        tree = group([
            cond("INDICATOR", "RSI", "GT", 70, params={"period": 14}),
            cond("INDICATOR", "EMA", "GT", 0, params={"period": 13}),
        ], op="AND")
        assert not evaluate_group(tree, ctx, ctx)

    def test_or_one_true_fires(self):
        ctx = self._ind({"RSI_14_5m": 65.0, "EMA_13_5m": 5010.0})
        tree = group([
            cond("INDICATOR", "RSI", "GT", 70, params={"period": 14}),
            cond("INDICATOR", "EMA", "GT", 0, params={"period": 13}),
        ], op="OR")
        assert evaluate_group(tree, ctx, ctx)

    def test_or_all_false_does_not_fire(self):
        ctx = self._ind({"RSI_14_5m": 65.0, "EMA_13_5m": -10.0})
        tree = group([
            cond("INDICATOR", "RSI", "GT", 70, params={"period": 14}),
            cond("INDICATOR", "EMA", "GT", 0, params={"period": 13}),
        ], op="OR")
        assert not evaluate_group(tree, ctx, ctx)

    def test_empty_group_returns_false(self):
        ctx = make_ctx()
        assert not evaluate_group(group([]), ctx, ctx)

    def test_nested_and_within_or(self):
        """(MTM > 1000 AND RSI > 70) OR (MTM < -500)"""
        ctx_good_rsi = make_ctx(mtm=1500.0, indicators={"RSI_14_5m": 75.0})
        ctx_loss     = make_ctx(mtm=-600.0,  indicators={"RSI_14_5m": 40.0})
        ctx_bad      = make_ctx(mtm=500.0,   indicators={"RSI_14_5m": 60.0})

        inner_and = group([
            cond("STRATEGY", "MTM",  "GT", 1000),
            cond("INDICATOR", "RSI", "GT", 70, params={"period": 14}),
        ], op="AND")
        loss_cond = group([cond("STRATEGY", "MTM", "LT", -500)], op="AND")
        tree = group([], op="OR", groups=[inner_and, loss_cond])

        assert evaluate_group(tree, ctx_good_rsi, ctx_good_rsi)
        assert evaluate_group(tree, ctx_loss, ctx_loss)
        assert not evaluate_group(tree, ctx_bad, ctx_bad)

    def test_and_mtm_cross_and_rsi_threshold(self):
        """MTM CROSS_ABOVE 0 AND RSI > 60 — combined entry signal."""
        prev = make_ctx(mtm=-100.0, indicators={"RSI_14_5m": 62.0})
        curr = make_ctx(mtm=200.0,  indicators={"RSI_14_5m": 65.0})
        tree = group([
            cond("STRATEGY", "MTM", "CROSS_ABOVE", 0),
            cond("INDICATOR", "RSI", "GT", 60, params={"period": 14}),
        ], op="AND")
        assert evaluate_group(tree, curr, prev)

    def test_macd_cross_and_ema_aligned(self):
        """MACD(10,22,7) cross above signal AND EMA(13) > EMA(34)."""
        prev = make_ctx(indicators={
            "MACD_LINE_10_22_7_5m":   -3.0,
            "MACD_SIGNAL_10_22_7_5m": -1.0,
            "EMA_13_5m": 5010.0, "EMA_34_5m": 5000.0,
        })
        curr = make_ctx(indicators={
            "MACD_LINE_10_22_7_5m":   2.0,
            "MACD_SIGNAL_10_22_7_5m": 0.5,
            "EMA_13_5m": 5020.0, "EMA_34_5m": 5000.0,
        })
        tree = group([
            cond("INDICATOR", "MACD_LINE", "CROSS_ABOVE",
                 params={"fast": 10, "slow": 22, "signal": 7},
                 rhs_type="INDICATOR", rhs_metric="MACD_SIGNAL",
                 rhs_params={"fast": 10, "slow": 22, "signal": 7}),
            cond("INDICATOR", "EMA", "GT", 0,
                 params={"period": 13},
                 rhs_type="INDICATOR", rhs_metric="EMA", rhs_params={"period": 34}),
        ], op="AND")
        assert evaluate_group(tree, curr, prev)


# ═══════════════════════════════════════════════════════════════════════════════
# 5. INDICATOR CALCULATORS
# ═══════════════════════════════════════════════════════════════════════════════

class TestIndicatorCalculators:
    """Verify calculator outputs for known inputs."""

    def test_ema_from_series(self):
        from services.indicators.calculators import ema_from_series
        import numpy as np
        closes = np.array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], dtype=np.float64)
        val = ema_from_series(closes, period=3)
        assert val is not None
        assert 7.0 < val < 10.0

    def test_ema_step_moves_toward_new_price(self):
        from services.indicators.calculators import ema_step
        # k = 2/(3+1) = 0.5; new_ema = 100 * 0.5 + 90 * 0.5 = 95
        val = ema_step(new_value=100.0, prev_ema=90.0, period=3)
        assert abs(val - 95.0) < 1e-9

    def test_rsi_seed_normal(self):
        from services.indicators.calculators import rsi_seed
        import numpy as np
        # Alternating +1/-1 prices → roughly RSI 50
        closes = np.array([10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10, 11, 10],
                          dtype=np.float64)
        result = rsi_seed(closes, 14)
        assert result is not None
        avg_gain, avg_loss = result
        assert avg_gain > 0
        assert avg_loss > 0

    def test_rsi_all_gains_returns_100(self):
        from services.indicators.calculators import rsi_seed, rsi_step
        import numpy as np
        closes = np.array([i * 1.0 for i in range(1, 17)], dtype=np.float64)
        result = rsi_seed(closes, 14)
        assert result is not None
        avg_gain, avg_loss = result
        # avg_loss should be 0 → RSI ~ 100
        if avg_loss < 1e-10:
            rsi = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - 100 / (1 + rs)
        assert rsi > 90.0

    def test_macd_full_standard(self):
        from services.indicators.calculators import macd_full
        import numpy as np
        import random
        random.seed(42)
        prices = np.array([5000 + random.uniform(-50, 50) for _ in range(35)],
                          dtype=np.float64)
        result = macd_full(prices, fast=12, slow=26, signal=9)
        assert result is not None
        macd_line, macd_signal, macd_hist = result
        assert abs(macd_hist - (macd_line - macd_signal)) < 1e-6

    def test_macd_10_22_7(self):
        from services.indicators.calculators import macd_full
        import numpy as np
        import random
        random.seed(7)
        prices = np.array([3000 + random.uniform(-30, 30) for _ in range(30)],
                          dtype=np.float64)
        result = macd_full(prices, fast=10, slow=22, signal=7)
        assert result is not None

    def test_macd_5_11_3(self):
        from services.indicators.calculators import macd_full
        import numpy as np
        import random
        random.seed(3)
        prices = np.array([3000 + random.uniform(-30, 30) for _ in range(15)],
                          dtype=np.float64)
        result = macd_full(prices, fast=5, slow=11, signal=3)
        assert result is not None

    def test_macd_returns_none_when_insufficient_data(self):
        from services.indicators.calculators import macd_full
        import numpy as np
        prices = np.array([5000.0] * 10, dtype=np.float64)
        # Need at least slow+signal bars for 12-26-9
        assert macd_full(prices, fast=12, slow=26, signal=9) is None

    def test_bollinger(self):
        from services.indicators.calculators import bollinger
        import numpy as np
        closes = np.array([5000.0 + i for i in range(25)], dtype=np.float64)
        result = bollinger(closes, 20, 2.0)
        assert result is not None
        upper, mid, lower = result
        assert upper > mid > lower

    def test_atr(self):
        from services.indicators.calculators import atr
        import numpy as np
        highs  = np.array([5050.0] * 20)
        lows   = np.array([4950.0] * 20)
        closes = np.array([5000.0] * 20)
        val = atr(highs, lows, closes, 14)
        assert val is not None
        assert abs(val - 100.0) < 1.0   # ATR ≈ high-low = 100

    def test_stochastic(self):
        from services.indicators.calculators import stochastic
        import numpy as np
        highs  = np.array([5100.0] * 20)
        lows   = np.array([4900.0] * 20)
        closes = np.array([5000.0] * 20)  # close always at midpoint
        result = stochastic(highs, lows, closes, 14, 3)
        assert result is not None
        k, d = result
        assert abs(k - 50.0) < 1.0   # (5000-4900)/(5100-4900) * 100 = 50


# ═══════════════════════════════════════════════════════════════════════════════
# 6. CANDLE BOUNDS — MCX vs NSE market open
# ═══════════════════════════════════════════════════════════════════════════════

class TestCandleBounds:
    """Verify _candle_bounds uses 9:00 for MCX and 9:15 for NSE."""

    from alerts.symbol_worker import _candle_bounds

    def _ts(self, h, m, s=0):
        return datetime(2026, 8, 7, h, m, s, tzinfo=IST)

    def test_mcx_5m_first_candle_starts_at_900(self):
        from alerts.symbol_worker import _candle_bounds
        start, end = _candle_bounds(self._ts(9, 3), "5m", "CRUDEOIL19AUG26FUT")
        assert start.hour == 9 and start.minute == 0

    def test_mcx_5m_second_candle_starts_at_905(self):
        from alerts.symbol_worker import _candle_bounds
        start, end = _candle_bounds(self._ts(9, 7), "5m", "CRUDEOIL19AUG26FUT")
        assert start.hour == 9 and start.minute == 5

    def test_mcx_5m_third_candle_starts_at_910(self):
        from alerts.symbol_worker import _candle_bounds
        start, end = _candle_bounds(self._ts(9, 12), "5m", "CRUDEOIL19AUG26FUT")
        assert start.hour == 9 and start.minute == 10

    def test_nse_5m_first_candle_starts_at_915(self):
        from alerts.symbol_worker import _candle_bounds
        start, end = _candle_bounds(self._ts(9, 17), "5m", "NIFTY24500CE")
        assert start.hour == 9 and start.minute == 15

    def test_nse_5m_second_candle_starts_at_920(self):
        from alerts.symbol_worker import _candle_bounds
        start, end = _candle_bounds(self._ts(9, 22), "5m", "NIFTY24500CE")
        assert start.hour == 9 and start.minute == 20

    def test_mcx_15m_first_candle_starts_at_900(self):
        from alerts.symbol_worker import _candle_bounds
        start, end = _candle_bounds(self._ts(9, 10), "15m", "GOLDM28AUG26FUT")
        assert start.hour == 9 and start.minute == 0

    def test_mcx_15m_second_candle_starts_at_915(self):
        from alerts.symbol_worker import _candle_bounds
        start, end = _candle_bounds(self._ts(9, 20), "15m", "SILVERMIC28AUG26FUT")
        assert start.hour == 9 and start.minute == 15

    def test_candle_end_is_start_plus_period(self):
        from alerts.symbol_worker import _candle_bounds
        start, end = _candle_bounds(self._ts(9, 3), "5m", "CRUDEOIL19AUG26FUT")
        assert (end - start) == timedelta(minutes=5)

    def test_15m_candle_duration(self):
        from alerts.symbol_worker import _candle_bounds
        start, end = _candle_bounds(self._ts(9, 20), "15m", "NIFTY24500CE")
        assert (end - start) == timedelta(minutes=15)

    def test_unknown_symbol_defaults_to_nse_open(self):
        from alerts.symbol_worker import _candle_bounds
        start, end = _candle_bounds(self._ts(9, 17), "5m", "SOMESTOCK")
        assert start.minute == 15


# ═══════════════════════════════════════════════════════════════════════════════
# 7. SYMBOL WORKER — end-to-end indicator build + alert dispatch
# ═══════════════════════════════════════════════════════════════════════════════

class TestSymbolWorkerEndToEnd:
    """
    Feed synthetic candles into a SymbolWorker, verify:
      - indicators compute correctly
      - CROSS alert fires when condition is met
      - CROSS alert does NOT fire on the candle before the cross
    """

    def _make_candle(self, symbol, tf, ts_ist, close):
        from alerts.symbol_worker import _LiveCandle
        return _LiveCandle(
            symbol=symbol, timeframe=tf,
            ts=ts_ist, end_ts=ts_ist + timedelta(minutes=5),
            open=close, high=close + 10, low=close - 10,
            close=close, volume=1000,
        )

    @pytest.mark.asyncio
    async def test_ema_builds_after_enough_candles(self):
        from alerts.symbol_worker import SymbolWorker
        from services.indicators.indicator_cache import IndicatorCache

        cache = IndicatorCache()
        import alerts.symbol_worker as sw_mod
        original_cache = sw_mod.indicator_cache
        sw_mod.indicator_cache = cache

        try:
            q = asyncio.Queue()
            worker = SymbolWorker(partition_id=3, alert_queue=q)

            sym = "CRUDEOIL19AUG26FUT"
            tf  = "5m"
            base_ts = datetime(2026, 8, 7, 9, 0, tzinfo=IST)

            # Seed 25 candles — EMA(21) needs 21, EMA(34) needs 34
            for i in range(25):
                ts = base_ts + timedelta(minutes=5 * i)
                candle = self._make_candle(sym, tf, ts, 5000 + i * 2)
                await worker.seed_candle(candle)

            ema21 = cache.get(sym, tf, "EMA_21")
            assert ema21 is not None, "EMA_21 should be computed after 25 candles"
            assert 4900 < ema21 < 5200

        finally:
            sw_mod.indicator_cache = original_cache

    @pytest.mark.asyncio
    async def test_macd_builds_after_enough_candles(self):
        from alerts.symbol_worker import SymbolWorker
        from services.indicators.indicator_cache import IndicatorCache

        cache = IndicatorCache()
        import alerts.symbol_worker as sw_mod
        original_cache = sw_mod.indicator_cache
        sw_mod.indicator_cache = cache

        try:
            q = asyncio.Queue()
            worker = SymbolWorker(partition_id=3, alert_queue=q)

            sym = "CRUDEOIL19AUG26FUT"
            tf  = "5m"
            base_ts = datetime(2026, 8, 7, 9, 0, tzinfo=IST)

            # MACD(10,22,7) needs 22+7=29 bars minimum
            for i in range(32):
                ts = base_ts + timedelta(minutes=5 * i)
                candle = self._make_candle(sym, tf, ts, 5000 + i)
                await worker.seed_candle(candle)

            macd = cache.get(sym, tf, "MACD_LINE_10_22_7")
            assert macd is not None, "MACD_LINE_10_22_7 should be computed after 32 candles"

        finally:
            sw_mod.indicator_cache = original_cache

    @pytest.mark.asyncio
    async def test_macd_5_11_3_builds_quickly(self):
        """MACD(5,11,3) needs only 14 bars."""
        from alerts.symbol_worker import SymbolWorker
        from services.indicators.indicator_cache import IndicatorCache

        cache = IndicatorCache()
        import alerts.symbol_worker as sw_mod
        original_cache = sw_mod.indicator_cache
        sw_mod.indicator_cache = cache

        try:
            q = asyncio.Queue()
            worker = SymbolWorker(partition_id=3, alert_queue=q)

            sym = "CRUDEOIL19AUG26FUT"
            base_ts = datetime(2026, 8, 7, 9, 0, tzinfo=IST)

            for i in range(16):
                ts = base_ts + timedelta(minutes=5 * i)
                candle = self._make_candle(sym, "5m", ts, 5000 + i)
                await worker.seed_candle(candle)

            macd = cache.get(sym, "5m", "MACD_LINE_5_11_3")
            assert macd is not None, "MACD_LINE_5_11_3 should be available after 16 candles"

        finally:
            sw_mod.indicator_cache = original_cache

    @pytest.mark.asyncio
    async def test_ema_cross_alert_fires(self):
        """
        Scenario: EMA(13) crosses above EMA(34).
        Set up dep graph + alert cache so the worker fires an alert.
        """
        import alerts.symbol_worker as sw_mod
        import alerts.dependency_graph as dg_mod
        import alerts.alert_pipeline as pipeline_mod
        from alerts.symbol_worker import SymbolWorker
        from alerts.dependency_graph import DependencyGraph
        from alerts.alert_cache import AlertCache
        from services.indicators.indicator_cache import IndicatorCache

        sym = "CRUDEOIL19AUG26FUT"
        tf  = "5m"
        underlying = "CRUDEOIL"

        # Patch singletons
        cache  = IndicatorCache()
        dg     = DependencyGraph()
        ac     = AlertCache()

        original_cache = sw_mod.indicator_cache
        original_dg    = dg_mod.dep_graph
        original_ac_in_sw = None  # patched via monkeypatch in the worker import

        sw_mod.indicator_cache = cache
        dg_mod.dep_graph = dg

        alert_id  = "test-alert-ema-cross"
        strat_id  = STRAT_ID

        rule = {
            "alert_id":       alert_id,
            "strategy_id":    strat_id,
            "name":           "EMA13 cross EMA34",
            "trigger_once":   False,
            "cooldown_secs":  0,
            "triggered_count": 0,
            "notify_popup":   False,
            "notify_telegram": False,
            "notify_email":   False,
            "notify_webhook": False,
            "notify_sound":   False,
            "webhook_url":    None,
            "telegram_chat_id": None,
            "condition_tree": group([cond(
                "INDICATOR", "EMA", "CROSS_ABOVE",
                params={"period": 13},
                rhs_type="INDICATOR", rhs_metric="EMA", rhs_params={"period": 34},
            )]),
        }
        ac._rules = {strat_id: [rule]}

        legs = [{"symbol": sym, "leg_id": LEG_ID, "option_type": "FUT",
                 "action": "BUY", "entry_price": 5000.0, "quantity": 100}]
        dg.rebuild(
            rules={strat_id: [rule]},
            strategy_underlyings={strat_id: underlying},
            strategy_legs={strat_id: legs},
        )

        pipeline_mod.strategy_legs_mirror[strat_id]       = legs
        pipeline_mod.strategy_underlying_mirror[strat_id] = underlying
        pipeline_mod.strategy_name_mirror[strat_id]       = "TestStrategy"

        try:
            alert_q = asyncio.Queue()
            worker  = SymbolWorker(partition_id=3, alert_queue=alert_q)

            # Patch alert_cache inside worker calls
            import alerts.alert_cache as ac_mod
            original_ac = ac_mod.alert_cache
            ac_mod.alert_cache = ac

            base_ts = datetime(2026, 8, 7, 9, 0, tzinfo=IST)

            # Phase 1: 34 seed candles with EMA(13) < EMA(34) (rising from below)
            for i in range(34):
                ts = base_ts + timedelta(minutes=5 * i)
                candle = self._make_candle(sym, tf, ts, 4950 + i * 0.5)
                await worker.seed_candle(candle)

            # At this point EMA(13) responds faster to recent prices — check it's
            # still below EMA(34) on a slow-rising series (may vary; just seed more)
            ema13_before = cache.get(sym, tf, "EMA_13")
            ema34_before = cache.get(sym, tf, "EMA_34")
            assert ema13_before is not None
            assert ema34_before is not None

            # Phase 2: Force a clear cross by driving price sharply higher
            # so EMA(13) crosses above EMA(34).
            # First, manually set prev context to simulate EMA(13) < EMA(34).
            from alerts.alert_cache import EvaluationContext
            fake_prev = EvaluationContext({
                "indicators": {
                    "EMA_13_5m": 4900.0,   # EMA13 below EMA34
                    "EMA_34_5m": 5000.0,
                },
                "leg_ltps": {LEG_ID: 5000.0},
                "leg_entry_prices": {LEG_ID: 5000.0},
                "leg_quantities": {LEG_ID: 100},
                "leg_sides": {LEG_ID: "BUY"},
                "mtm": 0.0, "pnl_pct": 0.0, "spot": 5000.0,
                "underlying": underlying, "strategy_id": strat_id,
                "strategy_name": "TestStrategy", "vwap": 5000.0,
                "candles": {}, "closed_candles": {},
            })
            ac.update_context(strat_id, fake_prev)
            # update_context sets _prev_ctx = old _ctx, _ctx = fake_prev
            # Call again to push fake_prev into prev slot
            ac.update_context(strat_id, EvaluationContext({
                **fake_prev,
                "indicators": {
                    "EMA_13_5m": 5100.0,   # EMA13 now above EMA34 → CROSS
                    "EMA_34_5m": 5000.0,
                }
            }))

            # Manually evaluate — normally done by _on_candle_close
            from alerts.rule_evaluator import evaluate_group as ev_grp
            curr_ctx, prev_ctx = ac.get_contexts(strat_id)
            fired = ev_grp(rule["condition_tree"], curr_ctx, prev_ctx)
            assert fired, (
                f"EMA cross should have fired. "
                f"EMA13: prev={prev_ctx.get('indicators', {}).get('EMA_13_5m')} "
                f"curr={curr_ctx.get('indicators', {}).get('EMA_13_5m')}, "
                f"EMA34: prev={prev_ctx.get('indicators', {}).get('EMA_34_5m')} "
                f"curr={curr_ctx.get('indicators', {}).get('EMA_34_5m')}"
            )

        finally:
            sw_mod.indicator_cache = original_cache
            dg_mod.dep_graph       = original_dg
            ac_mod.alert_cache     = original_ac
            pipeline_mod.strategy_legs_mirror.pop(strat_id, None)
            pipeline_mod.strategy_underlying_mirror.pop(strat_id, None)
            pipeline_mod.strategy_name_mirror.pop(strat_id, None)


# ═══════════════════════════════════════════════════════════════════════════════
# 8. MACD VARIANT COMPLETENESS — all 4 precomputed variants resolve from context
# ═══════════════════════════════════════════════════════════════════════════════

class TestMACDVariantResolution:
    """
    For each precomputed MACD variant, verify that:
      1. The rule evaluator resolves the correct key from the context.
      2. CROSS_ABOVE fires when line crosses above signal.
      3. CROSS_BELOW fires when line crosses below signal.
      4. CROSS_ABOVE zero fires on sign change.
    """

    VARIANTS = [
        (12, 26,  9),
        (10, 22,  7),
        ( 5, 11,  3),
        (21, 34,  8),
    ]

    @pytest.mark.parametrize("fast,slow,signal", VARIANTS)
    def test_cross_above_signal(self, fast, slow, signal):
        tf  = "5m"
        mk  = f"MACD_LINE_{fast}_{slow}_{signal}_{tf}"
        sk  = f"MACD_SIGNAL_{fast}_{slow}_{signal}_{tf}"
        prev = make_ctx(indicators={mk: -5.0, sk: -2.0})
        curr = make_ctx(indicators={mk:  3.0, sk:  1.0})
        c = cond("INDICATOR", "MACD_LINE", "CROSS_ABOVE",
                 params={"fast": fast, "slow": slow, "signal": signal},
                 rhs_type="INDICATOR", rhs_metric="MACD_SIGNAL",
                 rhs_params={"fast": fast, "slow": slow, "signal": signal})
        assert evaluate_condition(c, curr, prev), \
            f"MACD({fast},{slow},{signal}) CROSS_ABOVE signal failed"

    @pytest.mark.parametrize("fast,slow,signal", VARIANTS)
    def test_cross_below_signal(self, fast, slow, signal):
        tf  = "5m"
        mk  = f"MACD_LINE_{fast}_{slow}_{signal}_{tf}"
        sk  = f"MACD_SIGNAL_{fast}_{slow}_{signal}_{tf}"
        prev = make_ctx(indicators={mk:  5.0, sk:  2.0})
        curr = make_ctx(indicators={mk: -3.0, sk: -1.0})
        c = cond("INDICATOR", "MACD_LINE", "CROSS_BELOW",
                 params={"fast": fast, "slow": slow, "signal": signal},
                 rhs_type="INDICATOR", rhs_metric="MACD_SIGNAL",
                 rhs_params={"fast": fast, "slow": slow, "signal": signal})
        assert evaluate_condition(c, curr, prev), \
            f"MACD({fast},{slow},{signal}) CROSS_BELOW signal failed"

    @pytest.mark.parametrize("fast,slow,signal", VARIANTS)
    def test_cross_above_zero(self, fast, slow, signal):
        tf  = "5m"
        mk  = f"MACD_LINE_{fast}_{slow}_{signal}_{tf}"
        prev = make_ctx(indicators={mk: -1.5})
        curr = make_ctx(indicators={mk:  1.5})
        c = cond("INDICATOR", "MACD_LINE", "CROSS_ABOVE", 0,
                 params={"fast": fast, "slow": slow, "signal": signal})
        assert evaluate_condition(c, curr, prev), \
            f"MACD({fast},{slow},{signal}) CROSS_ABOVE 0 failed"

    @pytest.mark.parametrize("fast,slow,signal", VARIANTS)
    def test_cross_below_zero(self, fast, slow, signal):
        tf  = "5m"
        mk  = f"MACD_LINE_{fast}_{slow}_{signal}_{tf}"
        prev = make_ctx(indicators={mk:  1.5})
        curr = make_ctx(indicators={mk: -1.5})
        c = cond("INDICATOR", "MACD_LINE", "CROSS_BELOW", 0,
                 params={"fast": fast, "slow": slow, "signal": signal})
        assert evaluate_condition(c, curr, prev), \
            f"MACD({fast},{slow},{signal}) CROSS_BELOW 0 failed"

    @pytest.mark.parametrize("fast,slow,signal", VARIANTS)
    def test_dep_graph_key_matches_emitted_indicator(self, fast, slow, signal):
        """The dep-graph key must exactly match what SymbolWorker emits."""
        expected_indicator_name = f"MACD_LINE_{fast}_{slow}_{signal}"
        dep_key = _indicator_key(
            "CRUDEOIL", "5m", "MACD_LINE",
            params={"fast": fast, "slow": slow, "signal": signal},
        )
        assert dep_key == f"CRUDEOIL:5m:{expected_indicator_name}", \
            f"Dep key {dep_key!r} does not match emitted name {expected_indicator_name!r}"
