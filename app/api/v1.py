"""数据面:OpenAI 兼容入口 /v1/chat/completions(流式+非流式)与 /v1/models。"""
import json
import time
from time import perf_counter

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.deps import get_session, get_virtual_key
from app.errors import UpstreamError, openai_error
from app.models import VirtualKey
from app.services import cache as cache_svc
from app.services import downgrade as downgrade_svc
from app.services import forwarder, router as routing, stats as stats_svc, usage as usage_svc
from app.services import semantic_cache as semantic_svc
from app.trace import current_trace_id

router = APIRouter(prefix="/v1", tags=["data-plane"])


@router.get("/models")
async def list_models(
    request: Request,
    vkey: VirtualKey = Depends(get_virtual_key),
    session: AsyncSession = Depends(get_session),
):
    models = await routing.all_public_models(session)
    created = int(time.time())
    # 同一路径同时服务两种协议的客户端:OpenAI 客户端读 object/created/owned_by,
    # Anthropic 客户端(Claude Code 模型发现)读 type/display_name;多余字段互相忽略
    return {
        "object": "list",
        "has_more": False,
        "data": [
            {
                "id": m,
                "object": "model",
                "type": "model",
                "display_name": m,
                "created": created,
                "owned_by": "gateway",
            }
            for m in models
        ],
    }


def _cache_stream_chunks(response_json: dict, include_usage: bool) -> list[bytes]:
    """把缓存的完整响应合成为 SSE chunk 序列(缓存命中 + 客户端要流式时)。

    必须忠实回放 message 的全部生成内容:content、tool_calls、refusal;
    include_usage 时在 [DONE] 前补 usage chunk(与真实上游流行为一致)。
    """
    chunks: list[bytes] = []
    completion_id = response_json.get("id", "chatcmpl-cache")
    model = response_json.get("model", "")
    created = response_json.get("created", int(time.time()))

    def chunk(index: int, delta: dict, finish_reason: str | None) -> bytes:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [{"index": index, "delta": delta, "finish_reason": finish_reason}],
        }
        return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()

    for choice in response_json.get("choices", []):
        index = choice.get("index", 0)
        message = choice.get("message") or {}
        delta_events: list[dict] = [{"role": message.get("role", "assistant"), "content": ""}]
        content = message.get("content")
        if content:
            delta_events.append({"content": content})
        refusal = message.get("refusal")
        if refusal:
            delta_events.append({"refusal": refusal})
        tool_calls = message.get("tool_calls")
        if tool_calls:
            delta_events.append(
                {
                    "tool_calls": [
                        {
                            "index": i,
                            "id": tc.get("id"),
                            "type": tc.get("type", "function"),
                            "function": tc.get("function", {}),
                        }
                        for i, tc in enumerate(tool_calls)
                    ]
                }
            )
        for delta in delta_events:
            chunks.append(chunk(index, delta, None))
        chunks.append(chunk(index, {}, choice.get("finish_reason", "stop")))

    usage = response_json.get("usage")
    if include_usage and isinstance(usage, dict) and usage:
        payload = {
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model,
            "choices": [],
            "usage": usage,
        }
        chunks.append(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode())
    chunks.append(b"data: [DONE]\n\n")
    return chunks


@router.post("/chat/completions")
async def chat_completions(
    request: Request,
    vkey: VirtualKey = Depends(get_virtual_key),
    session: AsyncSession = Depends(get_session),
):
    app = request.app
    settings = app.state.settings
    meter = app.state.meter
    trace_id = current_trace_id()
    started_at = perf_counter()

    try:
        body = await request.json()
    except ValueError:
        return openai_error(400, "request body must be valid JSON")
    if not isinstance(body, dict):
        return openai_error(400, "request body must be a JSON object")
    model = body.get("model")
    messages = body.get("messages")
    if not isinstance(model, str) or not model:
        return openai_error(400, "'model' is required and must be a string")
    if not isinstance(messages, list) or not messages:
        return openai_error(400, "'messages' is required and must be a non-empty list")
    stream = bool(body.get("stream"))
    stream_options = body.get("stream_options")
    if stream_options is not None and not isinstance(stream_options, dict):
        return openai_error(400, "'stream_options' must be an object")

    metrics = app.state.metrics

    # 滑动窗口限流(每 key 每分钟请求数;key 未配置用全局默认)
    rpm_limit = vkey.rpm_limit if vkey.rpm_limit is not None else settings.default_rpm_limit
    decision = await app.state.rate_limiter.check(vkey.id, rpm_limit)
    if not decision.allowed:
        metrics.ratelimit_rejections_total.labels(key_name=vkey.name, reason="rpm").inc()
        return openai_error(
            429,
            f"rate limit exceeded for key '{vkey.name}' ({decision.limit} requests/min)",
            err_type="rate_limit_error",
            code="rate_limit_exceeded",
        )

    # 预算校验:月度花费 >= 预算即拒绝。软预算语义:计量异步落库,非流式滞后为秒级,
    # 流式请求要到流结束才计量(最长 upstream_timeout_read),并发长流可短暂超支
    if vkey.monthly_budget_usd is not None:
        spent = await stats_svc.key_spend_month_to_date(session, vkey.id)
        if spent >= vkey.monthly_budget_usd:
            metrics.ratelimit_rejections_total.labels(key_name=vkey.name, reason="budget").inc()
            return openai_error(
                429,
                f"monthly budget exhausted for key '{vkey.name}' "
                f"(spent ${spent:.4f} of ${vkey.monthly_budget_usd:.4f})",
                err_type="insufficient_quota",
                code="budget_exhausted",
            )

    # ---- 难度感知路由(P4):简单请求确定性降级到便宜模型 ----
    routed_model = model
    downgraded_to = downgrade_svc.downgrade_target(body, settings)
    if downgraded_to:
        routed_model = downgraded_to

    def meter_common(**extra):
        meter.record(
            trace_id=trace_id,
            virtual_key_id=vkey.id,
            model=model,
            stream=stream,
            **extra,
        )

    # ---- 缓存:精确匹配优先,未中再查语义缓存(embedding 调用有成本) ----
    use_cache = cache_svc.cacheable(body, settings)
    cache_key = cache_svc.make_cache_key(body) if use_cache else None
    semantic = app.state.semantic_cache
    request_embedding: list[float] | None = None

    def _serve_cached(
        cached: dict, cache_label: str, usage_source: str, extra_headers: dict | None = None
    ):
        pt, ct, tt = usage_svc.extract_usage(cached)
        duration_s = perf_counter() - started_at
        meter_common(
            channel_id=None,
            upstream_model=None,
            cache_hit=True,
            status="cache_hit",
            status_code=200,
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            usage_source=usage_source,
            cost_usd=0.0,
            latency_ms=int(duration_s * 1000),
        )
        metrics.record_request(
            model=model,
            channel="(cache)",
            status="cache_hit",
            cache=cache_label,
            stream=stream,
            duration_s=duration_s,
            prompt_tokens=pt,
            completion_tokens=ct,
        )
        headers = {"X-Gateway-Cache": cache_label, "X-Trace-Id": trace_id, **(extra_headers or {})}
        if stream:
            client_wants_usage = bool((stream_options or {}).get("include_usage"))
            return StreamingResponse(
                iter(_cache_stream_chunks(cached, include_usage=client_wants_usage)),
                media_type="text/event-stream",
                headers=headers,
            )
        return JSONResponse(cached, headers=headers)

    if cache_key:
        cached = await cache_svc.get_cached(session, cache_key)
        if cached is not None:
            metrics.cache_events_total.labels(kind="exact", outcome="hit").inc()
            return _serve_cached(cached, "hit", "cache")
        metrics.cache_events_total.labels(kind="exact", outcome="miss").inc()
        # 语义缓存:精确未中才走(先 embed 再余弦匹配;embedding 失败静默降级)
        if semantic.enabled():
            text = semantic_svc.request_text(body)
            if text:
                request_embedding = await semantic.embed(session, text)
            if request_embedding is not None:
                params_hash = cache_svc.make_params_hash(body)
                found = await semantic.lookup(session, model, params_hash, request_embedding)
                if found is not None:
                    response_json, score = found
                    metrics.cache_events_total.labels(kind="semantic", outcome="hit").inc()
                    return _serve_cached(
                        response_json,
                        "semantic-hit",
                        "semantic-cache",
                        {"X-Gateway-Similarity": str(score)},
                    )
                metrics.cache_events_total.labels(kind="semantic", outcome="miss").inc()

    # ---- 路由:静态优先级 failover 链(降级后按目标模型选渠道) ----
    channels = await routing.candidate_channels(session, routed_model)
    if not channels:
        return openai_error(
            404,
            f"model '{routed_model}' is not served by any enabled channel",
            code="model_not_found",
        )
    fernet = app.state.fernet
    client = app.state.upstream_client

    common_headers = {"X-Gateway-Cache": "miss" if use_cache else "skip", "X-Trace-Id": trace_id}
    if downgraded_to:
        common_headers["X-Gateway-Downgraded"] = downgraded_to

    # ---- 非流式 ----
    if not stream:
        try:
            data, channel = await forwarder.forward_non_stream(
                client, channels, routed_model, body, settings, fernet,
                breaker=app.state.breaker, metrics=metrics,
            )
        except UpstreamError as exc:
            duration_s = perf_counter() - started_at
            meter_common(
                channel_id=None,
                upstream_model=None,
                status="error",
                status_code=exc.status_code,
                usage_source="none",
                latency_ms=int(duration_s * 1000),
                error=exc.message,
            )
            metrics.record_request(
                model=model, channel="-", status="error",
                cache="miss" if use_cache else "skip", stream=False, duration_s=duration_s,
            )
            if exc.body and isinstance(exc.body.get("error"), dict):
                return JSONResponse(exc.body, status_code=exc.status_code)
            return openai_error(exc.status_code, exc.message, err_type="upstream_error")

        upstream_model = routing.upstream_model_for(channel, routed_model)
        # 响应里的 model 回写为对外模型名:客户端(与缓存)不应看到上游真实模型名
        if isinstance(data, dict) and isinstance(data.get("model"), str):
            data["model"] = model
        pt, ct, tt = usage_svc.extract_usage(data)
        content = ""
        choices = data.get("choices") or []
        if choices:
            content = (choices[0].get("message") or {}).get("content") or ""
        pt, ct, tt, usage_source = usage_svc.finalize_usage(pt, ct, tt, body, content)
        cost = usage_svc.compute_cost(channel, routed_model, pt, ct)
        duration_s = perf_counter() - started_at
        meter_common(
            channel_id=channel.id,
            upstream_model=upstream_model,
            downgraded_to=downgraded_to,
            status="ok",
            status_code=200,
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            usage_source=usage_source,
            cost_usd=cost,
            latency_ms=int(duration_s * 1000),
        )
        metrics.record_request(
            model=model, channel=channel.name, status="ok",
            cache="miss" if use_cache else "skip", stream=False, duration_s=duration_s,
            prompt_tokens=pt, completion_tokens=ct, cost_usd=cost,
        )
        if cache_key:
            await cache_svc.put_cached(session, cache_key, model, data, settings)
        if request_embedding is not None:
            await semantic.store(
                session, model, cache_svc.make_params_hash(body), request_embedding, data
            )
        return JSONResponse(
            data, headers={**common_headers, "X-Gateway-Channel": channel.name}
        )

    # ---- 流式 ----
    try:
        resp, channel, client_wants_usage = await forwarder.open_stream(
            client, channels, routed_model, body, settings, fernet,
            breaker=app.state.breaker, metrics=metrics,
        )
    except UpstreamError as exc:
        duration_s = perf_counter() - started_at
        meter_common(
            channel_id=None,
            upstream_model=None,
            status="error",
            status_code=exc.status_code,
            usage_source="none",
            latency_ms=int(duration_s * 1000),
            error=exc.message,
        )
        metrics.record_request(
            model=model, channel="-", status="error",
            cache="miss" if use_cache else "skip", stream=True, duration_s=duration_s,
        )
        if exc.body and isinstance(exc.body.get("error"), dict):
            return JSONResponse(exc.body, status_code=exc.status_code)
        return openai_error(exc.status_code, exc.message, err_type="upstream_error")

    upstream_model = routing.upstream_model_for(channel, routed_model)
    stats = forwarder.StreamStats()

    def meter_stream(status: str) -> None:
        pt, ct, tt = usage_svc.extract_usage({"usage": stats.usage} if stats.usage else None)
        pt, ct, tt, usage_source = usage_svc.finalize_usage(
            pt, ct, tt, body, "x" * stats.completion_chars
        )
        cost = usage_svc.compute_cost(channel, routed_model, pt, ct)
        duration_s = perf_counter() - started_at
        meter_common(
            channel_id=channel.id,
            upstream_model=upstream_model,
            downgraded_to=downgraded_to,
            status=status,
            status_code=200,
            prompt_tokens=pt,
            completion_tokens=ct,
            total_tokens=tt,
            usage_source=usage_source,
            cost_usd=cost,
            latency_ms=int(duration_s * 1000),
            first_token_ms=stats.first_token_ms,
            error=stats.error,
        )
        metrics.record_request(
            model=model, channel=channel.name, status=status,
            cache="miss" if use_cache else "skip", stream=True, duration_s=duration_s,
            prompt_tokens=pt, completion_tokens=ct, cost_usd=cost,
            first_token_s=stats.first_token_ms / 1000 if stats.first_token_ms is not None else None,
        )
        # 流中途上游断掉:计入该渠道的熔断窗口(打开流时的成功已计,这里补记失败)
        if stats.error and stats.error.startswith("upstream stream error"):
            app.state.breaker.record_failure(channel.id)

    # 客户端在上游流就绪前就断了:立刻关闭上游、计量、返回(响应不会被读到)
    if await request.is_disconnected():
        await resp.aclose()
        stats.error = "client disconnected before stream start"
        meter_stream("cancelled")
        return openai_error(499, "client disconnected")

    stream_started = False

    async def stream_body():
        nonlocal stream_started
        stream_started = True
        metered = False
        try:
            async for chunk in forwarder.relay_sse(
                resp,
                stats,
                forward_usage_chunk=client_wants_usage,
                started_at=started_at,
                public_model=model,
            ):
                yield chunk
            meter_stream("error" if stats.error else "ok")
            metered = True
        finally:
            if not metered:  # 客户端断连(生成器被取消)或转发中抛错
                if not stats.error:
                    stats.error = "client disconnected"
                meter_stream("cancelled")

    async def cleanup_if_never_started():
        # 生成器从未被迭代(断连竞态):relay_sse 的 finally 不会执行,这里兜底
        if not stream_started:
            await resp.aclose()
            if not stats.error:
                stats.error = "stream never started (client disconnected)"
            meter_stream("cancelled")

    return StreamingResponse(
        stream_body(),
        media_type="text/event-stream",
        headers={**common_headers, "X-Gateway-Channel": channel.name},
        background=BackgroundTask(cleanup_if_never_started),
    )
