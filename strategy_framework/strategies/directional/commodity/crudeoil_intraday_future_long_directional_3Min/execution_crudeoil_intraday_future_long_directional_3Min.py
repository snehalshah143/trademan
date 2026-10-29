"""
execution_crudeoil_intraday_future_long_directional_3Min.py
-----------------------------------------------------------
Crudeoil Intraday Futures — Directional Entry Script (3-Min signal).

Timeframe architecture
-----------------------
  M1  CandleBuilder (1-min)  : RSI-14, RSI-EMA8, RSI-EMA21, MACD-A(8,21,5), MACD-B(21,34,8), Supertrend(11,2.5)
  M3  CandleBuilder (3-min)  : RSI-14, RSI-EMA8, RSI-EMA21, MACD(8,21,5), Supertrend(11,2.5)
  M15 CandleBuilder (15-min) : RSI-14, RSI-EMA8, MACD-A(8,21,5), MACD-B(5,11,3), Supertrend(11,2.5)

Every WebSocket tick is fed into all three builders simultaneously.
A 1-min candle close triggers an M1 indicator update and entry evaluation.
A 3-min candle close also triggers an M3 indicator update.
A 15-min candle close triggers an M15 indicator update (base condition).

Entry conditions — LONG (evaluated on every M1 candle close):
  1. M3-Supertrend-Direction == -1  (M3 trend bullish)
  2. M3-MACD > M3-Signal  OR  M3-RSI-EMA8 > M3-RSI-EMA21
  3. M3-RSI14 > M3-RSI14-EMA21
  4. M1-RSI14 > M1-RSI14-EMA21  OR  M1-Supertrend-Direction == -1

Entry conditions — SHORT (evaluated on every M1 candle close):
  1. M3-Supertrend-Direction == +1  (M3 trend bearish)
  2. M3-MACD < M3-Signal  OR  M3-RSI-EMA8 < M3-RSI-EMA21
  3. M3-RSI14 < M3-RSI14-EMA21
  4. M1-RSI14 < M1-RSI14-EMA21  OR  M1-Supertrend-Direction == +1

TRADE_DIRECTION config: "LONG" | "SHORT" | "BOTH" (checks LONG first, then SHORT)

Execution modes:
  MONITOR_ONLY        — paper signal; no broker orders
  ENTRY_EXEC_MONITOR  — live entry; monitoring script handles exit
  EXIT_EXEC_MONITOR   — paper entry; monitoring script places live exits
  FULL_EXEC           — live entry + live exit

Usage
-----
    python -m strategies.directional.commodity.crudeoil_intraday_future_long_directional_3Min.execution_crudeoil_intraday_future_long_directional_3Min
"""
from __future__ import annotations

import argparse
import copy
import sys
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, time as _time
from pathlib import Path
from typing import Optional

_framework_root = Path(__file__).resolve().parent.parent.parent.parent.parent.parent
if str(_framework_root) not in sys.path:
    sys.path.insert(0, str(_framework_root))

from strategy_framework.framework.candle_builder import CandleBuilder
from strategy_framework.framework.config_loader import Config, ConfigError
from strategy_framework.framework.db_writer import DBWriteQueue
from strategy_framework.framework.unified_strategy_db import (
    SessionFactory, init_db,
    save_leg, save_signal_event, save_strategy_run, save_trade,
)
from strategy_framework.framework.expiry_utils import days_to_expiry, select_expiry
from strategy_framework.framework.market_utils import (
    is_mcx_market_open, is_within_window, MCX_OPEN_TIME, MCX_CLOSE_TIME,
)
from strategy_framework.framework.notifier import (
    LEVEL_ALERT, LEVEL_INFO, LEVEL_URGENT, CompositeNotifier, LogNotifier, TelegramNotifier,
)
from strategy_framework.framework.openalgo_client import OpenAlgoClient
from strategy_framework.models.future_leg import FutureDirection, FutureLeg, FutureStatus
from strategy_framework.utils.constants import (
    EXEC_MODE_ENTRY_EXEC_MONITOR, EXEC_MODE_FULL_EXEC,
    EXEC_MODE_MONITOR_ONLY,
    EXECUTION_MODE_LIVE, EXECUTION_MODE_PAPER,
    INSTRUMENT_FUTURES,
)
from strategy_framework.utils.helper import now_ist
from strategy_framework.utils.logger import get_logger, setup_logger

from ._engine import (
    Coil3MinIndicatorEngine, Coil3MinIndicatorState,
    build_entry_conditions, evaluate_entry,
)

logger = get_logger(__name__)


def _fmt(val, spec: str = ".2f") -> str:
    """Format an optional float; return '—' if None."""
    return f"{val:{spec}}" if val is not None else "—"


def _tick(ok: bool) -> str:
    return "✓" if ok else "✗"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crudeoil Intraday Futures — Entry Script (3-Min)")
    p.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config" / "execution_settings.yaml"),
    )
    return p.parse_args()


def _build_strategy_id(config: Config) -> str:
    return f"{config.get_str('STRATEGY_ID_PREFIX', 'COIL3')}_{now_ist().strftime('%Y%m%d')}"


# ---------------------------------------------------------------------------
# Bootstrap helpers
# ---------------------------------------------------------------------------

def _build_client(config: Config) -> OpenAlgoClient:
    client = OpenAlgoClient(
        api_key=config.get_str("OPENALGO_API_KEY"),
        host=config.get_str("OPENALGO_HOST", "http://127.0.0.1:5000"),
        timeout=config.get_float("API_TIMEOUT", 30.0),
    )
    if not client.ping():
        logger.critical("OpenAlgo not reachable — check server and API key.")
        sys.exit(1)
    logger.info("OpenAlgo connection verified.")
    return client


def _build_notifier(config: Config) -> CompositeNotifier:
    notifiers = [LogNotifier()]
    if config.get_bool("NOTIFY_TELEGRAM", False):
        notifiers.append(TelegramNotifier(
            token=config.get_str("TELEGRAM_TOKEN", ""),
            chat_id=config.get_str("TELEGRAM_CHAT_ID", ""),
            min_level=config.get_str("TELEGRAM_MIN_LEVEL", "ALERT"),
        ))
    return CompositeNotifier(notifiers)


def _build_db(config: Config) -> tuple[SessionFactory, DBWriteQueue]:
    db_url   = config.get_str("DATABASE_URL", "sqlite:///logs/strategies.db")
    engine   = init_db(db_url)
    sf       = SessionFactory(engine)
    db_queue = DBWriteQueue(sf.make, flush_interval=5.0)
    db_queue.start()
    return sf, db_queue


# ---------------------------------------------------------------------------
# Signal watcher  (dual-timeframe WebSocket-driven, 1-min candle loop)
# ---------------------------------------------------------------------------

class SignalWatcher:
    """
    Processes WebSocket ticks through two CandleBuilders:
      - m1_builder (1-min): updates RSI, MACD, EMA on every closed 1-min candle
      - m3_builder (3-min): updates Supertrend whenever a 3-min candle closes

    Fires on the first closed 1-min candle where evaluate_entry() returns True.
    Supports TRADE_DIRECTION = "LONG" | "SHORT" | "BOTH".
    When "BOTH": checks LONG first, then SHORT.
    """

    def __init__(
        self,
        config: Config,
        m1_builder:  CandleBuilder,
        m3_builder:  CandleBuilder,
        m15_builder: CandleBuilder,
        ind_eng: Coil3MinIndicatorEngine,
        direction: str = "LONG",
    ) -> None:
        self._cfg         = config
        self._m1_builder  = m1_builder
        self._m3_builder  = m3_builder
        self._m15_builder = m15_builder
        self._ind_eng     = ind_eng
        self._direction  = direction.upper()
        self._done       = threading.Event()
        self._result: list[tuple[Coil3MinIndicatorState, str]] = []

        # Candidate directions to evaluate (in priority order)
        if self._direction == "BOTH":
            self._candidates = ["LONG", "SHORT"]
        else:
            self._candidates = [self._direction]

    def on_tick(self, tick: dict) -> None:
        ltp = tick.get("data", {}).get("ltp") or tick.get("ltp") or tick.get("price")
        if ltp is None:
            return

        ts  = datetime.now()
        ltp = float(ltp)

        # Feed tick into M15 builder — it may close a 15-min candle
        m15_closed = self._m15_builder.tick(ltp, ts)
        if m15_closed is not None:
            self._ind_eng.update_m15(self._m15_builder)

        # Feed tick into M3 builder — it may close a 3-min candle
        m3_closed = self._m3_builder.tick(ltp, ts)
        if m3_closed is not None:
            self._ind_eng.update_m3(self._m3_builder)

        # Feed tick into M1 builder
        m1_closed = self._m1_builder.tick(ltp, ts)
        if m1_closed is None:
            return   # 1-min candle still building

        # 1-min candle closed — update M1 indicators + live M15 RSI/MACD
        self._ind_eng.update_m1(self._m1_builder)
        self._ind_eng.update_m15_live(m1_closed.close)

        if self._done.is_set():
            return

        now = now_ist()
        self._log_entry_conditions(now)

        if not is_mcx_market_open(now):
            return
        if not is_within_window(
            now,
            self._cfg.get_str("ENTRY_START_TIME", "09:10"),
            self._cfg.get_str("ENTRY_END_TIME",   "23:00"),
        ):
            return

        for candidate in self._candidates:
            if not evaluate_entry(self._ind_eng.state, direction=candidate):
                continue

            s = self._ind_eng.state
            logger.info(
                "ENTRY SIGNAL CONFIRMED [%s]: "
                "M1[close=%.2f RSI=%.1f EMA21=%.1f ST=%.2f dir=%s] | "
                "M3[RSI=%.1f EMA21=%.1f MACD=%.4f Sig=%.4f ST=%.2f dir=%s]",
                candidate,
                s.m1_close or 0,
                s.m1_rsi or 0, s.m1_rsi_ema_slow or 0,
                s.m1_supertrend or 0, s.m1_supertrend_direction,
                s.m3_rsi or 0, s.m3_rsi_ema_slow or 0,
                s.m3_macd_line or 0, s.m3_macd_signal or 0,
                s.m3_supertrend or 0, s.m3_supertrend_direction,
            )
            self._result.append((copy.copy(s), candidate))
            self._done.set()
            return

    def _log_entry_conditions(self, now: datetime) -> None:
        """Log indicator values and entry condition pass/fail on every 1-min candle close."""
        s  = self._ind_eng.state
        ts = now.strftime("%H:%M:%S")

        if not s.is_ready:
            logger.info(
                "CANDLE | %s — indicators NOT READY (waiting for first live M3 candle close)\n"
                "  M1: close=%s  RSI=%s  EMA21=%s  ST=%s  dir=%s\n"
                "  M3: close=%s  RSI=%s  EMA21=%s  MACD=%s  Sig=%s  ST=%s  dir=%s  prevRSI=%s",
                ts,
                _fmt(s.m1_close),          _fmt(s.m1_rsi, ".1f"),
                _fmt(s.m1_rsi_ema_slow, ".1f"), _fmt(s.m1_supertrend),
                s.m1_supertrend_direction,
                _fmt(s.m3_close),          _fmt(s.m3_rsi, ".1f"),
                _fmt(s.m3_rsi_ema_slow, ".1f"), _fmt(s.m3_macd_line, ".4f"),
                _fmt(s.m3_macd_signal, ".4f"),  _fmt(s.m3_supertrend),
                s.m3_supertrend_direction,
                _fmt(s.prev_m3_rsi, ".1f") if s.prev_m3_rsi is not None else "None←waiting",
            )
            return

        for direction in self._candidates:
            ec = build_entry_conditions(s, direction)

            logger.info(
                "CANDLE | %s [%s] M1close=%.2f\n"
                "  BASE(A=%s B=%s C=%s)=%s\n"
                "  C1(M3-ST dir=%s)=%s  C2(M3-RSI)=%s  C3(hist=%.4f)=%s\n"
                "  M15: MACD-A=%s>%s RSI=%s>EMA8=%s MACD-B=%s>%s ST-dir=%s\n"
                "  M3: ST=%.2f dir=%s  RSI=%.1f  EMA8=%.1f  EMA21=%.1f  MACD=%.4f  Sig=%.4f  EMA8p=%.2f EMA21p=%.2f\n"
                "  M1: RSI=%.1f  EMA8=%.1f  EMA21=%.1f  ST=%.2f  dir=%s",
                ts, direction, s.m1_close or 0,
                # base condition groups
                _tick(ec.base_group_a), _tick(ec.base_group_b), _tick(ec.base_group_c),
                _tick(ec.base_condition_met),
                # C1–C3
                s.m3_supertrend_direction,      _tick(ec.m3_supertrend_aligned),
                _tick(ec.m3_rsi_above_ema),
                ec.histogram_value or 0,        _tick(ec.histogram_strong_enough),
                # M15 values
                _fmt(s.m15_macd_a_line, ".4f"), _fmt(s.m15_macd_a_signal, ".4f"),
                _fmt(s.m15_rsi, ".1f"),         _fmt(s.m15_rsi_ema_fast, ".1f"),
                _fmt(s.m15_macd_b_line, ".4f"), _fmt(s.m15_macd_b_signal, ".4f"),
                s.m15_supertrend_direction,
                # M3 values
                s.m3_supertrend or 0, s.m3_supertrend_direction,
                s.m3_rsi or 0, s.m3_rsi_ema_fast or 0, s.m3_rsi_ema_slow or 0,
                s.m3_macd_line or 0, s.m3_macd_signal or 0,
                s.m3_ema8_price or 0, s.m3_ema21_price or 0,
                # M1 values
                s.m1_rsi or 0, s.m1_rsi_ema_fast or 0, s.m1_rsi_ema_slow or 0,
                s.m1_supertrend or 0, s.m1_supertrend_direction,
            )

    def wait(self) -> Optional[tuple[Coil3MinIndicatorState, str]]:
        """Block until signal fires or entry window closes.
        Returns (state_snapshot, direction) or None."""
        def _hhmm(s: str):
            h, m = s.split(":")
            return _time(int(h), int(m))

        entry_start = _hhmm(self._cfg.get_str("ENTRY_START_TIME", "09:10"))
        entry_end   = _hhmm(self._cfg.get_str("ENTRY_END_TIME",   "23:00"))

        while not self._done.is_set():
            now = now_ist()
            t   = now.time()
            if t < MCX_OPEN_TIME or t < entry_start:
                time.sleep(10)
                continue
            if t > entry_end or t > MCX_CLOSE_TIME:
                logger.info("Entry window closed without a confirmed signal.")
                return None
            time.sleep(5)

        return self._result[0] if self._result else None


# ---------------------------------------------------------------------------
# Entry executors
# ---------------------------------------------------------------------------

class EntryExecutor(ABC):
    @abstractmethod
    def execute(self, leg: FutureLeg, strategy_id: str) -> None: ...

    @property
    @abstractmethod
    def mode_tag(self) -> str: ...


class PaperEntryExecutor(EntryExecutor):
    def __init__(self, client: OpenAlgoClient, exchange: str) -> None:
        self._client   = client
        self._exchange = exchange

    def execute(self, leg: FutureLeg, strategy_id: str) -> None:
        ltp = 0.0
        try:
            q   = self._client.get_quotes(leg.symbol, self._exchange)
            ltp = float(q.get("ltp") or 0)
        except Exception as exc:
            logger.debug("LTP fetch skipped for %s: %s", leg.symbol, exc)
        leg.entry_price = ltp if ltp > 0 else None
        leg.entry_time  = now_ist()
        leg.status      = FutureStatus.OPEN
        logger.info("PAPER entry stamped: %s @ %s", leg.symbol, leg.entry_price)

    @property
    def mode_tag(self) -> str:
        return EXECUTION_MODE_PAPER


class LiveEntryExecutor(EntryExecutor):
    def __init__(self, client: OpenAlgoClient, config: Config) -> None:
        self._client = client
        self._config = config

    def execute(self, leg: FutureLeg, strategy_id: str) -> None:
        # TODO: implement live MCX futures order placement
        raise NotImplementedError(
            "LiveEntryExecutor.execute() — implement once live trading is enabled."
        )

    @property
    def mode_tag(self) -> str:
        return EXECUTION_MODE_LIVE


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------

def _persist_entry(
    sf: SessionFactory,
    db_queue: DBWriteQueue,
    strategy_id: str,
    strategy_name: str,
    underlying: str,
    expiry: str,
    entry_spot: float,
    lots: int,
    lot_size: int,
    exec_mode: str,
    mode_tag: str,
    leg: FutureLeg,
    ind_state: Coil3MinIndicatorState,
) -> int:
    """Save run, leg, trade, and signal event to DB. Returns run_id."""
    sess   = sf.make()
    run_id = 0
    try:
        run_id = save_strategy_run(
            sess,
            strategy_type="DIRECTIONAL_FUTURES",
            strategy_id=strategy_id, strategy_name=strategy_name,
            underlying=underlying, expiry=expiry,
            direction=leg.direction.value,
            entry_spot=entry_spot,
            lots=lots, lot_size=lot_size,
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

    entry_dt     = leg.entry_time.replace(tzinfo=None) if leg.entry_time else None
    trade_status = "paper" if mode_tag == EXECUTION_MODE_PAPER else "complete"
    action_taken = "MONITOR_ONLY_SIGNAL" if mode_tag == EXECUTION_MODE_PAPER else "ENTRY_TRIGGERED"

    db_queue.put(
        save_leg,
        run_id=run_id, symbol=leg.symbol, exchange=leg.exchange,
        direction=leg.direction.value, quantity=leg.quantity, lot_size=leg.lot_size,
        product=leg.product, expiry=leg.expiry,
        entry_price=leg.entry_price, entry_order_id=leg.order_id,
        entry_time=entry_dt, status=leg.status.value,
    )
    db_queue.put(
        save_trade,
        run_id=run_id, event_type="ENTRY", symbol=leg.symbol,
        direction=leg.direction.value, quantity=leg.quantity,
        order_id=leg.order_id, fill_price=leg.entry_price,
        fill_time=entry_dt, status=trade_status,
    )
    db_queue.put(
        save_signal_event,
        run_id=run_id, ts=now_ist().replace(tzinfo=None),
        bias="BULLISH" if leg.direction == FutureDirection.LONG else "BEARISH",
        rsi=ind_state.m3_rsi,
        rsi_ema=ind_state.m3_rsi_ema_slow,
        ema_fast=ind_state.m3_rsi_ema_fast,
        ema_slow=ind_state.m3_rsi_ema_slow,
        supertrend=ind_state.m3_supertrend,
        supertrend_dir=ind_state.m3_supertrend_direction,
        spot=entry_spot, confirmation_n=1,
        action_taken=action_taken,
    )
    return run_id


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()

    try:
        config = Config(args.config)
    except ConfigError as exc:
        print(f"Config error: {exc}", file=sys.stderr)
        sys.exit(1)

    _log_file  = config.get_str("LOG_FILE", "logs/coil3_execution.log")
    _log_level = config.get_str("LOG_LEVEL", "INFO")
    setup_logger("strategy_framework", log_file=_log_file, level=_log_level)
    setup_logger("strategies",         log_file=_log_file, level=_log_level)
    exec_mode = config.get_str("EXECUTION_MODE", EXEC_MODE_MONITOR_ONLY)
    logger.info("=" * 60)
    logger.info("Crudeoil Intraday Futures 3-Min — EXECUTION SCRIPT")
    logger.info("Mode: %s  Underlying: %s", exec_mode, config.get_str("UNDERLYING", "CRUDEOIL"))
    logger.info("=" * 60)

    notifier     = _build_notifier(config)
    sf, db_queue = _build_db(config)
    client       = _build_client(config)

    underlying = config.get_str("UNDERLYING", "CRUDEOIL")
    exchange   = config.get_str("EXCHANGE",   "MCX")
    product    = config.get_str("PRODUCT",    "MIS")
    lot_size   = config.get_int("LOT_SIZE",   100)

    # ── 1. Expiry selection ────────────────────────────────────────────────────
    try:
        expiries = client.get_expiry_dates(underlying, exchange, INSTRUMENT_FUTURES)
        if not expiries:
            logger.critical("No futures expiries found for %s/%s.", underlying, exchange)
            db_queue.stop(); client.close(); sys.exit(1)

        expiry = select_expiry(expiries, mode="NEAREST", dte_min=3, dte_max=60)
        if not expiry:
            logger.critical("No suitable futures expiry found (DTE >= 3) for %s.", underlying)
            db_queue.stop(); client.close(); sys.exit(1)

        dte = days_to_expiry(expiry)
        logger.info("Futures expiry selected: %s  (DTE=%d)", expiry, dte)
    except Exception as exc:
        logger.critical("Failed to fetch expiry list: %s", exc)
        db_queue.stop(); client.close(); sys.exit(1)

    futures_symbol = f"{underlying}{expiry.replace('-', '')}FUT"
    logger.info("Futures symbol: %s", futures_symbol)

    strategy_id = _build_strategy_id(config)

    # ── 2. Idempotency guard (LIVE entry modes only) ───────────────────────────
    if exec_mode in (EXEC_MODE_ENTRY_EXEC_MONITOR, EXEC_MODE_FULL_EXEC):
        try:
            positions = client.get_positions()
            for pos in positions:
                sym = pos.get("symbol", "")
                qty = pos.get("quantity", 0)
                if underlying in sym and qty != 0:
                    msg = (f"SKIPPED: existing {underlying} position detected "
                           f"({sym} qty={qty}) — not re-entering.")
                    logger.warning(msg)
                    notifier.send(msg, level=LEVEL_ALERT)
                    db_queue.stop(); client.close(); sys.exit(0)
        except Exception as exc:
            logger.warning("Position check failed: %s — proceeding.", exc)

    # ── 3. Sizing ─────────────────────────────────────────────────────────────
    lots     = config.get_int("FIXED_LOTS", 1)
    max_lots = config.get_int("MAX_LOTS", 5)
    lots     = min(lots, max_lots)
    quantity = lots * lot_size
    logger.info("Sizing: %d lot(s) × %d = %d qty", lots, lot_size, quantity)

    # ── 4. Triple-timeframe warm-up ───────────────────────────────────────────
    warmup_bars_m1  = config.get_int("WARMUP_BARS_M1",  100)
    warmup_bars_m3  = config.get_int("WARMUP_BARS_M3",   50)
    warmup_bars_m15 = config.get_int("WARMUP_BARS_M15",  30)

    m1_builder  = CandleBuilder(symbol=underlying, interval_minutes=1)
    m3_builder  = CandleBuilder(symbol=underlying, interval_minutes=3)
    m15_builder = CandleBuilder(symbol=underlying, interval_minutes=15)
    ind_eng     = Coil3MinIndicatorEngine(config)

    logger.info("Loading M1 history (%d bars) …", warmup_bars_m1)
    try:
        hist_m1 = client.get_history(
            symbol=futures_symbol, exchange=exchange,
            interval="1m", source="api", warmup_bars=warmup_bars_m1,
        )
        if hist_m1:
            m1_builder.seed_from_history(hist_m1[-warmup_bars_m1:])
    except Exception as exc:
        logger.warning("M1 history fetch failed (%s) — building from live ticks.", exc)

    logger.info("Loading M3 history (%d bars) …", warmup_bars_m3)
    try:
        hist_m3 = client.get_history(
            symbol=futures_symbol, exchange=exchange,
            interval="3m", source="api", warmup_bars=warmup_bars_m3,
        )
        if hist_m3:
            m3_builder.seed_from_history(hist_m3[-warmup_bars_m3:])
    except Exception as exc:
        logger.warning("M3 history fetch failed (%s) — Supertrend builds from live 3-min candles.", exc)

    logger.info("Loading M15 history (%d bars) …", warmup_bars_m15)
    try:
        hist_m15 = client.get_history(
            symbol=futures_symbol, exchange=exchange,
            interval="15m", source="api", warmup_bars=warmup_bars_m15,
        )
        if hist_m15:
            m15_builder.seed_from_history(hist_m15[-warmup_bars_m15:])
    except Exception as exc:
        logger.warning("M15 history fetch failed (%s) — M15 indicators build from live candles.", exc)

    ind_eng.seed(m1_builder, m3_builder, m15_builder)
    logger.info(
        "Warm-up complete: M1=%d bars  M3=%d bars  M15=%d bars",
        m1_builder.closed_count, m3_builder.closed_count, m15_builder.closed_count,
    )

    # ── 5. Signal watch ───────────────────────────────────────────────────────
    trade_direction = config.get_str("TRADE_DIRECTION", "LONG").upper()
    logger.info("Trade direction: %s", trade_direction)
    watcher = SignalWatcher(config, m1_builder, m3_builder, m15_builder, ind_eng, direction=trade_direction)

    logger.info("Subscribing %s/%s to WebSocket …", exchange, futures_symbol)
    try:
        from openalgo import api as _OA
        _oa_ws = _OA(
            api_key=config.get_str("OPENALGO_API_KEY"),
            host=config.get_str("OPENALGO_HOST", "http://127.0.0.1:5000"),
        )
        if not _oa_ws.connect():
            raise RuntimeError("WebSocket connect() failed")
        _oa_ws.subscribe_ltp(
            [{"exchange": exchange, "symbol": futures_symbol}],
            on_data_received=watcher.on_tick,
        )
    except Exception as exc:
        logger.critical("WebSocket subscription failed: %s", exc)
        notifier.send(f"WebSocket failed: {exc}", level=LEVEL_URGENT)
        db_queue.stop(); client.close(); sys.exit(1)

    notifier.send(
        f"Execution started: {underlying} {expiry} lots={lots} mode={exec_mode} [3-Min]",
        level=LEVEL_INFO,
    )
    logger.info("Watching for 3-Min entry signal … (Ctrl+C to abort)")

    try:
        signal = watcher.wait()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt — aborting before entry.")
        db_queue.stop(); client.close(); sys.exit(0)

    if signal is None:
        db_queue.stop(); client.close(); sys.exit(2)

    ind_state, fired_direction = signal

    # ── 6. Build futures leg ──────────────────────────────────────────────────
    fut_direction = FutureDirection.LONG if fired_direction == "LONG" else FutureDirection.SHORT
    leg = FutureLeg(
        symbol=futures_symbol, exchange=exchange,
        direction=fut_direction,
        quantity=quantity, lot_size=lot_size,
        product=product, expiry=expiry,
    )

    # ── 7. Entry execution ────────────────────────────────────────────────────
    live_entry_modes = (EXEC_MODE_ENTRY_EXEC_MONITOR, EXEC_MODE_FULL_EXEC)
    executor: EntryExecutor = (
        LiveEntryExecutor(client, config)
        if exec_mode in live_entry_modes
        else PaperEntryExecutor(client, exchange)
    )

    try:
        executor.execute(leg, strategy_id)
    except NotImplementedError as exc:
        logger.critical("Live entry not implemented: %s", exc)
        notifier.send(f"LIVE ENTRY NOT IMPLEMENTED: {exc}", level=LEVEL_URGENT)
        db_queue.stop(); client.close(); sys.exit(1)
    except Exception as exc:
        logger.critical("Entry execution failed: %s", exc)
        notifier.send(f"ENTRY FAILED: {exc}", level=LEVEL_URGENT)
        db_queue.stop(); client.close(); sys.exit(1)

    entry_spot = ind_state.m1_close or (leg.entry_price or 0.0)
    run_id = _persist_entry(
        sf, db_queue,
        strategy_id=strategy_id,
        strategy_name=config.get_str("STRATEGY_NAME", "COIL3_INTRADAY_FUT"),
        underlying=underlying, expiry=expiry,
        entry_spot=entry_spot, lots=lots, lot_size=lot_size,
        exec_mode=exec_mode, mode_tag=executor.mode_tag,
        leg=leg, ind_state=ind_state,
    )

    price      = f"{leg.entry_price:.2f}" if leg.entry_price is not None else "N/A"
    order_side = "BUY" if fired_direction == "LONG" else "SELL"
    logger.info("=" * 60)
    logger.info("ENTRY COMPLETE [%s]  direction=%s", executor.mode_tag, fired_direction)
    logger.info("  %s %s qty=%d @ %s", order_side, leg.symbol, leg.quantity, price)
    logger.info("  run_id=%d", run_id)
    logger.info("=" * 60)
    notifier.send(
        f"ENTRY COMPLETE [{executor.mode_tag}] {fired_direction}: {underlying} {expiry}\n"
        f"  {order_side} {leg.symbol} qty={leg.quantity} @ {price}",
        level=LEVEL_ALERT,
    )

    db_queue.stop()
    client.close()
    sys.exit(0)


if __name__ == "__main__":
    main()
