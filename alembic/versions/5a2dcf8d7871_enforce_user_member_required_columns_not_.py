"""enforce user/member required columns not null

Revision ID: 5a2dcf8d7871
Revises: 9f7752de540d
Create Date: 2026-10-15

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "5a2dcf8d7871"
down_revision: str | None = "9f7752de540d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 表名 -> 昵称列名（user 用 name，member 用 nickname）
_TABLES = (("user", "name"), ("member", "nickname"))


def upgrade() -> None:
    # 存量 NULL 兜底：MySQL 严格模式下直接改 NOT NULL，遇到历史 NULL 行会整条失败。
    # email 带唯一约束，故用 id 拼占位值，避免多行都置空后撞唯一键。
    for table, name_col in _TABLES:
        op.execute(f"UPDATE `{table}` SET `{name_col}` = '' WHERE `{name_col}` IS NULL")
        op.execute(f"UPDATE `{table}` SET `password` = '' WHERE `password` IS NULL")
        op.execute(
            f"UPDATE `{table}` SET `email` = CONCAT('legacy-', `id`, '@invalid.local') "
            f"WHERE `email` IS NULL"
        )

    for table, name_col in _TABLES:
        op.alter_column(table, name_col, existing_type=sa.String(length=32), nullable=False)
        op.alter_column(table, "email", existing_type=sa.String(length=255), nullable=False)
        op.alter_column(table, "password", existing_type=sa.String(length=255), nullable=False)


def downgrade() -> None:
    for table, name_col in _TABLES:
        op.alter_column(table, "password", existing_type=sa.String(length=255), nullable=True)
        op.alter_column(table, "email", existing_type=sa.String(length=255), nullable=True)
        op.alter_column(table, name_col, existing_type=sa.String(length=32), nullable=True)
