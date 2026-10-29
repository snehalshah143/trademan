"""
SymbolWorker — one asyncio coroutine per symbol partition.

PARTITIONING STRATEGY:
  Symbols are partitioned by their underlying name:
    Worker 0  →  NIFTY  (+ all NIFTY options/futures)
    Worker 1  →  BANKNIFTY
    Worker 2  →  FINNIFTY, MIDCPNIFTY, SENSEX
    Worker 3  →  CRUDEOIL, GOLD, SILVER  (MCX)
    Worker N  →  all others (hash-based)

  This means:
    - All NIFTY ticks land in Worker 0 → candles, indicators, conditions
      are processed sequentially — zero contention, zero locks.
    - NIFTY M15 RSI(14) is computed ONCE in Worker 0, written to
      IndicatorCache, read by condition evaluation in the same worker.

EACH WORKER DOES:
  1. Receives TickEvent from its partition queue (asyncio.Queue)
  2. Updates per-symbol CandleState (lightweight, in-memory only)
  3. On candle close: runs incremental indicator calculations (O(1) each)
  4. Writes updated values to IndicatorCache (global shared dict)
  5. Looks up DependencyGraph → which strategy+alert combos are affected
  6. Evaluates only those condition trees (not all conditions)
  7. Fires AlertFiredEvent to alert_queue if condition is met

NO I/O in steps 1-6. Everything is in-memory.
Step 7 (notifications) is handled by a separate NotificationWorker.
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

import numpy as np

from core.event_bus import TickEvent, CandleCloseEvent, IndicatorUpdateEvent, AlertFiredEvent
from services.indicators.calculators import (
    rsi_seed, rsi_step, ema_step, ema_from_series,
    macd_full, macd_histogram, atr, bollinger, stochastic, adx, vwap as calc_vwap,
    supertrend as calc_supertrend,
)
from services.indicators.rolling_window import RollingWindow
from services.indicators.indicator_cache import indicator_cache
from alerts.dependency_graph import dep_graph, StrategyRef

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

IST           = ZoneInfo("Asia/Kolkata")
MARKET_OPEN   = time(9, 0)     # MCX opens at 09:00; NSE at 09:15
MARKET_CLOSE  = time(23, 30)   # MCX evening session closes at 23:30; NSE at 15:30

# Minute-based timeframes: name → period in minutes
_MINUTE_TFS: dict[str, int] = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "75m": 75}
ALL_TIMEFRAMES = ("1m", "3m", "5m", "15m", "75m", "1d")

# ── Dynamic EMA period registry ───────────────────────────────────────────────
# Always compute these EMA periods; extended by update_required_ema_periods()
# when alert rules are loaded / reloaded.
_required_ema_periods: set[int] = {8, 13, 21, 34, 55, 89, 200}


def _collect_ema_periods_from_tree(tree: dict, out: set[int]) -> None:
    """Walk a condition tree and collect all EMA periods referenced (LHS + RHS)."""
    for cond in tree.get("conditions", []):
        if isinstance(cond, dict):
            # LHS indicator
            if cond.get("scope") == "INDICATOR" and cond.get("metric") == "EMA":
                p = cond.get("params")
                if isinstance(p, dict):
                    try:
                        out.add(int(p.get("period", 0)))
                    except (TypeError, ValueError):
                        pass
            # RHS indicator
            if cond.get("rhs_type") == "INDICATOR" and cond.get("rhs_metric") == "EMA":
                p = cond.get("rhs_params")
                if isinstance(p, dict):
                    try:
                        out.add(int(p.get("period", 0)))
                    except (TypeError, ValueError):
                        pass
    for sub in tree.get("groups", []):
        _collect_ema_periods_from_tree(sub, out)


def update_required_ema_periods(rules: dict[str, list[dict]]) -> None:
    """
    Scan all active alert rules and update _required_ema_periods.
    Call this after alert_cache.reload() completes.
    """
    global _required_ema_periods
    periods: set[int] = {8, 13, 21, 34, 55, 89, 200}  # always-on baseline
    for rule_list in rules.values():
        for rule in rule_list:
            tree = rule.get("condition_tree")
            if tree:
                _collect_ema_periods_from_tree(tree, periods)
    periods.discard(0)
    _required_ema_periods = periods
    logger.info("[SymbolWorker] EMA periods registered: %s", sorted(periods))

# ── Candle state (lightweight — only for indicator calculation) ────────────────

@dataclass
class _LiveCandle:
    symbol:    str
    timeframe: str
    ts:        datetime   # candle open (IST)
    end_ts:    datetime   # exclusive boundary
    open:      float
    high:      float
    low:       float
    close:     float
    volume:    int = 0


_MCX_SYMBOL_PREFIXES = (
    "CRUDEOIL", "GOLD", "SILVER", "NATURALGAS", "COPPER",
    "ALUMINUM", "ZINC", "LEAD", "NICKEL", "MENTHAOIL",
)


def _is_mcx_symbol(symbol: str) -> bool:
    su = symbol.upper()
    return any(su.startswith(p) for p in _MCX_SYMBOL_PREFIXES)


def _candle_bounds(ts_ist: datetime, tf: str, symbol: str = "") -> tuple[datetime, datetime]:
    # MCX opens at 09:00; NSE opens at 09:15
    mo_h, mo_m = (9, 0) if _is_mcx_symbol(symbol) else (9, 15)
    if tf == "1d":
        start = ts_ist.replace(hour=mo_h, minute=mo_m, second=0, microsecond=0)
        return start, start + timedelta(minutes=375)
    minutes     = _MINUTE_TFS[tf]
    market_open = ts_ist.replace(hour=mo_h, minute=mo_m, second=0, microsecond=0)
    delta_secs  = max((ts_ist - market_open).total_seconds(), 0.0)
    idx         = int(delta_secs // (minutes * 60))
    start       = market_open + timedelta(minutes=idx * minutes)
    return start, start + timedelta(minutes=minutes)


def _in_market_hours(ts: datetime) -> bool:
    t = ts.astimezone(IST).time()
    return MARKET_OPEN <= t <= MARKET_CLOSE


# ── Symbol partitioner ────────────────────────────────────────────────────────

# Well-known underlying → partition assignment
_FIXED_PARTITIONS: dict[str, int] = {
    "NIFTY":       0,
    "BANKNIFTY":   1,
    "FINNIFTY":    2,
    "MIDCPNIFTY":  2,
    "SENSEX":      2,
    "CRUDEOIL":    3,
    "GOLD":        3,
    "SILVER":      3,
    "COPPER":      3,
}

_N_WORKERS: int = 4


def get_partition(underlying: str) -> int:
    """Deterministic partition for an underlying symbol."""
    fixed = _FIXED_PARTITIONS.get(underlying.upper())
    if fixed is not None:
        return fixed
    return abs(hash(underlying.upper())) % _N_WORKERS


def configure_workers(n: int, fixed: dict[str, int] | None = None) -> None:
    """Call before starting workers to override defaults."""
    global _N_WORKERS
    _N_WORKERS = n
    if fixed:
        _FIXED_PARTITIONS.update(fixed)


# ── SymbolWorker ──────────────────────────────────────────────────────────────

class SymbolWorker:
    """
    Single asyncio coroutine that owns a partition of underlying symbols.

    Instantiate N of these, each with a unique partition_id.
    Call start() to launch the background task.
    Call enqueue() to route a tick from LTPService.
    """

    def __init__(self, partition_id: int, alert_queue: asyncio.Queue) -> None:
        self.partition_id  = partition_id
        self._alert_queue  = alert_queue
        self._tick_queue:  asyncio.Queue[TickEvent] = asyncio.Queue(maxsize=8192)
        self._task:        asyncio.Task | None       = None

        # (symbol, tf) → live candle
        self._candles: dict[tuple[str, str], _LiveCandle] = {}
        # (symbol, tf) → rolling window
        self._windows: dict[tuple[str, str], RollingWindow] = {}
        # symbol → last close (for RSI delta)
        self._last_close: dict[str, float] = {}
        # (symbol, tf) → last two closed candle snapshots [prev, current]
        # Used so CANDLE-scope CROSS conditions compare closed-candle-to-closed-candle,
        # not tick-to-tick (which would always see the same price in prev vs current).
        self._closed_candle_history: dict[tuple[str, str], list[dict]] = {}

    def enqueue(self, event: TickEvent) -> None:
        """Non-blocking.  Called from LTPService tick callback."""
        try:
            self._tick_queue.put_nowait(event)
        except asyncio.QueueFull:
            # Backpressure: drop tick rather than stall ingestion
            logger.debug("[Worker%d] tick queue full, dropping %s",
                         self.partition_id, event.symbol)

    async def start(self) -> None:
        self._task = asyncio.create_task(
            self._run(), name=f"symbol-worker-{self.partition_id}"
        )
        logger.info("[Worker%d] started", self.partition_id)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ── Main loop ──────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        while True:
            tick: TickEvent = await self._tick_queue.get()
            try:
                await self._process_tick(tick)
            except Exception as exc:
                logger.warning("[Worker%d] tick error %s: %s",
                               self.partition_id, tick.symbol, exc)

    async def _process_tick(self, tick: TickEvent) -> None:
        symbol = tick.symbol
        ltp    = tick.ltp
        ts     = tick.ts

        # ── Update live LTP mirror ────────────────────────────────────────────
        from alerts.alert_pipeline import ltp_mirror
        ltp_mirror[symbol] = ltp

        if not _in_market_hours(ts):
            # Outside market hours: still update LTP mirror, skip candles
            await self._evaluate_price_conditions(symbol, ltp)
            return

        ts_ist = ts.astimezone(IST)

        # ── Update candles for all timeframes ─────────────────────────────────
        for tf in ALL_TIMEFRAMES:
            closed = self._update_candle(symbol, tf, ltp, tick.volume, ts_ist)
            if closed is not None:
                await self._on_candle_close(closed)

        # ── Price-scope condition evaluation ──────────────────────────────────
        # (MTM, SPOT_PRICE, LEG LTP) — evaluated on every tick
        await self._evaluate_price_conditions(symbol, ltp)

    # ── Candle management ─────────────────────────────────────────────────────

    def _update_candle(self, symbol: str, tf: str,
                       ltp: float, volume: int,
                       ts_ist: datetime) -> _LiveCandle | None:
        """
        Update candle state.  Returns the closed candle if period rolled over.
        """
        key = (symbol, tf)
        start, end = _candle_bounds(ts_ist, tf, symbol)
        existing   = self._candles.get(key)

        if existing is None:
            self._candles[key] = _LiveCandle(
                symbol=symbol, timeframe=tf,
                ts=start, end_ts=end,
                open=ltp, high=ltp, low=ltp, close=ltp, volume=volume,
            )
            return None

        if ts_ist >= existing.end_ts:
            closed = existing
            self._candles[key] = _LiveCandle(
                symbol=symbol, timeframe=tf,
                ts=start, end_ts=end,
                open=ltp, high=ltp, low=ltp, close=ltp, volume=volume,
            )
            return closed

        existing.high   = max(existing.high, ltp)
        existing.low    = min(existing.low, ltp)
        existing.close  = ltp
        existing.volume += volume
        return None

    # ── Indicator computation on candle close ─────────────────────────────────

    async def seed_candle(self, candle: _LiveCandle) -> None:
        """
        Feed a historical candle into the rolling window and compute indicators
        WITHOUT triggering alert evaluation.  Called during startup warmup.
        """
        await self._on_candle_close(candle, evaluate=False)

    async def _on_candle_close(self, candle: _LiveCandle, evaluate: bool = True) -> None:
        sym = candle.symbol
        tf  = candle.timeframe
        key = (sym, tf)

        win = self._windows.get(key)
        if win is None:
            win = RollingWindow(sym, tf)
            self._windows[key] = win

        # Append closed candle to rolling window
        win.append(candle.close, candle.high, candle.low, candle.volume)
        closes = win.closes()
        n      = len(closes)

        indicators_updated: list[str] = []

        # ── RSI(14) — incremental Wilder's O(1) after seed ───────────────────
        # On the seeding candle we emit RSI directly from the seed average
        # (no rsi_step call) to avoid double-counting the last delta.
        rsi14_just_seeded = False
        if not win.rsi_seeded and n >= 15:
            result = rsi_seed(closes, 14)
            if result:
                win.rsi_avg_gain, win.rsi_avg_loss = result
                win.rsi_seeded = True
                rsi14_just_seeded = True
                _rs  = win.rsi_avg_gain / win.rsi_avg_loss if win.rsi_avg_loss > 1e-10 else 1e10
                rsi_val = 100.0 - (100.0 / (1.0 + _rs))
                win.rsi_values.append(rsi_val)
                self._emit(sym, tf, "RSI_14", rsi_val, indicators_updated)

        if win.rsi_seeded and not rsi14_just_seeded and n >= 2:
            rsi_val, win.rsi_avg_gain, win.rsi_avg_loss = rsi_step(
                win.rsi_avg_gain, win.rsi_avg_loss,
                closes[-1], closes[-2], period=14
            )
            win.rsi_values.append(rsi_val)
            self._emit(sym, tf, "RSI_14", rsi_val, indicators_updated)

        if win.rsi_seeded:
            # ── EMA(RSI(14), 9) and EMA(RSI(14), 21) ─────────────────────────
            rsi_arr = np.array(win.rsi_values, dtype=np.float64)
            for period, key_name in ((9, "EMA_RSI_9_14"), (21, "EMA_RSI_21_14")):
                prev_ema = win.get_ema_rsi(period)
                if prev_ema is None and len(rsi_arr) >= period:
                    val = ema_from_series(rsi_arr, period)
                else:
                    val = ema_step(rsi_arr[-1], prev_ema, period) if len(rsi_arr) >= 1 else None
                if val is not None:
                    win.set_ema_rsi(period, val)
                    self._emit(sym, tf, key_name, val, indicators_updated)

        # ── RSI(3) — incremental Wilder's, seeds after 3 candles ─────────────
        rsi3_just_seeded = False
        if not win.rsi3_seeded and n >= 4:
            result = rsi_seed(closes, 3)
            if result:
                win.rsi3_avg_gain, win.rsi3_avg_loss = result
                win.rsi3_seeded = True
                rsi3_just_seeded = True
                _rs   = win.rsi3_avg_gain / win.rsi3_avg_loss if win.rsi3_avg_loss > 1e-10 else 1e10
                rsi3_val = 100.0 - (100.0 / (1.0 + _rs))
                win.rsi3_values.append(rsi3_val)
                self._emit(sym, tf, "RSI_3", rsi3_val, indicators_updated)

        if win.rsi3_seeded and not rsi3_just_seeded and n >= 2:
            rsi3_val, win.rsi3_avg_gain, win.rsi3_avg_loss = rsi_step(
                win.rsi3_avg_gain, win.rsi3_avg_loss,
                closes[-1], closes[-2], period=3
            )
            win.rsi3_values.append(rsi3_val)
            self._emit(sym, tf, "RSI_3", rsi3_val, indicators_updated)

        if win.rsi3_seeded:
            # ── EMA(RSI(3), 9) and EMA(RSI(3), 21) ──────────────────────────
            rsi3_arr = np.array(win.rsi3_values, dtype=np.float64)
            for period, key_name in ((9, "EMA_RSI_9_3"), (21, "EMA_RSI_21_3")):
                prev_ema = win.get_ema_rsi3(period)
                if prev_ema is None and len(rsi3_arr) >= period:
                    val = ema_from_series(rsi3_arr, period)
                else:
                    val = ema_step(rsi3_arr[-1], prev_ema, period) if len(rsi3_arr) >= 1 else None
                if val is not None:
                    win.set_ema_rsi3(period, val)
                    self._emit(sym, tf, key_name, val, indicators_updated)

        # ── EMA(Close) for all required periods (dynamic, not hardcoded) ─────
        for period in _required_ema_periods:
            if period <= 0 or n < period:
                logger.debug("[Worker%d] %s %s EMA_%d not ready: have %d/%d candles",
                             self.partition_id, sym, tf, period, n, period)
                continue
            prev_ema = win.get_ema_close(period)
            if prev_ema is None:
                val = ema_from_series(closes, period)
            else:
                val = ema_step(closes[-1], prev_ema, period)
            if val is not None:
                win.set_ema_close(period, val)
                self._emit(sym, tf, f"EMA_{period}", val, indicators_updated)

        # ── MACD variants — full recalc on close ─────────────────────────────
        # Emits MACD_LINE, MACD_SIGNAL, MACD_HIST for each param set.
        # Key format: {TYPE}_{fast}_{slow}_{signal}
        # Legacy short keys (12-26-9) emitted for backward compat.
        for _fast, _slow, _signal in (
            (12, 26, 9),    # standard / default
            (10, 22, 7),    # user required
            (5,  11, 3),    # user required
            (21, 34, 8),    # user required (34-21-8)
        ):
            _mc = macd_full(closes, fast=_fast, slow=_slow, signal=_signal)
            if _mc is not None:
                _ml, _ms, _mh = _mc
                _sfx = f"{_fast}_{_slow}_{_signal}"
                self._emit(sym, tf, f"MACD_LINE_{_sfx}",   _ml, indicators_updated)
                self._emit(sym, tf, f"MACD_SIGNAL_{_sfx}", _ms, indicators_updated)
                self._emit(sym, tf, f"MACD_HIST_{_sfx}",   _mh, indicators_updated)
                if _fast == 12 and _slow == 26 and _signal == 9:
                    self._emit(sym, tf, "MACD_LINE",   _ml, indicators_updated)
                    self._emit(sym, tf, "MACD_SIGNAL", _ms, indicators_updated)
                    self._emit(sym, tf, "MACD_HIST",   _mh, indicators_updated)  # legacy

        # ── ATR(14) ────────────────────────────────────────────────────────────
        atr_val = atr(win.highs(), win.lows(), closes, 14)
        if atr_val is not None:
            self._emit(sym, tf, "ATR_14", atr_val, indicators_updated)

        # ── Supertrend variants — full recalc on close ────────────────────────
        # Key format: SUPERTREND_{period}_{factor*10}  (e.g. SUPERTREND_10_30)
        # (10, 3.0) is the evaluator default when no params are specified in rule.
        for _st_period, _st_factor in ((10, 3.0), (11, 2.0), (11, 2.5)):
            _st_val = calc_supertrend(win.highs(), win.lows(), closes,
                                      period=_st_period, factor=_st_factor)
            if _st_val is not None:
                _fk = round(_st_factor * 10)
                self._emit(sym, tf, f"SUPERTREND_{_st_period}_{_fk}", _st_val, indicators_updated)

        # ── Bollinger Bands(20, 2) ─────────────────────────────────────────────
        bb = bollinger(closes, 20, 2.0)
        if bb is not None:
            self._emit(sym, tf, "BB_UPPER_20", bb[0], indicators_updated)
            self._emit(sym, tf, "BB_MID_20",   bb[1], indicators_updated)
            self._emit(sym, tf, "BB_LOWER_20", bb[2], indicators_updated)

        # ── Stochastic(14, 3) ──────────────────────────────────────────────────
        stoch = stochastic(win.highs(), win.lows(), closes, 14, 3)
        if stoch is not None:
            self._emit(sym, tf, "STOCH_K_14", stoch[0], indicators_updated)
            self._emit(sym, tf, "STOCH_D_14", stoch[1], indicators_updated)

        # ── ADX(14) ────────────────────────────────────────────────────────────
        adx_val = adx(win.highs(), win.lows(), closes, 14)
        if adx_val is not None:
            self._emit(sym, tf, "ADX_14", adx_val, indicators_updated)

        # ── Session VWAP ───────────────────────────────────────────────────────
        vwap_val = win.session_vwap
        if vwap_val is not None:
            self._emit(sym, tf, "VWAP", vwap_val, indicators_updated)

        # ── Record closed candle (for CROSS detection on CANDLE scope) ──────
        # Keep the last 2 closed candles per (sym, tf) so that prev_ctx
        # sees the PREVIOUS closed candle and current_ctx sees THIS one.
        # Without this, prev_ctx would have the last tick's live close (≈same
        # price as current close), making CROSS_ABOVE/CROSS_BELOW never fire.
        _hist = self._closed_candle_history.setdefault((sym, tf), [])
        _hist.append({
            "open": candle.open, "high": candle.high,
            "low":  candle.low,  "close": candle.close,
            "volume": candle.volume,
        })
        if len(_hist) > 2:
            _hist.pop(0)

        if not evaluate:
            return   # warmup mode: only seed indicators, skip alert evaluation

        # ── Single merged dispatch per candle close ────────────────────────────
        # CRITICAL: candle-scope and indicator-scope refs MUST be dispatched in
        # ONE call so that update_context() is called only ONCE per strategy per
        # candle close.  If we call _dispatch_refs twice, the second call sees
        # prev_ctx = NEW indicator values (set by the first call), breaking
        # CROSS_ABOVE/CROSS_BELOW detection for indicator conditions.
        #
        # Dep graph is indexed by underlying (e.g. "CRUDEOIL"), not by the
        # raw tick symbol (e.g. "CRUDEOIL18JUN26FUT"), so resolve before lookup.
        query_sym = dep_graph.underlying_for_symbol(sym) or sym
        all_affected: set[StrategyRef] = set()

        # Candle-scope deps
        candle_refs = dep_graph.get_candle_affected(query_sym, tf)
        all_affected.update(candle_refs)

        # Indicator-scope deps (only for indicators that actually changed)
        for ind_name in indicators_updated:
            ind_refs = dep_graph.get_indicator_affected(query_sym, tf, ind_name)
            all_affected.update(ind_refs)

        if all_affected:
            logger.info(
                "[Worker%d] candle close %s %s close=%.2f → %d candle refs, "
                "%d indicator refs, %d unique strategies",
                self.partition_id, query_sym, tf, candle.close,
                len(candle_refs), len(all_affected) - len(candle_refs),
                len({r.strategy_id for r in all_affected}),
            )
            await self._dispatch_refs(frozenset(all_affected))

        logger.debug("[Worker%d] %s %s close=%.2f → %d indicators updated",
                     self.partition_id, sym, tf, candle.close,
                     len(indicators_updated))

    def _emit(self, sym: str, tf: str, indicator: str,
              value: float, updated_list: list[str]) -> None:
        """Write to IndicatorCache and record which indicators changed."""
        result = indicator_cache.update(sym, tf, indicator, value)
        if result is not None:
            updated_list.append(indicator)

    # ── Condition evaluation ───────────────────────────────────────────────────

    async def _evaluate_price_conditions(self, symbol: str, ltp: float) -> None:
        """Evaluate conditions that depend on this symbol's price. Called every tick."""
        refs = dep_graph.get_price_affected(symbol)
        if refs:
            await self._dispatch_refs(refs)

    async def _dispatch_refs(self, refs: frozenset[StrategyRef]) -> None:
        """
        Group refs by strategy_id so context is built ONCE per strategy per
        tick batch — not once per alert. This prevents prev_ctx corruption
        when a strategy has multiple alerts that all trigger on the same tick.
        """
        from collections import defaultdict
        by_strategy: dict[str, set[str]] = defaultdict(set)
        for ref in refs:
            by_strategy[ref.strategy_id].add(ref.alert_id)

        for strategy_id, alert_ids in by_strategy.items():
            await self._evaluate_strategy_alerts(strategy_id, alert_ids)

    async def _evaluate_strategy_alerts(self, strategy_id: str,
                                         alert_ids: set[str]) -> None:
        """
        Evaluate a specific set of alerts for one strategy.
        Context is built once and shared across all alerts in this call.
        """
        from alerts.alert_cache import alert_cache
        from alerts.rule_evaluator import evaluate_group

        rules = alert_cache.get_rules_for_strategy(strategy_id)
        if not rules:
            return

        # Build context once per strategy per evaluation batch — zero I/O
        ctx = self._build_context(strategy_id)
        alert_cache.update_context(strategy_id, ctx)
        current_ctx, prev_ctx = alert_cache.get_contexts(strategy_id)

        logger.debug(
            "[Worker%d] strategy=%s leg_ltps=%s prev_leg_ltps=%s",
            self.partition_id, strategy_id[:8],
            dict(current_ctx.get("leg_ltps", {})),
            dict(prev_ctx.get("leg_ltps", {})),
        )

        for rule in rules:
            alert_id = rule["alert_id"]
            if alert_id not in alert_ids:
                continue   # not triggered by the current dependency change

            if alert_cache.is_on_cooldown(alert_id, rule.get("cooldown_secs", 0)):
                continue

            try:
                fired = evaluate_group(rule["condition_tree"], current_ctx, prev_ctx)
            except Exception as exc:
                logger.warning("[Worker%d] evaluate error %s: %s",
                               self.partition_id, alert_id, exc)
                continue

            logger.debug(
                "[Worker%d] alert=%s fired=%s op=%s",
                self.partition_id, alert_id[:8], fired,
                rule["condition_tree"].get("conditions", [{}])[0].get("operator"),
            )

            if not fired:
                continue

            logger.info("[Worker%d] FIRED alert %s (%s)",
                        self.partition_id, alert_id, rule.get("name"))

            alert_cache.set_cooldown(alert_id)
            alert_cache.mark_triggered(alert_id)

            event = AlertFiredEvent(
                alert_id=alert_id,
                strategy_id=strategy_id,
                rule_name=rule.get("name", ""),
                ctx=dict(current_ctx),
                fired_at=datetime.now(tz=ZoneInfo("UTC")),
            )
            try:
                self._alert_queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.warning("[Worker%d] alert queue full, dropping fired event",
                               self.partition_id)

    # ── Context builder (zero I/O) ────────────────────────────────────────────

    def _build_context(self, strategy_id: str) -> dict:
        """
        Build EvaluationContext entirely from in-memory state.
        Zero Redis reads.  Zero DB queries.  Pure dict lookups.
        """
        from alerts.alert_pipeline import ltp_mirror, strategy_legs_mirror, strategy_underlying_mirror, strategy_name_mirror

        legs           = strategy_legs_mirror.get(strategy_id, [])
        underlying     = strategy_underlying_mirror.get(strategy_id, "")
        strategy_name  = strategy_name_mirror.get(strategy_id, "")

        leg_ltps:         dict[str, float] = {}
        leg_entry_prices: dict[str, float] = {}
        leg_quantities:   dict[str, int]   = {}
        leg_sides:        dict[str, str]   = {}
        leg_deltas:       dict[str, float] = {}
        leg_gammas:       dict[str, float] = {}
        leg_thetas:       dict[str, float] = {}
        leg_vegas:        dict[str, float] = {}
        total_mtm  = 0.0
        deployed   = 0.0
        _raw_net_delta  = 0.0
        _raw_net_gamma  = 0.0
        _raw_net_theta  = 0.0
        _raw_net_vega   = 0.0
        _option_legs_total   = 0
        _option_legs_loaded  = 0

        from services.greeks_service import greeks_cache, is_option

        for leg in legs:
            lid   = leg["leg_id"]
            sym   = leg["symbol"]
            entry = leg["entry_price"]
            qty   = leg["quantity"]
            side  = leg["action"]
            opt   = leg.get("option_type", "")

            ltp  = ltp_mirror.get(sym, entry)
            mult = 1 if side == "BUY" else -1

            leg_ltps[lid]         = ltp
            leg_entry_prices[lid] = entry
            leg_quantities[lid]   = qty
            leg_sides[lid]        = side
            total_mtm             += mult * (ltp - entry) * qty
            deployed              += entry * qty

            # Greeks — only for option legs
            if is_option(sym, opt):
                _option_legs_total += 1
                g = greeks_cache.get(sym)
                if g:
                    _option_legs_loaded += 1
                    leg_deltas[lid] = g["delta"]
                    leg_gammas[lid] = g["gamma"]
                    leg_thetas[lid] = g["theta"]
                    leg_vegas[lid]  = g["vega"]
                    _raw_net_delta  += mult * g["delta"]
                    _raw_net_gamma  += mult * g["gamma"]
                    _raw_net_theta  += mult * g["theta"]
                    _raw_net_vega   += mult * g["vega"]

        # Net greeks are only valid when EVERY option leg has data.
        # If any leg is missing (startup, OA error, new symbol) return None
        # so delta/greek conditions evaluate to False rather than fire spuriously.
        _all_greeks_ready = (_option_legs_total == 0 or
                             _option_legs_loaded == _option_legs_total)
        net_delta = _raw_net_delta if _all_greeks_ready else None
        net_gamma = _raw_net_gamma if _all_greeks_ready else None
        net_theta = _raw_net_theta if _all_greeks_ready else None
        net_vega  = _raw_net_vega  if _all_greeks_ready else None

        # Spot = underlying index LTP (e.g. NIFTY cash).
        # Never fall back to a leg LTP — option premiums (~50) would show as Spot:54.
        spot    = ltp_mirror.get(underlying, 0.0) if underlying else 0.0
        pnl_pct = (total_mtm / deployed * 100.0) if deployed > 0 else 0.0

        # Aggregate all indicator values for the underlying.
        # For options strategies: underlying="NIFTY", leg="NIFTY25MAY24000CE"
        #   → indicators built under "NIFTY" (direct index tick feed) ✓
        # For futures strategies: underlying="CRUDEOIL", leg="CRUDEOIL18JUN26FUT"
        #   → indicators built under "CRUDEOIL18JUN26FUT" (futures tick feed)
        #   → underlying lookup returns empty; fall back to leg symbol ✓
        sym_for_candle = underlying or (legs[0]["symbol"] if legs else "")
        leg_sym = legs[0]["symbol"] if legs else ""
        # Build ordered list of candidate symbols (underlying first so it wins
        # on key collision — e.g. NIFTY beats option CE/PE candles).
        _lookup_syms: list[str] = []
        _seen_syms: set[str] = set()
        for _s in [sym_for_candle, leg_sym]:
            if _s and _s not in _seen_syms:
                _lookup_syms.append(_s)
                _seen_syms.add(_s)

        indicators: dict[str, float] = {}
        for _ls in _lookup_syms:
            for tf in ALL_TIMEFRAMES:
                snap = indicator_cache.snapshot_for_symbol(_ls, tf)
                for k, v in snap.items():
                    key = f"{k}_{tf}"
                    if key not in indicators:   # first match wins
                        indicators[key] = v

        # Live (unconfirmed) candle data — try each candidate symbol
        candles: dict[str, dict] = {}
        for _ls in _lookup_syms:
            for tf in ALL_TIMEFRAMES:
                if tf in candles:
                    continue
                lc = self._candles.get((_ls, tf))
                if lc:
                    candles[tf] = {
                        "open":   lc.open,
                        "high":   lc.high,
                        "low":    lc.low,
                        "close":  lc.close,
                        "volume": lc.volume,
                    }

        # Most-recently CLOSED candle per timeframe — used by CANDLE-scope
        # CROSS conditions so consecutive closed-candle closes are compared,
        # not consecutive ticks (which are nearly identical in price).
        closed_candles: dict[str, dict] = {}
        for _ls in _lookup_syms:
            for tf in ALL_TIMEFRAMES:
                if tf in closed_candles:
                    continue
                hist = self._closed_candle_history.get((_ls, tf), [])
                if hist:
                    closed_candles[tf] = hist[-1]

        return {
            "strategy_id":      strategy_id,
            "strategy_name":    strategy_name,
            "underlying":       underlying,
            "mtm":              total_mtm,
            "pnl_pct":          pnl_pct,
            "spot":             spot,
            "leg_ltps":         leg_ltps,
            "leg_entry_prices": leg_entry_prices,
            "leg_quantities":   leg_quantities,
            "leg_sides":        leg_sides,
            "leg_deltas":       leg_deltas if _all_greeks_ready else {},
            "leg_gammas":       leg_gammas if _all_greeks_ready else {},
            "leg_thetas":       leg_thetas if _all_greeks_ready else {},
            "leg_vegas":        leg_vegas  if _all_greeks_ready else {},
            "net_delta":        net_delta,
            "net_gamma":        net_gamma,
            "net_theta":        net_theta,
            "net_vega":         net_vega,
            "indicators":       indicators,
            "candles":          candles,
            "closed_candles":   closed_candles,
            "vwap":             indicator_cache.get(underlying, "15m", "VWAP") or spot,
        }
