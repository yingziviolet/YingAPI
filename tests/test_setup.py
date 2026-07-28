"""开箱引导:一步完成首次配置。"""
from tests.conftest import ADMIN_HEADERS, create_channel, create_vkey, key_headers


async def test_setup_state_before_and_after(client):
    state = (await client.get("/admin/setup/state", headers=ADMIN_HEADERS)).json()
    assert state["configured"] is False
    assert state["channels"] == 0 and state["keys"] == 0

    await create_channel(client)
    await create_vkey(client)
    state2 = (await client.get("/admin/setup/state", headers=ADMIN_HEADERS)).json()
    assert state2["configured"] is True


async def test_quickstart_creates_channel_and_key(client, upstream_state):
    """粘一个 key -> 建渠道 + 发虚拟 key,返回可直接用的配置。"""
    resp = await client.post(
        "/admin/setup/quickstart",
        json={
            "api_key": "sk-my-real-key",
            "base_url": "http://up-a/v1",
            "models": ["m-large"],
            "name": "first",
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["channel"]["name"] == "first"
    assert data["key"]["key"].startswith("sk-gw-")
    assert data["base_url"].endswith("/v1")
    assert "anthropic_base_url" in data

    # 返回的虚拟 key 立刻可用
    r = await client.post(
        "/v1/chat/completions",
        json={"model": "m-large", "messages": [{"role": "user", "content": "hi"}]},
        headers=key_headers(data["key"]["key"]),
    )
    assert r.status_code == 200
    # 上游收到的是解密后的真 key
    assert upstream_state["auth_by_host"]["up-a"] == "Bearer sk-my-real-key"

    state = (await client.get("/admin/setup/state", headers=ADMIN_HEADERS)).json()
    assert state["configured"] is True


async def test_quickstart_rejects_bad_key(client, upstream_state):
    """上游鉴权失败时不建渠道,直接告诉用户 key 不对。"""
    upstream_state["mode"] = "fail-401"
    resp = await client.post(
        "/admin/setup/quickstart",
        json={"api_key": "sk-wrong", "base_url": "http://up-a/v1", "models": ["m-large"]},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 400
    assert "鉴权失败" in resp.json()["detail"]
    channels = (await client.get("/admin/channels", headers=ADMIN_HEADERS)).json()
    assert channels == []


async def test_quickstart_needs_base_url_for_unknown_key(client):
    resp = await client.post(
        "/admin/setup/quickstart", json={"api_key": "totally-unknown-format"}, headers=ADMIN_HEADERS
    )
    assert resp.status_code == 400
    assert "Base URL" in resp.json()["detail"]


async def test_quickstart_dedupes_names(client, upstream_state):
    body = {"api_key": "sk-a", "base_url": "http://up-a/v1", "models": ["m-large"], "name": "dup"}
    first = (await client.post("/admin/setup/quickstart", json=body, headers=ADMIN_HEADERS)).json()
    second = (await client.post("/admin/setup/quickstart", json=body, headers=ADMIN_HEADERS)).json()
    assert first["channel"]["name"] == "dup"
    assert second["channel"]["name"] == "dup-2"
    assert first["key"]["name"] == "default"
    assert second["key"]["name"] == "default-2"


async def test_quickstart_can_skip_verify(client):
    """verify=false 时不联网直接建(离线配置场景)。"""
    resp = await client.post(
        "/admin/setup/quickstart",
        json={
            "api_key": "sk-offline",
            "base_url": "http://unreachable.invalid/v1",
            "models": ["m1"],
            "verify": False,
        },
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
