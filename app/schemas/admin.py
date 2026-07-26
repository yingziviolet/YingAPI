"""控制面管理 API 的请求/响应模型。渠道 API key 永不回显。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ChannelCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    provider: str = "openai"
    base_url: str = Field(min_length=1, max_length=255)
    api_key: str = Field(min_length=1)
    models: list[str] = Field(default_factory=list)
    model_map: dict[str, str] = Field(default_factory=dict)
    prices: dict[str, dict[str, float]] = Field(default_factory=dict)
    priority: int = 100
    enabled: bool = True


class ChannelUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    provider: str | None = None
    base_url: str | None = None
    api_key: str | None = None  # 传入则轮换密钥
    models: list[str] | None = None
    model_map: dict[str, str] | None = None
    prices: dict[str, dict[str, float]] | None = None
    priority: int | None = None
    enabled: bool | None = None


class ChannelOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    provider: str
    base_url: str
    models: list[str]
    model_map: dict[str, str]
    prices: dict[str, dict[str, float]]
    priority: int
    enabled: bool
    created_at: datetime
    updated_at: datetime


class VirtualKeyCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    rpm_limit: int | None = Field(default=None, ge=1)


class VirtualKeyUpdate(BaseModel):
    enabled: bool | None = None
    monthly_budget_usd: float | None = Field(default=None, ge=0)
    rpm_limit: int | None = Field(default=None, ge=1)


class VirtualKeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    key_masked: str
    enabled: bool
    monthly_budget_usd: float | None
    rpm_limit: int | None
    created_at: datetime


class VirtualKeyCreated(VirtualKeyOut):
    key: str  # 原文仅创建时返回一次


class RequestLogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trace_id: str
    virtual_key_id: int | None
    channel_id: int | None
    model: str
    upstream_model: str | None
    stream: bool
    cache_hit: bool
    status: str
    status_code: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    usage_source: str
    cost_usd: float | None
    latency_ms: int | None
    first_token_ms: int | None
    error: str | None
    created_at: datetime
