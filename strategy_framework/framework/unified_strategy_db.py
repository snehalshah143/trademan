"""
unified_strategy_db.py
----------------------
Single SQLAlchemy ORM and DB helpers for ALL strategies.

All strategies — credit spread, directional futures, or any future type —
write to one shared ``strategies.db``.  A ``strategy_type`` column on
``strategy_runs`` differentiates them.  The backend reads one DB, so
new strategies appear on the dashboard automatically.

Tables
------
strategy_runs          — one row per strategy lifecycle (entry → exit)
strategy_legs          — one or more legs per run
strategy_mtm_snapshots — periodic MTM captures during monitoring
strategy_trades        — individual order/fill events (ENTRY, EXIT, ...)
strategy_signal_events — signal detection audit trail
strategy_adjustments   — SL trail / roll / partial-exit events
strategy_notifications — outgoing notification audit log

DBWriteQueue contract
---------------------
All write helpers except ``save_strategy_run`` accept ``session: Session``
as their first argument and perform NO commit / rollback / close — the
DBWriteQueue background thread owns the transaction lifecycle.

``save_strategy_run`` IS an exception: it must return the new ``run_id``
synchronously, so call it directly (not via the queue) and commit yourself.

Usage
-----
::

    from strategy_framework.framework.unified_strategy_db import (
        init_db, SessionFactory,
        save_strategy_run, save_leg, save_trade, save_signal_event,
        save_mtm_snapshot, update_leg, update_strategy_run,
        get_open_runs, get_run_legs,
    )
    from strategy_framework.framework.db_writer import DBWriteQueue

    engine   = init_db("sqlite:///logs/strategies.db")
    sf       = SessionFactory(engine)
    db_queue = DBWriteQueue(sf.make, flush_interval=5.0)
    db_queue.start()

    # Synchronous run creation (must return run_id):
    sess   = sf.make()
    run_id = save_strategy_run(sess, strategy_type="DIRECTIONAL_FUTURES", ...)
    sess.commit(); sess.close()

    # Non-blocking writes via queue:
    db_queue.put(save_leg,          run_id=run_id, ...)
    db_queue.put(save_mtm_snapshot, run_id=run_id, ...)
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

from sqlalchemy import (
    Column, DateTime, Float, Index, Integer, String, Text, create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# ORM base
# ---------------------------------------------------------------------------

class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

class StrategyRun(Base):
    """
    One row per strategy lifecycle (from entry to full exit).

    CS-only columns  : spread_type, primary_strike, hedge_strike,
                       net_premium, max_profit, max_loss
    FD-only columns  : direction
    All others apply to every strategy type.
    """
    __tablename__ = "strategy_runs"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    strategy_type    = Column(String(32),  nullable=False, index=True)   # CREDIT_SPREAD | DIRECTIONAL_FUTURES | ...
    strategy_id      = Column(String(64),  nullable=False, index=True)   # e.g. COIL_20261027
    strategy_name    = Column(String(128), nullable=False)
    underlying       = Column(String(32),  nullable=False)
    expiry           = Column(String(32),  nullable=False)
    lots             = Column(Integer,     nullable=True)
    lot_size         = Column(Integer,     nullable=True)
    entry_spot       = Column(Float,       nullable=True)
    entry_time       = Column(DateTime,    nullable=True)
    exit_time        = Column(DateTime,    nullable=True)
    exit_reason      = Column(String(64),  nullable=True)
    realized_pnl     = Column(Float,       nullable=True)
    status           = Column(String(16),  nullable=False, default="OPEN")  # OPEN | CLOSED | PAUSED
    execution_mode   = Column(String(32),  nullable=True)
    trade_mode       = Column(String(32),  nullable=False, default="MONITOR_ONLY")
    notes            = Column(Text,        nullable=True)
    created_at       = Column(DateTime,    nullable=False, default=datetime.datetime.utcnow)
    updated_at       = Column(DateTime,    nullable=False, default=datetime.datetime.utcnow,
                              onupdate=datetime.datetime.utcnow)

    # Credit spread specific (nullable for other types)
    spread_type      = Column(String(32),  nullable=True)   # BULL_PUT_SPREAD | BEAR_CALL_SPREAD | IRON_FLY | ...
    primary_strike   = Column(Float,       nullable=True)
    hedge_strike     = Column(Float,       nullable=True)
    net_premium      = Column(Float,       nullable=True)
    max_profit       = Column(Float,       nullable=True)
    max_loss         = Column(Float,       nullable=True)

    # Directional futures specific (nullable for other types)
    direction        = Column(String(8),   nullable=True)   # LONG | SHORT


class StrategyLeg(Base):
    """
    One row per leg per strategy run.

    CS-only columns : role, strike, option_type, action
    FD-only columns : direction, lot_size (leg-level override)
    """
    __tablename__ = "strategy_legs"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    run_id          = Column(Integer, nullable=False, index=True)
    symbol          = Column(String(64),  nullable=False)
    exchange        = Column(String(16),  nullable=False)
    quantity        = Column(Integer,     nullable=False)
    product         = Column(String(16),  nullable=False)
    expiry          = Column(String(32),  nullable=True)
    entry_price     = Column(Float,       nullable=True)
    exit_price      = Column(Float,       nullable=True)
    entry_order_id  = Column(String(64),  nullable=True)
    exit_order_id   = Column(String(64),  nullable=True)
    entry_time      = Column(DateTime,    nullable=True)
    exit_time       = Column(DateTime,    nullable=True)
    status          = Column(String(16),  nullable=False, default="PENDING")  # PENDING | OPEN | CLOSED | FAILED
    realized_pnl    = Column(Float,       nullable=True)
    created_at      = Column(DateTime,    nullable=False, default=datetime.datetime.utcnow)

    # Credit spread specific
    role            = Column(String(16),  nullable=True)   # PRIMARY | HEDGE
    strike          = Column(Float,       nullable=True)
    option_type     = Column(String(4),   nullable=True)   # CE | PE | FUT
    action          = Column(String(8),   nullable=True)   # BUY | SELL

    # Directional futures specific
    direction       = Column(String(8),   nullable=True)   # LONG | SHORT
    lot_size        = Column(Integer,     nullable=True)


class StrategyMtmSnapshot(Base):
    """Periodic MTM capture recorded during monitoring."""
    __tablename__ = "strategy_mtm_snapshots"

    id          = Column(Integer,  primary_key=True, autoincrement=True)
    run_id      = Column(Integer,  nullable=False, index=True)
    ts          = Column(DateTime, nullable=False, index=True, default=datetime.datetime.utcnow)
    spot        = Column(Float,    nullable=True)
    mtm         = Column(Float,    nullable=True)
    locked_mtm  = Column(Float,    nullable=True)   # trailing SL lock / locked profit

    # Credit spread specific
    max_mtm     = Column(Float,    nullable=True)
    primary_ltp = Column(Float,    nullable=True)
    hedge_ltp   = Column(Float,    nullable=True)
    phase       = Column(String(24), nullable=True)

    # Directional futures specific
    futures_ltp = Column(Float,    nullable=True)

    __table_args__ = (
        Index("ix_strat_mtm_run_ts", "run_id", "ts"),
    )


class StrategyTrade(Base):
    """Individual order/fill record — ENTRY, EXIT, PARTIAL_EXIT, etc."""
    __tablename__ = "strategy_trades"

    id          = Column(Integer,  primary_key=True, autoincrement=True)
    run_id      = Column(Integer,  nullable=False, index=True)
    leg_id      = Column(Integer,  nullable=True)
    event_type  = Column(String(32),  nullable=False)   # ENTRY | EXIT | PARTIAL_EXIT
    symbol      = Column(String(64),  nullable=False)
    quantity    = Column(Integer,     nullable=False)
    order_id    = Column(String(64),  nullable=True)
    fill_price  = Column(Float,       nullable=True)
    fill_time   = Column(DateTime,    nullable=True)
    status      = Column(String(16),  nullable=True)    # complete | paper | pending | rejected
    notes       = Column(Text,        nullable=True)
    created_at  = Column(DateTime,    nullable=False, default=datetime.datetime.utcnow)

    # Credit spread specific
    action      = Column(String(8),   nullable=True)    # BUY | SELL
    order_price = Column(Float,       nullable=True)

    # Directional futures specific
    direction   = Column(String(8),   nullable=True)    # LONG | SHORT


class StrategySignalEvent(Base):
    """Signal detection audit trail."""
    __tablename__ = "strategy_signal_events"

    id              = Column(Integer,  primary_key=True, autoincrement=True)
    run_id          = Column(Integer,  nullable=True, index=True)
    ts              = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    bias            = Column(String(16),  nullable=False)   # BULLISH | BEARISH | NEUTRAL
    spot            = Column(Float,       nullable=True)
    rsi             = Column(Float,       nullable=True)
    rsi_ema         = Column(Float,       nullable=True)
    ema_fast        = Column(Float,       nullable=True)
    ema_slow        = Column(Float,       nullable=True)
    supertrend      = Column(Float,       nullable=True)
    supertrend_dir  = Column(Integer,     nullable=True)    # -1 | +1
    confirmation_n  = Column(Integer,     nullable=True)
    action_taken    = Column(String(64),  nullable=True)    # ENTRY_TRIGGERED | MONITOR_ONLY_SIGNAL | ...

    # Credit spread specific
    spread_type     = Column(String(32),  nullable=True)
    notes           = Column(Text,        nullable=True)


class StrategyAdjustment(Base):
    """SL trail / partial exit / roll event."""
    __tablename__ = "strategy_adjustments"

    id              = Column(Integer,  primary_key=True, autoincrement=True)
    run_id          = Column(Integer,  nullable=False, index=True)
    ts              = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    adjustment_type = Column(String(32),  nullable=False)   # SL_TRAIL | PARTIAL_EXIT | ROLL | CLOSE
    trigger_reason  = Column(String(64),  nullable=True)
    spot_at_trigger = Column(Float,       nullable=True)
    mtm_at_trigger  = Column(Float,       nullable=True)
    old_sl          = Column(Float,       nullable=True)
    new_sl          = Column(Float,       nullable=True)
    lots_before     = Column(Integer,     nullable=True)
    lots_after      = Column(Integer,     nullable=True)
    notes           = Column(Text,        nullable=True)
    created_at      = Column(DateTime,    nullable=False, default=datetime.datetime.utcnow)


class StrategyNotification(Base):
    """Outgoing notification audit log."""
    __tablename__ = "strategy_notifications"

    id         = Column(Integer,  primary_key=True, autoincrement=True)
    run_id     = Column(Integer,  nullable=True)
    ts         = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)
    level      = Column(String(16), nullable=False)               # INFO | WARNING | ALERT | URGENT
    channel    = Column(String(32), nullable=False, default="LOG")  # LOG | TELEGRAM
    message    = Column(Text,     nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.datetime.utcnow)


# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------

def init_db(database_url: str = "sqlite:///logs/strategies.db"):
    """
    Create all tables (if not existing) and return the SQLAlchemy engine.

    Parameters
    ----------
    database_url : SQLAlchemy connection URL.
                   SQLite  (default): ``"sqlite:///logs/strategies.db"``
                   PostgreSQL:        ``"postgresql+psycopg2://user:pw@host/db"``
    """
    engine = create_engine(database_url, echo=False, future=True)
    Base.metadata.create_all(engine)
    logger.info("Unified strategy DB initialised: %s", database_url)
    return engine


class SessionFactory:
    """Thin factory that creates new Sessions from the engine."""

    def __init__(self, engine) -> None:
        self._factory = sessionmaker(bind=engine, expire_on_commit=False)

    def make(self) -> Session:
        return self._factory()


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def save_strategy_run(session: Session, **kwargs) -> int:
    """
    Insert a new strategy run.  Returns the new ``run_id``.

    MUST be called directly (not via DBWriteQueue) because it returns run_id.
    Caller is responsible for commit/rollback/close.

    Required kwargs: strategy_type, strategy_id, strategy_name,
                     underlying, expiry
    """
    if "updated_at" not in kwargs:
        kwargs["updated_at"] = datetime.datetime.utcnow()
    run = StrategyRun(**kwargs)
    session.add(run)
    session.flush()   # assigns run.id without committing
    return run.id


def update_strategy_run(session: Session, run_id: int, **kwargs) -> None:
    """Update fields on an existing strategy run (DBWriteQueue-compatible)."""
    kwargs["updated_at"] = datetime.datetime.utcnow()
    session.query(StrategyRun).filter_by(id=run_id).update(kwargs)


def save_leg(session: Session, **kwargs) -> None:
    """Insert a new leg record (DBWriteQueue-compatible)."""
    session.add(StrategyLeg(**kwargs))


def update_leg(session: Session, leg_id: int, **kwargs) -> None:
    """Update fields on a leg record (DBWriteQueue-compatible)."""
    leg = session.get(StrategyLeg, leg_id)
    if leg is None:
        logger.warning("update_leg: leg_id=%d not found", leg_id)
        return
    for k, v in kwargs.items():
        if v is not None:
            setattr(leg, k, v)


def save_mtm_snapshot(
    session: Session,
    run_id: int,
    spot: Optional[float],
    mtm: float,
    *,
    locked_mtm: Optional[float] = None,
    max_mtm: Optional[float] = None,
    primary_ltp: Optional[float] = None,
    hedge_ltp: Optional[float] = None,
    phase: Optional[str] = None,
    futures_ltp: Optional[float] = None,
    ts: Optional[datetime.datetime] = None,
) -> None:
    """Insert a periodic MTM snapshot (DBWriteQueue-compatible)."""
    session.add(StrategyMtmSnapshot(
        run_id=run_id,
        ts=ts or datetime.datetime.utcnow(),
        spot=spot,
        mtm=mtm,
        locked_mtm=locked_mtm,
        max_mtm=max_mtm,
        primary_ltp=primary_ltp,
        hedge_ltp=hedge_ltp,
        phase=phase,
        futures_ltp=futures_ltp,
    ))


def save_trade(session: Session, **kwargs) -> None:
    """Insert a trade/order record (DBWriteQueue-compatible)."""
    session.add(StrategyTrade(**kwargs))


def save_signal_event(session: Session, **kwargs) -> None:
    """Insert a signal detection record (DBWriteQueue-compatible)."""
    session.add(StrategySignalEvent(**kwargs))


def save_adjustment(session: Session, **kwargs) -> None:
    """Insert an adjustment record (DBWriteQueue-compatible)."""
    session.add(StrategyAdjustment(**kwargs))


def save_notification(
    session: Session,
    run_id: Optional[int],
    level: str,
    message: str,
    channel: str = "LOG",
    ts: Optional[datetime.datetime] = None,
) -> None:
    """Insert a notification audit-log row (DBWriteQueue-compatible)."""
    session.add(StrategyNotification(
        run_id=run_id,
        ts=ts or datetime.datetime.utcnow(),
        level=level,
        channel=channel,
        message=message,
    ))


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def get_open_runs(session: Session, strategy_id: str) -> list[StrategyRun]:
    """Return all OPEN runs for *strategy_id*."""
    return (
        session.query(StrategyRun)
        .filter_by(strategy_id=strategy_id, status="OPEN")
        .all()
    )


def get_open_runs_by_type(
    session: Session,
    strategy_type: str,
    strategy_id: Optional[str] = None,
) -> list[StrategyRun]:
    """Return all OPEN runs for a strategy type, optionally filtered by strategy_id."""
    q = session.query(StrategyRun).filter_by(status="OPEN", strategy_type=strategy_type)
    if strategy_id:
        q = q.filter(StrategyRun.strategy_id == strategy_id)
    return q.all()


def get_run_legs(session: Session, run_id: int) -> list[StrategyLeg]:
    """Return all legs for *run_id*."""
    return session.query(StrategyLeg).filter_by(run_id=run_id).order_by(StrategyLeg.id).all()


def get_run_leg(session: Session, run_id: int) -> Optional[StrategyLeg]:
    """Return the first leg for *run_id* (convenience for single-leg strategies)."""
    return session.query(StrategyLeg).filter_by(run_id=run_id).first()


def get_mtm_snapshots(session: Session, run_id: int) -> list[StrategyMtmSnapshot]:
    """Return MTM snapshots for *run_id* ordered by time."""
    return (
        session.query(StrategyMtmSnapshot)
        .filter_by(run_id=run_id)
        .order_by(StrategyMtmSnapshot.ts)
        .all()
    )


def get_trades_for_run(session: Session, run_id: int) -> list[StrategyTrade]:
    """Return trade records for *run_id*."""
    return (
        session.query(StrategyTrade)
        .filter_by(run_id=run_id)
        .order_by(StrategyTrade.fill_time)
        .all()
    )
