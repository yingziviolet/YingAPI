"""token 计量与成本记账:usage 解析、缺失时估算/回填、异步落库(不阻塞响应路径)。"""
import asyncio
import json
import logging
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Channel, RequestLog

logger = logging.getLogger("gateway.usage")


def extract_usage(payload: dict | None) -> tuple[int | None, int | None, int | None]:
    """从上游响应(或流式最后一个带 usage 的 chunk)提取 token 用量。"""
    if not payload:
        return None, None, None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None, None, None
    return (
        usage.get("prompt_tokens"),
        usage.get("completion_tokens"),
        usage.get("total_tokens"),
    )


def estimate_prompt_tokens(body: dict) -> int:
    """上游没给 usage 时的兜底估算:按字符数/4(记为 usage_source=estimated)。"""
    try:
        text = json.dumps(body.get("messages", []), ensure_ascii=False)
    except (TypeError, ValueError):
        return 0
    return max(1, len(text) // 4)


def estimate_completion_tokens(text: str) -> int:
    return max(1, len(text) // 4) if text else 0


def finalize_usage(
    pt: int | None,
    ct: int | None,
    tt: int | None,
    body: dict,
    completion_text: str,
) -> tuple[int, int, int, str]:
    """补全 usage:上游可能只给部分字段(如只有 prompt_tokens + total_tokens)。

    返回 (prompt, completion, total, usage_source):
    - 两边都有 → upstream
    - 两边都缺 → estimated(字符数/4)
    - 只缺一边 → mixed(优先用 total 反推,否则估算缺的那边)
    """
    if pt is not None and ct is not None:
        return pt, ct, tt if tt is not None else pt + ct, "upstream"
    if pt is None and ct is None:
        pt = estimate_prompt_tokens(body)
        ct = estimate_completion_tokens(completion_text)
        return pt, ct, pt + ct, "estimated"
    if pt is None:
        pt = max(tt - ct, 0) if tt is not None else estimate_prompt_tokens(body)
    else:
        ct = max(tt - pt, 0) if tt is not None else estimate_completion_tokens(completion_text)
    return pt, ct, tt if tt is not None else pt + ct, "mixed"


def compute_cost(
    channel: Channel | None,
    model: str,
    prompt_tokens: int | None,
    completion_tokens: int | None,
) -> float | None:
    """按渠道价格表计成本(美元)。价格单位:美元/1M tokens,键为对外模型名。

    未配价格返回 None——注意 None 不会计入预算,漏配价格要在渠道配置时就被 422 拦住
    (见 admin API 校验),这里的 warning 是最后一道观测手段。
    """
    if channel is None:
        return None
    price = (channel.prices or {}).get(model)
    if not isinstance(price, dict):
        if channel.prices:
            logger.warning(
                "channel %s has prices configured but no entry for model %r; cost_usd will be NULL",
                channel.name,
                model,
            )
        return None
    input_price = price.get("input", 0.0) or 0.0
    output_price = price.get("output", 0.0) or 0.0
    cost = ((prompt_tokens or 0) * input_price + (completion_tokens or 0) * output_price) / 1_000_000
    return round(cost, 8)


class Meter:
    """异步计量器:record() 立即返回,写库在后台任务完成;shutdown 时排干。"""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]):
        self._sessionmaker = sessionmaker
        self._tasks: set[asyncio.Task] = set()

    def record(self, **fields: Any) -> None:
        task = asyncio.create_task(self._write(fields))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _insert(self, fields: dict) -> None:
        async with self._sessionmaker() as session:
            session.add(RequestLog(**fields))
            await session.commit()

    async def _write(self, fields: dict) -> None:
        try:
            await self._insert(fields)
        except IntegrityError:
            # 渠道/key 在计量落库前被删除(异步落库的竞态):账不能丢,外键置空重试
            try:
                await self._insert({**fields, "channel_id": None, "virtual_key_id": None})
            except Exception:
                logger.exception(
                    "request metering retry failed (trace_id=%s)", fields.get("trace_id")
                )
        except Exception:  # 计量失败不能影响数据面,只记日志
            logger.exception("request metering write failed (trace_id=%s)", fields.get("trace_id"))

    async def drain(self) -> None:
        """等待所有在途计量任务完成(优雅停机/测试用)。"""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
