"""
GreeksCache — background service that keeps option greeks fresh in memory.

Design goals:
  • Zero I/O in the alert engine hot path — context builder reads from
    the in-memory dict synchronously.
  • One call to OA per option symbol per refresh cycle (60 s default).
  • Graceful degradation — if OA is unreachable the cache is stale but
    the alert engine simply returns False for delta conditions (no crash).

Exchange heuristic is the same as openalgo_adapter.py so MCX/BFO symbols
are routed correctly.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Dict, List, Optional, Tuple

import httpx

logger = logging.getLogger(__name__)

# ── Exchange heuristic ─────────────────────────────────────────────────────────
_MCX_PREFIXES = (
    "CRUDEOIL", "GOLD", "SILVER", "NATURALGAS", "COPPER",
    "ALUMINUM", "ZINC", "LEAD", "NICKEL", "MENTHAOIL",
)
_BFO_PREFIXES = ("SENSEX", "BANKEX")


def _infer_exchange(symbol: str) -> str:
    su = symbol.upper()
    if any(su.startswith(p) for p in _MCX_PREFIXES):
        return "MCX"
    if any(su.startswith(p) for p in _BFO_PREFIXES):
        return "BFO"
    return "NFO"


def is_option(symbol: str, option_type: str = "") -> bool:
    """Return True if the leg is a CE or PE option."""
    if option_type in ("CE", "PE"):
        return True
    su = symbol.upper()
    return su.endswith("CE") or su.endswith("PE")


# ── Cache ──────────────────────────────────────────────────────────────────────

class GreeksCache:
    """
    In-memory store for option greeks.  Keyed by symbol.

    Updated by a background task every `refresh_interval` seconds.
    Read by SymbolWorker._build_context() on every LTP tick — no I/O.
    """

    def __init__(self, refresh_interval: int = 60) -> None:
        self._cache: Dict[str, Dict[str, float]] = {}
        self._refresh_interval = refresh_interval
        self._task: Optional[asyncio.Task] = None

    # ── Public read interface (sync — called from hot path) ────────────────────

    def get(self, symbol: str) -> Optional[Dict[str, float]]:
        """Return {delta, gamma, theta, vega} or None if not cached."""
        return self._cache.get(symbol)

    def get_delta(self, symbol: str) -> Optional[float]:
        g = self._cache.get(symbol)
        return g["delta"] if g else None

    # ── Fetch from OA ──────────────────────────────────────────────────────────

    async def fetch_for_symbols(
        self,
        symbols_exchanges: List[Tuple[str, str]],
    ) -> None:
        """
        Call OA's /api/v1/optiongreeks for each (symbol, exchange) pair and
        store the results.  Safe to call concurrently.
        """
        from core.config import settings

        api_key = settings.openalgo_api_key
        if not api_key:
            logger.debug("[GreeksCache] OPENALGO_API_KEY not set — skipping greeks fetch")
            return

        host = settings.openalgo_host.strip().rstrip("/")
        if not host.startswith("http"):
            host = f"http://{host}"

        async with httpx.AsyncClient(timeout=8.0) as client:
            for symbol, exchange in symbols_exchanges:
                try:
                    resp = await client.post(
                        f"{host}/api/v1/optiongreeks",
                        json={"apikey": api_key, "symbol": symbol, "exchange": exchange},
                    )
                    data = resp.json()
                    if data.get("status") == "success":
                        g = data.get("greeks", {})
                        self._cache[symbol] = {
                            "delta": float(g.get("delta", 0.0)),
                            "gamma": float(g.get("gamma", 0.0)),
                            "theta": float(g.get("theta", 0.0)),
                            "vega":  float(g.get("vega",  0.0)),
                        }
                    else:
                        logger.debug(
                            "[GreeksCache] OA error for %s: %s",
                            symbol, data.get("message", "")
                        )
                except Exception as exc:
                    logger.debug("[GreeksCache] fetch failed %s: %s", symbol, exc)

    # ── Background refresh ─────────────────────────────────────────────────────

    async def _collect_option_symbols(self) -> List[Tuple[str, str]]:
        """Collect all option symbols currently in the alert engine legs mirror."""
        try:
            from alerts.alert_pipeline import strategy_legs_mirror
            seen: set[str] = set()
            result: List[Tuple[str, str]] = []
            for legs in strategy_legs_mirror.values():
                for leg in legs:
                    sym = leg.get("symbol", "")
                    opt = leg.get("option_type", "")
                    if sym and is_option(sym, opt) and sym not in seen:
                        seen.add(sym)
                        result.append((sym, _infer_exchange(sym)))
            return result
        except Exception:
            return []

    async def _do_refresh(self) -> None:
        symbols = await self._collect_option_symbols()
        if not symbols:
            return
        await self.fetch_for_symbols(symbols)
        logger.info("[GreeksCache] refreshed greeks for %d option symbols", len(symbols))

    async def _loop(self) -> None:
        # Initial refresh at startup
        try:
            await self._do_refresh()
        except Exception as exc:
            logger.warning("[GreeksCache] initial refresh error: %s", exc)
        # Then periodic refresh
        while True:
            try:
                await asyncio.sleep(self._refresh_interval)
                await self._do_refresh()
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("[GreeksCache] refresh loop error: %s", exc)

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._task = asyncio.create_task(self._loop())
        logger.info("[GreeksCache] started (refresh every %ds)", self._refresh_interval)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("[GreeksCache] stopped")


# Singleton
greeks_cache = GreeksCache()
