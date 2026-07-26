"""P2:熔断器、滑动窗口限流、语义缓存、Prometheus 指标。"""
import pytest

from app.config import Settings
from app.services.breaker import CircuitBreaker, CircuitState
from app.services.ratelimit import InMemoryRateLimiter
from tests.conftest import ADMIN_HEADERS, create_channel, create_vkey, fetch_logs, key_headers

CHAT_BODY = {"model": "m-large", "messages": [{"role": "user", "content": "hi"}]}


# ---------- 熔断器单元测试(注入假时钟,状态机逐步验证) ----------


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def make_breaker(**overrides):
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


def test_breaker_opens_on_error_rate():
    breaker, _ = make_breaker()
    # 3 次失败:还没到 min_requests,不熔断
    for _ in range(3):
        breaker.record_failure(1)
    assert breaker.state_of(1) == CircuitState.CLOSED
    # 第 4 次失败:错误率 100% >= 50%,熔断
    breaker.record_failure(1)
    assert breaker.state_of(1) == CircuitState.OPEN
    assert breaker.allow(1) is False


def test_breaker_respects_error_threshold():
    breaker, _ = make_breaker()
    # 4 次里 1 次失败 = 25% < 50%,不熔断
    breaker.record_failure(1)
    for _ in range(3):
        breaker.record_success(1)
    assert breaker.state_of(1) == CircuitState.CLOSED
    assert breaker.allow(1) is True


def test_breaker_half_open_probe_and_recovery():
    breaker, clock = make_breaker()
    for _ in range(4):
        breaker.record_failure(1)
    assert breaker.state_of(1) == CircuitState.OPEN

    clock.now += 29  # 冷却期内:仍拒绝
    assert breaker.allow(1) is False

    clock.now += 2  # 冷却期满:进入 HALF_OPEN,放行探测
    assert breaker.allow(1) is True
    assert breaker.state_of(1) == CircuitState.HALF_OPEN
    assert breaker.allow(1) is True  # 第二个探测(cb_half_open_probes=2)
    assert breaker.allow(1) is False  # 探测额度用完

    breaker.record_success(1)  # 探测成功:恢复
    assert breaker.state_of(1) == CircuitState.CLOSED
    assert breaker.allow(1) is True


def test_breaker_half_open_failure_reopens():
    breaker, clock = make_breaker()
    for _ in range(4):
        breaker.record_failure(1)
    clock.now += 31
    assert breaker.allow(1) is True  # HALF_OPEN 探测
    breaker.record_failure(1)  # 探测失败:重新熔断,冷却期重算
    assert breaker.state_of(1) == CircuitState.OPEN
    clock.now += 29
    assert breaker.allow(1) is False
    clock.now += 2
    assert breaker.allow(1) is True


def test_breaker_window_slides():
    breaker, clock = make_breaker()
    for _ in range(3):
        breaker.record_failure(1)
    clock.now += 61  # 旧失败滑出窗口
    breaker.record_failure(1)  # 窗口内只有 1 条,不到 min_requests
    assert breaker.state_of(1) == CircuitState.CLOSED


# ---------- 限流单元测试 ----------


async def test_rate_limiter_basic():
    clock = FakeClock()
    clock.now = 1200.0  # 分钟起点(1200 % 60 == 0,上一窗口权重 = 1)
    limiter = InMemoryRateLimiter(clock=clock)
    for _ in range(3):
        assert (await limiter.check(1, 3)).allowed
    assert (await limiter.check(1, 3)).allowed is False
    # 无限制的 key 不受影响
    assert (await limiter.check(2, None)).allowed

    # 下一分钟初:上一分钟计数按剩余占比加权,仍然被限
    clock.now = 1260.0
    assert (await limiter.check(1, 3)).allowed is False
    # 分钟过半:权重 0.5,3*0.5=1.5,+1 <= 3,放行
    clock.now = 1290.0
    assert (await limiter.check(1, 3)).allowed
    # 隔很久:窗口清零
    clock.now = 2000.0
    assert (await limiter.check(1, 3)).allowed


# ---------- 集成:限流 ----------


async def test_rpm_limit_enforced(client, gateway):
    await create_channel(client)
    vkey = await create_vkey(client, name="limited-key", rpm_limit=2)
    assert vkey["rpm_limit"] == 2
    headers = key_headers(vkey["key"])
    r1 = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
    r2 = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
    assert r1.status_code == 200 and r2.status_code == 200
    r3 = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
    assert r3.status_code == 429
    assert r3.json()["error"]["code"] == "rate_limit_exceeded"


# ---------- 集成:熔断 + 恢复 ----------


async def test_breaker_integration_open_and_recover(client, gateway, upstream_state):
    await create_channel(client)  # chan-a,conftest 里 cb_min_requests=2
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])

    upstream_state["mode"] = "fail-500"
    for _ in range(2):
        resp = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
        assert resp.status_code == 502

    # 熔断已打开:请求不再打上游,快速失败
    calls_before = upstream_state["calls"]
    resp = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
    assert resp.status_code == 502
    assert "circuit open" in resp.json()["error"]["message"]
    assert upstream_state["calls"] == calls_before  # 没打上游

    # 控制面可观测
    states = (await client.get("/admin/breakers", headers=ADMIN_HEADERS)).json()
    assert states[0]["channel_name"] == "chan-a"
    assert states[0]["state"] == "open"
    assert states[0]["opened_count"] == 1

    # 上游恢复 + 冷却期人为拨过:HALF_OPEN 探测成功后闭合
    upstream_state["mode"] = "ok"
    circuit = gateway.state.breaker._circuits[states[0]["channel_id"]]
    circuit.opened_at -= 31
    resp = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
    assert resp.status_code == 200
    states = (await client.get("/admin/breakers", headers=ADMIN_HEADERS)).json()
    assert states[0]["state"] == "closed"


async def test_breaker_manual_reset(client, gateway, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])
    upstream_state["mode"] = "fail-500"
    for _ in range(2):
        await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
    states = (await client.get("/admin/breakers", headers=ADMIN_HEADERS)).json()
    assert states[0]["state"] == "open"
    channel_id = states[0]["channel_id"]

    upstream_state["mode"] = "ok"
    resp = await client.post(f"/admin/breakers/{channel_id}/reset", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    resp = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
    assert resp.status_code == 200


async def test_breaker_failover_prefers_healthy_channel(client, upstream_state):
    """chan-a 熔断后,请求直接走 chan-b,不再浪费一次对 chan-a 的尝试。"""
    await create_channel(client, name="chan-a", base_url="http://up-a/v1", priority=10)
    await create_channel(
        client, name="chan-b", base_url="http://up-b/v1", api_key="sk-real-key-b", priority=20
    )
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])
    upstream_state["host_modes"] = {"up-a": "fail-500"}
    # 两次请求:每次 chan-a 失败 -> failover chan-b 成功;两次后 chan-a 熔断
    for _ in range(2):
        resp = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
        assert resp.status_code == 200
        assert resp.headers["X-Gateway-Channel"] == "chan-b"
    hosts_before = list(upstream_state["hosts_called"])
    resp = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
    assert resp.status_code == 200
    # 第三次请求 chan-a 已熔断,只打了 chan-b
    assert upstream_state["hosts_called"] == hosts_before + ["up-b"]


# ---------- 集成:语义缓存 ----------


async def make_embedder_channel(client):
    return await create_channel(
        client,
        name="embedder",
        base_url="http://embed-host/v1",
        api_key="sk-embed-key",
        models=[],
        prices={},
        priority=99,
    )


async def test_semantic_cache_hit(client, gateway, upstream_state):
    await create_channel(client)
    await make_embedder_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])

    q1 = "what is two plus two"
    q2 = "what's 2+2?"
    same_vec = [1.0, 0.0, 0.0, 0.0]
    upstream_state["embeddings"] = {f"user: {q1}": same_vec, f"user: {q2}": same_vec}

    body1 = {"model": "m-large", "messages": [{"role": "user", "content": q1}], "temperature": 0}
    r1 = await client.post("/v1/chat/completions", json=body1, headers=headers)
    assert r1.status_code == 200
    assert r1.headers["X-Gateway-Cache"] == "miss"
    assert upstream_state["calls"] == 1

    # 语义相同、字面不同:精确缓存必然 miss,语义缓存命中
    body2 = {"model": "m-large", "messages": [{"role": "user", "content": q2}], "temperature": 0}
    r2 = await client.post("/v1/chat/completions", json=body2, headers=headers)
    assert r2.status_code == 200
    assert r2.headers["X-Gateway-Cache"] == "semantic-hit"
    assert float(r2.headers["X-Gateway-Similarity"]) >= 0.9
    assert upstream_state["calls"] == 1  # 没打上游
    assert r2.json() == r1.json()

    logs = await fetch_logs(client, gateway)
    hit_log = next(log for log in logs if log["cache_hit"])
    assert hit_log["usage_source"] == "semantic-cache"

    cache_stats = (await client.get("/admin/stats/cache", headers=ADMIN_HEADERS)).json()
    assert cache_stats["semantic"]["entries"] == 1
    assert cache_stats["semantic"]["total_hits"] == 1


async def test_semantic_cache_miss_on_different_meaning(client, upstream_state):
    await create_channel(client)
    await make_embedder_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])

    upstream_state["embeddings"] = {
        "user: capital of france": [1.0, 0.0, 0.0, 0.0],
        "user: best pizza recipe": [0.0, 1.0, 0.0, 0.0],  # 正交:相似度 0
    }
    body1 = {
        "model": "m-large",
        "messages": [{"role": "user", "content": "capital of france"}],
        "temperature": 0,
    }
    body2 = {
        "model": "m-large",
        "messages": [{"role": "user", "content": "best pizza recipe"}],
        "temperature": 0,
    }
    await client.post("/v1/chat/completions", json=body1, headers=headers)
    r2 = await client.post("/v1/chat/completions", json=body2, headers=headers)
    assert r2.headers["X-Gateway-Cache"] == "miss"
    assert upstream_state["calls"] == 2


async def test_semantic_cache_degrades_without_embedder(client, upstream_state):
    """embedding 渠道不存在:语义缓存静默降级,主链路不受影响。"""
    await create_channel(client)  # 没有 embedder 渠道
    vkey = await create_vkey(client)
    body = {**CHAT_BODY, "temperature": 0}
    resp = await client.post("/v1/chat/completions", json=body, headers=key_headers(vkey["key"]))
    assert resp.status_code == 200
    assert upstream_state.get("embed_calls", 0) == 0


# ---------- Prometheus 指标 ----------


async def test_metrics_endpoint(client, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])
    deterministic = {**CHAT_BODY, "temperature": 0}
    await client.post("/v1/chat/completions", json=deterministic, headers=headers)
    await client.post("/v1/chat/completions", json=deterministic, headers=headers)  # exact hit

    resp = await client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert 'gateway_requests_total{cache="miss",channel="chan-a",model="m-large",status="ok"}' in text
    assert 'gateway_requests_total{cache="hit",channel="(cache)",model="m-large",status="cache_hit"}' in text
    assert "gateway_request_duration_seconds_bucket" in text
    assert 'gateway_tokens_total{channel="chan-a",direction="prompt",model="m-large"}' in text
    assert 'gateway_cache_events_total{kind="exact",outcome="hit"}' in text


async def test_rpm_limit_patchable(client):
    vkey = await create_vkey(client, name="patch-key")
    assert vkey["rpm_limit"] is None
    resp = await client.patch(
        f"/admin/keys/{vkey['id']}", json={"rpm_limit": 10}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 200
    assert resp.json()["rpm_limit"] == 10