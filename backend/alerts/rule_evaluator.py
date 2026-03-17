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
        if metric == "MTM":
            return ctx.get("mtm", 0.0)
        if metric == "PNL_PCT":
            return ctx.get("pnl_pct", 0.0)
        if metric == "PROFIT":
            v = ctx.get("mtm", 0.0)
            return v if v > 0 else 0.0
        if metric == "LOSS":
            v = ctx.get("mtm", 0.0)
            return abs(v) if v < 0 else 0.0

    elif scope == "LEG":
        if not leg_id:
            return None
        legs = ctx.get("leg_ltps", {})
        ltp = legs.get(leg_id)
        if ltp is None:
            return None
        if metric == "LTP":
            return ltp
        if metric == "PREMIUM_CHANGE":
            entry_prices = ctx.get("leg_entry_prices", {})
            entry = entry_prices.get(leg_id)
            if entry and entry > 0:
                return ((ltp - entry) / entry) * 100
            return 0.0
        if metric == "PNL":
            entry_prices = ctx.get("leg_entry_prices", {})
            entry = entry_prices.get(leg_id, 0.0)
            qty   = ctx.get("leg_quantities", {}).get(leg_id, 1)
            side  = ctx.get("leg_sides", {}).get(leg_id, "BUY")
            mult  = 1 if side == "BUY" else -1
            return mult * (ltp - entry) * qty

    elif scope == "SPOT":
        if metric == "SPOT_PRICE":
            return ctx.get("spot", 0.0)
        # SPOT_VS_SUPERTREND — not evaluable without indicator data; return None
        return None

    elif scope == "INDICATOR":
        indicators = ctx.get("indicators", {})
        if metric == "RSI":
            return indicators.get("rsi")
        if metric == "SUPERTREND":
            return indicators.get("supertrend")
        if metric == "EMA_CROSS":
            return indicators.get("ema_cross")

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
