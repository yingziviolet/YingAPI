"""审查修复:trace_id 加宽到 64(与接受的外部 trace id 上限一致)+ 预算查询复合索引

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("request_logs") as batch:
        batch.alter_column(
            "trace_id", existing_type=sa.String(32), type_=sa.String(64), existing_nullable=False
        )
    op.create_index(
        "ix_request_logs_vkey_created", "request_logs", ["virtual_key_id", "created_at"]
    )


def downgrade() -> None:
    op.drop_index("ix_request_logs_vkey_created", table_name="request_logs")
    with op.batch_alter_table("request_logs") as batch:
        batch.alter_column(
            "trace_id", existing_type=sa.String(64), type_=sa.String(32), existing_nullable=False
        )
