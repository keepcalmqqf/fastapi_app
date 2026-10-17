"""add task_run table

异步任务执行历史：把 task_run 建出来，供控制台「执行历史」分页回溯。
只保留本次新增的建表语句——autogenerate 同时检出了 permission/role 的历史漂移
（deleted_at 索引、NOT NULL 等），与本次改动无关，不混进这条迁移。

Revision ID: d3267330e406
Revises: f3c1a9d2b4e6
Create Date: 2026-10-17 19:59:52.393913

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = 'd3267330e406'
down_revision: str | None = 'f3c1a9d2b4e6'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'task_run',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('job_id', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=64), nullable=False),
        sa.Column('backend', sa.String(length=16), nullable=False),
        sa.Column('state', sa.String(length=16), nullable=False),
        sa.Column('attempt', sa.Integer(), nullable=False),
        sa.Column('retries', sa.Integer(), nullable=False),
        sa.Column('enqueued_at', sa.DateTime(), nullable=False),
        sa.Column('started_at', sa.DateTime(), nullable=True),
        sa.Column('finished_at', sa.DateTime(), nullable=True),
        sa.Column('duration_ms', sa.Integer(), nullable=True),
        sa.Column('error', sa.String(length=512), nullable=True),
        sa.Column('result', sa.String(length=512), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.text('now()'), nullable=False),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_task_run_enqueued_at'), 'task_run', ['enqueued_at'], unique=False)
    op.create_index(op.f('ix_task_run_job_id'), 'task_run', ['job_id'], unique=True)
    op.create_index(op.f('ix_task_run_name'), 'task_run', ['name'], unique=False)
    op.create_index(op.f('ix_task_run_state'), 'task_run', ['state'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_task_run_state'), table_name='task_run')
    op.drop_index(op.f('ix_task_run_name'), table_name='task_run')
    op.drop_index(op.f('ix_task_run_job_id'), table_name='task_run')
    op.drop_index(op.f('ix_task_run_enqueued_at'), table_name='task_run')
    op.drop_table('task_run')
