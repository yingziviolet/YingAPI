"""P2.5:Anthropic Messages API 入口(/v1/messages)双向协议翻译。"""
import json

from tests.conftest import ADMIN_HEADERS, create_channel, create_vkey, fetch_logs

MSG_BODY = {
    "model": "m-large",
    "max_tokens": 128,
    "messages": [{"role": "user", "content": "hi"}],
}


def anthropic_headers(raw_key: str) -> dict:
    return {"x-api-key": raw_key, "anthropic-version": "2023-06-01"}


def parse_anthropic_sse(text: str) -> list[tuple[str, dict]]:
    """解析 Anthropic SSE:返回 (event_name, data) 列表。"""
    events = []
    current_event = None
    for line in text.split("\n"):
        if line.startswith("event:"):
            current_event = line[6:].strip()
        elif line.startswith("data:") and current_event:
            events.append((current_event, json.loads(line[5:].strip())))
    return events


async def test_basic_non_stream(client, gateway, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/messages", json=MSG_BODY, headers=anthropic_headers(vkey["key"])
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["type"] == "message"
    assert data["role"] == "assistant"
    assert data["model"] == "m-large"
    assert data["content"] == [{"type": "text", "text": "Hello world"}]
    assert data["stop_reason"] == "end_turn"
    assert data["usage"] == {"input_tokens": 7, "output_tokens": 2}
    assert data["id"].startswith("msg_")

    # 上游收到的是 OpenAI 协议
    upstream_body = upstream_state["last_body"]
    assert upstream_body["messages"] == [{"role": "user", "content": "hi"}]
    assert upstream_body["max_tokens"] == 128
    # 计量落库
    logs = await fetch_logs(client, gateway)
    assert logs[0]["status"] == "ok"
    assert logs[0]["prompt_tokens"] == 7


async def test_system_and_multiturn_translation(client, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    body = {
        "model": "m-large",
        "max_tokens": 64,
        "system": "You are terse.",
        "messages": [
            {"role": "user", "content": [{"type": "text", "text": "one"}]},
            {"role": "assistant", "content": [{"type": "text", "text": "two"}]},
            {"role": "user", "content": "three"},
        ],
    }
    resp = await client.post("/v1/messages", json=body, headers=anthropic_headers(vkey["key"]))
    assert resp.status_code == 200
    upstream_messages = upstream_state["last_body"]["messages"]
    assert upstream_messages[0] == {"role": "system", "content": "You are terse."}
    assert upstream_messages[1] == {"role": "user", "content": "one"}
    assert upstream_messages[2] == {"role": "assistant", "content": "two"}
    assert upstream_messages[3] == {"role": "user", "content": "three"}


async def test_tool_use_round_trip(client, upstream_state):
    """anthropic tools -> OpenAI tools;OpenAI tool_calls -> anthropic tool_use;tool_result -> role=tool。"""
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = anthropic_headers(vkey["key"])

    body = {
        "model": "m-large",
        "max_tokens": 64,
        "tools": [
            {
                "name": "get_weather",
                "description": "查天气",
                "input_schema": {"type": "object", "properties": {"city": {"type": "string"}}},
            }
        ],
        "messages": [{"role": "user", "content": "weather?"}],
    }
    resp = await client.post("/v1/messages", json=body, headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    # fake upstream 看到 tools 会返回 tool_calls
    assert upstream_state["last_body"]["tools"][0]["function"]["name"] == "get_weather"
    tool_use = next(b for b in data["content"] if b["type"] == "tool_use")
    assert tool_use["name"] == "get_weather"
    assert tool_use["input"] == {"city": "sh"}
    assert data["stop_reason"] == "tool_use"

    # 第二轮:回传 tool_result
    body2 = {
        "model": "m-large",
        "max_tokens": 64,
        "messages": [
            {"role": "user", "content": "weather?"},
            {"role": "assistant", "content": data["content"]},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use["id"],
                        "content": "sunny, 25C",
                    }
                ],
            },
        ],
    }
    resp2 = await client.post("/v1/messages", json=body2, headers=headers)
    assert resp2.status_code == 200
    upstream_messages = upstream_state["last_body"]["messages"]
    assistant_msg = upstream_messages[1]
    assert assistant_msg["tool_calls"][0]["function"]["name"] == "get_weather"
    assert json.loads(assistant_msg["tool_calls"][0]["function"]["arguments"]) == {"city": "sh"}
    tool_msg = upstream_messages[2]
    assert tool_msg["role"] == "tool"
    assert tool_msg["tool_call_id"] == tool_use["id"]
    assert tool_msg["content"] == "sunny, 25C"


async def test_streaming_event_sequence(client, gateway, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    body = {**MSG_BODY, "stream": True}
    async with client.stream(
        "POST", "/v1/messages", json=body, headers=anthropic_headers(vkey["key"])
    ) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        text = (await resp.aread()).decode()

    events = parse_anthropic_sse(text)
    names = [name for name, _ in events]
    assert names[0] == "message_start"
    assert names[-2:] == ["message_delta", "message_stop"]
    assert "content_block_start" in names and "content_block_stop" in names

    start = events[0][1]
    assert start["message"]["role"] == "assistant"
    assert start["message"]["model"] == "m-large"

    text_deltas = [
        d["delta"]["text"]
        for name, d in events
        if name == "content_block_delta" and d["delta"]["type"] == "text_delta"
    ]
    assert "".join(text_deltas) == "Hello world"

    message_delta = next(d for name, d in events if name == "message_delta")
    assert message_delta["delta"]["stop_reason"] == "end_turn"
    # usage 来自网关注入的 include_usage(上游返回 completion_tokens=2)
    assert message_delta["usage"]["output_tokens"] == 2

    logs = await fetch_logs(client, gateway)
    assert logs[0]["status"] == "ok"
    assert logs[0]["stream"] is True
    assert logs[0]["completion_tokens"] == 2
    assert logs[0]["usage_source"] == "upstream"


async def test_auth_error_shape(client):
    resp = await client.post("/v1/messages", json=MSG_BODY)
    assert resp.status_code == 401
    data = resp.json()
    assert data["type"] == "error"
    assert data["error"]["type"] == "authentication_error"


async def test_validation_errors(client):
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = anthropic_headers(vkey["key"])
    # 缺 max_tokens
    resp = await client.post(
        "/v1/messages",
        json={"model": "m-large", "messages": [{"role": "user", "content": "hi"}]},
        headers=headers,
    )
    assert resp.status_code == 400
    assert "max_tokens" in resp.json()["error"]["message"]
    # 未知模型
    resp = await client.post(
        "/v1/messages",
        json={"model": "nope", "max_tokens": 8, "messages": [{"role": "user", "content": "x"}]},
        headers=headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["type"] == "not_found_error"


async def test_count_tokens(client):
    await create_channel(client)
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/messages/count_tokens",
        json={"model": "m-large", "messages": [{"role": "user", "content": "hello " * 100}]},
        headers=anthropic_headers(vkey["key"]),
    )
    assert resp.status_code == 200
    assert resp.json()["input_tokens"] > 50


async def test_exact_cache_shared_with_openai_entry(client, upstream_state):
    """两个协议入口共享同一份精确缓存(翻译后的 OpenAI 体做 key)。"""
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = anthropic_headers(vkey["key"])
    body = {**MSG_BODY, "temperature": 0}
    r1 = await client.post("/v1/messages", json=body, headers=headers)
    assert r1.headers["X-Gateway-Cache"] == "miss"
    r2 = await client.post("/v1/messages", json=body, headers=headers)
    assert r2.headers["X-Gateway-Cache"] == "hit"
    assert upstream_state["calls"] == 1
    assert r2.json()["content"] == r1.json()["content"]


async def test_models_endpoint_dual_protocol(client):
    await create_channel(client)
    vkey = await create_vkey(client)
    resp = await client.get("/v1/models", headers={"x-api-key": vkey["key"]})
    assert resp.status_code == 200
    data = resp.json()
    entry = data["data"][0]
    assert entry["object"] == "model"  # OpenAI 客户端
    assert entry["type"] == "model"  # Anthropic 客户端
    assert entry["display_name"] == entry["id"]