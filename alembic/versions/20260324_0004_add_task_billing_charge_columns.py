"""add task billing charge columns"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260324_0004"
down_revision = "20260324_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("billing_charged_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "billing_charge_amount",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "billing_charge_amount")
    op.drop_column("tasks", "billing_charged_at")
