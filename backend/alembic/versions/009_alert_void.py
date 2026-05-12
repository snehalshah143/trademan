"""009 — alert void: add void + strategy_name to alert_rule_builders, FK to SET NULL

Revision ID: 009
Revises: 008
Create Date: 2026-05-11
"""
from alembic import op
import sqlalchemy as sa

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ── Add new columns ───────────────────────────────────────────────────────
    with op.batch_alter_table("alert_rule_builders", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("strategy_name", sa.String(200), nullable=True)
        )
        batch_op.add_column(
            sa.Column("void", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        try:
            # PostgreSQL: drop old CASCADE FK and recreate as SET NULL.
            # SQLite: this is a no-op (FK constraints not enforced).
            batch_op.drop_constraint("alert_rule_builders_strategy_id_fkey",
                                     type_="foreignkey")
        except Exception:
            pass
        batch_op.create_foreign_key(
            "fk_arb_strategy_set_null",
            "strategies",
            ["strategy_id"],
            ["id"],
            ondelete="SET NULL",
        )

    # Back-fill strategy_name for existing alert rules from strategies table.
    # Safe to skip on empty tables.
    try:
        op.execute("""
            UPDATE alert_rule_builders
            SET strategy_name = (
                SELECT name FROM strategies
                WHERE strategies.id = alert_rule_builders.strategy_id
            )
            WHERE strategy_id IS NOT NULL AND strategy_name IS NULL
        """)
    except Exception:
        pass


def downgrade() -> None:
    with op.batch_alter_table("alert_rule_builders", schema=None) as batch_op:
        batch_op.drop_column("void")
        batch_op.drop_column("strategy_name")
        batch_op.alter_column("strategy_id", existing_type=sa.String(36), nullable=False)
        try:
            batch_op.drop_constraint("fk_arb_strategy_set_null", type_="foreignkey")
        except Exception:
            pass
        batch_op.create_foreign_key(
            "alert_rule_builders_strategy_id_fkey",
            "strategies",
            ["strategy_id"],
            ["id"],
            ondelete="CASCADE",
        )
