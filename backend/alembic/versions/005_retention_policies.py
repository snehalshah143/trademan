"""Add TimescaleDB data-retention policies.

Revision ID: 005
Revises: 004
Create Date: 2026-03-14

  candles       — keep 2 years  (5m bars = ~180 k rows/symbol/year)
  mtm_snapshots — keep 90 days  (15-second rows, high volume)
  ltp_ticks     — keep 90 days  (raw ticks, very high volume)
"""
from typing import Sequence, Union

from alembic import op

revision: str = "005"
down_revision: Union[str, None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgresql():
        return

    op.execute(
        "SELECT add_retention_policy('candles',       INTERVAL '2 years', if_not_exists => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy('mtm_snapshots', INTERVAL '90 days', if_not_exists => TRUE)"
    )
    op.execute(
        "SELECT add_retention_policy('ltp_ticks',     INTERVAL '90 days', if_not_exists => TRUE)"
    )


def downgrade() -> None:
    if not _is_postgresql():
        return
    op.execute("SELECT remove_retention_policy('candles',       if_exists => TRUE)")
    op.execute("SELECT remove_retention_policy('mtm_snapshots', if_exists => TRUE)")
    op.execute("SELECT remove_retention_policy('ltp_ticks',     if_exists => TRUE)")
