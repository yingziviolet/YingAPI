"""P2:虚拟 key 限流字段 + 语义缓存表

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("virtual_keys", sa.Column("rpm_limit", sa.Integer(), nullable=True))
    op.create_table(
        "semantic_cache_entries",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("embedding", sa.JSON(), nullable=False),
        sa.Column("response_json", sa.JSON(), nullable=False),
        sa.Column("hit_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_semantic_cache_entries_model", "semantic_cache_entries", ["model"])
    op.create_index(
        "ix_semantic_cache_entries_expires_at", "semantic_cache_entries", ["expires_at"]
    )


def downgrade() -> None:
    op.drop_table("semantic_cache_entries")
    with op.batch_alter_table("virtual_keys") as batch:
        batch.drop_column("rpm_limit")
