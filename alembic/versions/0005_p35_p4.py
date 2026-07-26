"""P3.5/P4:告警表 + 请求日志降级标记

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("severity", sa.String(12), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("detail", sa.Text(), nullable=False),
        sa.Column("dedupe_key", sa.String(128), nullable=False),
        sa.Column("acknowledged", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_alerts_kind", "alerts", ["kind"])
    op.create_index("ix_alerts_dedupe_key", "alerts", ["dedupe_key"], unique=True)
    op.create_index("ix_alerts_created_at", "alerts", ["created_at"])
    op.add_column("request_logs", sa.Column("downgraded_to", sa.String(128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("request_logs") as batch:
        batch.drop_column("downgraded_to")
    op.drop_table("alerts")
