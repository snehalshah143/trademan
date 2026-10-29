"""
report.py
---------
CLI script to print a formatted performance report for the credit spread strategy.

Usage
-----
    # From strategy_framework/ directory:
    python -m strategies.credit_spread.report

    # Filter to LIVE trades only:
    python -m strategies.credit_spread.report --mode LIVE

    # Filter to PAPER trades, BULL_PUT_SPREAD only:
    python -m strategies.credit_spread.report --mode PAPER --spread BULL_PUT_SPREAD

    # Custom DB path:
    python -m strategies.credit_spread.report --db sqlite:///logs/strategies.db

    # Show individual trade details:
    python -m strategies.credit_spread.report --trades

    # Export to JSON:
    python -m strategies.credit_spread.report --json report_out.json
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_framework_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_framework_root) not in sys.path:
    sys.path.insert(0, str(_framework_root))

from strategy_framework.framework.unified_strategy_db import init_db, SessionFactory
from strategy_framework.framework.reporter import build_report, StrategyReport


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _inr(value: float) -> str:
    sign = "+" if value >= 0 else "-"
    return f"{sign}₹{abs(value):,.0f}"


def _pct(value: float) -> str:
    return f"{value:.1f}%"


def _dur(minutes: float) -> str:
    if minutes < 60:
        return f"{minutes:.0f}m"
    h = int(minutes // 60)
    m = int(minutes % 60)
    return f"{h}h {m}m"


SEP = "=" * 64
SEP2 = "-" * 64


# ---------------------------------------------------------------------------
# Print functions
# ---------------------------------------------------------------------------

def _print_report(report: StrategyReport, show_trades: bool) -> None:
    print()
    print(SEP)
    print(f"  CREDIT SPREAD STRATEGY — PERFORMANCE REPORT")
    print(f"  Generated : {report.generated_at.strftime('%d-%b-%Y %H:%M UTC')}")
    print(f"  Mode      : {report.trade_mode}")
    print(SEP)

    if report.total_runs == 0:
        print("  No closed runs found matching the filter.")
        print(SEP)
        return

    print(f"  Total Runs       : {report.total_runs}")
    print(f"  Wins / Losses    : {report.wins} / {report.losses}  ({_pct(report.win_rate_pct)} win rate)")
    print(f"  Total P&L        : {_inr(report.total_pnl)}")
    print(f"  Average P&L      : {_inr(report.avg_pnl)} per trade")
    print(f"  Best Trade       : {_inr(report.best_trade)}")
    print(f"  Worst Trade      : {_inr(report.worst_trade)}")
    print(f"  Avg Duration     : {_dur(report.avg_duration_min)}")
    print(f"  Max Drawdown     : {_inr(report.max_drawdown)}")
    print(f"  Sharpe (daily)   : {report.sharpe_ratio:.3f}")

    # ── By spread type ───────────────────────────────────────────────────────
    if report.by_spread_type:
        print()
        print(SEP2)
        print("  BY SPREAD TYPE")
        print(SEP2)
        _print_breakdown(report.by_spread_type)

    # ── By trade mode ────────────────────────────────────────────────────────
    if report.by_trade_mode:
        print()
        print(SEP2)
        print("  BY TRADE MODE  (PAPER vs LIVE)")
        print(SEP2)
        _print_breakdown(report.by_trade_mode)

    # ── By exit reason ───────────────────────────────────────────────────────
    if report.by_exit_reason:
        print()
        print(SEP2)
        print("  BY EXIT REASON")
        print(SEP2)
        _print_breakdown(report.by_exit_reason)

    # ── Individual trades ────────────────────────────────────────────────────
    if show_trades and report.runs:
        print()
        print(SEP2)
        print("  INDIVIDUAL TRADES")
        print(SEP2)
        header = f"  {'ID':>4}  {'DATE':<12} {'SPREAD':<20} {'MODE':<6} {'EXIT REASON':<16} {'P&L':>12} {'DUR':>8}"
        print(header)
        print("  " + "-" * 60)
        for r in report.runs:
            date_str = r.entry_time.strftime("%d-%b-%Y") if r.entry_time else "?"
            pnl_str  = _inr(r.realized_pnl)
            dur_str  = _dur(r.duration_min)
            spread_short = r.spread_type.replace("_SPREAD", "").replace("BULL_PUT", "BULL PUT").replace("BEAR_CALL", "BEAR CALL")
            reason = r.exit_reason or "?"
            print(f"  {r.run_id:>4}  {date_str:<12} {spread_short:<20} {r.trade_mode:<6} {reason:<16} {pnl_str:>12} {dur_str:>8}")

    print()
    print(SEP)
    print()


def _print_breakdown(rows) -> None:
    header = f"  {'Label':<22} {'N':>4} {'Win%':>6} {'Total P&L':>12} {'Avg P&L':>12} {'Best':>10} {'Worst':>10}"
    print(header)
    print("  " + "-" * 60)
    for row in rows:
        print(
            f"  {row.label:<22} {row.count:>4} {_pct(row.win_rate_pct):>6}"
            f" {_inr(row.total_pnl):>12} {_inr(row.avg_pnl):>12}"
            f" {_inr(row.best_trade):>10} {_inr(row.worst_trade):>10}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Credit Spread Strategy — Performance Report")
    p.add_argument("--db",     default="sqlite:///logs/strategies.db",
                   help="SQLAlchemy DB URL (default: sqlite:///logs/strategies.db)")
    p.add_argument("--mode",   choices=["PAPER", "LIVE", "ALL"], default="ALL",
                   help="Filter by trade mode (default: ALL)")
    p.add_argument("--spread", choices=["BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"],
                   help="Filter by spread type")
    p.add_argument("--underlying", help="Filter by underlying (e.g. NIFTY)")
    p.add_argument("--limit",  type=int, default=500,
                   help="Max number of closed runs to load (default: 500)")
    p.add_argument("--trades", action="store_true",
                   help="Show individual trade rows")
    p.add_argument("--json",   metavar="FILE",
                   help="Export report to JSON file")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    try:
        engine = init_db(args.db)
        sf     = SessionFactory(engine)
    except Exception as exc:
        print(f"DB connection failed: {exc}", file=sys.stderr)
        sys.exit(1)

    session = sf.make()
    try:
        report = build_report(
            session,
            trade_mode=None if args.mode == "ALL" else args.mode,
            spread_type=args.spread,
            underlying=args.underlying,
            limit=args.limit,
        )
    finally:
        session.close()

    _print_report(report, show_trades=args.trades)

    if args.json:
        out_path = Path(args.json)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        def _serialize(obj):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            raise TypeError(f"Not serializable: {type(obj)}")

        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(report.to_dict(), fh, indent=2, default=_serialize)
        print(f"Report exported to: {out_path}")


if __name__ == "__main__":
    main()
