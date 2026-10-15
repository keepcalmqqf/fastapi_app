"""add audit log table

Revision ID: ef4816d0e0a4
Revises: 5a2dcf8d7871
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "ef4816d0e0a4"
down_revision: str | None = "5a2dcf8d7871"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "audit_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("actor_type", sa.String(length=16), nullable=False),
        sa.Column("actor_id", sa.Integer(), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("target_type", sa.String(length=32), nullable=False),
        sa.Column("target_id", sa.String(length=64), nullable=True),
        sa.Column("detail", sa.String(length=512), nullable=True),
        sa.Column("ip", sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_audit_log_actor_type", "audit_log", ["actor_type"], unique=False)
    op.create_index("ix_audit_log_actor_id", "audit_log", ["actor_id"], unique=False)
    op.create_index("ix_audit_log_action", "audit_log", ["action"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_audit_log_action", table_name="audit_log")
    op.drop_index("ix_audit_log_actor_id", table_name="audit_log")
    op.drop_index("ix_audit_log_actor_type", table_name="audit_log")
    op.drop_table("audit_log")
