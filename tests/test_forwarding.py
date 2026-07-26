"""非流式转发:透传、模型改写、密钥解密使用、failover、4xx 不 failover、计量落库。"""
from tests.conftest import create_channel, create_vkey, fetch_logs, key_headers

CHAT_BODY = {"model": "m-large", "messages": [{"role": "user", "content": "hi"}]}


async def test_basic_forward(client, gateway, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/chat/completions", json=CHAT_BODY, headers=key_headers(vkey["key"])
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["choices"][0]["message"]["content"] == "Hello world"
    assert resp.headers["X-Gateway-Channel"] == "chan-a"
    assert resp.headers["X-Trace-Id"]
    # 网关把解密后的真实 key 发给了上游
    assert upstream_state["auth_by_host"]["up-a"] == "Bearer sk-real-key-a"

    logs = await fetch_logs(client, gateway)
    assert len(logs) == 1
    log = logs[0]
    assert log["status"] == "ok"
    assert log["model"] == "m-large"
    assert log["prompt_tokens"] == 7
    assert log["completion_tokens"] == 2
    assert log["usage_source"] == "upstream"
    # 成本:7 * 1.0/1M + 2 * 2.0/1M
    assert abs(log["cost_usd"] - (7 * 1.0 + 2 * 2.0) / 1_000_000) < 1e-12
    assert log["trace_id"] == resp.headers["X-Trace-Id"]


async def test_model_map_rewrite(client, upstream_state):
    await create_channel(client, model_map={"m-large": "upstream-real-name"})
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/chat/completions", json=CHAT_BODY, headers=key_headers(vkey["key"])
    )
    assert resp.status_code == 200
    assert upstream_state["last_body"]["model"] == "upstream-real-name"


async def test_failover_to_next_channel(client, gateway, upstream_state):
    await create_channel(client, name="chan-a", base_url="http://up-a/v1", priority=10)
    await create_channel(
        client, name="chan-b", base_url="http://up-b/v1", api_key="sk-real-key-b", priority=20
    )
    upstream_state["host_modes"] = {"up-a": "fail-500"}
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/chat/completions", json=CHAT_BODY, headers=key_headers(vkey["key"])
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Gateway-Channel"] == "chan-b"
    assert upstream_state["hosts_called"] == ["up-a", "up-b"]

    logs = await fetch_logs(client, gateway)
    assert logs[0]["status"] == "ok"


async def test_all_channels_down(client, gateway, upstream_state):
    await create_channel(client)
    upstream_state["mode"] = "fail-500"
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/chat/completions", json=CHAT_BODY, headers=key_headers(vkey["key"])
    )
    assert resp.status_code == 502
    assert "all channels failed" in resp.json()["error"]["message"]
    logs = await fetch_logs(client, gateway)
    assert logs[0]["status"] == "error"


async def test_client_error_not_failover(client, upstream_state):
    """上游 400 是请求本身的问题:原样透传,不应该再打第二个渠道。"""
    await create_channel(client, name="chan-a", base_url="http://up-a/v1", priority=10)
    await create_channel(
        client, name="chan-b", base_url="http://up-b/v1", api_key="sk-real-key-b", priority=20
    )
    upstream_state["host_modes"] = {"up-a": "fail-400"}
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/chat/completions", json=CHAT_BODY, headers=key_headers(vkey["key"])
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["message"] == "bad params"  # 上游错误体透传
    assert upstream_state["hosts_called"] == ["up-a"]


async def test_unknown_model_404(client):
    await create_channel(client)
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "no-such-model", "messages": [{"role": "user", "content": "hi"}]},
        headers=key_headers(vkey["key"]),
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "model_not_found"


async def test_list_models(client):
    await create_channel(client, models=["m-large", "m-small"])
    vkey = await create_vkey(client)
    resp = await client.get("/v1/models", headers=key_headers(vkey["key"]))
    assert resp.status_code == 200
    ids = [m["id"] for m in resp.json()["data"]]
    assert ids == ["m-large", "m-small"]


async def test_trace_id_passthrough(client):
    await create_channel(client)
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/chat/completions",
        json=CHAT_BODY,
        headers={**key_headers(vkey["key"]), "X-Trace-Id": "abcdef1234567890"},
    )
    assert resp.headers["X-Trace-Id"] == "abcdef1234567890"
