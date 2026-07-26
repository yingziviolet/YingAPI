"""上游转发核心:非流式转发 + SSE 流式透传,静态优先级 failover。

failover 语义:
- 传输错误 / 超时 / 401 403 408 429 5xx / 密钥解密失败 —— 视为渠道故障,换下一个渠道
- 其余 4xx(400/404/422 等)—— 请求本身的问题,直接透传给客户端,不 failover
  例外:网关注入的 stream_options 导致的 400,会对同一渠道去掉注入重试一次(自愈)
- 本地连接池耗尽(PoolTimeout)不是渠道故障:换渠道用的还是同一个池,立即 503
流式一旦开始向客户端吐字节即不可 failover(只能中断)。
"""
import json
import logging
from dataclasses import dataclass, field
from time import perf_counter
from typing import AsyncIterator

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings
from app.errors import UpstreamError
from app.models import Channel
from app.security import decrypt_api_key
from app.services.router import upstream_model_for

logger = logging.getLogger("gateway.forwarder")

# 这些状态码视为"渠道不可用",触发 failover;401/403 是渠道密钥配置错误而非客户端问题
FAILOVER_STATUS = {401, 403, 408, 429, 500, 502, 503, 504}

POOL_EXHAUSTED_MSG = "gateway upstream connection pool exhausted, retry later"


def _timeout(settings: Settings) -> httpx.Timeout:
    return httpx.Timeout(
        connect=settings.upstream_timeout_connect,
        read=settings.upstream_timeout_read,
        write=settings.upstream_timeout_write,
        pool=settings.upstream_timeout_connect,
    )


def _chat_url(channel: Channel) -> str:
    return channel.base_url.rstrip("/") + "/chat/completions"


def _headers(channel: Channel, fernet: Fernet) -> dict:
    return {"Authorization": f"Bearer {decrypt_api_key(fernet, channel.api_key_encrypted)}"}


def _decrypt_failure(channel: Channel) -> str:
    return f"channel[{channel.name}] api key decryption failed (GW_SECRET_KEY rotated?)"


def _parse_error_body(raw: bytes) -> dict | None:
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else None
    except (ValueError, UnicodeDecodeError):
        return None


async def forward_non_stream(
    client: httpx.AsyncClient,
    channels: list[Channel],
    model: str,
    body: dict,
    settings: Settings,
    fernet: Fernet,
) -> tuple[dict, Channel]:
    """依次尝试渠道,返回 (上游响应 JSON, 命中的渠道)。全部失败抛 UpstreamError。"""
    last_error = "no channel available"
    for channel in channels:
        payload = dict(body)
        payload["model"] = upstream_model_for(channel, model)
        payload.pop("stream", None)
        payload.pop("stream_options", None)
        try:
            headers = _headers(channel, fernet)
        except InvalidToken:
            last_error = _decrypt_failure(channel)
            logger.error("failover: %s", last_error)
            continue
        try:
            resp = await client.post(
                _chat_url(channel), json=payload, headers=headers, timeout=_timeout(settings)
            )
        except httpx.PoolTimeout as exc:
            # 池是全局共享的:换渠道必然再次超时,直接快速失败,别把 N 个渠道各烧一遍超时
            raise UpstreamError(503, POOL_EXHAUSTED_MSG) from exc
        except httpx.HTTPError as exc:
            last_error = f"channel[{channel.name}] transport error: {exc!r}"
            logger.warning("failover: %s", last_error)
            continue
        if resp.status_code in FAILOVER_STATUS:
            last_error = f"channel[{channel.name}] upstream status {resp.status_code}"
            logger.warning("failover: %s", last_error)
            continue
        if resp.status_code >= 400:
            # 客户端请求本身的问题:透传上游错误,不再尝试其他渠道
            raise UpstreamError(
                resp.status_code,
                f"upstream rejected request ({resp.status_code})",
                body=_parse_error_body(resp.content),
            )
        try:
            data = resp.json()
        except ValueError:
            last_error = f"channel[{channel.name}] returned non-JSON body"
            logger.warning("failover: %s", last_error)
            continue
        return data, channel
    raise UpstreamError(502, f"all channels failed, last error: {last_error}")


@dataclass
class StreamStats:
    """流式转发的观测数据,由 relay_sse 边转发边填充。"""

    first_token_ms: int | None = None
    usage: dict | None = field(default=None)
    completion_chars: int = 0
    saw_done: bool = False
    error: str | None = None


async def _attempt_stream(
    client: httpx.AsyncClient,
    channel: Channel,
    payload: dict,
    settings: Settings,
    fernet: Fernet,
) -> tuple[httpx.Response | None, str | None, UpstreamError | None]:
    """对单渠道尝试打开流。返回三选一:
    (resp, None, None) 成功;(None, 原因, None) 渠道故障可 failover;(None, None, err) 客户端错误。
    """
    try:
        headers = _headers(channel, fernet)
    except InvalidToken:
        return None, _decrypt_failure(channel), None
    request = client.build_request(
        "POST", _chat_url(channel), json=payload, headers=headers, timeout=_timeout(settings)
    )
    try:
        resp = await client.send(request, stream=True)
    except httpx.PoolTimeout as exc:
        raise UpstreamError(503, POOL_EXHAUSTED_MSG) from exc
    except httpx.HTTPError as exc:
        return None, f"channel[{channel.name}] transport error: {exc!r}", None
    if resp.status_code < 400:
        return resp, None, None
    # 错误体读取本身也是网络操作,失败按渠道故障处理并确保连接归还
    try:
        raw = await resp.aread()
    except httpx.HTTPError as exc:
        await resp.aclose()
        return None, f"channel[{channel.name}] error-body read failed: {exc!r}", None
    await resp.aclose()
    if resp.status_code in FAILOVER_STATUS:
        return None, f"channel[{channel.name}] upstream status {resp.status_code}", None
    return (
        None,
        None,
        UpstreamError(
            resp.status_code,
            f"upstream rejected request ({resp.status_code})",
            body=_parse_error_body(raw),
        ),
    )


async def open_stream(
    client: httpx.AsyncClient,
    channels: list[Channel],
    model: str,
    body: dict,
    settings: Settings,
    fernet: Fernet,
) -> tuple[httpx.Response, Channel, bool]:
    """依次尝试渠道打开 SSE 流。返回 (已就绪的上游响应, 渠道, 客户端是否自己要了 usage)。

    响应校验通过才返回——此时尚未向客户端发送任何字节,仍可 failover。
    """
    client_wants_usage = bool((body.get("stream_options") or {}).get("include_usage"))
    injected = settings.inject_stream_usage and not client_wants_usage
    last_error = "no channel available"
    for channel in channels:
        payload = dict(body)
        payload["model"] = upstream_model_for(channel, model)
        payload["stream"] = True
        if settings.inject_stream_usage:
            stream_options = dict(body.get("stream_options") or {})
            stream_options["include_usage"] = True
            payload["stream_options"] = stream_options

        resp, failover_reason, client_err = await _attempt_stream(
            client, channel, payload, settings, fernet
        )
        if resp is not None:
            return resp, channel, client_wants_usage
        if failover_reason is not None:
            last_error = failover_reason
            logger.warning("failover(stream): %s", last_error)
            continue
        assert client_err is not None
        if injected and client_err.status_code == 400:
            # 400 可能是网关注入的 stream_options 不被该上游支持:去掉注入原样重试一次
            plain = dict(body)
            plain["model"] = upstream_model_for(channel, model)
            plain["stream"] = True
            resp2, failover_reason2, client_err2 = await _attempt_stream(
                client, channel, plain, settings, fernet
            )
            if resp2 is not None:
                logger.warning(
                    "channel[%s] rejected injected stream_options; retried without (usage will be estimated)",
                    channel.name,
                )
                return resp2, channel, client_wants_usage
            if failover_reason2 is not None:
                last_error = failover_reason2
                logger.warning("failover(stream): %s", last_error)
                continue
            assert client_err2 is not None
            raise client_err2
        raise client_err
    raise UpstreamError(502, f"all channels failed, last error: {last_error}")


async def _iter_sse_lines(resp: httpx.Response) -> AsyncIterator[str]:
    """字节级按 \\n 切行(rstrip \\r)。

    不用 aiter_lines():它按 str.splitlines 语义切行,会把 JSON 字符串里合法出现的
    U+2028/U+2029/U+0085 等 Unicode 行分隔符当行边界,切碎上游 chunk。SSE 规范行终止符只有
    CR/LF/CRLF,0x0A 不会出现在 UTF-8 多字节序列内部,按完整行解码是安全的。
    """
    buffer = b""
    async for raw in resp.aiter_bytes():
        buffer += raw
        while (idx := buffer.find(b"\n")) != -1:
            line = buffer[:idx].rstrip(b"\r")
            buffer = buffer[idx + 1 :]
            yield line.decode("utf-8", errors="replace")
    if buffer.strip():  # 合法 SSE 事件以换行结尾,残留一般为空;保守起见仍处理
        yield buffer.rstrip(b"\r").decode("utf-8", errors="replace")


async def relay_sse(
    resp: httpx.Response,
    stats: StreamStats,
    forward_usage_chunk: bool,
    started_at: float,
    public_model: str,
) -> AsyncIterator[bytes]:
    """逐事件转发上游 SSE:chunk 到达即吐出,同时捕获 usage/首 token 延迟。

    - 网关注入的 usage-only chunk(choices 为空)在客户端未主动要求时被吞掉
    - chunk 中的 model 字段回写为对外模型名(客户端不应看到上游真实模型名)
    - 客户端断连时(生成器被取消)在 finally 中关闭上游连接,实现双向取消
    """
    try:
        async for line in _iter_sse_lines(resp):
            if not line.strip():
                continue  # 事件分隔空行,重组时统一补
            if not line.startswith("data:"):
                yield (line + "\n").encode()  # SSE 注释/event 字段原样转发
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                stats.saw_done = True
                yield b"data: [DONE]\n\n"
                continue
            obj = None
            try:
                obj = json.loads(data)
            except ValueError:
                pass
            if isinstance(obj, dict):
                choices = obj.get("choices") or []
                if stats.first_token_ms is None and choices:
                    stats.first_token_ms = int((perf_counter() - started_at) * 1000)
                for choice in choices:
                    delta = choice.get("delta") or {}
                    content = delta.get("content")
                    if isinstance(content, str):
                        stats.completion_chars += len(content)
                usage = obj.get("usage")
                if isinstance(usage, dict) and usage:
                    stats.usage = usage
                    if not choices and not forward_usage_chunk:
                        continue  # 网关注入的 usage chunk,客户端没要,吞掉
                if isinstance(obj.get("model"), str):
                    obj["model"] = public_model
                data = json.dumps(obj, ensure_ascii=False, separators=(",", ":"))
            yield f"data: {data}\n\n".encode()
    except httpx.HTTPError as exc:
        # 流中途上游断了:无法改状态码,发一个错误事件后终止
        stats.error = f"upstream stream error: {exc!r}"
        logger.warning(stats.error)
        payload = {"error": {"message": "upstream stream interrupted", "type": "upstream_error"}}
        yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()
    finally:
        await resp.aclose()
