"""针对多智能体审查确认缺陷的回归测试。"""
import httpx
import pytest
from asgi_lifespan import LifespanManager
from cryptography.fernet import Fernet

from app.config import Settings
from app.errors import UpstreamError
from app.main import create_app
from app.models import Channel
from app.security import encrypt_api_key
from app.services import forwarder, usage as usage_svc
from tests.conftest import ADMIN_HEADERS, create_channel, create_vkey, fetch_logs, key_headers
from tests.test_streaming import parse_sse

CHAT_BODY = {"model": "m-large", "messages": [{"role": "user", "content": "hi"}]}

TOOLS_BODY = {
    "model": "m-large",
    "messages": [{"role": "user", "content": "weather?"}],
    "temperature": 0,
    "tools": [{"type": "function", "function": {"name": "get_weather", "parameters": {}}}],
}


# ---- 缓存流式回放:tool_calls 与 usage 必须忠实回放 ----


async def test_cached_tool_calls_stream_replay(client, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])

    r1 = await client.post("/v1/chat/completions", json=TOOLS_BODY, headers=headers)
    assert r1.status_code == 200
    assert r1.json()["choices"][0]["finish_reason"] == "tool_calls"

    async with client.stream(
        "POST", "/v1/chat/completions", json={**TOOLS_BODY, "stream": True}, headers=headers
    ) as resp:
        assert resp.headers["X-Gateway-Cache"] == "hit"
        text = (await resp.aread()).decode()
    assert upstream_state["calls"] == 1  # 命中缓存,没打上游

    events = [e for e in parse_sse(text) if e != "[DONE]"]
    tool_deltas = [
        e["choices"][0]["delta"]["tool_calls"]
        for e in events
        if e.get("choices") and "tool_calls" in e["choices"][0]["delta"]
    ]
    assert len(tool_deltas) == 1
    assert tool_deltas[0][0]["function"]["name"] == "get_weather"
    assert tool_deltas[0][0]["function"]["arguments"] == '{"city": "sh"}'
    finish_reasons = [
        e["choices"][0]["finish_reason"] for e in events if e.get("choices")
    ]
    assert finish_reasons[-1] == "tool_calls"


async def test_cached_stream_replay_usage_when_requested(client):
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])
    deterministic = {**CHAT_BODY, "temperature": 0}

    await client.post("/v1/chat/completions", json=deterministic, headers=headers)
    stream_body = {**deterministic, "stream": True, "stream_options": {"include_usage": True}}
    async with client.stream(
        "POST", "/v1/chat/completions", json=stream_body, headers=headers
    ) as resp:
        assert resp.headers["X-Gateway-Cache"] == "hit"
        text = (await resp.aread()).decode()
    usage_events = [
        e for e in parse_sse(text) if e != "[DONE]" and not e.get("choices") and e.get("usage")
    ]
    assert len(usage_events) == 1
    assert usage_events[0]["usage"]["total_tokens"] == 9


# ---- model 字段回写为对外模型名 ----


async def test_response_model_mapped_back_non_stream(client):
    await create_channel(client, model_map={"m-large": "upstream-real-name"})
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/chat/completions", json=CHAT_BODY, headers=key_headers(vkey["key"])
    )
    assert resp.status_code == 200
    assert resp.json()["model"] == "m-large"  # 不能把上游真实模型名漏给客户端


async def test_response_model_mapped_back_stream(client):
    await create_channel(client, model_map={"m-large": "upstream-real-name"})
    vkey = await create_vkey(client)
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={**CHAT_BODY, "stream": True},
        headers=key_headers(vkey["key"]),
    ) as resp:
        text = (await resp.aread()).decode()
    models = {e["model"] for e in parse_sse(text) if e != "[DONE]" and "model" in e}
    assert models == {"m-large"}


# ---- stream_options 处理 ----


async def test_stream_options_bad_type_400(client):
    await create_channel(client)
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/chat/completions",
        json={**CHAT_BODY, "stream": True, "stream_options": "yes"},
        headers=key_headers(vkey["key"]),
    )
    assert resp.status_code == 400
    assert "stream_options" in resp.json()["error"]["message"]


async def test_injected_stream_options_rejected_retries_without(client, gateway, upstream_state):
    """上游不支持 stream_options:注入导致 400 时应去掉注入对同一渠道重试,而不是把错误怪给客户端。"""
    await create_channel(client)
    upstream_state["mode"] = "reject-stream-options"
    vkey = await create_vkey(client)
    async with client.stream(
        "POST",
        "/v1/chat/completions",
        json={**CHAT_BODY, "stream": True},
        headers=key_headers(vkey["key"]),
    ) as resp:
        assert resp.status_code == 200
        text = (await resp.aread()).decode()
    assert upstream_state["calls"] == 2  # 第一次带注入被拒,第二次裸重试成功
    events = parse_sse(text)
    contents = [
        e["choices"][0]["delta"].get("content", "")
        for e in events
        if e != "[DONE]" and e.get("choices")
    ]
    assert "".join(contents) == "Hello world"
    logs = await fetch_logs(client, gateway)
    assert logs[0]["status"] == "ok"
    assert logs[0]["usage_source"] == "estimated"  # 没有 usage chunk,只能估算


# ---- SSE 字节级切行:U+2028 不能把 chunk 切碎 ----


async def test_u2028_in_content_survives_relay(client):
    await create_channel(client)
    vkey = await create_vkey(client)
    body = {
        "model": "m-large",
        "messages": [{"role": "user", "content": "use-u2028"}],
        "stream": True,
    }
    async with client.stream(
        "POST", "/v1/chat/completions", json=body, headers=key_headers(vkey["key"])
    ) as resp:
        text = (await resp.aread()).decode()
    events = parse_sse(text)  # 事件被切碎的话这里 json.loads 会直接炸
    contents = [
        e["choices"][0]["delta"].get("content", "")
        for e in events
        if e != "[DONE]" and e.get("choices")
    ]
    assert "".join(contents) == "A" + chr(0x2028) + "B"


# ---- 价格表配置校验 ----


async def test_price_keys_must_be_subset_of_models(client):
    resp = await client.post(
        "/admin/channels",
        json={
            "name": "bad-prices",
            "base_url": "http://up-x/v1",
            "api_key": "k",
            "models": ["m-large"],
            "prices": {"upstream-real-name": {"input": 1.0, "output": 2.0}},
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422
    assert "public model names" in resp.json()["detail"]

    # patch 只改 models 也可能造成失配,必须按合并后的值校验
    created = await create_channel(client)
    resp = await client.patch(
        f"/admin/channels/{created['id']}",
        json={"models": ["renamed-model"]},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 422


# ---- admin token 默认值启动即拒绝 ----


async def test_default_admin_token_rejected_at_startup(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'x.db').as_posix()}",
        admin_token="change-me",
        secret_key=Fernet.generate_key().decode(),
    )
    app = create_app(settings)
    with pytest.raises(RuntimeError, match="GW_ADMIN_TOKEN"):
        async with LifespanManager(app):
            pass
    await app.state.upstream_client.aclose()
    await app.state.engine.dispose()


# ---- usage 部分缺失的回填 ----


def test_finalize_usage_backfill():
    body = {"messages": [{"role": "user", "content": "x" * 400}]}
    # 全都有:upstream
    assert usage_svc.finalize_usage(7, 2, 9, body, "") == (7, 2, 9, "upstream")
    # 只缺 completion:用 total 反推
    assert usage_svc.finalize_usage(1000, None, 1600, body, "abc") == (1000, 600, 1600, "mixed")
    # 只缺 prompt:用 total 反推
    assert usage_svc.finalize_usage(None, 600, 1600, body, "") == (1000, 600, 1600, "mixed")
    # 只缺 completion 且没有 total:估算 completion
    pt, ct, tt, src = usage_svc.finalize_usage(1000, None, None, body, "x" * 40)
    assert (pt, src) == (1000, "mixed") and ct == 10 and tt == 1010
    # 全缺:整体估算
    pt, ct, tt, src = usage_svc.finalize_usage(None, None, None, body, "x" * 40)
    assert src == "estimated" and pt > 0 and ct == 10 and tt == pt + ct


# ---- 计量与被删外键的竞态:账不能丢 ----


async def test_meter_survives_deleted_fk(client, gateway):
    channel = await create_channel(client)
    vkey = await create_vkey(client)
    await client.delete(f"/admin/keys/{vkey['id']}", headers=ADMIN_HEADERS)

    gateway.state.meter.record(
        trace_id="deadbeef" * 4,
        virtual_key_id=vkey["id"],  # 已被删除:插入会违反外键
        channel_id=channel["id"],
        model="m-large",
        stream=False,
        status="ok",
        usage_source="upstream",
        cost_usd=0.5,
    )
    await gateway.state.meter.drain()
    logs = (await client.get("/admin/logs", headers=ADMIN_HEADERS)).json()
    assert len(logs) == 1  # 行还在(外键置空重试),账没丢
    assert logs[0]["virtual_key_id"] is None
    assert logs[0]["cost_usd"] == 0.5


# ---- 连接池耗尽:快速 503,不遍历渠道 ----


async def test_pool_timeout_fails_fast_503():
    fernet = Fernet(Fernet.generate_key())
    settings = Settings(admin_token="x", secret_key="")

    def make_channel(cid: int, name: str) -> Channel:
        return Channel(
            id=cid,
            name=name,
            base_url=f"http://{name}/v1",
            api_key_encrypted=encrypt_api_key(fernet, "k"),
            models=["m"],
            model_map={},
            prices={},
            priority=cid,
            enabled=True,
        )

    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        raise httpx.PoolTimeout("pool exhausted")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    channels = [make_channel(1, "a"), make_channel(2, "b")]
    body = {"model": "m", "messages": [{"role": "user", "content": "hi"}]}
    with pytest.raises(UpstreamError) as exc_info:
        await forwarder.forward_non_stream(client, channels, "m", body, settings, fernet)
    assert exc_info.value.status_code == 503
    assert calls["n"] == 1  # 池是共享的:第一个渠道超时后立即失败,不再遍历
    await client.aclose()


# ---- 渠道密钥解密失败:failover 而不是 500 ----


async def test_decrypt_failure_fails_over(client, gateway, upstream_state):
    await create_channel(client, name="chan-a", base_url="http://up-a/v1", priority=10)
    await create_channel(
        client, name="chan-b", base_url="http://up-b/v1", api_key="sk-real-key-b", priority=20
    )
    # 直接把 chan-a 的密文改坏,模拟 GW_SECRET_KEY 轮换后的旧密文
    from sqlalchemy import update

    async with gateway.state.sessionmaker() as session:
        await session.execute(
            update(Channel).where(Channel.name == "chan-a").values(api_key_encrypted="gAAAAAbroken")
        )
        await session.commit()

    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/chat/completions", json=CHAT_BODY, headers=key_headers(vkey["key"])
    )
    assert resp.status_code == 200
    assert resp.headers["X-Gateway-Channel"] == "chan-b"
    assert upstream_state["hosts_called"] == ["up-b"]  # chan-a 根本没被请求


# ---- 超长 trace_id(64 字符上限)完整落库 ----


async def test_long_trace_id_round_trip(client, gateway):
    await create_channel(client)
    vkey = await create_vkey(client)
    tid = "a" * 64
    resp = await client.post(
        "/v1/chat/completions",
        json=CHAT_BODY,
        headers={**key_headers(vkey["key"]), "X-Trace-Id": tid},
    )
    assert resp.headers["X-Trace-Id"] == tid
    logs = await fetch_logs(client, gateway)
    assert logs[0]["trace_id"] == tid
