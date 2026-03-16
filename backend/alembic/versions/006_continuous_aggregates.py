"""Create TimescaleDB continuous aggregate — candles_1h.

Revision ID: 006
Revises: 005
Create Date: 2026-03-14

candles_1h is a real-time materialized view that rolls up 5m candles into 1-hour bars.
It refreshes automatically via a background policy (every 1 hour, lag 2 hours).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "006"
down_revision: Union[str, None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgresql():
        return

    # Continuous aggregate: 1-hour OHLCV from 5-minute candles
    op.execute(
        """
        CREATE MATERIALIZED VIEW IF NOT EXISTS candles_1h
        WITH (timescaledb.continuous) AS
        SELECT
            time_bucket('1 hour', ts) AS ts,
            symbol,
            '1h'                      AS timeframe,
            first(open,  ts)          AS open,
            max(high)                 AS high,
            min(low)                  AS low,
            last(close,  ts)          AS close,
            sum(volume)               AS volume
        FROM candles
        WHERE timeframe = '5m'
        GROUP BY time_bucket('1 hour', ts), symbol
        WITH NO DATA
        """
    )

    # Auto-refresh policy: run every hour, covering data up to 2 hours ago
    op.execute(
        """
        SELECT add_continuous_aggregate_policy(
            'candles_1h',
            start_offset => INTERVAL '3 hours',
            end_offset   => INTERVAL '1 hour',
            schedule_interval => INTERVAL '1 hour',
            if_not_exists => TRUE
        )
        """
    )


def downgrade() -> None:
    if not _is_postgresql():
        return
    op.execute("SELECT remove_continuous_aggregate_policy('candles_1h', if_exists => TRUE)")
    op.execute("DROP MATERIALIZED VIEW IF EXISTS candles_1h")
