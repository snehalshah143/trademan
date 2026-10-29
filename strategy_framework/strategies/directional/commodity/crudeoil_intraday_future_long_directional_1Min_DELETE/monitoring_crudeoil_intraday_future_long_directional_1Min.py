"""
monitoring_crudeoil_intraday_future_long_directional_1Min.py
------------------------------------------------------------
Crudeoil Intraday Futures — Monitoring Script (1-Min).

Recovers the open LONG futures position from DB, then subscribes to
WebSocket ticks and builds closed 1-min candles.

On each closed 1-min candle the ExitWatcher:
  1. Updates CoilIndicatorEngine (RSI, RSI-EMAs, MACD, Supertrend)
  2. Updates RollingLowWindow (last 4 candle lows)
  3. Evaluates all exit conditions via evaluate_exit():
       RSI14 cross below EMA21  (2-bar confirmation)
       MACD cross below Signal  (immediate)
       Close < min(last 4 lows) (immediate)
       Close < entry - 30 pts   (initial SL)
       Close < Supertrend       (Supertrend break)
       PriceStepTrailSL hit     (stepped trailing SL)
  4. Checks intraday square-off time (default 23:00 IST)

PAPER vs LIVE is resolved once in main() and injected as an ExitExecutor.
ExitWatcher itself contains no PAPER/LIVE branching.

Usage
-----
    python -m strategies.directional.commodity.crudeoil_intraday_future_long_directional_1Min.monitoring_crudeoil_intraday_future_long_directional_1Min
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
    get_open_runs, get_run_leg,
    save_mtm_snapshot, save_trade,
    update_leg, update_strategy_run,
)
from strategy_framework.framework.market_utils import is_mcx_market_open
from strategy_framework.framework.notifier import (
    LEVEL_ALERT, LEVEL_INFO, LEVEL_URGENT, LEVEL_WARNING,
    CompositeNotifier, LogNotifier, TelegramNotifier,
)
from strategy_framework.framework.openalgo_client import OpenAlgoClient
from strategy_framework.framework.signal import ConfirmationCounter
from strategy_framework.framework.trailing import PriceStepTrailSL
from strategy_framework.models.future_leg import FutureDirection, FutureLeg, FutureStatus
from strategy_framework.utils.constants import (
    EXEC_MODE_EXIT_EXEC_MONITOR, EXEC_MODE_FULL_EXEC, EXEC_MODE_MONITOR_ONLY,
    EXECUTION_MODE_LIVE, EXECUTION_MODE_PAPER,
)
from strategy_framework.utils.helper import format_inr, now_ist
from strategy_framework.utils.logger import get_logger, setup_logger

# Strategy-local engine: indicators + exit evaluator
from ._engine import (
    CoilIndicatorEngine, RollingLowWindow, evaluate_exit,
    EXIT_RSI_CROSS_BELOW, EXIT_MACD_CROSS_BELOW, EXIT_SWING_LOW_BREAK,
    EXIT_INITIAL_SL, EXIT_SUPERTREND_BREAK, EXIT_TRAILING_SL,
)

logger = get_logger(__name__)

EXIT_REASON_INTRADAY_SQUAREOFF = "INTRADAY_SQUAREOFF"


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crudeoil Intraday Futures — Monitoring Script")
    p.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config" / "monitoring_settings.yaml"),
    )
    return p.parse_args()


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
# Position recovery
# ---------------------------------------------------------------------------

def _recover_position(
    client: OpenAlgoClient,
    config: Config,
    sf: SessionFactory,
) -> tuple[Optional[FutureLeg], int, Optional[int]]:
    """
    Recover the open futures position from DB.

    Returns (leg, run_id, leg_db_id) or (None, 0, None).

    Positionbook fallback is only attempted in live exit modes
    (EXEC_MODE_EXIT_EXEC_MONITOR, EXEC_MODE_FULL_EXEC) — PAPER modes
    never place real orders so no broker position exists.
    """
    prefix   = config.get_str("STRATEGY_ID_PREFIX", "COIL")
    today_id = f"{prefix}_{now_ist().strftime('%Y%m%d')}"

    session = sf.make()
    try:
        open_runs = get_open_runs(session, today_id)
        if open_runs:
            run    = open_runs[0]
            db_leg = get_run_leg(session, run.id)
            if db_leg:
                leg = _db_leg_to_future_leg(db_leg)
                logger.info(
                    "Recovery: DB run_id=%d direction=%s entry=%.2f",
                    run.id, run.direction, leg.entry_price or 0,
                )
                return leg, run.id, db_leg.id
    except Exception as exc:
        logger.warning("DB recovery failed: %s", exc)
    finally:
        session.close()

    exec_mode       = config.get_str("EXECUTION_MODE", EXEC_MODE_MONITOR_ONLY)
    live_exit_modes = (EXEC_MODE_EXIT_EXEC_MONITOR, EXEC_MODE_FULL_EXEC)
    if exec_mode not in live_exit_modes:
        logger.info(
            "No DB run found and exec_mode=%s (PAPER) — skipping positionbook fallback.",
            exec_mode,
        )
        return None, 0, None

    underlying = config.get_str("UNDERLYING", "CRUDEOIL")
    exchange   = config.get_str("EXCHANGE",   "MCX")
    try:
        positions = client.get_positions()
        matched   = [
            p for p in positions
            if underlying in p.get("symbol", "") and p.get("quantity", 0) != 0
        ]
        if matched:
            pos = matched[0]
            qty = int(pos.get("quantity", 0))
            leg = FutureLeg(
                symbol=pos.get("symbol", ""),
                exchange=exchange,
                direction=FutureDirection.LONG if qty > 0 else FutureDirection.SHORT,
                quantity=abs(qty),
                lot_size=config.get_int("LOT_SIZE", 100),
                product=pos.get("product", "MIS"),
                expiry=pos.get("expiry", ""),
                entry_price=float(pos.get("averageprice", 0) or 0),
                status=FutureStatus.OPEN,
            )
            logger.info(
                "Recovery: positionbook — %s direction=%s entry=%.2f",
                leg.symbol, leg.direction, leg.entry_price or 0,
            )
            return leg, 0, None
    except Exception as exc:
        logger.warning("Positionbook recovery failed: %s", exc)

    return None, 0, None


def _db_leg_to_future_leg(db_leg) -> FutureLeg:
    return FutureLeg(
        symbol=db_leg.symbol, exchange=db_leg.exchange,
        direction=FutureDirection(db_leg.direction),
        quantity=db_leg.quantity, lot_size=db_leg.lot_size,
        product=db_leg.product, expiry=db_leg.expiry or "",
        entry_price=db_leg.entry_price, order_id=db_leg.entry_order_id,
        status=FutureStatus(db_leg.status),
    )


# ---------------------------------------------------------------------------
# Exit executors  (Dependency Inversion)
# ---------------------------------------------------------------------------

class ExitExecutor(ABC):
    """
    Fills the futures leg with exit price and marks it CLOSED.
    Subclasses handle PAPER simulation vs LIVE broker order.
    ExitWatcher never branches on PAPER/LIVE — it just calls fill_leg().
    """

    @abstractmethod
    def fill_leg(self, leg: FutureLeg) -> None: ...

    @property
    @abstractmethod
    def mode_tag(self) -> str: ...


class PaperExitExecutor(ExitExecutor):
    """Simulates fill at the leg's current_ltp. No broker orders sent."""

    def fill_leg(self, leg: FutureLeg) -> None:
        if leg.status != FutureStatus.OPEN:
            return
        exit_px          = leg.current_ltp or 0.0
        entry_px         = leg.entry_price or 0.0
        leg.exit_price   = exit_px
        leg.exit_time    = now_ist()
        leg.status       = FutureStatus.CLOSED
        diff             = exit_px - entry_px
        leg.realized_pnl = diff * leg.quantity if leg.is_long else -diff * leg.quantity
        logger.info(
            "PAPER exit: %s @ %.2f  entry=%.2f  realized_pnl=%.0f",
            leg.symbol, exit_px, entry_px, leg.realized_pnl or 0,
        )

    @property
    def mode_tag(self) -> str:
        return EXECUTION_MODE_PAPER


class LiveExitExecutor(ExitExecutor):
    """Places a close order via OpenAlgoClient."""

    def __init__(self, client: OpenAlgoClient, config: Config) -> None:
        self._client = client
        self._config = config

    def fill_leg(self, leg: FutureLeg) -> None:
        # TODO: implement live close order for MCX futures
        # Close a LONG position by placing a SELL order:
        #   resp = self._client.placeorder(
        #       strategy="COIL_MONITORING",
        #       symbol=leg.symbol, exchange=leg.exchange,
        #       action="SELL",
        #       quantity=leg.quantity,
        #       product=leg.product,
        #       pricetype="MARKET",
        #   )
        raise NotImplementedError(
            "LiveExitExecutor.fill_leg() — implement once live trading is enabled."
        )

    @property
    def mode_tag(self) -> str:
        return EXECUTION_MODE_LIVE


# ---------------------------------------------------------------------------
# ExitWatcher  (WebSocket-driven, 1-min candle-based)
# ---------------------------------------------------------------------------

class ExitWatcher:
    """
    Monitors an open LONG futures position using live WebSocket ticks.

    Architecture:
      - Ticks feed into CandleBuilder (1-min aggregation)
      - On each closed candle → CoilIndicatorEngine.update() + RollingLowWindow.update()
      - evaluate_exit() checks all 6 exit conditions in priority order
      - Intraday square-off time is also checked on each candle close
      - When any exit fires → _fire_exit() sets done event

    call warmup() to seed indicators from history, then subscribe the
    WebSocket to on_tick(), then call wait() to block until exit.
    """

    def __init__(
        self,
        config: Config,
        leg: FutureLeg,
        client: OpenAlgoClient,
        exit_executor: ExitExecutor,
        notifier: CompositeNotifier,
        db_queue: DBWriteQueue,
        run_id: int = 0,
        leg_db_id: Optional[int] = None,
    ) -> None:
        self._cfg        = config
        self._leg        = leg
        self._client     = client
        self._executor   = exit_executor
        self._notifier   = notifier
        self._db_queue   = db_queue
        self._run_id     = run_id
        self._leg_db_id  = leg_db_id

        underlying = config.get_str("UNDERLYING", "CRUDEOIL")
        self._builder     = CandleBuilder(symbol=underlying, interval_minutes=1)
        self._ind_eng     = CoilIndicatorEngine(config)
        self._low_window  = RollingLowWindow(n=config.get_int("SWING_LOW_CANDLES", 4))
        self._rsi_counter = ConfirmationCounter(required=2)

        entry_price = leg.entry_price or 0.0
        self._initial_sl_pts = config.get_float("INITIAL_SL_POINTS", 30.0)
        self._step_trail = PriceStepTrailSL(
            entry_price=entry_price,
            initial_sl_pts=self._initial_sl_pts,
            activation_pts=config.get_float("TRAIL_ACTIVATION_POINTS", 30.0),
            trail_offset=config.get_float("TRAIL_OFFSET", 20.0),
            step_pts=config.get_float("TRAIL_STEP_POINTS", 5.0),
        )

        # Parse intraday exit time once
        hhmm = config.get_str("INTRADAY_EXIT_TIME", "23:00").split(":")
        self._exit_time = _time(int(hhmm[0]), int(hhmm[1]))
        self._intraday  = config.get_str("TRADE_VALIDITY", "INTRADAY").upper() == "INTRADAY"

        self._snapshot_interval = config.get_int("MTM_SNAPSHOT_INTERVAL", 15)

        self._done          = threading.Event()
        self._exit_reason:  Optional[str] = None
        self._candle_count  = 0

        logger.info(
            "ExitWatcher init: %s entry=%.2f initial_sl=%.1f "
            "trail[act=%.0f off=%.0f step=%.0f] sq_off=%s mode=%s",
            leg.symbol, entry_price, self._initial_sl_pts,
            config.get_float("TRAIL_ACTIVATION_POINTS", 30.0),
            config.get_float("TRAIL_OFFSET", 20.0),
            config.get_float("TRAIL_STEP_POINTS", 5.0),
            self._exit_time, exit_executor.mode_tag,
        )

    # ── Warm-up ───────────────────────────────────────────────────────────────

    def warmup(self) -> None:
        """Load historical 1-min bars and seed CoilIndicatorEngine."""
        exchange    = self._cfg.get_str("EXCHANGE",   "MCX")
        warmup_bars = self._cfg.get_int("WARMUP_BARS", 100)
        try:
            hist = self._client.get_history(
                self._leg.symbol, exchange, interval="1m", source="api", warmup_bars=warmup_bars,
            )
            if hist:
                self._builder.seed_from_history(hist[-warmup_bars:])
                self._ind_eng.seed(self._builder)
                # Pre-fill rolling low window from history
                swing_n = self._cfg.get_int("SWING_LOW_CANDLES", 4)
                lows    = self._builder.lows()
                for low in lows[-swing_n:]:
                    self._low_window.update(float(low))
                logger.info(
                    "ExitWatcher warm-up: %d bars loaded.  Indicators ready=%s",
                    self._builder.closed_count, self._ind_eng.state.is_ready,
                )
            else:
                logger.warning("No history returned — indicators build from live data.")
        except Exception as exc:
            logger.warning("Warm-up failed (%s) — indicators build from live data.", exc)

    # ── Tick handler (called by WebSocket on every tick) ─────────────────────

    def on_tick(self, tick: dict) -> None:
        """Feed a WebSocket tick into the candle builder."""
        ltp = tick.get("data", {}).get("ltp") or tick.get("ltp") or tick.get("price")
        if ltp is None:
            return

        ltp = float(ltp)
        self._leg.current_ltp = ltp   # keep LTP fresh for MTM calculation

        closed_bar = self._builder.tick(ltp, datetime.now())
        if closed_bar is not None:
            self._on_candle_closed(closed_bar.close, closed_bar.high, closed_bar.low)

    # ── Candle-close logic ────────────────────────────────────────────────────

    def _on_candle_closed(self, close: float, high: float, low: float) -> None:
        """Evaluate all exit conditions on each closed 1-min candle."""
        if self._done.is_set():
            return

        self._candle_count += 1
        self._ind_eng.update(self._builder)
        self._low_window.update(low)

        ltp = self._leg.current_ltp or close
        mtm = self._leg.unrealized_pnl or 0.0

        # Save MTM snapshot every N candles (not every candle)
        if self._candle_count % self._snapshot_interval == 0:
            self._db_queue.put(
                save_mtm_snapshot,
                run_id=self._run_id,
                spot=close,
                futures_ltp=ltp,
                mtm=mtm,
                locked_mtm=self._step_trail.current_sl,
            )

        self._log_heartbeat(close, mtm)

        # Intraday square-off time check
        now = now_ist()
        if self._intraday and now.time() >= self._exit_time:
            logger.info("Intraday square-off time reached (%s IST).", self._exit_time)
            self._fire_exit(EXIT_REASON_INTRADAY_SQUAREOFF)
            return

        if not is_mcx_market_open(now):
            return

        if not self._ind_eng.state.is_ready:
            logger.debug("Indicators not ready yet (%d candles).", self._candle_count)
            return

        entry_price = self._leg.entry_price or 0.0
        reason = evaluate_exit(
            ind=self._ind_eng.state,
            close=close,
            low_window=self._low_window,
            entry_price=entry_price,
            initial_sl_pts=self._initial_sl_pts,
            step_trail=self._step_trail,
            rsi_exit_counter=self._rsi_counter,
        )
        if reason:
            self._fire_exit(reason)

    def _fire_exit(self, reason: str) -> None:
        if not self._done.is_set():
            self._exit_reason = reason
            self._done.set()

    # ── Blocking wait ─────────────────────────────────────────────────────────

    def wait(self) -> Optional[str]:
        """
        Block the main thread until an exit fires or MCX market closes.

        Returns the exit reason string, or None if market closed naturally
        without an exit (position will have been auto-squared by broker at
        MCX close).
        """
        self._notifier.send(
            f"Monitoring started: {self._leg.symbol} "
            f"entry={self._leg.entry_price or 'N/A'} "
            f"SL={self._leg.entry_price - self._initial_sl_pts if self._leg.entry_price else 'N/A'} "
            f"mode={self._executor.mode_tag}",
            level=LEVEL_INFO,
        )
        logger.info("ExitWatcher running — waiting for exit signal … (Ctrl+C to stop)")

        while not self._done.is_set():
            if not is_mcx_market_open():
                logger.info("MCX market closed — stopping ExitWatcher.")
                return None
            time.sleep(5)

        return self._exit_reason

    # ── Exit execution (called from main after wait() returns) ───────────────

    def execute_exit(self, reason: str) -> None:
        """Execute exit fill, persist to DB, and notify. Call after wait() returns."""
        logger.info("Executing exit: reason=%s  mode=%s", reason, self._executor.mode_tag)
        self._notifier.send(
            f"EXIT TRIGGERED: {self._leg.symbol} reason={reason} "
            f"MTM={format_inr(self._leg.unrealized_pnl)}",
            level=LEVEL_ALERT,
        )

        try:
            self._executor.fill_leg(self._leg)
        except NotImplementedError as exc:
            logger.critical("Exit not implemented: %s", exc)
            self._notifier.send(f"EXIT NOT IMPLEMENTED: {exc}", level=LEVEL_URGENT)
            return
        except Exception as exc:
            logger.critical("Exit execution failed: %s", exc)
            self._notifier.send(f"EXIT FAILED: {exc}", level=LEVEL_URGENT)
            return

        self._persist_exit(reason)
        self._notify_exit_complete(reason)

    # ── Persistence helpers ───────────────────────────────────────────────────

    def _persist_exit(self, reason: str) -> None:
        if self._leg.status != FutureStatus.CLOSED:
            return
        exit_dt = self._leg.exit_time.replace(tzinfo=None) if self._leg.exit_time else None

        if self._leg_db_id:
            self._db_queue.put(
                update_leg, leg_id=self._leg_db_id,
                exit_price=self._leg.exit_price, exit_time=exit_dt,
                status=FutureStatus.CLOSED.value,
                exit_order_id=self._leg.order_id,
                realized_pnl=self._leg.realized_pnl,
            )
        self._db_queue.put(
            save_trade,
            run_id=self._run_id, leg_id=self._leg_db_id,
            event_type="EXIT", symbol=self._leg.symbol,
            direction=self._leg.direction.value, quantity=self._leg.quantity,
            order_id=self._leg.order_id, fill_price=self._leg.exit_price,
            fill_time=exit_dt, status="complete",
            notes=f"reason={reason} mode={self._executor.mode_tag}",
        )
        self._db_queue.put(
            update_strategy_run, run_id=self._run_id,
            exit_time=now_ist().replace(tzinfo=None),
            exit_reason=reason, status="CLOSED",
            realized_pnl=self._leg.realized_pnl or 0.0,
        )

    def _notify_exit_complete(self, reason: str) -> None:
        tag  = self._executor.mode_tag
        pnl  = self._leg.realized_pnl or 0.0
        ts   = (self._leg.exit_time.strftime("%H:%M:%S")
                if self._leg.exit_time else now_ist().strftime("%H:%M:%S"))
        line = (
            f"SELL {self._leg.symbol} qty={self._leg.quantity} "
            f"@ {self._leg.exit_price:.2f} [{tag}] at {ts}"
        )
        logger.info("=" * 60)
        logger.info("EXIT COMPLETE: %s", line)
        logger.info("Realized P&L: %s  (reason=%s)", format_inr(pnl), reason)
        logger.info("=" * 60)
        self._notifier.send(
            f"EXIT COMPLETE [{tag}]: {self._leg.symbol} reason={reason}\n"
            f"  {line}\n"
            f"  Realized P&L: {format_inr(pnl)}",
            level=LEVEL_ALERT,
        )

    # ── Heartbeat ─────────────────────────────────────────────────────────────

    def _log_heartbeat(self, close: float, mtm: float) -> None:
        s   = self._ind_eng.state
        leg = self._leg
        logger.info(
            "HEARTBEAT | #%d close=%.2f entry=%.2f MTM=%.0f | "
            "SL=%.2f trail_act=%s | "
            "RSI=%.1f MACD=%.4f ST=%.2f dir=%s",
            self._candle_count, close, leg.entry_price or 0, mtm,
            self._step_trail.current_sl, self._step_trail.is_activated,
            s.rsi or 0, s.macd_line or 0,
            s.supertrend or 0, s.supertrend_direction,
        )


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

    _log_file  = config.get_str("LOG_FILE", "logs/coil_monitoring.log")
    _log_level = config.get_str("LOG_LEVEL", "INFO")
    setup_logger("strategy_framework", log_file=_log_file, level=_log_level)
    setup_logger("strategies",         log_file=_log_file, level=_log_level)
    exec_mode = config.get_str("EXECUTION_MODE", EXEC_MODE_MONITOR_ONLY)
    logger.info("=" * 60)
    logger.info("Crudeoil Intraday Futures — MONITORING SCRIPT")
    logger.info("Mode: %s  Underlying: %s", exec_mode, config.get_str("UNDERLYING", "CRUDEOIL"))
    logger.info("=" * 60)

    notifier     = _build_notifier(config)
    sf, db_queue = _build_db(config)
    client       = _build_client(config)

    logger.info("Recovery scan …")
    leg, run_id, leg_db_id = _recover_position(client, config, sf)
    if not leg:
        logger.critical("No open position found — exiting.")
        notifier.send("No open position found — monitoring exiting.", level=LEVEL_WARNING)
        db_queue.stop(); client.close(); sys.exit(0)

    logger.info(
        "Position recovered: %s  direction=%s  entry=%.2f  run_id=%d",
        leg.symbol, leg.direction.value, leg.entry_price or 0, run_id,
    )

    live_exit_modes = (EXEC_MODE_EXIT_EXEC_MONITOR, EXEC_MODE_FULL_EXEC)
    is_live_exit    = exec_mode in live_exit_modes
    exit_executor: ExitExecutor = (
        LiveExitExecutor(client, config) if is_live_exit else PaperExitExecutor()
    )

    watcher = ExitWatcher(
        config=config,
        leg=leg,
        client=client,
        exit_executor=exit_executor,
        notifier=notifier,
        db_queue=db_queue,
        run_id=run_id,
        leg_db_id=leg_db_id,
    )
    watcher.warmup()

    underlying = config.get_str("UNDERLYING", "CRUDEOIL")
    exchange   = config.get_str("EXCHANGE",   "MCX")

    logger.info("Subscribing %s/%s to WebSocket …", exchange, leg.symbol)
    try:
        from openalgo import api as _OA
        _oa_ws = _OA(
            api_key=config.get_str("OPENALGO_API_KEY"),
            host=config.get_str("OPENALGO_HOST", "http://127.0.0.1:5000"),
        )
        if not _oa_ws.connect():
            raise RuntimeError("WebSocket connect() failed — check OpenAlgo WS on port 8765")
        _oa_ws.subscribe_ltp(
            [{"exchange": exchange, "symbol": leg.symbol}],
            on_data_received=watcher.on_tick,
        )
    except Exception as exc:
        logger.critical("WebSocket subscription failed: %s", exc)
        notifier.send(f"WebSocket failed: {exc}", level=LEVEL_URGENT)
        db_queue.stop(); client.close(); sys.exit(1)

    exit_reason = None
    try:
        exit_reason = watcher.wait()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt — stopping monitoring.")

    if exit_reason:
        watcher.execute_exit(exit_reason)

    # Flush DB queue before shutting down (execute_exit may have queued writes)
    db_queue.stop()
    client.close()
    logger.info("Monitoring script shutdown complete.")


if __name__ == "__main__":
    main()
