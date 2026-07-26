"""渠道余额查询端点

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-27
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("channels", sa.Column("balance_url", sa.String(255), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("channels") as batch:
        batch.drop_column("balance_url")
