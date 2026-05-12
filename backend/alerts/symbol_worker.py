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
    macd_histogram, atr, bollinger, stochastic, adx, vwap as calc_vwap,
)
from services.indicators.rolling_window import RollingWindow
from services.indicators.indicator_cache import indicator_cache
from alerts.dependency_graph import dep_graph, StrategyRef

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

IST           = ZoneInfo("Asia/Kolkata")
MARKET_OPEN   = time(9, 15)
MARKET_CLOSE  = time(15, 30)

# Minute-based timeframes: name → period in minutes
_MINUTE_TFS: dict[str, int] = {"1m": 1, "3m": 3, "5m": 5, "15m": 15, "75m": 75}
ALL_TIMEFRAMES = ("1m", "3m", "5m", "15m", "75m", "1d")

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


def _candle_bounds(ts_ist: datetime, tf: str) -> tuple[datetime, datetime]:
    if tf == "1d":
        start = ts_ist.replace(hour=9, minute=15, second=0, microsecond=0)
        return start, start + timedelta(minutes=375)
    minutes     = _MINUTE_TFS[tf]
    market_open = ts_ist.replace(hour=9, minute=15, second=0, microsecond=0)
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
        start, end = _candle_bounds(ts_ist, tf)
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

    async def _on_candle_close(self, candle: _LiveCandle) -> None:
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
        if not win.rsi_seeded and n >= 15:
            result = rsi_seed(closes, 14)
            if result:
                win.rsi_avg_gain, win.rsi_avg_loss = result
                win.rsi_seeded = True

        if win.rsi_seeded and n >= 2:
            rsi_val, win.rsi_avg_gain, win.rsi_avg_loss = rsi_step(
                win.rsi_avg_gain, win.rsi_avg_loss,
                closes[-1], closes[-2], period=14
            )
            win.rsi_values.append(rsi_val)
            self._emit(sym, tf, "RSI_14", rsi_val, indicators_updated)

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

        # ── EMA(Close) for common periods ─────────────────────────────────────
        for period, ind_name in ((9, "EMA_9"), (21, "EMA_21"), (50, "EMA_50"),
                                 (200, "EMA_200")):
            if n < period:
                continue
            prev_ema = win.get_ema_close(period)
            if prev_ema is None:
                val = ema_from_series(closes, period)
            else:
                val = ema_step(closes[-1], prev_ema, period)
            if val is not None:
                win.set_ema_close(period, val)
                self._emit(sym, tf, ind_name, val, indicators_updated)

        # ── MACD Histogram — full recalc on close (O(N) but only on candle close) ──
        macd_h = macd_histogram(closes)
        if macd_h is not None:
            self._emit(sym, tf, "MACD_HIST", macd_h, indicators_updated)

        # ── ATR(14) ────────────────────────────────────────────────────────────
        atr_val = atr(win.highs(), win.lows(), closes, 14)
        if atr_val is not None:
            self._emit(sym, tf, "ATR_14", atr_val, indicators_updated)

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

        # ── Candle-scope condition evaluation ─────────────────────────────────
        # Evaluate conditions that depend on this candle's OHLCV
        await self._evaluate_candle_conditions(sym, tf, candle)

        # ── Indicator-scope condition evaluation ──────────────────────────────
        # Evaluate ONLY the conditions that depend on the changed indicators
        await self._evaluate_indicator_conditions(sym, tf, indicators_updated)

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

    async def _evaluate_candle_conditions(self, symbol: str, timeframe: str,
                                          candle: _LiveCandle) -> None:
        refs = dep_graph.get_candle_affected(symbol, timeframe)
        if refs:
            await self._dispatch_refs(refs)

    async def _evaluate_indicator_conditions(self, symbol: str, timeframe: str,
                                              indicators_updated: list[str]) -> None:
        """
        CORE OPTIMIZATION: only evaluate conditions whose indicator actually changed.
        RSI updated → only RSI-dependent alerts run. EMA-only alerts are untouched.
        """
        affected: set[StrategyRef] = set()
        for ind_name in indicators_updated:
            affected.update(dep_graph.get_indicator_affected(symbol, timeframe, ind_name))
        if affected:
            await self._dispatch_refs(frozenset(affected))

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
        from alerts.alert_pipeline import ltp_mirror, strategy_legs_mirror, strategy_underlying_mirror

        legs      = strategy_legs_mirror.get(strategy_id, [])
        underlying = strategy_underlying_mirror.get(strategy_id, "")

        leg_ltps:         dict[str, float] = {}
        leg_entry_prices: dict[str, float] = {}
        leg_quantities:   dict[str, int]   = {}
        leg_sides:        dict[str, str]   = {}
        total_mtm  = 0.0
        deployed   = 0.0

        for leg in legs:
            lid   = leg["leg_id"]
            sym   = leg["symbol"]
            entry = leg["entry_price"]
            qty   = leg["quantity"]
            side  = leg["action"]

            ltp  = ltp_mirror.get(sym, entry)
            mult = 1 if side == "BUY" else -1

            leg_ltps[lid]         = ltp
            leg_entry_prices[lid] = entry
            leg_quantities[lid]   = qty
            leg_sides[lid]        = side
            total_mtm             += mult * (ltp - entry) * qty
            deployed              += entry * qty

        spot    = ltp_mirror.get(underlying, ltp_mirror.get(legs[0]["symbol"] if legs else "", 0.0))
        pnl_pct = (total_mtm / deployed * 100.0) if deployed > 0 else 0.0

        # Aggregate all indicator values for the underlying
        indicators: dict[str, float] = {}
        if underlying:
            for tf in ALL_TIMEFRAMES:
                snap = indicator_cache.snapshot_for_symbol(underlying, tf)
                for k, v in snap.items():
                    indicators[f"{k}_{tf}"] = v   # e.g. "RSI_14_15m"

        # Live (unconfirmed) candle data for the underlying
        candles: dict[str, dict] = {}
        for tf in ALL_TIMEFRAMES:
            lc = self._candles.get((underlying or (legs[0]["symbol"] if legs else ""), tf))
            if lc:
                candles[tf] = {
                    "open":   lc.open,
                    "high":   lc.high,
                    "low":    lc.low,
                    "close":  lc.close,
                    "volume": lc.volume,
                }

        return {
            "strategy_id":      strategy_id,
            "underlying":       underlying,
            "mtm":              total_mtm,
            "pnl_pct":          pnl_pct,
            "spot":             spot,
            "leg_ltps":         leg_ltps,
            "leg_entry_prices": leg_entry_prices,
            "leg_quantities":   leg_quantities,
            "leg_sides":        leg_sides,
            "indicators":       indicators,
            "candles":          candles,
            "vwap":             indicator_cache.get(underlying, "15m", "VWAP") or spot,
        }
