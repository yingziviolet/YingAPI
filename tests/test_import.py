"""批量导入(API Key / 历史账单)与本地免登录。"""
import json

from cryptography.fernet import Fernet
from starlette.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.importer import mask, parse_billing, parse_keys
from tests.conftest import ADMIN_HEADERS


# ---------- key 解析 ----------


def test_parse_plain_text_keys():
    text = "sk-abcdefghijklmnopqrstuvwxyz123456789\n# 注释行\nsk-zyxwvutsrqponmlkjihgfedcba987654321\n"
    items = parse_keys(text)
    assert len(items) == 2
    assert all(i["api_key"].startswith("sk-") for i in items)
    assert all("****" in i["api_key_masked"] for i in items)
    # 掩码不泄漏中段
    assert items[0]["api_key"] not in items[0]["api_key_masked"]


def test_parse_json_keys_with_fields():
    payload = json.dumps([
        {"name": "my-deepseek", "api_key": "sk-1234567890abcdef1234", "base_url": "https://x/v1",
         "models": ["m1"], "prices": {"m1": {"input": 1, "output": 2}}},
        {"key": "sk-fedcba0987654321fedc"},
    ])
    items = parse_keys(payload)
    assert len(items) == 2
    assert items[0]["name"] == "my-deepseek"
    assert items[0]["base_url"] == "https://x/v1"
    assert items[0]["models"] == ["m1"]
    assert items[1]["name"]  # 自动生成名称


def test_parse_csv_keys():
    csv_text = "name,api_key,base_url,models\nch1,sk-aaaaaaaaaaaaaaaaaaaa,https://a/v1,m1|m2\n"
    items = parse_keys(csv_text)
    assert len(items) == 1
    assert items[0]["name"] == "ch1"
    assert items[0]["models"] == ["m1", "m2"]


def test_provider_detection_prefills():
    """能识别的厂商自动预填 base_url 与价格表。"""
    items = parse_keys("sk-ant-api03-xxxxxxxxxxxxxxxxxxxxxx")
    assert items[0]["provider"] == "anthropic"
    assert "anthropic" in items[0]["base_url"]

    openai = parse_keys("sk-proj-abcdefghijklmnopqrstuvwx")
    assert openai[0]["provider"] == "openai"
    assert openai[0]["prices"]  # 预填了价格


def test_parse_empty_and_garbage():
    assert parse_keys("") == []
    assert parse_keys("   ") == []
    assert parse_keys("这里没有任何密钥") == []


def test_mask_never_reveals_middle():
    key = "sk-abcdefghijklmnopqrstuvwxyz"
    masked = mask(key)
    assert masked.startswith("sk-abcde")
    assert masked.endswith(key[-4:])
    assert "fghijklmnopqrstuv" not in masked


# ---------- 账单解析 ----------


def test_parse_billing_csv():
    csv_text = (
        "date,model,requests,input_tokens,output_tokens,cost\n"
        "2026-06-01,deepseek-chat,120,45000,8000,0.32\n"
        "2026-06-02,deepseek-chat,98,38000,6500,0.27\n"
    )
    rows = parse_billing(csv_text)
    assert len(rows) == 2
    assert rows[0]["model"] == "deepseek-chat"
    assert rows[0]["requests"] == 120
    assert rows[0]["cost_usd"] == 0.32
    assert rows[0]["created_at"].year == 2026


def test_parse_billing_chinese_columns():
    csv_text = "日期,模型,次数,消费\n2026-06-01,glm-4,50,1.5\n"
    rows = parse_billing(csv_text)
    assert len(rows) == 1
    assert rows[0]["requests"] == 50
    assert rows[0]["cost_usd"] == 1.5


def test_parse_billing_json():
    payload = json.dumps({"data": [
        {"date": "2026-06-03", "model": "gpt-4o", "cost": 1.25, "requests": 10},
    ]})
    rows = parse_billing(payload)
    assert len(rows) == 1
    assert rows[0]["cost_usd"] == 1.25


def test_parse_billing_skips_rows_without_date():
    rows = parse_billing("model,cost\ngpt-4o,1.0\n")
    assert rows == []


# ---------- 端点 ----------


async def test_import_preview_endpoint(client):
    resp = await client.post(
        "/admin/import/keys/preview",
        json={"text": "sk-aaaaaaaaaaaaaaaaaaaaaa\nsk-bbbbbbbbbbbbbbbbbbbbbb"},
        headers=ADMIN_HEADERS,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 2
    # 预览不落库
    channels = (await client.get("/admin/channels", headers=ADMIN_HEADERS)).json()
    assert channels == []


async def test_import_keys_creates_channels(client):
    items = [
        {"api_key": "sk-key-one", "name": "imported-a", "base_url": "http://up-a/v1",
         "models": ["m-large"], "prices": {"m-large": {"input": 1, "output": 2}}},
        {"api_key": "sk-key-two", "name": "imported-b", "base_url": "http://up-b/v1", "models": []},
    ]
    resp = await client.post("/admin/import/keys", json={"items": items}, headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    result = resp.json()
    assert len(result["created"]) == 2
    channels = (await client.get("/admin/channels", headers=ADMIN_HEADERS)).json()
    names = {c["name"] for c in channels}
    assert names == {"imported-a", "imported-b"}
    # key 加密落库,不回显
    assert all("api_key" not in c for c in channels)


async def test_import_keys_dedupes_names(client):
    items = [
        {"api_key": "sk-1", "name": "dup", "base_url": "http://x/v1"},
        {"api_key": "sk-2", "name": "dup", "base_url": "http://y/v1"},
    ]
    resp = await client.post("/admin/import/keys", json={"items": items}, headers=ADMIN_HEADERS)
    created = resp.json()["created"]
    assert [c["name"] for c in created] == ["dup", "dup-2"]


async def test_import_keys_skips_incomplete(client):
    items = [{"api_key": "", "name": "bad"}, {"api_key": "sk-ok", "name": "good", "base_url": "http://z/v1"}]
    result = (
        await client.post("/admin/import/keys", json={"items": items}, headers=ADMIN_HEADERS)
    ).json()
    assert len(result["created"]) == 1
    assert len(result["skipped"]) == 1


async def test_import_billing_merges_into_stats(client, gateway):
    csv_text = (
        "date,model,requests,input_tokens,output_tokens,cost\n"
        "2026-07-20,legacy-model,100,50000,9000,1.5\n"
        "2026-07-21,legacy-model,80,40000,7000,1.2\n"
    )
    resp = await client.post("/admin/import/billing", json={"text": csv_text}, headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["imported"] == 2
    assert abs(data["total_cost_usd"] - 2.7) < 1e-6
    trace = data["trace_id"]

    # 已并入统计
    models = (await client.get("/admin/stats/models?days=90", headers=ADMIN_HEADERS)).json()
    legacy = next(m for m in models if m["model"] == "legacy-model")
    assert abs(legacy["cost_usd"] - 2.7) < 1e-6

    # 可撤销
    undo = await client.delete(f"/admin/import/billing/{trace}", headers=ADMIN_HEADERS)
    assert undo.json()["deleted"] == 2
    models2 = (await client.get("/admin/stats/models?days=90", headers=ADMIN_HEADERS)).json()
    assert not any(m["model"] == "legacy-model" for m in models2)


async def test_import_billing_rejects_unparseable(client):
    resp = await client.post("/admin/import/billing", json={"text": "毫无结构的文本"}, headers=ADMIN_HEADERS)
    assert resp.json()["imported"] == 0


# ---------- 本地免登录 ----------


def _app(tmp_path, **overrides):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'auth.db').as_posix()}",
        admin_token="local-token",
        secret_key=Fernet.generate_key().decode(),
        sentinel_interval_seconds=0,
        **overrides,
    )
    return create_app(settings)


def test_bootstrap_gives_token_on_loopback(tmp_path):
    with TestClient(_app(tmp_path)) as tc:  # TestClient 的 client host 是 testclient
        resp = tc.get("/bootstrap")
        # TestClient 默认 host 不是回环名,应被拒绝;显式伪装成回环则放行
        assert resp.status_code == 403

    app = _app(tmp_path)
    with TestClient(app, client=("127.0.0.1", 5000)) as tc:
        resp = tc.get("/bootstrap")
        assert resp.status_code == 200
        assert resp.json() == {"auto": True, "token": "local-token"}


def test_bootstrap_disabled_by_config(tmp_path):
    app = _app(tmp_path, local_auto_auth=False)
    with TestClient(app, client=("127.0.0.1", 5000)) as tc:
        assert tc.get("/bootstrap").status_code == 403


def test_bootstrap_rejects_remote_client(tmp_path):
    app = _app(tmp_path)
    with TestClient(app, client=("203.0.113.7", 5000)) as tc:
        assert tc.get("/bootstrap").status_code == 403
