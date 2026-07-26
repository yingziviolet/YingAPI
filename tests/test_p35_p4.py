"""P3.5(告警哨兵/订阅面板)与 P4(难度感知路由)测试。"""
import json

import httpx
import pytest
from asgi_lifespan import LifespanManager
from cryptography.fernet import Fernet

from app.config import Settings
from app.main import create_app
from app.services.downgrade import downgrade_target
from tests.conftest import ADMIN_HEADERS, create_channel, create_vkey, fetch_logs, key_headers


# ---------- 难度感知路由:启发式判定单元测试 ----------


def _settings(**overrides):
    kwargs = {
        "admin_token": "x",
        "downgrade_enabled": True,
        "downgrade_map": {"m-large": "m-small"},
        "downgrade_max_chars": 100,
        "downgrade_max_messages": 3,
    }
    kwargs.update(overrides)
    return Settings(**kwargs)


def test_downgrade_simple_request():
    body = {"model": "m-large", "messages": [{"role": "user", "content": "hi"}]}
    assert downgrade_target(body, _settings()) == "m-small"


def test_downgrade_disabled_or_unmapped():
    body = {"model": "m-large", "messages": [{"role": "user", "content": "hi"}]}
    assert downgrade_target(body, _settings(downgrade_enabled=False)) is None
    assert downgrade_target({**body, "model": "other"}, _settings()) is None


def test_no_downgrade_on_complexity_signals():
    settings = _settings()
    base = {"model": "m-large", "messages": [{"role": "user", "content": "hi"}]}
    # 带工具
    assert downgrade_target({**base, "tools": [{}]}, settings) is None
    # 结构化输出
    assert downgrade_target({**base, "response_format": {"type": "json_object"}}, settings) is None
    # 长文本
    long_body = {"model": "m-large", "messages": [{"role": "user", "content": "x" * 200}]}
    assert downgrade_target(long_body, settings) is None
    # 含代码块
    code_body = {"model": "m-large", "messages": [{"role": "user", "content": "```py\nx\n```"}]}
    assert downgrade_target(code_body, settings) is None
    # 轮数过多
    many = {"model": "m-large", "messages": [{"role": "user", "content": "a"}] * 4}
    assert downgrade_target(many, settings) is None
    # 多模态
    img = {
        "model": "m-large",
        "messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}],
    }
    assert downgrade_target(img, settings) is None


# ---------- 难度感知路由:集成(独立 app,开启降级) ----------


@pytest.fixture
async def downgrade_gateway(tmp_path, upstream_state):
    from tests.conftest import make_upstream

    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'dg.db').as_posix()}",
        auto_create_tables=True,
        secret_key=Fernet.generate_key().decode(),
        admin_token="test-admin",
        downgrade_enabled=True,
        downgrade_map={"m-large": "m-small"},
        downgrade_max_chars=200,
        sentinel_interval_seconds=0,  # 测试不跑哨兵循环
    )
    app = create_app(settings)
    await app.state.upstream_client.aclose()
    app.state.upstream_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=make_upstream(upstream_state))
    )
    async with LifespanManager(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://gw.local"
        ) as client:
            yield app, client


async def test_downgrade_routes_to_cheaper_model(downgrade_gateway, upstream_state):
    app, client = downgrade_gateway
    # 渠道同时服务两个模型,价格不同
    await create_channel(
        client,
        models=["m-large", "m-small"],
        prices={"m-large": {"input": 10.0, "output": 30.0}, "m-small": {"input": 1.0, "output": 2.0}},
        model_map={"m-small": "upstream-small"},
    )
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])

    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "m-large", "messages": [{"role": "user", "content": "简单问题"}]},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.headers["X-Gateway-Downgraded"] == "m-small"
    # 上游收到的是降级模型(经渠道 model_map 改写)
    assert upstream_state["last_body"]["model"] == "upstream-small"
    # 响应 model 仍是客户端请求的对外名
    assert resp.json()["model"] == "m-large"

    logs = await fetch_logs(client, app)
    assert logs[0]["downgraded_to"] == "m-small"
    assert logs[0]["model"] == "m-large"
    # 成本按降级后的模型价格计:7 * 1.0/1M + 2 * 2.0/1M
    assert abs(logs[0]["cost_usd"] - (7 * 1.0 + 2 * 2.0) / 1_000_000) < 1e-12

    overview = (await client.get("/admin/stats/overview", headers=ADMIN_HEADERS)).json()
    assert overview["downgraded"] == 1


async def test_complex_request_not_downgraded(downgrade_gateway, upstream_state):
    app, client = downgrade_gateway
    await create_channel(
        client,
        models=["m-large", "m-small"],
        prices={"m-large": {"input": 10.0, "output": 30.0}, "m-small": {"input": 1.0, "output": 2.0}},
    )
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "m-large", "messages": [{"role": "user", "content": "复杂" * 200}]},
        headers=key_headers(vkey["key"]),
    )
    assert resp.status_code == 200
    assert "X-Gateway-Downgraded" not in resp.headers
    assert upstream_state["last_body"]["model"] == "m-large"


async def test_downgrade_applies_to_anthropic_entry(downgrade_gateway, upstream_state):
    app, client = downgrade_gateway
    await create_channel(
        client,
        models=["m-large", "m-small"],
        prices={"m-large": {"input": 10.0, "output": 30.0}, "m-small": {"input": 1.0, "output": 2.0}},
    )
    vkey = await create_vkey(client)
    resp = await client.post(
        "/v1/messages",
        json={"model": "m-large", "max_tokens": 64, "messages": [{"role": "user", "content": "hi"}]},
        headers={"x-api-key": vkey["key"]},
    )
    assert resp.status_code == 200
    assert resp.headers["X-Gateway-Downgraded"] == "m-small"
    assert resp.json()["model"] == "m-large"


# ---------- 哨兵与告警 ----------


async def test_sentinel_budget_and_breaker_alerts(client, gateway, upstream_state):
    from app.services.sentinel import Sentinel

    await create_channel(client)
    vkey = await create_vkey(client, name="tiny-budget", monthly_budget_usd=0.000001)
    headers = key_headers(vkey["key"])
    # 一笔真实消费,超过预算
    r = await client.post(
        "/v1/chat/completions",
        json={"model": "m-large", "messages": [{"role": "user", "content": "hi"}]},
        headers=headers,
    )
    assert r.status_code == 200
    await gateway.state.meter.drain()

    # 两次失败触发熔断(用另一个无预算的 key:tiny-budget 此刻已超支会被 429 拦在数据面之前)
    other = await create_vkey(client, name="no-budget")
    upstream_state["mode"] = "fail-500"
    for _ in range(2):
        r = await client.post(
            "/v1/chat/completions",
            json={"model": "m-large", "messages": [{"role": "user", "content": "hi"}]},
            headers=key_headers(other["key"]),
        )
        assert r.status_code == 502
    await gateway.state.meter.drain()

    sentinel = Sentinel(
        gateway.state.settings,
        gateway.state.sessionmaker,
        gateway.state.breaker,
        gateway.state.upstream_client,
    )
    await sentinel.run_once()

    alerts = (await client.get("/admin/alerts", headers=ADMIN_HEADERS)).json()
    kinds = {a["kind"] for a in alerts}
    assert "budget" in kinds
    assert "breaker" in kinds
    budget_alert = next(a for a in alerts if a["kind"] == "budget")
    assert "tiny-budget" in budget_alert["title"]
    assert budget_alert["severity"] == "critical"  # 100% 档

    # 再跑一轮:去重,不重复告警
    count_before = len(alerts)
    await sentinel.run_once()
    alerts2 = (await client.get("/admin/alerts", headers=ADMIN_HEADERS)).json()
    same_kind = [a for a in alerts2 if a["kind"] in ("budget", "breaker")]
    assert len(same_kind) == len([a for a in alerts if a["kind"] in ("budget", "breaker")])

    # 确认告警
    ack = await client.post(f"/admin/alerts/{budget_alert['id']}/ack", headers=ADMIN_HEADERS)
    assert ack.status_code == 200 and ack.json()["acknowledged"] is True
    remaining = (await client.get("/admin/alerts", headers=ADMIN_HEADERS)).json()
    assert budget_alert["id"] not in [a["id"] for a in remaining]


async def test_alert_webhook_push(gateway):
    """webhook 配置后,新告警 POST 出去;失败静默。"""
    from app.services.sentinel import create_alert

    received = []

    # 用 MockTransport 模拟 webhook 端点
    async def transport_handler(request):
        received.append(json.loads(request.content.decode()))
        return httpx.Response(200)

    client = httpx.AsyncClient(transport=httpx.MockTransport(transport_handler))
    created = await create_alert(
        gateway.state.sessionmaker,
        kind="budget",
        severity="warning",
        title="t",
        detail="d",
        dedupe_key="webhook-test-1",
        webhook_url="http://hook.local/alert",
        http_client=client,
    )
    assert created is True
    assert received[0]["title"] == "t"
    # 重复 dedupe_key:不再落库不再推送
    created2 = await create_alert(
        gateway.state.sessionmaker,
        kind="budget",
        severity="warning",
        title="t",
        detail="d",
        dedupe_key="webhook-test-1",
        webhook_url="http://hook.local/alert",
        http_client=client,
    )
    assert created2 is False
    assert len(received) == 1
    await client.aclose()


# ---------- 订阅用量扫描 ----------


def test_subscription_scan_parses_local_records(tmp_path, monkeypatch):
    from app.services import subscription

    fake_root = tmp_path / "projects" / "proj-a"
    fake_root.mkdir(parents=True)
    lines = [
        {"timestamp": "2026-07-26T10:00:00Z", "message": {"model": "claude-fable-5",
         "usage": {"input_tokens": 100, "output_tokens": 200, "cache_read_input_tokens": 1000,
                   "cache_creation_input_tokens": 50}}},
        {"timestamp": "2026-07-26T11:00:00Z", "message": {"model": "claude-haiku-4-5",
         "usage": {"input_tokens": 10, "output_tokens": 20}}},
        {"note": "no usage line"},
    ]
    (fake_root / "session.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines), encoding="utf-8"
    )
    monkeypatch.setattr(subscription, "claude_data_dir", lambda: tmp_path / "projects")

    settings = Settings(admin_token="x")
    # 时间窗按当前时间算;样例时间戳是过去固定值,用大窗口覆盖
    result = subscription.scan_usage(settings, days=36500)
    assert result["available"] is True
    assert result["totals"]["messages"] == 2
    assert result["totals"]["input_tokens"] == 110
    assert result["totals"]["output_tokens"] == 220
    by_model = {r["model"]: r for r in result["by_model"]}
    assert by_model["claude-fable-5"]["cache_read_tokens"] == 1000
    # fable: (100*10 + 200*50 + 1000*10*0.1 + 50*10*1.25)/1e6
    expected_fable = (100 * 10 + 200 * 50 + 1000 * 1.0 + 50 * 12.5) / 1_000_000
    assert abs(by_model["claude-fable-5"]["est_cost_usd"] - round(expected_fable, 4)) < 1e-4
    assert result["est_api_cost_usd"] > 0


def test_subscription_scan_missing_dir(tmp_path, monkeypatch):
    from app.services import subscription

    monkeypatch.setattr(subscription, "claude_data_dir", lambda: tmp_path / "nope")
    result = subscription.scan_usage(Settings(admin_token="x"), days=7)
    assert result["available"] is False


async def test_subscription_endpoint(client):
    resp = await client.get("/admin/subscription-usage?days=1", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert "available" in resp.json()