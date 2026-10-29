"""
execution_crudeoil_intraday_future_long_directional_1Min.py
-----------------------------------------------------------
Crudeoil Intraday Futures — Directional Entry Script (1-Min).

LONG only strategy.  BUY CRUDEOIL futures on BULLISH signal.

Entry conditions (all three required, evaluated on each closed 1-min candle):
  1. M1-Close > M1-Supertrend(11, 2.0)
  2. M1-MACD(fast=8, slow=21, signal=5) > MACD Signal
     OR M1-RSI14-EMA8 > M1-RSI14-EMA21
  3. M1-RSI14 > M1-RSI14-EMA21

Signal fires on the FIRST closed 1-min candle where all three conditions hold.
No N-bar confirmation for entry — fires immediately.

Execution modes:
  MONITOR_ONLY        — paper signal; no broker orders
  ENTRY_EXEC_MONITOR  — live entry; monitoring script handles exit
  EXIT_EXEC_MONITOR   — paper entry; monitoring script places live exits
  FULL_EXEC           — live entry + live exit

Usage
-----
    python -m strategies.directional.commodity.crudeoil_intraday_future_long_directional_1Min.execution_crudeoil_intraday_future_long_directional_1Min
"""
from __future__ import annotations

import argparse
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
from strategy_framework.framework.market_utils import is_mcx_market_open, is_within_window, MCX_OPEN_TIME, MCX_CLOSE_TIME
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

# Strategy-local engine: indicators + entry evaluator
from ._engine import CoilIndicatorEngine, CoilIndicatorState, evaluate_entry

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crudeoil Intraday Futures — Entry Script")
    p.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config" / "execution_settings.yaml"),
    )
    return p.parse_args()


def _build_strategy_id(config: Config) -> str:
    return f"{config.get_str('STRATEGY_ID_PREFIX', 'COIL')}_{now_ist().strftime('%Y%m%d')}"


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
# Signal watcher  (WebSocket-driven, 1-min candle-based)
# ---------------------------------------------------------------------------

class SignalWatcher:
    """
    Processes WebSocket ticks, builds 1-min candles, updates CoilIndicatorEngine,
    and fires on the first closed candle where evaluate_entry() returns True.

    Entry fires on a SINGLE closed 1-min candle satisfying all three conditions.
    No multi-bar confirmation — trades are taken immediately on the signal bar.
    """

    def __init__(
        self,
        config: Config,
        builder: CandleBuilder,
        ind_eng: CoilIndicatorEngine,
    ) -> None:
        self._cfg     = config
        self._builder = builder
        self._ind_eng = ind_eng
        self._done    = threading.Event()
        self._result: list[CoilIndicatorState] = []

    def on_tick(self, tick: dict) -> None:
        ltp = tick.get("data", {}).get("ltp") or tick.get("ltp") or tick.get("price")
        if ltp is None:
            return

        closed_bar = self._builder.tick(float(ltp), datetime.now())
        if closed_bar is None:
            return  # candle still building

        self._ind_eng.update(self._builder)

        if self._done.is_set():
            return

        # Only fire within MCX hours and the configured entry window
        now = now_ist()
        if not is_mcx_market_open(now):
            return
        if not is_within_window(
            now,
            self._cfg.get_str("ENTRY_START_TIME", "09:10"),
            self._cfg.get_str("ENTRY_END_TIME",   "23:00"),
        ):
            return

        if not evaluate_entry(self._ind_eng.state):
            return

        s = self._ind_eng.state
        logger.info(
            "ENTRY SIGNAL CONFIRMED: close=%.2f ST=%.2f MACD=%.4f Sig=%.4f "
            "RSI=%.1f RSI_EMA_slow=%.1f RSI_EMA_fast=%.1f",
            s.close or 0, s.supertrend or 0,
            s.macd_line or 0, s.macd_signal or 0,
            s.rsi or 0, s.rsi_ema_slow or 0, s.rsi_ema_fast or 0,
        )
        # Capture a snapshot of the indicator state at signal time
        import copy
        self._result.append(copy.copy(s))
        self._done.set()

    def wait(self) -> Optional[CoilIndicatorState]:
        """
        Block until a signal fires or the entry window closes.

        Returns the CoilIndicatorState snapshot at signal time, or None if no trade.
        """
        def _hhmm(s: str):
            h, m = s.split(":")
            return _time(int(h), int(m))
        entry_start = _hhmm(self._cfg.get_str("ENTRY_START_TIME", "09:10"))
        entry_end   = _hhmm(self._cfg.get_str("ENTRY_END_TIME",   "23:00"))
        while not self._done.is_set():
            now = now_ist()
            t   = now.time()
            # Pre-open or before entry window — sleep and wait
            if t < MCX_OPEN_TIME or t < entry_start:
                time.sleep(10)
                continue
            # Post entry-end or post market-close — exit cleanly
            if t > entry_end or t > MCX_CLOSE_TIME:
                logger.info("Entry window closed without a confirmed signal.")
                return None
            time.sleep(5)
        return self._result[0] if self._result else None


# ---------------------------------------------------------------------------
# Entry executors  (Dependency Inversion)
# ---------------------------------------------------------------------------

class EntryExecutor(ABC):
    """Stamps entry price on the leg. PAPER vs LIVE is the only difference."""

    @abstractmethod
    def execute(self, leg: FutureLeg, strategy_id: str) -> None: ...

    @property
    @abstractmethod
    def mode_tag(self) -> str: ...


class PaperEntryExecutor(EntryExecutor):
    """Stamps current futures LTP as entry price. No broker orders sent."""

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
    """Places a single futures BUY order via OpenAlgoClient."""

    def __init__(self, client: OpenAlgoClient, config: Config) -> None:
        self._client = client
        self._config = config

    def execute(self, leg: FutureLeg, strategy_id: str) -> None:
        # TODO: implement live order placement for MCX futures
        # Reference:
        #   resp = self._client.placeorder(
        #       strategy=strategy_id,
        #       symbol=leg.symbol, exchange=leg.exchange,
        #       action="BUY",
        #       quantity=leg.quantity,
        #       product=leg.product,
        #       pricetype="MARKET",
        #   )
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
    ind_state: CoilIndicatorState,
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
        bias="BULLISH",
        rsi=ind_state.rsi,
        rsi_ema=ind_state.rsi_ema_slow,
        ema_fast=ind_state.rsi_ema_fast,
        ema_slow=ind_state.rsi_ema_slow,
        supertrend=ind_state.supertrend,
        supertrend_dir=ind_state.supertrend_direction,
        spot=entry_spot, confirmation_n=1,
        action_taken=action_taken,
    )
    return run_id


# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def _notify_entry(
    notifier: CompositeNotifier,
    leg: FutureLeg,
    underlying: str,
    expiry: str,
    lots: int,
    mode_tag: str,
) -> None:
    price = f"{leg.entry_price:.2f}" if leg.entry_price is not None else "N/A"
    ts    = (leg.entry_time.strftime("%H:%M:%S")
             if leg.entry_time else now_ist().strftime("%H:%M:%S"))
    line  = (
        f"BUY {leg.symbol} qty={leg.quantity} "
        f"@ {price} [{mode_tag}] at {ts}"
    )
    logger.info("=" * 60)
    logger.info("ENTRY COMPLETE [%s]", mode_tag)
    logger.info("  %s", line)
    logger.info("Lots: %d  Qty: %d  Underlying: %s  Expiry: %s", lots, leg.quantity, underlying, expiry)
    logger.info("=" * 60)
    notifier.send(
        f"ENTRY COMPLETE [{mode_tag}]: {underlying} {expiry}\n  {line}",
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

    _log_file  = config.get_str("LOG_FILE", "logs/coil_execution.log")
    _log_level = config.get_str("LOG_LEVEL", "INFO")
    setup_logger("strategy_framework", log_file=_log_file, level=_log_level)
    setup_logger("strategies",         log_file=_log_file, level=_log_level)
    exec_mode = config.get_str("EXECUTION_MODE", EXEC_MODE_MONITOR_ONLY)
    logger.info("=" * 60)
    logger.info("Crudeoil Intraday Futures — EXECUTION SCRIPT")
    logger.info("Mode: %s  Underlying: %s", exec_mode, config.get_str("UNDERLYING", "CRUDEOIL"))
    logger.info("=" * 60)

    notifier     = _build_notifier(config)
    sf, db_queue = _build_db(config)
    client       = _build_client(config)

    underlying = config.get_str("UNDERLYING", "CRUDEOIL")
    exchange   = config.get_str("EXCHANGE",   "MCX")
    product    = config.get_str("PRODUCT",    "MIS")
    lot_size   = config.get_int("LOT_SIZE",   100)

    # ── 2. Expiry selection (DTE >= 3; roll to next if too close) ─────────────
    try:
        expiries = client.get_expiry_dates(underlying, exchange, INSTRUMENT_FUTURES)
        if not expiries:
            logger.critical("No futures expiries found for %s/%s.", underlying, exchange)
            db_queue.stop(); client.close(); sys.exit(1)

        expiry = select_expiry(expiries, mode="NEAREST", dte_min=3, dte_max=60)
        if not expiry:
            logger.critical(
                "No suitable futures expiry found (DTE >= 3) for %s. "
                "Nearest has DTE < 3 — wait for rollover or check manually.",
                underlying,
            )
            db_queue.stop(); client.close(); sys.exit(1)

        dte = days_to_expiry(expiry)
        logger.info("Futures expiry selected: %s  (DTE=%d)", expiry, dte)
    except Exception as exc:
        logger.critical("Failed to fetch expiry list: %s", exc)
        db_queue.stop(); client.close(); sys.exit(1)

    # MCX futures symbol format: CRUDEOIL19NOV26FUT  (strip hyphens, append FUT)
    futures_symbol = f"{underlying}{expiry.replace('-', '')}FUT"
    logger.info("Futures symbol: %s", futures_symbol)

    strategy_id = _build_strategy_id(config)

    # ── 3. Idempotency guard (LIVE entry modes only) ───────────────────────────
    if exec_mode in (EXEC_MODE_ENTRY_EXEC_MONITOR, EXEC_MODE_FULL_EXEC):
        try:
            positions = client.get_positions()
            for pos in positions:
                sym = pos.get("symbol", "")
                qty = pos.get("quantity", 0)
                if underlying in sym and qty != 0:
                    msg = (f"SKIPPED: existing {underlying} futures position detected "
                           f"({sym} qty={qty}) — not re-entering.")
                    logger.warning(msg)
                    notifier.send(msg, level=LEVEL_ALERT)
                    db_queue.stop(); client.close(); sys.exit(0)
        except Exception as exc:
            logger.warning("Position check failed: %s — proceeding.", exc)

    # ── 4. Sizing ─────────────────────────────────────────────────────────────
    lots     = config.get_int("FIXED_LOTS", 1)
    max_lots = config.get_int("MAX_LOTS", 5)
    lots     = min(lots, max_lots)
    quantity = lots * lot_size
    logger.info("Sizing: %d lot(s) × %d = %d qty", lots, lot_size, quantity)

    # ── 5. Warm-up (1-min candle history) ─────────────────────────────────────
    warmup_bars = config.get_int("WARMUP_BARS", 100)
    builder     = CandleBuilder(symbol=underlying, interval_minutes=1)
    ind_eng     = CoilIndicatorEngine(config)

    logger.info("Loading %d historical 1m bars for indicator warm-up …", warmup_bars)
    try:
        hist = client.get_history(
            symbol=futures_symbol, exchange=exchange,
            interval="1m", source="api", warmup_bars=warmup_bars,
        )
        if hist:
            builder.seed_from_history(hist[-warmup_bars:])
            ind_eng.seed(builder)
            logger.info("Warm-up complete: %d bars loaded.", builder.closed_count)
        else:
            logger.warning("No history returned — indicators will build from live data.")
    except Exception as exc:
        logger.warning("History fetch failed (%s) — continuing without warm-up.", exc)

    # ── 6. Signal watch ───────────────────────────────────────────────────────
    watcher = SignalWatcher(config, builder, ind_eng)

    logger.info("Subscribing %s/%s to WebSocket …", exchange, underlying)
    try:
        from openalgo import api as _OA
        _oa_ws = _OA(
            api_key=config.get_str("OPENALGO_API_KEY"),
            host=config.get_str("OPENALGO_HOST", "http://127.0.0.1:5000"),
        )
        if not _oa_ws.connect():
            raise RuntimeError("WebSocket connect() failed — check OpenAlgo WS on port 8765")
        _oa_ws.subscribe_ltp(
            [{"exchange": exchange, "symbol": futures_symbol}],
            on_data_received=watcher.on_tick,
        )
    except Exception as exc:
        logger.critical("WebSocket subscription failed: %s", exc)
        notifier.send(f"WebSocket failed: {exc}", level=LEVEL_URGENT)
        db_queue.stop(); client.close(); sys.exit(1)

    notifier.send(
        f"Execution started: {underlying} {expiry} lots={lots} direction=LONG mode={exec_mode}",
        level=LEVEL_INFO,
    )
    logger.info(
        "Watching for 1-min entry signal [09:10–23:00 MCX] … (Ctrl+C to abort)"
    )

    try:
        ind_state = watcher.wait()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt — aborting before entry.")
        db_queue.stop(); client.close(); sys.exit(0)

    if ind_state is None:
        # Exit code 2 = clean stop: MCX closed / entry window passed / no signal.
        # final script checks returncode == 2 and skips monitoring.
        db_queue.stop(); client.close(); sys.exit(2)

    # ── 7. Build futures leg ──────────────────────────────────────────────────
    leg = FutureLeg(
        symbol=futures_symbol, exchange=exchange,
        direction=FutureDirection.LONG,
        quantity=quantity, lot_size=lot_size,
        product=product, expiry=expiry,
    )
    logger.info("Leg: %s", leg)
    notifier.send(
        f"SIGNAL: BULLISH → BUY {futures_symbol} qty={quantity}",
        level=LEVEL_ALERT,
    )

    # ── 8. Funds check ────────────────────────────────────────────────────────
    try:
        funds     = client.get_funds()
        available = float(funds.get("availablecash", 0) or 0)
        logger.info("Available cash: ₹%.0f", available)
        deployed  = config.get_float("DEPLOYED_CAPITAL", 0.0)
        if deployed > 0 and available < deployed:
            logger.warning(
                "Available cash ₹%.0f < deployed capital ₹%.0f — proceeding with caution.",
                available, deployed,
            )
    except Exception as exc:
        logger.warning("Funds check failed: %s — proceeding.", exc)

    # ── 9. Resolve executor ───────────────────────────────────────────────────
    is_live_entry = exec_mode in (EXEC_MODE_ENTRY_EXEC_MONITOR, EXEC_MODE_FULL_EXEC)
    entry_executor: EntryExecutor = (
        LiveEntryExecutor(client, config)
        if is_live_entry
        else PaperEntryExecutor(client, exchange)
    )

    # ── 10. Execute ───────────────────────────────────────────────────────────
    logger.info("Executing entry [%s] …", entry_executor.mode_tag)
    try:
        entry_executor.execute(leg, strategy_id)
    except NotImplementedError as exc:
        logger.critical("Entry not implemented: %s", exc)
        notifier.send(f"ENTRY NOT IMPLEMENTED: {exc}", level=LEVEL_URGENT)
        db_queue.stop(); client.close(); sys.exit(1)
    except Exception as exc:
        logger.critical("Entry failed: %s", exc)
        notifier.send(f"ENTRY FAILED: {exc}", level=LEVEL_URGENT)
        db_queue.stop(); client.close(); sys.exit(1)

    # ── 11. Persist ───────────────────────────────────────────────────────────
    entry_spot = leg.entry_price or 0.0
    _persist_entry(
        sf, db_queue,
        strategy_id=strategy_id,
        strategy_name=config.get_str("STRATEGY_NAME", "COIL_INTRADAY_FUT"),
        underlying=underlying, expiry=expiry,
        entry_spot=entry_spot,
        lots=lots, lot_size=lot_size,
        exec_mode=exec_mode, mode_tag=entry_executor.mode_tag,
        leg=leg, ind_state=ind_state,
    )

    # ── 12. Notify ────────────────────────────────────────────────────────────
    _notify_entry(
        notifier, leg,
        underlying=underlying, expiry=expiry,
        lots=lots, mode_tag=entry_executor.mode_tag,
    )

    # ── 13. Shutdown ──────────────────────────────────────────────────────────
    db_queue.stop()
    client.close()
    logger.info("Execution script complete — monitoring script will pick up run_id from DB.")


if __name__ == "__main__":
    main()
