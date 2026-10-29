"""
final_crudeoil_intraday_future_long_directional_3Min.py
-------------------------------------------------------
Orchestrator for the Crudeoil Intraday Futures 3-Min Directional strategy.

Loops all day: execution → monitoring → execution → monitoring → ...
until the entry window closes (exit code 2) or a fatal error occurs.

Exit code semantics from the execution script:
  0 → trade placed successfully — start monitoring, then loop back
  2 → no trade placed (market closed / no signal / entry window passed) — stop for the day
  other non-zero → error — abort

Usage
-----
    # From strategy_framework/ directory:
    python -m strategies.directional.commodity.crudeoil_intraday_future_long_directional_3Min.final_crudeoil_intraday_future_long_directional_3Min

    # With custom configs:
    python ... --exec-config /path/to/execution_settings.yaml
               --mon-config  /path/to/monitoring_settings.yaml
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import sys as _sys
_HERE = Path(__file__).resolve().parent
_SF_ROOT = _HERE.parent.parent.parent.parent.parent
_SF_DIR  = _HERE.parent.parent.parent.parent          # strategy_framework/
if str(_SF_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_SF_ROOT))


# ---------------------------------------------------------------------------
# Open position helpers
# ---------------------------------------------------------------------------

def _find_open_position(exec_config_path: str) -> bool:
    """
    Return True if there is an OPEN DIRECTIONAL_FUTURES run in the DB for
    today's strategy_id (e.g. COIL3_20261027).

    Called at the top of every orchestrator loop so that a restarted script
    skips execution and goes straight to monitoring when a trade is already open.
    """
    try:
        from strategy_framework.framework.config_loader import Config
        from strategy_framework.framework.unified_strategy_db import (
            init_db, SessionFactory, StrategyRun,
        )
        from strategy_framework.utils.helper import now_ist

        cfg      = Config(exec_config_path)
        db_url   = cfg.get_str("DATABASE_URL", "sqlite:///logs/strategies.db")
        prefix   = cfg.get_str("STRATEGY_ID_PREFIX", "COIL3")
        today_id = f"{prefix}_{now_ist().strftime('%Y%m%d')}"

        engine = init_db(db_url)
        sf     = SessionFactory(engine)
        sess   = sf.make()
        try:
            open_run = (
                sess.query(StrategyRun)
                .filter(
                    StrategyRun.status == "OPEN",
                    StrategyRun.strategy_type == "DIRECTIONAL_FUTURES",
                    StrategyRun.strategy_id == today_id,
                )
                .first()
            )
            return open_run is not None
        finally:
            sess.close()
    except Exception as exc:
        print(f"[WARNING] Could not check DB for open position: {exc}")
        return False


def _check_open_positions(exec_config_path: str) -> None:
    """
    Query the DB for any OPEN DIRECTIONAL_FUTURES strategy_runs.
    Prints an urgent alert to stdout if any are found.
    Called before every exit so the operator knows if a position is unmonitored.
    """
    try:
        from strategy_framework.framework.config_loader import Config
        from strategy_framework.framework.unified_strategy_db import (
            init_db, SessionFactory, StrategyRun, StrategyLeg,
        )

        cfg    = Config(exec_config_path)
        db_url = cfg.get_str("DATABASE_URL", "sqlite:///logs/strategies.db")
        engine = init_db(db_url)
        sf     = SessionFactory(engine)
        sess   = sf.make()

        try:
            open_runs = (
                sess.query(StrategyRun)
                .filter(
                    StrategyRun.status == "OPEN",
                    StrategyRun.strategy_type == "DIRECTIONAL_FUTURES",
                )
                .all()
            )
            if not open_runs:
                return

            print("\n" + "!" * 60)
            print("!! ALERT — OPEN POSITION(S) DETECTED WHILE SCRIPT IS STOPPING !!")
            print("!" * 60)
            for run in open_runs:
                leg = sess.query(StrategyLeg).filter(StrategyLeg.run_id == run.id).first()
                print(
                    f"  run_id={run.id}  strategy_id={run.strategy_id}"
                    f"  underlying={run.underlying}  expiry={run.expiry}"
                    f"  direction={run.direction}  lots={run.lots}"
                )
                if leg:
                    print(
                        f"  symbol={leg.symbol}  qty={leg.quantity}"
                        f"  entry_price={leg.entry_price}  status={leg.status}"
                    )
            print("!!")
            print("!! ACTION REQUIRED: Square off manually or restart monitoring script.")
            print("!" * 60 + "\n")
        finally:
            sess.close()

    except Exception as exc:
        print(f"\n[WARNING] Could not check for open positions: {exc}")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Crudeoil Intraday Futures 3Min — Orchestrator"
    )
    p.add_argument(
        "--exec-config",
        default=str(_HERE / "config" / "execution_settings.yaml"),
        help="Path to execution_settings.yaml",
    )
    p.add_argument(
        "--mon-config",
        default=str(_HERE / "config" / "monitoring_settings.yaml"),
        help="Path to monitoring_settings.yaml",
    )
    return p.parse_args()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = _parse_args()
    _pkg = "strategies.directional.commodity.crudeoil_intraday_future_long_directional_3Min"
    trade_count = 0

    try:
        while True:
            trade_count += 1

            # ── Check DB first — resume monitoring if a position is already open ──
            if _find_open_position(args.exec_config):
                print("\n" + "=" * 60)
                print("OPEN POSITION DETECTED — skipping execution, resuming monitoring ...")
                print("=" * 60)
            else:
                print("=" * 60)
                print(f"EXECUTION — trade cycle #{trade_count} starting ...")
                print("=" * 60)

                exec_result = subprocess.run(
                    [
                        sys.executable, "-m",
                        f"{_pkg}.execution_crudeoil_intraday_future_long_directional_3Min",
                        "--config", args.exec_config,
                    ],
                    cwd=str(_SF_DIR),
                )

                if exec_result.returncode == 2:
                    print(f"\nEntry window closed after {trade_count - 1} trade(s). Done for the day.")
                    _check_open_positions(args.exec_config)
                    sys.exit(0)

                if exec_result.returncode != 0:
                    print(
                        f"\nExecution script failed (exit code {exec_result.returncode}). Aborting."
                    )
                    _check_open_positions(args.exec_config)
                    sys.exit(exec_result.returncode)

            print("\n" + "=" * 60)
            print(f"MONITORING — trade cycle #{trade_count} ...")
            print("=" * 60)

            subprocess.run(
                [
                    sys.executable, "-m",
                    f"{_pkg}.monitoring_crudeoil_intraday_future_long_directional_3Min",
                    "--config", args.mon_config,
                ],
                cwd=str(_SF_DIR),
            )

            print(f"\nTrade #{trade_count} complete. Looking for next entry signal ...\n")

    except KeyboardInterrupt:
        print(f"\nStopped by user after {trade_count - 1} trade(s).")
        _check_open_positions(args.exec_config)
        sys.exit(0)


if __name__ == "__main__":
    main()
