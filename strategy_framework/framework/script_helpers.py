"""
script_helpers.py
-----------------
Factory functions shared by all credit-spread (and future) entry scripts.

Each factory reads standard keys from a ``Config`` object and returns a
fully-initialised component.  Using these keeps ``main()`` functions thin
and guarantees every script constructs components in exactly the same way.

Functions
---------
build_client(config)
    OpenAlgoClient + connectivity ping.  Calls sys.exit(1) on failure.

build_notifier(config)
    CompositeNotifier with LogNotifier always active and optional Telegram.

build_db(config)
    (SessionFactory, DBWriteQueue) — tables created, queue started.

build_gateway(config, client, live_exec_modes)
    (ExecutionGateway, gw_mode_str) — PAPER or LIVE, based on exec mode.

Config keys consumed
--------------------
OPENALGO_API_KEY, OPENALGO_HOST, API_TIMEOUT
NOTIFY_TELEGRAM, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_MIN_LEVEL
DATABASE_URL
MAX_ORDER_RETRY, ORDER_POLL_INTERVAL, ORDER_FILL_TIMEOUT
PAPER_SNAPSHOT_PATH, EXECUTION_MODE
"""
from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from .unified_strategy_db import SessionFactory, init_db
from .db_writer import DBWriteQueue
from .execution_gateway import ExecutionGateway
from .notifier import (
    LEVEL_URGENT, CompositeNotifier, LogNotifier, TelegramNotifier,
)
from .openalgo_client import OpenAlgoClient
from ..paper.paper_engine import PaperEngine
from ..utils.constants import EXECUTION_MODE_LIVE, EXECUTION_MODE_PAPER
from ..utils.logger import get_logger

if TYPE_CHECKING:
    from .config_loader import Config

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# OpenAlgo client
# ---------------------------------------------------------------------------

def build_client(config: "Config") -> OpenAlgoClient:
    """
    Create an ``OpenAlgoClient`` and verify connectivity.

    Calls ``sys.exit(1)`` if OpenAlgo is not reachable, so the caller
    never receives an unconnected client.

    Parameters
    ----------
    config : Loaded ``Config`` object.

    Returns
    -------
    Connected ``OpenAlgoClient``.
    """
    client = OpenAlgoClient(
        api_key=config.get_str("OPENALGO_API_KEY"),
        host=config.get_str("OPENALGO_HOST", "http://127.0.0.1:5000"),
        timeout=config.get_float("API_TIMEOUT", 30.0),
    )
    if not client.ping():
        logger.critical("OpenAlgo not reachable — check server and API key.")
        sys.exit(1)
    logger.info("OpenAlgo connection verified.")
    return client


# ---------------------------------------------------------------------------
# Notifier
# ---------------------------------------------------------------------------

def build_notifier(config: "Config") -> CompositeNotifier:
    """
    Build a ``CompositeNotifier`` from config.

    ``LogNotifier`` is always active.  ``TelegramNotifier`` is added only
    when ``NOTIFY_TELEGRAM`` is ``true`` in the config.

    Config keys
    -----------
    NOTIFY_TELEGRAM   : bool  (default false)
    TELEGRAM_TOKEN    : str
    TELEGRAM_CHAT_ID  : str
    TELEGRAM_MIN_LEVEL: str   (default "ALERT")
    """
    notifiers = [LogNotifier()]
    if config.get_bool("NOTIFY_TELEGRAM", False):
        notifiers.append(TelegramNotifier(
            token=config.get_str("TELEGRAM_TOKEN", ""),
            chat_id=config.get_str("TELEGRAM_CHAT_ID", ""),
            min_level=config.get_str("TELEGRAM_MIN_LEVEL", "ALERT"),
        ))
    return CompositeNotifier(notifiers)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def build_db(config: "Config") -> tuple[SessionFactory, DBWriteQueue]:
    """
    Initialise the credit-spread database and return a ready write queue.

    Creates all tables if they don't exist, starts the background flush
    thread, and returns both the session factory (for synchronous writes)
    and the queue (for non-blocking async writes).

    Config keys
    -----------
    DATABASE_URL : SQLAlchemy URL  (default ``"sqlite:///logs/strategies.db"``)
    """
    db_url   = config.get_str("DATABASE_URL", "sqlite:///logs/strategies.db")
    engine   = init_db(db_url)
    sf       = SessionFactory(engine)
    db_queue = DBWriteQueue(sf.make, flush_interval=5.0)
    db_queue.start()
    return sf, db_queue


# ---------------------------------------------------------------------------
# Execution gateway
# ---------------------------------------------------------------------------

def build_gateway(
    config: "Config",
    client: OpenAlgoClient,
    live_exec_modes: tuple = (),
) -> tuple[ExecutionGateway, str]:
    """
    Build an ``ExecutionGateway`` in PAPER or LIVE mode.

    Parameters
    ----------
    config           : Loaded ``Config`` object.
    client           : Connected ``OpenAlgoClient``.
    live_exec_modes  : Tuple of execution-mode strings that indicate LIVE
                       execution for this script.
                       Execution script  : ``(EXEC_MODE_ENTRY_EXEC_MONITOR, EXEC_MODE_FULL_EXEC)``
                       Monitoring script : ``(EXEC_MODE_EXIT_EXEC_MONITOR,  EXEC_MODE_FULL_EXEC)``

    Config keys
    -----------
    EXECUTION_MODE      : str
    PAPER_SNAPSHOT_PATH : str  (default ``"logs/cs_paper_state.json"``)
    MAX_ORDER_RETRY     : int  (default 3)
    ORDER_POLL_INTERVAL : float (default 2.0)
    ORDER_FILL_TIMEOUT  : float (default 60.0)

    Returns
    -------
    (gateway, gw_mode) where ``gw_mode`` is ``"PAPER"`` or ``"LIVE"``.
    """
    exec_mode = config.get_str("EXECUTION_MODE", "MONITOR_ONLY")
    is_live   = exec_mode in live_exec_modes
    gw_mode   = EXECUTION_MODE_LIVE if is_live else EXECUTION_MODE_PAPER

    paper = PaperEngine(
        snapshot_path=config.get_str("PAPER_SNAPSHOT_PATH", "logs/cs_paper_state.json")
    )
    gateway = ExecutionGateway(
        mode=gw_mode,
        client=client,
        paper=paper,
        max_retry=config.get_int("MAX_ORDER_RETRY", 3),
        poll_interval=config.get_float("ORDER_POLL_INTERVAL", 2.0),
        fill_timeout=config.get_float("ORDER_FILL_TIMEOUT", 60.0),
    )
    return gateway, gw_mode
