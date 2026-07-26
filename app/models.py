"""SQLAlchemy 模型:渠道注册表、虚拟 key、请求日志(计量)、精确缓存。"""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Channel(Base):
    """上游渠道:一个 OpenAI 兼容端点 + 自己合法持有的 API key(加密存储)。"""

    __tablename__ = "channels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    provider: Mapped[str] = mapped_column(String(32), default="openai")
    base_url: Mapped[str] = mapped_column(String(255))  # 形如 https://api.openai.com/v1
    api_key_encrypted: Mapped[str] = mapped_column(Text)
    # 该渠道支持的对外模型名列表
    models: Mapped[list] = mapped_column(JSON, default=list)
    # 对外模型名 -> 上游真实模型名 的改写映射(可空)
    model_map: Mapped[dict] = mapped_column(JSON, default=dict)
    # 模型价格表:{model: {"input": 美元/1M tokens, "output": 美元/1M tokens}}
    prices: Mapped[dict] = mapped_column(JSON, default=dict)
    # 静态优先级,数字越小越优先(P1 路由策略)
    priority: Mapped[int] = mapped_column(Integer, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class VirtualKey(Base):
    """平台发放给客户端的虚拟 key:只存哈希,原文仅创建时返回一次。"""

    __tablename__ = "virtual_keys"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    key_masked: Mapped[str] = mapped_column(String(32))  # 展示用掩码
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # 月度预算(美元),空为不限
    monthly_budget_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 每分钟请求数上限,空则用全局 GW_DEFAULT_RPM_LIMIT
    rpm_limit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RequestLog(Base):
    """每次请求的计量与审计(只存元数据,不存消息内容——脱敏)。"""

    __tablename__ = "request_logs"
    # 预算校验按 (virtual_key_id, created_at) 聚合,给它一个复合索引
    __table_args__ = (Index("ix_request_logs_vkey_created", "virtual_key_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 64 与 trace.py 接受的外部 trace id 上限一致,过长会导致 Postgres 整行写入失败
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    virtual_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("virtual_keys.id", ondelete="SET NULL"), nullable=True
    )
    channel_id: Mapped[int | None] = mapped_column(
        ForeignKey("channels.id", ondelete="SET NULL"), nullable=True
    )
    model: Mapped[str] = mapped_column(String(128))  # 客户端请求的对外模型名
    upstream_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stream: Mapped[bool] = mapped_column(Boolean, default=False)
    cache_hit: Mapped[bool] = mapped_column(Boolean, default=False)
    # ok / error / cancelled / cache_hit
    status: Mapped[str] = mapped_column(String(16), index=True)
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # usage 来源:upstream(上游返回) / estimated(字符数估算) / none
    usage_source: Mapped[str] = mapped_column(String(16), default="none")
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_token_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class SemanticCacheEntry(Base):
    """语义缓存:请求文本 embedding -> 完整响应 JSON,余弦相似度匹配。

    P2 用 JSON 列存向量 + 应用层余弦(两种数据库通吃);
    P4 数据量上来后可平滑迁移到 pgvector 列 + 索引。
    """

    __tablename__ = "semantic_cache_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    model: Mapped[str] = mapped_column(String(128), index=True)
    # 生成参数指纹(tools/response_format/max_tokens 等):语义相同但参数不同的请求不能共享响应
    params_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # 产生该向量的 embedding 模型:换模型后新旧向量空间不能混算
    embedding_model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    embedding: Mapped[list] = mapped_column(JSON)
    response_json: Mapped[dict] = mapped_column(JSON)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class CacheEntry(Base):
    """精确匹配缓存:规范化请求体 SHA-256 -> 完整响应 JSON。"""

    __tablename__ = "cache_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    cache_key: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    model: Mapped[str] = mapped_column(String(128))
    response_json: Mapped[dict] = mapped_column(JSON)
    hit_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
