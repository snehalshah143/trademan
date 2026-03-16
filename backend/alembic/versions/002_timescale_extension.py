"""Enable TimescaleDB extension.

Revision ID: 002
Revises: 001
Create Date: 2026-03-14

NOTE: This migration is a no-op on SQLite (dev).
      On PostgreSQL it enables the TimescaleDB extension.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgresql() -> bool:
    bind = op.get_bind()
    return bind.dialect.name == "postgresql"


def upgrade() -> None:
    if _is_postgresql():
        op.execute("CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE")


def downgrade() -> None:
    # Extension removal is intentionally not automated — it would drop all hypertables.
    pass
