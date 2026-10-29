"""
notifier.py
-----------
Notification abstraction for credit spread strategies.

Design
------
- ``Notifier`` ABC: defines the interface.
- ``LogNotifier``: writes every notification to the Python logger (default,
  always active, zero config).
- ``TelegramNotifier``: sends messages via the Telegram Bot API using a
  fire-and-forget background thread.  Failures are logged but never raise.
- ``CompositeNotifier``: fan-out wrapper that calls multiple notifiers.
- ``notify(msg, level)`` module-level helper: sends via the global notifier.

Notification levels
-------------------
INFO    — routine heartbeat / status updates
WARNING — non-critical anomalies
ALERT   — trade events (entry, exit, SL triggered)
URGENT  — requires immediate human intervention (e.g. order failure)

Usage
-----
::

    from framework.notifier import CompositeNotifier, LogNotifier, TelegramNotifier

    notifier = CompositeNotifier([
        LogNotifier(),
        TelegramNotifier(token="...", chat_id="..."),
    ])
    notifier.send("ENTRY: BULL PUT SPREAD @ NIFTY 24500/24300 premium=₹85", level="ALERT")
"""
from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from typing import Optional

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Level constants
# ---------------------------------------------------------------------------

LEVEL_INFO    = "INFO"
LEVEL_WARNING = "WARNING"
LEVEL_ALERT   = "ALERT"
LEVEL_URGENT  = "URGENT"

_LEVEL_ORDER = {LEVEL_INFO: 0, LEVEL_WARNING: 1, LEVEL_ALERT: 2, LEVEL_URGENT: 3}


# ---------------------------------------------------------------------------
# Notifier ABC
# ---------------------------------------------------------------------------

class Notifier(ABC):
    """Abstract base class for all notifier implementations."""

    @abstractmethod
    def send(self, message: str, level: str = LEVEL_INFO, **kwargs) -> None:
        """Send *message* at the given *level*.  Must NOT raise."""


# ---------------------------------------------------------------------------
# LogNotifier
# ---------------------------------------------------------------------------

class LogNotifier(Notifier):
    """
    Sends all notifications to the Python logger.

    Always active — it is the fallback that ensures every notification is
    captured in the log file regardless of external service availability.
    """

    def send(self, message: str, level: str = LEVEL_INFO, **kwargs) -> None:
        prefix = f"[NOTIFY:{level}]"
        if level == LEVEL_URGENT:
            logger.critical("%s %s", prefix, message)
        elif level == LEVEL_ALERT:
            logger.warning("%s %s", prefix, message)
        elif level == LEVEL_WARNING:
            logger.warning("%s %s", prefix, message)
        else:
            logger.info("%s %s", prefix, message)


# ---------------------------------------------------------------------------
# TelegramNotifier
# ---------------------------------------------------------------------------

class TelegramNotifier(Notifier):
    """
    Sends messages to a Telegram chat via the Bot API.

    Messages are dispatched in a background daemon thread so the monitoring
    loop is never blocked by network latency.  All failures are swallowed
    and logged.

    Parameters
    ----------
    token   : Telegram bot token (from @BotFather).
    chat_id : Target chat / channel ID (string or integer).
    min_level : Minimum level to actually send.  Lower-priority messages are
                silently dropped.  Default: ``"ALERT"`` (INFO and WARNING are
                log-only; ALERT and URGENT go to Telegram).
    timeout : HTTP request timeout in seconds.
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        min_level: str = LEVEL_ALERT,
        timeout: float = 10.0,
    ) -> None:
        self._token    = token
        self._chat_id  = str(chat_id)
        self._min_level = min_level
        self._timeout   = timeout

    def send(self, message: str, level: str = LEVEL_INFO, **kwargs) -> None:
        if _LEVEL_ORDER.get(level, 0) < _LEVEL_ORDER.get(self._min_level, 0):
            return

        # Emoji prefix for visual scanning on mobile
        prefix = {
            LEVEL_INFO:    "ℹ️",
            LEVEL_WARNING: "⚠️",
            LEVEL_ALERT:   "🔔",
            LEVEL_URGENT:  "🚨",
        }.get(level, "")
        text = f"{prefix} {message}"

        t = threading.Thread(
            target=self._dispatch,
            args=(text,),
            daemon=True,
        )
        t.start()

    def _dispatch(self, text: str) -> None:
        """Fire-and-forget HTTP call executed in a background thread."""
        try:
            import urllib.request
            import urllib.parse
            import json

            url  = f"https://api.telegram.org/bot{self._token}/sendMessage"
            data = urllib.parse.urlencode({
                "chat_id": self._chat_id,
                "text":    text,
                "parse_mode": "HTML",
            }).encode()

            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                if resp.status != 200:
                    logger.warning(
                        "TelegramNotifier: HTTP %d for message %r", resp.status, text[:60]
                    )
        except Exception as exc:
            logger.warning("TelegramNotifier: send failed — %s", exc)


# ---------------------------------------------------------------------------
# CompositeNotifier
# ---------------------------------------------------------------------------

class CompositeNotifier(Notifier):
    """
    Fan-out wrapper that sends to multiple notifiers in sequence.

    Failure of one notifier does not prevent others from being called.

    Usage
    -----
    ::

        notifier = CompositeNotifier([
            LogNotifier(),
            TelegramNotifier(token="...", chat_id="..."),
        ])
    """

    def __init__(self, notifiers: list[Notifier]) -> None:
        self._notifiers = list(notifiers)

    def add(self, notifier: Notifier) -> None:
        self._notifiers.append(notifier)

    def send(self, message: str, level: str = LEVEL_INFO, **kwargs) -> None:
        for n in self._notifiers:
            try:
                n.send(message, level=level, **kwargs)
            except Exception as exc:
                logger.warning("CompositeNotifier: notifier %s raised %s", type(n).__name__, exc)


# ---------------------------------------------------------------------------
# Module-level singleton (optional convenience)
# ---------------------------------------------------------------------------

_global_notifier: Notifier = LogNotifier()


def set_global_notifier(notifier: Notifier) -> None:
    """Replace the module-level notifier used by ``notify()``."""
    global _global_notifier
    _global_notifier = notifier


def notify(message: str, level: str = LEVEL_INFO) -> None:
    """Send *message* via the current global notifier."""
    _global_notifier.send(message, level=level)
