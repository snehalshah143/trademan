# TRADEMAN — Technical Reference Document

> Single source of truth for architecture, features, design decisions, and development history.
> Update this document whenever a significant feature is added or a design decision is revisited.

---

## Table of Contents

1. [Product Overview](#1-product-overview)
2. [Target Use Cases](#2-target-use-cases)
3. [Technology Stack](#3-technology-stack)
4. [Repository Layout](#4-repository-layout)
5. [Build Phase Status](#5-build-phase-status)
6. [Feature Inventory](#6-feature-inventory)
7. [Frontend Architecture](#7-frontend-architecture)
8. [Backend Architecture](#8-backend-architecture)
9. [Real-Time Data Pipeline](#9-real-time-data-pipeline)
10. [Candle Construction](#10-candle-construction)
11. [Alert System](#11-alert-system)
12. [Order Execution Engine](#12-order-execution-engine)
13. [Database Design](#13-database-design)
14. [Redis Architecture](#14-redis-architecture)
15. [Broker Adapter Pattern](#15-broker-adapter-pattern)
16. [Design Decisions and Discussions](#16-design-decisions-and-discussions)
17. [Known Gaps and Limitations](#17-known-gaps-and-limitations)
18. [Future Roadmap](#18-future-roadmap)
19. [Dev Environment and Commands](#19-dev-environment-and-commands)
20. [Critical Conventions](#20-critical-conventions)

---

## 1. Product Overview

TRADEMAN is a self-hosted F&O (Futures & Options) strategy and position management platform built for Indian derivatives traders. It is broker-agnostic via an adapter pattern, currently targeting OpenAlgo as the primary broker integration.

**Key goals:**
- Build, analyze, and manage multi-leg F&O strategies (Iron Condors, Spreads, Straddles, etc.)
- Real-time MTM (Mark-to-Market) tracking across all legs
- Chartink-style conditional alert system — trigger notifications when price, MTM, indicator, or candle conditions are met
- Payoff diagram visualization (AlgoTest-style)
- Automated order entry and exit with correct margin-aware sequencing

**Scale target (v1):** 100 users × 10 positions × 4 legs = 4,000 active legs

---

## 2. Target Use Cases

### 2.1 Strategy Builder
- User selects an underlying (NIFTY, BANKNIFTY, FINNIFTY, SENSEX, individual stocks)
- Adds legs by selecting strike, option type (CE/PE/FUT), expiry, lots, and side (BUY/SELL)
- Loads prebuilt strategies (Iron Condor, Bull Put Spread, Bear Call Spread, Straddle, Strangle, etc.)
- Visualizes payoff diagram at expiry and at current date (time-value adjusted)
- Names and saves the strategy

### 2.2 Position Manager
- Groups active legs into named strategies
- Shows live MTM per leg and per strategy
- LTP updates in real-time via WebSocket
- Flash animation on price change (green for up, red for down)

### 2.3 Alert Engine
- User defines rules: "Notify me when Strategy MTM drops below -3000" or "When NIFTY 15m RSI crosses above 70"
- Rules are a recursive condition tree (AND/OR nested groups)
- Evaluation happens on every LTP tick — real-time, not polled
- Notifications: popup, sound, Telegram, email, webhook

### 2.4 Order Execution
- One-click entry: places BUY legs first, waits for fills, then places SELL legs
- One-click exit: reverses SELL legs first, waits for fills, then exits BUY legs
- Margin-aware sequencing ensures broker recognises hedged position before net debit

---

## 3. Technology Stack

| Layer | Technology |
|---|---|
| Frontend | React 18, TypeScript, Vite, Zustand, TanStack Query v5, Tailwind CSS |
| Backend | FastAPI, Python 3.12, asyncio, Pydantic v2, SQLAlchemy 2 async |
| Hot data layer | Redis 7 (LTP cache, pub/sub fan-out) |
| Persistent storage | TimescaleDB / PostgreSQL 16 (production), SQLite (dev) |
| Migrations | Alembic (manages relational tables AND TimescaleDB hypertables) |
| DB drivers | asyncpg (PostgreSQL), aiosqlite (SQLite) |
| Redis driver | redis[asyncio] + hiredis |
| Broker | OpenAlgo adapter (REST port 5000, WS port 8765) |
| Dev broker | MockAdapter — simulates ticks every 0.5s |
| Deployment | Docker Compose (TimescaleDB + Redis + backend + frontend) |

---

## 4. Repository Layout

```
trademan/
  frontend/                         React app — npm run dev (port 3000)
    src/
      types/
        domain.ts                   All TypeScript domain types
        alertRules.ts               Alert rule condition tree types + METRIC_CONFIGS
      store/
        ltpStore.ts                 Zustand — real-time LTP + flash detection
        strategyStore.ts            Zustand — strategies, persisted to localStorage
        alertStore.ts               Zustand — alert event log
      adapters/
        broker.adapter.ts           BrokerAdapter ABC + OpenAlgoAdapter + MockAdapter
        AdapterContext.tsx          Runtime-switchable adapter context
      hooks/
        useMarketWebSocket.ts       WS hook with exponential backoff reconnect
      lib/
        payoff.ts                   Pure payoff computation engine
        execution.ts                Sequenced order placement engine
        utils.ts                    INR formatting, color helpers
      components/
        layout/
          Sidebar.tsx               Navigation sidebar with alert badge
        AlertManager/
          AlertDashboard.tsx        Alert manager tabbed container
          AlertList.tsx             Per-strategy alert rule list + CRUD
          AlertRuleBuilder.tsx      Chartink-style condition tree builder UI
          AlertsByPosition.tsx      All strategies grouped with their alert counts
          AlertHistory.tsx          Alert trigger event log
          AlertTemplates.tsx        Preset alert templates
        Monitor/                    Position monitor components
      pages/                        Top-level page components
      services/
        alertService.ts             REST API client for alert CRUD
        monitorService.ts           REST API client for monitor endpoints
      utils/                        Shared frontend utilities

  backend/                          FastAPI app — uvicorn main:app --reload (port 8000)
    main.py                         Lifespan startup, Redis-WS bridge, alert engine wiring
    core/
      config.py                     Pydantic Settings — all config from env vars
      database.py                   SQLAlchemy async engine + AsyncSessionLocal
    models/
      relational.py                 Strategy, StrategyLeg, Order, AlertRule, AlertEvent
      timeseries.py                 Candle, MTMSnapshot, LTPTick (hypertables)
    alerts/
      alert_engine.py               Tick-driven rule evaluator — called on every LTP tick
      alert_cache.py                In-memory rule cache + evaluation context store
      rule_evaluator.py             Recursive condition tree evaluator (pure functions)
      models.py                     AlertRule, AlertEvent Pydantic + ORM models
    monitors/
      monitor_engine.py             Monitored position engine
    services/
      redis_service.py              Redis operations — LTP cache, pub/sub, batch reads
      candle_repository.py          TimescaleDB candle CRUD + MTM snapshots
      mtm_tracker.py                Async background task — MTM snapshot every 15s
      ltp/
        ltp_service.py              Subscribe OpenAlgo WS → Redis fan-out → CandleBuilder
        candle_builder.py           Tick → OHLCV candles for all timeframes
      execution/
        execution_service.py        BUY-first / SELL-first-on-exit order sequencing
      alert/
        alert_service.py            Alert rule CRUD, notification dispatch
    adapters/
      broker_adapter.py             BrokerAdapter ABC
      openalgo_adapter.py           Full OpenAlgo REST + WS implementation
      mock_adapter.py               Mock for dev/testing — random walk ticks
      adapter_factory.py            Returns correct adapter from settings
    ws/
      hub.py                        WebSocket connection manager + broadcast
      endpoint.py                   WS route — subscribes Redis, fans out to clients
    api/routes/
      alert_rules.py                Alert rule CRUD endpoints
      monitor_alerts.py             Monitor alert endpoints
      monitored_positions.py        Monitored positions endpoints
      strategies.py                 Strategy CRUD
      orders.py                     Order history
      positions.py                  Proxy to OpenAlgo positions
      alerts.py                     Alert event log
      settings.py                   Broker config endpoint
    alembic/
      versions/
        001_initial_schema.py       Core relational tables
        002_timescale_extension.py  Enable TimescaleDB extension
        003_hypertables.py          Convert candles/mtm_snapshots/ltp_ticks to hypertables
        004_compression_policies.py TimescaleDB columnar compression after 7 days
        005_retention_policies.py   Auto-drop data older than configured retention
        006_continuous_aggregates.py candles_1h materialized view from candles_5m
        007_alert_rules.py          AlertRule + AlertEvent tables
        008_monitored_positions.py  MonitoredPosition table

  docker-compose.yml
  CLAUDE.md                         Claude Code context file (always loaded)
  TRADEMAN_TECHNICAL_REFERENCE.md   This document
```

---

## 5. Build Phase Status

| Phase | Name | Status |
|---|---|---|
| Phase 1 | Foundation — types, stores, adapters, payoff, execution scaffolded | COMPLETE |
| Phase 2 | Backend core — FastAPI, DB models, migrations, REST routes | COMPLETE |
| Phase 3 | Data layer — Redis, LTPService, CandleBuilder, MTM tracker, alert service | COMPLETE |
| Phase 4 | OpenAlgo wiring — full broker adapter, WS integration | COMPLETE (scaffold + mock) |
| Phase 5 | Frontend — Strategy Builder, Position Manager, Alert Manager | IN PROGRESS |
| Phase 6 | Strategy Automation Engine — indicator engine, signal evaluator | NOT STARTED |
| Phase 7 | Multi-user auth / RBAC | NOT STARTED |
| Phase 8 | Telegram + Email alert delivery | NOT STARTED |

---

## 6. Feature Inventory

### 6.1 Strategy Builder (Frontend)
- Multi-leg strategy construction with underlying selector
- Supports NIFTY, BANKNIFTY, FINNIFTY, SENSEX, individual stocks
- Leg form: strike, CE/PE/FUT, expiry, lots, product (MIS/NRML), side (BUY/SELL)
- Real-time LTP feed per leg from WebSocket
- Live P&L per leg and total MTM in footer
- Payoff diagram: expiry payoff + current date payoff line (AlgoTest style)
- Option chain tab: ATM highlighted, click strike to add leg
- Prebuilt strategy templates: Iron Condor, Bull Put Spread, Bear Call Spread, Straddle, Strangle, Synthetic Long/Short

### 6.2 Position Manager
- Strategies grouped and displayed with all legs
- Real-time LTP updates with flash animation (green up, red down) via Zustand ltpStore
- MTM calculated per leg: `(LTP - entry) × qty × side_multiplier`
- Strategy-level MTM = sum of all leg MTMs
- Strategies persisted to localStorage via Zustand persist middleware

### 6.3 Alert System — Full Feature Set
See Section 11 for full specification.

**Condition Scopes:**
- STRATEGY — MTM, MTM%, Max Profit Hit, Max Loss Hit
- LEG — LTP, Premium Change %, Premium Change ₹, Leg P&L
- SPOT — Spot Price, Spot Change %, Spot vs VWAP
- INDICATOR — EMA, SMA, Supertrend, RSI, MACD Histogram, Bollinger Upper/Lower, VWAP, ATR, ADX, Stochastic %K
- CANDLE — Open, High, Low, Close, Volume, Prev Close, Body Size, Upper Shadow, Lower Shadow, Change from Open %, Change from Prev Close %

**Supported Timeframes (all aligned to 09:15 IST market open):**
- 1m, 3m, 5m, 15m, 75m (intraday)
- 1d (session: 09:15–15:30), 1w (Monday–Friday), 1M (calendar month)

**Operators:** ≥, >, ≤, <, =, crosses above, crosses below

**Condition tree:** Recursive AND/OR nested groups (unlimited depth)

**Notification channels:** Popup (in-app), Sound, Telegram, Email, Webhook

**Trigger settings:** trigger-once or repeat, cooldown in seconds

**UI:** Chartink-style — each condition is a horizontal card with labeled columns (TARGET | METRIC | PARAMS | TIMEFRAME | OPERATOR | VALUE), AND/OR connector pills between conditions, collapsible sub-groups

### 6.4 Order Execution
- Entry: BUY legs simultaneously → poll fills every 500ms → SELL legs simultaneously
- Exit: SELL exits simultaneously → poll fills every 500ms → BUY exits simultaneously
- 30-second timeout per phase
- BUY rejection aborts entire entry — SELL legs never placed if any BUY fails
- Products: MIS (intraday) and NRML (carry-forward)

### 6.5 Candle Builder
- Processes every LTP tick into OHLCV candles for all 8 timeframes simultaneously
- Closed candles persisted to DB via CandleRepository
- `register_on_close()` callback system for future indicator engine subscription
- `get_live()` returns the currently building (unconfirmed) candle for any symbol+timeframe

### 6.6 MTM Tracker
- Background asyncio task — takes a snapshot of all strategy MTMs every 15 seconds
- Writes to `mtm_snapshots` TimescaleDB hypertable
- Used for P&L history chart (future feature)

### 6.7 Settings Page
- Adapter toggle: Mock / OpenAlgo
- OpenAlgo Host, WS Host, API Key fields
- Test Connection button with live status feedback
- System Status: backend health, broker connectivity, Redis status

---

## 7. Frontend Architecture

### 7.1 State Management Split

| Store | Library | What it holds |
|---|---|---|
| ltpStore | Zustand | Real-time LTP prices + previous prices for flash detection |
| strategyStore | Zustand + localStorage | Strategy and leg definitions, persisted across sessions |
| alertStore | Zustand | Alert event log (triggered notifications in session) |
| Server data | TanStack Query | All REST data (alert rules, orders, positions, history) |

**Rule:** Real-time data that changes on every tick → Zustand. Data that comes from REST API → TanStack Query. Never mix.

### 7.2 WebSocket Hook
`useMarketWebSocket.ts` — connects to `ws://backend/ws`, receives JSON tick messages, dispatches to `ltpStore`. Implements exponential backoff reconnect (initial 1s, max 30s). Broadcasts `STALE_WARNING` message if no tick arrives within threshold.

### 7.3 TanStack Query Cache Keys
Alert rules use two distinct cache keys that must be cross-invalidated:
- `['alert-rules', strategyId]` — used by `AlertList` (per-strategy view)
- `['alert-rules-all']` — used by `AlertsByPosition` (all strategies view)

**Critical:** Both keys must be invalidated in every mutation's `onSuccess` handler. This was a bug that caused newly created rules to not appear in the By Position view.

### 7.4 Component Hierarchy (Alert Manager)
```
AlertDashboard (tabs)
  ├── AlertsByPosition
  │     └── PositionGroup (per strategy)
  │           ├── AlertRow (per rule, inline)
  │           └── Dialog → AlertList
  │                         └── Dialog → AlertRuleBuilder
  ├── AlertHistory
  └── AlertTemplates
```

---

## 8. Backend Architecture

### 8.1 Startup Sequence (`main.py` lifespan)
1. Connect to database (create tables if needed)
2. Connect to Redis
3. Start LTPService (subscribes to broker WS or starts poll fallback)
4. Start Redis→WS bridge background task (reads `ltp:ticks` pub/sub channel)
5. Start AlertEngine (loads rules and strategy legs into memory)
6. Start MonitorEngine
7. Start MTM tracker background task

### 8.2 Request Flow
```
Browser → REST API → FastAPI route → Service layer → DB (SQLAlchemy async)
Browser → WS /ws → hub.py → receives real-time tick broadcasts
```

### 8.3 Tick Flow (critical path — see Section 9 for detail)
```
Broker WS → LTPService → Redis → pub/sub → main.py bridge → AlertEngine + WS hub
```

---

## 9. Real-Time Data Pipeline

```
OpenAlgo WebSocket (port 8765)
        │
        ▼
LTPService._on_tick(symbol, ltp, ts)
        │
        ├── redis_service.hmset(market:tick:{symbol}, {ltp, change, ts})
        ├── redis_service.hmset(market:ltp, {symbol: ltp})           ← batch LTP cache
        ├── redis_service.publish("ltp:ticks", {symbol, ltp, ts})    ← fan-out channel
        └── candle_builder.ingest(symbol, ltp, 0, ts)                ← all 8 timeframes
                │
                └── on period boundary: repo.insert_candle() + on_close callbacks

Redis pub/sub "ltp:ticks"
        │
        ▼
main.py background listener (asyncio task)
        │
        ├── hub.broadcast({type:"tick", symbol, ltp})    → all WS browser clients
        └── alert_engine.on_tick(symbol, ltp)
                │
                ├── look up affected strategy_ids from _symbol_index
                ├── _build_context(): reads sibling leg LTPs from Redis (HGETALL)
                │   + reads live candle state from candle_builder.get_live()
                └── evaluate_group(condition_tree, ctx, prev_ctx)
                        └── fire notification if triggered
```

**Frequency:** Event-driven — fires on every broker tick (~1/sec in OpenAlgo). No polling interval.

**Staleness watchdog:** If no tick arrives for `ltp_stale_threshold_seconds` (default 10s), broadcasts `STALE_WARNING` to all WS clients. Frontend shows a stale indicator.

**Poll fallback:** If `adapter.subscribe_ws()` raises (e.g., broker offline), LTPService falls back to polling `adapter.get_ltp()` at `ltp_poll_interval_seconds` (default 1s).

---

## 10. Candle Construction

### 10.1 Supported Timeframes

| Timeframe | Type | Period | Candles/Day |
|---|---|---|---|
| 1m | Intraday | 1 minute | 375 |
| 3m | Intraday | 3 minutes | 125 |
| 5m | Intraday | 5 minutes | 75 |
| 15m | Intraday | 15 minutes | 25 |
| 75m | Intraday | 75 minutes | 5 |
| 1d | Calendar | Full session | 1 |
| 1w | Calendar | Mon–Fri week | ~1/week |
| 1M | Calendar | Calendar month | ~1/month |

### 10.2 Boundary Logic

All **intraday** timeframes are anchored to market open at **09:15 IST**:
```
candle_idx = floor((tick_ts - 09:15) / period_minutes)
candle_start = 09:15 + candle_idx × period_minutes
```

75-minute candles fall exactly on session boundaries:
- 09:15, 10:30, 11:45, 13:00, 14:15 → 15:30 (5 × 75 = 375 min = full session)

**1d:** One candle per session. `start = 09:15`, `end = 15:30` (same day)

**1w:** Rolls to Monday 09:15 of the current calendar week. `end = next Monday 09:15`

**1M:** Rolls to 1st of current month at 09:15. `end = 1st of next month 09:15`

### 10.3 Why 1h and 4h are NOT supported

60-minute candles anchored to 09:15 would end at 16:15 — 45 minutes after market close. Hourly candles do not align to NSE session boundaries. 75m is the standard NSE-aligned intraday timeframe. `1h` and `4h` options were removed from the frontend to prevent silent alert failures.

### 10.4 Removed Timeframes

Timeframes `10m`, `30m`, `1h`, `4h` were removed from the frontend dropdown. They had been in the type definition but the backend never built these candles — conditions using them silently returned False on every tick.

### 10.5 Candle State
Live (unconfirmed) candles are held in memory in `CandleBuilder._live` dict keyed by `(symbol, timeframe)`. Closed candles are persisted to the `candles` DB table. The alert engine reads live candles via `get_live()` — not from DB — so alert conditions evaluate against the current in-progress candle.

---

## 11. Alert System

### 11.1 Data Model

```
AlertRule
  alert_id          UUID primary key
  strategy_id       FK → Strategy
  name              string
  description       string
  is_active         bool
  trigger_once      bool (auto-disable after first trigger)
  cooldown_secs     int (minimum seconds between re-triggers)
  triggered_count   int
  last_triggered    datetime
  notify_popup      bool
  notify_telegram   bool
  notify_email      bool
  notify_sound      bool
  notify_webhook    bool
  webhook_url       string
  telegram_chat_id  string
  condition_tree    JSONB (recursive ConditionGroup)

AlertEvent
  event_id          UUID
  alert_id          FK → AlertRule
  strategy_id       string
  triggered_at      datetime
  condition_summary string (human-readable at time of trigger)
  context_snapshot  JSONB (MTM, LTPs at trigger moment)
```

### 11.2 Condition Tree Structure

```json
{
  "id": "uuid",
  "op": "AND",
  "conditions": [
    {
      "id": "uuid",
      "scope": "STRATEGY",
      "metric": "MTM",
      "operator": "LTE",
      "value": -3000,
      "leg_id": null,
      "timeframe": null,
      "params": {}
    }
  ],
  "groups": [
    {
      "id": "uuid",
      "op": "OR",
      "conditions": [...],
      "groups": []
    }
  ]
}
```

### 11.3 Condition Scopes and Metrics

**STRATEGY scope:**
| Metric | Description |
|---|---|
| MTM | Net strategy P&L in ₹ |
| MTM_PCT | Strategy P&L as % of deployed capital |
| MAX_PROFIT | Highest MTM reached this session |
| MAX_LOSS | Deepest drawdown (absolute ₹, so `MAX_LOSS >= 5000` means loss exceeded 5k) |

**LEG scope:**
| Metric | Description |
|---|---|
| LTP | Current last traded price of the leg instrument |
| PREMIUM_CHG_PCT | (LTP - entry) / entry × 100 |
| PREMIUM_CHG_ABS | LTP - entry in ₹ |
| LEG_PNL | side_multiplier × (LTP - entry) × quantity |

**SPOT scope:**
| Metric | Description |
|---|---|
| SPOT_PRICE | Underlying index/spot price |
| SPOT_CHG_PCT | (spot - prev_close) / prev_close × 100 |
| SPOT_VS_VWAP | spot - VWAP |

**INDICATOR scope** (all require timeframe, most have params):
EMA (period), SMA (period), Supertrend (period, factor), RSI (period), MACD Histogram (fast, slow, signal), Bollinger Upper (period, std), Bollinger Lower (period, std), VWAP, ATR (period), ADX (period), Stochastic %K (k, d)

Note: Indicator values are read from `ctx["indicators"]` which is populated from Redis `indicator:{symbol}:{tf}` hash. This hash is not yet written (Strategy Automation Engine is Phase 6). Indicator conditions will silently not fire until Phase 6 is built.

**CANDLE scope** (all require timeframe):
OPEN, HIGH, LOW, CLOSE, VOLUME, PREV_CLOSE, BODY_SIZE (|C-O|), UPPER_SHADOW (H-max(O,C)), LOWER_SHADOW (min(O,C)-L), CHG_FROM_OPEN%, CHG_FROM_PREV%

### 11.4 Operators

| Operator | Symbol | Notes |
|---|---|---|
| GTE | ≥ | Standard comparison |
| GT | > | Standard comparison |
| LTE | ≤ | Standard comparison |
| LT | < | Standard comparison |
| EQ | = | Uses epsilon comparison (abs diff < 1e-9) |
| CROSS_ABOVE | crosses above | Requires prev_ctx — prev < threshold ≤ current |
| CROSS_BELOW | crosses below | Requires prev_ctx — prev > threshold ≥ current |

CROSS operators are only shown in the UI for scopes where crossing is meaningful: SPOT, INDICATOR, CANDLE. They are hidden for STRATEGY and LEG scopes.

### 11.5 Evaluation Engine

Called on every tick via `alert_engine.on_tick(symbol, ltp)`:

1. Look up `_symbol_index[symbol]` → list of strategy_ids with a leg for this symbol
2. For each strategy_id:
   a. Check `alert_cache.get_rules_for_strategy(strategy_id)` — skip if no rules
   b. `_build_context()` — build evaluation context:
      - MTM from leg LTPs (tick price for current symbol, Redis cache for others)
      - Spot from Redis cache for the underlying symbol
      - Live candle OHLCV from `candle_builder.get_live()` for all 8 timeframes
      - Indicators dict from Redis (empty until Phase 6)
   c. `evaluate_group(tree, current_ctx, prev_ctx)` — recursive AND/OR evaluation
   d. If triggered: check cooldown, check trigger_once, fire notifications, write AlertEvent

### 11.6 Alert Cache
`alert_cache` (module-level singleton) holds:
- All active alert rules indexed by strategy_id (reloaded on CRUD operations)
- Rolling two-tick context per strategy (current + previous) — needed for CROSS operators

### 11.7 Known Alert Engine Gap
Strategies built in the browser via "Group as Strategy" from broker positions are only in Zustand → localStorage. The alert engine's `_load_strategy_legs()` queries the `strategies` and `strategy_legs` DB tables. These localStorage-only strategies are invisible to the alert engine — rules are saved to DB correctly but conditions are never evaluated because the leg-to-symbol index is empty for those strategies.

**Fix required:** Either sync localStorage strategies to DB on creation, or expose an API endpoint to register strategy legs with the alert engine.

---

## 12. Order Execution Engine

### 12.1 Entry Sequencing

```
Step 1: Place all BUY legs simultaneously
Step 2: Poll order status every 500ms until all fills confirmed (30s timeout)
Step 3: If any BUY rejected → abort entirely, surface error, do NOT place SELL legs
Step 4: Place all SELL legs simultaneously
Step 5: Poll fills (500ms, 30s timeout)
```

**Reason for BUY-first:** Most F&O strategies involve buying expensive options (hedges) and selling cheaper ones (premium collection). Brokers only grant margin benefit (lower total margin) once they see the hedging legs. If SELL legs were placed first without the hedge, the broker would block on insufficient margin.

### 12.2 Exit Sequencing

```
Step 1: Place exit orders for all SELL legs simultaneously (buy back short positions)
Step 2: Poll fills (500ms, 30s timeout)
Step 3: Place exit orders for all BUY legs simultaneously (sell out long positions)
```

**Reason for SELL-first exit:** Exiting SELL legs first removes the short position while keeping the long hedge. This ensures the broker never sees an unhedged short during the exit sequence.

### 12.3 Partial Exit
- Partial exit targets specific legs by leg_id
- Same sequencing applies: SELL exits first, then BUY exits
- Remaining legs continue tracking MTM

---

## 13. Database Design

### 13.1 Relational Tables (SQLAlchemy ORM)

**strategies**
- id (UUID PK), name, underlying, status (draft/active/closed), created_at, updated_at

**strategy_legs**
- id (UUID PK), strategy_id (FK), symbol, action (BUY/SELL), product (MIS/NRML), quantity, entry_price, order_id, status

**orders**
- id (UUID PK), strategy_id, leg_id, broker_order_id, action, symbol, quantity, product, order_type, price, status, placed_at, filled_at

**alert_rules**
- id (UUID PK), strategy_id, name, description, is_active, trigger_once, cooldown_secs, condition_tree (JSON), notify_*, triggered_count, last_triggered

**alert_events**
- id (UUID PK), alert_id, strategy_id, triggered_at, condition_summary, context_snapshot (JSON)

### 13.2 TimescaleDB Hypertables

**candles** — OHLCV data, hypertable on (ts), partitioned by symbol
- Timeframes stored: 1m, 3m, 5m, 15m, 75m, 1d, 1w, 1M
- Compression after 7 days (columnar)
- Retention policy: configurable (default 365 days)

**mtm_snapshots** — Strategy MTM snapshots every 15s
- Hypertable on (ts), partitioned by strategy_id
- Used for P&L history charts

**ltp_ticks** — Raw tick archive
- Hypertable on (ts), partitioned by symbol
- Optional — high volume, retention typically 7 days

**candles_1h** — Continuous aggregate materialized from 5m candles
- Available in DB historical queries; NOT available for real-time alert evaluation

### 13.3 SQLite vs PostgreSQL Decision

**Analysis performed (May 2026):**

SQLite is appropriate for:
- Single-user personal deployment
- Development and testing
- No concurrent write contention (SQLite has single-writer limitation)

PostgreSQL + TimescaleDB is required for:
- 2+ concurrent users (multiple writers to orders, strategies tables simultaneously)
- Tick archiving (`ltp_ticks` hypertable — SQLite cannot handle 4,000 symbols × 1 tick/sec × 86,400 sec/day)
- Continuous aggregates (TimescaleDB-specific feature — no-op on SQLite)
- Compression policies (TimescaleDB-specific — no-op on SQLite)
- Query performance at scale (time-bucket queries, indexed hypertables)

**Decision:** Codebase is fully architected for PostgreSQL from day one (asyncpg driver, all migrations written, TimescaleDB-specific SQL in migrations). Switch from SQLite to PostgreSQL is a single `.env` change (`DATABASE_URL`). Alembic migrations skip TimescaleDB operations on SQLite via no-op detection.

---

## 14. Redis Architecture

### 14.1 Key Schema

```
market:ltp                   HASH  {symbol: price}             Hot LTP cache (all symbols)
market:tick:{symbol}         HASH  {ltp, change, ts}           Per-symbol tick detail
ltp:ticks                    CHANNEL (pub/sub)                 Tick fan-out channel
indicator:{symbol}:{tf}      HASH  {ema_21, rsi_14, ...}       Indicator values (Phase 6)
signal:{symbol}:state        STRING BUY|NEUTRAL                Automation signal state
signal:{symbol}:cooldown     STRING 1  EX {seconds}            Auto-TTL cooldown key
strategy:{id}:mtm            STRING {float}                    Hot MTM for WS broadcast
ws:session:{session_id}      HASH  {user_id, subscriptions}    WS session (future auth)
```

### 14.2 Why Redis

1. **Zero DB queries per tick in alert engine:** `_build_context()` fetches all sibling leg LTPs with one `HGETALL market:ltp` call. Without Redis: 4 legs × 1 DB query × 4,000 strategies × 1 tick/sec = 16,000 DB reads/sec.

2. **Pub/sub decoupling:** LTPService publishes to `ltp:ticks`. All consumers (WS bridge, alert engine, future indicator engine) subscribe independently. Adding a new consumer requires no change to LTPService.

3. **Hot cache:** LTP values survive short WS reconnects — stale-but-available is better than no data.

4. **TTL for cooldowns:** Redis TTL keys are used for alert cooldown tracking — key auto-expires, no cleanup task needed.

### 14.3 Redis Version Note
Current dev environment uses Redis 3.x (Windows). `HSET` with multi-field mapping syntax is not supported. All multi-field writes use `hmset()` for compatibility. Redis 7.x in production (Docker) supports both.

---

## 15. Broker Adapter Pattern

All broker operations go through the `BrokerAdapter` ABC. Business logic never calls OpenAlgo directly.

```python
class BrokerAdapter(ABC):
    async def get_ltp(symbol: str) -> float
    async def get_ltp_batch(symbols: list[str]) -> dict[str, float]
    async def place_order(order: OrderRequest) -> OrderResponse
    async def get_order_status(order_id: str) -> OrderStatus
    async def get_positions() -> list[Position]
    async def subscribe_ws(callback: Callable) -> None
    async def disconnect() -> None
```

**OpenAlgoAdapter:** Full REST + WS implementation. REST on port 5000, WS on port 8765.

**MockAdapter:** Generates random walk prices for NIFTY, BANKNIFTY, FINNIFTY, SENSEX. Tick interval configurable via `ltp_poll_interval_seconds`. Used for all development and testing without a live broker connection.

**Runtime switching:** `GET /api/v1/settings` returns current adapter type. `POST /api/v1/settings` with `{"adapter": "openalgo"}` switches at runtime without restart.

---

## 16. Design Decisions and Discussions

### 16.1 Alert Evaluation: Real-time vs Polling

**Decision: Real-time, event-driven on every tick.**

- `alert_engine.on_tick()` is called directly from the Redis pub/sub bridge in `main.py`
- Frequency = OpenAlgo tick rate (~1/sec)
- No separate polling loop, no timer
- Advantage: zero latency between price change and alert evaluation
- Trade-off: every tick triggers evaluation for all affected strategies. At 100 users × 10 strategies = 1,000 strategies × 1 tick/sec, this is manageable in-process.

### 16.2 Condition Tree vs Simple Threshold

**Decision: Recursive AND/OR condition tree (Chartink-style).**

Simple threshold: "Alert when MTM < -3000" — covers 90% of basic cases but cannot express:
- "MTM < -3000 AND RSI(15m) > 70" (exit signal confirmation)
- "Spot crosses above 24000 OR MTM > 5000" (take-profit either condition)
- Nested groups: "(A AND B) OR (C AND D)"

The recursive tree design supports all these with no backend changes needed for new condition combinations.

### 16.3 CROSS_ABOVE / CROSS_BELOW Implementation

To detect a crossover, the evaluator needs the previous tick's value. `alert_cache` stores two rolling contexts per strategy: `current_ctx` and `prev_ctx`. On each tick: current becomes prev, new context becomes current.

```python
if operator == "CROSS_ABOVE":
    return prev_value < threshold <= current_value
if operator == "CROSS_BELOW":
    return prev_value > threshold >= current_value
```

CROSS operators are only available for SPOT, INDICATOR, and CANDLE scopes in the UI — they are hidden for STRATEGY and LEG since price crossing is not a natural concept for those metrics.

### 16.4 Hourly Candle Alignment

**Decision: Remove 1h and 4h timeframes entirely.**

60-minute candles anchored to 09:15 end at 16:15 — 45 minutes after market close. The final "1h" candle of the day would only have 15 minutes of actual data. Similarly, 4h candles span across market open/close. 75m is the NSE-standard intraday timeframe (5 equal candles per session). Any code using 1h or 4h timeframes would silently evaluate against empty candle data.

### 16.5 Frontend State: Zustand vs TanStack Query

**Rule: Real-time (tick-driven) data → Zustand. REST/async server data → TanStack Query.**

- LTP prices change on every tick — Zustand with direct mutation is ideal. No query invalidation overhead.
- Alert rules are CRUD data with clear server-of-record — TanStack Query handles caching, loading states, and refetching.
- Mixing them (e.g., putting LTPs in TanStack Query) caused unnecessary re-renders and cache staleness.

### 16.6 Dual Cache Key Bug (Resolved)

`AlertList` component fetched rules under `['alert-rules', strategyId]` and invalidated only that key on mutations.
`AlertsByPosition` fetched under `['alert-rules-all']` — a completely separate cache entry.

Creating a rule in `AlertList` correctly refreshed the per-strategy view but `AlertsByPosition` continued showing stale data.

**Fix:** Both components' mutations now call `invalidateAll()` which invalidates both keys:
```typescript
const invalidateAll = () => {
  queryClient.invalidateQueries({ queryKey: ['alert-rules', strategyId] })
  queryClient.invalidateQueries({ queryKey: ['alert-rules-all'] })
}
```

### 16.7 MAX_LOSS Semantics

`MAX_LOSS` returns the absolute value of the deepest drawdown so users can write natural conditions:
```
MAX_LOSS >= 5000   (means: I've lost more than 5000)
```
Rather than the confusing double-negative:
```
MAX_LOSS <= -5000  (would be required if we stored it as a negative number)
```

---

## 17. Known Gaps and Limitations

### 17.1 Alert Engine: localStorage-Only Strategies Invisible

**Problem:** Strategies built via "Group as Strategy" from broker positions exist only in Zustand → localStorage. Alert rules for these strategies ARE saved to the DB. But `alert_engine._load_strategy_legs()` queries the `strategies` DB table — finds nothing — so the leg-to-symbol index is empty and conditions are never evaluated.

**Symptom:** Rules show in Alert Manager UI but never trigger.

**Required fix:** When creating an alert rule for a strategy that does not exist in the DB, either:
- a) Persist the strategy+legs to DB before saving the rule, OR
- b) Expose a `POST /api/v1/alert-engine/reload` endpoint that also accepts strategy leg data from the frontend

### 17.2 Indicator Conditions Do Not Fire

Indicator scope conditions (`RSI > 70`, `EMA crosses above spot`) are stored and evaluated correctly in the rule evaluator. But `ctx["indicators"]` is always `{}` because no component populates the `indicator:{symbol}:{tf}` Redis hash. The Strategy Automation Engine (Phase 6) will write computed indicator values here after each candle closes via the `register_on_close()` callback.

### 17.3 `prev_close` in Candle Context

`PREV_CLOSE` in CANDLE scope and `SPOT_CHG_PCT` both require the previous day's closing price. This is not currently fetched or stored anywhere. The alert evaluator will return `None` for these metrics. Needs: either a startup task to fetch prev_close from broker API or historical DB query.

### 17.4 Volume in Candle Context

`CandleBuilder.ingest()` is called with `volume=0` from `LTPService._on_tick()` because OpenAlgo tick messages do not include volume. CANDLE/VOLUME conditions will always evaluate against 0.

### 17.5 No Telegram / Email Delivery

`notify_telegram` and `notify_email` flags are stored and evaluated but the actual delivery code is not built. Placeholders in `alert_service.py` log the intent but do not send messages. Popup and sound notifications work.

### 17.6 Single-User Only

No authentication, no RBAC, no user isolation. All strategies and alert rules belong to the single user running the instance. Multi-user support is a Phase 7+ item.

---

## 18. Future Roadmap

### Phase 6: Strategy Automation Engine
- Indicator engine: computes EMA, RSI, MACD, Bollinger, Supertrend after each candle close
- Writes values to `indicator:{symbol}:{tf}` Redis hash
- Signal evaluator: combines indicators into BUY/SELL/NEUTRAL signals
- Signal-based auto-entry and auto-exit
- Spec: `docs/STRATEGY_AUTOMATION_BUILD_PROMPT.md`

### Phase 7: Multi-User Auth
- JWT-based authentication
- Per-user strategy and rule isolation
- RBAC (admin vs trader)
- Spring Boot migration planned for v3 SaaS scale

### Phase 8: Alert Delivery
- Telegram: bot API integration, per-user chat_id configuration
- Email: SMTP with template rendering
- Webhook: HTTP POST to user-configured URL with rule context payload

### Phase 9: Backtester
- Strategy P&L simulation against historical candle data
- Rule-based entry/exit backtesting
- Drawdown, Sharpe ratio, win rate metrics

### Phase 10: Data Infrastructure
- Historical data import from broker
- Continuous aggregate dashboards
- Per-symbol data quality monitoring
- Spec: `docs/DATA_INFRASTRUCTURE_BUILD_PROMPT.md`

### Other Planned Items
- Order Book page: full order history with status and fill details
- MTM chart: live and historical P&L chart per strategy
- Position-level Greeks display (Delta, Theta, Vega)
- Options strategy screener (Chartink-style for options)
- Mobile-responsive layout

---

## 19. Dev Environment and Commands

### Prerequisites
- Python 3.11+ (3.12 recommended)
- Node.js 18+
- Redis (Windows: Redis 3.x, Docker: Redis 7)
- SQLite (dev) or PostgreSQL 16 + TimescaleDB (production)

### Running Locally

```bash
# Backend
cd backend
uvicorn main:app --reload --port 8000

# Frontend
cd frontend
npm run dev       # port 3000

# TypeScript type check (no errors = clean)
cd frontend
npx tsc --noEmit

# Database migrations
# Windows: use full path to alembic.exe
C:/Users/sneha/AppData/Local/Packages/PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0/LocalCache/local-packages/Python311/Scripts/alembic.exe upgrade head
```

### Environment Variables (`.env`)

```
DATABASE_URL=sqlite+aiosqlite:///./trademan.db    # dev
# DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/trademan   # prod

REDIS_URL=redis://localhost:6379/0

BROKER_ADAPTER=mock          # or: openalgo
OPENALGO_HOST=http://127.0.0.1:5000
OPENALGO_WS_HOST=ws://127.0.0.1:8765
OPENALGO_API_KEY=your_key_here

LTP_POLL_INTERVAL_SECONDS=1
LTP_STALE_THRESHOLD_SECONDS=10
MTM_SNAPSHOT_INTERVAL_SECONDS=15
```

### Platform Notes (Windows 11)
- Redis 3.x: `HSET` only accepts single field-value; use `hmset()` for multi-field writes
- All `hmset()` calls in `redis_service.py` are already Redis-3.x-compatible
- `alembic.exe` and `uvicorn.exe` are in the Python 3.11 Scripts folder (full path required)
- `backend/alembic/` directory shadows the installed `alembic` package — always invoke CLI by full path

---

## 20. Critical Conventions

These rules override everything else and must never be violated.

1. **All backend code is async** — `async def`, `await`, `AsyncSession` everywhere. Never use sync SQLAlchemy in async context.

2. **Never hardcode config** — all URLs, API keys, ports come from `core/config.py` Settings class only. Never `os.environ` directly.

3. **Adapter pattern** — business logic never calls OpenAlgo directly. Only via `BrokerAdapter` interface.

4. **Entry execution: BUY first** — see Section 12. Never change this sequencing.

5. **Exit execution: SELL exits first** — see Section 12. Never change this sequencing.

6. **Redis key naming:** `resource:identifier:field` (e.g., `market:ltp`, `indicator:NIFTY:15m`)

7. **TimescaleDB operations in migrations only** — `create_hypertable`, `add_compression_policy`, etc. go in Alembic migrations via `op.execute()`. Never call these in application code.

8. **All Pydantic models:** `model_config = ConfigDict(from_attributes=True)`

9. **Frontend state split:** Zustand for real-time (LTP, alerts, strategies). TanStack Query for REST data. Never put tick-frequency data in TanStack Query.

10. **TanStack Query cache invalidation:** When a mutation affects alert rules, always invalidate both `['alert-rules', strategyId]` AND `['alert-rules-all']`.

11. **Candle timeframes:** Only build what the backend supports: `1m, 3m, 5m, 15m, 75m, 1d, 1w, 1M`. Never add a timeframe to the frontend without first building it in `candle_builder.py`.

12. **Do not commit or push** without explicit user instruction.

---

*Last updated: 2026-05-07*
*Next review: after Phase 6 (Strategy Automation Engine)*
