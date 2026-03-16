"""Initial schema — all relational and time-series tables.

Revision ID: 001
Revises:
Create Date: 2026-03-14
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── strategies ────────────────────────────────────────────────────────────
    op.create_table(
        "strategies",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("description", sa.String(1000), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="draft"),
        sa.Column("underlying", sa.String(50), nullable=True),
        sa.Column("expiry", sa.String(20), nullable=True),
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

    # ── strategy_legs ─────────────────────────────────────────────────────────
    op.create_table(
        "strategy_legs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "strategy_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("strategies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(100), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False, server_default="1"),
        sa.Column("option_type", sa.String(10), nullable=True),
        sa.Column("strike", sa.Float, nullable=True),
        sa.Column("expiry", sa.String(20), nullable=True),
        sa.Column("entry_price", sa.Float, nullable=True),
        sa.Column("exit_price", sa.Float, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("broker_order_id", sa.String(100), nullable=True),
    )
    op.create_index("ix_strategy_legs_strategy_id", "strategy_legs", ["strategy_id"])

    # ── orders ────────────────────────────────────────────────────────────────
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "strategy_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("strategies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "leg_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("strategy_legs.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("broker_order_id", sa.String(100), nullable=True),
        sa.Column("symbol", sa.String(100), nullable=False),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("quantity", sa.Integer, nullable=False),
        sa.Column("order_type", sa.String(10), nullable=False, server_default="MARKET"),
        sa.Column("price", sa.Float, nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("filled_price", sa.Float, nullable=True),
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
    op.create_index("ix_orders_strategy_id", "orders", ["strategy_id"])

    # ── alert_events ──────────────────────────────────────────────────────────
    op.create_table(
        "alert_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "strategy_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("strategies.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("rule_id", sa.String(100), nullable=True),
        sa.Column("symbol", sa.String(100), nullable=True),
        sa.Column("message", sa.String(1000), nullable=False),
        sa.Column("severity", sa.String(20), nullable=False, server_default="info"),
        sa.Column(
            "triggered_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("dismissed", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_alert_events_strategy_id", "alert_events", ["strategy_id"])
    op.create_index("ix_alert_events_dismissed", "alert_events", ["dismissed"])

    # ── strategy_automation_configs ───────────────────────────────────────────
    op.create_table(
        "strategy_automation_configs",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True),
        sa.Column(
            "strategy_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("strategies.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("enabled", sa.Boolean, nullable=False, server_default="0"),
        sa.Column("alert_rules", sa.JSON, nullable=True),
        sa.Column("config", sa.JSON, nullable=True),
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

    # ── candles ───────────────────────────────────────────────────────────────
    op.create_table(
        "candles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("symbol", sa.String(100), nullable=False),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("open", sa.Float, nullable=False),
        sa.Column("high", sa.Float, nullable=False),
        sa.Column("low", sa.Float, nullable=False),
        sa.Column("close", sa.Float, nullable=False),
        sa.Column("volume", sa.Integer, nullable=False, server_default="0"),
    )
    op.create_index("ix_candles_ts", "candles", ["ts"])
    op.create_index("ix_candles_symbol", "candles", ["symbol"])
    op.create_index("ix_candles_symbol_timeframe_ts", "candles", ["symbol", "timeframe", "ts"])

    # ── mtm_snapshots ─────────────────────────────────────────────────────────
    op.create_table(
        "mtm_snapshots",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("strategy_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("mtm", sa.Float, nullable=False),
        sa.Column("realized_pnl", sa.Float, nullable=False, server_default="0"),
        sa.Column("unrealized_pnl", sa.Float, nullable=False, server_default="0"),
    )
    op.create_index("ix_mtm_snapshots_ts", "mtm_snapshots", ["ts"])
    op.create_index("ix_mtm_snapshots_strategy_id", "mtm_snapshots", ["strategy_id"])

    # ── ltp_ticks ─────────────────────────────────────────────────────────────
    op.create_table(
        "ltp_ticks",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("symbol", sa.String(100), nullable=False),
        sa.Column("ltp", sa.Float, nullable=False),
        sa.Column("change", sa.Float, nullable=True),
    )
    op.create_index("ix_ltp_ticks_ts", "ltp_ticks", ["ts"])
    op.create_index("ix_ltp_ticks_symbol", "ltp_ticks", ["symbol"])


def downgrade() -> None:
    op.drop_table("ltp_ticks")
    op.drop_table("mtm_snapshots")
    op.drop_table("candles")
    op.drop_table("strategy_automation_configs")
    op.drop_table("alert_events")
    op.drop_table("orders")
    op.drop_table("strategy_legs")
    op.drop_table("strategies")
