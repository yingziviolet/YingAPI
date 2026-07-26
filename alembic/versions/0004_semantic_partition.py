"""审查修复:语义缓存条目按生成参数指纹与 embedding 模型分区

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-26
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "semantic_cache_entries", sa.Column("params_hash", sa.String(64), nullable=True)
    )
    op.add_column(
        "semantic_cache_entries", sa.Column("embedding_model", sa.String(128), nullable=True)
    )
    op.create_index(
        "ix_semantic_cache_entries_params_hash", "semantic_cache_entries", ["params_hash"]
    )
    # 旧行两列均为 NULL:lookup 按等值过滤自然排除,随 TTL 自灭,无需回填


def downgrade() -> None:
    op.drop_index("ix_semantic_cache_entries_params_hash", table_name="semantic_cache_entries")
    with op.batch_alter_table("semantic_cache_entries") as batch:
        batch.drop_column("embedding_model")
        batch.drop_column("params_hash")
