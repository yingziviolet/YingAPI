"""虚拟 key 鉴权与管理 token 鉴权。"""
from tests.conftest import ADMIN_HEADERS, create_channel, create_vkey, key_headers

CHAT_BODY = {"model": "m-large", "messages": [{"role": "user", "content": "hi"}]}


async def test_missing_key_rejected(client):
    resp = await client.post("/v1/chat/completions", json=CHAT_BODY)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_api_key"


async def test_bad_key_rejected(client):
    resp = await client.post(
        "/v1/chat/completions", json=CHAT_BODY, headers=key_headers("sk-gw-not-a-real-key")
    )
    assert resp.status_code == 401


async def test_disabled_key_rejected(client):
    await create_channel(client)
    created = await create_vkey(client)
    patch = await client.patch(
        f"/admin/keys/{created['id']}", json={"enabled": False}, headers=ADMIN_HEADERS
    )
    assert patch.status_code == 200
    resp = await client.post(
        "/v1/chat/completions", json=CHAT_BODY, headers=key_headers(created["key"])
    )
    assert resp.status_code == 401


async def test_admin_requires_token(client):
    resp = await client.get("/admin/channels")
    assert resp.status_code == 401
    resp = await client.get("/admin/channels", headers={"Authorization": "Bearer wrong"})
    assert resp.status_code == 401


async def test_healthz_open(client):
    resp = await client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
