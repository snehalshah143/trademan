"""Add TimescaleDB compression policies.

Revision ID: 004
Revises: 003
Create Date: 2026-03-14

Compression reduces on-disk size ~10x for time-series data.
Policy: compress chunks older than the specified age.

  candles       — compress after 7 days
  mtm_snapshots — compress after 1 day
  ltp_ticks     — compress after 1 day
"""
from typing import Sequence, Union

from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgresql():
        return

    # Enable compression and add policies
    op.execute(
        """
        ALTER TABLE candles SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol, timeframe'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('candles', INTERVAL '7 days', if_not_exists => TRUE)"
    )

    op.execute(
        """
        ALTER TABLE mtm_snapshots SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'strategy_id'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('mtm_snapshots', INTERVAL '1 day', if_not_exists => TRUE)"
    )

    op.execute(
        """
        ALTER TABLE ltp_ticks SET (
            timescaledb.compress,
            timescaledb.compress_segmentby = 'symbol'
        )
        """
    )
    op.execute(
        "SELECT add_compression_policy('ltp_ticks', INTERVAL '1 day', if_not_exists => TRUE)"
    )


def downgrade() -> None:
    if not _is_postgresql():
        return
    op.execute("SELECT remove_compression_policy('candles',     if_exists => TRUE)")
    op.execute("SELECT remove_compression_policy('mtm_snapshots', if_exists => TRUE)")
    op.execute("SELECT remove_compression_policy('ltp_ticks',   if_exists => TRUE)")
