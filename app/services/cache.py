"""精确匹配缓存:规范化请求体 -> SHA-256 -> 数据库缓存表。

P2 将升级为 pgvector 语义缓存;本模块的 key 规范化逻辑届时复用。
"""
import hashlib
import json
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import CacheEntry, utcnow

# 参与缓存 key 的字段:影响生成结果的都要进,其余(stream、user 等)不进
_KEY_FIELDS = (
    "model",
    "messages",
    "temperature",
    "top_p",
    "max_tokens",
    "max_completion_tokens",
    "n",
    "stop",
    "presence_penalty",
    "frequency_penalty",
    "seed",
    "tools",
    "tool_choice",
    "response_format",
    "logit_bias",
    "logprobs",
    "top_logprobs",
    "parallel_tool_calls",
    "reasoning_effort",
    "prediction",
    "modalities",
    "audio",
)


def cacheable(body: dict, settings: Settings) -> bool:
    """只缓存确定性意图的请求:temperature 缺省视为不确定,必须显式 <= 阈值。"""
    if not settings.cache_enabled:
        return False
    temperature = body.get("temperature")
    if temperature is None or not isinstance(temperature, (int, float)):
        return False
    if temperature > settings.cache_max_temperature:
        return False
    n = body.get("n")
    if n is not None and n != 1:
        return False
    return True


def make_cache_key(body: dict) -> str:
    normalized = {k: body[k] for k in _KEY_FIELDS if k in body and body[k] is not None}
    payload = json.dumps(normalized, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()


async def get_cached(session: AsyncSession, cache_key: str) -> dict | None:
    result = await session.execute(select(CacheEntry).where(CacheEntry.cache_key == cache_key))
    entry = result.scalar_one_or_none()
    if entry is None:
        return None
    expires_at = entry.expires_at
    if expires_at.tzinfo is None:  # SQLite 存取会丢 tzinfo,统一按 UTC 解释
        from datetime import timezone

        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at <= utcnow():
        await session.execute(delete(CacheEntry).where(CacheEntry.id == entry.id))
        await session.commit()
        return None
    await session.execute(
        update(CacheEntry).where(CacheEntry.id == entry.id).values(hit_count=CacheEntry.hit_count + 1)
    )
    await session.commit()
    return entry.response_json


async def put_cached(
    session: AsyncSession, cache_key: str, model: str, response_json: dict, settings: Settings
) -> None:
    expires_at = utcnow() + timedelta(seconds=settings.cache_ttl_seconds)
    existing = (
        await session.execute(select(CacheEntry).where(CacheEntry.cache_key == cache_key))
    ).scalar_one_or_none()
    if existing is not None:
        existing.response_json = response_json
        existing.expires_at = expires_at
    else:
        session.add(
            CacheEntry(
                cache_key=cache_key, model=model, response_json=response_json, expires_at=expires_at
            )
        )
    try:
        await session.commit()
    except IntegrityError:
        # 并发写同一 cache_key:赢家已写入等价响应,输家放弃即可,不能让请求 500
        await session.rollback()
