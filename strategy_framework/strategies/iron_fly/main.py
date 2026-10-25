"""
main.py
-------
Entry point for the Monthly Dynamic Iron Fly strategy.

Usage
-----
    # From strategy_framework/ directory:
    python -m strategies.iron_fly.main

    # Or directly:
    python strategies/iron_fly/main.py

    # With a custom config path:
    python strategies/iron_fly/main.py --config path/to/settings.yaml

Flow
----
1. Load configuration from settings.yaml
2. Setup logging
3. Connect to OpenAlgo
4. Run startup recovery scan
5. If existing strategy found → resume monitoring
6. Else → wait for first trading day and deploy
7. Start monitoring loop
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Ensure strategy_framework is importable regardless of working directory
# ---------------------------------------------------------------------------
_framework_root = Path(__file__).resolve().parent.parent.parent.parent
if str(_framework_root) not in sys.path:
    sys.path.insert(0, str(_framework_root))

from strategy_framework.framework.config_loader import Config, ConfigError
from strategy_framework.framework.context_builder import ContextBuilder
from strategy_framework.framework.execution_gateway import ExecutionGateway
from strategy_framework.framework.monitor import MonitoringEngine
from strategy_framework.framework.openalgo_client import OpenAlgoClient, OpenAlgoError
from strategy_framework.framework.recovery_manager import RecoveryManager
from strategy_framework.models.strategy_state import StrategyPhase, StrategyState
from strategy_framework.paper.paper_engine import PaperEngine
from strategy_framework.strategies.iron_fly.strategy.iron_fly_strategy import IronFlyStrategy
from strategy_framework.utils.logger import get_logger, setup_logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Monthly Dynamic Iron Fly Strategy — powered by OpenAlgo"
    )
    parser.add_argument(
        "--config",
        default=str(Path(__file__).parent / "config" / "settings.yaml"),
        help="Path to settings.yaml (default: config/settings.yaml in this directory)",
    )
    return parser.parse_args()


def build_strategy_id(config: Config) -> str:
    """Generate a unique monthly strategy ID e.g. ``IRON_FLY_202608``."""
    now = datetime.now()
    return f"IRON_FLY_{now.year}{now.month:02d}"


def main() -> None:
    args = parse_args()

    # ---------------------------------------------------------------------------
    # 1. Load configuration
    # ---------------------------------------------------------------------------
    try:
        config = Config(args.config)
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        sys.exit(1)

    # ---------------------------------------------------------------------------
    # 2. Setup logging
    # ---------------------------------------------------------------------------
    log_file = config.get_str("LOG_FILE", "logs/iron_fly.log")
    log_level = config.get_str("LOG_LEVEL", "INFO")
    setup_logger("strategy_framework", log_file=log_file, level=log_level)
    logger = get_logger("strategy_framework.main")

    logger.info("=" * 52)
    logger.info("Monthly Dynamic Iron Fly Strategy Starting")
    logger.info(f"Mode      : {config.execution_mode}")
    logger.info(f"Underlying: {config.underlying}")
    logger.info(f"Interval  : {config.monitoring_interval}s")
    logger.info(f"Config    : {args.config}")
    logger.info("=" * 52)

    # ---------------------------------------------------------------------------
    # 3. Connect to OpenAlgo
    # ---------------------------------------------------------------------------
    try:
        client = OpenAlgoClient(
            api_key=config.api_key,
            host=config.host,
            timeout=config.get_float("API_TIMEOUT", 30.0),
        )
    except Exception as exc:
        logger.critical(f"Failed to initialise OpenAlgo client: {exc}")
        sys.exit(1)

    # Quick connectivity check
    try:
        client.get_funds()
        logger.info("OpenAlgo connection verified.")
    except OpenAlgoError as exc:
        logger.critical(f"OpenAlgo connectivity check failed: {exc}")
        logger.critical("Ensure OpenAlgo server is running and API key is correct.")
        sys.exit(1)

    # ---------------------------------------------------------------------------
    # 4. Setup paper engine (always created; ignored in LIVE mode)
    # ---------------------------------------------------------------------------
    paper = PaperEngine(snapshot_path=config.get_str("PAPER_SNAPSHOT_PATH", "logs/paper_state.json"))

    # ---------------------------------------------------------------------------
    # 5. Execution gateway
    # ---------------------------------------------------------------------------
    gateway = ExecutionGateway(
        mode=config.execution_mode,
        client=client,
        paper=paper,
        max_retry=config.get_int("MAX_ORDER_RETRY", 3),
        poll_interval=config.get_float("ORDER_POLL_INTERVAL", 2.0),
        fill_timeout=config.get_float("ORDER_FILL_TIMEOUT", 60.0),
    )

    # ---------------------------------------------------------------------------
    # 6. Strategy ID and initial state
    # ---------------------------------------------------------------------------
    strategy_id = build_strategy_id(config)
    logger.info(f"Strategy ID: {strategy_id}")

    initial_state = StrategyState(
        strategy_id=strategy_id,
        strategy_name="IRON_FLY",
        phase=StrategyPhase.IDLE,
    )

    # ---------------------------------------------------------------------------
    # 7. Recovery — always attempt before assuming fresh start
    # ---------------------------------------------------------------------------
    recovery_manager = RecoveryManager(
        client=client,
        strategy_id=strategy_id,
        state_path=config.get_str("STATE_SNAPSHOT_PATH", "logs/strategy_state.json"),
    )

    logger.info("Running startup recovery scan …")
    recovery_result = recovery_manager.recover(config)

    if recovery_result.found and recovery_result.state:
        state = recovery_result.state
        logger.info(f"Recovery successful: {recovery_result.message}")
        logger.info(f"Resumed state: {state}")
    else:
        state = initial_state
        logger.info(f"No existing strategy found: {recovery_result.message}")
        logger.info("Starting fresh — will deploy on first trading day.")

    # ---------------------------------------------------------------------------
    # 8. Build the strategy
    # ---------------------------------------------------------------------------
    strategy = IronFlyStrategy(state=state, config=config, gateway=gateway)

    # ---------------------------------------------------------------------------
    # 9. Context builder
    # ---------------------------------------------------------------------------
    context_builder = ContextBuilder(client=client, config=config, state=state)

    # ---------------------------------------------------------------------------
    # 10. Start the monitoring loop
    # ---------------------------------------------------------------------------
    engine = MonitoringEngine(
        strategy=strategy,
        context_builder=context_builder,
        interval=config.monitoring_interval,
    )

    try:
        engine.run()
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
    finally:
        # Persist final state
        recovery_manager.save_local_snapshot(state)
        client.close()
        logger.info("Shutdown complete.")


if __name__ == "__main__":
    main()
