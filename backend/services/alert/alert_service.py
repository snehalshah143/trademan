"""
AlertService — evaluates alert rules on every tick and fires alerts.

Rules (defined in StrategyAutomationConfig.alert_rules JSON):
  {"rules": [
      {"id": "mtm_target",    "type": "MTM_TARGET",    "value": 5000,  "cooldown_seconds": 300},
      {"id": "mtm_sl",        "type": "MTM_SL",        "value": -3000, "cooldown_seconds": 0},
      {"id": "max_loss_pct",  "type": "MAX_LOSS_PCT",  "value": 50,    "cooldown_seconds": 0},
      {"id": "be_proximity",  "type": "BE_PROXIMITY",  "threshold_pct": 5.0, "cooldown_seconds": 60}
  ]}

On alert fired: INSERT alert_events → broadcast WS → set Redis cooldown.
"""
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from models.relational import AlertEvent, AlertSeverity, Strategy
from services.redis_service import redis_service

logger = logging.getLogger(__name__)


class AlertService:
    async def evaluate(
        self,
        strategy: Strategy,
        current_spot: float,
        ltp_map: Dict[str, float],
    ) -> None:
        """
        Check all enabled alert rules for a strategy.  Fires alerts for any that trigger.
        ``ltp_map`` is a symbol → ltp dict covering the strategy's legs.
        """
        if (
            not strategy.automation_config
            or not strategy.automation_config.enabled
            or not strategy.automation_config.alert_rules
        ):
            return

        rules: List[Dict] = strategy.automation_config.alert_rules.get("rules", [])
        if not rules:
            return

        # Compute current MTM from ltp_map
        mtm = self._compute_mtm(strategy, ltp_map)

        # Compute initial credit (sum of SELL-leg premium received)
        initial_credit = sum(
            (l.entry_price or 0.0) * l.quantity
            for l in strategy.legs
            if l.action == "SELL" and l.entry_price
        )

        fired: List[Dict] = []

        for rule in rules:
            rule_type = rule.get("type")
            rule_id   = rule.get("id") or rule_type
            cooldown  = int(rule.get("cooldown_seconds", 300))

            # Skip if in cooldown
            ck = f"{strategy.id}:{rule_id}"
            if await redis_service.is_in_cooldown(ck):
                continue

            triggered, message, severity = self._check_rule(
                rule_type, rule, mtm, initial_credit, current_spot
            )

            if triggered:
                fired.append({
                    "rule_id":  rule_id,
                    "message":  message,
                    "severity": severity,
                    "cooldown": cooldown,
                    "cooldown_key": ck,
                })

        # ── Rich alert types (positionAlerts / overallTarget / underlyingAlert / deltaAlert) ──
        alert_rules_dict = strategy.automation_config.alert_rules

        # Per-leg position alerts (target & stop-loss per leg LTP)
        for pa in alert_rules_dict.get("positionAlerts", []):
            leg_id = pa.get("legId", "")
            target = pa.get("targetPrice")
            sl     = pa.get("slPrice")
            leg = next((l for l in strategy.legs if str(l.id) == leg_id), None)
            if not leg:
                continue
            ltp = ltp_map.get(leg.symbol)
            if ltp is None:
                continue
            if target is not None:
                ck = f"{strategy.id}:pos_target:{leg_id[:8]}"
                if not await redis_service.is_in_cooldown(ck) and ltp >= float(target):
                    fired.append({
                        "rule_id": f"pos_target_{leg_id[:8]}",
                        "message": f"Target hit for {leg.symbol}: LTP {ltp:.2f} ≥ {float(target):.2f}",
                        "severity": AlertSeverity.INFO,
                        "cooldown": 0, "cooldown_key": ck,
                    })
            if sl is not None:
                ck = f"{strategy.id}:pos_sl:{leg_id[:8]}"
                if not await redis_service.is_in_cooldown(ck):
                    hit = (ltp >= float(sl)) if leg.action == "SELL" else (ltp <= float(sl))
                    if hit:
                        fired.append({
                            "rule_id": f"pos_sl_{leg_id[:8]}",
                            "message": f"Stop loss hit for {leg.symbol}: LTP {ltp:.2f}",
                            "severity": AlertSeverity.CRITICAL,
                            "cooldown": 0, "cooldown_key": ck,
                        })

        # Overall MTM target
        oa = alert_rules_dict.get("overallTarget")
        if oa and oa.get("enabled") and oa.get("mtmValue") is not None:
            ck = f"{strategy.id}:overall_target"
            if not await redis_service.is_in_cooldown(ck) and mtm >= float(oa["mtmValue"]):
                fired.append({
                    "rule_id": "overall_target",
                    "message": f"Overall target reached: ₹{mtm:,.0f} ≥ ₹{oa['mtmValue']:,.0f}",
                    "severity": AlertSeverity.INFO,
                    "cooldown": 300, "cooldown_key": ck,
                })

        # Overall MTM stop loss
        osl = alert_rules_dict.get("overallStopLoss")
        if osl and osl.get("enabled") and osl.get("mtmValue") is not None:
            ck = f"{strategy.id}:overall_sl"
            if not await redis_service.is_in_cooldown(ck) and mtm <= float(osl["mtmValue"]):
                fired.append({
                    "rule_id": "overall_sl",
                    "message": f"Overall stop loss hit: ₹{mtm:,.0f} ≤ ₹{osl['mtmValue']:,.0f}",
                    "severity": AlertSeverity.CRITICAL,
                    "cooldown": 0, "cooldown_key": ck,
                })

        # Underlying price alert
        ua = alert_rules_dict.get("underlyingAlert")
        if ua and ua.get("enabled") and ua.get("value") is not None:
            ck = f"{strategy.id}:underlying_alert"
            if not await redis_service.is_in_cooldown(ck):
                val = float(ua["value"])
                op  = ua.get("operator", "less_than")
                hit = (
                    (current_spot < val) if op == "less_than" else
                    (current_spot > val) if op == "greater_than" else
                    (abs(current_spot - val) < 1.0)
                )
                if hit:
                    fired.append({
                        "rule_id": "underlying_alert",
                        "message": f"Underlying {current_spot:.2f} {op.replace('_', ' ')} {val:.2f}",
                        "severity": AlertSeverity.WARNING,
                        "cooldown": 300, "cooldown_key": ck,
                    })

        # Delta alert
        da = alert_rules_dict.get("deltaAlert")
        if da and da.get("enabled") and da.get("value") is not None:
            ck = f"{strategy.id}:delta_alert"
            if not await redis_service.is_in_cooldown(ck):
                net_delta = self._compute_net_delta(strategy, ltp_map)
                val = float(da["value"])
                op  = da.get("operator", "less_than")
                hit = (net_delta < val) if op == "less_than" else (net_delta > val)
                if hit:
                    fired.append({
                        "rule_id": "delta_alert",
                        "message": f"Net delta {net_delta:.3f} {op.replace('_', ' ')} {val:.3f}",
                        "severity": AlertSeverity.WARNING,
                        "cooldown": 300, "cooldown_key": ck,
                    })

        if fired:
            await self._fire_alerts(strategy, fired)

    # ── Rule evaluation ───────────────────────────────────────────────────────

    @staticmethod
    def _check_rule(
        rule_type: Optional[str],
        rule: Dict,
        mtm: float,
        initial_credit: float,
        current_spot: float,
    ):
        """Return (triggered: bool, message: str, severity: str)."""
        if rule_type == "MTM_TARGET":
            target = float(rule.get("value", 0))
            if mtm >= target:
                return (
                    True,
                    f"MTM target reached: ₹{mtm:,.0f} ≥ ₹{target:,.0f}",
                    AlertSeverity.INFO,
                )

        elif rule_type == "MTM_SL":
            sl = float(rule.get("value", 0))
            if mtm <= sl:
                return (
                    True,
                    f"MTM stop-loss hit: ₹{mtm:,.0f} ≤ ₹{sl:,.0f}",
                    AlertSeverity.CRITICAL,
                )

        elif rule_type == "MAX_LOSS_PCT":
            max_pct = float(rule.get("value", 50))
            if initial_credit > 0:
                loss_pct = abs(min(0.0, mtm)) / initial_credit * 100
                if loss_pct >= max_pct:
                    return (
                        True,
                        f"Max-loss {loss_pct:.1f}% of premium exceeded (limit {max_pct}%)",
                        AlertSeverity.CRITICAL,
                    )

        elif rule_type == "BE_PROXIMITY":
            threshold_pct = float(rule.get("threshold_pct", 5.0))
            if initial_credit > 0:
                # Approximate: fired when MTM has eroded to within threshold% of break-even
                credit_erosion_pct = (initial_credit - mtm) / initial_credit * 100
                if credit_erosion_pct >= (100 - threshold_pct):
                    return (
                        True,
                        f"Near break-even: credit erosion {credit_erosion_pct:.1f}% (MTM ₹{mtm:,.0f})",
                        AlertSeverity.WARNING,
                    )

        return False, "", AlertSeverity.INFO

    # ── Alert firing ──────────────────────────────────────────────────────────

    async def _fire_alerts(self, strategy: Strategy, fired: List[Dict]) -> None:
        from core.database import AsyncSessionLocal
        from ws.hub import hub

        async with AsyncSessionLocal() as session:
            for item in fired:
                alert = AlertEvent(
                    strategy_id=strategy.id,
                    rule_id=item["rule_id"],
                    symbol=strategy.underlying,
                    message=item["message"],
                    severity=item["severity"],
                    triggered_at=datetime.now(timezone.utc),
                )
                session.add(alert)
                logger.info(
                    "[AlertService] %s fired: %s", item["rule_id"], item["message"]
                )
            await session.commit()

        # Broadcast via WS
        for item in fired:
            try:
                await hub.broadcast(
                    json.dumps({
                        "type":     "ALERT",
                        "rule_id":  item["rule_id"],
                        "message":  item["message"],
                        "severity": item["severity"],
                        "strategy_id": str(strategy.id),
                    })
                )
            except Exception as exc:
                logger.warning("[AlertService] WS broadcast error: %s", exc)

            # Set cooldown
            await redis_service.set_cooldown(item["cooldown_key"], item["cooldown"])

    # ── MTM / Delta helpers ──────────────────────────────────────────────────

    @staticmethod
    def _compute_net_delta(strategy: Strategy, ltp_map: Dict[str, float]) -> float:
        """Approximated net delta (0.5 for CE, -0.5 for PE, 1 for FUT × side × lots)."""
        delta = 0.0
        for leg in strategy.legs:
            mult = 1.0 if leg.action == "BUY" else -1.0
            if hasattr(leg, "option_type"):
                ot = (leg.option_type or "").upper()
                base = 1.0 if ot == "FUT" else (0.5 if ot == "CE" else -0.5)
            else:
                base = 0.5
            qty = getattr(leg, "quantity", 1)
            delta += mult * base * qty
        return delta

    @staticmethod
    def _compute_mtm(strategy: Strategy, ltp_map: Dict[str, float]) -> float:
        mtm = 0.0
        for leg in strategy.legs:
            if leg.entry_price is None:
                continue
            ltp = ltp_map.get(leg.symbol)
            if ltp is None:
                continue
            if leg.action == "BUY":
                mtm += (ltp - leg.entry_price) * leg.quantity
            else:
                mtm += (leg.entry_price - ltp) * leg.quantity
        return mtm


# ── Singleton ─────────────────────────────────────────────────────────────────
alert_service = AlertService()
