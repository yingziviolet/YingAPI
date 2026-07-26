"""控制面:渠道 CRUD(密钥加密不回显)、虚拟 key 生命周期、统计聚合、预算拒绝。"""
from sqlalchemy import select

from app.models import Channel
from tests.conftest import ADMIN_HEADERS, create_channel, create_vkey, fetch_logs, key_headers

CHAT_BODY = {"model": "m-large", "messages": [{"role": "user", "content": "hi"}]}


async def test_channel_crud_and_key_encryption(client, gateway):
    created = await create_channel(client)
    assert "api_key" not in created and "api_key_encrypted" not in created

    # 数据库里存的是密文,且能解密回原文
    async with gateway.state.sessionmaker() as session:
        channel = (
            await session.execute(select(Channel).where(Channel.id == created["id"]))
        ).scalar_one()
        assert channel.api_key_encrypted != "sk-real-key-a"
        from app.security import decrypt_api_key

        assert decrypt_api_key(gateway.state.fernet, channel.api_key_encrypted) == "sk-real-key-a"

    # 更新
    resp = await client.patch(
        f"/admin/channels/{created['id']}",
        json={"priority": 5, "models": ["m-large", "m-extra"]},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    assert resp.json()["priority"] == 5
    assert resp.json()["models"] == ["m-large", "m-extra"]

    # 重名冲突
    resp = await client.post(
        "/admin/channels",
        json={"name": "chan-a", "base_url": "http://x/v1", "api_key": "k"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 409

    # 删除
    resp = await client.delete(f"/admin/channels/{created['id']}", headers=ADMIN_HEADERS)
    assert resp.status_code == 204
    resp = await client.get(f"/admin/channels/{created['id']}", headers=ADMIN_HEADERS)
    assert resp.status_code == 404


async def test_vkey_lifecycle(client):
    created = await create_vkey(client, name="ide-key")
    assert created["key"].startswith("sk-gw-")
    assert "****" in created["key_masked"]

    listed = (await client.get("/admin/keys", headers=ADMIN_HEADERS)).json()
    assert len(listed) == 1
    assert "key" not in listed[0]  # 原文不再出现
    assert listed[0]["key_masked"] == created["key_masked"]


async def test_budget_exhausted_rejects(client, gateway):
    await create_channel(client)
    # 预算设成 0:第一笔真实成本落库后立即触发;先发一笔(此时花费 0 未超)
    vkey = await create_vkey(client, name="budget-key", monthly_budget_usd=0.000001)
    headers = key_headers(vkey["key"])
    r1 = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
    assert r1.status_code == 200
    await gateway.state.meter.drain()  # 等成本落库

    r2 = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
    assert r2.status_code == 429
    assert r2.json()["error"]["code"] == "budget_exhausted"


async def test_stats_aggregation(client, gateway, upstream_state):
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])
    deterministic = {**CHAT_BODY, "temperature": 0}
    await client.post("/v1/chat/completions", json=deterministic, headers=headers)
    await client.post("/v1/chat/completions", json=deterministic, headers=headers)  # cache hit
    upstream_state["mode"] = "fail-500"
    await client.post(
        "/v1/chat/completions",
        json={"model": "m-large", "messages": [{"role": "user", "content": "other"}]},
        headers=headers,
    )
    await gateway.state.meter.drain()

    overview = (await client.get("/admin/stats/overview", headers=ADMIN_HEADERS)).json()
    assert overview["requests"] == 3
    assert overview["cache_hits"] == 1
    assert overview["errors"] == 1
    assert overview["cache_hit_rate"] == round(1 / 3, 4)
    assert overview["prompt_tokens"] > 0

    by_channel = (await client.get("/admin/stats/channels", headers=ADMIN_HEADERS)).json()
    names = {row["channel_name"]: row["requests"] for row in by_channel}
    assert names["chan-a"] == 1  # 一次真实转发
    assert names["(cache)"] == 1  # 缓存命中
    assert names["(none)"] == 1  # 全渠道失败的错误请求,不能混进 "(cache)"

    by_model = (await client.get("/admin/stats/models", headers=ADMIN_HEADERS)).json()
    assert by_model[0]["model"] == "m-large"
    assert by_model[0]["requests"] == 3

    daily = (await client.get("/admin/stats/daily", headers=ADMIN_HEADERS)).json()
    assert len(daily) == 1
    assert daily[0]["requests"] == 3

    spend = (
        await client.get(f"/admin/keys/{vkey['id']}/spend", headers=ADMIN_HEADERS)
    ).json()
    assert spend["month_to_date_usd"] > 0


async def test_logs_pagination(client, gateway):
    await create_channel(client)
    vkey = await create_vkey(client)
    headers = key_headers(vkey["key"])
    for _ in range(3):
        await client.post("/v1/chat/completions", json=CHAT_BODY, headers=headers)
    logs = await fetch_logs(client, gateway)
    assert len(logs) == 3
    page = await client.get("/admin/logs?limit=2&offset=1", headers=ADMIN_HEADERS)
    assert len(page.json()) == 2
