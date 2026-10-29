"""
Greeks REST endpoint.

Returns live option greeks (delta, gamma, theta, vega) for every leg of a
strategy plus aggregated net greeks across all legs.

Data comes from the in-memory GreeksCache which is refreshed every 60s by
a background task that calls OpenAlgo's /api/v1/optiongreeks.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.database import get_db
from models.relational import Strategy as StrategyModel

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/greeks", tags=["greeks"])


# ── Response models ────────────────────────────────────────────────────────────

class LegGreeks(BaseModel):
    leg_id:      str
    symbol:      str
    action:      str
    option_type: str
    delta:       Optional[float] = None
    gamma:       Optional[float] = None
    theta:       Optional[float] = None
    vega:        Optional[float] = None
    # Signed delta = raw delta × signed_qty (BUY CE = +delta, SELL CE = -delta)
    signed_delta: Optional[float] = None


class StrategyGreeksResponse(BaseModel):
    strategy_id: str
    legs:        List[LegGreeks]
    net_delta:   Optional[float] = None
    net_gamma:   Optional[float] = None
    net_theta:   Optional[float] = None
    net_vega:    Optional[float] = None
    data_age_secs: Optional[float] = None


# ── Route ──────────────────────────────────────────────────────────────────────

@router.get("/{strategy_id}", response_model=StrategyGreeksResponse)
async def get_strategy_greeks(
    strategy_id: str,
    db: AsyncSession = Depends(get_db),
) -> StrategyGreeksResponse:
    """
    Return cached greeks for all option legs of a strategy.

    Non-option legs (futures/equities) will have null greeks.
    Net greeks are the signed sum across all legs:
      - BUY legs: +greeks  (long delta, long gamma)
      - SELL legs: -greeks (short delta, short gamma)
    """
    from alerts.alert_pipeline import strategy_legs_mirror
    from services.greeks_service import greeks_cache, is_option

    legs_data = strategy_legs_mirror.get(strategy_id)

    if legs_data is None:
        # Fall back: parse legs from the strategy's stored description JSON
        try:
            sid_uuid = uuid.UUID(strategy_id)
            result = await db.execute(
                select(StrategyModel).where(StrategyModel.id == sid_uuid)
            )
            row = result.scalar_one_or_none()
            if row and row.description:
                desc = json.loads(row.description)
                legs_data = []
                for leg in desc.get("legs", []):
                    inst = leg.get("instrument", {})
                    inst_type = inst.get("instrumentType", "")
                    legs_data.append({
                        "leg_id":      leg.get("id", ""),
                        "symbol":      inst.get("symbol", ""),
                        "action":      leg.get("side", "BUY"),
                        "option_type": inst_type if inst_type in ("CE", "PE", "FUT") else "",
                    })
        except Exception as exc:
            logger.debug("[greeks] fallback DB lookup failed for %s: %s", strategy_id, exc)

    if not legs_data:
        return StrategyGreeksResponse(strategy_id=strategy_id, legs=[])

    leg_responses: List[LegGreeks] = []
    net_delta = net_gamma = net_theta = net_vega = 0.0
    has_any_greeks = False

    for leg in legs_data:
        leg_id      = leg.get("leg_id", "")
        symbol      = leg.get("symbol", "")
        action      = leg.get("action", "BUY")
        option_type = leg.get("option_type", "")

        g = greeks_cache.get(symbol) if is_option(symbol, option_type) else None

        if g:
            has_any_greeks = True
            raw_delta = g["delta"]
            raw_gamma = g["gamma"]
            raw_theta = g["theta"]
            raw_vega  = g["vega"]

            sign = 1.0 if action == "BUY" else -1.0
            signed = sign * raw_delta
            net_delta += sign * raw_delta
            net_gamma += sign * raw_gamma
            net_theta += sign * raw_theta
            net_vega  += sign * raw_vega

            leg_responses.append(LegGreeks(
                leg_id=leg_id, symbol=symbol,
                action=action, option_type=option_type,
                delta=raw_delta, gamma=raw_gamma,
                theta=raw_theta, vega=raw_vega,
                signed_delta=signed,
            ))
        else:
            leg_responses.append(LegGreeks(
                leg_id=leg_id, symbol=symbol,
                action=action, option_type=option_type,
            ))

    return StrategyGreeksResponse(
        strategy_id=strategy_id,
        legs=leg_responses,
        net_delta=round(net_delta, 4) if has_any_greeks else None,
        net_gamma=round(net_gamma, 4) if has_any_greeks else None,
        net_theta=round(net_theta, 4) if has_any_greeks else None,
        net_vega=round(net_vega,  4) if has_any_greeks else None,
    )
