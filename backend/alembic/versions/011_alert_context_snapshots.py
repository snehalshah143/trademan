"""011 — alert_context_snapshots: persist alert evaluation context across restarts

Stores the last-known EvaluationContext per strategy so that CROSS_ABOVE /
CROSS_BELOW conditions can fire immediately on the first tick after a backend
restart instead of waiting for a natural price oscillation.

Revision ID: 011
Revises: 010
Create Date: 2026-11-19
"""
from alembic import op
import sqlalchemy as sa

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alert_context_snapshots",
        sa.Column("strategy_id", sa.String(36), primary_key=True),
        sa.Column("ctx_json",    sa.Text,        nullable=False),
        sa.Column("saved_at",    sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("alert_context_snapshots")
