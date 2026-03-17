"""
AlertRuleBuilder — SQLAlchemy model for the nested condition-tree alert rules.
Named AlertRuleBuilder to avoid collision with existing AlertRule dict types.
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
