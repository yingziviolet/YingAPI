"""P2/P2.5 审查确认缺陷的回归测试。"""
import json

import pytest

from app.config import Settings
from app.services.anthropic_translate import (
    AnthropicStreamTranslator,
    anthropic_to_openai_request,
    map_stop_reason,
)
from app.services.breaker import CircuitBreaker, CircuitState
from app.services.semantic_cache import request_text
from app.services.usage import estimate_prompt_tokens
from tests.conftest import ADMIN_HEADERS, create_channel, create_vkey, key_headers
from tests.test_p2 import FakeClock, make_embedder_channel

CHAT_BODY = {"model": "m-large", "messages": [{"role": "user", "content": "hi"}]}


# ---- 语义缓存:生成参数分区 / 多模态跳过 ----


async def test_semantic_no_cross_hit_between_different_params(client, upstream_state):
    """语义相同但 tools 不同的请求,不能共享语义缓存响应。"""
    await create_channel(client)
    await make_embedder_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])

    q = "what is two plus two"
    upstream_state["embeddings"] = {f"user: {q}": [1.0, 0.0, 0.0, 0.0]}

    base = {"model": "m-large", "messages": [{"role": "user", "content": q}], "temperature": 0}
    r1 = await client.post("/v1/chat/completions", json=base, headers=headers)
    assert r1.status_code == 200

    with_tools = {
        **base,
        "tools": [{"type": "function", "function": {"name": "f", "parameters": {}}}],
    }
    r2 = await client.post("/v1/chat/completions", json=with_tools, headers=headers)
    assert r2.headers["X-Gateway-Cache"] == "miss"  # 不能命中无 tools 的语义条目
    assert upstream_state["calls"] == 2

    # 同参数的请求仍能命中
    r3 = await client.post("/v1/chat/completions", json=base, headers=headers)
    assert r3.headers["X-Gateway-Cache"] == "hit"  # 精确命中(完全相同)
    assert upstream_state["calls"] == 2


def test_request_text_skips_multimodal():
    assert request_text({"messages": [{"role": "user", "content": "plain"}]}) == "user: plain"
    # 图片段:整体跳过
    assert (
        request_text(
            {
                "messages": [
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": "look"},
                            {"type": "image_url", "image_url": {"url": "data:..."}},
                        ],
                    }
                ]
            }
        )
        == ""
    )
    # assistant tool_calls:整体跳过
    assert (
        request_text(
            {
                "messages": [
                    {"role": "user", "content": "q"},
                    {"role": "assistant", "content": None, "tool_calls": [{"id": "x"}]},
                ]
            }
        )
        == ""
    )


# ---- 熔断器:探测名额泄漏与自愈 ----


def _breaker(**overrides):
    settings = Settings(
        admin_token="x",
        cb_window_seconds=60,
        cb_min_requests=4,
        cb_error_threshold=0.5,
        cb_open_seconds=30,
        cb_half_open_probes=2,
        **overrides,
    )
    clock = FakeClock()
    return CircuitBreaker(settings, clock=clock), clock


def test_release_probe_returns_half_open_slot():
    breaker, clock = _breaker()
    for _ in range(4):
        breaker.record_failure(1)
    clock.now += 31
    assert breaker.allow(1) is True
    assert breaker.allow(1) is True
    assert breaker.allow(1) is False  # 名额用完
    breaker.release_probe(1)  # PoolTimeout 等无结论路径归还名额
    assert breaker.allow(1) is True


def test_half_open_self_heals_after_leak():
    """即使名额全部泄漏(没有 release/record),超时后也能自愈重新放探测。"""
    breaker, clock = _breaker()
    for _ in range(4):
        breaker.record_failure(1)
    clock.now += 31
    assert breaker.allow(1) is True
    assert breaker.allow(1) is True
    assert breaker.allow(1) is False  # 泄漏:名额耗尽且无人回写
    clock.now += 30 + 60 + 1  # cb_open_seconds + cb_window_seconds
    assert breaker.allow(1) is True  # 自愈
    assert breaker.state_of(1) == CircuitState.HALF_OPEN


# ---- Anthropic 协议修复 ----


def test_system_role_in_messages_translates():
    out = anthropic_to_openai_request(
        {
            "model": "m",
            "max_tokens": 8,
            "messages": [
                {"role": "user", "content": "a"},
                {"role": "system", "content": "mode switch"},
                {"role": "user", "content": "b"},
            ],
        }
    )
    assert out["messages"][1] == {"role": "system", "content": "mode switch"}


def test_stop_reason_content_filter_maps_to_refusal():
    assert map_stop_reason("content_filter") == "refusal"
    assert map_stop_reason("stop") == "end_turn"


def test_stream_translator_parallel_tools_same_index():
    """部分上游并行工具调用全用 index 0:靠新 id 切块。"""
    t = AnthropicStreamTranslator("m")
    t.start()
    events = []
    events += t.feed(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_a", "function": {"name": "fa", "arguments": '{"x":1}'}}
        ]}, "finish_reason": None}]}
    )
    events += t.feed(
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_b", "function": {"name": "fb", "arguments": '{"y":2}'}}
        ]}, "finish_reason": None}]}
    )
    starts = [
        json.loads(e.decode().split("data: ")[1])
        for e in events
        if b"content_block_start" in e
    ]
    assert len(starts) == 2
    assert starts[0]["content_block"]["id"] == "call_a"
    assert starts[1]["content_block"]["id"] == "call_b"
    assert starts[1]["index"] == 1


def test_stream_translator_backfills_input_tokens():
    t = AnthropicStreamTranslator("m", input_tokens_estimate=999)
    t.start()
    t.feed({"choices": [{"delta": {"content": "hi"}, "finish_reason": None}]})
    t.feed({"choices": [], "usage": {"prompt_tokens": 42, "completion_tokens": 7}})
    events = t.finish()
    delta = next(
        json.loads(e.decode().split("data: ")[1]) for e in events if b"message_delta" in e
    )
    assert delta["usage"]["output_tokens"] == 7
    assert delta["usage"]["input_tokens"] == 42  # 上游准确值回填,替代 999 估算


async def test_upstream_error_body_passthrough(client, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    upstream_state["mode"] = "fail-400"
    resp = await client.post(
        "/v1/messages",
        json={"model": "m-large", "max_tokens": 8, "messages": [{"role": "user", "content": "x"}]},
        headers={"x-api-key": vkey["key"]},
    )
    assert resp.status_code == 400
    data = resp.json()
    assert data["error"]["message"] == "bad params"  # 上游原文,不是笼统的 upstream rejected
    assert data["error"]["type"] == "invalid_request_error"


def test_count_tokens_includes_tools():
    base = {"messages": [{"role": "user", "content": "hi"}]}
    with_tools = {
        **base,
        "tools": [{"type": "function", "function": {"name": "f", "description": "d" * 400, "parameters": {}}}],
    }
    assert estimate_prompt_tokens(with_tools) > estimate_prompt_tokens(base) + 50


# ---- 限流:rpm=0 覆盖全局默认 / Redis fail-open ----


async def test_rpm_zero_means_unlimited(client):
    vkey = await create_vkey(client, name="unlimited-key", rpm_limit=0)
    assert vkey["rpm_limit"] == 0
    headers = key_headers(vkey["key"])
    await create_channel(client)
    for _ in range(5):
        resp = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
        assert resp.status_code == 200


async def test_redis_ratelimiter_fails_open():
    pytest.importorskip("redis")
    from app.services.ratelimit import RedisRateLimiter

    limiter = RedisRateLimiter("redis://127.0.0.1:1/0")  # 打不通的端口

    class BoomPipe:
        def get(self, *a): ...
        def incr(self, *a): ...
        def expire(self, *a): ...
        async def execute(self):
            import redis.exceptions

            raise redis.exceptions.ConnectionError("down")

    class BoomRedis:
        def pipeline(self):
            return BoomPipe()

        async def aclose(self):
            pass

    limiter._redis = BoomRedis()
    decision = await limiter.check(1, 10)
    assert decision.allowed is True  # fail-open:限流后端挂了不拖垮数据面
    await limiter.aclose()


# ---- admin:显式 null 防护 ----


async def test_patch_null_rejected(client):
    channel = await create_channel(client)
    resp = await client.patch(
        f"/admin/channels/{channel['id']}", json={"models": None}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 422

    vkey = await create_vkey(client)
    resp = await client.patch(
        f"/admin/keys/{vkey['id']}", json={"enabled": None}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 422
    # 可空列显式 null = 清空,允许
    resp = await client.patch(
        f"/admin/keys/{vkey['id']}", json={"monthly_budget_usd": None}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["monthly_budget_usd"] is None