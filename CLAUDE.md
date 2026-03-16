# TRADEMAN — Claude Code Context File
# This file is read automatically at the start of every Claude Code session.
# Keep it updated as the project evolves.

## Product
TRADEMAN is an F&O strategy and position management platform.
Self-hosted. Broker-agnostic via adapter pattern. Built for Indian derivatives traders.
Target v1: 100 users × 10 positions × 4 legs = 4,000 active legs.

## Stack
- Frontend: React 18 + TypeScript + Vite + Zustand + TanStack Query + Tailwind CSS
- Backend: FastAPI + Python 3.12 + asyncio + Pydantic v2 + SQLAlchemy 2 async
- Databases: Redis 7 (hot layer) + TimescaleDB/PostgreSQL 16 (time-series + relational)
- Broker: OpenAlgo adapter (port 5000 REST, port 8765 WS). MockAdapter for dev.
- Migrations: Alembic (manages both relational tables AND TimescaleDB hypertables)
- ORM driver: asyncpg for PostgreSQL, aiosqlite for SQLite dev
- Redis driver: redis[asyncio] + hiredis

## Repository layout
```
trademan/
  frontend/                   React app — npm run dev (port 3000)
    src/
      types/domain.ts          All TypeScript domain types (COMPLETE)
      store/ltpStore.ts        Zustand LTP store with flash detection (COMPLETE)
      store/strategyStore.ts   Zustand strategy store + localStorage persist (COMPLETE)
      store/alertStore.ts      Zustand alert event store (COMPLETE)
      adapters/
        broker.adapter.ts      BrokerAdapter ABC + OpenAlgoAdapter + MockAdapter (COMPLETE)
        AdapterContext.tsx     Runtime-switchable adapter context (COMPLETE)
      hooks/
        useMarketWebSocket.ts  WS hook with exponential backoff reconnect (COMPLETE)
      lib/
        payoff.ts              Pure payoff computation engine (COMPLETE)
        execution.ts           Sequenced order placement engine (COMPLETE)
        utils.ts               INR formatting, color helpers (COMPLETE)
      modules/
        strategy-builder/      (Phase 5)
        position-manager/      (Phase 5)
        alert-engine/          (Phase 5)
        order-book/            (future)
        mtm-chart/             (future)
      components/
        layout/                (Phase 5)
        charts/                (Phase 5)

  backend/                    FastAPI app — uvicorn main:app --reload (port 8000)
    main.py                   Entry point with lifespan (COMPLETE scaffold)
    core/
      config.py               Pydantic settings — all env-var driven (COMPLETE)
      database.py             SQLAlchemy async engine + session (Phase 2)
    models/
      timeseries.py           Candle, MTMSnapshot, LTPTick — hypertables (Phase 2)
      relational.py           Strategy, StrategyLeg, Order, AlertEvent (Phase 2)
    services/
      redis_service.py        Redis operations — LTP cache, pub/sub, state (Phase 2)
      candle_repository.py    TimescaleDB candle CRUD + MTM snapshots (Phase 2)
      mtm_tracker.py          Async task — MTM snapshot every 15s (Phase 3)
      ltp/
        ltp_service.py        Subscribe OpenAlgo WS → Redis fan-out (Phase 3)
        candle_builder.py     Tick → OHLCV candles, 5m/15m/75m (Phase 3)
      execution/
        execution_service.py  Entry/exit sequencing — BUY-first, SELL-exit-first (Phase 3)
      alert/
        alert_service.py      Rule evaluation on every tick (Phase 3)
      strategy_automation/    Indicator engine + signal evaluator (future module)
    adapters/
      broker_adapter.py       BrokerAdapter ABC (Phase 4)
      openalgo_adapter.py     Full OpenAlgo implementation (Phase 4)
      mock_adapter.py         Mock for dev/testing (Phase 4)
      adapter_factory.py      Returns correct adapter from settings (Phase 4)
    ws/
      hub.py                  WebSocket connection manager + broadcast (Phase 2)
      endpoint.py             WS route — subscribes Redis, fans out to client (Phase 2)
    api/routes/
      strategies.py           Strategy CRUD (Phase 2)
      orders.py               Order history (Phase 2)
      positions.py            Proxy to OpenAlgo positions (Phase 2)
      alerts.py               Alert event log (Phase 2)
      settings.py             Broker config endpoint (Phase 2)
    alembic/                  Database migrations (Phase 2)
      versions/
        001_initial_schema.py
        002_timescale_extension.py
        003_hypertables.py
        004_compression_policies.py
        005_retention_policies.py
        006_continuous_aggregates.py

  docker-compose.yml          timescaledb + redis + backend + frontend
  CLAUDE.md                   This file — permanent Claude Code context
  .env                        Never commit. Copy from .env.example
```

## Critical conventions — ALWAYS follow these

1. **All backend code is async** — use `async def`, `await`, `AsyncSession` everywhere
2. **Never use sync SQLAlchemy** in async context — always `AsyncSession` from `core.database`
3. **All models inherit from** `core.database.Base`
4. **Environment variables** come from `core/config.py Settings` class ONLY — never os.environ directly
5. **Never hardcode** broker URLs, API keys, or ports — always from `settings`
6. **Adapter pattern** — business logic NEVER calls OpenAlgo directly. Only via `BrokerAdapter` interface
7. **Redis key naming**: `resource:identifier:field` (e.g. `market:ltp`, `indicator:NIFTY:15m`)
8. **TimescaleDB functions** (`create_hypertable`, `add_compression_policy` etc.) go in Alembic migrations via `op.execute()`
9. **All Pydantic models** use `model_config = ConfigDict(from_attributes=True)`
10. **Frontend state**: Zustand for real-time (LTP/alerts/strategies), TanStack Query for REST data fetching

## Execution sequencing — NEVER change this logic

```
ENTRY:
  Step 1: Place all BUY legs simultaneously
  Step 2: Poll order status every 500ms until all fills confirmed (30s timeout)
  Step 3: If any BUY rejected → abort, surface error, do NOT place SELL legs
  Step 4: Place all SELL legs simultaneously
  Reason: margin benefit requires broker to see hedged position first

EXIT (full or partial):
  Step 1: Place exit orders for all SELL legs simultaneously (buy back)
  Step 2: Poll fills (500ms, 30s timeout)
  Step 3: Place exit orders for all BUY legs simultaneously (sell out)
```

## Redis key schema

```
market:ltp                       HASH  {symbol: price}         # hot LTP cache
market:tick:{symbol}             HASH  {ltp, change, ts}       # tick detail
ltp:ticks                        CHANNEL (pub/sub)             # fan-out channel
indicator:{symbol}:{tf}          HASH  {ema_21: val, ...}      # rolling indicator state
signal:{symbol}:state            STRING BUY|NEUTRAL            # current signal
signal:{symbol}:cooldown         STRING 1  EX {seconds}        # auto-TTL cooldown
strategy:{id}:mtm                STRING {float}                # hot MTM read
ws:session:{session_id}          HASH  {user_id, subscriptions}
```

## TimescaleDB tables

```
candles          — hypertable on ts, partitioned by symbol. 5m/15m/75m/1d timeframes.
mtm_snapshots    — hypertable on ts, partitioned by strategy_id. 15-second snapshots.
ltp_ticks        — hypertable on ts, partitioned by symbol. Raw tick archive.
candles_1h       — continuous aggregate materialized view from candles_5m.
```

## Current build status
```
Phase 1 (Foundation):      COMPLETE — types, stores, adapters, payoff, execution scaffolded
Phase 2 (Backend core):    NOT STARTED
Phase 3 (Data layer):      NOT STARTED
Phase 4 (OpenAlgo wiring): NOT STARTED
Phase 5 (Frontend):        NOT STARTED
```

## Known files NOT to recreate (already complete from Phase 1)
- frontend/src/types/domain.ts
- frontend/src/store/ltpStore.ts
- frontend/src/store/strategyStore.ts
- frontend/src/store/alertStore.ts
- frontend/src/adapters/broker.adapter.ts
- frontend/src/adapters/AdapterContext.tsx
- frontend/src/hooks/useMarketWebSocket.ts
- frontend/src/lib/payoff.ts
- frontend/src/lib/execution.ts
- frontend/src/lib/utils.ts
- frontend/package.json
- frontend/vite.config.ts
- frontend/tsconfig.json
- frontend/tailwind.config.js
- frontend/src/index.css
- backend/main.py
- backend/core/config.py

## Future modules (do NOT build until explicitly asked)
- Strategy Automation Engine (indicator engine, candle builder, signal evaluator)
  → See docs/STRATEGY_AUTOMATION_BUILD_PROMPT.md for full spec
- Data infrastructure deep build
  → See docs/DATA_INFRASTRUCTURE_BUILD_PROMPT.md for full spec
- Backtester
- Multi-user auth / RBAC
- Telegram alert delivery
- Spring Boot migration (planned for v3 SaaS scale)
