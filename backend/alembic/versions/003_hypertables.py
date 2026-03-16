"""Convert time-series tables to TimescaleDB hypertables.

Revision ID: 003
Revises: 002
Create Date: 2026-03-14

NOTE: No-op on SQLite (dev).  On PostgreSQL these tables become hypertables
      with chunk intervals tuned for F&O data volumes.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "003"
down_revision: Union[str, None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgresql():
        return

    # candles — chunk by 1 day (active trading data)
    op.execute(
        """
        SELECT create_hypertable(
            'candles', 'ts',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists => TRUE
        )
        """
    )

    # mtm_snapshots — chunk by 1 day (15-second rows, ~2 k rows/strategy/day)
    op.execute(
        """
        SELECT create_hypertable(
            'mtm_snapshots', 'ts',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists => TRUE
        )
        """
    )

    # ltp_ticks — chunk by 1 hour (very high-frequency if enabled)
    op.execute(
        """
        SELECT create_hypertable(
            'ltp_ticks', 'ts',
            chunk_time_interval => INTERVAL '1 hour',
            if_not_exists => TRUE
        )
        """
    )


def downgrade() -> None:
    # Hypertable conversion is not reversible without a full table rebuild.
    pass
