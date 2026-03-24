"""ensure credit ledger index"""

from __future__ import annotations

from alembic import op


revision = "20260324_0005"
down_revision = "20260324_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_credit_ledger_user_created_at ON credit_ledger (user_id, created_at)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_credit_ledger_user_created_at")
