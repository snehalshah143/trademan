"""
InstrumentSyncService — syncs F&O instrument data from OpenAlgo to local DB cache.

Strategy:
  1. On startup, seed static instruments (indices + F&O stocks) if DB is empty.
  2. Fetch expiries from OpenAlgo for each instrument and cache them.
  3. API routes serve from cache; fall back to computed expiries if offline.
"""
from __future__ import annotations

import logging
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from models.relational import CachedExpiry, CachedInstrument, InstrumentSyncLog

logger = logging.getLogger(__name__)

# ─── Seed data ────────────────────────────────────────────────────────────────

_SEED: list[tuple] = [
    # (symbol, display_name, full_name, category, exchange, lot_size, strike_interval)
    ("NIFTY",      "NIFTY 50",             "NIFTY 50",                   "INDEX", "NFO",   75,   50.0),
    ("BANKNIFTY",  "NIFTY BANK",           "NIFTY BANK",                 "INDEX", "NFO",   15,  100.0),
    ("FINNIFTY",   "NIFTY FIN SERVICE",    "NIFTY FINANCIAL SERVICES",   "INDEX", "NFO",   40,   50.0),
    ("SENSEX",     "SENSEX",               "S&P BSE SENSEX",             "INDEX", "BFO",   10,  100.0),
    ("MIDCPNIFTY", "NIFTY MIDCAP SELECT",  "NIFTY MIDCAP SELECT",        "INDEX", "NFO",   50,   25.0),
    ("RELIANCE",   "RELIANCE",   "Reliance Industries",     "STOCK", "NFO",  250,  50.0),
    ("TCS",        "TCS",        "Tata Consultancy",        "STOCK", "NFO",  150,  50.0),
    ("INFY",       "INFY",       "Infosys",                 "STOCK", "NFO",  300,  25.0),
    ("HDFCBANK",   "HDFCBANK",   "HDFC Bank",               "STOCK", "NFO",  550,  10.0),
    ("ICICIBANK",  "ICICIBANK",  "ICICI Bank",              "STOCK", "NFO",  700,  10.0),
    ("SBIN",       "SBIN",       "State Bank of India",     "STOCK", "NFO", 1500,   5.0),
    ("WIPRO",      "WIPRO",      "Wipro",                   "STOCK", "NFO", 1500,   5.0),
    ("AXISBANK",   "AXISBANK",   "Axis Bank",               "STOCK", "NFO",  625,  10.0),
    ("KOTAKBANK",  "KOTAKBANK",  "Kotak Mahindra Bank",     "STOCK", "NFO",  400,  10.0),
    ("LT",         "LT",         "Larsen & Toubro",         "STOCK", "NFO",  150,  25.0),
    ("BHARTIARTL", "BHARTIARTL", "Bharti Airtel",           "STOCK", "NFO",  950,   5.0),
    ("ITC",        "ITC",        "ITC Limited",             "STOCK", "NFO", 3200,   2.0),
    ("BAJFINANCE", "BAJFINANCE", "Bajaj Finance",           "STOCK", "NFO",  125,  50.0),
    ("ASIANPAINT", "ASIANPAINT", "Asian Paints",            "STOCK", "NFO",  200,  25.0),
    ("MARUTI",     "MARUTI",     "Maruti Suzuki",           "STOCK", "NFO",  100, 100.0),
    ("TATASTEEL",  "TATASTEEL",  "Tata Steel",              "STOCK", "NFO", 5500,   2.0),
    ("ONGC",       "ONGC",       "ONGC",                    "STOCK", "NFO", 4850,   2.0),
    ("POWERGRID",  "POWERGRID",  "Power Grid",              "STOCK", "NFO", 4700,   2.0),
    ("NTPC",       "NTPC",       "NTPC",                    "STOCK", "NFO", 3750,   2.0),
    ("ADANIENT",   "ADANIENT",   "Adani Enterprises",       "STOCK", "NFO",  250,  25.0),
    ("HINDUNILVR", "HINDUNILVR", "Hindustan Unilever",      "STOCK", "NFO",  300,  25.0),
    ("SUNPHARMA",  "SUNPHARMA",  "Sun Pharmaceutical",      "STOCK", "NFO",  700,  10.0),
    ("DRREDDY",    "DRREDDY",    "Dr. Reddy's Lab",         "STOCK", "NFO",  125,  50.0),
    ("TECHM",      "TECHM",      "Tech Mahindra",           "STOCK", "NFO",  600,  10.0),
    ("HCLTECH",    "HCLTECH",    "HCL Technologies",        "STOCK", "NFO",  700,  10.0),
    ("ULTRACEMCO", "ULTRACEMCO", "UltraTech Cement",        "STOCK", "NFO",   50, 200.0),
    ("TITAN",      "TITAN",      "Titan Company",           "STOCK", "NFO",  375,  25.0),
    ("BAJAJFINSV", "BAJAJFINSV", "Bajaj Finserv",           "STOCK", "NFO", 1000,   5.0),
    ("NESTLEIND",  "NESTLEIND",  "Nestle India",            "STOCK", "NFO",  100, 100.0),
    ("DIVISLAB",   "DIVISLAB",   "Divi's Laboratories",     "STOCK", "NFO",  200,  50.0),
]

# Icon metadata for frontend (by category)
_ICON_META: dict[str, dict] = {
    "NIFTY":      {"iconBg": "#1565C0", "iconText": "50", "iconColor": "#FFFFFF"},
    "BANKNIFTY":  {"iconBg": "#E65100", "iconText": "B",  "iconColor": "#FFFFFF"},
    "FINNIFTY":   {"iconBg": "#558B2F", "iconText": "FN", "iconColor": "#FFFFFF"},
    "SENSEX":     {"iconBg": "#6A1B9A", "iconText": "S",  "iconColor": "#FFFFFF"},
    "MIDCPNIFTY": {"iconBg": "#37474F", "iconText": "MC", "iconColor": "#FFFFFF"},
}


def _icon_for(symbol: str, category: str) -> dict:
    if symbol in _ICON_META:
        return _ICON_META[symbol]
    return {"iconBg": "#455A64", "iconText": symbol[:2].upper(), "iconColor": "#FFFFFF"}


# ─── Expiry helpers ──────────────────────────────────────────────────────────

def _last_thursday_of_month(year: int, month: int) -> date:
    last_day = monthrange(year, month)[1]
    last = date(year, month, last_day)
    # weekday: 0=Mon … 3=Thu
    offset = (last.weekday() - 3) % 7
    return last - timedelta(days=offset)


def compute_monthly_expiries(count: int = 3) -> list[str]:
    """Fallback: compute next N monthly expiries (last Thursday of month)."""
    expiries: list[str] = []
    today = date.today()
    year, month = today.year, today.month
    attempts = 0
    while len(expiries) < count and attempts < count + 3:
        thu = _last_thursday_of_month(year, month)
        if thu > today:
            expiries.append(thu.isoformat())
        month += 1
        if month > 12:
            month = 1
            year += 1
        attempts += 1
    return expiries


def expiry_display(expiry_str: str) -> str:
    """Format YYYY-MM-DD → '27 Mar' (cross-platform, works on Windows)."""
    try:
        d = date.fromisoformat(expiry_str)
        return f"{d.day} {d.strftime('%b')}"
    except Exception:
        return expiry_str


def days_to_expiry(expiry_str: str) -> int:
    try:
        d = date.fromisoformat(expiry_str)
        return max(0, (d - date.today()).days)
    except Exception:
        return 0


# ─── Service ─────────────────────────────────────────────────────────────────

class InstrumentSyncService:

    # ── Seeding ──────────────────────────────────────────────────────────────

    async def seed_static_instruments(self, db: AsyncSession) -> int:
        """Seed DB with static instruments if the table is empty. Returns inserted count."""
        result = await db.execute(select(CachedInstrument).limit(1))
        if result.scalar_one_or_none() is not None:
            return 0

        for (sym, disp, full, cat, exch, lot, strike_int) in _SEED:
            db.add(CachedInstrument(
                symbol=sym, display_name=disp, full_name=full,
                category=cat, exchange=exch, lot_size=lot,
                strike_interval=strike_int, has_options=True,
                has_futures=True, source="static",
            ))
        await db.commit()
        logger.info("[InstrumentSync] Seeded %d static instruments", len(_SEED))
        return len(_SEED)

    # ── OpenAlgo fetch ───────────────────────────────────────────────────────

    @staticmethod
    def _normalise_expiry_date(raw: str) -> str:
        """
        Convert any OpenAlgo date format to YYYY-MM-DD.
        Handles: DD-MMM-YY (17-MAR-26), DD-MMM-YYYY (17-MAR-2026),
                 YYYY-MM-DD (already normalised), DD/MM/YYYY.
        Returns empty string on parse failure.
        """
        raw = raw.strip()
        if not raw:
            return ""
        # Already YYYY-MM-DD
        if len(raw) == 10 and raw[4] == "-" and raw[7] == "-":
            return raw
        for fmt in ("%d-%b-%y", "%d-%b-%Y", "%d/%m/%Y", "%Y/%m/%d"):
            try:
                return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
        logger.debug("[InstrumentSync] Could not parse expiry date: %r", raw)
        return ""

    @staticmethod
    def _openalgo_base_url() -> str:
        """Return the OpenAlgo base URL, ensuring it has an http:// scheme."""
        host = settings.openalgo_host
        if not host.lower().startswith(("http://", "https://")):
            host = f"http://{host}"
        return host.rstrip("/")

    async def _fetch_expiries_openalgo(self, symbol: str, exchange: str) -> list[str]:
        """Fetch expiry strings from OpenAlgo POST /api/v1/expiry. Returns [] on any error."""
        base = self._openalgo_base_url()
        api_key = settings.openalgo_api_key
        expiries: list[str] = []

        # Fetch both options and futures expiries
        for instrument_type in ("options", "futures"):
            try:
                async with httpx.AsyncClient(timeout=10.0) as client:
                    resp = await client.post(
                        f"{base}/api/v1/expiry",
                        json={
                            "apikey":         api_key,
                            "symbol":         symbol,
                            "exchange":       exchange,
                            "instrumenttype": instrument_type,
                        },
                    )
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    if data.get("status") != "success":
                        continue
                    raw = data.get("data") or data.get("expiries") or []
                    if isinstance(raw, list):
                        for e in raw:
                            s = self._normalise_expiry_date(str(e).strip())
                            if s and s not in expiries:
                                expiries.append(s)
            except Exception as exc:
                logger.debug(
                    "[InstrumentSync] OpenAlgo expiry fetch failed (%s %s %s): %s",
                    symbol, exchange, instrument_type, exc,
                )
        return expiries

    async def _fetch_instruments_openalgo(self) -> list[dict]:
        """Fetch F&O stock list from OpenAlgo symbols endpoint."""
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                url = f"{self._openalgo_base_url()}/api/v1/symbols?exchange=NFO"
                resp = await client.get(url)
                if resp.status_code == 200:
                    data = resp.json()
                    return data if isinstance(data, list) else data.get("data", [])
        except Exception as exc:
            logger.debug("[InstrumentSync] OpenAlgo instruments fetch failed: %s", exc)
        return []

    # ── Sync operations ──────────────────────────────────────────────────────

    async def sync_expiries_for_symbol(
        self, symbol: str, exchange: str, db: AsyncSession
    ) -> list[str]:
        """Fetch expiries from OpenAlgo and upsert into DB. Returns expiry strings."""
        expiries = await self._fetch_expiries_openalgo(symbol, exchange)
        if not expiries:
            return []

        # Delete old + insert fresh (clean upsert)
        await db.execute(
            delete(CachedExpiry).where(
                CachedExpiry.symbol == symbol,
                CachedExpiry.exchange == exchange,
            )
        )
        for order, exp in enumerate(expiries):
            db.add(CachedExpiry(
                symbol=symbol, exchange=exchange,
                expiry=exp, expiry_type="monthly", expiry_order=order,
            ))
        await db.commit()
        return expiries

    async def sync_all_expiries(self, db: AsyncSession) -> dict:
        """Sync expiries for all instruments in DB. Returns summary dict."""
        t0 = datetime.now(timezone.utc)
        result = await db.execute(select(CachedInstrument))
        instruments = list(result.scalars().all())

        total_expiries = 0
        synced_symbols = 0
        failed: list[str] = []

        for inst in instruments:
            expiries = await self.sync_expiries_for_symbol(inst.symbol, inst.exchange, db)
            if expiries:
                total_expiries += len(expiries)
                synced_symbols += 1
            else:
                failed.append(inst.symbol)

        duration_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
        status = "success" if not failed else ("partial" if synced_symbols > 0 else "error")
        message = (
            f"Synced {total_expiries} expiries for {synced_symbols}/{len(instruments)} instruments"
            + (f". Failed: {', '.join(failed[:5])}" if failed else "")
        )
        await self._log(
            "sync_expiries", status, message,
            total_expiries, duration_ms, db,
        )
        return {
            "status": status, "records": total_expiries,
            "synced": synced_symbols, "failed": failed,
            "duration_ms": duration_ms,
        }

    async def sync_instruments_from_openalgo(self, db: AsyncSession) -> int:
        """Fetch F&O stock list from OpenAlgo and update lot sizes in DB."""
        items = await self._fetch_instruments_openalgo()
        if not items:
            return 0

        known = {sym for (sym, *_) in _SEED}
        updated = 0
        for item in items:
            sym = item.get("symbol") or item.get("ticker") or ""
            if not sym or sym in ("FUT", "CE", "PE") or "FUT" in sym:
                continue
            lot = item.get("lotSize") or item.get("lot_size") or item.get("lotsSize")
            if not lot:
                continue

            result = await db.execute(
                select(CachedInstrument).where(CachedInstrument.symbol == sym)
            )
            inst = result.scalar_one_or_none()
            if inst:
                inst.lot_size = int(lot)
                inst.source = "openalgo"
                updated += 1
            elif sym not in known:
                # Add new instrument discovered from OpenAlgo
                db.add(CachedInstrument(
                    symbol=sym,
                    display_name=item.get("displayName", sym),
                    full_name=item.get("name", sym),
                    category="STOCK",
                    exchange="NFO",
                    lot_size=int(lot),
                    strike_interval=float(item.get("strikeInterval") or item.get("tick_size") or 1),
                    source="openalgo",
                ))
                updated += 1

        await db.commit()
        return updated

    async def sync_all(self, db: AsyncSession) -> dict:
        """Full sync: seed → enrich from OpenAlgo → sync expiries. Non-raising."""
        t0 = datetime.now(timezone.utc)
        seeded = await self.seed_static_instruments(db)
        enriched = await self.sync_instruments_from_openalgo(db)
        exp_result = await self.sync_all_expiries(db)
        duration_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)

        status = exp_result.get("status", "error")
        message = (
            f"Seeded {seeded} | Enriched {enriched} | "
            f"Expiries: {exp_result.get('records', 0)} across {exp_result.get('synced', 0)} symbols"
        )
        await self._log("sync_all", status, message, exp_result.get("records", 0), duration_ms, db)
        return {
            "seeded": seeded, "enriched": enriched,
            "status": status, "message": message,
            "expiries": exp_result.get("records", 0),
            "duration_ms": duration_ms,
        }

    # ── Read helpers ─────────────────────────────────────────────────────────

    async def get_expiries(
        self, symbol: str, exchange: str, db: AsyncSession
    ) -> tuple[list[str], str]:
        """
        Returns (expiry_strings, source).
        source: 'cached' | 'live' | 'computed'
        """
        result = await db.execute(
            select(CachedExpiry)
            .where(CachedExpiry.symbol == symbol, CachedExpiry.exchange == exchange)
            .order_by(CachedExpiry.expiry_order)
        )
        cached = list(result.scalars().all())
        if cached:
            return [e.expiry for e in cached], "cached"

        # Not in DB — try live fetch and cache
        live = await self.sync_expiries_for_symbol(symbol, exchange, db)
        if live:
            return live, "live"

        # Final fallback: compute last-Thursday expiries
        return compute_monthly_expiries(), "computed"

    async def get_instruments(self, db: AsyncSession) -> tuple[list[dict], str]:
        """Returns (instruments_list_for_api, source)."""
        result = await db.execute(select(CachedInstrument))
        rows = list(result.scalars().all())
        if not rows:
            return [], "empty"

        source = "openalgo" if any(r.source == "openalgo" for r in rows) else "static"
        instruments = []
        for r in rows:
            icon = _icon_for(r.symbol, r.category)
            instruments.append({
                "symbol": r.symbol,
                "displayName": r.display_name,
                "fullName": r.full_name,
                "category": r.category,
                "exchange": r.exchange,
                "lotSize": r.lot_size,
                "strikeInterval": r.strike_interval or 50,
                "hasOptions": r.has_options,
                "hasFutures": r.has_futures,
                "source": r.source,
                **icon,
            })
        return instruments, source

    async def is_expiry_cache_fresh(
        self, symbol: str, exchange: str, db: AsyncSession, max_age_hours: int = 24
    ) -> bool:
        """Check if cached expiries are younger than max_age_hours."""
        result = await db.execute(
            select(CachedExpiry.synced_at)
            .where(CachedExpiry.symbol == symbol, CachedExpiry.exchange == exchange)
            .limit(1)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return False
        synced = row.replace(tzinfo=timezone.utc) if row.tzinfo is None else row
        return (datetime.now(timezone.utc) - synced).total_seconds() < max_age_hours * 3600

    async def get_sync_stats(self, db: AsyncSession) -> dict:
        """Count instruments and expiries in cache."""
        instr_count_result = await db.execute(select(CachedInstrument))
        instr_count = len(list(instr_count_result.scalars().all()))
        exp_count_result = await db.execute(select(CachedExpiry))
        exp_count = len(list(exp_count_result.scalars().all()))
        return {"instruments": instr_count, "expiries": exp_count}

    # ── Logging ──────────────────────────────────────────────────────────────

    async def _log(
        self,
        operation: str, status: str, message: str,
        records: int, duration_ms: int,
        db: AsyncSession,
    ) -> None:
        log = InstrumentSyncLog(
            operation=operation, status=status, message=message,
            records_synced=records, completed_at=datetime.now(timezone.utc),
            duration_ms=duration_ms,
        )
        db.add(log)
        try:
            await db.commit()
        except Exception as exc:
            logger.debug("Sync log commit failed: %s", exc)
            await db.rollback()

    async def get_recent_logs(self, db: AsyncSession, limit: int = 10) -> list[InstrumentSyncLog]:
        result = await db.execute(
            select(InstrumentSyncLog)
            .order_by(InstrumentSyncLog.started_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())


# Singleton
instrument_sync_service = InstrumentSyncService()
