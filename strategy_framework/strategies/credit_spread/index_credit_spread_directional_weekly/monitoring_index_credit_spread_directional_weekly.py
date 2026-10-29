"""
monitoring_index_credit_spread_directional_weekly.py
-----------------------------------------------------
Directional Index Credit Spread — Monitoring Script (Weekly).

Each cycle:
  1. Fetch live LTPs from option chain
  2. Compute MTM and update trailing SL
  3. Persist MTM snapshot to DB (non-blocking)
  4. Check all exit conditions (SL, ST flip, expiry, intraday, profit ladder)
  5. On exit trigger → fill legs → persist DB → notify

PAPER vs LIVE is resolved once in main() and injected as an ExitExecutor.
MonitoringLoop itself contains no PAPER/LIVE branching.

Usage
-----
    python -m strategies.credit_spread.index_credit_spread_directional_weekly.monitoring_index_credit_spread_directional_weekly
"""
from __future__ import annotations

import argparse
import sys
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
    SessionFactory, get_open_runs, get_run_legs,
    save_adjustment, save_mtm_snapshot, save_notification,
    save_trade, update_leg, update_strategy_run,
)
from strategy_framework.framework.execution_gateway import ExecutionError, ExecutionGateway
from strategy_framework.framework.expiry_utils import days_to_expiry
from strategy_framework.framework.indicators import supertrend_seed, supertrend_step
from strategy_framework.framework.market_utils import is_exit_window, is_market_open
from strategy_framework.framework.notifier import (
    LEVEL_ALERT, LEVEL_INFO, LEVEL_URGENT, LEVEL_WARNING,
)
from strategy_framework.framework.openalgo_client import OpenAlgoClient, OpenAlgoError
from strategy_framework.framework.script_helpers import (
    build_client, build_db, build_gateway, build_notifier,
)
from strategy_framework.framework.signal import (
    supertrend_flipped_bearish, supertrend_flipped_bullish,
)
from strategy_framework.framework.spread_template import spread_net_premium
from strategy_framework.framework.trailing import ProfitLadder, TrailType, TrailingSlManager
from strategy_framework.models.option_leg import LegAction, LegRole, LegStatus, OptionLeg, OptionType
from strategy_framework.models.strategy_context import OptionChainEntry, StrategyContext
from strategy_framework.models.strategy_state import StrategyState
from strategy_framework.utils.constants import (
    EXEC_MODE_EXIT_EXEC_MONITOR, EXEC_MODE_FULL_EXEC, EXEC_MODE_MONITOR_ONLY,
    EXECUTION_MODE_LIVE, EXECUTION_MODE_PAPER,
    EXIT_REASON_EXPIRY, EXIT_REASON_LADDER, EXIT_REASON_SL_HIT,
    EXIT_REASON_ST_FLIP,
    HEARTBEAT_SEPARATOR,
)
from strategy_framework.utils.helper import format_inr, now_ist
from strategy_framework.utils.logger import get_logger, setup_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Directional Index Credit Spread — Monitoring Script"
    )
    p.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config" / "monitoring_settings.yaml"),
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Position recovery
# ---------------------------------------------------------------------------

def _recover_position(
    client: OpenAlgoClient,
    config: Config,
    sf: SessionFactory,
) -> tuple[Optional[list], Optional[str], int, dict]:
    """
    Recover an open position from DB (preferred) or positionbook (LIVE-only fallback).
    Returns (legs, spread_type, run_id, leg_id_map).
    run_id=0 and leg_id_map={} when recovered from positionbook only.

    Positionbook fallback is skipped in PAPER modes (MONITOR_ONLY, ENTRY_EXEC_MONITOR)
    because no real orders were placed — broker positions belong to other strategies.
    """
    prefix   = config.get_str("STRATEGY_ID_PREFIX", "ICS")
    today_id = f"{prefix}_{now_ist().strftime('%Y%m%d')}"

    session = sf.make()
    try:
        open_runs = get_open_runs(session, today_id)
        if open_runs:
            run        = open_runs[0]
            db_legs    = get_run_legs(session, run.id)
            legs       = _db_legs_to_option_legs(db_legs)
            leg_id_map = {dl.symbol: dl.id for dl in db_legs}
            logger.info("Recovery: DB run_id=%d spread=%s", run.id, run.spread_type)
            return legs, run.spread_type, run.id, leg_id_map
    except Exception as exc:
        logger.warning("DB recovery failed: %s", exc)
    finally:
        session.close()

    # Positionbook fallback — LIVE execution modes only.
    # In PAPER modes (MONITOR_ONLY / ENTRY_EXEC_MONITOR) no real orders were placed,
    # so matching broker positions would belong to unrelated strategies.
    exec_mode       = config.get_str("EXECUTION_MODE", EXEC_MODE_MONITOR_ONLY)
    live_exit_modes = (EXEC_MODE_EXIT_EXEC_MONITOR, EXEC_MODE_FULL_EXEC)
    if exec_mode not in live_exit_modes:
        logger.info(
            "No DB run found and exec_mode=%s (PAPER) — skipping positionbook fallback.",
            exec_mode,
        )
        return None, None, 0, {}

    underlying = config.get_str("UNDERLYING", "NIFTY")
    try:
        positions = client.get_positions()
        matched   = [
            p for p in positions
            if underlying in p.get("symbol", "") and p.get("quantity", 0) != 0
        ]
        if matched:
            legs        = _positionbook_to_option_legs(matched, config)
            spread_type = _infer_spread_type(legs)
            logger.info("Recovery: positionbook — %d leg(s)", len(matched))
            return legs, spread_type, 0, {}
    except Exception as exc:
        logger.warning("Positionbook recovery failed: %s", exc)

    return None, None, 0, {}


def _db_legs_to_option_legs(db_legs) -> list[OptionLeg]:
    return [
        OptionLeg(
            role=LegRole(dl.role),
            symbol=dl.symbol,
            exchange=dl.exchange,
            strike=dl.strike,
            option_type=OptionType(dl.option_type),
            action=LegAction(dl.action),
            quantity=dl.quantity,
            product=dl.product,
            expiry=dl.expiry,
            entry_price=dl.entry_price,
            order_id=dl.entry_order_id,
            status=LegStatus(dl.status),
        )
        for dl in db_legs
    ]


def _positionbook_to_option_legs(positions: list[dict], config: Config) -> list[OptionLeg]:
    legs = []
    for pos in positions:
        sym    = pos.get("symbol", "")
        qty    = int(pos.get("quantity", 0))
        action = LegAction.BUY if qty > 0 else LegAction.SELL
        opt_t  = OptionType.CE if sym.endswith("CE") else OptionType.PE
        role   = LegRole.HEDGE if action == LegAction.BUY else LegRole.PRIMARY
        legs.append(OptionLeg(
            role=role, symbol=sym,
            exchange=config.get_str("EXCHANGE_OPTIONS", "NFO"),
            strike=float(pos.get("strikeprice", 0) or 0),
            option_type=opt_t, action=action,
            quantity=abs(qty),
            product=pos.get("product", "NRML"),
            expiry=pos.get("expiry", ""),
            entry_price=float(pos.get("averageprice", 0) or 0),
            status=LegStatus.OPEN,
        ))
    return legs


def _infer_spread_type(legs: list[OptionLeg]) -> str:
    sell_legs = [l for l in legs if l.action == LegAction.SELL]
    if sell_legs and sell_legs[0].option_type == OptionType.PE:
        return "BULL_PUT_SPREAD"
    return "BEAR_CALL_SPREAD"


# ---------------------------------------------------------------------------
# LTP helpers
# ---------------------------------------------------------------------------

def _get_leg_ltp(leg: OptionLeg, chain_entries: list[OptionChainEntry]) -> Optional[float]:
    for entry in chain_entries:
        if entry.strike == leg.strike:
            return entry.ce_ltp if leg.option_type == OptionType.CE else entry.pe_ltp
    for entry in chain_entries:
        if leg.option_type == OptionType.CE and entry.ce_symbol == leg.symbol:
            return entry.ce_ltp
        if leg.option_type == OptionType.PE and entry.pe_symbol == leg.symbol:
            return entry.pe_ltp
    return None


def _compute_mtm(legs: list[OptionLeg]) -> float:
    return sum(leg.unrealized_pnl for leg in legs if leg.status == LegStatus.OPEN)


# ---------------------------------------------------------------------------
# Supertrend trail engine
# ---------------------------------------------------------------------------

class StTrailEngine:
    """Tracks Supertrend for SL trailing using 1-min candles."""

    def __init__(self, config: Config) -> None:
        self._period               = config.get_int("SUPERTREND_PERIOD", 10)
        self._factor               = config.get_float("SUPERTREND_FACTOR", 3.0)
        self._st_state: Optional[dict] = None
        self._prev_direction: Optional[int] = None

    def seed(self, builder: CandleBuilder) -> None:
        highs, lows, closes = builder.highs(), builder.lows(), builder.closes()
        if len(closes) < self._period + 2:
            return
        self._st_state = supertrend_seed(highs, lows, closes, self._period, self._factor)
        if self._st_state:
            self._prev_direction = self._st_state["direction"]

    def update(self, builder: CandleBuilder) -> None:
        bar = builder.last_closed
        if bar is None or self._st_state is None:
            return
        self._prev_direction = self._st_state["direction"]
        self._st_state = supertrend_step(
            self._st_state, bar.high, bar.low, bar.close, self._period, self._factor
        )

    @property
    def value(self) -> Optional[float]:
        return self._st_state["supertrend"] if self._st_state else None

    @property
    def direction(self) -> Optional[int]:
        return self._st_state["direction"] if self._st_state else None

    @property
    def prev_direction(self) -> Optional[int]:
        return self._prev_direction

    def just_flipped(self) -> bool:
        if self._prev_direction is None or self.direction is None:
            return False
        return self._prev_direction != self.direction


# ---------------------------------------------------------------------------
# Exit executors  (Dependency Inversion — MonitoringLoop depends on this ABC)
# ---------------------------------------------------------------------------

class ExitExecutor(ABC):
    """
    Fills open legs with exit prices.
    Subclasses decide whether to call the broker (LIVE) or simulate at LTP (PAPER).
    MonitoringLoop never branches on PAPER/LIVE — it just calls fill_legs().
    """

    @abstractmethod
    def fill_legs(self, legs: list[OptionLeg], expiry: str) -> None:
        """Set exit_price, exit_time, status=CLOSED and realized_pnl on every open leg."""

    @property
    @abstractmethod
    def mode_tag(self) -> str:
        """Label for logs and DB notes: PAPER or LIVE."""


class PaperExitExecutor(ExitExecutor):
    """Simulates fills at the leg's current_ltp. No broker orders sent."""

    def fill_legs(self, legs: list[OptionLeg], expiry: str) -> None:
        now = now_ist()
        for leg in legs:
            if leg.status != LegStatus.OPEN:
                continue
            exit_px          = leg.current_ltp or 0.0
            entry_px         = leg.entry_price or 0.0
            leg.exit_price   = exit_px
            leg.exit_time    = now
            leg.status       = LegStatus.CLOSED
            leg.realized_pnl = (
                (entry_px - exit_px) * leg.quantity
                if leg.action == LegAction.SELL
                else (exit_px - entry_px) * leg.quantity
            )
        logger.info("PAPER exit simulated at current LTPs.")

    @property
    def mode_tag(self) -> str:
        return EXECUTION_MODE_PAPER


class LiveExitExecutor(ExitExecutor):
    """Sends close orders to the broker via ExecutionGateway (SHORT-first sequencing)."""

    def __init__(
        self,
        gateway: ExecutionGateway,
        client: OpenAlgoClient,
        config: Config,
    ) -> None:
        self._gateway = gateway
        self._client  = client
        self._config  = config

    def fill_legs(self, legs: list[OptionLeg], expiry: str) -> None:
        underlying = self._config.get_str("UNDERLYING", "NIFTY")
        exch_idx   = self._config.get_str("EXCHANGE_INDEX", "NSE_INDEX")
        try:
            chain_data    = self._client.get_option_chain(underlying, exch_idx, expiry, 20)
            chain_entries = parse_chain_entries(chain_data)
            spot          = float(chain_data.get("underlying_ltp", 0) or 0)
        except Exception:
            chain_entries, spot = [], 0.0

        ctx = StrategyContext(
            timestamp=now_ist(), spot=spot, atm_strike=spot,
            expiry=expiry, option_chain=chain_entries,
            positions=[], trades=[], orders=[],
            available_cash=0, m2m_unrealized=0, m2m_realized=0,
            config={},
            strategy_state=StrategyState(
                strategy_id="ICS_MONITORING",
                strategy_name=self._config.get_str("STRATEGY_NAME", "ICS"),
            ),
        )
        self._gateway.close_legs_short_first(legs, "ICS_MONITORING", ctx)

    @property
    def mode_tag(self) -> str:
        return EXECUTION_MODE_LIVE


# ---------------------------------------------------------------------------
# Monitoring loop
# ---------------------------------------------------------------------------

class MonitoringLoop:
    """
    Monitoring loop for an open credit spread position.

    Single responsibility: orchestrate one monitoring cycle.
    PAPER/LIVE branching is fully delegated to the injected ExitExecutor.
    """

    def __init__(
        self,
        config: Config,
        legs: list[OptionLeg],
        spread_type: str,
        client: OpenAlgoClient,
        exit_executor: ExitExecutor,
        notifier,
        db_queue,
        run_id: int = 0,
        leg_id_map: Optional[dict] = None,
    ) -> None:
        self._cfg          = config
        self._legs         = legs
        self._spread_type  = spread_type
        self._client       = client
        self._executor     = exit_executor
        self._notifier     = notifier
        self._db_queue     = db_queue
        self._run_id       = run_id
        self._leg_id_map   = leg_id_map or {}
        self._interval     = config.get_int("MONITORING_INTERVAL", 60)
        self._running      = True
        self._cycle        = 0
        self._bias         = "BULLISH" if spread_type == "BULL_PUT_SPREAD" else "BEARISH"
        self._expiry       = legs[0].expiry if legs else ""

        self._trailing_sl   = self._build_trailing_sl()
        self._profit_ladder = ProfitLadder.from_config(config.get("PROFIT_LADDER") or [])

        underlying       = config.get_str("UNDERLYING", "NIFTY")
        self._builder    = CandleBuilder(symbol=underlying)
        self._st_engine  = StTrailEngine(config)

        logger.info(
            "MonitoringLoop: spread=%s bias=%s expiry=%s mode=%s trail=%s",
            spread_type, self._bias, self._expiry,
            exit_executor.mode_tag, config.get_str("TRAIL_TYPE", "NONE"),
        )

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def warmup(self) -> None:
        """Load historical bars and seed indicator engines."""
        underlying  = self._cfg.get_str("UNDERLYING", "NIFTY")
        exch_idx    = self._cfg.get_str("EXCHANGE_INDEX", "NSE_INDEX")
        warmup_bars = self._cfg.get_int("WARMUP_BARS", 50)
        tf          = self._cfg.get_str("TRAIL_TIMEFRAME", "1m")
        try:
            hist = self._client.get_history(
                underlying, exch_idx, interval=tf, source="api", warmup_bars=warmup_bars,
            )
            if hist:
                self._builder.seed_from_history(hist[-warmup_bars:])
                self._st_engine.seed(self._builder)
                logger.info("Warm-up: %d bars loaded.", self._builder.closed_count)
        except Exception as exc:
            logger.warning("Warm-up failed (%s) — ST trail will build from live data.", exc)

    def run(self) -> None:
        """Block until position exits or script is interrupted."""
        import signal as _signal
        _signal.signal(_signal.SIGINT,  self._on_shutdown)
        _signal.signal(_signal.SIGTERM, self._on_shutdown)

        self._notifier.send(
            f"Monitoring started: {self._spread_type} expiry={self._expiry} "
            f"bias={self._bias} mode={self._executor.mode_tag}",
            level=LEVEL_INFO,
        )

        while self._running:
            cycle_start = time.time()
            self._cycle += 1
            try:
                exit_reason = self._run_cycle()
                if exit_reason:
                    self._execute_full_exit(exit_reason)
                    break
            except Exception as exc:
                logger.exception("Unhandled error in cycle %d: %s", self._cycle, exc)
                self._notifier.send(f"Monitoring error cycle {self._cycle}: {exc}", level=LEVEL_WARNING)

            elapsed = time.time() - cycle_start
            if self._running:
                time.sleep(max(0.0, self._interval - elapsed))

        logger.info("MonitoringLoop stopped after %d cycles.", self._cycle)

    # -----------------------------------------------------------------------
    # Cycle internals
    # -----------------------------------------------------------------------

    def _run_cycle(self) -> Optional[str]:
        now = now_ist()
        if not is_market_open(now):
            logger.debug("Market closed — skipping cycle.")
            return None

        spot, chain_entries = self._fetch_market_data()
        if spot == 0.0:
            return None

        self._update_leg_ltps(chain_entries)
        mtm = _compute_mtm(self._legs)
        self._update_trailing_sl(mtm, spot)
        self._persist_mtm_snapshot(spot, mtm)
        self._log_heartbeat(now, spot, mtm)

        exit_reason = self._check_exits(mtm, spot, now)
        if exit_reason:
            return exit_reason

        self._check_profit_ladder(mtm, spot, chain_entries)
        return None

    def _fetch_market_data(self) -> tuple[float, list]:
        underlying = self._cfg.get_str("UNDERLYING", "NIFTY")
        exch_idx   = self._cfg.get_str("EXCHANGE_INDEX", "NSE_INDEX")
        try:
            chain_data    = self._client.get_option_chain(underlying, exch_idx, self._expiry, 20)
            spot          = float(chain_data.get("underlying_ltp", 0) or 0)
            chain_entries = parse_chain_entries(chain_data)
            return spot, chain_entries
        except OpenAlgoError as exc:
            logger.warning("Option chain fetch failed: %s — skipping cycle.", exc)
            return 0.0, []

    def _update_leg_ltps(self, chain_entries: list) -> None:
        for leg in self._legs:
            if leg.status == LegStatus.OPEN:
                ltp = _get_leg_ltp(leg, chain_entries)
                if ltp is not None:
                    leg.current_ltp = ltp

    def _update_trailing_sl(self, mtm: float, spot: float) -> None:
        trail = self._cfg.get_str("TRAIL_TYPE", "NONE").upper()
        if trail == "MTM_STEP":
            self._trailing_sl.update_mtm(mtm)
        elif trail == "SPOT_STEP":
            self._trailing_sl.update_spot(spot, self._bias)
        elif trail == "SUPERTREND" and self._st_engine.value is not None:
            self._trailing_sl.update_supertrend(self._st_engine.value)

    def _persist_mtm_snapshot(self, spot: float, mtm: float) -> None:
        trail = self._cfg.get_str("TRAIL_TYPE", "NONE").upper()
        p_leg = next((l for l in self._legs if l.role == LegRole.PRIMARY), None)
        h_leg = next((l for l in self._legs if l.role == LegRole.HEDGE),   None)
        self._db_queue.put(
            save_mtm_snapshot,
            run_id=self._run_id, spot=spot, mtm=mtm,
            locked_mtm=self._trailing_sl.current_sl if trail != "NONE" else None,
            primary_ltp=p_leg.current_ltp if p_leg else None,
            hedge_ltp=h_leg.current_ltp   if h_leg else None,
        )

    def _check_exits(self, mtm: float, spot: float, now: datetime) -> Optional[str]:
        """Check all exit conditions in priority order. Returns exit reason or None."""
        # 1. Stop-loss
        trail = self._cfg.get_str("TRAIL_TYPE", "NONE").upper()
        sl_check_value = spot if trail == "SUPERTREND" else mtm
        sl_triggered   = (
            self._trailing_sl.is_triggered(sl_check_value)
            if self._trailing_sl.trail_type != TrailType.NONE
            else mtm < -abs(self._cfg.get_float("INITIAL_SL_POINTS", 3000.0))
        )
        if sl_triggered:
            logger.warning("SL TRIGGERED: MTM=%.0f SL=%.0f", mtm, self._trailing_sl.current_sl)
            return EXIT_REASON_SL_HIT

        # 2. Supertrend flip
        if self._cfg.get_bool("EXIT_ON_SUPERTREND_FLIP", True) and self._st_engine.just_flipped():
            if (self._bias == "BULLISH"
                    and supertrend_flipped_bearish(self._st_engine.prev_direction, self._st_engine.direction)):
                logger.warning("ST flipped BEARISH — exiting BULLISH spread.")
                return EXIT_REASON_ST_FLIP
            if (self._bias == "BEARISH"
                    and supertrend_flipped_bullish(self._st_engine.prev_direction, self._st_engine.direction)):
                logger.warning("ST flipped BULLISH — exiting BEARISH spread.")
                return EXIT_REASON_ST_FLIP

        # 3. Expiry / T-1 forced exit
        dte = days_to_expiry(self._expiry) if self._expiry else -1
        if dte == 0 and is_exit_window(now, self._cfg.get_str("EXPIRY_DAY_EXIT_TIME", "15:00")):
            logger.info("EXPIRY DAY exit triggered.")
            return EXIT_REASON_EXPIRY
        if (dte == 1 and self._cfg.get_bool("EXIT_ON_T1", False)
                and is_exit_window(now, self._cfg.get_str("T1_EXIT_TIME", "15:15"))):
            logger.info("T-1 exit triggered.")
            return EXIT_REASON_EXPIRY

        # 4. Intraday square-off
        if self._cfg.get_str("TRADE_VALIDITY", "INTRADAY").upper() == "INTRADAY":
            if is_exit_window(now, self._cfg.get_str("INTRADAY_EXIT_TIME", "15:10")):
                logger.info("INTRADAY square-off triggered.")
                return EXIT_REASON_EXPIRY

        return None

    def _check_profit_ladder(self, mtm: float, spot: float, chain_entries: list) -> None:
        open_legs    = [l for l in self._legs if l.status == LegStatus.OPEN]
        lots_open    = len(open_legs) // 2 if open_legs else 0
        lots_to_exit = self._profit_ladder.check(mtm, lots_open)
        if lots_to_exit > 0:
            self._execute_partial_exit(lots_to_exit, mtm, spot)

    # -----------------------------------------------------------------------
    # Exit execution  (Single Responsibility per method)
    # -----------------------------------------------------------------------

    def _execute_full_exit(self, reason: str) -> None:
        """Orchestrates full exit: fill → persist → notify."""
        logger.info("EXIT: reason=%s mode=%s", reason, self._executor.mode_tag)
        self._notifier.send(
            f"EXIT: {self._spread_type} reason={reason} "
            f"MTM={format_inr(_compute_mtm(self._legs))}",
            level=LEVEL_ALERT,
        )
        try:
            self._executor.fill_legs(self._legs, self._expiry)
        except ExecutionError as exc:
            logger.critical("Exit execution failed: %s", exc)
            self._notifier.send(f"EXIT FAILED: {exc}", level=LEVEL_URGENT)
            self._running = False
            return

        self._persist_exit(reason)
        self._notify_exit_complete(reason)
        self._running = False

    def _persist_exit(self, reason: str) -> None:
        """Write exit records to DB for all closed legs."""
        total_realized = 0.0
        for leg in self._legs:
            if leg.status != LegStatus.CLOSED:
                continue
            leg_db_id  = self._leg_id_map.get(leg.symbol, 0)
            exit_dt    = leg.exit_time.replace(tzinfo=None) if leg.exit_time else None
            if leg_db_id:
                self._db_queue.put(
                    update_leg, leg_id=leg_db_id,
                    exit_price=leg.exit_price, exit_time=exit_dt,
                    status=LegStatus.CLOSED.value,
                    exit_order_id=leg.order_id,
                    realized_pnl=leg.realized_pnl,
                )
            self._db_queue.put(
                save_trade,
                run_id=self._run_id, leg_id=leg_db_id or None,
                event_type="EXIT", symbol=leg.symbol,
                action=leg.action.value, quantity=leg.quantity,
                order_id=leg.order_id, fill_price=leg.exit_price,
                fill_time=exit_dt, status="complete",
                notes=f"trade_mode={self._executor.mode_tag}",
            )
            total_realized += leg.realized_pnl or 0.0

        self._db_queue.put(
            update_strategy_run, run_id=self._run_id,
            exit_time=now_ist().replace(tzinfo=None),
            exit_reason=reason, status="CLOSED",
            realized_pnl=total_realized,
        )

    def _notify_exit_complete(self, reason: str) -> None:
        """Log and send exit summary notification."""
        tag        = self._executor.mode_tag
        total_pnl  = sum(l.realized_pnl or 0 for l in self._legs)
        exit_lines = []
        for leg in self._legs:
            if leg.status == LegStatus.CLOSED and leg.exit_price is not None:
                ex = leg.expiry.replace("-", "")
                ts = (leg.exit_time.strftime("%H:%M:%S")
                      if leg.exit_time else now_ist().strftime("%H:%M:%S"))
                line = (
                    f"{leg.action.value}-{int(leg.strike)}-{leg.option_type.value}"
                    f"-{ex}-qty={leg.quantity}-@{leg.exit_price:.2f} [{tag}] at {ts}"
                )
                logger.info("EXIT TRADE: %s", line)
                exit_lines.append(f"  {line}")

        self._notifier.send(
            f"EXIT COMPLETE [{tag}]: {self._spread_type} reason={reason}\n"
            + "\n".join(exit_lines)
            + f"\nRealized: {format_inr(total_pnl)}",
            level=LEVEL_ALERT,
        )

    def _execute_partial_exit(self, lots_to_exit: int, mtm: float, spot: float) -> None:
        """Partial exit via profit ladder — same DB/notify flow for PAPER and LIVE."""
        logger.info(
            "PARTIAL EXIT: %d lot(s) MTM=%.0f mode=%s",
            lots_to_exit, mtm, self._executor.mode_tag,
        )
        self._profit_ladder.mark_last_triggered()
        self._notifier.send(
            f"PARTIAL EXIT [{self._executor.mode_tag}]: {lots_to_exit} lot(s) "
            f"MTM={format_inr(mtm)}",
            level=LEVEL_ALERT,
        )
        self._db_queue.put(
            save_adjustment,
            run_id=self._run_id, ts=now_ist().replace(tzinfo=None),
            adjustment_type="PARTIAL_EXIT", trigger_reason=EXIT_REASON_LADDER,
            spot_at_trigger=spot, mtm_at_trigger=mtm,
            notes=f"Profit ladder: exiting {lots_to_exit} lot(s) [{self._executor.mode_tag}]",
        )

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    def _build_trailing_sl(self) -> TrailingSlManager:
        trail_map = {
            "MTM_STEP":   TrailType.MTM_STEP,
            "SPOT_STEP":  TrailType.SPOT_STEP,
            "SUPERTREND": TrailType.SUPERTREND,
            "NONE":       TrailType.NONE,
        }
        trail_type = trail_map.get(self._cfg.get_str("TRAIL_TYPE", "NONE").upper(), TrailType.NONE)
        initial_sl = self._resolve_initial_sl()
        return TrailingSlManager(
            trail_type=trail_type, initial_sl=initial_sl,
            step=self._cfg.get_float("TRAIL_STEP", 500.0),
            activation=self._cfg.get_float("TRAIL_ACTIVATION", 1000.0),
        )

    def _resolve_initial_sl(self) -> float:
        sl_multiple = self._cfg.get_float("SL_AS_SPREAD_MULTIPLE", 0.0)
        if sl_multiple > 0:
            p_leg = next((l for l in self._legs if l.role == LegRole.PRIMARY), self._legs[0])
            h_leg = next((l for l in self._legs if l.role == LegRole.HEDGE),   self._legs[-1])
            net_prem     = spread_net_premium(p_leg, h_leg) or 0
            spread_width = self._cfg.get_float("SPREAD_WIDTH", 200.0)
            lot_size     = self._cfg.get_int("LOT_SIZE", 25)
            lots         = (self._legs[0].quantity // lot_size) if lot_size else 1
            return (spread_width - net_prem) * lot_size * lots * sl_multiple
        return self._cfg.get_float("INITIAL_SL_POINTS", 3000.0)

    def _log_heartbeat(self, now: datetime, spot: float, mtm: float) -> None:
        p_leg = next((l for l in self._legs if l.role == LegRole.PRIMARY), None)
        h_leg = next((l for l in self._legs if l.role == LegRole.HEDGE),   None)
        dte   = days_to_expiry(self._expiry) if self._expiry else "?"
        lines = [
            HEARTBEAT_SEPARATOR,
            f"Time    : {now.strftime('%H:%M:%S')}  Cycle: {self._cycle}  Mode: {self._executor.mode_tag}",
            f"Spread  : {self._spread_type}  Bias: {self._bias}  Expiry: {self._expiry}  DTE: {dte}",
            f"Spot    : {spot:.0f}  MTM: {format_inr(mtm)}  SL: {format_inr(-self._trailing_sl.current_sl)}",
            f"Prim LTP: {p_leg.current_ltp:.2f if p_leg and p_leg.current_ltp else 'N/A'}  "
            f"Hedge LTP: {h_leg.current_ltp:.2f if h_leg and h_leg.current_ltp else 'N/A'}",
            HEARTBEAT_SEPARATOR,
        ]
        for line in lines:
            logger.info(line)

    def _on_shutdown(self, signum: int, frame) -> None:
        logger.info("Shutdown signal %d — stopping after current cycle.", signum)
        self._running = False


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

    _log_file  = config.get_str("LOG_FILE", "logs/cs_monitoring.log")
    _log_level = config.get_str("LOG_LEVEL", "INFO")
    setup_logger("strategy_framework", log_file=_log_file, level=_log_level)
    setup_logger("strategies",         log_file=_log_file, level=_log_level)
    exec_mode = config.get_str("EXECUTION_MODE", EXEC_MODE_MONITOR_ONLY)
    logger.info("=" * 60)
    logger.info("Directional Index Credit Spread — MONITORING SCRIPT")
    logger.info("Mode: %s  Underlying: %s",
                exec_mode, config.get_str("UNDERLYING", "NIFTY"))
    logger.info("=" * 60)

    notifier         = build_notifier(config)
    sf, db_queue     = build_db(config)
    client           = build_client(config)

    # Recover open position
    logger.info("Recovery scan …")
    legs, spread_type, run_id, leg_id_map = _recover_position(client, config, sf)
    if not legs:
        logger.critical("No open position found — exiting.")
        notifier.send("No open position found — monitoring exiting.", level=LEVEL_WARNING)
        db_queue.stop(); client.close(); sys.exit(0)

    logger.info(
        "Position recovered: %s  legs=%d  run_id=%d",
        spread_type, len(legs), run_id,
    )

    # Build gateway and resolve trade mode
    gateway, gw_mode = build_gateway(
        config, client,
        live_exec_modes=(EXEC_MODE_EXIT_EXEC_MONITOR, EXEC_MODE_FULL_EXEC),
    )

    # Inject the right executor — only place in the codebase that knows PAPER vs LIVE
    exit_executor: ExitExecutor = (
        PaperExitExecutor()
        if gw_mode == EXECUTION_MODE_PAPER
        else LiveExitExecutor(gateway, client, config)
    )

    loop = MonitoringLoop(
        config=config,
        legs=legs,
        spread_type=spread_type,
        client=client,
        exit_executor=exit_executor,
        notifier=notifier,
        db_queue=db_queue,
        run_id=run_id,
        leg_id_map=leg_id_map,
    )
    loop.warmup()

    try:
        loop.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt.")
    finally:
        db_queue.stop()
        client.close()
        logger.info("Monitoring script shutdown complete.")


if __name__ == "__main__":
    main()
