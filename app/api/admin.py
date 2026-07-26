"""控制面管理 API:渠道 CRUD、虚拟 key 管理、用量统计聚合、请求日志。

鉴权:Bearer <GW_ADMIN_TOKEN>。后续 React 控制台直接对接这组接口。
"""
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

import httpx

from app.deps import get_session, require_admin
from app.models import Alert, Channel, RequestLog, VirtualKey, utcnow
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
        balance_url=payload.balance_url,
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
    # balance_url 可空(null = 清空回自动探测);其余列非空,显式 null 一律拒绝
    for field, value in data.items():
        if value is None and field != "balance_url":
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
        note=payload.note,
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
        note=vkey.note,
        rotated_count=vkey.rotated_count,
        created_at=vkey.created_at,
        key=raw,  # 原文仅此一次
    )


@router.post("/keys/{key_id}/rotate", response_model=VirtualKeyCreated)
async def rotate_key(key_id: int, session: AsyncSession = Depends(get_session)):
    """轮换 key:旧 key 立即失效,新 key 原文仅此一次返回。计量历史与预算保留。"""
    vkey = await session.get(VirtualKey, key_id)
    if vkey is None:
        raise HTTPException(status_code=404, detail="key not found")
    raw = generate_virtual_key()
    vkey.key_hash = hash_virtual_key(raw)
    vkey.key_masked = mask_key(raw)
    vkey.rotated_count += 1
    await session.commit()
    await session.refresh(vkey)
    return VirtualKeyCreated(
        id=vkey.id,
        name=vkey.name,
        key_masked=vkey.key_masked,
        enabled=vkey.enabled,
        monthly_budget_usd=vkey.monthly_budget_usd,
        rpm_limit=vkey.rpm_limit,
        note=vkey.note,
        rotated_count=vkey.rotated_count,
        created_at=vkey.created_at,
        key=raw,
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
        # 可空列(预算/限流/备注)允许 null=清空;其余显式 null 拒绝
        if value is None and field not in {"monthly_budget_usd", "rpm_limit", "note"}:
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


# ---------- 批量导入:API Key 与历史账单 ----------


@router.post("/import/keys/preview")
async def import_keys_preview(request: Request):
    """解析粘贴/上传的内容,识别厂商并预填配置。此步不落库、不联网。"""
    from app.services.importer import parse_keys

    body = await request.json()
    text = body.get("text") or ""
    items = parse_keys(text)
    return {"count": len(items), "items": items}


@router.post("/import/keys/verify")
async def import_keys_verify(request: Request):
    """对待导入项逐个做连通性 + 余额探测(用它自己的 key,只调公开接口)。"""
    import asyncio

    from app.models import Channel as ChannelModel
    from app.services.balance import fetch_balance
    from app.services.forwarder import _chat_url, _timeout

    body = await request.json()
    items = body.get("items") or []
    client = request.app.state.upstream_client
    fernet = request.app.state.fernet
    settings = request.app.state.settings

    async def probe(item: dict) -> dict:
        api_key = item.get("api_key") or ""
        base_url = (item.get("base_url") or "").rstrip("/")
        models = item.get("models") or []
        out = {"api_key_masked": item.get("api_key_masked"), "reachable": False}
        if not api_key or not base_url:
            out["error"] = "缺少 api_key 或 base_url"
            return out
        headers = {"Authorization": f"Bearer {api_key}"}
        model = models[0] if models else "gpt-4o-mini"
        try:
            resp = await client.post(
                f"{base_url}/chat/completions",
                json={"model": model, "messages": [{"role": "user", "content": "hi"}], "max_tokens": 1},
                headers=headers,
                timeout=_timeout(settings),
            )
            out["status_code"] = resp.status_code
            # 401/403 = key 无效;其余(含 400 模型名不对)都说明端点与鉴权是通的
            out["reachable"] = resp.status_code not in (401, 403)
            if resp.status_code in (401, 403):
                out["error"] = "鉴权失败,key 可能无效"
        except Exception as exc:  # 网络层问题
            out["error"] = f"连接失败: {exc!r}"
            return out

        # 余额:构造临时渠道对象复用探测逻辑(不落库)
        probe_channel = ChannelModel(
            id=0,
            name=item.get("name") or "probe",
            provider=item.get("provider") or "openai",
            base_url=base_url,
            api_key_encrypted=encrypt_api_key(fernet, api_key),
            models=models,
            model_map={},
            prices={},
            balance_url=item.get("balance_url"),
            priority=100,
            enabled=True,
        )
        try:
            result = await fetch_balance(client, probe_channel, fernet)
            if result.get("ok"):
                out["balance"] = result.get("balance")
        except Exception:
            pass
        return out

    results = await asyncio.gather(*(probe(i) for i in items), return_exceptions=True)
    return [
        r if not isinstance(r, BaseException) else {"reachable": False, "error": repr(r)}
        for r in results
    ]


@router.post("/import/keys")
async def import_keys(request: Request, session: AsyncSession = Depends(get_session)):
    """把确认后的待导入项落库为渠道。重名自动加后缀,单条失败不影响其余。"""
    body = await request.json()
    items = body.get("items") or []
    fernet = request.app.state.fernet
    created, skipped = [], []

    existing = {
        name for (name,) in (await session.execute(select(Channel.name))).all()
    }
    for item in items:
        api_key = (item.get("api_key") or "").strip()
        base_url = (item.get("base_url") or "").strip()
        if not api_key or not base_url:
            skipped.append({"name": item.get("name"), "reason": "缺少 api_key 或 base_url"})
            continue
        name = (item.get("name") or "channel").strip()
        base_name, suffix = name, 2
        while name in existing:
            name = f"{base_name}-{suffix}"
            suffix += 1
        models = item.get("models") or []
        prices = {k: v for k, v in (item.get("prices") or {}).items() if k in models}
        channel = Channel(
            name=name,
            provider=item.get("provider") or "openai",
            base_url=base_url,
            api_key_encrypted=encrypt_api_key(fernet, api_key),
            models=models,
            model_map={},
            prices=prices,
            balance_url=item.get("balance_url") or None,
            priority=int(item.get("priority") or 100),
            enabled=True,
        )
        session.add(channel)
        try:
            await session.commit()
            await session.refresh(channel)
            existing.add(name)
            created.append({"id": channel.id, "name": channel.name})
        except IntegrityError:
            await session.rollback()
            skipped.append({"name": name, "reason": "名称冲突"})
    return {"created": created, "skipped": skipped}


@router.post("/import/billing")
async def import_billing(request: Request, session: AsyncSession = Depends(get_session)):
    """导入厂商后台导出的历史账单,补齐切到网关之前的消费曲线。

    落成 status='imported' 的计量行,与网关自身转发记录区分开。
    """
    from app.services.importer import parse_billing

    body = await request.json()
    rows = parse_billing(body.get("text") or "")
    if not rows:
        return {"imported": 0, "reason": "没有解析出可用记录(需要包含日期与成本列)"}

    trace = f"import-{int(utcnow().timestamp())}"
    total_cost = 0.0
    for row in rows:
        session.add(
            RequestLog(
                trace_id=trace,
                virtual_key_id=None,
                channel_id=None,
                model=row["model"],
                upstream_model=None,
                stream=False,
                cache_hit=False,
                status="imported",
                status_code=200,
                prompt_tokens=row["prompt_tokens"],
                completion_tokens=row["completion_tokens"],
                total_tokens=row["prompt_tokens"] + row["completion_tokens"],
                usage_source="imported",
                cost_usd=row["cost_usd"],
                created_at=row["created_at"],
            )
        )
        total_cost += row["cost_usd"]
    await session.commit()
    return {
        "imported": len(rows),
        "total_cost_usd": round(total_cost, 6),
        "trace_id": trace,
        "note": "已并入大盘统计,status=imported 可与网关自身计量区分",
    }


@router.delete("/import/billing/{trace_id}")
async def delete_imported_billing(trace_id: str, session: AsyncSession = Depends(get_session)):
    """撤销一次账单导入(导错了可以回退)。"""
    from sqlalchemy import delete as sa_delete

    result = await session.execute(
        sa_delete(RequestLog).where(
            RequestLog.trace_id == trace_id, RequestLog.status == "imported"
        )
    )
    await session.commit()
    return {"deleted": result.rowcount or 0}


# ---------- 渠道余额(用渠道自己的 key 调公开余额接口) ----------


@router.get("/channels/{channel_id}/balance")
async def channel_balance(
    channel_id: int, request: Request, session: AsyncSession = Depends(get_session)
):
    from app.services.balance import fetch_balance

    channel = await session.get(Channel, channel_id)
    if channel is None:
        raise HTTPException(status_code=404, detail="channel not found")
    result = await fetch_balance(request.app.state.upstream_client, channel, request.app.state.fernet)
    return {"channel_id": channel.id, "channel_name": channel.name, **result}


@router.get("/balances")
async def all_balances(request: Request, session: AsyncSession = Depends(get_session)):
    """并发查所有启用渠道的余额;失败的渠道返回 ok=false,不影响其他渠道。"""
    import asyncio

    from app.services.balance import fetch_balance

    channels = (
        (await session.execute(select(Channel).where(Channel.enabled == True)))  # noqa: E712
        .scalars()
        .all()
    )
    client = request.app.state.upstream_client
    fernet = request.app.state.fernet
    results = await asyncio.gather(
        *(fetch_balance(client, ch, fernet) for ch in channels), return_exceptions=True
    )
    out = []
    for channel, result in zip(channels, results):
        if isinstance(result, BaseException):
            result = {"ok": False, "error": repr(result)}
        out.append({"channel_id": channel.id, "channel_name": channel.name, **result})
    return out


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
