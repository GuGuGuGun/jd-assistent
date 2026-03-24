"""add task billing reservation columns"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260324_0003"
down_revision = "20260324_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column(
            "billing_status",
            sa.String(length=50),
            nullable=False,
            server_default="none",
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("billing_reserved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("billing_released_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column(
            "billing_reservation_amount",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )


def downgrade() -> None:
    op.drop_column("tasks", "billing_reservation_amount")
    op.drop_column("tasks", "billing_released_at")
    op.drop_column("tasks", "billing_reserved_at")
    op.drop_column("tasks", "billing_status")
