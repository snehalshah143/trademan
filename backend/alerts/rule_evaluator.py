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
        timeframe  = condition.get("timeframe") or "15m"
        indicators = ctx.get("indicators", {})

        # Extract period from params (frontend sends list [14] or dict {"period": 14})
        raw_params = condition.get("params")
        period: int | None = None
        if isinstance(raw_params, list) and raw_params:
            try:
                period = int(raw_params[0])
            except (TypeError, ValueError):
                pass
        elif isinstance(raw_params, dict):
            try:
                period = int(raw_params.get("period", raw_params.get("0", 0)) or 0) or None
            except (TypeError, ValueError):
                pass

        # For EMA/SMA of another indicator (e.g. EMA of RSI(14) period 9)
        source = condition.get("lhs_source", "CLOSE")
        if metric in ("EMA", "SMA") and source and source.upper() not in ("CLOSE", ""):
            # Chained: source might be "RSI" or "RSI_14"
            src_parts = source.upper().split("_")
            src_name  = src_parts[0]
            src_per   = src_parts[1] if len(src_parts) > 1 else "14"
            if period:
                key = f"{metric}_{src_name}_{period}_{src_per}_{timeframe}"
            else:
                key = f"{metric}_{src_name}_{src_per}_{timeframe}"
        elif period:
            key = f"{metric}_{period}_{timeframe}"
        else:
            key = f"{metric}_{timeframe}"

        val = indicators.get(key)
        if val is not None:
            return float(val)

        # Fallback: try without period (for metrics that don't have one, e.g. MACD_HIST, VWAP)
        fallback_key = f"{metric}_{timeframe}"
        val = indicators.get(fallback_key)
        if val is not None:
            return float(val)

        # Final fallback: case-insensitive flat key (legacy)
        metric_lower = metric.lower()
        for k, v in indicators.items():
            if k.lower().startswith(metric_lower):
                return float(v)

        return None

    elif scope == "CANDLE":
        # OHLCV candle data — keyed as "candles:{timeframe}" in ctx
        timeframe = condition.get("timeframe") or "15m"
        # ctx["candles"] is expected to be dict[timeframe, dict[field, value]]
        # e.g. {"15m": {"open": 24100, "high": 24250, "low": 24050, "close": 24200, "volume": 123456}}
        candles = ctx.get("candles", {})
        candle  = candles.get(timeframe, {})

        field_map = {
            "OPEN":          "open",
            "HIGH":          "high",
            "LOW":           "low",
            "CLOSE":         "close",
            "VOLUME":        "volume",
            "PREV_CLOSE":    "prev_close",
            "BODY_SIZE":     None,   # computed below
            "UPPER_SHADOW":  None,
            "LOWER_SHADOW":  None,
            "CHG_FROM_OPEN": None,
            "CHG_FROM_PREV": None,
        }

        if metric in ("OPEN", "HIGH", "LOW", "CLOSE", "VOLUME", "PREV_CLOSE"):
            val = candle.get(field_map[metric])
            return float(val) if val is not None else None

        # Derived candle metrics
        o = candle.get("open")
        h = candle.get("high")
        l = candle.get("low")
        c = candle.get("close")
        pc = candle.get("prev_close")

        if metric == "BODY_SIZE" and o is not None and c is not None:
            return abs(float(c) - float(o))
        if metric == "UPPER_SHADOW" and h is not None and o is not None and c is not None:
            return float(h) - max(float(o), float(c))
        if metric == "LOWER_SHADOW" and l is not None and o is not None and c is not None:
            return min(float(o), float(c)) - float(l)
        if metric == "CHG_FROM_OPEN" and o is not None and c is not None and float(o) > 0:
            return ((float(c) - float(o)) / float(o)) * 100
        if metric == "CHG_FROM_PREV" and pc is not None and c is not None and float(pc) > 0:
            return ((float(c) - float(pc)) / float(pc)) * 100

        return None

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
