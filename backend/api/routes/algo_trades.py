"""
Read-only endpoints over the unified strategies SQLite DB.

GET /api/v1/algo-trades/runs                  — list all strategy runs
GET /api/v1/algo-trades/runs/{run_id}         — single run + legs
GET /api/v1/algo-trades/runs/{run_id}/timeline — mtm + trades + signals + notifications
GET /api/v1/algo-trades/runs/{run_id}/mtm      — mtm snapshots only (for chart)
"""
import logging
import os
from pathlib import Path
from typing import Any, Optional

import aiosqlite
from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)
router = APIRouter(tags=["algo-trades"])


# ── DB path ───────────────────────────────────────────────────────────────────

def _db_path() -> str:
    env = os.environ.get("STRATEGIES_DB_PATH")
    if env:
        return env
    root = Path(__file__).resolve().parent.parent.parent.parent
    return str(root / "strategy_framework" / "logs" / "strategies.db")


def _row_to_dict(cursor: aiosqlite.Cursor, row: tuple) -> dict[str, Any]:
    return {col[0]: row[i] for i, col in enumerate(cursor.description)}


# ── DB helpers ────────────────────────────────────────────────────────────────

async def _fetch_all(sql: str, params: tuple = ()) -> list[dict]:
    path = _db_path()
    if not Path(path).exists():
        return []
    async with aiosqlite.connect(path) as db:
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [_row_to_dict(cur, r) for r in rows]


async def _fetch_one(sql: str, params: tuple = ()) -> Optional[dict]:
    path = _db_path()
    if not Path(path).exists():
        return None
    async with aiosqlite.connect(path) as db:
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return _row_to_dict(cur, row) if row else None


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("/algo-trades/runs")
async def list_runs(
    status: Optional[str] = Query(None, description="OPEN | CLOSED | PAUSED"),
    underlying: Optional[str] = Query(None),
    strategy_type: Optional[str] = Query(None, description="CREDIT_SPREAD | DIRECTIONAL_FUTURES | ..."),
    date_from: Optional[str] = Query(None, description="YYYY-MM-DD — filter by entry/created date (inclusive)"),
    date_to: Optional[str] = Query(None, description="YYYY-MM-DD — filter by entry/created date (inclusive)"),
    include_open_all_time: bool = Query(False, description="When True, OPEN runs are always included regardless of date filter"),
    limit: int = Query(200, le=500),
):
    """List strategy runs from the unified strategies DB, newest first.

    Default view (date_from = date_to = today, include_open_all_time = true):
      returns today's runs (any status) PLUS all OPEN runs from prior days.
    """
    where_clauses: list[str] = []
    params: list[Any] = []

    if status:
        where_clauses.append("r.status = ?")
        params.append(status.upper())
    if underlying:
        where_clauses.append("r.underlying = ?")
        params.append(underlying.upper())
    if strategy_type:
        where_clauses.append("r.strategy_type = ?")
        params.append(strategy_type.upper())

    # Date range filter on entry/created date
    # When include_open_all_time is set, OPEN runs bypass the date filter entirely
    if date_from or date_to:
        date_conditions: list[str] = []
        date_params: list[Any] = []
        if date_from and date_to:
            date_conditions.append(
                "date(COALESCE(r.entry_time, r.created_at)) BETWEEN ? AND ?"
            )
            date_params.extend([date_from, date_to])
        elif date_from:
            date_conditions.append("date(COALESCE(r.entry_time, r.created_at)) >= ?")
            date_params.append(date_from)
        else:
            date_conditions.append("date(COALESCE(r.entry_time, r.created_at)) <= ?")
            date_params.append(date_to)

        if include_open_all_time and not status:
            # (in date range) OR (open any time)
            where_clauses.append(f"({date_conditions[0]} OR r.status = 'OPEN')")
        else:
            where_clauses.append(date_conditions[0])
        params.extend(date_params)

    where = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sql = f"""
        SELECT r.*,
               (SELECT mtm   FROM strategy_mtm_snapshots s WHERE s.run_id = r.id ORDER BY ts DESC LIMIT 1) AS latest_mtm,
               (SELECT COUNT(*) FROM strategy_trades      t WHERE t.run_id = r.id) AS trade_count,
               (SELECT COUNT(*) FROM strategy_notifications n WHERE n.run_id = r.id) AS notif_count,
               (SELECT COUNT(*) FROM strategy_mtm_snapshots m WHERE m.run_id = r.id) AS snapshot_count
        FROM strategy_runs r
        {where}
        ORDER BY COALESCE(r.entry_time, r.created_at) DESC
        LIMIT ?
    """
    rows = await _fetch_all(sql, tuple(params + [limit]))
    return rows


@router.get("/algo-trades/runs/{run_id}")
async def get_run(run_id: int):
    """Single strategy run detail with its legs, plus latest SL and snapshot LTPs."""
    run = await _fetch_one("SELECT * FROM strategy_runs WHERE id = ?", (run_id,))
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    legs = await _fetch_all(
        "SELECT * FROM strategy_legs WHERE run_id = ? ORDER BY id", (run_id,)
    )
    # Latest MTM snapshot: locked_mtm = current trailing SL price; *_ltp = last known LTPs
    snap = await _fetch_one(
        "SELECT locked_mtm, primary_ltp, hedge_ltp, futures_ltp "
        "FROM strategy_mtm_snapshots WHERE run_id = ? ORDER BY id DESC LIMIT 1",
        (run_id,),
    )
    return {
        **run,
        "legs": legs,
        "current_sl":       snap["locked_mtm"]   if snap else None,
        "last_primary_ltp": snap["primary_ltp"]  if snap else None,
        "last_hedge_ltp":   snap["hedge_ltp"]    if snap else None,
        "last_futures_ltp": snap["futures_ltp"]  if snap else None,
    }


@router.get("/algo-trades/runs/{run_id}/timeline")
async def get_run_timeline(
    run_id: int,
    kind: Optional[str] = Query(None, description="mtm | trade | signal | adjustment | notification"),
    limit: int = Query(2000, le=10000),
):
    """Combined chronological timeline for a run."""
    run = await _fetch_one("SELECT id FROM strategy_runs WHERE id = ?", (run_id,))
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")

    events: list[dict] = []

    if not kind or kind == "mtm":
        for r in await _fetch_all(
            "SELECT * FROM strategy_mtm_snapshots WHERE run_id = ? ORDER BY ts", (run_id,)
        ):
            events.append({**r, "kind": "mtm"})

    if not kind or kind == "trade":
        for r in await _fetch_all(
            "SELECT * FROM strategy_trades WHERE run_id = ? ORDER BY COALESCE(fill_time, created_at)",
            (run_id,),
        ):
            events.append({**r, "kind": "trade"})

    if not kind or kind == "signal":
        for r in await _fetch_all(
            "SELECT * FROM strategy_signal_events WHERE run_id = ? ORDER BY ts", (run_id,)
        ):
            events.append({**r, "kind": "signal"})

    if not kind or kind == "adjustment":
        for r in await _fetch_all(
            "SELECT * FROM strategy_adjustments WHERE run_id = ? ORDER BY ts", (run_id,)
        ):
            events.append({**r, "kind": "adjustment"})

    if not kind or kind == "notification":
        for r in await _fetch_all(
            "SELECT * FROM strategy_notifications WHERE run_id = ? ORDER BY ts", (run_id,)
        ):
            events.append({**r, "kind": "notification"})

    def _ts(e: dict) -> str:
        return e.get("ts") or e.get("fill_time") or e.get("created_at") or ""

    events.sort(key=_ts)
    return events[:limit]


@router.get("/algo-trades/runs/{run_id}/mtm")
async def get_run_mtm(run_id: int):
    """MTM snapshots only — lightweight endpoint for chart rendering."""
    run = await _fetch_one("SELECT id FROM strategy_runs WHERE id = ?", (run_id,))
    if not run:
        raise HTTPException(status_code=404, detail="Run not found")
    return await _fetch_all(
        "SELECT ts, spot, mtm, max_mtm, locked_mtm, primary_ltp, hedge_ltp, futures_ltp, phase "
        "FROM strategy_mtm_snapshots WHERE run_id = ? ORDER BY ts",
        (run_id,),
    )
