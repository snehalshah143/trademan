"""
db_writer.py
------------
Non-blocking write queue that batches DB writes to SQLite (or any SQLAlchemy-
compatible backend) every fixed interval.

Design
------
- ``DBWriteQueue.put(fn, *args, **kwargs)`` is a non-blocking O(1) enqueue.
  The caller (monitoring loop) never waits on a DB lock.
- A background daemon thread wakes every ``flush_interval`` seconds, drains
  the queue, and executes all pending callables inside a single transaction.
- On shutdown (``stop()`` / ``drain()``), the background thread processes all
  remaining items before exiting.

Thread safety
-------------
``queue.Queue`` is thread-safe.  All SQLAlchemy session usage is confined to
the background thread — no cross-thread session sharing.

Upgrade path
------------
To move to Redis Streams replace the queue with an rq / Celery task.
The ``put()`` call-site never changes — only this module's background thread.

Usage
-----
::

    queue = DBWriteQueue(session_factory, flush_interval=5)
    queue.start()

    # Non-blocking from the monitoring thread:
    queue.put(save_mtm_snapshot, strategy_id="X", mtm=1234.0)

    # On shutdown:
    queue.stop()
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Any, Callable, Optional

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Internal work item
# ---------------------------------------------------------------------------

class _WriteItem:
    """A pending DB write: a callable + its arguments."""

    __slots__ = ("fn", "args", "kwargs")

    def __init__(self, fn: Callable, args: tuple, kwargs: dict) -> None:
        self.fn     = fn
        self.args   = args
        self.kwargs = kwargs

    def execute(self, session) -> None:
        """Call fn(session, *args, **kwargs)."""
        self.fn(session, *self.args, **self.kwargs)


# ---------------------------------------------------------------------------
# DBWriteQueue
# ---------------------------------------------------------------------------

class DBWriteQueue:
    """
    Non-blocking, batched, single-writer DB queue.

    Parameters
    ----------
    session_factory : Zero-argument callable that returns a new SQLAlchemy
                      Session.  The background thread creates one session per
                      flush cycle and closes it afterwards.
    flush_interval  : Seconds between flush cycles (default 5).
    max_queue_size  : Maximum pending items (default 1000).  If the queue is
                      full ``put()`` silently drops items and logs a warning.
    """

    def __init__(
        self,
        session_factory: Callable,
        flush_interval: float = 5.0,
        max_queue_size: int = 1000,
    ) -> None:
        self._session_factory = session_factory
        self._flush_interval  = flush_interval
        self._q: queue.Queue  = queue.Queue(maxsize=max_queue_size)
        self._running         = False
        self._thread: Optional[threading.Thread] = None
        self._stats = {"enqueued": 0, "flushed": 0, "errors": 0, "dropped": 0}

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def start(self) -> None:
        """Start the background flush thread.  Idempotent."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            name="DBWriteQueue-flusher",
            daemon=True,
        )
        self._thread.start()
        logger.info(
            "DBWriteQueue: started (flush_interval=%ss, max_queue=%d)",
            self._flush_interval, self._q.maxsize,
        )

    def stop(self, drain: bool = True) -> None:
        """
        Signal the flush thread to stop.

        Parameters
        ----------
        drain : If ``True`` (default), process all remaining queue items
                before exiting.  Set to ``False`` for an immediate shutdown
                (pending items are lost).
        """
        if not self._running:
            return
        logger.info("DBWriteQueue: stop requested (drain=%s)", drain)
        self._running = False
        if self._thread and drain:
            self._thread.join(timeout=30)
        logger.info(
            "DBWriteQueue: stopped. stats=%s", self._stats
        )

    def put(self, fn: Callable, *args: Any, **kwargs: Any) -> bool:
        """
        Enqueue a write operation.  Non-blocking, O(1).

        The callable *fn* will be invoked as ``fn(session, *args, **kwargs)``
        inside a transaction by the background thread.

        Returns ``True`` on success, ``False`` if the queue is full (item dropped).
        """
        item = _WriteItem(fn, args, kwargs)
        try:
            self._q.put_nowait(item)
            self._stats["enqueued"] += 1
            return True
        except queue.Full:
            self._stats["dropped"] += 1
            logger.warning(
                "DBWriteQueue: queue full (%d items) — dropping write for %s",
                self._q.qsize(), fn.__name__,
            )
            return False

    @property
    def queue_size(self) -> int:
        return self._q.qsize()

    @property
    def stats(self) -> dict:
        return dict(self._stats)

    # -----------------------------------------------------------------------
    # Background thread
    # -----------------------------------------------------------------------

    def _run(self) -> None:
        """Background flush loop."""
        while self._running:
            time.sleep(self._flush_interval)
            self._flush()

        # Drain remaining items after _running is set to False
        if not self._q.empty():
            logger.info("DBWriteQueue: draining %d remaining items …", self._q.qsize())
            self._flush()

    def _flush(self) -> None:
        """Drain the queue and execute all items in a single transaction."""
        if self._q.empty():
            return

        items: list[_WriteItem] = []
        try:
            while True:
                items.append(self._q.get_nowait())
        except queue.Empty:
            pass

        if not items:
            return

        logger.debug("DBWriteQueue: flushing %d items …", len(items))

        session = None
        try:
            session = self._session_factory()
            for item in items:
                try:
                    item.execute(session)
                except Exception as exc:
                    logger.error(
                        "DBWriteQueue: error executing %s: %s",
                        item.fn.__name__, exc,
                    )
                    self._stats["errors"] += 1
                    # Continue with remaining items — don't abort the whole batch
            session.commit()
            self._stats["flushed"] += len(items)
            logger.debug("DBWriteQueue: committed %d items", len(items))

        except Exception as exc:
            logger.error("DBWriteQueue: transaction error: %s", exc)
            self._stats["errors"] += 1
            if session:
                try:
                    session.rollback()
                except Exception:
                    pass
        finally:
            if session:
                try:
                    session.close()
                except Exception:
                    pass
