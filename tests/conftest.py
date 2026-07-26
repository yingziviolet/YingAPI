"""测试基座:内嵌假上游(ASGI)+ SQLite 网关实例,全链路不出进程。"""
import json

import httpx
import pytest
from asgi_lifespan import LifespanManager
from cryptography.fernet import Fernet
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.config import Settings
from app.main import create_app

ADMIN_HEADERS = {"Authorization": "Bearer test-admin"}


def make_upstream(state: dict) -> FastAPI:
    """假上游:按 Host 头区分"不同渠道",行为由 state 控制。

    state:
      calls: 总调用次数
      mode: 默认行为 ok / fail-500 / fail-400 / fail-timeout
      host_modes: {host: mode} 按渠道覆盖
      auth_by_host: {host: authorization 头} 供断言解密后的真实 key
    """
    up = FastAPI()

    @up.post("/v1/chat/completions")
    async def chat(request: Request):
        state["calls"] += 1
        host = request.headers.get("host", "")
        state.setdefault("hosts_called", []).append(host)
        state.setdefault("auth_by_host", {})[host] = request.headers.get("authorization")
        body = await request.json()
        state["last_body"] = body
        mode = state.get("host_modes", {}).get(host, state.get("mode", "ok"))

        if mode == "fail-500":
            return JSONResponse(
                {"error": {"message": "upstream exploded", "type": "server_error"}}, status_code=500
            )
        if mode == "fail-429":
            return JSONResponse(
                {"error": {"message": "rate limited", "type": "rate_limit_error"}}, status_code=429
            )
        if mode == "fail-400":
            return JSONResponse(
                {"error": {"message": "bad params", "type": "invalid_request_error"}},
                status_code=400,
            )
        if mode == "reject-stream-options" and body.get("stream") and "stream_options" in body:
            # 模拟不支持 stream_options 参数的老上游
            return JSONResponse(
                {"error": {"message": "unknown parameter: stream_options", "type": "invalid_request_error"}},
                status_code=400,
            )

        first_user_content = ""
        for m in body.get("messages", []):
            if isinstance(m, dict) and m.get("role") == "user":
                first_user_content = m.get("content") or ""
                break

        if body.get("stream"):
            include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

            def gen():
                base = {"id": "chatcmpl-t1", "object": "chat.completion.chunk", "model": body["model"]}
                if first_user_content == "use-u2028":
                    # 内容里带裸 U+2028(JSON 合法,ensure_ascii=False 不转义)
                    deltas = [{"role": "assistant", "content": ""}, {"content": "A" + chr(0x2028) + "B"}]
                else:
                    deltas = [
                        {"role": "assistant", "content": ""},
                        {"content": "Hello"},
                        {"content": " world"},
                    ]
                for d in deltas:
                    chunk = {**base, "choices": [{"index": 0, "delta": d, "finish_reason": None}]}
                    yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                finish = {**base, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
                yield f"data: {json.dumps(finish)}\n\n"
                if include_usage:
                    usage_chunk = {
                        **base,
                        "choices": [],
                        "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
                    }
                    yield f"data: {json.dumps(usage_chunk)}\n\n"
                yield "data: [DONE]\n\n"

            return StreamingResponse(gen(), media_type="text/event-stream")

        if body.get("tools"):
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": '{"city": "sh"}'},
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": "Hello world"}
            finish_reason = "stop"
        return {
            "id": "chatcmpl-t1",
            "object": "chat.completion",
            "created": 1700000000,
            "model": body["model"],
            "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2, "total_tokens": 9},
        }

    return up


@pytest.fixture
def upstream_state() -> dict:
    return {"calls": 0, "mode": "ok", "host_modes": {}}


@pytest.fixture
async def gateway(tmp_path, upstream_state):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'test.db').as_posix()}",
        auto_create_tables=True,
        secret_key=Fernet.generate_key().decode(),
        admin_token="test-admin",
        cache_enabled=True,
        cache_ttl_seconds=3600,
    )
    app = create_app(settings)
    # 上游连接池整体替换为指向假上游的 ASGI transport
    await app.state.upstream_client.aclose()
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_upstream(upstream_state))
    )
    async with LifespanManager(app):
        yield app


@pytest.fixture
async def client(gateway):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=gateway), base_url="http://gw.local"
    ) as c:
        yield c


async def create_channel(client: httpx.AsyncClient, **overrides) -> dict:
    payload = {
        "name": "chan-a",
        "base_url": "http://up-a/v1",
        "api_key": "sk-real-key-a",
        "models": ["m-large"],
        "prices": {"m-large": {"input": 1.0, "output": 2.0}},
        "priority": 10,
    }
    payload.update(overrides)
    resp = await client.post("/admin/channels", json=payload, headers=ADMIN_HEADERS)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def create_vkey(client: httpx.AsyncClient, name: str = "test-key", **overrides) -> dict:
    payload = {"name": name, **overrides}
    resp = await client.post("/admin/keys", json=payload, headers=ADMIN_HEADERS)
    assert resp.status_code == 201, resp.text
    return resp.json()


def key_headers(raw_key: str) -> dict:
    return {"Authorization": f"Bearer {raw_key}"}


async def fetch_logs(client: httpx.AsyncClient, gateway) -> list[dict]:
    await gateway.state.meter.drain()
    resp = await client.get("/admin/logs", headers=ADMIN_HEADERS)
    assert resp.status_code == 200, resp.text
    return resp.json()
