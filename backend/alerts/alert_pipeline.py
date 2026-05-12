"""
AlertPipeline — orchestrator that wires all alert engine components together.

REPLACES alert_engine.on_tick() with a symbol-partitioned, event-driven pipeline.
alert_engine.py is kept for backward compatibility (reload, start, leg loading).

Entry point:
  main.py calls:  await alert_pipeline.start()
  _relay() calls: alert_pipeline.route_tick(symbol, ltp, ts)

Internal flow:
  route_tick(symbol, ltp, ts)
    → get_partition(underlying)
    → workers[partition].enqueue(TickEvent)          [non-blocking]
    → SymbolWorker processes: candle → indicators → conditions → alert

Shared in-memory state (module-level dicts — process-global, no locks):
  ltp_mirror               dict[symbol → ltp]
  strategy_legs_mirror     dict[strategy_id → legs]
  strategy_underlying_mirror dict[strategy_id → underlying]

These are written by the pipeline on startup/reload and on every tick.
All SymbolWorkers read from them without any locking (asyncio = single thread).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# ── Shared in-memory mirrors (module-level globals) ───────────────────────────

# symbol → last known LTP   (updated on every tick from LTPService)
ltp_mirror:                  dict[str, float] = {}

# strategy_id → list of leg dicts (loaded from DB on startup, updated on reload)
strategy_legs_mirror:        dict[str, list[dict]] = {}

# strategy_id → underlying symbol (e.g. "NIFTY", "CRUDEOIL")
strategy_underlying_mirror:  dict[str, str] = {}


# ── NotificationWorker ────────────────────────────────────────────────────────

async def _notification_worker(alert_queue: asyncio.Queue) -> None:
    """
    Drains AlertFiredEvent from the alert queue.
    Handles: WS broadcast, DB persist, cooldown, trigger_once removal.
    Runs as a single coroutine — I/O here does NOT block condition evaluation.
    """
    from core.event_bus import AlertFiredEvent
    from alerts.notification_engine import notify

    while True:
        event: AlertFiredEvent = await alert_queue.get()
        try:
            # Persist trigger count + last_triggered to DB
            await _persist_trigger(event.alert_id)

            # Find the full rule dict (for notification channels)
            from alerts.alert_cache import alert_cache
            rule = _find_rule(alert_cache, event.strategy_id, event.alert_id)
            if rule:
                from alerts.alert_cache import EvaluationContext
                ctx = EvaluationContext(event.ctx)
                await notify(rule, ctx)
        except Exception as exc:
            logger.warning("[NotificationWorker] error for %s: %s",
                           event.alert_id, exc)


def _find_rule(cache, strategy_id: str, alert_id: str) -> dict | None:
    for rule in cache.get_rules_for_strategy(strategy_id):
        if rule["alert_id"] == alert_id:
            return rule
    return None


async def _persist_trigger(alert_id: str) -> None:
    from core.database import AsyncSessionLocal
    from alerts.models import AlertRuleBuilder
    from sqlalchemy import select

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AlertRuleBuilder).where(AlertRuleBuilder.alert_id == alert_id)
            )
            row = result.scalar_one_or_none()
            if row:
                row.triggered_count += 1
                row.last_triggered   = datetime.now(tz=timezone.utc)
                await db.commit()
    except Exception as exc:
        logger.warning("[AlertPipeline] persist trigger failed: %s", exc)


# ── AlertPipeline ─────────────────────────────────────────────────────────────

class AlertPipeline:
    """
    Main orchestrator.  Singleton at module bottom.

    Startup sequence (called from main.py lifespan):
      1. Load strategy legs + underlyings from DB
      2. Load alert rules into AlertCache
      3. Build DependencyGraph
      4. Start N SymbolWorkers + 1 NotificationWorker
    """

    def __init__(self) -> None:
        from alerts.symbol_worker import _N_WORKERS
        self._n_workers    = _N_WORKERS
        self._workers:     list = []
        self._alert_queue: asyncio.Queue | None = None
        self._tasks:       list[asyncio.Task]   = []
        self._started      = False

    async def start(self) -> None:
        if self._started:
            return

        from core.event_bus import make_queue
        from alerts.symbol_worker import SymbolWorker, configure_workers
        from alerts.alert_cache import alert_cache
        from alerts.dependency_graph import dep_graph

        self._alert_queue = make_queue(maxsize=1024)

        # Load alert rules
        await alert_cache.reload()

        # Load strategy data
        await self._load_strategy_data()

        # Build dependency graph
        dep_graph.rebuild(
            rules                = dict(alert_cache._rules),
            strategy_underlyings = dict(strategy_underlying_mirror),
            strategy_legs        = dict(strategy_legs_mirror),
        )

        # Create symbol workers
        configure_workers(self._n_workers)
        self._workers = [
            SymbolWorker(i, self._alert_queue)
            for i in range(self._n_workers)
        ]
        for w in self._workers:
            await w.start()

        # Start notification worker
        self._tasks.append(
            asyncio.create_task(
                _notification_worker(self._alert_queue),
                name="notification-worker",
            )
        )

        self._started = True
        logger.info(
            "[AlertPipeline] started: %d symbol workers, %d strategies, %d alert rules",
            self._n_workers,
            len(strategy_legs_mirror),
            sum(len(v) for v in alert_cache._rules.values()),
        )

    async def stop(self) -> None:
        for w in self._workers:
            await w.stop()
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        self._started = False

    async def reload(self) -> None:
        """
        Called after alert CRUD to refresh rules and rebuild dependency graph.
        Non-blocking for running workers — they read from AlertCache which is
        updated atomically.
        """
        from alerts.alert_cache import alert_cache
        from alerts.dependency_graph import dep_graph

        await alert_cache.reload()
        await self._load_strategy_data()

        dep_graph.rebuild(
            rules                = dict(alert_cache._rules),
            strategy_underlyings = dict(strategy_underlying_mirror),
            strategy_legs        = dict(strategy_legs_mirror),
        )
        logger.info("[AlertPipeline] reloaded")

    # ── Tick routing — called on every tick from main.py ─────────────────────

    def route_tick(self, symbol: str, ltp: float, ts: datetime) -> None:
        """
        Non-blocking tick entry point.
        Routes the tick to the correct SymbolWorker based on underlying symbol.
        Called from the Redis pub/sub relay in main.py — must never block.
        """
        if not self._started:
            return

        from core.event_bus import TickEvent
        from alerts.symbol_worker import get_partition
        from alerts.dependency_graph import dep_graph

        # Update global LTP mirror immediately (used by context builder)
        ltp_mirror[symbol] = ltp

        # Determine which worker owns this symbol
        underlying  = dep_graph.underlying_for_symbol(symbol) or symbol
        partition   = get_partition(underlying)

        if partition < len(self._workers):
            event = TickEvent(
                symbol=symbol, ltp=ltp, change=0.0, volume=0, ts=ts
            )
            self._workers[partition].enqueue(event)

    # ── Strategy data loader ──────────────────────────────────────────────────

    async def _load_strategy_data(self) -> None:
        """
        Load strategy legs and underlyings into in-memory mirrors.
        Two-pass approach (same as original alert_engine) to handle
        legacy UUID mismatch via description JSON fallback.
        """
        import json as _json
        import uuid as _uuid
        from core.database import AsyncSessionLocal
        from models.relational import Strategy, StrategyLeg
        from alerts.models import AlertRuleBuilder
        from sqlalchemy import select

        strategy_legs_mirror.clear()
        strategy_underlying_mirror.clear()

        try:
            async with AsyncSessionLocal() as db:
                # Pass 1: standard join
                result = await db.execute(
                    select(StrategyLeg).join(Strategy).where(
                        Strategy.status.in_(["active", "draft"])
                    )
                )
                for leg in result.scalars().all():
                    # Normalize to hyphenated UUID — Strategy.id is CHAR(32) (no hyphens)
                    # but AlertRuleBuilder.strategy_id is String(36) (with hyphens).
                    # All mirrors MUST use hyphenated form to match alert_cache keys.
                    try:
                        sid = str(_uuid.UUID(str(leg.strategy_id)))
                    except (ValueError, AttributeError):
                        sid = str(leg.strategy_id)
                    try:
                        leg_id = str(_uuid.UUID(str(leg.id)))
                    except (ValueError, AttributeError):
                        leg_id = str(leg.id)
                    sym = leg.symbol
                    if sid not in strategy_legs_mirror:
                        strategy_legs_mirror[sid] = []
                    strategy_legs_mirror[sid].append({
                        "leg_id":      leg_id,
                        "symbol":      sym,
                        "action":      leg.action,
                        "entry_price": leg.entry_price or 0.0,
                        "quantity":    leg.quantity,
                        "option_type": leg.option_type or "",   # "CE"|"PE"|"FUT"|""
                    })

                # Load underlyings
                strat_res = await db.execute(
                    select(Strategy).where(Strategy.status.in_(["active", "draft"]))
                )
                all_strategies = strat_res.scalars().all()
                for s in all_strategies:
                    try:
                        sid = str(_uuid.UUID(str(s.id)))
                    except (ValueError, AttributeError):
                        sid = str(s.id)
                    if s.underlying:
                        strategy_underlying_mirror[sid] = s.underlying

                # Pass 1.5: remap backend-generated StrategyLeg.id → frontend leg UUIDs
                #
                # Problem: StrategyLeg.id is always uuid4() (backend-generated).
                # The frontend stores its own UUID (crypto.randomUUID()) in condition
                # trees.  So leg_ltps = {backend_uuid: ltp}, but the condition tree
                # says leg_id = frontend_uuid  →  lookup always returns None  →
                # all LEG-scope conditions always evaluate to False.
                #
                # Fix: read description JSON (stores full frontend Strategy object),
                # match each DB leg to a JSON leg by symbol, use the JSON leg's id.
                for s in all_strategies:
                    try:
                        sid = str(_uuid.UUID(str(s.id)))
                    except (ValueError, AttributeError):
                        sid = str(s.id)
                    if sid not in strategy_legs_mirror or not s.description:
                        continue
                    try:
                        desc = _json.loads(s.description)
                        desc_legs = desc.get("legs", [])
                        if not desc_legs:
                            continue
                        # symbol → frontend leg id (normalize to hyphenated)
                        sym_to_fid: dict[str, str] = {}
                        for dl in desc_legs:
                            fid = dl.get("id") or ""
                            sym = (dl.get("instrument") or {}).get("symbol") or ""
                            if fid and sym:
                                try:
                                    fid = str(_uuid.UUID(fid))
                                except (ValueError, AttributeError):
                                    pass
                                sym_to_fid[sym] = fid
                        if not sym_to_fid:
                            continue
                        for leg_d in strategy_legs_mirror[sid]:
                            fid = sym_to_fid.get(leg_d["symbol"])
                            if fid and fid != leg_d["leg_id"]:
                                logger.debug(
                                    "[AlertPipeline] leg UUID remap %s…→%s… (%s)",
                                    leg_d["leg_id"][:8], fid[:8], leg_d["symbol"],
                                )
                                leg_d["leg_id"] = fid
                    except Exception:
                        pass

                # Pass 2: fallback for UUID mismatch (description JSON)
                arb_res = await db.execute(
                    select(AlertRuleBuilder.strategy_id)
                    .where(AlertRuleBuilder.is_active == True)  # noqa: E712
                    .distinct()
                )
                alert_sids   = {str(r[0]) for r in arb_res.fetchall()}
                missing_sids = alert_sids - set(strategy_legs_mirror.keys())

                for sid in missing_sids:
                    strat = next((s for s in all_strategies if str(s.id) == sid), None)
                    if strat is None:
                        for s in all_strategies:
                            if not s.description:
                                continue
                            try:
                                desc = _json.loads(s.description)
                                if str(desc.get("id", "")) == sid:
                                    strat = s
                                    break
                            except Exception:
                                continue

                    if strat is None or not strat.description:
                        continue

                    try:
                        desc = _json.loads(strat.description)
                        legs_data: list[dict] = []
                        for fl in desc.get("legs", []):
                            inst = fl.get("instrument", {})
                            sym  = inst.get("symbol", "")
                            if not sym:
                                continue
                            inst_type = inst.get("instrumentType", "")
                            legs_data.append({
                                "leg_id":      fl.get("id", str(_uuid.uuid4())),
                                "symbol":      sym,
                                "action":      fl.get("side", "BUY"),
                                "entry_price": fl.get("entryPrice") or 0.0,
                                "quantity":    int(fl.get("quantity", 1)),
                                "option_type": inst_type if inst_type in ("CE", "PE", "FUT") else "",
                            })
                        if legs_data:
                            strategy_legs_mirror[sid] = legs_data
                            under = desc.get("underlyingSymbol") or (
                                strat.underlying or ""
                            )
                            if under:
                                strategy_underlying_mirror[sid] = under
                            logger.info(
                                "[AlertPipeline] fallback: %d legs for strategy %s",
                                len(legs_data), sid
                            )
                    except Exception as exc:
                        logger.warning("[AlertPipeline] description parse %s: %s", sid, exc)

            # Register symbols with LTPService so ticks actually arrive
            all_symbols: set[str] = set()
            for legs in strategy_legs_mirror.values():
                for leg in legs:
                    all_symbols.add(leg["symbol"])
            all_symbols.update(strategy_underlying_mirror.values())

            if all_symbols:
                try:
                    from services.ltp.ltp_service import ltp_service
                    await ltp_service.add_symbols(list(all_symbols))
                    logger.info("[AlertPipeline] subscribed %d symbols", len(all_symbols))
                except Exception as exc:
                    logger.warning("[AlertPipeline] symbol subscribe failed: %s", exc)

        except Exception as exc:
            logger.warning("[AlertPipeline] strategy data load failed: %s", exc)


# ── Singleton ─────────────────────────────────────────────────────────────────
alert_pipeline = AlertPipeline()
