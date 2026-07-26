"""每虚拟 key 的滑动窗口限流(请求数/分钟)。

两个实现,按 GW_REDIS_URL 自动选择:
- InMemoryRateLimiter:双桶近似滑动窗口(当前分钟 + 上一分钟加权),exe 单机模式零依赖
- RedisRateLimiter:同一算法落在 Redis 计数器上,支持多 worker 共享窗口;
  Redis 故障时 fail-open(限流是保护措施,不能反过来把数据面打挂)
"""
import logging
import time
from dataclasses import dataclass

from app.config import Settings

logger = logging.getLogger("gateway.ratelimit")


@dataclass
class RateDecision:
    allowed: bool
    limit: int | None
    # 观测用:当前窗口的近似请求数
    current: float = 0.0


def _weighted_count(prev: int, curr: int, now: float, window: float = 60.0) -> float:
    """近似滑动窗口:上一窗口按剩余占比加权 + 当前窗口计数。"""
    elapsed = now % window
    prev_weight = (window - elapsed) / window
    return prev * prev_weight + curr


class InMemoryRateLimiter:
    def __init__(self, clock=time.time):
        self._clock = clock
        # key_id -> (window_start_epoch_minute, prev_count, curr_count)
        self._buckets: dict[int, tuple[int, int, int]] = {}

    async def check(self, key_id: int, limit: int | None) -> RateDecision:
        if not limit or limit <= 0:
            return RateDecision(allowed=True, limit=limit)
        now = self._clock()
        minute = int(now // 60)
        window_minute, prev, curr = self._buckets.get(key_id, (minute, 0, 0))
        if minute == window_minute:
            pass
        elif minute == window_minute + 1:
            prev, curr = curr, 0
        else:  # 隔了不止一分钟,窗口全部过期
            prev, curr = 0, 0
        count = _weighted_count(prev, curr, now)
        if count + 1 > limit:
            self._buckets[key_id] = (minute, prev, curr)
            return RateDecision(allowed=False, limit=limit, current=round(count, 2))
        self._buckets[key_id] = (minute, prev, curr + 1)
        return RateDecision(allowed=True, limit=limit, current=round(count + 1, 2))

    async def aclose(self) -> None:
        pass


class RedisRateLimiter:
    """Redis 版:INCR 先行原子预占槽位(避免 check-then-act 竞态),超限 DECR 回滚。"""

    def __init__(self, redis_url: str, clock=time.time):
        import redis.asyncio as aioredis  # 可选依赖,配置了才 import
        from redis.exceptions import RedisError

        self._redis = aioredis.from_url(redis_url, decode_responses=True)
        self._clock = clock
        self._error_types = (RedisError, OSError)

    async def check(self, key_id: int, limit: int | None) -> RateDecision:
        if not limit or limit <= 0:
            return RateDecision(allowed=True, limit=limit)
        now = self._clock()
        minute = int(now // 60)
        curr_key = f"gw:rl:{key_id}:{minute}"
        prev_key = f"gw:rl:{key_id}:{minute - 1}"
        try:
            pipe = self._redis.pipeline()
            pipe.get(prev_key)
            pipe.incr(curr_key)  # 原子预占:并发请求各自拿到唯一计数
            pipe.expire(curr_key, 180)  # 保留到下下分钟做 prev 读数
            prev_raw, curr, _ = await pipe.execute()
            prev = int(prev_raw or 0)
            count = _weighted_count(prev, int(curr) - 1, now)  # -1 = 本请求之前的计数
            if count + 1 > limit:
                await self._redis.decr(curr_key)  # 被拒请求不占窗口
                return RateDecision(allowed=False, limit=limit, current=round(count, 2))
            return RateDecision(allowed=True, limit=limit, current=round(count + 1, 2))
        except self._error_types as exc:
            logger.warning("rate limit backend unavailable, failing open: %r", exc)
            return RateDecision(allowed=True, limit=limit)

    async def aclose(self) -> None:
        try:
            await self._redis.aclose()
        except self._error_types:
            pass


def build_rate_limiter(settings: Settings):
    if settings.redis_url:
        return RedisRateLimiter(settings.redis_url)
    return InMemoryRateLimiter()
