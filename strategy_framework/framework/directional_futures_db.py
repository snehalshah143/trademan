"""
directional_futures_db.py
--------------------------
SQLAlchemy ORM and DB helper functions for directional futures strategies.

Tables
------
fd_strategy_runs   — one row per strategy run (entry → exit lifecycle)
fd_legs            — one futures leg per run
fd_mtm_snapshots   — periodic MTM snapshots during monitoring
fd_trades          — individual trade records (ENTRY, EXIT, PARTIAL_EXIT)
fd_signal_events   — signal fire events for audit / analytics

Usage
-----
    from strategy_framework.framework.directional_futures_db import (
        init_db, SessionFactory,
        save_strategy_run, save_leg, save_trade, save_signal_event,
        save_mtm_snapshot, update_leg, update_strategy_run,
        get_open_runs, get_run_legs,
    )
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Column, DateTime, Float, ForeignKey, Integer, String, Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Session, relationship

from ..utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# ORM base
# ---------------------------------------------------------------------------

class _Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# ORM models
# ---------------------------------------------------------------------------

class FDStrategyRun(_Base):
    """One row per strategy run (entry through exit)."""
    __tablename__ = "fd_strategy_runs"

    id             = Column(Integer, primary_key=True, autoincrement=True)
    strategy_id    = Column(String(64),  nullable=False, index=True)
    strategy_name  = Column(String(128), nullable=False)
    underlying     = Column(String(32),  nullable=False)
    expiry         = Column(String(32),  nullable=False)
    direction      = Column(String(8),   nullable=False)          # LONG | SHORT
    entry_spot     = Column(Float,       nullable=True)
    lots           = Column(Integer,     nullable=False, default=1)
    lot_size       = Column(Integer,     nullable=False, default=1)
    entry_time     = Column(DateTime,    nullable=True)
    exit_time      = Column(DateTime,    nullable=True)
    exit_reason    = Column(String(64),  nullable=True)
    status         = Column(String(16),  nullable=False, default="OPEN")  # OPEN | CLOSED
    realized_pnl   = Column(Float,       nullable=True)
    execution_mode = Column(String(32),  nullable=True)
    trade_mode     = Column(String(32),  nullable=False, default="MONITOR_ONLY")  # MONITOR_ONLY | ENTRY_EXEC_MONITOR | EXIT_EXEC_MONITOR | FULL_EXEC
    created_at     = Column(DateTime,    nullable=False, default=datetime.utcnow)

    legs     = relationship("FDLeg",          back_populates="run", cascade="all, delete-orphan")
    trades   = relationship("FDTrade",        back_populates="run", cascade="all, delete-orphan")
    signals  = relationship("FDSignalEvent",  back_populates="run", cascade="all, delete-orphan")
    snapshots = relationship("FDMtmSnapshot", back_populates="run", cascade="all, delete-orphan")


class FDLeg(_Base):
    """Single futures leg — one per strategy run."""
    __tablename__ = "fd_legs"

    id              = Column(Integer, primary_key=True, autoincrement=True)
    run_id          = Column(Integer, ForeignKey("fd_strategy_runs.id"), nullable=False, index=True)
    symbol          = Column(String(64),  nullable=False)
    exchange        = Column(String(16),  nullable=False)
    direction       = Column(String(8),   nullable=False)   # LONG | SHORT
    quantity        = Column(Integer,     nullable=False)
    lot_size        = Column(Integer,     nullable=False)
    product         = Column(String(16),  nullable=False)
    expiry          = Column(String(32),  nullable=True)
    entry_price     = Column(Float,       nullable=True)
    entry_order_id  = Column(String(64),  nullable=True)
    entry_time      = Column(DateTime,    nullable=True)
    exit_price      = Column(Float,       nullable=True)
    exit_order_id   = Column(String(64),  nullable=True)
    exit_time       = Column(DateTime,    nullable=True)
    status          = Column(String(16),  nullable=False, default="PENDING")
    realized_pnl    = Column(Float,       nullable=True)

    run = relationship("FDStrategyRun", back_populates="legs")


class FDMtmSnapshot(_Base):
    """Periodic MTM snapshot recorded during monitoring."""
    __tablename__ = "fd_mtm_snapshots"

    id          = Column(Integer,  primary_key=True, autoincrement=True)
    run_id      = Column(Integer,  ForeignKey("fd_strategy_runs.id"), nullable=False, index=True)
    ts          = Column(DateTime, nullable=False, default=datetime.utcnow)
    spot        = Column(Float,    nullable=True)
    futures_ltp = Column(Float,    nullable=True)
    mtm         = Column(Float,    nullable=True)
    locked_mtm  = Column(Float,    nullable=True)   # trailing SL lock level

    run = relationship("FDStrategyRun", back_populates="snapshots")


class FDTrade(_Base):
    """Individual order/trade record — ENTRY, EXIT, PARTIAL_EXIT."""
    __tablename__ = "fd_trades"

    id         = Column(Integer,  primary_key=True, autoincrement=True)
    run_id     = Column(Integer,  ForeignKey("fd_strategy_runs.id"), nullable=False, index=True)
    leg_id     = Column(Integer,  ForeignKey("fd_legs.id"),          nullable=True)
    event_type = Column(String(32),  nullable=False)   # ENTRY | EXIT | PARTIAL_EXIT
    symbol     = Column(String(64),  nullable=False)
    direction  = Column(String(8),   nullable=False)
    quantity   = Column(Integer,     nullable=False)
    order_id   = Column(String(64),  nullable=True)
    fill_price = Column(Float,       nullable=True)
    fill_time  = Column(DateTime,    nullable=True)
    status     = Column(String(16),  nullable=True)    # complete | paper | pending | rejected
    notes      = Column(Text,        nullable=True)

    run = relationship("FDStrategyRun", back_populates="trades")


class FDSignalEvent(_Base):
    """Signal fire event — audit trail for every confirmed signal."""
    __tablename__ = "fd_signal_events"

    id                 = Column(Integer,  primary_key=True, autoincrement=True)
    run_id             = Column(Integer,  ForeignKey("fd_strategy_runs.id"), nullable=True, index=True)
    ts                 = Column(DateTime, nullable=False, default=datetime.utcnow)
    bias               = Column(String(16),  nullable=False)   # BULLISH | BEARISH
    # Indicator snapshot at signal time
    rsi                = Column(Float,       nullable=True)
    rsi_ema            = Column(Float,       nullable=True)
    ema_fast           = Column(Float,       nullable=True)
    ema_slow           = Column(Float,       nullable=True)
    supertrend         = Column(Float,       nullable=True)
    supertrend_dir     = Column(Integer,     nullable=True)
    spot               = Column(Float,       nullable=True)
    confirmation_n     = Column(Integer,     nullable=True)
    action_taken       = Column(String(64),  nullable=True)   # ENTRY_TRIGGERED | MONITOR_ONLY_SIGNAL

    run = relationship("FDStrategyRun", back_populates="signals")


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------

class SessionFactory:
    """Thin wrapper around SQLAlchemy engine to create sessions."""

    def __init__(self, engine) -> None:
        self._engine = engine

    def make(self) -> Session:
        from sqlalchemy.orm import sessionmaker
        Session_ = sessionmaker(bind=self._engine)
        return Session_()


# ---------------------------------------------------------------------------
# DB init
# ---------------------------------------------------------------------------

def init_db(url: str):
    """Create engine, all tables, and return the engine."""
    engine = create_engine(url, echo=False)
    _Base.metadata.create_all(engine)
    logger.info("DB initialised: %s", url)
    return engine


# ---------------------------------------------------------------------------
# Write helpers
# ---------------------------------------------------------------------------

def save_strategy_run(
    session: Session, *,
    strategy_id: str, strategy_name: str,
    underlying: str, expiry: str, direction: str,
    entry_spot: float, lots: int, lot_size: int,
    entry_time: Optional[datetime],
    status: str = "OPEN",
    execution_mode: str = "",
    trade_mode: str = "MONITOR_ONLY",
) -> int:
    run = FDStrategyRun(
        strategy_id=strategy_id, strategy_name=strategy_name,
        underlying=underlying, expiry=expiry, direction=direction,
        entry_spot=entry_spot, lots=lots, lot_size=lot_size,
        entry_time=entry_time, status=status,
        execution_mode=execution_mode, trade_mode=trade_mode,
    )
    session.add(run)
    session.flush()
    return run.id


def save_leg(
    session: Session, *,
    run_id: int, symbol: str, exchange: str,
    direction: str, quantity: int, lot_size: int,
    product: str, expiry: str,
    entry_price: Optional[float] = None,
    entry_order_id: Optional[str] = None,
    entry_time: Optional[datetime] = None,
    status: str = "PENDING",
) -> None:
    session.add(FDLeg(
        run_id=run_id, symbol=symbol, exchange=exchange,
        direction=direction, quantity=quantity, lot_size=lot_size,
        product=product, expiry=expiry,
        entry_price=entry_price, entry_order_id=entry_order_id,
        entry_time=entry_time, status=status,
    ))


def update_leg(
    session: Session, *,
    leg_id: int,
    exit_price: Optional[float] = None,
    exit_time: Optional[datetime] = None,
    exit_order_id: Optional[str] = None,
    status: Optional[str] = None,
    realized_pnl: Optional[float] = None,
) -> None:
    leg = session.get(FDLeg, leg_id)
    if leg is None:
        logger.warning("update_leg: leg_id=%d not found", leg_id)
        return
    if exit_price    is not None: leg.exit_price    = exit_price
    if exit_time     is not None: leg.exit_time     = exit_time
    if exit_order_id is not None: leg.exit_order_id = exit_order_id
    if status        is not None: leg.status        = status
    if realized_pnl  is not None: leg.realized_pnl  = realized_pnl


def save_mtm_snapshot(
    session: Session, *,
    run_id: int, spot: float, futures_ltp: Optional[float],
    mtm: float, locked_mtm: Optional[float] = None,
) -> None:
    session.add(FDMtmSnapshot(
        run_id=run_id, ts=datetime.utcnow(),
        spot=spot, futures_ltp=futures_ltp,
        mtm=mtm, locked_mtm=locked_mtm,
    ))


def save_trade(
    session: Session, *,
    run_id: int, event_type: str, symbol: str,
    direction: str, quantity: int,
    order_id: Optional[str] = None,
    fill_price: Optional[float] = None,
    fill_time: Optional[datetime] = None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
    leg_id: Optional[int] = None,
) -> None:
    session.add(FDTrade(
        run_id=run_id, leg_id=leg_id,
        event_type=event_type, symbol=symbol,
        direction=direction, quantity=quantity,
        order_id=order_id, fill_price=fill_price,
        fill_time=fill_time, status=status, notes=notes,
    ))


def save_signal_event(
    session: Session, *,
    run_id: Optional[int], ts: datetime, bias: str,
    rsi: Optional[float] = None, rsi_ema: Optional[float] = None,
    ema_fast: Optional[float] = None, ema_slow: Optional[float] = None,
    supertrend: Optional[float] = None, supertrend_dir: Optional[int] = None,
    spot: Optional[float] = None,
    confirmation_n: Optional[int] = None,
    action_taken: Optional[str] = None,
) -> None:
    session.add(FDSignalEvent(
        run_id=run_id, ts=ts, bias=bias,
        rsi=rsi, rsi_ema=rsi_ema,
        ema_fast=ema_fast, ema_slow=ema_slow,
        supertrend=supertrend, supertrend_dir=supertrend_dir,
        spot=spot, confirmation_n=confirmation_n,
        action_taken=action_taken,
    ))


def update_strategy_run(
    session: Session, *,
    run_id: int,
    exit_time: Optional[datetime] = None,
    exit_reason: Optional[str] = None,
    status: Optional[str] = None,
    realized_pnl: Optional[float] = None,
) -> None:
    run = session.get(FDStrategyRun, run_id)
    if run is None:
        logger.warning("update_strategy_run: run_id=%d not found", run_id)
        return
    if exit_time    is not None: run.exit_time    = exit_time
    if exit_reason  is not None: run.exit_reason  = exit_reason
    if status       is not None: run.status       = status
    if realized_pnl is not None: run.realized_pnl = realized_pnl


# ---------------------------------------------------------------------------
# Read helpers
# ---------------------------------------------------------------------------

def get_open_runs(session: Session, strategy_id: str) -> list:
    """Return all OPEN runs for today's strategy_id."""
    return (
        session.query(FDStrategyRun)
        .filter(
            FDStrategyRun.strategy_id == strategy_id,
            FDStrategyRun.status == "OPEN",
        )
        .all()
    )


def get_run_leg(session: Session, run_id: int) -> Optional[FDLeg]:
    """Return the futures leg for a given run_id."""
    return (
        session.query(FDLeg)
        .filter(FDLeg.run_id == run_id)
        .first()
    )
