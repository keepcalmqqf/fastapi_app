"""add audit_log created_at index

审计日志按时间范围复盘时，created_at 是主过滤维度，但该列来自 TimestampMixin
且不带索引。本表只增不删、是库里数据量最大的表，缺索引时一次
GET /api/v1/audit/logs?created_at_from=...&created_at_to=... 就是全表扫描加上
一次 COUNT 全表扫描，随数据增长线性劣化。

索引只在 audit_log 上单独建：TimestampMixin 被所有表共用，在混入里加会连带
给每张表都补一个用不上的索引。

Revision ID: 5ce9f374fcaf
Revises: d3267330e406
Create Date: 2026-10-18 22:30:00.000000

"""
from collections.abc import Sequence

from alembic import op

revision: str = '5ce9f374fcaf'
down_revision: str | None = 'd3267330e406'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        op.f('ix_audit_log_created_at'), 'audit_log', ['created_at'], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f('ix_audit_log_created_at'), table_name='audit_log')
