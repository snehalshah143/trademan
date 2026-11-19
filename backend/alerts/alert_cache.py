"""
In-memory cache for alert rules and evaluation contexts.
Reloaded from DB on startup and after any CRUD operation.

Context persistence
-------------------
  EvaluationContext is saved to ``alert_context_snapshots`` (DB) every few
  seconds by a background flush task started in AlertPipeline.  On restart,
  load_from_db() pre-populates _prev_ctx so CROSS_ABOVE / CROSS_BELOW
  conditions can fire immediately on the first real tick instead of waiting
  for a full price oscillation through the threshold.
"""
from __future__ import annotations
import asyncio
import json as _json
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class EvaluationContext(dict):
    """Thin dict wrapper with typed accessors."""
    pass


class AlertCache:
    def __init__(self) -> None:
        # strategy_id → list of rule dicts (plain Python, not ORM objects)
        self._rules: dict[str, list[dict]] = {}
        # strategy_id → current EvaluationContext
        self._ctx: dict[str, EvaluationContext] = {}
        # strategy_id → previous EvaluationContext (for cross detection)
        self._prev_ctx: dict[str, EvaluationContext] = {}
        # alert_id → datetime of last trigger
        self._cooldowns: dict[str, datetime] = {}
        self._lock = asyncio.Lock()
        # Strategies whose context has changed since the last DB flush
        self._dirty: set[str] = set()

    # ── Cache CRUD ─────────────────────────────────────────────────────────────

    async def reload(self) -> None:
        """Reload ALL active alert rules from DB."""
        from core.database import AsyncSessionLocal
        from alerts.models import AlertRuleBuilder
        from sqlalchemy import select

        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(AlertRuleBuilder).where(AlertRuleBuilder.is_active == True)  # noqa: E712
                )
                rows = result.scalars().all()
                async with self._lock:
                    self._rules = {}
                    for row in rows:
                        import uuid as _uuid
                        try:
                            sid = str(_uuid.UUID(str(row.strategy_id))) if row.strategy_id else str(row.strategy_id)
                        except (ValueError, AttributeError):
                            sid = str(row.strategy_id)
                        if sid not in self._rules:
                            self._rules[sid] = []
                        self._rules[sid].append({
                            "alert_id":       row.alert_id,
                            "strategy_id":    sid,
                            "name":           row.name,
                            "strategy_name":  row.strategy_name or "",
                            "trigger_once":   row.trigger_once,
                            "cooldown_secs":  row.cooldown_secs,
                            "triggered_count": row.triggered_count,
                            "notify_popup":   row.notify_popup,
                            "notify_telegram": row.notify_telegram,
                            "notify_email":   row.notify_email,
                            "notify_webhook": row.notify_webhook,
                            "notify_sound":   row.notify_sound,
                            "webhook_url":    row.webhook_url,
                            "telegram_chat_id": row.telegram_chat_id,
                            "condition_tree": row.condition_tree,
                        })
            logger.info("[AlertCache] loaded %d active rules", sum(len(v) for v in self._rules.values()))
        except Exception as exc:
            logger.warning("[AlertCache] reload failed: %s", exc)

    def get_rules_for_strategy(self, strategy_id: str) -> list[dict]:
        return self._rules.get(strategy_id, [])

    def get_all_strategy_ids(self) -> list[str]:
        return list(self._rules.keys())

    def update_context(self, strategy_id: str, ctx: EvaluationContext) -> None:
        self._prev_ctx[strategy_id] = self._ctx.get(strategy_id, EvaluationContext())
        self._ctx[strategy_id] = ctx
        self._dirty.add(strategy_id)

    def get_contexts(self, strategy_id: str) -> tuple[EvaluationContext, EvaluationContext]:
        return (
            self._ctx.get(strategy_id, EvaluationContext()),
            self._prev_ctx.get(strategy_id, EvaluationContext()),
        )

    def is_on_cooldown(self, alert_id: str, cooldown_secs: int) -> bool:
        if cooldown_secs <= 0:
            return False
        last = self._cooldowns.get(alert_id)
        if last is None:
            return False
        from datetime import timezone
        elapsed = (datetime.now(tz=timezone.utc) - last).total_seconds()
        return elapsed < cooldown_secs

    def set_cooldown(self, alert_id: str) -> None:
        from datetime import timezone
        self._cooldowns[alert_id] = datetime.now(tz=timezone.utc)

    def mark_triggered(self, alert_id: str) -> None:
        """Remove from cache if trigger_once."""
        for sid, rules in self._rules.items():
            for r in rules:
                if r["alert_id"] == alert_id and r["trigger_once"]:
                    self._rules[sid] = [x for x in rules if x["alert_id"] != alert_id]
                    return

    # ── Context persistence ───────────────────────────────────────────────────

    async def flush_to_db(self) -> None:
        """
        Upsert all dirty EvaluationContexts to alert_context_snapshots.
        Called by a background task every ~5 s — never blocks the tick pipeline.
        Only the fields needed for CROSS restart-recovery are saved (leg_ltps,
        spot, indicators, mtm, pnl_pct).
        """
        if not self._dirty:
            return

        async with self._lock:
            dirty = set(self._dirty)
            self._dirty.clear()

        from core.database import AsyncSessionLocal
        from sqlalchemy import text

        now = datetime.now(tz=timezone.utc).isoformat()
        try:
            async with AsyncSessionLocal() as db:
                for sid in dirty:
                    ctx = self._ctx.get(sid)
                    if ctx is None:
                        continue
                    snapshot = {
                        "leg_ltps":   ctx.get("leg_ltps", {}),
                        "spot":       ctx.get("spot", 0.0),
                        "mtm":        ctx.get("mtm", 0.0),
                        "pnl_pct":    ctx.get("pnl_pct", 0.0),
                        "indicators": ctx.get("indicators", {}),
                    }
                    ctx_json = _json.dumps(snapshot)
                    # DB-agnostic upsert: UPDATE then INSERT if no row existed
                    result = await db.execute(
                        text(
                            "UPDATE alert_context_snapshots "
                            "SET ctx_json=:ctx, saved_at=:ts "
                            "WHERE strategy_id=:sid"
                        ),
                        {"ctx": ctx_json, "ts": now, "sid": sid},
                    )
                    if result.rowcount == 0:
                        await db.execute(
                            text(
                                "INSERT INTO alert_context_snapshots "
                                "(strategy_id, ctx_json, saved_at) "
                                "VALUES (:sid, :ctx, :ts)"
                            ),
                            {"sid": sid, "ctx": ctx_json, "ts": now},
                        )
                await db.commit()
            logger.debug("[AlertCache] flushed %d context snapshots", len(dirty))
        except Exception as exc:
            logger.warning("[AlertCache] flush_to_db failed: %s", exc)
            # Re-mark dirty so the next flush retries
            self._dirty.update(dirty)

    async def load_from_db(self) -> None:
        """
        Load saved EvaluationContexts from DB into _prev_ctx on startup.
        After load, the first real tick advances prev→current so CROSS
        conditions can fire without waiting for a full price oscillation.
        """
        from core.database import AsyncSessionLocal
        from sqlalchemy import text

        try:
            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    text("SELECT strategy_id, ctx_json FROM alert_context_snapshots")
                )
                rows = result.fetchall()

            loaded = 0
            for row in rows:
                sid, ctx_json = row[0], row[1]
                try:
                    data = _json.loads(ctx_json)
                    # Restore as a plain EvaluationContext dict so _resolve_value
                    # can read leg_ltps, spot, indicators, mtm, pnl_pct from it.
                    self._prev_ctx[sid] = EvaluationContext(data)
                    loaded += 1
                except Exception:
                    pass
            logger.info("[AlertCache] loaded %d context snapshots from DB", loaded)
        except Exception as exc:
            logger.warning("[AlertCache] load_from_db failed: %s", exc)


alert_cache = AlertCache()
