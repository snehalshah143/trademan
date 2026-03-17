"""
AlertEngine — evaluates nested condition-tree alert rules on every price tick.
Called from main.py's Redis→WS bridge with minimal coupling.
"""
from __future__ import annotations
import logging
from datetime import datetime, timezone

from alerts.alert_cache import alert_cache, EvaluationContext
from alerts.rule_evaluator import evaluate_group

logger = logging.getLogger(__name__)


class AlertEngine:
    """
    On each tick: build context for affected strategies → evaluate rules → notify.
    Keeps a local strategy-leg map loaded from DB to avoid per-tick DB queries.
    """

    def __init__(self) -> None:
        # strategy_id → list of {leg_id, symbol, action, entry_price, quantity}
        self._strategy_legs: dict[str, list[dict]] = {}
        # symbol → list of strategy_ids that have a leg for this symbol
        self._symbol_index: dict[str, list[str]] = {}
        self._loaded = False

    # ── Startup ────────────────────────────────────────────────────────────────

    async def start(self) -> None:
        await alert_cache.reload()
        await self._load_strategy_legs()
        self._loaded = True
        logger.info("[AlertEngine] started")

    async def _load_strategy_legs(self) -> None:
        """Cache active strategy legs keyed by symbol for fast tick lookup."""
        from core.database import AsyncSessionLocal
        from models.relational import Strategy, StrategyLeg
        from sqlalchemy import select

        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(StrategyLeg).join(Strategy).where(
                        Strategy.status.in_(["active", "draft"])
                    )
                )
                legs = result.scalars().all()

            self._strategy_legs = {}
            self._symbol_index  = {}

            for leg in legs:
                sid = str(leg.strategy_id)
                sym = leg.symbol

                if sid not in self._strategy_legs:
                    self._strategy_legs[sid] = []
                self._strategy_legs[sid].append({
                    "leg_id":       str(leg.id),
                    "symbol":       sym,
                    "action":       leg.action,
                    "entry_price":  leg.entry_price or 0.0,
                    "quantity":     leg.quantity,
                })

                if sym not in self._symbol_index:
                    self._symbol_index[sym] = []
                if sid not in self._symbol_index[sym]:
                    self._symbol_index[sym].append(sid)

        except Exception as exc:
            logger.warning("[AlertEngine] leg load failed: %s", exc)

    async def reload(self) -> None:
        """Called after alert CRUD operations to refresh cache."""
        await alert_cache.reload()
        await self._load_strategy_legs()

    # ── Tick handler ───────────────────────────────────────────────────────────

    async def on_tick(self, symbol: str, ltp: float) -> None:
        """Called for every price tick. Evaluates alerts for affected strategies."""
        if not self._loaded:
            return

        affected = self._symbol_index.get(symbol, [])
        if not affected:
            return

        from services.redis_service import redis_service

        for strategy_id in affected:
            rules = alert_cache.get_rules_for_strategy(strategy_id)
            if not rules:
                continue

            ctx = await self._build_context(strategy_id, symbol, ltp, redis_service)
            alert_cache.update_context(strategy_id, ctx)
            current_ctx, prev_ctx = alert_cache.get_contexts(strategy_id)

            for rule in rules:
                await self._evaluate_rule(rule, current_ctx, prev_ctx)

    # ── Context builder ────────────────────────────────────────────────────────

    async def _build_context(
        self,
        strategy_id: str,
        tick_symbol: str,
        tick_ltp: float,
        redis_service,
    ) -> EvaluationContext:
        legs = self._strategy_legs.get(strategy_id, [])

        # Collect LTPs: use tick for the current symbol, else Redis cache
        leg_ltps:         dict[str, float] = {}
        leg_entry_prices: dict[str, float] = {}
        leg_quantities:   dict[str, int]   = {}
        leg_sides:        dict[str, str]   = {}
        total_mtm = 0.0

        # Get all symbols we need
        symbols_needed = [l["symbol"] for l in legs if l["symbol"] != tick_symbol]
        cached_ltps: dict[str, float] = {}
        if symbols_needed:
            try:
                ltps = await redis_service.get_ltp_batch(symbols_needed)
                cached_ltps = {s: float(v) for s, v in ltps.items() if v is not None}
            except Exception:
                pass

        for leg in legs:
            lid    = leg["leg_id"]
            sym    = leg["symbol"]
            entry  = leg["entry_price"]
            qty    = leg["quantity"]
            action = leg["action"]

            cur_ltp = tick_ltp if sym == tick_symbol else cached_ltps.get(sym, entry)

            leg_ltps[lid]         = cur_ltp
            leg_entry_prices[lid] = entry
            leg_quantities[lid]   = qty
            leg_sides[lid]        = action

            mult = 1 if action == "BUY" else -1
            total_mtm += mult * (cur_ltp - entry) * qty

        # Spot: try to get underlying spot from Redis
        spot = tick_ltp  # fallback to tick symbol
        try:
            from models.relational import Strategy
            from core.database import AsyncSessionLocal
            from sqlalchemy import select
            async with AsyncSessionLocal() as db:
                res = await db.execute(select(Strategy.underlying).where(
                    Strategy.id == strategy_id
                ))
                underlying = res.scalar_one_or_none()
            if underlying:
                spot_cached = await redis_service.get_ltp(underlying)
                if spot_cached:
                    spot = float(spot_cached)
        except Exception:
            pass

        deployed = sum(l["entry_price"] * l["quantity"] for l in legs)
        pnl_pct = (total_mtm / deployed * 100) if deployed else 0.0

        return EvaluationContext({
            "strategy_id":      strategy_id,
            "mtm":              total_mtm,
            "pnl_pct":          pnl_pct,
            "spot":             spot,
            "leg_ltps":         leg_ltps,
            "leg_entry_prices": leg_entry_prices,
            "leg_quantities":   leg_quantities,
            "leg_sides":        leg_sides,
            "indicators":       {},
        })

    # ── Rule evaluation ────────────────────────────────────────────────────────

    async def _evaluate_rule(
        self,
        rule: dict,
        ctx: EvaluationContext,
        prev_ctx: EvaluationContext,
    ) -> None:
        alert_id     = rule["alert_id"]
        cooldown     = rule.get("cooldown_secs", 0)

        if alert_cache.is_on_cooldown(alert_id, cooldown):
            return

        try:
            fired = evaluate_group(rule["condition_tree"], ctx, prev_ctx)
        except Exception as exc:
            logger.warning("[AlertEngine] evaluate error for %s: %s", alert_id, exc)
            return

        if not fired:
            return

        logger.info("[AlertEngine] FIRED alert %s (%s)", alert_id, rule.get("name"))

        # Persist trigger count + last_triggered
        await self._persist_trigger(alert_id)

        # Set cooldown
        alert_cache.set_cooldown(alert_id)
        alert_cache.mark_triggered(alert_id)  # handles trigger_once

        # Notify
        from alerts.notification_engine import notify
        await notify(rule, ctx)

    async def _persist_trigger(self, alert_id: str) -> None:
        from core.database import AsyncSessionLocal
        from alerts.models import AlertRuleBuilder
        from sqlalchemy import select
        from datetime import timezone

        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(AlertRuleBuilder).where(AlertRuleBuilder.alert_id == alert_id)
                )
                row = result.scalar_one_or_none()
                if row:
                    row.triggered_count += 1
                    row.last_triggered = datetime.now(tz=timezone.utc)
                    await db.commit()
        except Exception as exc:
            logger.warning("[AlertEngine] persist trigger failed: %s", exc)


alert_engine = AlertEngine()
