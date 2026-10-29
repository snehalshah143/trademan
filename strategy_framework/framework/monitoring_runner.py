"""
monitoring_runner.py
--------------------
Reusable monitoring runner — handles ALL boilerplate for any credit-spread
strategy monitoring loop.

Writing a new strategy requires only one thing beyond the entry runner:
  1. Implement ``check_exit(state: CycleState) → Optional[str]`` in the strategy.
  2. Call ``MonitoringRunner(strategy, config).run()`` from a thin wrapper script.

Boilerplate handled here
------------------------
  Config, logging, notifier, DB, client, gateway setup
  Position recovery  (from DB, then positionbook fallback)
  Candle builder + ST trail engine warm-up
  Full monitoring loop (every MONITORING_INTERVAL seconds):
    - Live LTP fetch via option chain
    - MTM computation
    - Trailing SL update  (MTM_STEP / SPOT_STEP / SUPERTREND)
    - MTM snapshot persistence  (non-blocking queue)
    - Heartbeat log
    - SL breach check  → full exit
    - Expiry-day / T-1 / intraday forced exit
    - Profit-booking ladder  → partial exit
    - ``strategy.check_exit(CycleState)``  → custom strategy-specific exit
  Full exit execution  (SHORT-first order, DB persistence, notifier)
  Partial exit execution  (lot reduction)
  Graceful SIGINT / SIGTERM handling

Strategy hook called (pluggable — lives in the concrete strategy class)
-----------------------------------------------------------------------
  strategy.check_exit(CycleState) → Optional[str]
      Return an exit-reason string to trigger a full exit, or None to keep
      monitoring.  The runner has already handled SL breach, expiry-day, and
      intraday square-off — this method is for strategy-specific exits such as:
        - Supertrend flip against the position bias
        - Custom profit target
        - Custom time-based rules
        - Indicator-driven reversal conditions

Config keys consumed
--------------------
  (same as execution config, plus:)
  MONITORING_INTERVAL
  INITIAL_SL_POINTS, SL_AS_SPREAD_MULTIPLE, SPREAD_WIDTH
  TRAIL_TYPE, TRAIL_STEP, TRAIL_ACTIVATION, TRAIL_TIMEFRAME
  SUPERTREND_PERIOD, SUPERTREND_FACTOR
  PROFIT_LADDER  (list of {mtm, exit_pct})
  EXPIRY_DAY_EXIT_TIME, EXIT_ON_T1, T1_EXIT_TIME
  TRADE_VALIDITY, INTRADAY_EXIT_TIME
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from typing import Optional, TYPE_CHECKING

from .base_strategy import BaseStrategy, CycleState
from .candle_builder import CandleBuilder
from .config_loader import Config
from .context_builder import parse_chain_entries
from .credit_spread_db import (
    SessionFactory, get_open_runs, get_run_legs,
    save_adjustment, save_mtm_snapshot, save_notification, save_trade,
    update_leg, update_strategy_run,
)
from .execution_gateway import ExecutionError
from .expiry_utils import days_to_expiry
from .indicators import supertrend_seed, supertrend_step
from .market_utils import is_exit_window, is_market_open
from .notifier import LEVEL_ALERT, LEVEL_INFO, LEVEL_URGENT, LEVEL_WARNING
from .openalgo_client import OpenAlgoClient, OpenAlgoError
from .script_helpers import build_client, build_db, build_gateway, build_notifier
from .signal import supertrend_flipped_bearish, supertrend_flipped_bullish
from .spread_template import spread_net_premium
from .trailing import ProfitLadder, TrailType, TrailingSlManager
from ..models.option_leg import LegAction, LegRole, LegStatus, OptionLeg, OptionType
from ..models.strategy_context import OptionChainEntry, StrategyContext
from ..models.strategy_state import StrategyState
from ..utils.constants import (
    EXEC_MODE_EXIT_EXEC_MONITOR, EXEC_MODE_FULL_EXEC, EXEC_MODE_MONITOR_ONLY,
    EXIT_REASON_EXPIRY, EXIT_REASON_LADDER, EXIT_REASON_SL_HIT,
    HEARTBEAT_SEPARATOR,
)
from ..utils.helper import format_inr, now_ist
from ..utils.logger import get_logger, setup_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Supertrend trail engine (builds from live 1-min candles)
# ---------------------------------------------------------------------------

class _StTrailEngine:
    """Incremental Supertrend tracker for SL trailing and flip detection."""

    def __init__(self, config: Config) -> None:
        self._period         = config.get_int("SUPERTREND_PERIOD", 10)
        self._factor         = config.get_float("SUPERTREND_FACTOR", 3.0)
        self._st_state: Optional[dict] = None
        self._prev_direction: Optional[int] = None

    def seed(self, builder: CandleBuilder) -> None:
        highs  = builder.highs()
        lows   = builder.lows()
        closes = builder.closes()
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
            self._st_state, bar.high, bar.low, bar.close,
            self._period, self._factor,
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
# LTP / MTM helpers
# ---------------------------------------------------------------------------

def _get_leg_ltp(leg: OptionLeg, chain_entries: list) -> Optional[float]:
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
# Trailing SL factory
# ---------------------------------------------------------------------------

def _build_trailing_sl(config: Config, initial_sl: float) -> TrailingSlManager:
    trail_str = config.get_str("TRAIL_TYPE", "NONE").upper()
    trail_map = {
        "MTM_STEP":   TrailType.MTM_STEP,
        "SPOT_STEP":  TrailType.SPOT_STEP,
        "SUPERTREND": TrailType.SUPERTREND,
        "NONE":       TrailType.NONE,
    }
    return TrailingSlManager(
        trail_type=trail_map.get(trail_str, TrailType.NONE),
        initial_sl=initial_sl,
        step=config.get_float("TRAIL_STEP", 500.0),
        activation=config.get_float("TRAIL_ACTIVATION", 1000.0),
    )


# ---------------------------------------------------------------------------
# Position recovery helpers
# ---------------------------------------------------------------------------

def _db_legs_to_option_legs(db_legs) -> list[OptionLeg]:
    legs = []
    for dl in db_legs:
        legs.append(OptionLeg(
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
        ))
    return legs


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
            option_type=opt_t, action=action, quantity=abs(qty),
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


def _recover_position(
    client: OpenAlgoClient,
    config: Config,
    sf: SessionFactory,
) -> tuple[Optional[list], Optional[str], int, dict]:
    """
    Recover an open credit spread from DB (preferred) or positionbook (fallback).

    Returns
    -------
    (legs, spread_type, run_id, leg_id_map)
    run_id=0 and leg_id_map={} when recovered from positionbook only.
    """
    strategy_id_prefix = config.get_str("STRATEGY_ID_PREFIX", "ICS")
    today_id           = f"{strategy_id_prefix}_{now_ist().strftime('%Y%m%d')}"

    session = sf.make()
    try:
        open_runs = get_open_runs(session, today_id)
        if open_runs:
            run        = open_runs[0]
            db_legs    = get_run_legs(session, run.id)
            logger.info("Recovery: DB open run  run_id=%d  spread=%s", run.id, run.spread_type)
            legs       = _db_legs_to_option_legs(db_legs)
            leg_id_map = {dl.symbol: dl.id for dl in db_legs}
            return legs, run.spread_type, run.id, leg_id_map
    except Exception as exc:
        logger.warning("DB recovery failed: %s", exc)
    finally:
        session.close()

    # Fallback: positionbook
    underlying = config.get_str("UNDERLYING", "NIFTY")
    try:
        positions = client.get_positions()
        matched   = [
            p for p in positions
            if underlying in p.get("symbol", "") and p.get("quantity", 0) != 0
        ]
        if matched:
            logger.info(
                "Recovery: positionbook — %d leg(s) for %s", len(matched), underlying
            )
            legs       = _positionbook_to_option_legs(matched, config)
            spread_type = _infer_spread_type(legs)
            return legs, spread_type, 0, {}
    except Exception as exc:
        logger.warning("Positionbook recovery failed: %s", exc)

    return None, None, 0, {}


# ---------------------------------------------------------------------------
# Internal monitoring loop
# ---------------------------------------------------------------------------

class _MonitoringLoop:
    """
    Core monitoring loop.  Accepts a ``BaseStrategy`` instance whose
    ``check_exit(CycleState)`` method is called every cycle for custom
    strategy-specific exit conditions.

    The runner handles all standard exits:
      SL breach, expiry-day, T-1, intraday square-off, profit ladder.

    The strategy handles everything else:
      Supertrend flip, custom targets, reversal indicators, etc.
    """

    def __init__(
        self,
        strategy: BaseStrategy,
        config: Config,
        legs: list[OptionLeg],
        spread_type: str,
        client: OpenAlgoClient,
        gateway,
        notifier,
        db_queue,
        run_id: int = 0,
        leg_id_map: Optional[dict] = None,
        trade_mode: str = "PAPER",
    ) -> None:
        self._strategy    = strategy
        self._cfg         = config
        self._legs        = legs
        self._spread_type = spread_type
        self._client      = client
        self._gateway     = gateway
        self._notifier    = notifier
        self._db_queue    = db_queue
        self._run_id      = run_id
        self._leg_id_map  = leg_id_map or {}
        self._trade_mode  = trade_mode
        self._interval    = config.get_int("MONITORING_INTERVAL", 60)
        self._running     = True
        self._cycle       = 0
        self._max_mtm     = 0.0

        # Derive bias from spread type
        self._bias = "BULLISH" if spread_type == "BULL_PUT_SPREAD" else "BEARISH"

        # Expiry from first leg
        self._expiry = legs[0].expiry if legs else ""

        # Compute initial SL
        sl_multiple = config.get_float("SL_AS_SPREAD_MULTIPLE", 0.0)
        if sl_multiple > 0:
            p_leg = next((l for l in legs if l.role == LegRole.PRIMARY), legs[0])
            h_leg = next((l for l in legs if l.role == LegRole.HEDGE),   legs[-1])
            net_prem      = spread_net_premium(p_leg, h_leg) or 0
            spread_width  = config.get_float("SPREAD_WIDTH", 200.0)
            lot_size      = config.get_int("LOT_SIZE", 25)
            lots_count    = (legs[0].quantity // lot_size) if lot_size else 1
            max_loss      = (spread_width - net_prem) * lot_size
            initial_sl    = max_loss * lots_count * sl_multiple
        else:
            initial_sl = config.get_float("INITIAL_SL_POINTS", 3000.0)

        self._trailing_sl   = _build_trailing_sl(config, initial_sl)
        self._profit_ladder = ProfitLadder.from_config(config.get("PROFIT_LADDER") or [])

        # Candle builder + ST trail engine
        underlying        = config.get_str("UNDERLYING", "NIFTY")
        self._builder     = CandleBuilder(symbol=underlying)
        self._st_engine   = _StTrailEngine(config)
        self._warmup_done = False

        logger.info(
            "MonitoringLoop: spread=%s  bias=%s  expiry=%s  initial_sl=%.0f  trail=%s",
            spread_type, self._bias, self._expiry,
            initial_sl, config.get_str("TRAIL_TYPE", "NONE"),
        )

    # -----------------------------------------------------------------------
    # Warm-up
    # -----------------------------------------------------------------------

    def warmup(self) -> None:
        """Load historical bars and seed the ST trail engine."""
        exch_idx       = self._cfg.get_str("EXCHANGE_INDEX", "NSE_INDEX")
        underlying     = self._cfg.get_str("UNDERLYING", "NIFTY")
        warmup_bars    = self._cfg.get_int("WARMUP_BARS", 50)
        trail_interval = self._cfg.get_str("TRAIL_TIMEFRAME", "1m")
        try:
            hist = self._client.get_history(
                underlying, exch_idx,
                interval=trail_interval, source="api", warmup_bars=warmup_bars,
            )
            if hist:
                self._builder.seed_from_history(hist[-warmup_bars:])
                self._st_engine.seed(self._builder)
                logger.info("Warm-up: %d bars loaded.", self._builder.closed_count)
        except Exception as exc:
            logger.warning("Warm-up failed (%s) — ST trail builds from live data.", exc)
        self._warmup_done = True

    # -----------------------------------------------------------------------
    # Main loop
    # -----------------------------------------------------------------------

    def run(self) -> None:
        """Block until the position is exited or the script is interrupted."""
        self._notifier.send(
            f"Monitoring started: {self._spread_type}  expiry={self._expiry}  bias={self._bias}",
            level=LEVEL_INFO,
        )

        import signal as _signal
        _signal.signal(_signal.SIGINT,  self._on_shutdown)
        _signal.signal(_signal.SIGTERM, self._on_shutdown)

        while self._running:
            cycle_start = time.time()
            self._cycle += 1
            try:
                exit_reason = self._run_cycle()
                if exit_reason:
                    self._execute_full_exit(exit_reason)
                    break
            except Exception as exc:
                logger.exception(
                    "Unhandled exception in monitoring cycle %d: %s", self._cycle, exc
                )
                self._notifier.send(
                    f"Monitoring error cycle {self._cycle}: {exc}", level=LEVEL_WARNING
                )

            elapsed   = time.time() - cycle_start
            sleep_for = max(0.0, self._interval - elapsed)
            if self._running:
                time.sleep(sleep_for)

        logger.info("MonitoringLoop stopped after %d cycles.", self._cycle)

    def _on_shutdown(self, signum: int, frame) -> None:
        logger.info("Shutdown signal %d received — stopping after current cycle.", signum)
        self._running = False

    # -----------------------------------------------------------------------
    # Single cycle
    # -----------------------------------------------------------------------

    def _run_cycle(self) -> Optional[str]:
        """
        One monitoring cycle.  Returns an exit-reason string or None.

        Exit priority order (checked in sequence):
          1. SL breach (fixed or trailing)
          2. strategy.check_exit()  — custom exits (ST flip, targets, etc.)
          3. Expiry-day / T-1 forced exit
          4. Intraday square-off
          5. Profit ladder (partial, not a full exit reason)
        """
        now = now_ist()

        if not is_market_open(now):
            logger.debug("Market closed — skipping cycle.")
            return None

        # ── Fetch live LTPs via option chain ──────────────────────────────
        underlying = self._cfg.get_str("UNDERLYING", "NIFTY")
        exch_idx   = self._cfg.get_str("EXCHANGE_INDEX", "NSE_INDEX")
        try:
            chain_data = self._client.get_option_chain(
                underlying, exch_idx, self._expiry, strike_count=20
            )
        except OpenAlgoError as exc:
            logger.warning("Option chain fetch failed: %s — skipping cycle.", exc)
            return None

        spot          = float(chain_data.get("underlying_ltp", 0) or 0)
        chain_entries = parse_chain_entries(chain_data)

        # Update current LTPs on all open legs
        open_legs = [l for l in self._legs if l.status == LegStatus.OPEN]
        for leg in open_legs:
            ltp = _get_leg_ltp(leg, chain_entries)
            if ltp is not None:
                leg.current_ltp = ltp

        # ── MTM ───────────────────────────────────────────────────────────
        mtm = _compute_mtm(self._legs)
        if mtm > self._max_mtm:
            self._max_mtm = mtm

        # ── Trailing SL update ────────────────────────────────────────────
        trail_type = self._cfg.get_str("TRAIL_TYPE", "NONE").upper()
        if trail_type == "MTM_STEP":
            self._trailing_sl.update_mtm(mtm)
        elif trail_type == "SPOT_STEP":
            self._trailing_sl.update_spot(spot, self._bias)
        elif trail_type == "SUPERTREND" and self._st_engine.value is not None:
            self._trailing_sl.update_supertrend(self._st_engine.value)

        # ── Persist MTM snapshot ──────────────────────────────────────────
        p_leg = next((l for l in self._legs if l.role == LegRole.PRIMARY), None)
        h_leg = next((l for l in self._legs if l.role == LegRole.HEDGE),   None)
        self._db_queue.put(save_mtm_snapshot,
            run_id=self._run_id, spot=spot, mtm=mtm,
            locked_mtm=self._trailing_sl.current_sl if trail_type != "NONE" else None,
            primary_ltp=p_leg.current_ltp if p_leg else None,
            hedge_ltp=h_leg.current_ltp   if h_leg else None,
        )

        # ── Heartbeat ─────────────────────────────────────────────────────
        self._log_heartbeat(now, spot, mtm)

        # ── 1. SL breach ──────────────────────────────────────────────────
        if self._trailing_sl.trail_type != TrailType.NONE:
            sl_triggered = self._trailing_sl.is_triggered(
                spot if trail_type == "SUPERTREND" else mtm
            )
        else:
            sl_triggered = mtm < -abs(self._cfg.get_float("INITIAL_SL_POINTS", 3000.0))

        if sl_triggered:
            logger.warning(
                "SL TRIGGERED: MTM=%.0f  spot=%.0f  SL=%.0f",
                mtm, spot, self._trailing_sl.current_sl,
            )
            return EXIT_REASON_SL_HIT

        # ── 2. Strategy custom exit ────────────────────────────────────────
        ind = self._strategy.indicators
        state = CycleState(
            mtm=mtm,
            max_mtm=self._max_mtm,
            spot=spot,
            bias=self._bias,
            legs=self._legs,
            trailing_sl=self._trailing_sl,
            st_engine=self._st_engine,
            dte=days_to_expiry(self._expiry) if self._expiry else -1,
            now=now,
            cycle=self._cycle,
            indicators=ind if ind.is_ready else None,
        )
        custom_reason = self._strategy.check_exit(state)
        if custom_reason:
            logger.warning("Strategy-defined exit triggered: %s", custom_reason)
            return custom_reason

        # ── 3. Expiry-day / T-1 forced exit ──────────────────────────────
        dte = state.dte
        if dte == 0 and is_exit_window(now, self._cfg.get_str("EXPIRY_DAY_EXIT_TIME", "15:00")):
            logger.info("EXPIRY DAY exit triggered.")
            return EXIT_REASON_EXPIRY
        if (dte == 1 and self._cfg.get_bool("EXIT_ON_T1", False)
                and is_exit_window(now, self._cfg.get_str("T1_EXIT_TIME", "15:15"))):
            logger.info("T-1 exit triggered.")
            return EXIT_REASON_EXPIRY

        # ── 4. Intraday forced square-off ─────────────────────────────────
        if self._cfg.get_str("TRADE_VALIDITY", "INTRADAY").upper() == "INTRADAY":
            if is_exit_window(now, self._cfg.get_str("INTRADAY_EXIT_TIME", "15:10")):
                logger.info("INTRADAY square-off triggered.")
                return EXIT_REASON_EXPIRY
        else:
            logger.debug("TRADE_VALIDITY=POSITIONAL — skipping intraday square-off.")

        # ── 5. Profit ladder (partial exits — not a full exit) ────────────
        lots_open    = len(open_legs) // 2 if open_legs else 0
        lots_to_exit = self._profit_ladder.check(mtm, lots_open)
        if lots_to_exit > 0:
            self._execute_partial_exit(lots_to_exit, mtm, spot, chain_entries)

        return None

    # -----------------------------------------------------------------------
    # Exit execution helpers
    # -----------------------------------------------------------------------

    def _execute_full_exit(self, reason: str) -> None:
        exec_mode = self._cfg.get_str("EXECUTION_MODE", EXEC_MODE_MONITOR_ONLY)
        logger.info("EXIT triggered: reason=%s  exec_mode=%s", reason, exec_mode)
        self._notifier.send(
            f"EXIT: {self._spread_type}  reason={reason}\n"
            f"MTM at exit: {format_inr(_compute_mtm(self._legs))}",
            level=LEVEL_ALERT,
        )

        if exec_mode == EXEC_MODE_MONITOR_ONLY:
            logger.info("MONITOR_ONLY — no exit orders placed.")
            self._running = False
            return

        dummy_state = StrategyState(
            strategy_id="MONITORING",
            strategy_name=self._cfg.get_str("STRATEGY_NAME", "ICS"),
        )
        underlying = self._cfg.get_str("UNDERLYING", "NIFTY")
        exch_idx   = self._cfg.get_str("EXCHANGE_INDEX", "NSE_INDEX")
        try:
            chain_data    = self._client.get_option_chain(underlying, exch_idx, self._expiry, 20)
            chain_entries = parse_chain_entries(chain_data)
            spot          = float(chain_data.get("underlying_ltp", 0) or 0)
        except Exception:
            chain_entries = []
            spot          = 0.0

        ctx = StrategyContext(
            timestamp=now_ist(), spot=spot, atm_strike=spot,
            expiry=self._expiry, option_chain=chain_entries,
            positions=[], trades=[], orders=[],
            available_cash=0, m2m_unrealized=0, m2m_realized=0,
            config={}, strategy_state=dummy_state,
        )

        try:
            self._gateway.close_legs_short_first(self._legs, "MONITORING", ctx)
        except ExecutionError as exc:
            logger.critical("Exit execution failed: %s", exc)
            self._notifier.send(f"EXIT FAILED: {exc}", level=LEVEL_URGENT)
            self._running = False
            return

        # DB persistence for each closed leg
        total_realized = 0.0
        _exit_lines    = []
        for leg in self._legs:
            if leg.status == LegStatus.CLOSED:
                leg_db_id = self._leg_id_map.get(leg.symbol, 0)
                if leg_db_id:
                    self._db_queue.put(update_leg, leg_id=leg_db_id,
                        exit_price=leg.exit_price,
                        exit_time=leg.exit_time.replace(tzinfo=None) if leg.exit_time else None,
                        status=LegStatus.CLOSED.value,
                        exit_order_id=leg.order_id,
                        realized_pnl=leg.realized_pnl,
                    )
                self._db_queue.put(save_trade,
                    run_id=self._run_id, leg_id=leg_db_id or None,
                    event_type="EXIT", symbol=leg.symbol,
                    action=leg.action.value, quantity=leg.quantity,
                    order_id=leg.order_id, fill_price=leg.exit_price,
                    fill_time=leg.exit_time.replace(tzinfo=None) if leg.exit_time else None,
                    status="complete",
                    notes=f"trade_mode={self._trade_mode}",
                )
                total_realized += leg.realized_pnl or 0.0

                if leg.exit_price is not None:
                    ex  = leg.expiry.replace("-", "")
                    ts  = (leg.exit_time.strftime("%H:%M:%S")
                           if leg.exit_time else now_ist().strftime("%H:%M:%S"))
                    line = (
                        f"{leg.action.value}-{int(leg.strike)}-{leg.option_type.value}"
                        f"-{ex}-qty={leg.quantity}-@{leg.exit_price:.2f}"
                        f" [{self._trade_mode}] at {ts}"
                    )
                    logger.info("EXIT TRADE: %s", line)
                    _exit_lines.append(f"  {line}")

        self._db_queue.put(update_strategy_run,
            run_id=self._run_id,
            exit_time=now_ist().replace(tzinfo=None),
            exit_reason=reason, status="CLOSED",
            realized_pnl=total_realized,
        )

        self._notifier.send(
            f"EXIT COMPLETE [{self._trade_mode}]: {self._spread_type}  reason={reason}\n"
            + "\n".join(_exit_lines)
            + f"\nRealized: {format_inr(total_realized)}",
            level=LEVEL_ALERT,
        )

        # Call strategy hook
        self._strategy.on_exit_complete(self._legs, reason, self._trade_mode)
        self._running = False

    def _execute_partial_exit(
        self, lots_to_exit: int, mtm: float, spot: float, chain_entries: list
    ) -> None:
        exec_mode = self._cfg.get_str("EXECUTION_MODE", EXEC_MODE_MONITOR_ONLY)
        logger.info(
            "PARTIAL EXIT: %d lot(s)  MTM=%.0f  spot=%.0f  mode=%s",
            lots_to_exit, mtm, spot, exec_mode,
        )
        self._profit_ladder.mark_last_triggered()

        if exec_mode == EXEC_MODE_MONITOR_ONLY:
            logger.info("MONITOR_ONLY — no partial exit orders placed.")
            return

        self._notifier.send(
            f"PARTIAL EXIT: {lots_to_exit} lot(s)  MTM={format_inr(mtm)}",
            level=LEVEL_ALERT,
        )
        self._db_queue.put(save_adjustment,
            run_id=self._run_id,
            ts=now_ist().replace(tzinfo=None),
            adjustment_type="PARTIAL_EXIT",
            trigger_reason=EXIT_REASON_LADDER,
            spot_at_trigger=spot, mtm_at_trigger=mtm,
            notes=f"Profit ladder: exiting {lots_to_exit} lot(s)",
        )

    # -----------------------------------------------------------------------
    # Heartbeat
    # -----------------------------------------------------------------------

    def _log_heartbeat(self, now: datetime, spot: float, mtm: float) -> None:
        p_leg = next((l for l in self._legs if l.role == LegRole.PRIMARY), None)
        h_leg = next((l for l in self._legs if l.role == LegRole.HEDGE),   None)
        dte   = days_to_expiry(self._expiry) if self._expiry else "?"
        lines = [
            HEARTBEAT_SEPARATOR,
            "Monitoring Alive",
            f"Time         : {now.strftime('%H:%M:%S')}",
            f"Cycle        : {self._cycle}",
            f"Spread       : {self._spread_type}",
            f"Bias         : {self._bias}",
            f"Expiry       : {self._expiry}  DTE={dte}",
            f"Spot         : {spot:.0f}",
            f"Current MTM  : {format_inr(mtm)}",
            f"Peak MTM     : {format_inr(self._max_mtm)}",
            f"Current SL   : {format_inr(-self._trailing_sl.current_sl)}",
            f"Trail Type   : {self._cfg.get_str('TRAIL_TYPE', 'NONE')}",
            f"PRIMARY LTP  : {p_leg.current_ltp:.2f if p_leg and p_leg.current_ltp else 'N/A'}",
            f"HEDGE LTP    : {h_leg.current_ltp:.2f if h_leg and h_leg.current_ltp else 'N/A'}",
            f"Next Ladder  : {format_inr(self._profit_ladder.next_rung.mtm_threshold) if self._profit_ladder.next_rung else 'All done'}",
            f"Heartbeat    : OK",
            HEARTBEAT_SEPARATOR,
        ]
        for line in lines:
            logger.info(line)

        logger.debug(
            "INDICATORS | ST=%.2f  ST_dir=%s  Spot=%.2f  MTM=%.2f  SL=%.2f",
            self._st_engine.value or 0, self._st_engine.direction,
            spot, mtm, self._trailing_sl.current_sl,
        )


# ---------------------------------------------------------------------------
# Public MonitoringRunner
# ---------------------------------------------------------------------------

class MonitoringRunner:
    """
    Runs the full monitoring lifecycle for any ``BaseStrategy`` subclass.

    Usage::

        config   = Config("config/monitoring_settings.yaml")
        strategy = MyStrategy(config)
        MonitoringRunner(strategy, config).run()
    """

    def __init__(self, strategy: BaseStrategy, config: Config) -> None:
        self._strategy = strategy
        self._cfg      = config

    def run(self) -> None:
        cfg       = self._cfg
        strategy  = self._strategy
        exec_mode = cfg.get_str("EXECUTION_MODE", EXEC_MODE_MONITOR_ONLY)

        # ── Logging ──────────────────────────────────────────────────────────
        setup_logger(
            "strategy_framework",
            log_file=cfg.get_str("LOG_FILE", "logs/cs_monitoring.log"),
            level=cfg.get_str("LOG_LEVEL", "INFO"),
        )
        logger.info("=" * 60)
        logger.info("MonitoringRunner — %s", cfg.get_str("STRATEGY_NAME", "STRATEGY"))
        logger.info("Mode      : %s", exec_mode)
        logger.info("Underlying: %s", cfg.get_str("UNDERLYING", "NIFTY"))
        logger.info("=" * 60)

        # ── Infrastructure ───────────────────────────────────────────────────
        notifier     = build_notifier(cfg)
        sf, db_queue = build_db(cfg)
        client       = build_client(cfg)

        # ── Position recovery ─────────────────────────────────────────────────
        logger.info("Running recovery scan for open position …")
        legs, spread_type, run_id, leg_id_map = _recover_position(client, cfg, sf)

        if not legs:
            logger.critical("No open position found. Nothing to monitor.")
            notifier.send("No open position found — monitoring script exiting.", level=LEVEL_WARNING)
            db_queue.stop(); client.close(); sys.exit(0)

        logger.info(
            "Position recovered: %s — %d legs (%s)  run_id=%d",
            spread_type, len(legs),
            ", ".join(l.symbol for l in legs),
            run_id,
        )

        # ── Gateway ───────────────────────────────────────────────────────────
        gateway, gw_mode = build_gateway(
            cfg, client,
            live_exec_modes=(EXEC_MODE_EXIT_EXEC_MONITOR, EXEC_MODE_FULL_EXEC),
        )

        # ── Build and run monitoring loop ─────────────────────────────────────
        loop = _MonitoringLoop(
            strategy=strategy,
            config=cfg,
            legs=legs,
            spread_type=spread_type,
            client=client,
            gateway=gateway,
            notifier=notifier,
            db_queue=db_queue,
            run_id=run_id,
            leg_id_map=leg_id_map,
            trade_mode=exec_mode,
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
