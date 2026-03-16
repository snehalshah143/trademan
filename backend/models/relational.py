"""
Relational models — Strategy, StrategyLeg, Order, AlertEvent, StrategyAutomationConfig.

UUID primary keys (stored as CHAR(32) on SQLite, native UUID on PostgreSQL).
JSON columns map to JSONB on PostgreSQL and TEXT/BLOB on SQLite — identical Python API.
"""
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


# ── Enums (stored as VARCHAR) ─────────────────────────────────────────────────

class StrategyStatus:
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    CLOSED = "closed"


class LegAction:
    BUY = "BUY"
    SELL = "SELL"


class OptionType:
    CE = "CE"
    PE = "PE"
    FUT = "FUT"


class LegStatus:
    PENDING = "pending"
    FILLED = "filled"
    CANCELLED = "cancelled"


class OrderStatus:
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    REJECTED = "rejected"
    CANCELLED = "cancelled"


class OrderType:
    MARKET = "MARKET"
    LIMIT = "LIMIT"
    SL = "SL"
    SL_M = "SL-M"


class AlertSeverity:
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ── Models ────────────────────────────────────────────────────────────────────

class Strategy(Base):
    __tablename__ = "strategies"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(1000), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=StrategyStatus.DRAFT)
    underlying: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    expiry: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    legs: Mapped[List["StrategyLeg"]] = relationship(
        "StrategyLeg", back_populates="strategy", cascade="all, delete-orphan"
    )
    orders: Mapped[List["Order"]] = relationship(
        "Order", back_populates="strategy", cascade="all, delete-orphan"
    )
    alert_events: Mapped[List["AlertEvent"]] = relationship(
        "AlertEvent", back_populates="strategy", cascade="all, delete-orphan"
    )
    automation_config: Mapped[Optional["StrategyAutomationConfig"]] = relationship(
        "StrategyAutomationConfig",
        back_populates="strategy",
        uselist=False,
        cascade="all, delete-orphan",
    )


class StrategyLeg(Base):
    __tablename__ = "strategy_legs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    symbol: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)          # BUY | SELL
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    option_type: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)  # CE | PE | FUT
    strike: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    expiry: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    entry_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    exit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=LegStatus.PENDING)
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)

    # Relationship
    strategy: Mapped["Strategy"] = relationship("Strategy", back_populates="legs")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategies.id", ondelete="CASCADE"), nullable=False
    )
    leg_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategy_legs.id", ondelete="SET NULL"), nullable=True
    )
    broker_order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    symbol: Mapped[str] = mapped_column(String(100), nullable=False)
    action: Mapped[str] = mapped_column(String(10), nullable=False)          # BUY | SELL
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    order_type: Mapped[str] = mapped_column(String(10), nullable=False, default=OrderType.MARKET)
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=OrderStatus.PENDING)
    filled_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationships
    strategy: Mapped["Strategy"] = relationship("Strategy", back_populates="orders")


class AlertEvent(Base):
    __tablename__ = "alert_events"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("strategies.id", ondelete="SET NULL"), nullable=True
    )
    rule_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    message: Mapped[str] = mapped_column(String(1000), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default=AlertSeverity.INFO)
    triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    dismissed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dismissed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # Relationship
    strategy: Mapped[Optional["Strategy"]] = relationship(
        "Strategy", back_populates="alert_events"
    )


class CachedInstrument(Base):
    """Instrument catalogue cached from OpenAlgo or seeded from static list."""

    __tablename__ = "cached_instruments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[str] = mapped_column(String(20), nullable=False, default="STOCK")  # INDEX | STOCK
    exchange: Mapped[str] = mapped_column(String(10), nullable=False, default="NFO")
    lot_size: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    strike_interval: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    has_options: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    has_futures: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="static")  # static | openalgo
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class CachedExpiry(Base):
    """Expiry dates cached per symbol from OpenAlgo."""

    __tablename__ = "cached_expiries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    symbol: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    exchange: Mapped[str] = mapped_column(String(10), nullable=False, default="NFO")
    expiry: Mapped[str] = mapped_column(String(20), nullable=False)   # YYYY-MM-DD
    expiry_type: Mapped[str] = mapped_column(String(10), nullable=False, default="monthly")
    expiry_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    synced_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class InstrumentSyncLog(Base):
    """Log of instrument sync operations."""

    __tablename__ = "instrument_sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    operation: Mapped[str] = mapped_column(String(50), nullable=False)   # sync_all | sync_expiries | seed
    status: Mapped[str] = mapped_column(String(20), nullable=False)      # success | partial | error
    message: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    records_synced: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)


class StrategyAutomationConfig(Base):
    """Automation rules and thresholds for a strategy (JSONB on PG, JSON on SQLite)."""

    __tablename__ = "strategy_automation_configs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    strategy_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    alert_rules: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)  # JSONB on PG
    config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)       # JSONB on PG
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # Relationship
    strategy: Mapped["Strategy"] = relationship(
        "Strategy", back_populates="automation_config"
    )
