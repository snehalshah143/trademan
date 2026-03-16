"""Add instrument cache tables: cached_instruments, cached_expiries, instrument_sync_logs.

Revision ID: 007
Revises: 006
Create Date: 2026-03-14
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
        "cached_instruments",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(20), nullable=False, server_default="STOCK"),
        sa.Column("exchange", sa.String(10), nullable=False, server_default="NFO"),
        sa.Column("lot_size", sa.Integer, nullable=False, server_default="1"),
        sa.Column("strike_interval", sa.Float, nullable=True),
        sa.Column("has_options", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("has_futures", sa.Boolean, nullable=False, server_default="1"),
        sa.Column("source", sa.String(20), nullable=False, server_default="static"),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_cached_instruments_symbol", "cached_instruments", ["symbol"], unique=True)

    op.create_table(
        "cached_expiries",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(50), nullable=False),
        sa.Column("exchange", sa.String(10), nullable=False, server_default="NFO"),
        sa.Column("expiry", sa.String(20), nullable=False),
        sa.Column("expiry_type", sa.String(10), nullable=False, server_default="monthly"),
        sa.Column("expiry_order", sa.Integer, nullable=False, server_default="0"),
        sa.Column("synced_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
    )
    op.create_index("ix_cached_expiries_symbol", "cached_expiries", ["symbol"])

    op.create_table(
        "instrument_sync_logs",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("operation", sa.String(50), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("message", sa.String(500), nullable=True),
        sa.Column("records_synced", sa.Integer, nullable=False, server_default="0"),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=True),
    )


def downgrade() -> None:
    op.drop_table("instrument_sync_logs")
    op.drop_index("ix_cached_expiries_symbol", "cached_expiries")
    op.drop_table("cached_expiries")
    op.drop_index("ix_cached_instruments_symbol", "cached_instruments")
    op.drop_table("cached_instruments")
