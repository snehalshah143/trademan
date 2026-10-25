"""
logger.py
---------
Centralised logging setup for the strategy execution framework.

Features
--------
- Single call ``get_logger(name)`` returns a configured logger.
- Log level and file path come from the caller (configured via settings.yaml).
- Supports a ``[PAPER]`` prefix injected automatically when in paper mode.
- Console handler + rotating file handler (10 MB, 5 backups).
- UTF-8 encoding everywhere so ₹ and other symbols render correctly.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path


# ---------------------------------------------------------------------------
# Internal registry — one logger per name, configured once
# ---------------------------------------------------------------------------
_configured_loggers: set[str] = set()


def setup_logger(
    name: str,
    log_file: str = "strategy.log",
    level: str = "INFO",
) -> logging.Logger:
    """
    Create (or retrieve) a logger with console + rotating file output.

    Parameters
    ----------
    name     : Logger name, typically ``__name__`` of the calling module.
    log_file : Path to the log file.  Parent directories are created if absent.
    level    : Logging level string (DEBUG / INFO / WARNING / ERROR / CRITICAL).

    Returns
    -------
    logging.Logger
    """
    logger = logging.getLogger(name)

    # Avoid adding duplicate handlers on repeated calls
    if name in _configured_loggers:
        return logger

    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(numeric_level)

    fmt = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # Console handler — UTF-8 to handle ₹ on Windows
    console_handler = logging.StreamHandler(
        stream=open(sys.stdout.fileno(), mode="w", encoding="utf-8", closefd=False)
    )
    console_handler.setFormatter(fmt)
    console_handler.setLevel(numeric_level)

    # Rotating file handler
    log_path = Path(log_file)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    file_handler = RotatingFileHandler(
        filename=str(log_path),
        maxBytes=10 * 1024 * 1024,   # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    file_handler.setLevel(numeric_level)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.propagate = False

    _configured_loggers.add(name)
    return logger


def get_logger(name: str) -> logging.Logger:
    """
    Return the logger registered under *name*.

    If the logger has not been set up yet (e.g. called before ``setup_logger``),
    a basic logger is returned so the module still works.

    Callers should call ``setup_logger`` once at startup (in main.py) and then
    use ``get_logger`` everywhere else.
    """
    return logging.getLogger(name)


class PaperLogAdapter(logging.LoggerAdapter):
    """
    Wraps a logger and prepends ``[PAPER]`` to every message automatically.

    Usage::

        raw_logger = get_logger(__name__)
        logger = PaperLogAdapter(raw_logger, {})
        logger.info("BUY 25000 CE — Virtual Order Executed")
        # → "[PAPER] BUY 25000 CE — Virtual Order Executed"
    """

    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:
        return f"[PAPER] {msg}", kwargs
