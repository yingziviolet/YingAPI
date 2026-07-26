"""控制面统计聚合:总量、命中率、按渠道/模型分组、按日序列、key 花费。"""
from datetime import timedelta

from sqlalchemy import Integer, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Channel, RequestLog, utcnow


def _window(days: int):
    return utcnow() - timedelta(days=days)


async def overview(session: AsyncSession, days: int = 7) -> dict:
    since = _window(days)
    base = select(
        func.count(RequestLog.id),
        func.coalesce(func.sum(RequestLog.prompt_tokens), 0),
        func.coalesce(func.sum(RequestLog.completion_tokens), 0),
        func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
        func.coalesce(func.sum(case((RequestLog.cache_hit == True, 1), else_=0)), 0),  # noqa: E712
        func.coalesce(func.sum(case((RequestLog.status == "error", 1), else_=0)), 0),
        func.coalesce(func.sum(case((RequestLog.downgraded_to != None, 1), else_=0)), 0),  # noqa: E711
    ).where(RequestLog.created_at >= since)
    total, pt, ct, cost, cache_hits, errors, downgraded = (await session.execute(base)).one()
    return {
        "window_days": days,
        "requests": total,
        "prompt_tokens": int(pt),
        "completion_tokens": int(ct),
        "cost_usd": round(float(cost), 6),
        "cache_hits": int(cache_hits),
        "cache_hit_rate": round(cache_hits / total, 4) if total else 0.0,
        "errors": int(errors),
        "error_rate": round(errors / total, 4) if total else 0.0,
        "downgraded": int(downgraded),
    }


async def by_channel(session: AsyncSession, days: int = 7) -> list[dict]:
    since = _window(days)
    stmt = (
        select(
            RequestLog.channel_id,
            Channel.name,
            RequestLog.cache_hit,
            func.count(RequestLog.id),
            func.coalesce(func.sum(RequestLog.total_tokens), 0),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
            func.coalesce(func.avg(RequestLog.latency_ms), 0.0),
        )
        .join(Channel, Channel.id == RequestLog.channel_id, isouter=True)
        .where(RequestLog.created_at >= since)
        .group_by(RequestLog.channel_id, Channel.name, RequestLog.cache_hit)
        .order_by(func.count(RequestLog.id).desc())
    )
    rows = (await session.execute(stmt)).all()
    # channel_id 为 NULL 的行有三种来源,不能都标成 "(cache)":
    # 缓存命中(cache_hit=True)、全渠道失败的错误请求、渠道被删后的历史行
    return [
        {
            "channel_id": cid,
            "channel_name": name
            or ("(cache)" if hit else ("(none)" if cid is None else str(cid))),
            "requests": count,
            "total_tokens": int(tokens),
            "cost_usd": round(float(cost), 6),
            "avg_latency_ms": round(float(latency), 1),
        }
        for cid, name, hit, count, tokens, cost, latency in rows
    ]


async def by_model(session: AsyncSession, days: int = 7) -> list[dict]:
    since = _window(days)
    stmt = (
        select(
            RequestLog.model,
            func.count(RequestLog.id),
            func.coalesce(func.sum(RequestLog.total_tokens), 0),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
        )
        .where(RequestLog.created_at >= since)
        .group_by(RequestLog.model)
        .order_by(func.count(RequestLog.id).desc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "model": model,
            "requests": count,
            "total_tokens": int(tokens),
            "cost_usd": round(float(cost), 6),
        }
        for model, count, tokens, cost in rows
    ]


async def daily_series(session: AsyncSession, days: int = 7) -> list[dict]:
    since = _window(days)
    day = func.date(RequestLog.created_at)
    stmt = (
        select(
            day,
            func.count(RequestLog.id),
            func.coalesce(func.sum(RequestLog.total_tokens), 0),
            func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
            func.coalesce(func.sum(func.cast(RequestLog.cache_hit, Integer)), 0),
        )
        .where(RequestLog.created_at >= since)
        .group_by(day)
        .order_by(day)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "date": str(d),
            "requests": count,
            "total_tokens": int(tokens),
            "cost_usd": round(float(cost), 6),
            "cache_hits": int(hits),
        }
        for d, count, tokens, cost, hits in rows
    ]


async def key_spend_month_to_date(session: AsyncSession, virtual_key_id: int) -> float:
    """该虚拟 key 本自然月已产生的成本(美元),用于预算校验。"""
    now = utcnow()
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    stmt = select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0)).where(
        RequestLog.virtual_key_id == virtual_key_id,
        RequestLog.created_at >= month_start,
    )
    return float((await session.execute(stmt)).scalar_one())
