"""
execution_index_credit_spread_directional_weekly.py
----------------------------------------------------
Directional Index Credit Spread — Entry Script (Weekly).

BULLISH signal → BULL PUT SPREAD  (SELL PUT + BUY PUT at lower strike)
BEARISH signal → BEAR CALL SPREAD (SELL CALL + BUY CALL at higher strike)

Execution modes:
  MONITOR_ONLY        — paper signal; no broker orders
  ENTRY_EXEC_MONITOR  — live entry; monitoring script handles exit
  EXIT_EXEC_MONITOR   — paper entry; monitoring script places live exits
  FULL_EXEC           — live entry; monitoring script places live exits

PAPER vs LIVE is resolved once in main() and injected as an EntryExecutor.
No PAPER/LIVE branching inside any other class or function.

Usage
-----
    python -m strategies.credit_spread.index_credit_spread_directional_weekly.execution_index_credit_spread_directional_weekly
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime
from pathlib import Path
from typing import Optional

_framework_root = Path(__file__).resolve().parent.parent.parent.parent.parent
if str(_framework_root) not in sys.path:
    sys.path.insert(0, str(_framework_root))

import numpy as np

from strategy_framework.framework.candle_builder import CandleBuilder
from strategy_framework.framework.config_loader import Config, ConfigError
from strategy_framework.framework.context_builder import parse_chain_entries
from strategy_framework.framework.unified_strategy_db import (
    save_leg, save_signal_event, save_strategy_run, save_trade,
)
from strategy_framework.framework.execution_gateway import ExecutionError, ExecutionGateway
from strategy_framework.framework.expiry_utils import (
    fetch_lot_size, fetch_strike_interval, select_expiry,
)
from strategy_framework.framework.indicators import (
    ema_from_series, ema_step, rsi_from_series, rsi_seed, rsi_step,
    supertrend_seed, supertrend_step,
)
from strategy_framework.framework.market_utils import is_entry_allowed, is_market_open
from strategy_framework.framework.notifier import LEVEL_ALERT, LEVEL_INFO, LEVEL_URGENT
from strategy_framework.framework.openalgo_client import OpenAlgoClient, OpenAlgoError
from strategy_framework.framework.script_helpers import (
    build_client, build_db, build_gateway, build_notifier,
)
from strategy_framework.framework.signal import (
    Bias, ConfirmationCounter, IndicatorState,
    supertrend_flipped_bullish, supertrend_flipped_bearish,
)
from strategy_framework.framework.sizing import (
    fit_lots_to_margin, fixed_lots, margin_based_lots, quantity_from_lots,
)
from strategy_framework.framework.spread_template import (
    build_spread_legs, select_bear_call_spread, select_bull_put_spread,
    spread_net_premium,
)
from strategy_framework.models.option_leg import LegRole, LegStatus, OptionLeg
from strategy_framework.models.strategy_context import StrategyContext
from strategy_framework.models.strategy_state import StrategyState
from strategy_framework.utils.constants import (
    EXEC_MODE_ENTRY_EXEC_MONITOR, EXEC_MODE_EXIT_EXEC_MONITOR,
    EXEC_MODE_FULL_EXEC, EXEC_MODE_MONITOR_ONLY,
    EXECUTION_MODE_LIVE, EXECUTION_MODE_PAPER,
    INSTRUMENT_OPTIONS,
)
from strategy_framework.utils.helper import format_inr, now_ist
from strategy_framework.utils.logger import get_logger, setup_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Directional Index Credit Spread — Entry Script"
    )
    p.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config" / "execution_settings.yaml"),
    )
    return p.parse_args()


def _build_strategy_id(config: Config) -> str:
    return f"{config.get_str('STRATEGY_ID_PREFIX', 'ICS')}_{now_ist().strftime('%Y%m%d')}"


# ---------------------------------------------------------------------------
# Indicator engine
# ---------------------------------------------------------------------------

class IndicatorEngine:
    """
    Holds all live indicator state for the M15 timeframe.
    seed() — full recalculation from history.
    update() — O(1) incremental update on each new closed candle.
    """

    def __init__(self, config: Config) -> None:
        self.state      = IndicatorState()
        self._rsi_p     = config.get_int("RSI_PERIOD",       14)
        self._rsi_ema_p = config.get_int("RSI_EMA_PERIOD",   21)
        self._es_p      = config.get_int("EMA_SLOW_PERIOD",  21)
        self._st_p      = config.get_int("SUPERTREND_PERIOD", 11)
        self._st_f      = config.get_float("SUPERTREND_FACTOR", 2.5)

    def seed(self, builder: CandleBuilder) -> None:
        closes = builder.closes()
        highs  = builder.highs()
        lows   = builder.lows()
        n      = len(closes)

        if n < self._rsi_p + 1:
            logger.warning("Not enough bars for warm-up (%d/%d).", n, self._rsi_p + 1)
            return

        self.state.close    = float(closes[-1])
        self.state.ema_slow = ema_from_series(closes, self._es_p)

        rsi_state = rsi_seed(closes, self._rsi_p)
        if rsi_state:
            ag, al     = rsi_state
            rsi_series = []
            for i in range(self._rsi_p, n):
                rsi_val, ag, al = rsi_step(ag, al, closes[i], closes[i - 1], self._rsi_p)
                rsi_series.append(rsi_val)

            if rsi_series:
                self.state.rsi            = rsi_series[-1]
                self.state.rsi_avg_gain   = ag
                self.state.rsi_avg_loss   = al
                self.state.rsi_prev_close = float(closes[-1])
            if len(rsi_series) >= self._rsi_ema_p:
                self.state.rsi_ema = ema_from_series(
                    np.array(rsi_series, dtype=float), self._rsi_ema_p
                )

        if n >= self._st_p + 2:
            st = supertrend_seed(highs, lows, closes, self._st_p, self._st_f)
            if st:
                self.state.supertrend_state     = st
                self.state.supertrend           = st["supertrend"]
                self.state.supertrend_direction = st["direction"]

        logger.info(
            "IndicatorEngine seeded: close=%.1f EMA21=%.1f RSI=%.1f RSI_EMA=%.1f ST=%.1f dir=%s",
            self.state.close or 0, self.state.ema_slow or 0,
            self.state.rsi or 0, self.state.rsi_ema or 0,
            self.state.supertrend or 0, self.state.supertrend_direction,
        )

    def update(self, builder: CandleBuilder) -> None:
        bar = builder.last_closed
        if bar is None:
            return

        self.state.snapshot_previous()
        self.state.close    = bar.close
        self.state.ema_slow = ema_step(bar.close, self.state.ema_slow, self._es_p)

        if (self.state.rsi_avg_gain is not None
                and self.state.rsi_avg_loss is not None
                and self.state.rsi_prev_close is not None):
            rsi_val, ag, al = rsi_step(
                self.state.rsi_avg_gain, self.state.rsi_avg_loss,
                bar.close, self.state.rsi_prev_close, self._rsi_p,
            )
            self.state.rsi            = rsi_val
            self.state.rsi_avg_gain   = ag
            self.state.rsi_avg_loss   = al
            self.state.rsi_prev_close = bar.close
            if self.state.rsi_ema is not None:
                self.state.rsi_ema = ema_step(rsi_val, self.state.rsi_ema, self._rsi_ema_p)

        if self.state.supertrend_state is not None:
            st = supertrend_step(
                self.state.supertrend_state,
                bar.high, bar.low, bar.close,
                self._st_p, self._st_f,
            )
            self.state.supertrend           = st["supertrend"]
            self.state.supertrend_direction = st["direction"]

        logger.debug(
            "CANDLE | close=%.1f EMA21=%.1f RSI=%.1f RSI_EMA=%.1f ST=%.1f dir=%s",
            self.state.close or 0, self.state.ema_slow or 0,
            self.state.rsi or 0, self.state.rsi_ema or 0,
            self.state.supertrend or 0, self.state.supertrend_direction,
        )


# ---------------------------------------------------------------------------
# Signal evaluator
# ---------------------------------------------------------------------------

def _evaluate_signal(indicators: IndicatorState) -> Bias:
    """
    BULLISH conditions (all three):
      1. M15 Close > M15 EMA21
      2. SuperTrend(11,2.5) just flipped bullish
      3. RSI-14 > RSI-14 EMA21

    BEARISH conditions (mirror of above).
    Returns BULLISH, BEARISH, or NEUTRAL.
    """
    if not indicators.is_ready:
        return Bias.NEUTRAL

    close = indicators.close
    ema   = indicators.ema_slow
    rsi   = indicators.rsi
    rema  = indicators.rsi_ema
    st    = indicators.supertrend_direction
    pst   = indicators.prev_supertrend_direction

    bull = close > ema and supertrend_flipped_bullish(pst, st) and rsi > rema
    bear = close < ema and supertrend_flipped_bearish(pst, st) and rsi < rema

    if bull:
        return Bias.BULLISH
    if bear:
        return Bias.BEARISH
    return Bias.NEUTRAL


# ---------------------------------------------------------------------------
# Signal watcher  (S — single responsibility: tick → candle → signal)
# ---------------------------------------------------------------------------

class SignalWatcher:
    """
    Processes WebSocket ticks, updates indicators, and fires when a confirmed
    directional signal is detected within the entry window.
    """

    def __init__(self, config: Config, builder: CandleBuilder, ind_eng: IndicatorEngine) -> None:
        self._cfg      = config
        self._builder  = builder
        self._ind_eng  = ind_eng
        confirm_n      = config.get_int("CONFIRMATION_BARS", 2)
        self._bull_ctr = ConfirmationCounter(required=confirm_n)
        self._bear_ctr = ConfirmationCounter(required=confirm_n)
        self._confirm_n       = confirm_n
        self._cooldown_bars   = config.get_int("SIGNAL_COOLDOWN_BARS", 5)
        self._cooldown_left   = 0
        self._done            = threading.Event()
        self._confirmed: list[Bias] = []

    # Called by the WebSocket thread on every tick
    def on_tick(self, tick: dict) -> None:
        ltp = tick.get("data", {}).get("ltp") or tick.get("ltp") or tick.get("price")
        if ltp is None:
            return

        closed_bar = self._builder.tick(float(ltp), datetime.now())
        if closed_bar is None:
            return  # bar still building

        self._ind_eng.update(self._builder)

        if self._done.is_set():
            return

        if self._cooldown_left > 0:
            self._cooldown_left -= 1
            self._bull_ctr.reset()
            self._bear_ctr.reset()
            logger.debug("Signal cooldown: %d bars remaining.", self._cooldown_left)
            return

        if not is_entry_allowed(
            now=now_ist(),
            entry_start_hhmm=self._cfg.get_str("ENTRY_START_TIME", "09:30"),
            entry_end_hhmm=self._cfg.get_str("ENTRY_END_TIME",   "14:30"),
        ):
            return

        bias       = _evaluate_signal(self._ind_eng.state)
        bull_fired = self._bull_ctr.update(bias == Bias.BULLISH)
        bear_fired = self._bear_ctr.update(bias == Bias.BEARISH)

        if bias != Bias.BULLISH:
            self._bull_ctr.reset()
        if bias != Bias.BEARISH:
            self._bear_ctr.reset()

        if bias != Bias.NEUTRAL:
            cnt = self._bull_ctr.count if bias == Bias.BULLISH else self._bear_ctr.count
            if cnt == 1:
                s = self._ind_eng.state
                logger.debug(
                    "SIGNAL FIRST MATCH [%s] (1/%d) RSI=%.2f EMA=%.2f ST_dir=%s",
                    bias.value, self._confirm_n,
                    s.rsi or 0, s.ema_slow or 0, s.supertrend_direction,
                )

        if bull_fired or bear_fired:
            confirmed = Bias.BULLISH if bull_fired else Bias.BEARISH
            s = self._ind_eng.state
            logger.info(
                "SIGNAL CONFIRMED [%s] RSI=%.2f RSI_EMA=%.2f close=%.2f EMA=%.2f ST_dir=%s",
                confirmed.value,
                s.rsi or 0, s.rsi_ema or 0, s.close or 0,
                s.ema_slow or 0, s.supertrend_direction,
            )
            self._confirmed.append(confirmed)
            self._done.set()

    # Called by the main thread to block until signal or window close
    def wait(self) -> Optional[Bias]:
        """Block until signal confirmed or entry window passes. Returns confirmed bias or None."""
        while not self._done.is_set():
            if not is_market_open():
                logger.info("Market closed — exiting.")
                return None
            if not is_entry_allowed(
                now=now_ist(),
                entry_start_hhmm=self._cfg.get_str("ENTRY_START_TIME", "09:30"),
                entry_end_hhmm=self._cfg.get_str("ENTRY_END_TIME",   "14:30"),
            ):
                logger.info("Entry window closed without a confirmed signal.")
                return None
            time.sleep(5)
        return self._confirmed[0] if self._confirmed else None


# ---------------------------------------------------------------------------
# Entry executors  (D — depend on abstraction, not on PAPER/LIVE concretions)
# ---------------------------------------------------------------------------

class EntryExecutor(ABC):
    """
    Fills open legs with entry prices.
    Subclasses decide whether to call the broker (LIVE) or fetch LTPs (PAPER).
    """

    @abstractmethod
    def execute(
        self,
        h_leg: OptionLeg,
        p_leg: OptionLeg,
        strategy_id: str,
        ctx: StrategyContext,
    ) -> None:
        """Stamp entry prices and order IDs on both legs."""

    @property
    @abstractmethod
    def mode_tag(self) -> str:
        """PAPER or LIVE — used in logs, DB notes, and notifications."""


class PaperEntryExecutor(EntryExecutor):
    """Stamps current LTP as entry price. No broker orders sent."""

    def __init__(self, client: OpenAlgoClient, exchange: str) -> None:
        self._client   = client
        self._exchange = exchange

    def execute(self, h_leg: OptionLeg, p_leg: OptionLeg, strategy_id: str, ctx: StrategyContext) -> None:
        entry_ts = now_ist()
        for leg in (h_leg, p_leg):
            ltp = 0.0
            try:
                q   = self._client.get_quotes(leg.symbol, self._exchange)
                ltp = float(q.get("ltp") or 0)
            except Exception as exc:
                logger.debug("LTP fetch skipped for %s: %s", leg.symbol, exc)
            leg.entry_price = ltp if ltp > 0 else None
            leg.entry_time  = entry_ts
            leg.status      = LegStatus.OPEN
        logger.info("PAPER entry stamped at current LTPs.")

    @property
    def mode_tag(self) -> str:
        return EXECUTION_MODE_PAPER


class LiveEntryExecutor(EntryExecutor):
    """Places HEDGE-first entry orders via ExecutionGateway."""

    def __init__(self, gateway: ExecutionGateway) -> None:
        self._gateway = gateway

    def execute(self, h_leg: OptionLeg, p_leg: OptionLeg, strategy_id: str, ctx: StrategyContext) -> None:
        self._gateway.execute_legs_hedge_first([h_leg, p_leg], strategy_id, ctx)

    @property
    def mode_tag(self) -> str:
        return EXECUTION_MODE_LIVE


# ---------------------------------------------------------------------------
# DB persistence  (S — single responsibility: write entry records)
# ---------------------------------------------------------------------------

def _persist_entry(
    sf, db_queue,
    strategy_id: str, strategy_name: str, spread_type: str,
    underlying: str, expiry: str, spot: float,
    p_strike: float, h_strike: float, net_prem: Optional[float],
    lots: int, lot_size: int, exec_mode: str, mode_tag: str,
    h_leg: OptionLeg, p_leg: OptionLeg,
    confirmed_bias: Bias, ind_state: IndicatorState, confirm_n: int,
) -> int:
    """Save run, legs, trades and signal event to DB. Returns run_id."""
    sess   = sf.make()
    run_id = 0
    try:
        run_id = save_strategy_run(
            sess,
            strategy_type="CREDIT_SPREAD",
            strategy_id=strategy_id, strategy_name=strategy_name,
            spread_type=spread_type, underlying=underlying, expiry=expiry,
            entry_spot=spot, primary_strike=p_strike, hedge_strike=h_strike,
            net_premium=net_prem, lots=lots, lot_size=lot_size,
            entry_time=now_ist().replace(tzinfo=None),
            status="OPEN", execution_mode=exec_mode, trade_mode=exec_mode,
        )
        sess.commit()
        logger.info("Run saved to DB: run_id=%d mode=%s", run_id, mode_tag)
    except Exception as exc:
        logger.error("DB: failed to save run: %s", exc)
        sess.rollback()
    finally:
        sess.close()

    trade_status = "paper" if mode_tag == EXECUTION_MODE_PAPER else "complete"
    action_taken = "MONITOR_ONLY_SIGNAL" if mode_tag == EXECUTION_MODE_PAPER else "ENTRY_TRIGGERED"

    for leg in (h_leg, p_leg):
        entry_dt = leg.entry_time.replace(tzinfo=None) if leg.entry_time else None
        db_queue.put(
            save_leg,
            run_id=run_id, role=leg.role.value, symbol=leg.symbol,
            exchange=leg.exchange, strike=leg.strike,
            option_type=leg.option_type.value, action=leg.action.value,
            quantity=leg.quantity, product=leg.product, expiry=leg.expiry,
            entry_price=leg.entry_price, entry_order_id=leg.order_id,
            entry_time=entry_dt, status=leg.status.value,
        )
        db_queue.put(
            save_trade,
            run_id=run_id, event_type="ENTRY", symbol=leg.symbol,
            action=leg.action.value, quantity=leg.quantity,
            order_id=leg.order_id, fill_price=leg.entry_price,
            fill_time=entry_dt, status=trade_status,
        )

    db_queue.put(
        save_signal_event,
        run_id=run_id, ts=now_ist().replace(tzinfo=None),
        bias=confirmed_bias.value, spread_type=spread_type,
        rsi=ind_state.rsi, supertrend_dir=ind_state.supertrend_direction,
        ema_fast=ind_state.ema_fast, ema_slow=ind_state.ema_slow,
        spot=spot, confirmation_n=confirm_n, action_taken=action_taken,
    )

    return run_id


# ---------------------------------------------------------------------------
# Notification  (S — single responsibility: summarise and notify)
# ---------------------------------------------------------------------------

def _notify_entry(
    notifier, h_leg: OptionLeg, p_leg: OptionLeg,
    spread_type: str, underlying: str, expiry: str,
    lots: int, lot_size: int, net_prem: Optional[float],
    mode_tag: str,
) -> None:
    def _trade_line(leg: OptionLeg) -> str:
        ex    = leg.expiry.replace("-", "")
        price = f"{leg.entry_price:.2f}" if leg.entry_price is not None else "N/A"
        ts    = (leg.entry_time.strftime("%H:%M:%S")
                 if leg.entry_time else now_ist().strftime("%H:%M:%S"))
        return (
            f"{leg.action.value}-{int(leg.strike)}-{leg.option_type.value}"
            f"-{ex}-qty={leg.quantity}-@{price} [{mode_tag}] at {ts}"
        )

    h_line   = _trade_line(h_leg)
    p_line   = _trade_line(p_leg)
    quantity = h_leg.quantity

    logger.info("=" * 60)
    logger.info("ENTRY COMPLETE [%s]", mode_tag)
    logger.info("  %s", h_line)
    logger.info("  %s", p_line)
    logger.info(
        "Net Premium: %s  qty=%d (%d lots × %d)",
        format_inr(net_prem or 0), quantity, lots, lot_size,
    )
    logger.info("=" * 60)

    notifier.send(
        f"ENTRY COMPLETE [{mode_tag}]: {spread_type} | {underlying} {expiry}\n"
        f"  {h_line}\n  {p_line}\n"
        f"Net premium: {format_inr(net_prem or 0)}  qty={quantity}",
        level=LEVEL_ALERT,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()

    # ── 1. Bootstrap ──────────────────────────────────────────────────────────
    try:
        config = Config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(1)

    _log_file  = config.get_str("LOG_FILE", "logs/cs_execution.log")
    _log_level = config.get_str("LOG_LEVEL", "INFO")
    setup_logger("strategy_framework", log_file=_log_file, level=_log_level)
    setup_logger("strategies",         log_file=_log_file, level=_log_level)
    exec_mode = config.get_str("EXECUTION_MODE", EXEC_MODE_MONITOR_ONLY)
    logger.info("=" * 60)
    logger.info("Directional Index Credit Spread — EXECUTION SCRIPT")
    logger.info("Mode: %s  Underlying: %s",
                exec_mode, config.get_str("UNDERLYING", "NIFTY"))
    logger.info("=" * 60)

    notifier     = build_notifier(config)
    sf, db_queue = build_db(config)
    client       = build_client(config)

    underlying = config.get_str("UNDERLYING",      "NIFTY")
    exch_idx   = config.get_str("EXCHANGE_INDEX",  "NSE_INDEX")
    exch_opt   = config.get_str("EXCHANGE_OPTIONS", "NFO")

    # ── 2. Instrument metadata ────────────────────────────────────────────────
    expiries = client.get_expiry_dates(underlying, exch_opt, INSTRUMENT_OPTIONS)
    expiry   = select_expiry(
        expiries,
        mode=config.get_str("EXPIRY_MODE", "WEEKLY"),
        dte_min=config.get_int("DTE_MIN", 1),
        dte_max=config.get_int("DTE_MAX", 14),
        rollover_hhmm=config.get_str("EXPIRY_ROLLOVER_TIME", "11:00"),
    )
    if not expiry:
        logger.critical("No valid expiry found. Exiting.")
        sys.exit(1)
    logger.info("Selected expiry: %s", expiry)

    lot_size = (config.get_int("LOT_SIZE", 0)
                or fetch_lot_size(client, underlying, exch_idx, expiry, exch_opt))
    interval = (config.get_int("STRIKE_INTERVAL", 50)
                or fetch_strike_interval(client, underlying, exch_idx, expiry))
    logger.info("lot_size=%d  strike_interval=%.0f", lot_size, interval)

    strategy_id = _build_strategy_id(config)

    # ── 3. Idempotency guard (LIVE entry modes only) ───────────────────────────
    # PAPER entry modes never place real orders — no double-entry risk.
    if exec_mode in (EXEC_MODE_ENTRY_EXEC_MONITOR, EXEC_MODE_FULL_EXEC):
        for pos in client.get_positions():
            if underlying in pos.get("symbol", "") and pos.get("quantity", 0) != 0:
                msg = f"SKIPPED: existing {underlying} position detected — not re-entering."
                logger.warning("Existing position found: %s", pos.get("symbol"))
                notifier.send(msg, level=LEVEL_ALERT)
                db_queue.stop(); client.close(); sys.exit(0)

    # ── 4. Sizing ─────────────────────────────────────────────────────────────
    if config.get_str("SIZING_MODE", "FIXED") == "MARGIN_BASED":
        lots = margin_based_lots(
            client=client, underlying=underlying, exchange=exch_opt,
            primary_symbol="", hedge_symbol="", lot_size=lot_size,
            product=config.get_str("PRODUCT", "NRML"),
            max_lots=config.get_int("MAX_LOTS", 10),
            capital_utilisation_pct=config.get_float("CAPITAL_UTILISATION_PCT", 100.0),
            deployed_capital=config.get_float("DEPLOYED_CAPITAL", 0.0),
            margin_buffer_pct=config.get_float("MARGIN_BUFFER_PCT", 15.0),
        )
    else:
        lots = fixed_lots(
            config.get_int("FIXED_LOTS", 1), lot_size,
            max_lots=config.get_int("MAX_LOTS", 10),
        )
    logger.info("Sizing: %d lot(s) × %d = %d", lots, lot_size, lots * lot_size)

    # ── 5. Warm-up ────────────────────────────────────────────────────────────
    builder_m15 = CandleBuilder(symbol=underlying, interval_minutes=15)
    ind_eng     = IndicatorEngine(config)
    warmup_bars = config.get_int("WARMUP_BARS", 100)
    logger.info("Loading %d historical 15-min bars …", warmup_bars)
    try:
        hist = client.get_history(
            symbol=underlying, exchange=exch_idx,
            interval="15m", source="api", warmup_bars=warmup_bars,
        )
        if hist:
            builder_m15.seed_from_history(hist[-warmup_bars:])
            ind_eng.seed(builder_m15)
            logger.info("Warm-up complete: %d bars.", builder_m15.closed_count)
        else:
            logger.warning("No history returned — indicators will build from live data.")
    except Exception as exc:
        logger.warning("History fetch failed (%s) — continuing without warm-up.", exc)

    # ── 6. Signal watch ───────────────────────────────────────────────────────
    watcher = SignalWatcher(config, builder_m15, ind_eng)

    logger.info("Subscribing %s to WebSocket …", underlying)
    try:
        from openalgo import api as _OA
        _oa_ws = _OA(
            api_key=config.get_str("OPENALGO_API_KEY"),
            host=config.get_str("OPENALGO_HOST", "http://127.0.0.1:5000"),
        )
        if not _oa_ws.connect():
            raise RuntimeError("WebSocket connect() failed — check OpenAlgo WS on port 8765")
        _oa_ws.subscribe_ltp(
            [{"exchange": exch_idx, "symbol": underlying}],
            on_data_received=watcher.on_tick,
        )
    except Exception as exc:
        logger.critical("WebSocket subscription failed: %s", exc)
        notifier.send(f"WebSocket failed: {exc}", level=LEVEL_URGENT)
        db_queue.stop(); client.close(); sys.exit(1)

    notifier.send(
        f"Execution started: {underlying} expiry={expiry} lots={lots}",
        level=LEVEL_INFO,
    )
    logger.info("Watching for entry signal … (Ctrl+C to abort)")

    try:
        confirmed_bias = watcher.wait()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt — aborting before entry.")
        db_queue.stop(); client.close(); sys.exit(0)

    if confirmed_bias is None:
        # Exit code 2 = no trade placed (market closed / entry window passed / no signal).
        # The orchestrator uses this to skip monitoring gracefully.
        db_queue.stop(); client.close(); sys.exit(2)

    # ── 7. Build spread legs ──────────────────────────────────────────────────
    try:
        chain = client.get_option_chain(underlying, exch_idx, expiry, strike_count=5)
        spot  = float(chain.get("underlying_ltp", 0) or 0)
    except Exception as exc:
        logger.error("Failed to fetch spot: %s", exc)
        spot, chain = 0.0, {}

    otm_dist     = config.get_float("OTM_DISTANCE",  200.0)
    spread_width = config.get_float("SPREAD_WIDTH",  200.0)
    product      = config.get_str("PRODUCT",         "NRML")

    if confirmed_bias == Bias.BULLISH:
        spread_type        = "BULL_PUT_SPREAD"
        p_strike, h_strike = select_bull_put_spread(spot, interval, otm_dist, spread_width)
    else:
        spread_type        = "BEAR_CALL_SPREAD"
        p_strike, h_strike = select_bear_call_spread(spot, interval, otm_dist, spread_width)

    quantity = quantity_from_lots(lots, lot_size)
    p_leg, h_leg = build_spread_legs(
        spread_type=spread_type, underlying=underlying, expiry=expiry,
        exchange=exch_opt, primary_strike=p_strike, hedge_strike=h_strike,
        quantity=quantity, product=product,
    )
    logger.info(
        "Spread: %s  PRIMARY=%s  HEDGE=%s  qty=%d",
        spread_type, p_leg.symbol, h_leg.symbol, quantity,
    )
    notifier.send(
        f"SIGNAL: {confirmed_bias.value} → {spread_type}\n"
        f"PRIMARY: {p_leg.symbol}  HEDGE: {h_leg.symbol}  qty={quantity}",
        level=LEVEL_ALERT,
    )

    # ── 8. Margin preflight ───────────────────────────────────────────────────
    lots, margin_per_lot, _, margin_available = fit_lots_to_margin(
        client=client, primary_symbol=p_leg.symbol, hedge_symbol=h_leg.symbol,
        exchange=exch_opt, lot_size=lot_size, requested_lots=lots,
        product=product,
        deployed_capital=config.get_float("DEPLOYED_CAPITAL", 0.0),
        margin_buffer_pct=config.get_float("MARGIN_BUFFER_PCT", 15.0),
    )
    if lots == 0:
        msg = (
            f"MARGIN INSUFFICIENT: need ₹{margin_per_lot:,.0f}/lot "
            f"but only ₹{margin_available:,.0f} available. Aborting."
        )
        logger.critical(msg)
        notifier.send(msg, level=LEVEL_URGENT)
        db_queue.stop(); client.close(); sys.exit(1)

    quantity = quantity_from_lots(lots, lot_size)
    if quantity != p_leg.quantity:
        logger.info("Margin-adjusted: %d lot(s) × %d = %d", lots, lot_size, quantity)
        p_leg, h_leg = build_spread_legs(
            spread_type=spread_type, underlying=underlying, expiry=expiry,
            exchange=exch_opt, primary_strike=p_strike, hedge_strike=h_strike,
            quantity=quantity, product=product,
        )

    # ── 9. Resolve executor (one PAPER/LIVE decision for the whole script) ────
    gateway, gw_mode = build_gateway(
        config, client,
        live_exec_modes=(EXEC_MODE_ENTRY_EXEC_MONITOR, EXEC_MODE_FULL_EXEC),
    )
    entry_executor: EntryExecutor = (
        PaperEntryExecutor(client, exch_opt)
        if gw_mode == EXECUTION_MODE_PAPER
        else LiveEntryExecutor(gateway)
    )

    ctx = StrategyContext(
        timestamp=now_ist(), spot=spot,
        atm_strike=round(spot / interval) * interval if interval else spot,
        expiry=expiry, option_chain=parse_chain_entries(chain),
        positions=[], trades=[], orders=[],
        available_cash=0, m2m_unrealized=0, m2m_realized=0,
        config={},
        strategy_state=StrategyState(
            strategy_id=strategy_id,
            strategy_name=config.get_str("STRATEGY_NAME", "ICS"),
        ),
    )

    # ── 10. Execute ───────────────────────────────────────────────────────────
    logger.info("Executing entry [%s] …", entry_executor.mode_tag)
    try:
        entry_executor.execute(h_leg, p_leg, strategy_id, ctx)
    except ExecutionError as exc:
        logger.critical("Entry failed: %s", exc)
        notifier.send(f"ENTRY FAILED: {exc}", level=LEVEL_URGENT)
        db_queue.stop(); client.close(); sys.exit(1)

    # ── 11. Persist ───────────────────────────────────────────────────────────
    net_prem = spread_net_premium(p_leg, h_leg)
    _persist_entry(
        sf, db_queue,
        strategy_id=strategy_id,
        strategy_name=config.get_str("STRATEGY_NAME", "ICS"),
        spread_type=spread_type, underlying=underlying, expiry=expiry,
        spot=spot, p_strike=p_strike, h_strike=h_strike,
        net_prem=net_prem, lots=lots, lot_size=lot_size,
        exec_mode=exec_mode, mode_tag=entry_executor.mode_tag,
        h_leg=h_leg, p_leg=p_leg,
        confirmed_bias=confirmed_bias,
        ind_state=ind_eng.state,
        confirm_n=watcher._confirm_n,
    )

    # ── 12. Notify ────────────────────────────────────────────────────────────
    _notify_entry(
        notifier, h_leg, p_leg,
        spread_type=spread_type, underlying=underlying, expiry=expiry,
        lots=lots, lot_size=lot_size, net_prem=net_prem,
        mode_tag=entry_executor.mode_tag,
    )

    # ── 13. Shutdown ──────────────────────────────────────────────────────────
    db_queue.stop()
    client.close()
    logger.info("Execution script completed. Monitoring script will pick up run_id from DB.")


if __name__ == "__main__":
    main()
