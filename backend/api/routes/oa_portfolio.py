"""
OA Portfolio proxy route.

Calls OpenAlgo's /api/v1/strategy-portfolio server-to-server using the
configured API key — no browser session, no CORS, production-safe.
"""
import logging
from typing import Any, Dict, List

import httpx
from fastapi import APIRouter
from pydantic import BaseModel

from core.config import settings

router = APIRouter(prefix="/oa-portfolio", tags=["oa-portfolio"])
logger = logging.getLogger(__name__)


class OAPortfolioResponse(BaseModel):
    status: str
    items: List[Dict[str, Any]]


def _oa_base() -> str:
    host = settings.openalgo_host.strip().rstrip("/")
    if not host.startswith("http"):
        host = f"http://{host}"
    return host


@router.get("", response_model=OAPortfolioResponse)
async def get_oa_portfolio() -> OAPortfolioResponse:
    """
    Proxy to OpenAlgo's strategy portfolio API using the configured API key.
    Returns the same {status, items} shape as the OA endpoint.
    """
    if not settings.openalgo_api_key:
        logger.warning("[oa-portfolio] OPENALGO_API_KEY not configured in .env")
        return OAPortfolioResponse(status="unconfigured", items=[])

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{_oa_base()}/api/v1/strategy-portfolio",
                json={"apikey": settings.openalgo_api_key},
            )
            data = resp.json()
            if data.get("status") == "success":
                return OAPortfolioResponse(status="success", items=data.get("items", []))
            logger.warning("[oa-portfolio] OA returned error: %s", data.get("message"))
            return OAPortfolioResponse(status="error", items=[])
    except Exception as exc:
        logger.warning("[oa-portfolio] request failed: %s", exc)
        return OAPortfolioResponse(status="error", items=[])
