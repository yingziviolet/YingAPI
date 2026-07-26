"""控制面管理 API:渠道 CRUD、虚拟 key 管理、用量统计聚合、请求日志。

鉴权:Bearer <GW_ADMIN_TOKEN>。后续 React 控制台直接对接这组接口。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

import httpx

from app.deps import get_session, require_admin
from app.models import Alert, Channel, RequestLog, VirtualKey
from app.schemas.admin import (
    AlertOut,
    ChannelCreate,
    ChannelOut,
    ChannelUpdate,
    RequestLogOut,
    VirtualKeyCreate,
    VirtualKeyCreated,
    VirtualKeyOut,
    VirtualKeyUpdate,
)
from app.security import encrypt_api_key, generate_virtual_key, hash_virtual_key, mask_key
from app.services import stats as stats_svc
from app.services.forwarder import _chat_url, _headers, _timeout

router = APIRouter(prefix="/admin", tags=["control-plane"], dependencies=[Depends(require_admin)])


def _validate_prices(models: list[str], prices: dict) -> None:
    """价格表键必须是对外模型名(models 列表里的名字),配错键会让成本静默记 NULL、预算失效。"""
    unknown = set(prices or {}) - set(models or [])
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=(
                f"prices contain models not in 'models' list: {sorted(unknown)}; "
                "price keys must use public model names (not upstream names from model_map)"
            ),
        )


# ---------- 渠道 CRUD ----------


@router.post("/channels", response_model=ChannelOut, status_code=201)
async def create_channel(
    payload: ChannelCreate, request: Request, session: AsyncSession = Depends(get_session)
):
    _validate_prices(payload.models, payload.prices)
    exists = (
        await session.execute(select(Channel).where(Channel.name == payload.name))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail=f"channel '{payload.name}' already exists")
    channel = Channel(
        name=payload.name,
        provider=payload.provider,
        base_url=payload.base_url,
        api_key_encrypted=encrypt_api_key(request.app.state.fernet, payload.api_key),
        models=payload.models,
        model_map=payload.model_map,
        prices=payload.prices,
        priority=payload.priority,
        enabled=payload.enabled,
    )
    session.add(channel)
    await session.commit()
    await session.refresh(channel)
    return channel


@router.get("/channels", response_model=list[ChannelOut])
async def list_channels(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(Channel).order_by(Channel.priority, Channel.id))
    return result.scalars().all()


@router.get("/channels/{channel_id}", response_model=ChannelOut)
async def get_channel(channel_id: int, session: AsyncSession = Depends(get_session)):
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="channel not found")
    return channel


@router.patch("/channels/{channel_id}", response_model=ChannelOut)
async def update_channel(
    channel_id: int,
    payload: ChannelUpdate,
    request: Request,
    session: AsyncSession = Depends(get_session),
):
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="channel not found")
    data = payload.model_dump(exclude_unset=True)
    api_key = data.pop("api_key", None)
    # Channel 没有可空列:显式传 null 一律拒绝(否则落库时 500)
    for field, value in data.items():
        if value is None:
            raise HTTPException(status_code=422, detail=f"field '{field}' cannot be null")
    # 校验合并后的最终值:只改 models 或只改 prices 也可能造成键失配
    final_models = data.get("models", channel.models)
    final_prices = data.get("prices", channel.prices)
    _validate_prices(final_models, final_prices)
    if api_key:
        channel.api_key_encrypted = encrypt_api_key(request.app.state.fernet, api_key)
    for field, value in data.items():
        setattr(channel, field, value)
    await session.commit()
    await session.refresh(channel)
    return channel


@router.delete("/channels/{channel_id}", status_code=204)
async def delete_channel(channel_id: int, session: AsyncSession = Depends(get_session)):
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="channel not found")
    await session.delete(channel)
    await session.commit()


@router.post("/channels/{channel_id}/test")
async def test_channel(
    channel_id: int, request: Request, session: AsyncSession = Depends(get_session)
):
    """连通性测试:向该渠道发一条最小请求,返回状态与延迟(D7 排障用)。"""
    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="channel not found")
    model = (channel.models or ["gpt-4o-mini"])[0]
    upstream_model = (channel.model_map or {}).get(model, model)
    body = {
        "model": upstream_model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    client: httpx.AsyncClient = request.app.state.upstream_client
    settings = request.app.state.settings
    from time import perf_counter

    start = perf_counter()
    try:
        resp = await client.post(
            _chat_url(channel),
            json=body,
            headers=_headers(channel, request.app.state.fernet),
            timeout=_timeout(settings),
        )
        return {
            "ok": resp.status_code < 400,
            "status_code": resp.status_code,
            "latency_ms": int((perf_counter() - start) * 1000),
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "status_code": None,
            "latency_ms": int((perf_counter() - start) * 1000),
            "error": repr(exc),
        }


# ---------- 虚拟 key ----------


@router.post("/keys", response_model=VirtualKeyCreated, status_code=201)
async def create_key(payload: VirtualKeyCreate, session: AsyncSession = Depends(get_session)):
    exists = (
        await session.execute(select(VirtualKey).where(VirtualKey.name == payload.name))
    ).scalar_one_or_none()
    if exists:
        raise HTTPException(status_code=409, detail=f"key '{payload.name}' already exists")
    raw = generate_virtual_key()
    vkey = VirtualKey(
        name=payload.name,
        key_hash=hash_virtual_key(raw),
        key_masked=mask_key(raw),
        monthly_budget_usd=payload.monthly_budget_usd,
        rpm_limit=payload.rpm_limit,
    )
    session.add(vkey)
    await session.commit()
    await session.refresh(vkey)
    return VirtualKeyCreated(
        id=vkey.id,
        name=vkey.name,
        key_masked=vkey.key_masked,
        enabled=vkey.enabled,
        monthly_budget_usd=vkey.monthly_budget_usd,
        rpm_limit=vkey.rpm_limit,
        created_at=vkey.created_at,
        key=raw,  # 原文仅此一次
    )


@router.get("/keys", response_model=list[VirtualKeyOut])
async def list_keys(session: AsyncSession = Depends(get_session)):
    result = await session.execute(select(VirtualKey).order_by(VirtualKey.id))
    return result.scalars().all()


@router.patch("/keys/{key_id}", response_model=VirtualKeyOut)
async def update_key(
    key_id: int, payload: VirtualKeyUpdate, session: AsyncSession = Depends(get_session)
):
    vkey = await session.get(VirtualKey, key_id)
    if vkey is None:
        raise HTTPException(status_code=404, detail="key not found")
    for field, value in payload.model_dump(exclude_unset=True).items():
        # 可空列(预算/限流)允许 null=清空;其余显式 null 拒绝
        if value is None and field not in {"monthly_budget_usd", "rpm_limit"}:
            raise HTTPException(status_code=422, detail=f"field '{field}' cannot be null")
        setattr(vkey, field, value)
    await session.commit()
    await session.refresh(vkey)
    return vkey


@router.delete("/keys/{key_id}", status_code=204)
async def delete_key(key_id: int, session: AsyncSession = Depends(get_session)):
    vkey = await session.get(VirtualKey, key_id)
    if vkey is None:
        raise HTTPException(status_code=404, detail="key not found")
    await session.delete(vkey)
    await session.commit()


@router.get("/keys/{key_id}/spend")
async def key_spend(key_id: int, session: AsyncSession = Depends(get_session)):
    vkey = await session.get(VirtualKey, key_id)
    if vkey is None:
        raise HTTPException(status_code=404, detail="key not found")
    spent = await stats_svc.key_spend_month_to_date(session, key_id)
    return {
        "key_id": key_id,
        "name": vkey.name,
        "month_to_date_usd": round(spent, 6),
        "monthly_budget_usd": vkey.monthly_budget_usd,
    }


# ---------- 熔断器观测(P2:状态机全程可观察) ----------


@router.get("/breakers")
async def breaker_states(request: Request, session: AsyncSession = Depends(get_session)):
    snapshot = request.app.state.breaker.snapshot()
    rows = (await session.execute(select(Channel.id, Channel.name))).all()
    names = {cid: name for cid, name in rows}
    return [
        {"channel_id": cid, "channel_name": names.get(cid, str(cid)), **info}
        for cid, info in snapshot.items()
    ]


@router.post("/breakers/{channel_id}/reset")
async def reset_breaker(channel_id: int, request: Request):
    request.app.state.breaker.reset(channel_id)
    return {"channel_id": channel_id, "state": "closed"}


# ---------- 告警中心(P3.5) ----------


@router.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    limit: int = Query(default=100, ge=1, le=500),
    include_acked: bool = Query(default=False),
    session: AsyncSession = Depends(get_session),
):
    stmt = select(Alert).order_by(Alert.id.desc()).limit(limit)
    if not include_acked:
        stmt = stmt.where(Alert.acknowledged == False)  # noqa: E712
    return (await session.execute(stmt)).scalars().all()


@router.post("/alerts/{alert_id}/ack", response_model=AlertOut)
async def ack_alert(alert_id: int, session: AsyncSession = Depends(get_session)):
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=404, detail="alert not found")
    alert.acknowledged = True
    await session.commit()
    await session.refresh(alert)
    return alert


@router.post("/alerts/ack-all")
async def ack_all_alerts(session: AsyncSession = Depends(get_session)):
    from sqlalchemy import update as sa_update

    result = await session.execute(
        sa_update(Alert).where(Alert.acknowledged == False).values(acknowledged=True)  # noqa: E712
    )
    await session.commit()
    return {"acknowledged": result.rowcount or 0}


@router.post("/sentinel/run")
async def run_sentinel_now(request: Request):
    """手动触发一轮哨兵巡检(演示/排障用)。"""
    sentinel = getattr(request.app.state, "sentinel", None)
    if sentinel is None:
        raise HTTPException(status_code=400, detail="sentinel disabled")
    await sentinel.run_once()
    return {"ok": True}


# ---------- 订阅用量面板(P3.5,cockpit:只读本机 Claude Code 记录) ----------


@router.get("/subscription-usage")
async def subscription_usage(
    request: Request, days: int = Query(default=7, ge=1, le=90)
):
    import asyncio

    from app.services import subscription

    settings = request.app.state.settings
    if not settings.subscription_panel_enabled:
        return {"available": False, "reason": "GW_SUBSCRIPTION_PANEL_ENABLED=false"}
    # 文件扫描是同步 IO,丢线程池,不阻塞事件循环
    return await asyncio.to_thread(subscription.scan_usage, settings, days)


# ---------- 统计与日志 ----------


@router.get("/stats/overview")
async def stats_overview(
    days: int = Query(default=7, ge=1, le=90), session: AsyncSession = Depends(get_session)
):
    return await stats_svc.overview(session, days)


@router.get("/stats/channels")
async def stats_channels(
    days: int = Query(default=7, ge=1, le=90), session: AsyncSession = Depends(get_session)
):
    return await stats_svc.by_channel(session, days)


@router.get("/stats/models")
async def stats_models(
    days: int = Query(default=7, ge=1, le=90), session: AsyncSession = Depends(get_session)
):
    return await stats_svc.by_model(session, days)


@router.get("/stats/daily")
async def stats_daily(
    days: int = Query(default=7, ge=1, le=90), session: AsyncSession = Depends(get_session)
):
    return await stats_svc.daily_series(session, days)


@router.get("/stats/cache")
async def stats_cache(session: AsyncSession = Depends(get_session)):
    """缓存成绩单:精确/语义两层的有效条目数与累计命中(简历上最漂亮的数字来源)。"""
    from sqlalchemy import func

    from app.models import CacheEntry, SemanticCacheEntry, utcnow

    now = utcnow()
    exact_count, exact_hits = (
        await session.execute(
            select(
                func.count(CacheEntry.id), func.coalesce(func.sum(CacheEntry.hit_count), 0)
            ).where(CacheEntry.expires_at > now)
        )
    ).one()
    # 命中数按全表算(历史成绩),条目数只算未过期的
    exact_hits_total = (
        await session.execute(select(func.coalesce(func.sum(CacheEntry.hit_count), 0)))
    ).scalar_one()
    sem_count = (
        await session.execute(
            select(func.count(SemanticCacheEntry.id)).where(SemanticCacheEntry.expires_at > now)
        )
    ).scalar_one()
    sem_hits_total = (
        await session.execute(
            select(func.coalesce(func.sum(SemanticCacheEntry.hit_count), 0))
        )
    ).scalar_one()
    exact_hits = exact_hits_total
    sem_hits = sem_hits_total
    return {
        "exact": {"entries": exact_count, "total_hits": int(exact_hits)},
        "semantic": {"entries": sem_count, "total_hits": int(sem_hits)},
    }


@router.get("/logs", response_model=list[RequestLogOut])
async def list_logs(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(RequestLog).order_by(RequestLog.id.desc()).limit(limit).offset(offset)
    )
    return result.scalars().all()
