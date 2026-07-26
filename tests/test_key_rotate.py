"""虚拟 key 轮换与备注(卡片视图所需的后端能力)。"""
from tests.conftest import ADMIN_HEADERS, create_channel, create_vkey, key_headers

CHAT_BODY = {"model": "m-large", "messages": [{"role": "user", "content": "hi"}]}


async def test_rotate_invalidates_old_key(client, gateway):
    await create_channel(client)
    created = await create_vkey(client, name="rotate-me")
    old_headers = key_headers(created["key"])

    r = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=old_headers)
    assert r.status_code == 200

    rotated = await client.post(f"/admin/keys/{created['id']}/rotate", headers=ADMIN_HEADERS)
    assert rotated.status_code == 200
    data = rotated.json()
    assert data["key"] != created["key"]
    assert data["key"].startswith("sk-gw-")
    assert data["rotated_count"] == 1
    assert data["key_masked"] != created["key_masked"]

    # 旧 key 立即失效
    r_old = await client.post("/v1/chat/completions", json=CHAT_BODY, headers=old_headers)
    assert r_old.status_code == 401
    # 新 key 可用
    r_new = await client.post(
        "/v1/chat/completions", json=CHAT_BODY, headers=key_headers(data["key"])
    )
    assert r_new.status_code == 200


async def test_rotate_preserves_budget_and_history(client, gateway):
    await create_channel(client)
    created = await create_vkey(client, name="keep-budget", monthly_budget_usd=5.0)
    await client.post(
        "/v1/chat/completions", json=CHAT_BODY, headers=key_headers(created["key"])
    )
    await gateway.state.meter.drain()
    spend_before = (
        await client.get(f"/admin/keys/{created['id']}/spend", headers=ADMIN_HEADERS)
    ).json()["month_to_date_usd"]

    rotated = (
        await client.post(f"/admin/keys/{created['id']}/rotate", headers=ADMIN_HEADERS)
    ).json()
    assert rotated["monthly_budget_usd"] == 5.0
    spend_after = (
        await client.get(f"/admin/keys/{created['id']}/spend", headers=ADMIN_HEADERS)
    ).json()["month_to_date_usd"]
    assert spend_after == spend_before  # 计量历史保留


async def test_rotate_count_accumulates(client):
    created = await create_vkey(client, name="counted")
    for expected in (1, 2, 3):
        res = await client.post(f"/admin/keys/{created['id']}/rotate", headers=ADMIN_HEADERS)
        assert res.json()["rotated_count"] == expected


async def test_rotate_unknown_key_404(client):
    res = await client.post("/admin/keys/9999/rotate", headers=ADMIN_HEADERS)
    assert res.status_code == 404


async def test_note_create_update_clear(client):
    created = await create_vkey(client, name="noted", note="给记账 agent 用")
    assert created["note"] == "给记账 agent 用"

    updated = await client.patch(
        f"/admin/keys/{created['id']}", json={"note": "改成 IDE 用"}, headers=ADMIN_HEADERS
    )
    assert updated.json()["note"] == "改成 IDE 用"

    cleared = await client.patch(
        f"/admin/keys/{created['id']}", json={"note": None}, headers=ADMIN_HEADERS
    )
    assert cleared.json()["note"] is None
