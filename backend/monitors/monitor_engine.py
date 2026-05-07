"""
MonitorEngine — live MTM tracking for manually entered (monitored) positions.
On every price tick: updates leg prices → computes MTM → broadcasts MONITOR_UPDATE
→ evaluates alert rules.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone

from monitors.monitor_cache import monitor_cache

logger = logging.getLogger(__name__)


class MonitorEngine:
    def __init__(self) -> None:
        self._loaded = False
        # position_id → previous evaluation context (for cross detection)
        self._prev_contexts: dict[str, dict] = {}

    async def start(self) -> None:
        await monitor_cache.reload()
        self._loaded = True
        logger.info("[MonitorEngine] started")

    async def reload_position(self, monitor_id: str) -> None:
        await monitor_cache.reload_position(monitor_id)
        logger.debug("[MonitorEngine] reloaded position %s", monitor_id)

    async def on_price_tick(self, symbol: str, ltp: float) -> None:
        """Called for every price tick. Updates leg prices and evaluates alerts."""
        if not self._loaded:
            return

        pairs = monitor_cache.get_legs_for_symbol(symbol)
        if not pairs:
            return

        changed_monitors: set[str] = set()
        for monitor_id, leg_id in pairs:
            monitor_cache.update_price(monitor_id, leg_id, ltp)
            changed_monitors.add(monitor_id)

        from ws.hub import hub

        for monitor_id in changed_monitors:
            mtm_data = monitor_cache.get_mtm_data(monitor_id)
            if not mtm_data:
                continue

            # Broadcast live update to frontend
            msg = json.dumps({
                "type": "MONITOR_UPDATE",
                "payload": mtm_data,
            })
            try:
                await hub.broadcast(msg)
            except Exception as exc:
                logger.debug("[MonitorEngine] broadcast error: %s", exc)

            # Evaluate alert rules
            ctx = monitor_cache.get_evaluation_context(monitor_id)
            if ctx:
                prev_ctx = self._prev_contexts.get(monitor_id, ctx)
                await self._evaluate_alerts(monitor_id, ctx, prev_ctx)
                self._prev_contexts[monitor_id] = ctx

    async def _evaluate_alerts(
        self, position_id: str, ctx: dict, prev_ctx: dict
    ) -> None:
        """Evaluate all active AlertRule rows for this position."""
        try:
            from core.database import AsyncSessionLocal
            from alerts.models import AlertRule
            from alerts.rule_evaluator import evaluate_group
            from sqlalchemy import select

            async with AsyncSessionLocal() as db:
                result = await db.execute(
                    select(AlertRule).where(
                        AlertRule.position_id == position_id,
                        AlertRule.is_active == True,
                    )
                )
                rules = result.scalars().all()

            if not rules:
                return

            now = datetime.now(tz=timezone.utc)
            for rule in rules:
                alert_id = rule.alert_id

                # Cooldown check
                if self._is_on_cooldown(alert_id, rule.cooldown_secs, rule.last_triggered):
                    continue

                try:
                    fired = evaluate_group(rule.condition_tree, ctx, prev_ctx)
                except Exception as exc:
                    logger.debug("[MonitorEngine] rule eval error %s: %s", alert_id, exc)
                    continue

                if not fired:
                    continue

                logger.info("[MonitorEngine] ALERT FIRED %s — %s", alert_id, rule.name)
                await self._fire_alert(rule, ctx)

        except Exception as exc:
            logger.warning("[MonitorEngine] _evaluate_alerts error: %s", exc)

    def _is_on_cooldown(
        self, alert_id: str, cooldown_secs: int, last_triggered: datetime | None
    ) -> bool:
        if not cooldown_secs or not last_triggered:
            return False
        elapsed = (datetime.now(tz=timezone.utc) - last_triggered).total_seconds()
        return elapsed < cooldown_secs

    async def _fire_alert(self, rule, ctx: dict) -> None:
        from core.database import AsyncSessionLocal
        from alerts.models import AlertRule, AlertHistory
        from ws.hub import hub
        from alerts.rule_evaluator import build_condition_summary

        now = datetime.now(tz=timezone.utc)

        # Persist history
        try:
            async with AsyncSessionLocal() as db:
                history = AlertHistory(
                    history_id=__import__("uuid").uuid4().__str__(),
                    alert_id=rule.alert_id,
                    position_id=rule.position_id,
                    alert_name=rule.name,
                    strategy_name=rule.strategy_name or "",
                    underlying=rule.underlying or "",
                    fired_at=now,
                    condition_summary=build_condition_summary(rule.condition_tree),
                    context_snapshot={
                        "mtm": ctx.get("mtm", 0),
                        "pnl_pct": ctx.get("pnl_pct", 0),
                    },
                    notifications_sent={
                        "popup": rule.notify_popup,
                        "telegram": rule.notify_telegram,
                        "sound": rule.notify_sound,
                        "email": rule.notify_email,
                        "webhook": rule.notify_webhook,
                    },
                )
                db.add(history)

                # Update rule's trigger stats
                result = await db.execute(
                    __import__("sqlalchemy").select(AlertRule).where(
                        AlertRule.alert_id == rule.alert_id
                    )
                )
                r = result.scalar_one_or_none()
                if r:
                    r.triggered_count += 1
                    r.last_triggered = now
                    if r.trigger_once:
                        r.is_active = False

                await db.commit()
        except Exception as exc:
            logger.warning("[MonitorEngine] fire_alert persist error: %s", exc)

        # Broadcast WebSocket ALERT_FIRED
        try:
            notify_channels = []
            if rule.notify_popup:   notify_channels.append("popup")
            if rule.notify_telegram: notify_channels.append("telegram")
            if rule.notify_sound:   notify_channels.append("sound")

            ws_msg = json.dumps({
                "type": "ALERT_FIRED",
                "payload": {
                    "alert_id":          rule.alert_id,
                    "name":              rule.name,
                    "position_id":       rule.position_id,
                    "strategy_name":     rule.strategy_name or "",
                    "underlying":        rule.underlying or "",
                    "condition_summary": build_condition_summary(rule.condition_tree),
                    "context_snapshot": {
                        "mtm":     round(ctx.get("mtm", 0), 2),
                        "pnl_pct": round(ctx.get("pnl_pct", 0), 2),
                    },
                    "timestamp": now.isoformat(),
                    "channels":  notify_channels,
                },
            })
            await hub.broadcast(ws_msg)

            if rule.notify_sound:
                await hub.broadcast(json.dumps({
                    "type": "ALERT_SOUND",
                    "payload": {"alert_id": rule.alert_id},
                }))
        except Exception as exc:
            logger.warning("[MonitorEngine] broadcast error: %s", exc)

        # Telegram / webhook (best-effort)
        if rule.notify_telegram and rule.telegram_chat_id:
            try:
                from alerts.notification_engine import send_telegram
                msg = f"ALERT: {rule.name}\nMTM: {ctx.get('mtm', 0):.0f}"
                await send_telegram(rule.telegram_chat_id, msg)
            except Exception:
                pass

        if rule.notify_webhook and rule.webhook_url:
            try:
                from alerts.notification_engine import post_webhook
                await post_webhook(rule.webhook_url, {"alert": rule.name, "context": ctx})
            except Exception:
                pass


monitor_engine = MonitorEngine()
