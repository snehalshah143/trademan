"""
reporter.py
-----------
Performance analytics for the credit spread strategy.

Loads closed runs from the DB and computes:
  - Win rate, average P&L, total P&L
  - Best / worst trade
  - Max drawdown (from MTM snapshot curve)
  - Avg holding duration
  - Per-spread-type and per-trade-mode breakdown
  - Sharpe-like ratio (daily P&L / std-dev)

All functions accept a SQLAlchemy Session.  The ``StrategyReport`` dataclass
holds all computed metrics and can be printed or serialised to dict/JSON.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Optional

from .credit_spread_db import (
    CsStrategyRun, CsMtmSnapshot,
    get_closed_runs, get_mtm_snapshots,
)

try:
    from sqlalchemy.orm import Session
except ImportError:
    Session = object  # type: ignore


# ---------------------------------------------------------------------------
# Data container
# ---------------------------------------------------------------------------

@dataclass
class RunSummary:
    """Single-run summary used inside StrategyReport."""
    run_id:       int
    strategy_id:  str
    spread_type:  str
    underlying:   str
    trade_mode:   str          # MONITOR_ONLY | ENTRY_EXEC_MONITOR | EXIT_EXEC_MONITOR | FULL_EXEC
    entry_time:   Optional[datetime]
    exit_time:    Optional[datetime]
    exit_reason:  Optional[str]
    realized_pnl: float
    duration_min: float        # holding time in minutes
    max_profit:   float        # peak MTM during the run
    max_loss:     float        # worst MTM during the run


@dataclass
class BreakdownRow:
    label:        str
    count:        int
    wins:         int
    win_rate_pct: float
    total_pnl:    float
    avg_pnl:      float
    best_trade:   float
    worst_trade:  float


@dataclass
class StrategyReport:
    """Complete performance report."""
    generated_at:    datetime
    trade_mode:      str            # filter used — ALL / PAPER / LIVE
    total_runs:      int
    wins:            int
    losses:          int
    win_rate_pct:    float
    total_pnl:       float
    avg_pnl:         float
    best_trade:      float
    worst_trade:     float
    avg_duration_min: float
    max_drawdown:    float          # largest peak-to-trough in MTM curves
    sharpe_ratio:    float          # simplified: mean(daily_pnl) / std(daily_pnl)
    by_spread_type:  list[BreakdownRow] = field(default_factory=list)
    by_trade_mode:   list[BreakdownRow] = field(default_factory=list)
    by_exit_reason:  list[BreakdownRow] = field(default_factory=list)
    runs:            list[RunSummary]  = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Core builder
# ---------------------------------------------------------------------------

def build_report(
    session: Session,
    trade_mode: Optional[str] = None,
    spread_type: Optional[str] = None,
    underlying: Optional[str] = None,
    limit: int = 500,
) -> StrategyReport:
    """
    Build a performance report from closed DB runs.

    Parameters
    ----------
    session     : SQLAlchemy Session.
    trade_mode  : Filter to ``"PAPER"`` or ``"LIVE"``.  None = all.
    spread_type : Filter to ``"BULL_PUT_SPREAD"`` or ``"BEAR_CALL_SPREAD"``.
    underlying  : Filter to ``"NIFTY"`` / ``"BANKNIFTY"`` etc.
    limit       : Max number of closed runs to load.
    """
    closed_runs = get_closed_runs(
        session,
        trade_mode=trade_mode,
        spread_type=spread_type,
        underlying=underlying,
        limit=limit,
    )

    run_summaries: list[RunSummary] = []
    all_drawdowns: list[float] = []

    for run in closed_runs:
        snapshots = get_mtm_snapshots(session, run.id)
        peak, trough, drawdown = _max_drawdown(snapshots)
        all_drawdowns.append(drawdown)

        peak_mtm, worst_mtm = _peak_worst_mtm(snapshots)

        dur_min = 0.0
        if run.entry_time and run.exit_time:
            dur_min = (run.exit_time - run.entry_time).total_seconds() / 60.0

        run_summaries.append(RunSummary(
            run_id=run.id,
            strategy_id=run.strategy_id,
            spread_type=run.spread_type,
            underlying=run.underlying,
            trade_mode=run.trade_mode,
            entry_time=run.entry_time,
            exit_time=run.exit_time,
            exit_reason=run.exit_reason,
            realized_pnl=run.realized_pnl or 0.0,
            duration_min=dur_min,
            max_profit=peak_mtm,
            max_loss=worst_mtm,
        ))

    if not run_summaries:
        return StrategyReport(
            generated_at=datetime.utcnow(),
            trade_mode=trade_mode or "ALL",
            total_runs=0, wins=0, losses=0, win_rate_pct=0.0,
            total_pnl=0.0, avg_pnl=0.0, best_trade=0.0, worst_trade=0.0,
            avg_duration_min=0.0, max_drawdown=0.0, sharpe_ratio=0.0,
        )

    pnls        = [r.realized_pnl for r in run_summaries]
    wins        = sum(1 for p in pnls if p > 0)
    losses      = len(pnls) - wins
    total_pnl   = sum(pnls)
    avg_pnl     = total_pnl / len(pnls)
    best        = max(pnls)
    worst       = min(pnls)
    avg_dur     = statistics.mean(r.duration_min for r in run_summaries)
    max_dd      = max(all_drawdowns) if all_drawdowns else 0.0
    sharpe      = _sharpe(run_summaries)

    report = StrategyReport(
        generated_at=datetime.utcnow(),
        trade_mode=trade_mode or "ALL",
        total_runs=len(run_summaries),
        wins=wins,
        losses=losses,
        win_rate_pct=round(wins / len(run_summaries) * 100, 1),
        total_pnl=round(total_pnl, 2),
        avg_pnl=round(avg_pnl, 2),
        best_trade=round(best, 2),
        worst_trade=round(worst, 2),
        avg_duration_min=round(avg_dur, 1),
        max_drawdown=round(max_dd, 2),
        sharpe_ratio=round(sharpe, 3),
        by_spread_type=_breakdown(run_summaries, lambda r: r.spread_type),
        by_trade_mode=_breakdown(run_summaries, lambda r: r.trade_mode),
        by_exit_reason=_breakdown(run_summaries, lambda r: r.exit_reason or "UNKNOWN"),
        runs=run_summaries,
    )
    return report


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _max_drawdown(snapshots: list[CsMtmSnapshot]) -> tuple[float, float, float]:
    """
    Compute max drawdown from a list of MTM snapshots.

    Returns (peak_value, trough_value, drawdown_amount).
    Drawdown is expressed as a positive number (magnitude of drop).
    """
    if not snapshots:
        return 0.0, 0.0, 0.0

    peak     = float("-inf")
    drawdown = 0.0

    for snap in snapshots:
        mtm = snap.mtm
        if mtm > peak:
            peak = mtm
        drop = peak - mtm
        if drop > drawdown:
            drawdown = drop

    trough = peak - drawdown
    return peak, trough, drawdown


def _peak_worst_mtm(snapshots: list[CsMtmSnapshot]) -> tuple[float, float]:
    """Return (max_mtm, min_mtm) across all snapshots."""
    if not snapshots:
        return 0.0, 0.0
    values = [s.mtm for s in snapshots]
    return max(values), min(values)


def _breakdown(
    runs: list[RunSummary],
    key_fn,
) -> list[BreakdownRow]:
    """Group runs by key_fn and compute per-group stats."""
    groups: dict[str, list[RunSummary]] = {}
    for r in runs:
        k = key_fn(r)
        groups.setdefault(k, []).append(r)

    rows = []
    for label, grp in sorted(groups.items()):
        pnls  = [r.realized_pnl for r in grp]
        wins  = sum(1 for p in pnls if p > 0)
        rows.append(BreakdownRow(
            label=label,
            count=len(grp),
            wins=wins,
            win_rate_pct=round(wins / len(grp) * 100, 1),
            total_pnl=round(sum(pnls), 2),
            avg_pnl=round(statistics.mean(pnls), 2),
            best_trade=round(max(pnls), 2),
            worst_trade=round(min(pnls), 2),
        ))
    return rows


def _sharpe(runs: list[RunSummary]) -> float:
    """
    Simplified daily Sharpe-like ratio.

    Groups realized P&L by calendar date, then computes mean/std.
    Risk-free rate assumed zero.
    """
    if len(runs) < 2:
        return 0.0

    daily: dict[str, float] = {}
    for r in runs:
        day = (r.entry_time or datetime.utcnow()).strftime("%Y-%m-%d")
        daily[day] = daily.get(day, 0.0) + r.realized_pnl

    values = list(daily.values())
    if len(values) < 2:
        return 0.0

    mean  = statistics.mean(values)
    stdev = statistics.stdev(values)
    if stdev == 0:
        return 0.0
    return mean / stdev
