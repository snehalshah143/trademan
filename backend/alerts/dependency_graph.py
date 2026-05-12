"""
DependencyGraph — maps indicator/price change events to the alert conditions
that need re-evaluation.

CORE OPTIMIZATION:
  Instead of evaluating all conditions on every tick, we build an inverted
  index at startup:

    "NIFTY:15m:RSI_14"  → {(strategy_A, alert_1), (strategy_B, alert_2), ...}
    "price:CRUDEOILFUT" → {(strategy_C, alert_3)}

  When RSI(14) updates for NIFTY 15m, we look up "NIFTY:15m:RSI_14" → O(1) →
  get the exact set of strategies to evaluate.  Everyone else is untouched.

Lifecycle:
  - Built at startup from all active alert rules.
  - Rebuilt (full rebuild, cheap) whenever alert CRUD occurs.
  - Read-only during normal operation (no locks needed).
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StrategyRef:
    """Minimal reference to a strategy+alert pair."""
    strategy_id: str
    alert_id:    str


# Metric name → canonical indicator key suffix
# e.g. "RSI" with period 14 → "RSI_14"
# Used when parsing condition trees to build dep keys.
_INDICATOR_METRICS = {
    "RSI", "EMA", "SMA", "MACD_HIST", "BB_UPPER", "BB_LOWER",
    "BB_MID", "ATR", "ADX", "STOCH_K", "STOCH_D", "VWAP",
    "EMA_CROSS", "SUPERTREND",
}


def _indicator_key(symbol: str, timeframe: str, metric: str,
                   params: list | None = None,
                   source: str | None = None) -> str:
    """
    Build canonical cache/dependency key.

    Examples:
      RSI, period=14             → "NIFTY:15m:RSI_14"
      EMA of RSI(14), period=9   → "NIFTY:15m:EMA_RSI_9_14"
      EMA of Close, period=21    → "NIFTY:15m:EMA_21"
      MACD_HIST                  → "NIFTY:15m:MACD_HIST"
    """
    period = int(params[0]) if params else ""

    if metric in ("EMA", "SMA") and source and source.upper() != "CLOSE":
        # Chained indicator: EMA of RSI(14) with period 9
        # source might be "RSI" or "RSI_14" from the frontend
        src_base = source.split("_")[0].upper()
        src_per  = source.split("_")[1] if "_" in source else "14"
        suffix   = f"{metric}_{src_base}_{period}_{src_per}"
    elif period:
        suffix = f"{metric}_{period}"
    else:
        suffix = metric

    return f"{symbol}:{timeframe}:{suffix}"


class DependencyGraph:
    """
    Inverted index: trigger_key → set[StrategyRef]

    trigger_key formats:
      Indicator  → "{symbol}:{timeframe}:{indicator}"    e.g. "NIFTY:15m:RSI_14"
      Price      → "price:{symbol}"                      e.g. "price:CRUDEOILFUT"
      Strategy   → "strategy:{strategy_id}"              (MTM/PNL — price-driven)
    """

    def __init__(self) -> None:
        # trigger_key → strategies that depend on it
        self._deps: dict[str, set[StrategyRef]] = defaultdict(set)
        # strategy_id → set of trigger keys it depends on (for fast remove)
        self._strategy_keys: dict[str, set[str]] = defaultdict(set)
        # symbol → underlying (for routing ticks to correct worker)
        self._symbol_underlying: dict[str, str] = {}
        # strategy_id → underlying symbol
        self._strategy_underlying: dict[str, str] = {}

    # ── Build ─────────────────────────────────────────────────────────────────

    def rebuild(
        self,
        rules:              dict[str, list[dict]],   # strategy_id → [rule_dict]
        strategy_underlyings: dict[str, str],         # strategy_id → underlying sym
        strategy_legs:      dict[str, list[dict]],   # strategy_id → leg list
    ) -> None:
        """
        Full rebuild from scratch.  O(total conditions across all rules).
        Safe to call on every alert CRUD operation.
        """
        self._deps.clear()
        self._strategy_keys.clear()
        self._strategy_underlying.update(strategy_underlyings)

        for strategy_id, rule_list in rules.items():
            underlying = strategy_underlyings.get(strategy_id, "")
            legs       = strategy_legs.get(strategy_id, [])

            # Register each leg symbol as a price dependency
            for leg in legs:
                sym = leg.get("symbol", "")
                if sym:
                    self._register(strategy_id, f"price:{sym}",
                                   rule_list, underlying)
                    self._symbol_underlying[sym] = underlying or sym
            # Map the underlying to itself so route_tick can find its partition
            if underlying:
                self._symbol_underlying[underlying] = underlying

            for rule in rule_list:
                ref = StrategyRef(strategy_id=strategy_id, alert_id=rule["alert_id"])
                self._index_tree(rule.get("condition_tree", {}),
                                 ref, underlying, legs)

        total = sum(len(v) for v in self._deps.values())
        logger.info("[DependencyGraph] rebuilt: %d trigger keys, %d total refs",
                    len(self._deps), total)

    def _register(self, strategy_id: str, key: str,
                  rule_list: list, underlying: str) -> None:
        """Register a price dep key for all rules of this strategy."""
        for rule in rule_list:
            ref = StrategyRef(strategy_id=strategy_id, alert_id=rule["alert_id"])
            self._deps[key].add(ref)
            self._strategy_keys[strategy_id].add(key)

    def _index_tree(self, node: dict, ref: StrategyRef,
                    underlying: str, legs: list[dict]) -> None:
        for cond in node.get("conditions", []):
            self._index_condition(cond, ref, underlying, legs)
        for group in node.get("groups", []):
            self._index_tree(group, ref, underlying, legs)

    def _index_condition(self, cond: dict, ref: StrategyRef,
                         underlying: str, legs: list[dict]) -> None:
        scope  = cond.get("scope",  "")
        metric = cond.get("metric", "")
        tf     = cond.get("timeframe", "15m") or "15m"
        params = cond.get("params", [])
        source = cond.get("lhs_source")

        if scope == "INDICATOR":
            # Indicator conditions depend on the underlying symbol's indicators
            sym = underlying or _leg_symbol(legs)
            if sym:
                key = _indicator_key(sym, tf, metric, params, source)
                self._deps[key].add(ref)
                self._strategy_keys[ref.strategy_id].add(key)

        elif scope == "SPOT":
            sym = underlying or _leg_symbol(legs)
            if sym:
                key = f"price:{sym}"
                self._deps[key].add(ref)
                self._strategy_keys[ref.strategy_id].add(key)

        elif scope == "LEG":
            leg_id = cond.get("leg_id")
            sym    = _leg_symbol_by_id(legs, leg_id) if leg_id else _leg_symbol(legs)
            if sym:
                key = f"price:{sym}"
                self._deps[key].add(ref)
                self._strategy_keys[ref.strategy_id].add(key)
            # For options legs (CE/PE) also register underlying so spot ticks
            # trigger re-evaluation when the option contract has no direct feed.
            # Futures/cash legs have their own ticks — no underlying dep needed.
            if underlying and _leg_is_option(legs, leg_id):
                key = f"price:{underlying}"
                self._deps[key].add(ref)
                self._strategy_keys[ref.strategy_id].add(key)

        elif scope == "STRATEGY":
            # MTM / PNL — depends on price of every leg
            for leg in legs:
                sym = leg.get("symbol", "")
                if sym:
                    key = f"price:{sym}"
                    self._deps[key].add(ref)
                    self._strategy_keys[ref.strategy_id].add(key)
            # For options strategies, also register underlying so spot ticks
            # trigger MTM recalc when option contract ticks aren't flowing.
            # Futures/cash legs have their own direct tick feeds — skip.
            if underlying and any(leg.get("option_type") in ("CE", "PE") for leg in legs):
                key = f"price:{underlying}"
                self._deps[key].add(ref)
                self._strategy_keys[ref.strategy_id].add(key)

        elif scope == "CANDLE":
            sym = underlying or _leg_symbol(legs)
            if sym:
                key = f"candle:{sym}:{tf}"
                self._deps[key].add(ref)
                self._strategy_keys[ref.strategy_id].add(key)

    # ── Query (O(1)) ──────────────────────────────────────────────────────────

    def get_affected(self, trigger_key: str) -> frozenset[StrategyRef]:
        """
        Return all (strategy, alert) pairs affected by a trigger key change.
        O(1) dict lookup.
        """
        return frozenset(self._deps.get(trigger_key, set()))

    def get_price_affected(self, symbol: str) -> frozenset[StrategyRef]:
        return self.get_affected(f"price:{symbol}")

    def get_indicator_affected(self, symbol: str, timeframe: str,
                               indicator: str) -> frozenset[StrategyRef]:
        return self.get_affected(f"{symbol}:{timeframe}:{indicator}")

    def get_candle_affected(self, symbol: str, timeframe: str) -> frozenset[StrategyRef]:
        return self.get_affected(f"candle:{symbol}:{timeframe}")

    def underlying_for_symbol(self, symbol: str) -> str:
        return self._symbol_underlying.get(symbol, symbol)

    def underlying_for_strategy(self, strategy_id: str) -> str:
        return self._strategy_underlying.get(strategy_id, "")

    def all_trigger_keys(self) -> list[str]:
        return list(self._deps.keys())

    def strategy_count(self) -> int:
        return len(self._strategy_keys)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _leg_symbol(legs: list[dict]) -> str:
    """Return first available leg symbol."""
    for leg in legs:
        sym = leg.get("symbol", "")
        if sym:
            return sym
    return ""


def _leg_symbol_by_id(legs: list[dict], leg_id: str) -> str:
    for leg in legs:
        if leg.get("leg_id") == leg_id:
            return leg.get("symbol", "")
    return ""


def _leg_is_option(legs: list[dict], leg_id: str | None) -> bool:
    """Return True if the leg referenced by leg_id is an options leg (CE or PE).
    If leg_id is None, returns True if any leg is an option."""
    if leg_id:
        for leg in legs:
            if leg.get("leg_id") == leg_id:
                return leg.get("option_type") in ("CE", "PE")
        return False
    return any(leg.get("option_type") in ("CE", "PE") for leg in legs)


# Singleton
dep_graph = DependencyGraph()
