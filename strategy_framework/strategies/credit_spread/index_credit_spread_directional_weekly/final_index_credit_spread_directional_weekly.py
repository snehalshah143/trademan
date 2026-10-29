"""
run.py
------
Orchestrator for the Directional Index Credit Spread (Weekly) strategy.

Runs the two scripts in sequence:
  1. Execution script  — waits for signal, places entry orders, exits.
  2. Monitoring script — picks up the open position, manages SL/exits.

The monitoring script only starts if the execution script exits successfully
(exit code 0).  Any non-zero exit from execution aborts the run.

Usage
-----
    # From strategy_framework/ directory:
    python -m strategies.credit_spread.index_credit_spread_directional_weekly.run

    # With custom configs:
    python ... --exec-config /path/to/execution_settings.yaml
               --mon-config  /path/to/monitoring_settings.yaml
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent

import sys as _sys
_SF_ROOT = _HERE.parent.parent.parent
if str(_SF_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_SF_ROOT))


# ---------------------------------------------------------------------------
# Open position helper
# ---------------------------------------------------------------------------

def _find_open_position(exec_config_path: str) -> bool:
    """
    Return True if there is an OPEN CREDIT_SPREAD run in the DB for
    today's strategy_id (e.g. ICS_20261027).

    Allows a restarted orchestrator to skip execution and resume monitoring.
    """
    try:
        from strategy_framework.framework.config_loader import Config
        from strategy_framework.framework.unified_strategy_db import (
            init_db, SessionFactory, StrategyRun,
        )
        from strategy_framework.utils.helper import now_ist

        cfg      = Config(exec_config_path)
        db_url   = cfg.get_str("DATABASE_URL", "sqlite:///logs/strategies.db")
        prefix   = cfg.get_str("STRATEGY_ID_PREFIX", "ICS")
        today_id = f"{prefix}_{now_ist().strftime('%Y%m%d')}"

        engine = init_db(db_url)
        sf     = SessionFactory(engine)
        sess   = sf.make()
        try:
            open_run = (
                sess.query(StrategyRun)
                .filter(
                    StrategyRun.status == "OPEN",
                    StrategyRun.strategy_type == "CREDIT_SPREAD",
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


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Directional Index Credit Spread — Orchestrator"
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

    exec_script = _HERE / "execution_index_credit_spread_directional_weekly.py"
    mon_script  = _HERE / "monitoring_index_credit_spread_directional_weekly.py"

    # ── Step 1: Execution (skip if position already open in DB) ──────────────
    if _find_open_position(args.exec_config):
        print("\n" + "=" * 60)
        print("OPEN POSITION DETECTED — skipping execution, resuming monitoring ...")
        print("=" * 60)
    else:
        print("=" * 60)
        print("STEP 1 — Execution script starting ...")
        print("=" * 60)

        exec_result = subprocess.run(
            [sys.executable, str(exec_script), "--config", args.exec_config],
        )

        if exec_result.returncode == 2:
            print("\nNo trade placed (market closed or no signal). Monitoring will NOT start.")
            sys.exit(0)

        if exec_result.returncode != 0:
            print(
                f"\nExecution script failed (exit code {exec_result.returncode}). "
                "Monitoring will NOT start."
            )
            sys.exit(exec_result.returncode)

    # ── Step 2: Monitoring ───────────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("STEP 2 — Execution complete. Monitoring script starting ...")
    print("=" * 60)

    mon_result = subprocess.run(
        [sys.executable, str(mon_script), "--config", args.mon_config],
    )

    sys.exit(mon_result.returncode)


if __name__ == "__main__":
    main()
