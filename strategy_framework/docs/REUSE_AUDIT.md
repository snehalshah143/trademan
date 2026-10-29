# Reuse Audit — Credit Spread Strategy Framework

**Scope:** OpenAlgo repo (`D:\SnehalImp\git\OpenAlgo\openalgo`) +
Trademan strategy_framework (`D:\SnehalImp\git\trademan\strategy_framework`) +
Trademan backend alert engine (`D:\SnehalImp\git\trademan\backend\alerts\`)

**Purpose:** Inform Phase 1 design so nothing is built that already exists,
and nothing shared is broken by new code.

---

## 1. OpenAlgo — What Is Actually Available

### 1.1 Python SDK (`openalgo` package)

The SDK is a single `api` class that inherits from several mixins:

| Mixin | File | Methods confirmed |
|---|---|---|
| BaseAPI | `base.py` | `_post()`, connection-pooled httpx client, `close()` |
| DataAPI | `data.py` | `quotes`, `multiquotes`, `depth`, `history`, `intervals`, `market_holidays`, `market_timings` |
| OrderAPI | `orders.py` | `placeorder`, `placesmartorder`, `modifyorder`, `cancelorder`, `cancelallorder`, `orderstatus`, `orderbook`, `tradebook`, `positionbook`, `openposition`, `closeposition` |
| OptionsAPI | `options.py` | `optionchain`, `option_greeks`, `multi_option_greeks`, `option_symbol`, `expiry`, `options_order`, `options_multiorder` |
| AccountAPI | `account.py` | `funds`, `holdings`, `margin` |
| FeedAPI | `feed.py` | WebSocket LTP / quotes / depth / order-updates, `connect`, `disconnect`, `subscribe_ltp`, `subscribe_quotes`, `subscribe_depth`, `subscribe_orders`, `unsubscribe` |
| StrategyAPI | `strategy_api.py` | `strategylist`, `strategystart`, `strategystop`, `strategystatus`, `strategyevents` — manages OpenAlgo's own strategy module (not our standalone scripts) |
| GttAPI | `gtt.py` | GTT order CRUD |
| TelegramAPI | `telegram.py` | `telegram(message=...)` — Telegram bot message via OpenAlgo |
| `ta` module | `indicators/` | `supertrend`, `rsi`, `ema`, `macd`, `atr`, `bollinger`, `vwap`, `adx`, volume indicators, etc. |

All REST calls return `{"status": "success"|"error", "data": {...}, "message": "..."}`.
The SDK wraps every failure in the same error dict — callers check `status == "error"`.

### 1.2 REST Endpoints Used by Our Strategy

| Endpoint | SDK method | Our use |
|---|---|---|
| `/api/v1/ping` | `ping()` | Health check on startup |
| `/api/v1/funds` | `funds()` | Available cash + margin check |
| `/api/v1/margin` | `margin(positions=[…])` | Pre-trade margin estimate |
| `/api/v1/quotes` | `quotes(symbol, exchange)` | Single-symbol LTP |
| `/api/v1/multiquotes` | `multiquotes(symbols=[…])` | Batch LTP for all legs |
| `/api/v1/history` | `history(symbol, exchange, interval, start_date, end_date, source)` | Candle history; `source="db"` uses local DuckDB cache, `source="api"` always hits broker |
| `/api/v1/intervals` | `intervals()` | Broker-supported intervals |
| `/api/v1/optionchain` | `optionchain(underlying, exchange, expiry_date, strike_count)` | ATM strike, option LTPs, lot size hint |
| `/api/v1/expiry` | `expiry(symbol, exchange, instrumenttype)` | List of weekly/monthly expiry dates |
| `/api/v1/placeorder` | `placeorder(strategy, symbol, action, exchange, price_type, product, quantity, price, …)` | Entry and exit orders |
| `/api/v1/orderstatus` | `orderstatus(order_id, strategy)` | Poll fill |
| `/api/v1/modifyorder` | `modifyorder(order_id, …)` | Modify pending order to marketable limit |
| `/api/v1/cancelorder` | `cancelorder(order_id, strategy)` | Cancel unfilled order |
| `/api/v1/orderbook` | `orderbook()` | Reconcile after placement |
| `/api/v1/tradebook` | `tradebook()` | Confirm fill prices on recovery |
| `/api/v1/positionbook` | `positionbook()` | Reconcile open positions on recovery |
| `/api/v1/openposition` | `openposition(strategy, symbol, exchange, product)` | Check if a leg is open after restart |
| `/api/v1/closeposition` | `closeposition(strategy)` | Emergency square-off |

**Not confirmed as needed:** split_order (will handle freeze qty manually), basket_order, GTT, synthetic_future.

### 1.3 WebSocket Feed

- URL: `ws://127.0.0.1:8765` (env: `WEBSOCKET_URL` or derived from `OPENALGO_HOST`)
- Auth: API key sent as a message after connect; server acks
- **Subscription modes:**
  - Mode 1 — LTP only: `subscribe_ltp(instruments=[{"exchange": "NFO", "symbol": "NIFTY…"}])`
  - Mode 2 — Quotes (OHLCV + LTP + volume): `subscribe_quotes(…)`
  - Mode 3 — Depth (20-level order book): `subscribe_depth(…)`
- **Callbacks:** `ltp_callback(data)`, `quote_callback(data)`, `depth_callback(data)`, `order_callback(data)`
- **Auto-reconnect:** `auto_reconnect=True` (default) — SDK reconnects with exponential backoff, re-authenticates, and replays all active subscriptions. No extra code needed in strategy.
- **Fallback:** strategy falls back to `quotes()` REST call if WS is unavailable.
- **Our use:** subscribe Mode 1 (LTP) for spot + all option legs; update indicator engine and MTM each tick; build 1-min candle on WS tick stream; REST fallback per cycle.

### 1.4 NIFTY Symbol Format

| Instrument | Exchange | Symbol format | Example |
|---|---|---|---|
| NIFTY spot / index | `NSE_INDEX` | `NIFTY` | `NIFTY` |
| NIFTY futures | `NFO` | `NIFTY{DDMMMYY}FUT` | `NIFTY29MAY26FUT` |
| NIFTY weekly CE | `NFO` | `NIFTY{DDMMMYY}{STRIKE}CE` | `NIFTY07MAY2624000CE` |
| NIFTY weekly PE | `NFO` | `NIFTY{DDMMMYY}{STRIKE}PE` | `NIFTY07MAY2622500PE` |

- Expiry string format: `DDMMMYY` upper-case (e.g. `07MAY26`)
- Strike interval: 50 points for NIFTY (from option chain)
- Lot size: **always fetch from optionchain API or instruments** — currently 65 but changes on contract rollover
- `build_option_symbol(expiry, strike, opt_type)` → `f"NIFTY{expiry}{int(strike)}{opt_type}"` (confirmed in existing credit spread strategy)

### 1.5 OpenAlgo `ta` Module (Built-in Indicators)

Available via `from openalgo import ta`:

| Function | Signature | Notes |
|---|---|---|
| `ta.supertrend` | `(high, low, close, period, multiplier)` → `(values, direction)` | direction: **-1 = uptrend/bullish**, +1 = downtrend/bearish |
| `ta.rsi` | `(close, period)` → Series | Standard Wilder RSI |
| `ta.ema` | `(series, period)` → Series | Standard EMA |
| `ta.macd` | `(close, fast_period, slow_period, signal_period)` → `(macd, signal, histogram)` | |
| `ta.atr` | `(high, low, close, period)` | |

All accept pandas Series. **These work on the full history DataFrame** — appropriate for the warm-up pass. For incremental per-tick updates we use Trademan's own `calculators.py`.

### 1.6 History + Candle Resampling Pattern (from existing credit spread)

```python
df_1m = client.history(symbol="NIFTY", exchange="NSE_INDEX",
                       interval="1m", start_date=..., end_date=..., source="db")
df_15m = df_1m.resample("15min", label="left", closed="left") \
              .agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}) \
              .dropna(subset=["open"])
```

The last candle in `df_15m` is the **forming bar** (current partial). This is the pattern to follow for M15 and M3/M5 computations. Confirmed: this is exactly the pattern we need for the new strategy.

### 1.7 OpenAlgo Python Strategy Hosting

When a script is hosted inside OpenAlgo via the `/python` route:
- Each strategy runs as a **separate subprocess** (`subprocess.Popen`) — full isolation
- **Env vars injected automatically:**
  - `OPENALGO_API_KEY` — user's API key
  - `OPENALGO_HOST` — default `http://127.0.0.1:5000`
  - `STRATEGY_ID` — unique ID assigned by OpenAlgo
  - `STRATEGY_NAME` — strategy name from UI
  - `OPENALGO_STRATEGY_EXCHANGE` — configured exchange
- `WEBSOCKET_URL` is **not** injected; derive from `OPENALGO_HOST` or set in your own config
- Logs from stdout/stderr are captured and shown in the UI log stream
- Strategy receives SIGTERM on stop — must handle gracefully
- Standalone run: `uv run python strategies/my_strategy.py` (same code, reads env vars from `.env` or shell)
- **No special lifecycle callbacks** — strategy is a long-running script; stop = SIGTERM

### 1.8 Analyzer / Sandbox Mode — Paper Trading Suitability

OpenAlgo's analyzer mode is a **server-side broker order sandbox** — it simulates order routing within OpenAlgo's own engine (fake fills, fake positions). It is:
- Not accessible from standalone strategy scripts as a paper mode switch
- Not per-strategy controllable from external code
- Designed for testing the OpenAlgo platform's own strategy module, not our script-based strategies

**Decision (confirmed in spec):** Use our own paper ledger (`PaperEngine`) as primary. Analyzer mode is noted as a future optional alternative only.

### 1.9 Example Strategy Structure (Style to Follow)

Both `Nifty_credit_spread_positional_weekly_05May.py` and `noss_strategy.py` follow a pattern we should adopt:

1. All config at top of file / in YAML
2. Single `api` client initialized from env vars
3. Shared `strategy_db.py` (SQLite) for position + trade persistence
4. Main loop polling history → compute indicators → check conditions
5. Background thread for MTM risk monitoring
6. SIGTERM / keyboard interrupt caught for clean exit
7. `strategy_db.init_db()` on startup → `save_open_position()` on entry → `close_position()` on exit

**New code should follow this pattern** and extend `strategy_db.py` (or create `credit_spread_db.py`) for credit spread specific columns (confirmation counters, trailing SL levels, profit-booking stage).

---

## 2. Trademan / strategy_framework — What Already Exists

### 2.1 Core Framework Modules (`strategy_framework/framework/`)

| Module | File | Status | Notes |
|---|---|---|---|
| OpenAlgo client wrapper | `framework/openalgo_client.py` | **Complete** | Wraps SDK, raises `OpenAlgoError`, exposes: quotes, optionchain, expiry, positions, trades, orders, funds, margin, placeorder, orderstatus, cancelorder, closeposition, openposition |
| Config loader | `framework/config_loader.py` | **Complete** | YAML → typed `Config` object; validates required keys; env-var override support |
| Execution gateway | `framework/execution_gateway.py` | **Complete** | Routes to paper/live; BUY-first rule; polling; retries; `execute_legs_buy_first()`, `close_leg()` |
| Monitoring engine | `framework/monitor.py` | **Complete** | Fixed-interval loop; `on_start/stop/cycle_start/cycle_end/evaluate` hooks; SIGINT/SIGTERM; paused detection; heartbeat |
| Context builder | `framework/context_builder.py` | **Complete** | Builds `StrategyContext` each cycle from OpenAlgo data |
| Recovery manager | `framework/recovery_manager.py` | **Complete** | Reconstructs state from positionbook + orderbook + tradebook + local JSON snapshot |

### 2.2 Models (`strategy_framework/models/`)

| Model | File | Status | Notes |
|---|---|---|---|
| `OptionLeg` | `models/option_leg.py` | **Complete** | role, symbol, exchange, strike, option_type, action, quantity, product, expiry, entry/exit price, order_id, status, timestamps; `LegRole`, `LegAction`, `LegStatus`, `OptionType` enums |
| `StrategyState` | `models/strategy_state.py` | **Complete** | Full Iron Fly state; `StrategyPhase` enum (IDLE/DEPLOYING/MONITORING/ADJUSTING/REALIGNING/TIGHTENING/EXITING/EXITED/PAUSED); MTM high-water tracking; `open_legs`, `current_mtm`, `pause()` |
| `StrategyContext` | `models/strategy_context.py` | **Complete** | Immutable per-cycle snapshot: spot, expiry, available_cash, option chain, etc. |

### 2.3 Paper Engine (`strategy_framework/paper/`)

| Feature | Status | Notes |
|---|---|---|
| Virtual order book + position book | **Complete** | Instant fill at LTP |
| MTM calculation | **Complete** | `current_mtm`, `max_mtm`, `min_mtm` |
| JSON snapshot persistence | **Complete** | Restores across process restarts |
| Bid/ask fill price option | **Missing** | Currently fills at LTP only; spec item I.7 allows bid/ask from depth |

### 2.4 Base Strategy (`strategy_framework/base/`)

```python
class BaseStrategy(ABC):
    def evaluate(context)       # abstract — called every cycle
    def is_deployment_due(ctx)  # abstract
    def deploy(ctx)             # abstract
    def should_exit(ctx)        # abstract — returns (bool, reason)
    def exit_all(ctx, reason)   # abstract
    def on_start/stop/cycle_start/cycle_end  # optional hooks
```

Iron Fly concretises all of these. Credit spread will need a different concrete implementation — **extend `BaseStrategy`, do not change it**.

### 2.5 Utilities (`strategy_framework/utils/`)

| Module | Status | Notes |
|---|---|---|
| `logger.py` | **Complete** | `setup_logger`, `get_logger`, `PaperLogAdapter` ([PAPER] prefix); rotating file + console; UTF-8 |
| `constants.py` | **Complete** | Fill statuses, terminal statuses, execution modes, heartbeat separator |
| `helper.py` | **Complete** | `now_ist()`, `format_inr()`, `safe_float()`, `safe_int()` |

### 2.6 Trademan Backend Alert Engine (`backend/alerts/`)

> **Do not modify these files — they are used by the running FastAPI server.**

| Module | Key features |
|---|---|
| `rule_evaluator.py` | Recursive condition-tree evaluator; scopes: `STRATEGY` (MTM, PNL_PCT, max_profit, greeks), `LEG` (LTP, premium change %, PNL, delta/gamma/theta/vega), `SPOT` (price, price action), `INDICATOR` (EMA, RSI, Supertrend values) |
| `models.py` | `AlertRuleBuilder` (strategy-linked), `AlertRule` (position-linked), `AlertHistory`, `AlertTemplate` — all with `condition_tree: JSON` |
| `alert_pipeline.py` | Orchestrates rule evaluation → notification |
| `notification_engine.py` | Pluggable: popup, telegram, email, webhook, sound |
| `alert_cache.py` | Redis-based dedup and cooldown |
| `dependency_graph.py` | Dependency tracking between alert rules |
| `symbol_worker.py` | Per-symbol evaluation worker |

**Reuse opportunity:** The `condition_tree` JSON schema is an established format for serialisable rules. We should store credit spread rules (entry, SL, exit, trailing) using the same schema structure so the Trademan UI can edit them later. We do **not** call the backend's alert engine directly from the strategy script — we replicate a lightweight subset in the strategy.

### 2.7 Trademan Backend Indicators (`backend/services/indicators/`)

| Module | What's in it |
|---|---|
| `calculators.py` | `rsi_seed/rsi_step`, `ema_step/ema_from_series`, `macd_full/macd_histogram`, `atr`, `bollinger`, `supertrend` (pure numpy, no side effects) |
| `rolling_window.py` | Circular buffer management for per-symbol indicator state |

**Both exist and are complete.** However they are part of the FastAPI backend, not the strategy_framework. For the standalone strategy we have two options:
- **Option A (recommended):** Copy (vendor) the pure `calculators.py` into `strategy_framework/framework/` — it has no backend dependencies
- **Option B:** Use the OpenAlgo `ta` module for the warm-up pass (full-history pandas), then use manual incremental updates with inline math

### 2.8 Existing Iron Fly Strategy (`strategy_framework/strategies/iron_fly/`)

**Do not modify.** Use as a reference implementation. Key patterns:
- Full `BaseStrategy` implementation
- Config via `settings.yaml` with all parameters
- DB persistence via JSON snapshots
- Recovery via `RecoveryManager`
- Tests in `tests/`

---

## 3. Requirement → Module Mapping Table

| Requirement | Existing module (path) | Action | Notes |
|---|---|---|---|
| **OpenAlgo health check** | `framework/openalgo_client.py::OpenAlgoClient.get_funds()` | **Reuse as-is** | ping via funds; add explicit ping wrapper |
| **API key from env / injection** | Pattern from `Nifty_credit_spread…py` | **Copy pattern** | `os.getenv("OPENALGO_API_KEY")` |
| **Trading window check** | Not in strategy_framework | **Build new** | Simple time-of-day check; market holiday via `client.market_holidays()` |
| **Config loading (YAML)** | `framework/config_loader.py::Config` | **Extend** | Add credit spread required keys; keep backward compat |
| **Expiry selection** | `Nifty_credit_spread…py::select_expiry()` | **Extract + reuse** | Move to `framework/expiry_utils.py`; already handles DTE logic |
| **Lot size from API** | `optionchain` response | **Build wrapper** | Extract `lot_size` from chain response; add to `OpenAlgoClient` |
| **Strike interval from chain** | `optionchain` response | **Build wrapper** | Extract from chain `strike_interval` field |
| **Spread strike selection** | `Nifty_credit_spread…py::calc_put_spread_strikes()` | **Extract + generalise** | Move to `framework/spread_template.py`; accept `short_offset`, `hedge_width` params |
| **Option symbol builder** | `Nifty_credit_spread…py::build_option_symbol()` | **Extract + reuse** | Already parametric |
| **1-min history fetch + warm-up** | `Nifty_credit_spread…py::fetch_and_compute_indicators()` | **Extract + generalise** | Uses `client.history(source="db"/"api")` + pandas resample |
| **M15 candle resampling** | `Nifty_credit_spread…py` | **Extract** | pandas resample pattern |
| **EMA(21), EMA(34)** | `backend/services/indicators/calculators.py::ema_from_series / ema_step` | **Vendor copy** | Copy `calculators.py` to `strategy_framework/framework/indicators.py`; no deps |
| **RSI(14)** | `calculators.py::rsi_seed / rsi_step` | **Vendor copy** | Same file |
| **EMA of RSI** | Composable from `ema_step` | **Vendor copy** | Apply `ema_step` to RSI values |
| **Supertrend(11, 2.5)** | `ta.supertrend` (OpenAlgo SDK) / `calculators.py` has ATR | **Use ta module for warm-up; vendor for incremental** | Full warm-up: `ta.supertrend(high,low,close,11,2.5)` on DataFrame; incremental: custom |
| **Volume EMA(21)** | `calculators.py::ema_step` | **Vendor copy** | Applied to volume Series |
| **N-check confirmation counter** | Not in any existing module | **Build new** | `ConfirmationCounter` class in `framework/signal.py` |
| **Cross-above / cross-below detection** | Not in any existing module | **Build new** | 2-bar comparison helper in `framework/signal.py` |
| **Margin check** | `framework/openalgo_client.py::get_margin()` | **Reuse as-is** | Already implemented |
| **Funds check** | `framework/openalgo_client.py::get_funds()` | **Reuse as-is** | Already implemented |
| **Paper trading** | `paper/paper_engine.py::PaperEngine` | **Reuse + extend** | Add bid/ask fill price option |
| **PAPER / LIVE mode routing** | `framework/execution_gateway.py::ExecutionGateway` | **Extend** | Add 4-mode enum (MONITOR_ONLY, ENTRY_EXEC_MONITOR, EXIT_EXEC_MONITOR, FULL_EXEC); add SHORT-first exit path; add modify-to-limit retry |
| **Execution modes (4)** | Not in any existing module | **Build new** | Enum + gateway extension |
| **BUY-first entry** | `framework/execution_gateway.py::execute_legs_buy_first()` | **Reuse** | Credit spread: hedge (BUY) first |
| **SHORT-first exit** | Not yet in ExecutionGateway | **Extend** | Add `close_legs_short_first()` method |
| **Order retry + modify to limit** | `framework/execution_gateway.py` | **Extend** | Add `modify_to_limit()` call on retry |
| **Fill polling** | `framework/execution_gateway.py::_poll_for_fill()` | **Reuse as-is** | |
| **Position recovery (restart)** | `framework/recovery_manager.py::RecoveryManager` | **Extend** | Subclass for credit spread 2-leg structure |
| **State persistence (JSON)** | `framework/recovery_manager.py::save_local_snapshot()` | **Extend** | Add credit spread fields |
| **Strategy state machine** | `models/strategy_state.py::StrategyState` | **Extend** | Add: trailing_sl_level, initial_sl_level, confirmation_count, profit_ladder_stage, execution_mode |
| **Monitoring loop** | `framework/monitor.py::MonitoringEngine` | **Reuse as-is** | Generic; works for any eval interval |
| **Heartbeat logging** | `framework/monitor.py` | **Reuse as-is** | Already structured |
| **MTM calculation** | `models/strategy_state.py::current_mtm` + `paper/paper_engine.py::current_mtm` | **Reuse as-is** | |
| **MTM trailing SL ratchet** | Not in any module | **Build new** | `TrailingSlManager` in `framework/trailing.py` |
| **MTM step-trail** | Not in any module | **Build new** | Part of `TrailingSlManager` |
| **Spot step-trail** | Not in any module | **Build new** | Part of `TrailingSlManager` |
| **Indicator trail (Supertrend)** | Not in any module | **Build new** | Part of `TrailingSlManager` |
| **Profit booking ladder** | Not in any module | **Build new** | `ProfitLadder` in `framework/trailing.py` |
| **Signal logger + structured events** | `utils/logger.py` | **Extend** | Add `log_event()` / `raise_alert()` helpers; per-instance rotating log |
| **Notifier interface** | `backend/alerts/notification_engine.py` — **do not import directly** | **Build new lightweight version** | `Notifier` ABC + `LogNotifier` impl; DB outbox; Telegram as plugin |
| **DB outbox (alerts)** | Not in strategy_framework | **Build new** | SQLite table in `credit_spread_db.py` |
| **strategy_db pattern (SQLite)** | `OpenAlgo/strategies/strategy_db.py` | **Adopt pattern, build new** | `credit_spread_db.py` with schema for multi-leg, multi-instance credit spreads |
| **Strategy instance DB** | Not in strategy_framework | **Build new** | `strategy_instances` table; soft-delete; instance IDs |
| **Structured strategy legs DB** | `strategy_db.py` (2-leg) | **Extend pattern** | `strategy_legs` table with role field |
| **Monitor state persistence** | Not in strategy_framework | **Build new** | `monitor_state` table: confirmation counters, trailing SL, ladder stage, last candle ts |
| **MTM snapshot history** | Not in strategy_framework | **Build new** | `mtm_snapshots` table |
| **Alert/event audit trail** | Not in strategy_framework | **Build new** | `strategy_alerts` + `strategy_events` tables |
| **Market holiday check** | Not in strategy_framework | **Build wrapper** | `client.market_holidays()` + `market_timings()` |
| **Eval interval (LIVE/15s/30s/1m etc.)** | `framework/monitor.py::interval` param | **Reuse + document** | Already configurable in seconds |
| **1-min candle close wait** | Not in any module | **Build new** | `wait_for_next_minute_close()` helper |
| **WebSocket subscription** | `framework/openalgo_client.py` — NOT yet | **Extend** | Add WS subscribe/unsubscribe wrappers; fallback to REST |
| **Stale data detection** | Not in any module | **Build new** | Timestamp check on LTP: alert if no tick > configurable threshold |
| **Multiple instances isolation** | `MonitoringEngine` is single-strategy | **Design new** | `StrategyRunner` supervisor — one thread/worker per instance |
| **Multiple monitors per instance** | Not in any module | **Design new** | `MonitorAttachment` model; exec-rights flag |
| **Graceful shutdown (SIGTERM)** | `framework/monitor.py` | **Reuse as-is** | Already handles SIGINT/SIGTERM |
| **OpenAlgo hosting compatibility** | Already followed in existing strategies | **Follow pattern** | Read `OPENALGO_API_KEY` and `OPENALGO_HOST` from env; config YAML as fallback |
| **Standalone + hosted dual-mode** | Pattern from credit spread example | **Follow pattern** | Same script; env vars differ between modes |
| **Config-driven rules (no if-chains)** | Partial — condition_tree exists in backend | **Build new lightweight version** | `RuleSet` + `ConditionEvaluator` in `framework/rules.py`; JSON/YAML serialisable |
| **Leg role (PRIMARY/HEDGE)** | `models/option_leg.py::LegRole` | **Extend enum** | Add `PRIMARY` and `HEDGE` to `LegRole`; current values are LONG_CALL/SHORT_CALL/LONG_PUT/SHORT_PUT |
| **Volume source config (FUT/LEG)** | Not in any module | **Build new** | `VolumeConfig` in config schema; `volume_monitor.py` |
| **Position sizing (lots)** | Not in any module | **Build new** | `PositionSizer` in `framework/sizing.py`; pluggable |
| **Capital / margin-short handling** | Not in any module | **Build new** | Wrapper in `framework/sizing.py` |
| **IST timezone** | `utils/helper.py::now_ist()` | **Reuse as-is** | Already uses `ZoneInfo("Asia/Kolkata")` |
| **Partial fill detection** | Not in any module | **Build new** | Compare `filled_qty` vs `requested_qty` in gateway |
| **Idempotent order tags** | `strategy` param on `placeorder()` | **Extend** | Deterministic tag: `f"{instance_id}_{leg_role}_{action}"` |
| **Freeze qty split** | Not in any module | **Build new** | Check qty vs freeze limit (from `qtyfreeze.csv`) |
| **Telegram notifier** | `openalgo_client.py` exposes `client.telegram()` | **Reuse as plugin** | Wrap in `TelegramNotifier` impl of `Notifier` ABC |

---

## 4. Risks of Regression

### 4.1 Files That Must Not Change

| File / Module | Reason |
|---|---|
| `backend/alerts/` (entire directory) | Used by the running FastAPI server; any change risks breaking the live alert engine |
| `backend/services/indicators/calculators.py` | Used by the FastAPI indicator pipeline; copy don't modify |
| `backend/alembic/versions/` | Existing migrations; only add new migration files |
| `strategy_framework/strategies/iron_fly/` | Existing, deployed strategy; do not change |
| `strategy_framework/framework/openalgo_client.py` | Iron fly uses this; only add methods, never change signatures |
| `strategy_framework/framework/execution_gateway.py` | Iron fly uses this; only add methods/modes |
| `strategy_framework/base/base_strategy.py` | Iron fly inherits this; only add optional hooks with no-op defaults |
| `strategy_framework/models/strategy_state.py` | Iron fly uses this; only add fields with defaults |
| `strategy_framework/models/option_leg.py` | Iron fly uses this; extend `LegRole` enum additively |

### 4.2 Safe Extension Points

| What | How |
|---|---|
| Add credit spread config keys | New YAML file; extend `Config._REQUIRED_KEYS` only if subclassing `Config` |
| Add methods to `OpenAlgoClient` | Purely additive; iron fly won't call them |
| Extend `ExecutionGateway` | Add `close_legs_short_first()` and `execute_mode` param; existing methods unchanged |
| Extend `StrategyState` | Add fields with `field(default=...)` — dataclass defaults are safe |
| Add to `LegRole` enum | Additive; Python enums are open |
| New DB tables | New Alembic migration file (011+); no existing table altered |
| New `framework/` modules | No impact on existing code |
| `BaseStrategy` new optional hooks | Default no-op implementations; iron fly is unaffected |

### 4.3 Shared Files That Need Care

| File | Risk | Mitigation |
|---|---|---|
| `strategy_framework/framework/openalgo_client.py` | Iron fly + credit spread both use it | Add methods only; do not change existing method signatures or return types |
| `strategy_framework/utils/constants.py` | Shared constants | Add new constants; do not rename/change existing ones |
| `strategy_framework/utils/logger.py` | Shared logger | Add helpers; do not change `setup_logger` or `PaperLogAdapter` signatures |
| `strategy_framework/requirements.txt` | Shared dependencies | Add `pandas` and `numpy` (not currently listed but used by examples) |

---

## 5. What Must Be Built New (Summary)

### New files in `strategy_framework/framework/`:

| File | Purpose |
|---|---|
| `indicators.py` | Vendored copy of `backend/services/indicators/calculators.py` + Supertrend incremental |
| `candle_builder.py` | 1-min tick stream → OHLCV candle close; warm-up from history |
| `signal.py` | `ConfirmationCounter`, `crossover_above/below()`, `check_volume_condition()` |
| `trailing.py` | `TrailingSlManager` (MTM step, spot step, indicator trail), `ProfitLadder` |
| `sizing.py` | `PositionSizer`, margin-short handling |
| `spread_template.py` | `SpreadTemplate`, `select_spread_strikes()`, leg role assignment |
| `expiry_utils.py` | `select_expiry()` (extracted from credit spread example), lot size fetcher |
| `rules.py` | Lightweight `RuleSet` + `ConditionEvaluator` (JSON/YAML serialisable) |
| `notifier.py` | `Notifier` ABC, `LogNotifier`, `TelegramNotifier`, DB outbox |
| `market_utils.py` | Trading window check, holiday check, freeze qty check |

### New files in `strategy_framework/`:

| File | Purpose |
|---|---|
| `credit_spread_db.py` | SQLite DB for instances, legs, orders, monitor state, MTM snapshots, alerts |
| `alembic migration 011` (backend) | New DB tables for strategy_instances, etc. (if using Trademan backend DB) |

### New strategy files:

| Path | Purpose |
|---|---|
| `strategies/credit_spread/strategy_execution/execution_index_credit_spread_directional.py` | Entry script |
| `strategies/credit_spread/strategy_execution/config/execution_index_credit_spread_directional.yaml` | Entry config |
| `strategies/credit_spread/strategy_monitoring_adjustment/monitoring_index_credit_spread_directional_weekly.py` | Monitoring script |
| `strategies/credit_spread/strategy_monitoring_adjustment/config/monitoring_index_credit_spread_directional_weekly.yaml` | Monitoring config |
| `tests/` | Full test suite |
| `docs/CREDIT_SPREAD_DESIGN.md` | Phase 1 design doc |

---

## 6. Open Questions Found During Audit

1. **`client.history()` `source="db"`**: Requires Historify to have run and populated DuckDB. Need to confirm DuckDB is populated for NIFTY 1m before strategy starts, or always use `source="api"` as primary.

2. **Lot size from API**: The `optionchain` response returns LTPs per strike but `lot_size` may not always be in the response. Need to confirm the exact field name in live data. Fallback: `expiry()` or a hardcoded map (risk: wrong on lot size change).

3. **NIFTY futures volume symbol**: For volume exit condition using `NIFTY_FUT`, the symbol for current-month NIFTY futures needs to be constructed dynamically (current month expiry). The expiry API gives option expiries — confirm it also covers futures, or use a separate lookup.

4. **Freeze quantity limit**: NIFTY options freeze qty is 1,800 shares (≈ 27 lots at 65/lot). Confirm current value from `qtyfreeze.csv` (`OpenAlgo/data/qtyfreeze.csv`). Our strategy is unlikely to exceed this for 1–5 lots, but the split-order path must be coded.

5. **`modifyorder` availability**: The `modifyorder` endpoint is in the SDK. Whether the broker supports mid-fill modifications for NRML options orders needs to be tested. If not, the retry path falls back to cancel + re-place.

6. **RedisAvailability**: Trademan backend uses Redis for pub/sub. The strategy_framework currently uses **no Redis** (SQLite + JSON snapshots). For Phase 2 this is fine; if we later want shared LTP across strategies we can add Redis. No Redis dependency for Phase 2.

7. **`client.market_timings()` response format**: Need to confirm the exact JSON shape to implement the trading window check correctly.
