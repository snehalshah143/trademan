"""
Dispatches alert notifications via WebSocket, Telegram, Webhook, etc.
"""
from __future__ import annotations
import json
import logging
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)


async def notify(rule: dict, ctx: dict) -> None:
    """Send notifications for a triggered alert rule."""
    alert_id    = rule["alert_id"]
    strategy_id = rule["strategy_id"]
    name        = rule["name"]
    ts          = datetime.now(tz=timezone.utc).isoformat()

    mtm           = ctx.get("mtm", 0.0)
    spot          = ctx.get("spot", 0.0)
    # Prefer strategy_name stored on the rule itself (always populated at rule creation);
    # fall back to the runtime context mirror as a secondary source.
    strategy_name = rule.get("strategy_name") or ctx.get("strategy_name", "")
    prefix        = f"{strategy_name} | " if strategy_name else ""
    spot_part     = f" | Spot: {spot:.0f}" if spot and spot > 0 else ""
    message       = f"{prefix}{name} triggered | MTM: {mtm:.0f}{spot_part}"

    # ── Persist AlertEvent to DB (powers alert history endpoint) ───────────────
    try:
        import uuid as _uuid
        from core.database import AsyncSessionLocal
        from models.relational import AlertEvent
        async with AsyncSessionLocal() as _db:
            _event = AlertEvent(
                id=_uuid.uuid4(),
                strategy_id=_uuid.UUID(strategy_id) if strategy_id else None,
                rule_id=alert_id,
                symbol=ctx.get("underlying") or None,
                message=message,
                severity="warning",
                dismissed=False,
            )
            _db.add(_event)
            await _db.commit()
    except Exception as exc:
        logger.warning("[notify] AlertEvent persist failed: %s", exc)

    # ── WebSocket popup ────────────────────────────────────────────────────────
    if rule.get("notify_popup"):
        try:
            from ws.hub import hub
            payload = {
                "type": "ALERT_FIRED",
                "payload": {
                    "alert_id":    alert_id,
                    "strategy_id": strategy_id,
                    "name":        name,
                    "message":     message,
                    "timestamp":   ts,
                    "severity":    "WARNING",
                },
            }
            await hub.broadcast(json.dumps(payload))
        except Exception as exc:
            logger.warning("[notify] WS broadcast failed: %s", exc)

    # ── Sound event ───────────────────────────────────────────────────────────
    if rule.get("notify_sound"):
        try:
            from ws.hub import hub
            await hub.broadcast(json.dumps({"type": "ALERT_SOUND", "payload": {"alert_id": alert_id}}))
        except Exception as exc:
            logger.warning("[notify] sound event failed: %s", exc)

    # ── Telegram ───────────────────────────────────────────────────────────────
    if rule.get("notify_telegram"):
        try:
            from core.config import settings
            # Per-alert chat ID takes priority; fall back to global default.
            chat_id = rule.get("telegram_chat_id") or \
                      getattr(settings, "telegram_default_chat_id", "") or ""
            bot_token = getattr(settings, "telegram_bot_token", None)

            # Telegram requires numeric IDs or @username format.
            # If the stored value is a plain username (no @, not numeric), add @.
            if chat_id and not chat_id.startswith("@") \
                       and not chat_id.startswith("-") \
                       and not chat_id.lstrip("-").isdigit():
                chat_id = f"@{chat_id}"

            if bot_token and chat_id:
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.post(url, json={
                        "chat_id": chat_id,
                        "text":    f"🔔 {message}",
                        "parse_mode": "HTML",
                    })
                if resp.status_code != 200:
                    logger.warning(
                        "[notify] Telegram API error %d for alert %s chat_id=%s: %s",
                        resp.status_code, alert_id, chat_id, resp.text,
                    )
                else:
                    logger.info("[notify] Telegram sent for alert %s to %s", alert_id, chat_id)
            elif not bot_token:
                logger.warning("[notify] Telegram skipped: TELEGRAM_BOT_TOKEN not configured")
            elif not chat_id:
                logger.warning("[notify] Telegram skipped: no chat_id on rule and no default set")
        except Exception as exc:
            logger.warning("[notify] Telegram failed: %s", exc)

    # ── Webhook ────────────────────────────────────────────────────────────────
    if rule.get("notify_webhook") and rule.get("webhook_url"):
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(rule["webhook_url"], json={
                    "alert_id":    alert_id,
                    "strategy_id": strategy_id,
                    "name":        name,
                    "message":     message,
                    "timestamp":   ts,
                    "context":     ctx,
                })
        except Exception as exc:
            logger.warning("[notify] webhook failed: %s", exc)

    # ── Email placeholder ─────────────────────────────────────────────────────
    if rule.get("notify_email"):
        logger.warning("[notify] email delivery not yet implemented for alert %s", alert_id)
