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

    mtm  = ctx.get("mtm", 0.0)
    spot = ctx.get("spot", 0.0)
    message = f"{name} triggered | MTM: {mtm:.0f} | Spot: {spot:.0f}"

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
    if rule.get("notify_telegram") and rule.get("telegram_chat_id"):
        try:
            from core.config import settings
            bot_token = getattr(settings, "telegram_bot_token", None)
            if bot_token:
                url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
                async with httpx.AsyncClient(timeout=5) as client:
                    await client.post(url, json={
                        "chat_id": rule["telegram_chat_id"],
                        "text":    f"🔔 {message}",
                        "parse_mode": "HTML",
                    })
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
