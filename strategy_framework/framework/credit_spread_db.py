"""
credit_spread_db.py
-------------------
SQLAlchemy ORM models and DB helpers for credit spread strategy persistence.

Tables
------
cs_strategy_runs     — one row per strategy deployment (entry to exit).
cs_legs              — one row per option leg per run.
cs_mtm_snapshots     — periodic MTM captures (every monitoring cycle).
cs_trades            — entry and exit order events.
cs_signal_events     — signal detection history.
cs_adjustments       — adjustment / roll events.
cs_notifications     — outbox for sent notifications (audit log).

Usage
-----
::

    from framework.credit_spread_db import init_db, SessionFactory
    from framework.db_writer import DBWriteQueue

    engine = init_db("sqlite:///credit_spread.db")
    queue  = DBWriteQueue(SessionFactory(engine).make)
    queue.start()

    # Non-blocking write:
    queue.put(save_mtm_snapshot, run_id=1, mtm=1234.0, spot=24500.0)
"""
from __future__ import annotations

import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer, String, Text, create_engine,
    Index,
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

class CsStrategyRun(Base):
    """
    One row per strategy deployment lifecycle (from first entry to full exit).
    """
    __tablename__ = "cs_strategy_runs"

    id               = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id      = Column(String(64),  nullable=False, index=True)  # e.g. "CREDIT_SPREAD_202508"
    strategy_name    = Column(String(64),  nullable=False)
    spread_type      = Column(String(32),  nullable=False)               # BULL_PUT_SPREAD / BEAR_CALL_SPREAD
    underlying       = Column(String(16),  nullable=False)               # NIFTY / BANKNIFTY
    expiry           = Column(String(16),  nullable=False)               # 31JUL25
    entry_spot       = Column(Float,       nullable=True)
    primary_strike   = Column(Float,       nullable=True)
    hedge_strike     = Column(Float,       nullable=True)
    net_premium      = Column(Float,       nullable=True)                # collected per unit
    lots             = Column(Integer,     nullable=True)
    lot_size         = Column(Integer,     nullable=True)
    max_profit       = Column(Float,       nullable=True)
    max_loss         = Column(Float,       nullable=True)
    entry_time       = Column(DateTime,    nullable=True)
    exit_time        = Column(DateTime,    nullable=True)
    exit_reason      = Column(String(64),  nullable=True)                # SL_HIT / TARGET / EXPIRY / MANUAL
    realized_pnl     = Column(Float,       nullable=True)
    status           = Column(String(16),  nullable=False, default="OPEN")  # OPEN / CLOSED / PAUSED
    execution_mode   = Column(String(16),  nullable=False, default="MONITOR_ONLY")
    trade_mode       = Column(String(32),  nullable=False, default="MONITOR_ONLY")  # MONITOR_ONLY | ENTRY_EXEC_MONITOR | EXIT_EXEC_MONITOR | FULL_EXEC
    notes            = Column(Text,        nullable=True)
    created_at       = Column(DateTime,    default=datetime.datetime.utcnow)
    updated_at       = Column(DateTime,    default=datetime.datetime.utcnow,
                              onupdate=datetime.datetime.utcnow)


class CsLeg(Base):
    """One row per option leg per strategy run."""
    __tablename__ = "cs_legs"

    id            = Column(Integer, primary_key=True, autoincrement=True)
    run_id        = Column(Integer, nullable=False, index=True)
    role          = Column(String(16), nullable=False)      # PRIMARY / HEDGE
    symbol        = Column(String(32), nullable=False)
    exchange      = Column(String(16), nullable=False)
    strike        = Column(Float,      nullable=False)
    option_type   = Column(String(4),  nullable=False)      # CE / PE
    action        = Column(String(8),  nullable=False)      # BUY / SELL
    quantity      = Column(Integer,    nullable=False)
    product       = Column(String(8),  nullable=False)
    expiry        = Column(String(16), nullable=False)
    entry_price   = Column(Float,      nullable=True)
    exit_price    = Column(Float,      nullable=True)
    entry_order_id= Column(String(64), nullable=True)
    exit_order_id = Column(String(64), nullable=True)
    entry_time    = Column(DateTime,   nullable=True)
    exit_time     = Column(DateTime,   nullable=True)
    status        = Column(String(16), nullable=False, default="PENDING")  # PENDING/OPEN/CLOSED/FAILED
    realized_pnl  = Column(Float,      nullable=True)
    created_at    = Column(DateTime,   default=datetime.datetime.utcnow)


class CsMtmSnapshot(Base):
    """Periodic MTM capture (one row per monitoring cycle)."""
    __tablename__ = "cs_mtm_snapshots"

    id          = Column(Integer,  primary_key=True, autoincrement=True)
    run_id      = Column(Integer,  nullable=False, index=True)
    ts          = Column(DateTime, nullable=False, index=True)
    spot        = Column(Float,    nullable=False)
    mtm         = Column(Float,    nullable=False)
    max_mtm     = Column(Float,    nullable=True)
    locked_mtm  = Column(Float,    nullable=True)
    primary_ltp = Column(Float,    nullable=True)
    hedge_ltp   = Column(Float,    nullable=True)
    phase       = Column(String(24), nullable=True)

    __table_args__ = (
        Index("ix_cs_mtm_run_ts", "run_id", "ts"),
    )


class CsTrade(Base):
    """An individual order event (entry or exit)."""
    __tablename__ = "cs_trades"

    id          = Column(Integer,  primary_key=True, autoincrement=True)
    run_id      = Column(Integer,  nullable=False, index=True)
    leg_id      = Column(Integer,  nullable=True)               # FK to cs_legs.id
    event_type  = Column(String(16), nullable=False)            # ENTRY / EXIT / PARTIAL_EXIT
    symbol      = Column(String(32), nullable=False)
    action      = Column(String(8),  nullable=False)
    quantity    = Column(Integer,    nullable=False)
    order_id    = Column(String(64), nullable=True)
    order_price = Column(Float,      nullable=True)
    fill_price  = Column(Float,      nullable=True)
    fill_time   = Column(DateTime,   nullable=True)
    status      = Column(String(16), nullable=True)             # complete / rejected
    notes       = Column(Text,       nullable=True)
    created_at  = Column(DateTime,   default=datetime.datetime.utcnow)


class CsSignalEvent(Base):
    """A signal detection record (fired or suppressed)."""
    __tablename__ = "cs_signal_events"

    id              = Column(Integer,  primary_key=True, autoincrement=True)
    run_id          = Column(Integer,  nullable=False, index=True)
    ts              = Column(DateTime, nullable=False)
    bias            = Column(String(16), nullable=False)         # BULLISH / BEARISH / NEUTRAL
    spread_type     = Column(String(32), nullable=True)
    rsi             = Column(Float,   nullable=True)
    supertrend_dir  = Column(Integer, nullable=True)             # -1 / +1
    ema_fast        = Column(Float,   nullable=True)
    ema_slow        = Column(Float,   nullable=True)
    spot            = Column(Float,   nullable=True)
    confirmation_n  = Column(Integer, nullable=True)
    action_taken    = Column(String(32), nullable=True)          # ENTRY_TRIGGERED / COOLDOWN / SKIPPED
    notes           = Column(Text,    nullable=True)
    created_at      = Column(DateTime, default=datetime.datetime.utcnow)


class CsAdjustment(Base):
    """An adjustment or roll event during the strategy lifecycle."""
    __tablename__ = "cs_adjustments"

    id              = Column(Integer,  primary_key=True, autoincrement=True)
    run_id          = Column(Integer,  nullable=False, index=True)
    ts              = Column(DateTime, nullable=False)
    adjustment_type = Column(String(32), nullable=False)         # SL_TRAIL / PARTIAL_EXIT / ROLL / CLOSE
    trigger_reason  = Column(String(64), nullable=True)
    spot_at_trigger = Column(Float,   nullable=True)
    mtm_at_trigger  = Column(Float,   nullable=True)
    old_sl          = Column(Float,   nullable=True)
    new_sl          = Column(Float,   nullable=True)
    lots_before     = Column(Integer, nullable=True)
    lots_after      = Column(Integer, nullable=True)
    notes           = Column(Text,    nullable=True)
    created_at      = Column(DateTime, default=datetime.datetime.utcnow)


class CsNotification(Base):
    """Audit log of every notification sent."""
    __tablename__ = "cs_notifications"

    id         = Column(Integer,  primary_key=True, autoincrement=True)
    run_id     = Column(Integer,  nullable=True)
    ts         = Column(DateTime, nullable=False)
    level      = Column(String(16), nullable=False)              # INFO / WARNING / ALERT / URGENT
    channel    = Column(String(32), nullable=False, default="LOG")  # LOG / TELEGRAM
    message    = Column(Text,     nullable=False)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)


# ---------------------------------------------------------------------------
# Engine & session factory
# ---------------------------------------------------------------------------

def init_db(database_url: str = "sqlite:///credit_spread.db") -> object:
    """
    Create all tables and return the SQLAlchemy engine.

    Parameters
    ----------
    database_url : SQLAlchemy connection URL.
                   SQLite  (default): ``"sqlite:///credit_spread.db"``
                   PostgreSQL:        ``"postgresql+psycopg2://user:pw@host/db"``
    """
    engine = create_engine(database_url, echo=False, future=True)
    Base.metadata.create_all(engine)
    logger.info("DB initialised: %s", database_url)
    return engine


class SessionFactory:
    """Thin factory that creates new Sessions from the engine."""

    def __init__(self, engine) -> None:
        self._factory = sessionmaker(bind=engine, expire_on_commit=False)

    def make(self) -> Session:
        return self._factory()


# ---------------------------------------------------------------------------
# Write helpers (called by DBWriteQueue)
# ---------------------------------------------------------------------------

def save_strategy_run(session: Session, **kwargs) -> int:
    """Insert a new strategy run record. Returns the new run_id."""
    run = CsStrategyRun(**kwargs)
    session.add(run)
    session.flush()
    return run.id


def update_strategy_run(session: Session, run_id: int, **kwargs) -> None:
    """Update fields on an existing strategy run."""
    session.query(CsStrategyRun).filter_by(id=run_id).update(
        {**kwargs, "updated_at": datetime.datetime.utcnow()}
    )


def save_leg(session: Session, **kwargs) -> int:
    """Insert a new leg record. Returns the new leg_id."""
    leg = CsLeg(**kwargs)
    session.add(leg)
    session.flush()
    return leg.id


def update_leg(session: Session, leg_id: int, **kwargs) -> None:
    """Update fields on a leg record (e.g. after fill or exit)."""
    session.query(CsLeg).filter_by(id=leg_id).update(kwargs)


def save_mtm_snapshot(
    session: Session,
    run_id: int,
    spot: float,
    mtm: float,
    max_mtm: Optional[float] = None,
    locked_mtm: Optional[float] = None,
    primary_ltp: Optional[float] = None,
    hedge_ltp: Optional[float] = None,
    phase: Optional[str] = None,
) -> None:
    """Insert a periodic MTM snapshot row."""
    snap = CsMtmSnapshot(
        run_id=run_id,
        ts=datetime.datetime.utcnow(),
        spot=spot,
        mtm=mtm,
        max_mtm=max_mtm,
        locked_mtm=locked_mtm,
        primary_ltp=primary_ltp,
        hedge_ltp=hedge_ltp,
        phase=phase,
    )
    session.add(snap)


def save_trade(session: Session, **kwargs) -> None:
    """Insert a trade record (entry or exit order event)."""
    session.add(CsTrade(**kwargs))


def save_signal_event(session: Session, **kwargs) -> None:
    """Insert a signal detection record."""
    session.add(CsSignalEvent(**kwargs))


def save_adjustment(session: Session, **kwargs) -> None:
    """Insert an adjustment record."""
    session.add(CsAdjustment(**kwargs))


def save_notification(
    session: Session,
    run_id: Optional[int],
    level: str,
    message: str,
    channel: str = "LOG",
) -> None:
    """Insert a notification audit-log row."""
    session.add(CsNotification(
        run_id=run_id,
        ts=datetime.datetime.utcnow(),
        level=level,
        channel=channel,
        message=message,
    ))


def get_open_runs(session: Session, strategy_id: str) -> list[CsStrategyRun]:
    """Return all OPEN runs for *strategy_id* (should be at most 1)."""
    return (
        session.query(CsStrategyRun)
        .filter_by(strategy_id=strategy_id, status="OPEN")
        .all()
    )


def get_run_legs(session: Session, run_id: int) -> list[CsLeg]:
    """Return all leg records for *run_id*."""
    return session.query(CsLeg).filter_by(run_id=run_id).all()


def get_closed_runs(
    session: Session,
    trade_mode: Optional[str] = None,
    spread_type: Optional[str] = None,
    underlying: Optional[str] = None,
    limit: int = 500,
) -> list[CsStrategyRun]:
    """Return CLOSED runs, optionally filtered by trade_mode / spread_type / underlying."""
    q = session.query(CsStrategyRun).filter_by(status="CLOSED")
    if trade_mode:
        q = q.filter(CsStrategyRun.trade_mode == trade_mode)
    if spread_type:
        q = q.filter(CsStrategyRun.spread_type == spread_type)
    if underlying:
        q = q.filter(CsStrategyRun.underlying == underlying)
    return q.order_by(CsStrategyRun.entry_time).limit(limit).all()


def get_mtm_snapshots(session: Session, run_id: int) -> list[CsMtmSnapshot]:
    """Return all MTM snapshots for *run_id* ordered by time."""
    return (
        session.query(CsMtmSnapshot)
        .filter_by(run_id=run_id)
        .order_by(CsMtmSnapshot.ts)
        .all()
    )


def get_trades_for_run(session: Session, run_id: int) -> list[CsTrade]:
    """Return all trade records for *run_id*."""
    return session.query(CsTrade).filter_by(run_id=run_id).order_by(CsTrade.fill_time).all()
