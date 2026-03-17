"""
In-memory cache for alert rules and evaluation contexts.
Reloaded from DB on startup and after any CRUD operation.
"""
from __future__ import annotations
import asyncio
import logging
from datetime import datetime

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
                        sid = str(row.strategy_id)
                        if sid not in self._rules:
                            self._rules[sid] = []
                        self._rules[sid].append({
                            "alert_id":       row.alert_id,
                            "strategy_id":    sid,
                            "name":           row.name,
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


alert_cache = AlertCache()
