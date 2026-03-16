"""
Time-series models — Candle, MTMSnapshot, LTPTick.

These will become TimescaleDB hypertables in production via Alembic migrations
(002_timescale_extension → 003_hypertables). In SQLite dev they are plain tables.
"""
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class Candle(Base):
    """OHLCV candle. Hypertable on (ts) partitioned by symbol in production."""

    __tablename__ = "candles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)  # 5m | 15m | 75m | 1d
    open: Mapped[float] = mapped_column(Float, nullable=False)
    high: Mapped[float] = mapped_column(Float, nullable=False)
    low: Mapped[float] = mapped_column(Float, nullable=False)
    close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class MTMSnapshot(Base):
    """Mark-to-market snapshot captured every 15 s. Hypertable on (ts) in production."""

    __tablename__ = "mtm_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    mtm: Mapped[float] = mapped_column(Float, nullable=False)
    realized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    unrealized_pnl: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class LTPTick(Base):
    """Raw LTP tick archive (only written when STORE_TICKS=true). Hypertable on (ts) in production."""

    __tablename__ = "ltp_ticks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    symbol: Mapped[str] = mapped_column(String(100), nullable=False, index=True)
    ltp: Mapped[float] = mapped_column(Float, nullable=False)
    change: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
