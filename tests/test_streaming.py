"""SSE 流式透传:chunk 转发、[DONE]、usage 捕获与注入 chunk 的吞吐控制、流式 failover。"""
import json

from tests.conftest import create_channel, create_vkey, fetch_logs, key_headers

STREAM_BODY = {
    "model": "m-large",
    "messages": [{"role": "user", "content": "hi"}],
    "stream": True,
}


def parse_sse(text: str) -> list:
    """把 SSE 文本解析为 data 载荷列表([DONE] 保留为字符串)。

    只按 \\n 切行(真实 SSE 客户端的行为)——不能用 splitlines(),
    它会把内容里合法的 U+2028 当行边界。
    """
    events = []
    for line in text.split("\n"):
        if line.startswith("data:"):
            data = line[5:].strip()
            if data == "[DONE]":
                events.append("[DONE]")
            else:
                events.append(json.loads(data))
    return events


async def test_stream_basic(client, gateway, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    async with client.stream(
        "POST", "/v1/chat/completions", json=STREAM_BODY, headers=key_headers(vkey["key"])
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        assert resp.headers["X-Gateway-Channel"] == "chan-a"
        text = (await resp.aread()).decode()

    events = parse_sse(text)
    assert events[-1] == "[DONE]"
    contents = [
        e["choices"][0]["delta"].get("content", "")
        for e in events
        if e != "[DONE]" and e.get("choices")
    ]
    assert "".join(contents) == "Hello world"
    # 客户端没要 usage:网关注入的 usage-only chunk 必须被吞掉
    assert all(e == "[DONE]" or e.get("choices") for e in events)
    # 但网关自己拿到了 usage 并计了账
    logs = await fetch_logs(client, gateway)
    log = logs[0]
    assert log["status"] == "ok"
    assert log["stream"] is True
    assert log["prompt_tokens"] == 7
    assert log["completion_tokens"] == 2
    assert log["usage_source"] == "upstream"
    assert log["first_token_ms"] is not None
    # 上游确实收到了注入的 include_usage
    assert upstream_state["last_body"]["stream_options"]["include_usage"] is True


async def test_stream_client_wants_usage(client, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    body = {**STREAM_BODY, "stream_options": {"include_usage": True}}
    async with client.stream(
        "POST", "/v1/chat/completions", json=body, headers=key_headers(vkey["key"])
    ) as resp:
        text = (await resp.aread()).decode()
    events = parse_sse(text)
    usage_events = [e for e in events if e != "[DONE]" and not e.get("choices") and e.get("usage")]
    assert len(usage_events) == 1  # 客户端主动要了,转发给它
    assert usage_events[0]["usage"]["total_tokens"] == 9


async def test_stream_failover_before_first_byte(client, upstream_state):
    await create_channel(client, name="chan-a", base_url="http://up-a/v1", priority=10)
    await create_channel(
        client, name="chan-b", base_url="http://up-b/v1", api_key="sk-real-key-b", priority=20
    )
    upstream_state["host_modes"] = {"up-a": "fail-429"}
    vkey = await create_vkey(client)
    async with client.stream(
        "POST", "/v1/chat/completions", json=STREAM_BODY, headers=key_headers(vkey["key"])
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["X-Gateway-Channel"] == "chan-b"
        text = (await resp.aread()).decode()
    assert parse_sse(text)[-1] == "[DONE]"
    assert upstream_state["hosts_called"] == ["up-a", "up-b"]


async def test_stream_all_channels_down_returns_json_error(client, upstream_state):
    await create_channel(client)
    upstream_state["mode"] = "fail-500"
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/chat/completions", json=STREAM_BODY, headers=key_headers(vkey["key"])
    )
    # 流还没开始,可以返回正常 JSON 错误
    assert resp.status_code == 502
    assert "all channels failed" in resp.json()["error"]["message"]
