"""add email_verified to user and member

Revision ID: e08b78ca65d3
Revises: ef4816d0e0a4
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e08b78ca65d3"
down_revision: str | None = "ef4816d0e0a4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("email_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
    )
    op.add_column(
        "member",
        sa.Column("email_verified", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("member", "email_verified")
    op.drop_column("user", "email_verified")
