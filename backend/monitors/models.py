"""
MonitoredPosition + MonitoredLeg — manually entered positions for live monitoring.
No order execution. TradeMan fetches live LTP and computes real-time MTM.
"""
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.database import Base


class MonitoredPosition(Base):
    __tablename__ = "monitored_positions"

    monitor_id: Mapped[str] = mapped_column(
        sa.String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    strategy_type: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="CUSTOM"
    )  # IRON_CONDOR | STRADDLE | STRANGLE | BULL_CALL_SPREAD | BEAR_PUT_SPREAD | IRON_FLY | COVERED_CALL | FUTURES | CUSTOM
    underlying: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    exchange: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="NFO")
    status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default="ACTIVE"
    )  # ACTIVE | PAUSED | CLOSED
    notes: Mapped[str | None] = mapped_column(sa.Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )

    legs: Mapped[list["MonitoredLeg"]] = relationship(
        "MonitoredLeg",
        back_populates="position",
        cascade="all, delete-orphan",
        order_by="MonitoredLeg.leg_number",
    )


class MonitoredLeg(Base):
    __tablename__ = "monitored_legs"

    leg_id: Mapped[str] = mapped_column(
        sa.String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    monitor_id: Mapped[str] = mapped_column(
        sa.String(36),
        sa.ForeignKey("monitored_positions.monitor_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    leg_number: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    instrument: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    underlying: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    strike: Mapped[float | None] = mapped_column(sa.Float, nullable=True)
    option_type: Mapped[str] = mapped_column(sa.String(10), nullable=False)  # CE | PE | FUT | EQ
    expiry: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    side: Mapped[str] = mapped_column(sa.String(10), nullable=False)  # BUY | SELL
    quantity: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    lot_size: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=1)
    entry_price: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    current_price: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    pnl: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)
    premium_change_pct: Mapped[float] = mapped_column(sa.Float, nullable=False, default=0.0)

    position: Mapped["MonitoredPosition"] = relationship(
        "MonitoredPosition", back_populates="legs"
    )
