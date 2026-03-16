"""
Instruments API — cache-first from local DB, enriched from OpenAlgo when available.

Endpoints:
  GET  /api/instruments/list          → instrument catalogue
  GET  /api/instruments/search        → filtered search
  GET  /api/instruments/expiries      → expiry list (cached or live)
  GET  /api/instruments/optionchain   → option chain proxy
  POST /api/instruments/greeks        → Black-Scholes Greeks
  POST /api/instruments/sync          → trigger manual full sync
  GET  /api/instruments/sync/status   → recent sync logs + stats
"""
from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.database import get_db
from services.instrument_sync_service import (
    days_to_expiry,
    expiry_display,
    instrument_sync_service,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/instruments", tags=["instruments"])


# ─── /list ────────────────────────────────────────────────────────────────────

@router.get("/list")
async def list_instruments(db: AsyncSession = Depends(get_db)):
    """Return F&O instrument catalogue. Served from DB cache; falls back to static seed."""
    instruments, source = await instrument_sync_service.get_instruments(db)
    if not instruments:
        # DB empty (first run before seed) — return empty; startup will seed
        return {"instruments": [], "source": "empty"}
    return {"instruments": instruments, "source": source}


# ─── /search ─────────────────────────────────────────────────────────────────

@router.get("/search")
async def search_instruments(
    q: str = Query("", min_length=1),
    exchange: str = Query("NFO"),
    db: AsyncSession = Depends(get_db),
):
    """Search instruments by symbol or full name (top 10 matches)."""
    instruments, _ = await instrument_sync_service.get_instruments(db)
    q_lower = q.lower()
    matches = [
        i for i in instruments
        if q_lower in i["symbol"].lower() or q_lower in i["fullName"].lower()
    ]
    return {"instruments": matches[:10]}


# ─── FUT expiry helper ────────────────────────────────────────────────────────

async def _get_fut_expiries(symbol: str, exchange: str) -> tuple[list[str], str]:
    """
    Fetch futures-only expiries from OpenAlgo (/api/v1/expiry, instrumenttype=futures).
    Converts 'DD-MON-YY' → 'YYYY-MM-DD'.  Falls back to cached DB expiries on error.
    """
    from datetime import datetime
    host = settings.openalgo_host.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
            resp = await client.post(
                f"{host}/api/v1/expiry",
                json={
                    "apikey":         settings.openalgo_api_key,
                    "symbol":         symbol,
                    "exchange":       exchange,
                    "instrumenttype": "futures",
                },
            )
            data = resp.json()
        if data.get("status") == "success":
            iso_dates: list[str] = []
            for raw in data.get("data", []):
                try:
                    iso_dates.append(datetime.strptime(raw, "%d-%b-%y").strftime("%Y-%m-%d"))
                except ValueError:
                    pass
            if iso_dates:
                return iso_dates, "openalgo"
    except Exception as exc:
        logger.warning("[expiries] FUT fetch failed for %s: %s", symbol, exc)
    return [], "empty"


# ─── /expiries ────────────────────────────────────────────────────────────────

@router.get("/expiries")
async def get_expiries(
    symbol: str = Query(...),
    exchange: str = Query("NFO"),
    instrument_type: str = Query(None, alias="type"),  # 'FUT' → futures-only from OpenAlgo
    db: AsyncSession = Depends(get_db),
):
    """
    Get available expiries for a symbol.

    When type=FUT, calls OpenAlgo /api/v1/expiry with instrumenttype=futures
    so only real monthly futures expiries are returned (no weekly option dates).

    Returns:
      expiries      : list[str]         — plain date strings for backward compat
      expiry_infos  : list[ExpiryInfo]  — rich objects with display + days
      source        : str               — 'openalgo' | 'cached' | 'computed'
    """
    if instrument_type and instrument_type.upper() == "FUT":
        expiry_strings, source = await _get_fut_expiries(symbol, exchange)
    else:
        expiry_strings, source = await instrument_sync_service.get_expiries(symbol, exchange, db)

    expiry_infos = [
        {
            "expiry": exp,
            "display": expiry_display(exp),
            "days_to_expiry": days_to_expiry(exp),
            "expiry_order": i,
        }
        for i, exp in enumerate(expiry_strings)
    ]

    return {
        "expiries": expiry_strings,
        "expiry_infos": expiry_infos,
        "source": source,
    }


# ─── /optionchain ────────────────────────────────────────────────────────────

def _to_openalgo_expiry(expiry_iso: str) -> str:
    """Convert YYYY-MM-DD → DDMMMYY format expected by OpenAlgo (e.g. '17MAR26')."""
    from datetime import date as _date
    try:
        d = _date.fromisoformat(expiry_iso)
        return f"{d.day:02d}{d.strftime('%b').upper()}{d.strftime('%y')}"
    except Exception:
        return expiry_iso


# Indices whose underlying exchange is NSE_INDEX / BSE_INDEX
_NSE_INDICES = {"NIFTY", "BANKNIFTY", "FINNIFTY", "NIFTYNXT50", "MIDCPNIFTY"}
_BSE_INDICES = {"SENSEX", "BANKEX"}

def _underlying_exchange(symbol: str, requested_exchange: str) -> str:
    """Return the correct exchange for the underlying (index vs equity)."""
    sym = symbol.upper()
    if sym in _NSE_INDICES:
        return "NSE_INDEX"
    if sym in _BSE_INDICES:
        return "BSE_INDEX"
    # For individual stocks the underlying exchange matches the options exchange
    return requested_exchange


def _f(v, default=0.0) -> float:
    try: return float(v or default)
    except (TypeError, ValueError): return default

def _i(v, default=0) -> int:
    try: return int(v or default)
    except (TypeError, ValueError): return default


def _normalise_openalgo_chain(data: dict, expiry: str) -> dict:
    """Map OpenAlgo option chain response to our internal format.

    OpenAlgo v2 returns:
      { status, underlying, underlying_ltp, atm_strike,
        chain: [ {strike, ce: {...}, pe: {...}}, ... ] }
    """
    if data.get("status") != "success":
        msg = data.get("message", "")
        error_code = "master_contract" if "master contract" in msg.lower() else "unavailable"
        return {
            "rows": [], "atm_strike": None, "spot": None,
            "synthetic_fut": None, "expiry": expiry,
            "error": msg, "error_code": error_code,
        }

    chain = data.get("chain", [])
    rows = []

    for entry in chain:
        try:
            strike = _f(entry.get("strike"))
            ce = entry.get("ce") or {}
            pe = entry.get("pe") or {}
            rows.append({
                "strike":       strike,
                "callLTP":      _f(ce.get("ltp")),
                "putLTP":       _f(pe.get("ltp")),
                "callBid":      _f(ce.get("bid")),
                "callAsk":      _f(ce.get("ask")),
                "putBid":       _f(pe.get("bid")),
                "putAsk":       _f(pe.get("ask")),
                "callDelta":    _f(ce.get("delta"), 0.5),
                "putDelta":     _f(pe.get("delta"), -0.5),
                "callIV":       _f(ce.get("iv") or ce.get("impliedVolatility")),
                "putIV":        _f(pe.get("iv") or pe.get("impliedVolatility")),
                "callOI":       _i(ce.get("oi") or ce.get("open_interest")),
                "putOI":        _i(pe.get("oi") or pe.get("open_interest")),
                "callOIChange": _i(ce.get("oi_change") or ce.get("oichange")),
                "putOIChange":  _i(pe.get("oi_change") or pe.get("oichange")),
                "callVolume":   _i(ce.get("volume")),
                "putVolume":    _i(pe.get("volume")),
                "callSymbol":   ce.get("symbol", ""),
                "putSymbol":    pe.get("symbol", ""),
                "callLabel":    ce.get("label", ""),
                "putLabel":     pe.get("label", ""),
            })
        except (ValueError, TypeError):
            continue

    rows.sort(key=lambda r: r["strike"])

    atm = data.get("atm_strike") or (rows[len(rows) // 2]["strike"] if rows else None)
    spot = data.get("underlying_ltp") or data.get("spot") or None

    return {
        "rows":          rows,
        "atm_strike":    atm,
        "spot":          spot,
        "synthetic_fut": data.get("synthetic_fut") or spot,
        "expiry":        expiry,
        "error":         None,
        "error_code":    None,
    }


# Simple in-process TTL cache for option chain (avoids hammering OpenAlgo on every re-render)
import time as _time
_chain_cache: dict[str, tuple[float, dict]] = {}  # key → (timestamp, data)
_CHAIN_TTL = 5  # seconds


@router.get("/optionchain")
async def get_option_chain(
    symbol: str = Query(...),
    exchange: str = Query("NFO"),
    expiry: str = Query(...),
    strike_count: int = Query(10),
):
    """Proxy option chain data from OpenAlgo (POST /api/v1/optionchain). 5 s in-process cache."""
    cache_key = f"{symbol}:{exchange}:{expiry}:{strike_count}"
    cached = _chain_cache.get(cache_key)
    if cached and (_time.monotonic() - cached[0]) < _CHAIN_TTL:
        return cached[1]

    host = settings.openalgo_host
    if not host.lower().startswith(("http://", "https://")):
        host = f"http://{host}"

    expiry_oa = _to_openalgo_expiry(expiry)  # YYYY-MM-DD → DDMMMYY
    oa_exchange = _underlying_exchange(symbol, exchange)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{host.rstrip('/')}/api/v1/optionchain",
                json={
                    "apikey":       settings.openalgo_api_key,
                    "underlying":   symbol,
                    "exchange":     oa_exchange,
                    "expiry_date":  expiry_oa,
                    "strike_count": strike_count,
                },
            )
            try:
                result = _normalise_openalgo_chain(resp.json(), expiry)
                if result.get("error") is None:
                    _chain_cache[cache_key] = (_time.monotonic(), result)
                return result
            except Exception:
                logger.debug("Option chain non-JSON response HTTP %s: %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.debug("Option chain fetch failed: %s", exc)

    return {
        "rows": [], "atm_strike": None, "spot": None,
        "synthetic_fut": None, "expiry": expiry,
        "error": "Could not reach OpenAlgo", "error_code": "unreachable",
    }


# ─── /optionsymbol ───────────────────────────────────────────────────────────

@router.get("/optionsymbol")
async def get_option_symbol(
    symbol: str = Query(...),
    exchange: str = Query("NFO"),
    expiry: str = Query(...),
    option_type: str = Query(...),
    offset: str = Query("ATM"),
):
    """Resolve an option symbol via OpenAlgo POST /api/v1/optionsymbol."""
    host = settings.openalgo_host
    if not host.lower().startswith(("http://", "https://")):
        host = f"http://{host}"

    expiry_oa = _to_openalgo_expiry(expiry)
    oa_exchange = _underlying_exchange(symbol, exchange)

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{host.rstrip('/')}/api/v1/optionsymbol",
                json={
                    "apikey":       settings.openalgo_api_key,
                    "underlying":   symbol,
                    "exchange":     oa_exchange,
                    "expiry_date":  expiry_oa,
                    "option_type":  option_type.upper(),
                    "offset":       offset,
                },
            )
            if resp.status_code == 200:
                data = resp.json()
                if data.get("status") == "success":
                    return {"symbol": data.get("symbol", ""), "status": "success"}
                return {"symbol": "", "status": "error", "message": data.get("message", "")}
    except Exception as exc:
        logger.debug("optionsymbol fetch failed: %s", exc)

    return {"symbol": "", "status": "error", "message": "Could not reach OpenAlgo"}


# ─── /greeks ─────────────────────────────────────────────────────────────────

@router.post("/greeks")
async def compute_greeks(body: dict):
    """Compute option Greeks using Black-Scholes (mibian) or approximation fallback."""
    legs = body.get("legs", [])
    results: list[dict[str, Any]] = []

    for leg in legs:
        leg_id = leg.get("legId", "")
        option_type = leg.get("optionType", "CE")
        try:
            import mibian  # type: ignore
            spot = float(leg.get("spotPrice", 0) or 22500)
            strike = float(leg.get("strike", spot))
            ltp = float(leg.get("ltp", 0) or 0)
            days = max(1, int(leg.get("daysToExpiry", 7) or 7))
            rate = 6.5

            iv = 20.0
            if ltp > 0 and option_type in ("CE", "PE"):
                try:
                    c_iv = mibian.BS(
                        [spot, strike, rate, days],
                        callPrice=ltp if option_type == "CE" else None,
                        putPrice=ltp if option_type == "PE" else None,
                    )
                    iv = c_iv.impliedVolatility
                except Exception:
                    pass

            c = mibian.BS([spot, strike, rate, days], volatility=iv)
            results.append({
                "legId": leg_id,
                "delta": c.callDelta if option_type == "CE" else c.putDelta,
                "gamma": c.gamma,
                "theta": c.callTheta if option_type == "CE" else c.putTheta,
                "vega": c.vega,
                "iv": iv,
            })
        except ImportError:
            results.append({
                "legId": leg_id,
                "delta": 0.5 if option_type == "CE" else -0.5,
                "gamma": 0.002, "theta": -5.0, "vega": 10.0, "iv": None,
            })
        except Exception as exc:
            logger.debug("Greeks computation failed for %s: %s", leg_id, exc)
            results.append({
                "legId": leg_id,
                "delta": 0.5 if option_type == "CE" else -0.5,
                "gamma": 0.001, "theta": -2.0, "vega": 5.0, "iv": None,
            })

    return results


# ─── /sync ───────────────────────────────────────────────────────────────────

@router.post("/sync")
async def trigger_sync(
    scope: str = Query("all", description="'all' | 'expiries'"),
    db: AsyncSession = Depends(get_db),
):
    """Trigger a manual instrument sync from OpenAlgo."""
    if scope == "expiries":
        result = await instrument_sync_service.sync_all_expiries(db)
    else:
        result = await instrument_sync_service.sync_all(db)
    return result


# ─── /sync/status ─────────────────────────────────────────────────────────────

@router.get("/sync/status")
async def get_sync_status(db: AsyncSession = Depends(get_db)):
    """Return recent sync logs and DB cache statistics."""
    stats = await instrument_sync_service.get_sync_stats(db)
    logs = await instrument_sync_service.get_recent_logs(db, limit=10)
    return {
        "stats": stats,
        "logs": [
            {
                "id": log.id,
                "operation": log.operation,
                "status": log.status,
                "message": log.message,
                "records_synced": log.records_synced,
                "started_at": log.started_at.isoformat() if log.started_at else None,
                "completed_at": log.completed_at.isoformat() if log.completed_at else None,
                "duration_ms": log.duration_ms,
            }
            for log in logs
        ],
    }
