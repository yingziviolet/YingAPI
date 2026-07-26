"""P2.5 数据面:Anthropic Messages API 入口(/v1/messages)。

Claude Code 等 Anthropic 协议客户端设置 ANTHROPIC_BASE_URL=网关 + 自己的虚拟 key
即可接入;网关翻译成 OpenAI 协议后复用同一套路由/熔断/限流/计量/缓存。
性质红线:只服务配了自有 key 的客户端,不碰订阅 OAuth 流量。
"""
import json
import logging
from time import perf_counter

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.deps import get_session, get_virtual_key
from app.errors import UpstreamError
from app.models import VirtualKey
from app.services import cache as cache_svc
from app.services import forwarder, router as routing, stats as stats_svc, usage as usage_svc
from app.services import semantic_cache as semantic_svc
from app.services.anthropic_translate import (
    AnthropicStreamTranslator,
    TranslateError,
    anthropic_to_openai_request,
    openai_to_anthropic_response,
)
from app.trace import current_trace_id

logger = logging.getLogger("gateway.anthropic")

router = APIRouter(prefix="/v1", tags=["data-plane-anthropic"])


def anthropic_error(status_code: int, message: str, err_type: str = "invalid_request_error"):
    return JSONResponse(
        status_code=status_code,
        content={"type": "error", "error": {"type": err_type, "message": message}},
    )


def _upstream_anthropic_error(exc: UpstreamError):
    """透传上游错误原文(客户端要看到真实原因,不是笼统的 'upstream rejected')。"""
    message = exc.message
    if exc.body and isinstance(exc.body.get("error"), dict):
        upstream_msg = exc.body["error"].get("message")
        if isinstance(upstream_msg, str) and upstream_msg:
            message = upstream_msg
    err_type = "invalid_request_error" if 400 <= exc.status_code < 500 else "api_error"
    return anthropic_error(exc.status_code, message, err_type=err_type)


@router.post("/messages/count_tokens")
async def count_tokens(
    request: Request,
    vkey: VirtualKey = Depends(get_virtual_key),
):
    try:
        body = await request.json()
    except ValueError:
        return anthropic_error(400, "request body must be valid JSON")
    if not isinstance(body, dict):
        return anthropic_error(400, "request body must be a JSON object")
    try:
        openai_body = anthropic_to_openai_request({**body, "max_tokens": 1})
    except TranslateError as exc:
        return anthropic_error(400, str(exc))
    return {"input_tokens": usage_svc.estimate_prompt_tokens(openai_body)}


@router.post("/messages")
async def messages(
    request: Request,
    vkey: VirtualKey = Depends(get_virtual_key),
    session: AsyncSession = Depends(get_session),
):
    app = request.app
    settings = app.state.settings
    meter = app.state.meter
    metrics = app.state.metrics
    trace_id = current_trace_id()
    started_at = perf_counter()

    try:
        body = await request.json()
    except ValueError:
        return anthropic_error(400, "request body must be valid JSON")
    if not isinstance(body, dict):
        return anthropic_error(400, "request body must be a JSON object")
    try:
        openai_body = anthropic_to_openai_request(body)
    except TranslateError as exc:
        return anthropic_error(400, str(exc))

    model = openai_body["model"]
    stream = bool(openai_body.get("stream"))

    # 限流与预算:与 OpenAI 入口同一套语义
    rpm_limit = vkey.rpm_limit if vkey.rpm_limit is not None else settings.default_rpm_limit
    decision = await app.state.rate_limiter.check(vkey.id, rpm_limit)
    if not decision.allowed:
        metrics.ratelimit_rejections_total.labels(key_name=vkey.name, reason="rpm").inc()
        return anthropic_error(
            429,
            f"rate limit exceeded for key '{vkey.name}' ({decision.limit} requests/min)",
            err_type="rate_limit_error",
        )
    if vkey.monthly_budget_usd is not None:
        spent = await stats_svc.key_spend_month_to_date(session, vkey.id)
        if spent >= vkey.monthly_budget_usd:
            metrics.ratelimit_rejections_total.labels(key_name=vkey.name, reason="budget").inc()
            return anthropic_error(
                429,
                f"monthly budget exhausted for key '{vkey.name}' "
                f"(spent ${spent:.4f} of ${vkey.monthly_budget_usd:.4f})",
                err_type="rate_limit_error",
            )

    def meter_common(**extra):
        meter.record(
            trace_id=trace_id, virtual_key_id=vkey.id, model=model, stream=stream, **extra
        )

    # 缓存(翻译后的 OpenAI 请求体做 key,与 OpenAI 入口共享精确+语义两层);流式暂不查缓存
    use_cache = cache_svc.cacheable(openai_body, settings)
    cache_key = cache_svc.make_cache_key(openai_body) if use_cache and not stream else None
    semantic = app.state.semantic_cache
    request_embedding: list[float] | None = None

    def _serve_cached(cached: dict, cache_label: str, usage_source: str, extra_headers: dict | None = None):
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
            model=model, channel="(cache)", status="cache_hit", cache=cache_label,
            stream=False, duration_s=duration_s, prompt_tokens=pt, completion_tokens=ct,
        )
        return JSONResponse(
            openai_to_anthropic_response(cached, model),
            headers={"X-Gateway-Cache": cache_label, "X-Trace-Id": trace_id, **(extra_headers or {})},
        )

    if cache_key:
        cached = await cache_svc.get_cached(session, cache_key)
        if cached is not None:
            metrics.cache_events_total.labels(kind="exact", outcome="hit").inc()
            return _serve_cached(cached, "hit", "cache")
        metrics.cache_events_total.labels(kind="exact", outcome="miss").inc()
        if semantic.enabled():
            text = semantic_svc.request_text(openai_body)
            if text:
                request_embedding = await semantic.embed(session, text)
            if request_embedding is not None:
                params_hash = cache_svc.make_params_hash(openai_body)
                found = await semantic.lookup(session, model, params_hash, request_embedding)
                if found is not None:
                    response_json, score = found
                    metrics.cache_events_total.labels(kind="semantic", outcome="hit").inc()
                    return _serve_cached(
                        response_json, "semantic-hit", "semantic-cache",
                        {"X-Gateway-Similarity": str(score)},
                    )
                metrics.cache_events_total.labels(kind="semantic", outcome="miss").inc()

    channels = await routing.candidate_channels(session, model)
    if not channels:
        return anthropic_error(
            404, f"model '{model}' is not served by any enabled channel", err_type="not_found_error"
        )
    fernet = app.state.fernet
    client = app.state.upstream_client

    # ---- 非流式 ----
    if not stream:
        try:
            data, channel = await forwarder.forward_non_stream(
                client, channels, model, openai_body, settings, fernet, breaker=app.state.breaker, metrics=metrics
            )
        except UpstreamError as exc:
            duration_s = perf_counter() - started_at
            meter_common(
                channel_id=None, upstream_model=None, status="error",
                status_code=exc.status_code, usage_source="none",
                latency_ms=int(duration_s * 1000), error=exc.message,
            )
            metrics.record_request(
                model=model, channel="-", status="error",
                cache="miss" if use_cache else "skip", stream=False, duration_s=duration_s,
            )
            return _upstream_anthropic_error(exc)

        upstream_model = routing.upstream_model_for(channel, model)
        if isinstance(data, dict) and isinstance(data.get("model"), str):
            data["model"] = model
        pt, ct, tt = usage_svc.extract_usage(data)
        content = ""
        choices = data.get("choices") or []
        if choices:
            content = (choices[0].get("message") or {}).get("content") or ""
        pt, ct, tt, usage_source = usage_svc.finalize_usage(pt, ct, tt, openai_body, content)
        cost = usage_svc.compute_cost(channel, model, pt, ct)
        duration_s = perf_counter() - started_at
        meter_common(
            channel_id=channel.id, upstream_model=upstream_model, status="ok", status_code=200,
            prompt_tokens=pt, completion_tokens=ct, total_tokens=tt, usage_source=usage_source,
            cost_usd=cost, latency_ms=int(duration_s * 1000),
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
                session, model, cache_svc.make_params_hash(openai_body), request_embedding, data
            )
        return JSONResponse(
            openai_to_anthropic_response(data, model),
            headers={
                "X-Gateway-Cache": "miss" if use_cache else "skip",
                "X-Gateway-Channel": channel.name,
                "X-Trace-Id": trace_id,
            },
        )

    # ---- 流式:消费上游 OpenAI SSE,翻译成 Anthropic 事件序列 ----
    try:
        resp, channel, _ = await forwarder.open_stream(
            client, channels, model, openai_body, settings, fernet, breaker=app.state.breaker, metrics=metrics
        )
    except UpstreamError as exc:
        duration_s = perf_counter() - started_at
        meter_common(
            channel_id=None, upstream_model=None, status="error",
            status_code=exc.status_code, usage_source="none",
            latency_ms=int(duration_s * 1000), error=exc.message,
        )
        metrics.record_request(
            model=model, channel="-", status="error", cache="skip",
            stream=True, duration_s=duration_s,
        )
        return _upstream_anthropic_error(exc)

    upstream_model = routing.upstream_model_for(channel, model)
    translator = AnthropicStreamTranslator(
        model, input_tokens_estimate=usage_svc.estimate_prompt_tokens(openai_body)
    )
    stream_error: list[str] = []  # 闭包可写容器
    first_token_ms: list[int] = []

    def meter_stream(status: str) -> None:
        usage = translator.usage or {}
        pt = usage.get("prompt_tokens")
        ct = usage.get("completion_tokens")
        tt = usage.get("total_tokens")
        pt, ct, tt, usage_source = usage_svc.finalize_usage(
            pt, ct, tt, openai_body, "x" * translator.output_chars
        )
        cost = usage_svc.compute_cost(channel, model, pt, ct)
        duration_s = perf_counter() - started_at
        meter_common(
            channel_id=channel.id, upstream_model=upstream_model, status=status, status_code=200,
            prompt_tokens=pt, completion_tokens=ct, total_tokens=tt, usage_source=usage_source,
            cost_usd=cost, latency_ms=int(duration_s * 1000),
            first_token_ms=first_token_ms[0] if first_token_ms else None,
            error=stream_error[0] if stream_error else None,
        )
        metrics.record_request(
            model=model, channel=channel.name, status=status, cache="skip",
            stream=True, duration_s=duration_s, prompt_tokens=pt, completion_tokens=ct,
            cost_usd=cost,
            first_token_s=first_token_ms[0] / 1000 if first_token_ms else None,
        )
        if stream_error and stream_error[0].startswith("upstream stream error"):
            app.state.breaker.record_failure(channel.id)

    # 客户端在上游流就绪前就断了:立刻关闭上游、计量、返回
    if await request.is_disconnected():
        await resp.aclose()
        stream_error.append("client disconnected before stream start")
        meter_stream("cancelled")
        return anthropic_error(499, "client disconnected", err_type="api_error")

    stream_started = False

    async def stream_body():
        nonlocal stream_started
        stream_started = True
        import httpx

        metered = False
        try:
            # 立即发 message_start:不等上游首 chunk,客户端(与中间代理)第一时间有字节可读
            for event in translator.start():
                yield event
            async for line in forwarder._iter_sse_lines(resp):
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                if not isinstance(chunk, dict):
                    continue
                events = translator.feed(chunk)
                if events and not first_token_ms and (chunk.get("choices") or []):
                    first_token_ms.append(int((perf_counter() - started_at) * 1000))
                for event in events:
                    yield event
            for event in translator.finish():
                yield event
            meter_stream("ok")
            metered = True
        except httpx.HTTPError as exc:
            stream_error.append(f"upstream stream error: {exc!r}")
            yield translator.error_event("upstream stream interrupted")
            meter_stream("error")
            metered = True
        finally:
            await resp.aclose()
            if not metered:
                if not stream_error:
                    stream_error.append("client disconnected")
                meter_stream("cancelled")

    async def cleanup_if_never_started():
        # 生成器从未被迭代(断连竞态):stream_body 的 finally 不会执行,这里兜底
        if not stream_started:
            await resp.aclose()
            if not stream_error:
                stream_error.append("stream never started (client disconnected)")
            meter_stream("cancelled")

    return StreamingResponse(
        stream_body(),
        media_type="text/event-stream",
        headers={
            "X-Gateway-Channel": channel.name,
            "X-Gateway-Cache": "skip",
            "X-Trace-Id": trace_id,
        },
        background=BackgroundTask(cleanup_if_never_started),
    )
