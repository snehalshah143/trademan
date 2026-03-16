"""
Broker / application settings endpoints.

GET  /api/v1/settings         — current config (API key masked)
PATCH /api/v1/settings        — in-memory runtime update (lost on restart)
POST /api/v1/settings/broker  — persistent update: writes to .env + resets adapter
POST /api/v1/settings/test    — probe OpenAlgo with given credentials (no save)
"""
import os
from typing import Optional

import httpx
from dotenv import find_dotenv, set_key
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from adapters.adapter_factory import reset_adapter
from core.config import settings

router = APIRouter(prefix="/settings", tags=["settings"])


# ── Helpers ───────────────────────────────────────────────────────────────────

def _mask_key(key: str) -> str:
    """Return only the last 8 characters of the API key."""
    if not key:
        return ""
    return "…" + key[-8:] if len(key) > 8 else key


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class BrokerConfigOut(BaseModel):
    broker_adapter:              str
    openalgo_host:               str
    openalgo_ws_host:            str
    openalgo_api_key_masked:     str   # last 8 chars only
    ltp_poll_interval_seconds:   float
    ltp_stale_threshold_seconds: float
    store_ticks:                 bool
    mtm_snapshot_interval_seconds: float
    warmup_source:               str
    debug:                       bool


class BrokerConfigPatch(BaseModel):
    broker_adapter:              Optional[str]   = None
    openalgo_host:               Optional[str]   = None
    openalgo_ws_host:            Optional[str]   = None
    openalgo_api_key:            Optional[str]   = None
    ltp_poll_interval_seconds:   Optional[float] = None
    ltp_stale_threshold_seconds: Optional[float] = None
    store_ticks:                 Optional[bool]  = None
    mtm_snapshot_interval_seconds: Optional[float] = None
    warmup_source:               Optional[str]   = None


class BrokerSaveRequest(BaseModel):
    host:         Optional[str] = None
    ws_host:      Optional[str] = None
    api_key:      Optional[str] = None
    adapter_type: Optional[str] = None   # "mock" | "openalgo"


class BrokerTestRequest(BaseModel):
    host:    str
    api_key: str


class BrokerTestResponse(BaseModel):
    connected: bool
    message:   str


class BrokerSaveResponse(BaseModel):
    success:              bool
    broker_adapter:       str
    openalgo_api_key_masked: str


# ── Helpers ───────────────────────────────────────────────────────────────────

def _current_config() -> BrokerConfigOut:
    return BrokerConfigOut(
        broker_adapter=settings.broker_adapter,
        openalgo_host=settings.openalgo_host,
        openalgo_ws_host=settings.openalgo_ws_host,
        openalgo_api_key_masked=_mask_key(settings.openalgo_api_key),
        ltp_poll_interval_seconds=settings.ltp_poll_interval_seconds,
        ltp_stale_threshold_seconds=settings.ltp_stale_threshold_seconds,
        store_ticks=settings.store_ticks,
        mtm_snapshot_interval_seconds=settings.mtm_snapshot_interval_seconds,
        warmup_source=settings.warmup_source,
        debug=settings.debug,
    )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=BrokerConfigOut)
async def get_settings():
    """Return current settings.  API key is masked (last 8 chars)."""
    return _current_config()


@router.patch("", response_model=BrokerConfigOut)
async def update_settings(body: BrokerConfigPatch):
    """
    In-memory runtime update.  Changes take effect immediately but are
    lost on restart.  Use POST /settings/broker to persist to .env.
    """
    for field_name, value in body.model_dump(exclude_unset=True).items():
        if hasattr(settings, field_name):
            object.__setattr__(settings, field_name, value)
    return _current_config()


@router.post("/broker", response_model=BrokerSaveResponse)
async def save_broker_config(body: BrokerSaveRequest):
    """
    Persist broker connection settings to .env and reset the adapter singleton.
    The new adapter will be created on the next API request.
    """
    # Find (or default to) the .env file next to main.py / CWD
    env_path = find_dotenv(usecwd=True) or os.path.join(os.getcwd(), ".env")

    env_map = {
        "host":         ("OPENALGO_HOST",    "openalgo_host"),
        "ws_host":      ("OPENALGO_WS_HOST", "openalgo_ws_host"),
        "api_key":      ("OPENALGO_API_KEY", "openalgo_api_key"),
        "adapter_type": ("BROKER_ADAPTER",   "broker_adapter"),
    }

    for attr, (env_key, settings_attr) in env_map.items():
        value = getattr(body, attr)
        if value is not None:
            set_key(env_path, env_key, value)
            object.__setattr__(settings, settings_attr, value)

    # Reset adapter singleton so new settings take effect
    reset_adapter()

    # If switching to openalgo, eagerly connect the new adapter so
    # is_connected returns True immediately (not just on first API call)
    if settings.broker_adapter == "openalgo":
        try:
            from adapters.adapter_factory import get_adapter
            adapter = get_adapter()
            await adapter.connect()
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "[settings] eager adapter connect failed: %s", exc
            )

    return BrokerSaveResponse(
        success=True,
        broker_adapter=settings.broker_adapter,
        openalgo_api_key_masked=_mask_key(settings.openalgo_api_key),
    )


@router.post("/test", response_model=BrokerTestResponse)
async def test_broker_connection(body: BrokerTestRequest):
    """
    Probe OpenAlgo with the supplied credentials without saving anything.
    Creates a temporary HTTP client, calls /api/v1/funds, and returns the result.
    """
    host = body.host.strip()
    # Normalise: ensure http:// scheme, no trailing slash
    if not host.lower().startswith(("http://", "https://")):
        host = f"http://{host}"
    host = host.rstrip("/")

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=5.0)) as client:
            resp = await client.post(
                f"{host}/api/v1/funds",
                json={"apikey": body.api_key},
            )
            data = resp.json()
            if resp.status_code == 200 and data.get("status") == "success":
                return BrokerTestResponse(connected=True, message="Connected successfully")
            msg = data.get("message") or data.get("error") or resp.text
            return BrokerTestResponse(connected=False, message=str(msg))
    except httpx.ConnectError:
        return BrokerTestResponse(
            connected=False,
            message=f"Cannot reach OpenAlgo at {host}. Is it running?",
        )
    except Exception as exc:
        return BrokerTestResponse(connected=False, message=str(exc))
