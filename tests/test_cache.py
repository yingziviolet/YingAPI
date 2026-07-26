"""精确匹配缓存:命中/未命中、temperature 门槛、TTL 过期、流式命中回放。"""
from datetime import timedelta

from sqlalchemy import update

from app.models import CacheEntry, utcnow
from tests.conftest import create_channel, create_vkey, fetch_logs, key_headers

DETERMINISTIC_BODY = {
    "model": "m-large",
    "messages": [{"role": "user", "content": "hi"}],
    "temperature": 0,
}


async def test_cache_hit_on_repeat(client, gateway, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])

    r1 = await client.post("/v1/chat/completions", json=DETERMINISTIC_BODY, headers=headers)
    assert r1.status_code == 200
    assert r1.headers["X-Gateway-Cache"] == "miss"
    assert upstream_state["calls"] == 1

    r2 = await client.post("/v1/chat/completions", json=DETERMINISTIC_BODY, headers=headers)
    assert r2.status_code == 200
    assert r2.headers["X-Gateway-Cache"] == "hit"
    assert upstream_state["calls"] == 1  # 没打上游
    assert r2.json() == r1.json()

    logs = await fetch_logs(client, gateway)
    # 计量是异步落库,两条日志先后不保证;按内容断言
    assert sorted(log["cache_hit"] for log in logs) == [False, True]
    hit_log = next(log for log in logs if log["cache_hit"])
    assert hit_log["status"] == "cache_hit"
    assert hit_log["cost_usd"] == 0.0
    assert hit_log["prompt_tokens"] == 7  # 记录省下来的 token


async def test_high_temperature_not_cached(client, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])
    body = {**DETERMINISTIC_BODY, "temperature": 0.9}
    r1 = await client.post("/v1/chat/completions", json=body, headers=headers)
    r2 = await client.post("/v1/chat/completions", json=body, headers=headers)
    assert r1.headers["X-Gateway-Cache"] == "skip"
    assert r2.headers["X-Gateway-Cache"] == "skip"
    assert upstream_state["calls"] == 2


async def test_no_temperature_not_cached(client, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])
    body = {"model": "m-large", "messages": [{"role": "user", "content": "hi"}]}
    await client.post("/v1/chat/completions", json=body, headers=headers)
    await client.post("/v1/chat/completions", json=body, headers=headers)
    assert upstream_state["calls"] == 2


async def test_different_prompt_different_key(client, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])
    await client.post("/v1/chat/completions", json=DETERMINISTIC_BODY, headers=headers)
    other = {**DETERMINISTIC_BODY, "messages": [{"role": "user", "content": "different"}]}
    r2 = await client.post("/v1/chat/completions", json=other, headers=headers)
    assert r2.headers["X-Gateway-Cache"] == "miss"
    assert upstream_state["calls"] == 2


async def test_expired_entry_is_miss(client, gateway, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])
    await client.post("/v1/chat/completions", json=DETERMINISTIC_BODY, headers=headers)

    # 手动把缓存条目改成已过期
    async with gateway.state.sessionmaker() as session:
        await session.execute(
            update(CacheEntry).values(expires_at=utcnow() - timedelta(seconds=1))
        )
        await session.commit()

    r2 = await client.post("/v1/chat/completions", json=DETERMINISTIC_BODY, headers=headers)
    assert r2.headers["X-Gateway-Cache"] == "miss"
    assert upstream_state["calls"] == 2


async def test_cache_hit_replayed_as_stream(client, upstream_state):
    """先非流式写入缓存,再以 stream=True 请求同内容:命中并合成 SSE 回放。"""
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])
    r1 = await client.post("/v1/chat/completions", json=DETERMINISTIC_BODY, headers=headers)
    assert r1.status_code == 200

    from tests.test_streaming import parse_sse

    stream_body = {**DETERMINISTIC_BODY, "stream": True}
    async with client.stream(
        "POST", "/v1/chat/completions", json=stream_body, headers=headers
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["X-Gateway-Cache"] == "hit"
        text = (await resp.aread()).decode()
    assert upstream_state["calls"] == 1
    events = parse_sse(text)
    assert events[-1] == "[DONE]"
    contents = [
        e["choices"][0]["delta"].get("content", "")
        for e in events
        if e != "[DONE]" and e.get("choices")
    ]
    assert "".join(contents) == "Hello world"
    finish_reasons = [
        e["choices"][0]["finish_reason"] for e in events if e != "[DONE]" and e.get("choices")
    ]
    assert finish_reasons[-1] == "stop"
