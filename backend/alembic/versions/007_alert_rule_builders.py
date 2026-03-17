"""Add alert_rule_builders table

Revision ID: 007
Revises: 006
Create Date: 2026-03-17
"""
from typing import Sequence, Union
import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: Union[str, None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_rule_builders",
        sa.Column("alert_id", sa.String(36), primary_key=True),
        sa.Column(
            "strategy_id",
            sa.String(36),
            sa.ForeignKey("strategies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("trigger_once", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("cooldown_secs", sa.Integer, nullable=False, server_default="0"),
        sa.Column("triggered_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_triggered", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notify_popup", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("notify_telegram", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("notify_email", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("notify_webhook", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("notify_sound", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("webhook_url", sa.String(500), nullable=True),
        sa.Column("telegram_chat_id", sa.String(100), nullable=True),
        sa.Column("condition_tree", sa.JSON, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_alert_rule_builders_strategy_id",
        "alert_rule_builders",
        ["strategy_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_alert_rule_builders_strategy_id", "alert_rule_builders")
    op.drop_table("alert_rule_builders")
