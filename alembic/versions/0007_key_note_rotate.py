"""虚拟 key 备注与轮换计数

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("virtual_keys", sa.Column("note", sa.String(200), nullable=True))
    op.add_column(
        "virtual_keys",
        sa.Column("rotated_count", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    with op.batch_alter_table("virtual_keys") as batch:
        batch.drop_column("rotated_count")
        batch.drop_column("note")
