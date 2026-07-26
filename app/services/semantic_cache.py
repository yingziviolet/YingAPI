"""语义缓存:请求文本 -> embedding -> 余弦相似度 >= 阈值命中。

P2 实现为应用层余弦匹配(候选集按模型隔离、TTL 过滤、上限 N 条),
SQLite/Postgres 通吃,自用流量规模下毫秒级;数据量上来后的 pgvector
索引化是 P4 的优化项(表结构已按可平滑迁移设计)。

embedding 来自指定渠道的 OpenAI 兼容 /v1/embeddings(自己的 key,自己计费)。
"""
import json
import logging
import math
from datetime import timedelta, timezone

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Channel, SemanticCacheEntry, utcnow
from app.security import decrypt_api_key

logger = logging.getLogger("gateway.semantic_cache")


def request_text(body: dict) -> str:
    """把 messages 规范化成一段用于 embedding 的文本。"""
    parts: list[str] = []
    for message in body.get("messages", []):
        if not isinstance(message, dict):
            continue
        role = message.get("role", "")
        content = message.get("content")
        if isinstance(content, str):
            parts.append(f"{role}: {content}")
        elif isinstance(content, list):  # 多模态消息只取文本段
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    parts.append(f"{role}: {item.get('text', '')}")
    return "\n".join(parts)


def cosine(a: list[float], b: list[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class SemanticCache:
    """依赖注入:client_getter 延迟取上游 HTTP 客户端(测试会整体替换 app.state 上的客户端)。"""

    def __init__(self, settings: Settings, client_getter, fernet: Fernet):
        self._settings = settings
        self._client_getter = client_getter
        self._fernet = fernet

    @property
    def _client(self) -> httpx.AsyncClient:
        return self._client_getter()

    def enabled(self) -> bool:
        return bool(self._settings.semantic_cache_enabled and self._settings.embedding_channel)

    async def _embedding_channel(self, session: AsyncSession) -> Channel | None:
        result = await session.execute(
            select(Channel).where(
                Channel.name == self._settings.embedding_channel, Channel.enabled == True  # noqa: E712
            )
        )
        return result.scalar_one_or_none()

    async def embed(self, session: AsyncSession, text: str) -> list[float] | None:
        """调 embedding 渠道;任何失败都静默降级(语义缓存是优化,不能影响主链路)。"""
        channel = await self._embedding_channel(session)
        if channel is None:
            return None
        try:
            headers = {
                "Authorization": f"Bearer {decrypt_api_key(self._fernet, channel.api_key_encrypted)}"
            }
        except InvalidToken:
            logger.error("embedding channel[%s] api key decryption failed", channel.name)
            return None
        try:
            resp = await self._client.post(
                channel.base_url.rstrip("/") + "/embeddings",
                json={"model": self._settings.embedding_model, "input": [text]},
                headers=headers,
                timeout=httpx.Timeout(10.0),
            )
            if resp.status_code != 200:
                logger.warning("embedding call failed: status %s", resp.status_code)
                return None
            data = resp.json()
            embedding = data["data"][0]["embedding"]
            if not isinstance(embedding, list) or not embedding:
                return None
            return [float(x) for x in embedding]
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            logger.warning("embedding call failed: %r", exc)
            return None

    async def lookup(
        self, session: AsyncSession, model: str, embedding: list[float]
    ) -> tuple[dict, float] | None:
        """在同模型、未过期的候选里找最相似条目。返回 (响应, 相似度) 或 None。"""
        now = utcnow()
        result = await session.execute(
            select(SemanticCacheEntry)
            .where(SemanticCacheEntry.model == model)
            .order_by(SemanticCacheEntry.id.desc())
            .limit(self._settings.semantic_max_candidates)
        )
        best: SemanticCacheEntry | None = None
        best_score = 0.0
        for entry in result.scalars():
            expires_at = entry.expires_at
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            if expires_at <= now:
                continue
            score = cosine(embedding, entry.embedding)
            if score > best_score:
                best, best_score = entry, score
        if best is not None and best_score >= self._settings.semantic_threshold:
            await session.execute(
                update(SemanticCacheEntry)
                .where(SemanticCacheEntry.id == best.id)
                .values(hit_count=SemanticCacheEntry.hit_count + 1)
            )
            await session.commit()
            return best.response_json, round(best_score, 4)
        return None

    async def store(
        self,
        session: AsyncSession,
        model: str,
        embedding: list[float],
        response_json: dict,
    ) -> None:
        session.add(
            SemanticCacheEntry(
                model=model,
                embedding=embedding,
                response_json=response_json,
                expires_at=utcnow() + timedelta(seconds=self._settings.cache_ttl_seconds),
            )
        )
        try:
            await session.commit()
        except Exception:  # 缓存写入失败不影响响应
            await session.rollback()
            logger.exception("semantic cache store failed")

    async def purge_expired(self, session: AsyncSession) -> int:
        """清理过期条目(哨兵任务/启动时调用)。"""
        result = await session.execute(
            delete(SemanticCacheEntry).where(SemanticCacheEntry.expires_at <= utcnow())
        )
        await session.commit()
        return result.rowcount or 0
