"""
Recursive condition-tree evaluator.
Operates on plain dicts (loaded from JSON) — no DB queries.
"""
from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# ── Operators ──────────────────────────────────────────────────────────────────

_OPS = {
    "GTE":   lambda a, b: a >= b,
    "LTE":   lambda a, b: a <= b,
    "GT":    lambda a, b: a > b,
    "LT":    lambda a, b: a < b,
    "EQ":    lambda a, b: abs(a - b) < 1e-9,
}


def _resolve_value(condition: dict, ctx: dict) -> float | None:
    """Extract the actual metric value from the evaluation context."""
    scope  = condition.get("scope", "")
    metric = condition.get("metric", "")
    leg_id = condition.get("leg_id")

    if scope == "STRATEGY":
        mtm = ctx.get("mtm", 0.0)
        if metric == "MTM":
            return mtm
        if metric in ("PNL_PCT", "MTM_PCT"):
            return ctx.get("pnl_pct", 0.0)
        if metric in ("PROFIT", "MAX_PROFIT"):
            return ctx.get("max_profit", max(mtm, 0.0))
        if metric in ("LOSS", "MAX_LOSS"):
            # Return absolute drawdown value so user can write "MAX_LOSS >= 3000"
            v = ctx.get("max_loss", abs(min(mtm, 0.0)))
            return abs(v)

    elif scope == "LEG":
        if not leg_id:
            return None
        legs = ctx.get("leg_ltps", {})
        ltp  = legs.get(leg_id)
        if ltp is None:
            return None
        if metric == "LTP":
            return ltp
        entry_prices = ctx.get("leg_entry_prices", {})
        entry = entry_prices.get(leg_id, 0.0)
        if metric in ("PREMIUM_CHANGE", "PREMIUM_CHG_PCT"):
            if entry and entry > 0:
                return ((ltp - entry) / entry) * 100
            return 0.0
        if metric == "PREMIUM_CHG_ABS":
            return ltp - entry
        if metric in ("PNL", "LEG_PNL"):
            qty  = ctx.get("leg_quantities", {}).get(leg_id, 1)
            side = ctx.get("leg_sides", {}).get(leg_id, "BUY")
            mult = 1 if side == "BUY" else -1
            return mult * (ltp - entry) * qty

    elif scope == "SPOT":
        spot = ctx.get("spot", 0.0)
        if metric == "SPOT_PRICE":
            return spot
        if metric == "SPOT_CHG_PCT":
            prev_close = ctx.get("prev_close", spot)
            if prev_close and prev_close > 0:
                return ((spot - prev_close) / prev_close) * 100
            return 0.0
        if metric == "SPOT_VS_VWAP":
            return spot - ctx.get("vwap", spot)
        # Legacy
        if metric == "SPOT_VS_SUPERTREND":
            return None

    elif scope == "INDICATOR":
        # Indicators are keyed by metric + timeframe in the context for multi-TF support
        timeframe  = condition.get("timeframe") or "15m"
        params     = condition.get("params", {})
        indicators = ctx.get("indicators", {})
        # Try keyed lookup first: "{METRIC}_{TF}" e.g. "RSI_15m"
        keyed = indicators.get(f"{metric}_{timeframe}")
        if keyed is not None:
            return float(keyed)
        # Fall back to flat key (legacy / simple setup)
        simple_keys = {
            "RSI":        "rsi",
            "SUPERTREND": "supertrend",
            "EMA":        "ema",
            "SMA":        "sma",
            "MACD_HIST":  "macd_hist",
            "BB_UPPER":   "bb_upper",
            "BB_LOWER":   "bb_lower",
            "VWAP":       "vwap",
            "ATR":        "atr",
            "ADX":        "adx",
            "STOCH_K":    "stoch_k",
            # Legacy
            "EMA_CROSS":  "ema_cross",
        }
        flat_key = simple_keys.get(metric, metric.lower())
        val = indicators.get(flat_key)
        return float(val) if val is not None else None

    return None


def evaluate_condition(
    condition: dict,
    ctx: dict,
    prev_ctx: dict,
) -> bool:
    """Evaluate a single condition leaf."""
    operator = condition.get("operator", "GTE")
    threshold = condition.get("value")

    current = _resolve_value(condition, ctx)
    if current is None:
        return False

    if operator == "CROSS_ABOVE":
        prev = _resolve_value(condition, prev_ctx)
        if prev is None:
            return False
        ref = threshold if threshold is not None else 0.0
        return prev < ref <= current

    if operator == "CROSS_BELOW":
        prev = _resolve_value(condition, prev_ctx)
        if prev is None:
            return False
        ref = threshold if threshold is not None else 0.0
        return prev > ref >= current

    if threshold is None:
        return False

    op_fn = _OPS.get(operator)
    if op_fn is None:
        logger.warning("Unknown operator %s", operator)
        return False

    try:
        return op_fn(float(current), float(threshold))
    except (TypeError, ValueError):
        return False


def evaluate_group(
    group: dict,
    ctx: dict,
    prev_ctx: dict,
) -> bool:
    """Recursively evaluate a condition group (AND/OR)."""
    op = group.get("op", "AND").upper()
    results: list[bool] = []

    for cond in group.get("conditions", []):
        results.append(evaluate_condition(cond, ctx, prev_ctx))

    for sub in group.get("groups", []):
        results.append(evaluate_group(sub, ctx, prev_ctx))

    if not results:
        return False

    return all(results) if op == "AND" else any(results)


def build_condition_summary(tree: dict, depth: int = 0) -> str:
    """Human-readable summary of a condition tree (for AlertHistory)."""
    op = tree.get("op", "AND")
    parts: list[str] = []

    for cond in tree.get("conditions", []):
        scope  = cond.get("scope", "")
        metric = cond.get("metric", "")
        oper   = cond.get("operator", "")
        value  = cond.get("value")
        leg_id = cond.get("leg_id")
        leg_part = f" [leg:{leg_id[:6]}]" if leg_id else ""
        val_part = f" {value}" if value is not None else ""
        parts.append(f"{scope}{leg_part} {metric} {oper}{val_part}")

    for sub in tree.get("groups", []):
        parts.append(f"({build_condition_summary(sub, depth + 1)})")

    return f" {op} ".join(parts) if parts else ""
