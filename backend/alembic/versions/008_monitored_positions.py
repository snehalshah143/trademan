"""008 — monitored positions, alert_rules, alert_histories tables

Revision ID: 008
Revises: 007
Create Date: 2026-03-17
"""
from alembic import op
import sqlalchemy as sa

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "monitored_positions",
        sa.Column("monitor_id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("strategy_type", sa.String(50), nullable=False, server_default="CUSTOM"),
        sa.Column("underlying", sa.String(50), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False, server_default="NFO"),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_monitored_positions_status", "monitored_positions", ["status"])

    op.create_table(
        "monitored_legs",
        sa.Column("leg_id", sa.String(36), primary_key=True),
        sa.Column("monitor_id", sa.String(36), sa.ForeignKey("monitored_positions.monitor_id", ondelete="CASCADE"), nullable=False),
        sa.Column("leg_number", sa.Integer, nullable=False, server_default="1"),
        sa.Column("instrument", sa.String(50), nullable=False),
        sa.Column("underlying", sa.String(50), nullable=False),
        sa.Column("strike", sa.Float, nullable=True),
        sa.Column("option_type", sa.String(10), nullable=False),
        sa.Column("expiry", sa.String(20), nullable=False),
        sa.Column("side", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
        sa.Column("lot_size", sa.Integer, nullable=False, server_default="1"),
        sa.Column("entry_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("current_price", sa.Float, nullable=False, server_default="0"),
        sa.Column("pnl", sa.Float, nullable=False, server_default="0"),
        sa.Column("premium_change_pct", sa.Float, nullable=False, server_default="0"),
    )
    op.create_index("ix_monitored_legs_monitor_id", "monitored_legs", ["monitor_id"])

    op.create_table(
        "alert_rules",
        sa.Column("alert_id", sa.String(36), primary_key=True),
        sa.Column("position_id", sa.String(36), nullable=False),
        sa.Column("position_type", sa.String(30), nullable=False, server_default="MONITORED"),
        sa.Column("strategy_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("underlying", sa.String(50), nullable=False, server_default=""),
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
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_alert_rules_position_id", "alert_rules", ["position_id"])

    op.create_table(
        "alert_histories",
        sa.Column("history_id", sa.String(36), primary_key=True),
        sa.Column("alert_id", sa.String(36), nullable=False),
        sa.Column("position_id", sa.String(36), nullable=False),
        sa.Column("alert_name", sa.String(200), nullable=False),
        sa.Column("strategy_name", sa.String(200), nullable=False, server_default=""),
        sa.Column("underlying", sa.String(50), nullable=False, server_default=""),
        sa.Column("fired_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("condition_summary", sa.Text, nullable=False, server_default=""),
        sa.Column("context_snapshot", sa.JSON, nullable=False),
        sa.Column("notifications_sent", sa.JSON, nullable=False),
    )
    op.create_index("ix_alert_histories_alert_id", "alert_histories", ["alert_id"])
    op.create_index("ix_alert_histories_position_id", "alert_histories", ["position_id"])


def downgrade() -> None:
    op.drop_table("alert_histories")
    op.drop_table("alert_rules")
    op.drop_table("monitored_legs")
    op.drop_table("monitored_positions")
