"""
Alert models:
  AlertRuleBuilder — strategy-based alert rules (FK → strategies)
  AlertRule        — monitored-position alert rules (plain position_id string)
  AlertHistory     — fired alert history for monitored positions
"""
import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from core.database import Base


class AlertRuleBuilder(Base):
    __tablename__ = "alert_rule_builders"

    alert_id: Mapped[str] = mapped_column(
        sa.String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    strategy_id: Mapped[str] = mapped_column(
        sa.String(36),
        sa.ForeignKey("strategies.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    trigger_once: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    cooldown_secs: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    triggered_count: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    last_triggered: Mapped[datetime | None] = mapped_column(
        sa.DateTime(timezone=True), nullable=True
    )
    notify_popup: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    notify_telegram: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    notify_email: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    notify_webhook: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    notify_sound: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    webhook_url: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    condition_tree: Mapped[dict] = mapped_column(sa.JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )


# ── AlertRule — attached to monitored positions ────────────────────────────────

class AlertRule(Base):
    __tablename__ = "alert_rules"

    alert_id: Mapped[str] = mapped_column(
        sa.String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    position_id: Mapped[str] = mapped_column(sa.String(36), nullable=False, index=True)
    position_type: Mapped[str] = mapped_column(sa.String(30), nullable=False, default="MONITORED")
    strategy_name: Mapped[str] = mapped_column(sa.String(200), nullable=False, default="")
    underlying: Mapped[str] = mapped_column(sa.String(50), nullable=False, default="")
    name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.String(1000), nullable=True)
    is_active: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    trigger_once: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    cooldown_secs: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    triggered_count: Mapped[int] = mapped_column(sa.Integer, default=0, nullable=False)
    last_triggered: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    notify_popup: Mapped[bool] = mapped_column(sa.Boolean, default=True, nullable=False)
    notify_telegram: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    notify_email: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    notify_webhook: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    notify_sound: Mapped[bool] = mapped_column(sa.Boolean, default=False, nullable=False)
    webhook_url: Mapped[str | None] = mapped_column(sa.String(500), nullable=True)
    telegram_chat_id: Mapped[str | None] = mapped_column(sa.String(100), nullable=True)
    condition_tree: Mapped[dict] = mapped_column(sa.JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        server_default=sa.func.now(),
        onupdate=sa.func.now(),
        nullable=False,
    )


# ── AlertHistory — fired alert events for monitored positions ──────────────────

class AlertHistory(Base):
    __tablename__ = "alert_histories"

    history_id: Mapped[str] = mapped_column(
        sa.String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    alert_id: Mapped[str] = mapped_column(sa.String(36), nullable=False, index=True)
    position_id: Mapped[str] = mapped_column(sa.String(36), nullable=False, index=True)
    alert_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    strategy_name: Mapped[str] = mapped_column(sa.String(200), nullable=False, default="")
    underlying: Mapped[str] = mapped_column(sa.String(50), nullable=False, default="")
    fired_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
    )
    condition_summary: Mapped[str] = mapped_column(sa.Text, nullable=False, default="")
    context_snapshot: Mapped[dict] = mapped_column(sa.JSON, nullable=False, default=dict)
    notifications_sent: Mapped[dict] = mapped_column(sa.JSON, nullable=False, default=dict)
