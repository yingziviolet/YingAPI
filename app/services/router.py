"""P1 路由:静态优先级。选出所有支持该模型的启用渠道,按 priority 升序作为 failover 链。

P4 将在此扩展:难度感知降级、权重+健康度动态选择、控制台手动 pin。
"""
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel


async def candidate_channels(session: AsyncSession, model: str) -> list[Channel]:
    result = await session.execute(
        select(Channel).where(Channel.enabled == True).order_by(Channel.priority, Channel.id)  # noqa: E712
    )
    channels = result.scalars().all()
    return [c for c in channels if model in (c.models or [])]


def upstream_model_for(channel: Channel, model: str) -> str:
    """对外模型名 -> 该渠道的上游真实模型名。"""
    return (channel.model_map or {}).get(model, model)


async def all_public_models(session: AsyncSession) -> list[str]:
    result = await session.execute(select(Channel).where(Channel.enabled == True))  # noqa: E712
    names: set[str] = set()
    for c in result.scalars():
        names.update(c.models or [])
    return sorted(names)
