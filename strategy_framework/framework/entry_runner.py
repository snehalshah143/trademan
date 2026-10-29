"""
entry_runner.py
---------------
Generic entry runner — works for ANY strategy type.

The runner knows nothing about leg structure, spread type, or instrument
category.  It only does:
  - Infrastructure setup (config, logging, notifier, DB, client)
  - Instrument metadata (expiry, lot_size, strike_interval)
  - Idempotency guard (skip if position already open)
  - Position sizing (FIXED or MARGIN_BASED)
  - CandleBuilder construction + historical warm-up
  - ConfirmationCounter wiring
  - WebSocket subscription + on_ltp tick handler
  - Entry window polling loop (wait for signal)
  - Spot fetch on signal confirmation
  - Margin preflight
  - Execute whatever legs the strategy selects  (BUY legs first, then SELL)
  - DB persistence + summary logging + notifier calls

What the strategy defines (pluggable — never lives in the runner):
  evaluate_signal(indicators)  → what bias is, using which indicators
  select_legs(...)             → what instruments / strikes / legs to build
  on_signal_first_match(...)   → optional notification hook
  on_signal_confirmed(...)     → optional notification hook
  on_entry_complete(...)       → optional notification hook

The runner calls strategy.select_legs() and receives a StrategyLegsResult.
It executes those legs via the gateway without caring what they are —
a 2-leg credit spread, a 4-leg iron fly, a single naked buy, a pair of
straddles — the runner is indifferent.

Config keys consumed
--------------------
  OPENALGO_API_KEY, OPENALGO_HOST, API_TIMEOUT
  EXECUTION_MODE, STRATEGY_ID_PREFIX, STRATEGY_NAME
  LOG_FILE, LOG_LEVEL
  NOTIFY_TELEGRAM, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_MIN_LEVEL
  DATABASE_URL, PAPER_SNAPSHOT_PATH
  UNDERLYING, EXCHANGE_INDEX, EXCHANGE_OPTIONS
  EXPIRY_MODE, DTE_MIN, DTE_MAX, EXPIRY_ROLLOVER_TIME
  LOT_SIZE, STRIKE_INTERVAL
  SIZING_MODE, FIXED_LOTS, MAX_LOTS, DEPLOYED_CAPITAL,
    MARGIN_BUFFER_PCT, CAPITAL_UTILISATION_PCT, PRODUCT
  INDICATOR_TIMEFRAME  (e.g. "15m")
  WARMUP_BARS
  CONFIRMATION_BARS, SIGNAL_COOLDOWN_BARS
  ENTRY_START_TIME, ENTRY_END_TIME
  MAX_ORDER_RETRY, ORDER_POLL_INTERVAL, ORDER_FILL_TIMEOUT
"""
from __future__ import annotations

import sys
import threading
import time
from datetime import datetime

from .base_strategy import BaseStrategy
from .candle_builder import CandleBuilder
from .config_loader import Config
from .context_builder import parse_chain_entries
from .credit_spread_db import (
    save_leg, save_signal_event, save_strategy_run, save_trade,
)
from .execution_gateway import ExecutionError
from .expiry_utils import fetch_lot_size, fetch_strike_interval, select_expiry
from .market_utils import is_entry_allowed, is_market_open
from .notifier import LEVEL_ALERT, LEVEL_INFO, LEVEL_URGENT
from .script_helpers import build_client, build_db, build_gateway, build_notifier
from .signal import Bias, ConfirmationCounter
from .sizing import (
    fit_lots_to_margin, fixed_lots, margin_based_lots, quantity_from_lots,
)
from ..models.strategy_context import StrategyContext
from ..models.strategy_state import StrategyState
from ..utils.constants import (
    EXEC_MODE_ENTRY_EXEC_MONITOR, EXEC_MODE_FULL_EXEC, EXEC_MODE_MONITOR_ONLY,
    INSTRUMENT_OPTIONS,
)
from ..utils.helper import format_inr, now_ist
from ..utils.logger import get_logger, setup_logger

logger = get_logger(__name__)


class EntryRunner:
    """
    Runs the full entry lifecycle for any ``BaseStrategy`` subclass.

    Usage::

        config   = Config("config/execution_settings.yaml")
        strategy = MyStrategy(config)
        EntryRunner(strategy, config).run()

    Blocks until a signal is confirmed and entry is complete, the entry
    window closes, or the market is closed.
    """

    def __init__(self, strategy: BaseStrategy, config: Config) -> None:
        self._strategy = strategy
        self._cfg      = config

    # -------------------------------------------------------------------------
    # Public entry point
    # -------------------------------------------------------------------------

    def run(self) -> None:
        cfg       = self._cfg
        strategy  = self._strategy
        exec_mode = cfg.get_str("EXECUTION_MODE", EXEC_MODE_MONITOR_ONLY)

        # ── Logging ──────────────────────────────────────────────────────────
        setup_logger(
            "strategy_framework",
            log_file=cfg.get_str("LOG_FILE", "logs/cs_execution.log"),
            level=cfg.get_str("LOG_LEVEL", "INFO"),
        )
        logger.info("=" * 60)
        logger.info("EntryRunner — %s", cfg.get_str("STRATEGY_NAME", "STRATEGY"))
        logger.info("Mode      : %s", exec_mode)
        logger.info("Underlying: %s", cfg.get_str("UNDERLYING", "NIFTY"))
        logger.info("=" * 60)

        # ── Infrastructure ───────────────────────────────────────────────────
        notifier     = build_notifier(cfg)
        sf, db_queue = build_db(cfg)
        client       = build_client(cfg)

        # ── Instrument metadata ──────────────────────────────────────────────
        underlying = cfg.get_str("UNDERLYING", "NIFTY")
        exch_idx   = cfg.get_str("EXCHANGE_INDEX", "NSE_INDEX")
        exch_opt   = cfg.get_str("EXCHANGE_OPTIONS", "NFO")

        expiries = client.get_expiry_dates(underlying, exch_opt, INSTRUMENT_OPTIONS)
        expiry   = select_expiry(
            expiries,
            mode=cfg.get_str("EXPIRY_MODE", "WEEKLY"),
            dte_min=cfg.get_int("DTE_MIN", 1),
            dte_max=cfg.get_int("DTE_MAX", 14),
            rollover_hhmm=cfg.get_str("EXPIRY_ROLLOVER_TIME", "11:00"),
        )
        if not expiry:
            logger.critical("No valid expiry found. Exiting.")
            sys.exit(1)
        logger.info("Selected expiry: %s", expiry)

        lot_size = cfg.get_int("LOT_SIZE", 0) or fetch_lot_size(client, underlying, exch_idx, expiry, exch_opt)
        interval = cfg.get_int("STRIKE_INTERVAL", 50) or fetch_strike_interval(client, underlying, exch_idx, expiry)
        logger.info("lot_size=%d  strike_interval=%.0f", lot_size, interval)

        # ── Idempotency guard ─────────────────────────────────────────────────
        strategy_id = self._build_strategy_id()
        for pos in client.get_positions():
            sym = pos.get("symbol", "")
            if underlying in sym and pos.get("quantity", 0) != 0:
                logger.warning("Existing %s position found — skipping entry.", underlying)
                notifier.send(
                    f"SKIPPED: existing {underlying} position detected — not re-entering.",
                    level=LEVEL_ALERT,
                )
                db_queue.stop(); client.close(); sys.exit(0)

        # ── Position sizing ──────────────────────────────────────────────────
        lots     = self._compute_lots(client, lot_size, exch_opt)
        quantity = quantity_from_lots(lots, lot_size)
        logger.info("Sizing: %d lot(s) × %d = %d", lots, lot_size, quantity)

        # ── CandleBuilder + warm-up ──────────────────────────────────────────
        tf_str  = cfg.get_str("INDICATOR_TIMEFRAME", "15m")
        tf_min  = int(tf_str.replace("m", "").strip())
        builder = CandleBuilder(symbol=underlying, interval_minutes=tf_min)

        warmup_bars = cfg.get_int("WARMUP_BARS", 100)
        logger.info("Loading %d historical %s bars for warm-up …", warmup_bars, tf_str)
        try:
            hist = client.get_history(
                symbol=underlying, exchange=exch_idx,
                interval=tf_str, source="api", warmup_bars=warmup_bars,
            )
            if hist:
                builder.seed_from_history(hist[-warmup_bars:])
                strategy.seed_indicators(builder)
                logger.info("Warm-up complete: %d bars loaded.", builder.closed_count)
            else:
                logger.warning("No historical bars returned — warm-up skipped.")
        except Exception as exc:
            logger.warning("History fetch failed (%s) — continuing without warm-up.", exc)

        # ── Confirmation counters ────────────────────────────────────────────
        confirm_n      = cfg.get_int("CONFIRMATION_BARS", 2)
        cooldown_bars  = cfg.get_int("SIGNAL_COOLDOWN_BARS", 5)
        bull_counter   = ConfirmationCounter(required=confirm_n)
        bear_counter   = ConfirmationCounter(required=confirm_n)
        _cooldown_left = 0
        _entry_done    = threading.Event()
        _pending_bias: list[Bias] = []

        # ── WebSocket tick handler ────────────────────────────────────────────
        def on_ltp(tick: dict) -> None:
            nonlocal _cooldown_left

            ltp = tick.get("data", {}).get("ltp") or tick.get("ltp") or tick.get("price")
            if ltp is None:
                return

            closed_bar = builder.tick(float(ltp), datetime.now())
            if closed_bar is None:
                return   # bar still building

            strategy.update_indicators(builder)

            _dbg = strategy.indicators
            logger.debug(
                "CANDLE | Close=%.2f  EMA21=%.2f  RSI=%.2f  RSI_EMA=%.2f  ST=%.2f  ST_dir=%s",
                _dbg.close or 0, _dbg.ema_slow or 0, _dbg.rsi or 0,
                _dbg.rsi_ema or 0, _dbg.supertrend or 0, _dbg.supertrend_direction,
            )

            if _entry_done.is_set():
                return

            if _cooldown_left > 0:
                _cooldown_left -= 1
                logger.debug("Signal cooldown: %d bars remaining.", _cooldown_left)
                bull_counter.reset(); bear_counter.reset()
                return

            if not is_entry_allowed(
                now=now_ist(),
                entry_start_hhmm=cfg.get_str("ENTRY_START_TIME", "09:30"),
                entry_end_hhmm=cfg.get_str("ENTRY_END_TIME", "14:30"),
            ):
                logger.debug("Outside entry window — skipping signal check.")
                return

            ind        = strategy.indicators
            bias       = strategy.evaluate_signal(ind)
            bull_fired = bull_counter.update(bias == Bias.BULLISH)
            bear_fired = bear_counter.update(bias == Bias.BEARISH)

            if bias != Bias.BULLISH:
                bull_counter.reset()
            if bias != Bias.BEARISH:
                bear_counter.reset()

            # First-match log (counter just hit 1)
            if bias != Bias.NEUTRAL:
                _cnt = bull_counter.count if bias == Bias.BULLISH else bear_counter.count
                if _cnt == 1:
                    strategy.on_signal_first_match(bias, ind)
                    logger.debug(
                        "SIGNAL FIRST MATCH [%s] (1/%d) | "
                        "RSI=%.2f  RSI_EMA=%.2f  Close=%.2f  EMA21=%.2f  ST=%.2f  ST_dir=%s",
                        bias.value, confirm_n,
                        ind.rsi or 0, ind.rsi_ema or 0,
                        ind.close or 0, ind.ema_slow or 0,
                        ind.supertrend or 0, ind.supertrend_direction,
                    )

            if bull_fired or bear_fired:
                confirmed = Bias.BULLISH if bull_fired else Bias.BEARISH
                ind = strategy.indicators
                strategy.on_signal_confirmed(confirmed, ind)
                logger.info(
                    "SIGNAL CONFIRMED [%s] | "
                    "RSI=%.2f  RSI_EMA=%.2f  Close=%.2f  EMA21=%.2f  ST=%.2f  ST_dir=%s",
                    confirmed.value,
                    ind.rsi or 0, ind.rsi_ema or 0,
                    ind.close or 0, ind.ema_slow or 0,
                    ind.supertrend or 0, ind.supertrend_direction,
                )
                _pending_bias.append(confirmed)
                _entry_done.set()

        # ── Subscribe to WebSocket ────────────────────────────────────────────
        logger.info("Subscribing to %s LTP on WebSocket …", underlying)
        try:
            from openalgo import api as _OA
            _oa_ws = _OA(
                api_key=cfg.get_str("OPENALGO_API_KEY"),
                host=cfg.get_str("OPENALGO_HOST", "http://127.0.0.1:5000"),
            )
            if not _oa_ws.connect():
                raise Exception("WebSocket connect() failed — check OpenAlgo WS server.")
            _oa_ws.subscribe_ltp(
                [{"exchange": exch_idx, "symbol": underlying}],
                on_data_received=on_ltp,
            )
            logger.info("WebSocket subscribed for %s.", underlying)
        except Exception as exc:
            logger.critical("WebSocket subscription failed: %s", exc)
            notifier.send(f"WebSocket subscription failed: {exc}", level=LEVEL_URGENT)
            db_queue.stop(); client.close(); sys.exit(1)

        # ── Wait for signal or entry-window close ────────────────────────────
        logger.info("Watching for entry signal … (Ctrl+C to abort)")
        notifier.send(
            f"Entry script started: {underlying}  expiry={expiry}  lots={lots}",
            level=LEVEL_INFO,
        )

        try:
            while not _entry_done.is_set():
                if not is_market_open():
                    logger.info("Market closed — nothing to do. Exiting.")
                    db_queue.stop(); client.close(); sys.exit(0)

                if not is_entry_allowed(
                    now=now_ist(),
                    entry_start_hhmm=cfg.get_str("ENTRY_START_TIME", "09:30"),
                    entry_end_hhmm=cfg.get_str("ENTRY_END_TIME", "14:30"),
                ):
                    logger.info("Entry window closed without a confirmed signal. Exiting.")
                    notifier.send(
                        f"{underlying}: entry window closed — no signal today.",
                        level=LEVEL_INFO,
                    )
                    db_queue.stop(); client.close(); sys.exit(0)

                time.sleep(5)

        except KeyboardInterrupt:
            logger.info("Keyboard interrupt — aborting before entry.")
            db_queue.stop(); client.close(); sys.exit(0)

        # ── Signal confirmed → fetch spot → ask strategy to select legs ───────
        confirmed_bias = _pending_bias[0]

        try:
            chain = client.get_option_chain(underlying, exch_idx, expiry, strike_count=5)
            spot  = float(chain.get("underlying_ltp", 0) or 0)
        except Exception as exc:
            logger.error("Spot fetch failed: %s", exc)
            spot  = 0.0
            chain = {}

        # Strategy decides EVERYTHING about the legs — type, strikes, count, product
        result   = strategy.select_legs(
            bias=confirmed_bias,
            spot=spot,
            expiry=expiry,
            exchange=exch_opt,
            interval=interval,
            quantity=quantity,
        )
        all_legs = result.all_legs

        logger.info(
            "Legs selected: %s | %d leg(s) | net_premium=%.2f",
            result.spread_type,
            len(all_legs),
            result.net_premium or 0,
        )
        for leg in all_legs:
            logger.info("  %s  strike=%.0f  qty=%d", leg.symbol, leg.strike, leg.quantity)
        notifier.send(
            f"SIGNAL: {confirmed_bias.value} → {result.spread_type}\n"
            + "\n".join(f"  {l.symbol}  qty={l.quantity}" for l in all_legs),
            level=LEVEL_ALERT,
        )

        # ── Margin preflight ──────────────────────────────────────────────────
        # Use the first BUY leg as primary, first SELL leg as hedge for margin
        # estimation.  If the strategy has only buys (debit), hedge_symbol = "".
        from ..models.option_leg import LegAction as _LA
        _buy_legs  = [l for l in all_legs if l.action == _LA.BUY]
        _sell_legs = [l for l in all_legs if l.action == _LA.SELL]
        _primary_sym = _sell_legs[0].symbol if _sell_legs else (_buy_legs[0].symbol if _buy_legs else "")
        _hedge_sym   = _buy_legs[0].symbol  if _buy_legs  else ""

        _product = cfg.get_str("PRODUCT", "NRML")
        lots, margin_per_lot, _margin_req, margin_avail = fit_lots_to_margin(
            client=client,
            primary_symbol=_primary_sym,
            hedge_symbol=_hedge_sym,
            exchange=exch_opt,
            lot_size=lot_size,
            requested_lots=lots,
            product=_product,
            deployed_capital=cfg.get_float("DEPLOYED_CAPITAL", 0.0),
            margin_buffer_pct=cfg.get_float("MARGIN_BUFFER_PCT", 15.0),
        )

        if lots == 0:
            msg = (
                f"MARGIN INSUFFICIENT: ₹{margin_per_lot:,.0f}/lot incl. buffer, "
                f"available ₹{margin_avail:,.0f}. Aborting."
            )
            logger.critical(msg)
            notifier.send(msg, level=LEVEL_URGENT)
            db_queue.stop(); client.close(); sys.exit(1)

        # Rebuild legs at revised lot count if margin check reduced them
        quantity = quantity_from_lots(lots, lot_size)
        if any(l.quantity != quantity for l in all_legs):
            logger.info("Margin-adjusted sizing: %d lot(s) × %d = %d", lots, lot_size, quantity)
            result   = strategy.select_legs(
                bias=confirmed_bias, spot=spot, expiry=expiry,
                exchange=exch_opt, interval=interval, quantity=quantity,
            )
            all_legs = result.all_legs

        # ── Execute ───────────────────────────────────────────────────────────
        gateway, gw_mode = build_gateway(
            cfg, client,
            live_exec_modes=(EXEC_MODE_ENTRY_EXEC_MONITOR, EXEC_MODE_FULL_EXEC),
        )

        if exec_mode == EXEC_MODE_MONITOR_ONLY:
            _ts = now_ist().strftime("%H:%M:%S")
            logger.info("[MONITOR_ONLY] Signal fired — would have entered:")
            for leg in all_legs:
                _ltp = 0.0
                try:
                    q    = client.get_quotes(leg.symbol, exch_opt)
                    _ltp = float(q.get("ltp") or 0)
                except Exception:
                    pass
                ex = leg.expiry.replace("-", "")
                logger.info(
                    "  %s-%s-%s-%s-qty=%d-@%.2f [PAPER] at %s",
                    leg.action.value, int(leg.strike), leg.option_type.value,
                    ex, leg.quantity, _ltp, _ts,
                )
            notifier.send(
                f"MONITOR_ONLY: {result.spread_type} signal — no orders placed.",
                level=LEVEL_INFO,
            )
            db_queue.stop(); client.close(); sys.exit(0)

        chain_entries = parse_chain_entries(chain)
        dummy_state   = StrategyState(
            strategy_id=strategy_id,
            strategy_name=cfg.get_str("STRATEGY_NAME", "ICS"),
        )
        ctx = StrategyContext(
            timestamp=now_ist(), spot=spot,
            atm_strike=round(spot / interval) * interval if interval else spot,
            expiry=expiry, option_chain=chain_entries,
            positions=[], trades=[], orders=[],
            available_cash=0, m2m_unrealized=0, m2m_realized=0,
            config={}, strategy_state=dummy_state,
        )

        logger.info("Placing orders (BUY legs first, then SELL) …")
        try:
            gateway.execute_legs_hedge_first(all_legs, strategy_id, ctx)
        except ExecutionError as exc:
            logger.critical("Entry execution failed: %s", exc)
            notifier.send(f"ENTRY FAILED: {exc}", level=LEVEL_URGENT)
            db_queue.stop(); client.close(); sys.exit(1)

        # ── Persist to DB ─────────────────────────────────────────────────────
        # Use first and last leg strikes as primary/hedge in the run record.
        # The strategy can store any per-leg detail it needs via the legs table.
        _sess  = sf.make()
        run_id = 0
        try:
            run_id = save_strategy_run(
                _sess,
                strategy_id=strategy_id,
                strategy_name=cfg.get_str("STRATEGY_NAME", "ICS"),
                spread_type=result.spread_type,
                underlying=underlying,
                expiry=expiry,
                entry_spot=spot,
                primary_strike=all_legs[0].strike  if all_legs else 0,
                hedge_strike=all_legs[-1].strike    if len(all_legs) > 1 else 0,
                net_premium=result.net_premium,
                lots=lots,
                lot_size=lot_size,
                entry_time=now_ist().replace(tzinfo=None),
                status="OPEN",
                execution_mode=exec_mode,
                trade_mode=exec_mode,
            )
            _sess.commit()
            logger.info("Strategy run saved: run_id=%d", run_id)
        except Exception as exc:
            logger.error("DB save failed: %s", exc)
            _sess.rollback()
        finally:
            _sess.close()

        ind = strategy.indicators
        for leg in all_legs:
            db_queue.put(save_leg,
                run_id=run_id, role=leg.role.value, symbol=leg.symbol,
                exchange=leg.exchange, strike=leg.strike,
                option_type=leg.option_type.value, action=leg.action.value,
                quantity=leg.quantity, product=leg.product, expiry=leg.expiry,
                entry_price=leg.entry_price, entry_order_id=leg.order_id,
                entry_time=leg.entry_time.replace(tzinfo=None) if leg.entry_time else None,
                status=leg.status.value,
            )
            db_queue.put(save_trade,
                run_id=run_id, event_type="ENTRY", symbol=leg.symbol,
                action=leg.action.value, quantity=leg.quantity,
                order_id=leg.order_id, fill_price=leg.entry_price,
                fill_time=leg.entry_time.replace(tzinfo=None) if leg.entry_time else None,
                status="complete",
            )

        db_queue.put(save_signal_event,
            run_id=run_id,
            ts=now_ist().replace(tzinfo=None),
            bias=confirmed_bias.value,
            spread_type=result.spread_type,
            rsi=ind.rsi,
            supertrend_dir=ind.supertrend_direction,
            ema_fast=ind.ema_fast,
            ema_slow=ind.ema_slow,
            spot=spot,
            confirmation_n=confirm_n,
            action_taken="ENTRY_TRIGGERED",
        )

        # ── Summary ───────────────────────────────────────────────────────────
        strategy.on_entry_complete(result, gw_mode)

        logger.info("=" * 60)
        logger.info("ENTRY COMPLETE [%s]", gw_mode)
        logger.info("Strategy ID : %s", strategy_id)
        logger.info("Type        : %s", result.spread_type)
        logger.info("Expiry      : %s", expiry)
        logger.info("TRADES EXECUTED:")
        for leg in all_legs:
            ex      = leg.expiry.replace("-", "")
            price_s = f"{leg.entry_price:.2f}" if leg.entry_price is not None else "N/A"
            ts_s    = leg.entry_time.strftime("%H:%M:%S") if leg.entry_time else now_ist().strftime("%H:%M:%S")
            logger.info(
                "  %s-%s-%s-%s-qty=%d-@%s [%s] at %s",
                leg.action.value, int(leg.strike), leg.option_type.value,
                ex, leg.quantity, price_s, gw_mode, ts_s,
            )
        logger.info("Net Premium : %s", format_inr(result.net_premium or 0))
        logger.info("=" * 60)

        notifier.send(
            f"ENTRY COMPLETE [{gw_mode}]: {result.spread_type} | {underlying} {expiry}\n"
            + "\n".join(
                f"  {l.action.value}-{int(l.strike)}-{l.option_type.value}"
                f"-{l.expiry.replace('-','')}-qty={l.quantity}"
                f"-@{'N/A' if l.entry_price is None else f'{l.entry_price:.2f}'} [{gw_mode}]"
                for l in all_legs
            )
            + f"\nNet premium: {format_inr(result.net_premium or 0)}  qty={quantity}",
            level=LEVEL_ALERT,
        )

        db_queue.stop()
        client.close()
        logger.info("Entry script completed. Hand off to monitoring script.")

    # -------------------------------------------------------------------------
    # Private helpers
    # -------------------------------------------------------------------------

    def _build_strategy_id(self) -> str:
        prefix = self._cfg.get_str("STRATEGY_ID_PREFIX", "ICS")
        today  = now_ist().strftime("%Y%m%d")
        return f"{prefix}_{today}"

    def _compute_lots(self, client, lot_size: int, exch_opt: str) -> int:
        cfg         = self._cfg
        sizing_mode = cfg.get_str("SIZING_MODE", "FIXED")
        if sizing_mode == "MARGIN_BASED":
            return margin_based_lots(
                client=client,
                underlying=cfg.get_str("UNDERLYING", "NIFTY"),
                exchange=exch_opt,
                primary_symbol="",
                hedge_symbol="",
                lot_size=lot_size,
                product=cfg.get_str("PRODUCT", "NRML"),
                max_lots=cfg.get_int("MAX_LOTS", 10),
                capital_utilisation_pct=cfg.get_float("CAPITAL_UTILISATION_PCT", 100.0),
                deployed_capital=cfg.get_float("DEPLOYED_CAPITAL", 0.0),
                margin_buffer_pct=cfg.get_float("MARGIN_BUFFER_PCT", 15.0),
            )
        return fixed_lots(
            cfg.get_int("FIXED_LOTS", 1),
            lot_size,
            max_lots=cfg.get_int("MAX_LOTS", 10),
        )
