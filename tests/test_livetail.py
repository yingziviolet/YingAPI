"""P3:live-tail 发布/订阅与 WebSocket 端点。"""
import json

import pytest
from cryptography.fernet import Fernet
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.config import Settings
from app.main import create_app
from app.services.livetail import LiveTailHub


def test_hub_pubsub_and_slow_consumer():
    hub = LiveTailHub(queue_size=2)
    q1 = hub.subscribe()
    q2 = hub.subscribe()
    assert hub.subscriber_count == 2

    hub.publish({"model": "m", "status": "ok"})
    assert q1.get_nowait()["model"] == "m"
    assert q2.get_nowait()["status"] == "ok"

    # 慢消费者:队列满后丢弃,不阻塞
    for i in range(5):
        hub.publish({"i": i})
    assert q1.qsize() == 2

    hub.unsubscribe(q1)
    assert hub.subscriber_count == 1


async def test_meter_publishes_to_hub(gateway, client):
    """计量落库的同时旁路推送到 live-tail。"""
    from tests.conftest import create_channel, create_vkey, key_headers

    await create_channel(client)
    vkey = await create_vkey(client)
    queue = gateway.state.livetail.subscribe()
    resp = await client.post(
        "/v1/chat/completions",
        json={"model": "m-large", "messages": [{"role": "user", "content": "hi"}]},
        headers=key_headers(vkey["key"]),
    )
    assert resp.status_code == 200
    event = queue.get_nowait()
    assert event["model"] == "m-large"
    assert event["status"] == "ok"
    assert "ts" in event
    gateway.state.livetail.unsubscribe(queue)


def _make_app(tmp_path):
    settings = Settings(
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'ws.db').as_posix()}",
        admin_token="ws-token",
        secret_key=Fernet.generate_key().decode(),
    )
    return create_app(settings)


def test_ws_livetail_receives_metering(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as tc:
        # 建渠道(指向打不通的地址)+ key,请求失败也会计量 -> live-tail 收到 error 事件
        admin = {"Authorization": "Bearer ws-token"}
        ch = tc.post(
            "/admin/channels",
            json={"name": "dead", "base_url": "http://127.0.0.1:9/v1", "api_key": "k", "models": ["m"]},
            headers=admin,
        )
        assert ch.status_code == 201
        key = tc.post("/admin/keys", json={"name": "k1"}, headers=admin).json()["key"]

        with tc.websocket_connect("/admin/ws/livetail?token=ws-token") as ws:
            resp = tc.post(
                "/v1/chat/completions",
                json={"model": "m", "messages": [{"role": "user", "content": "hi"}]},
                headers={"Authorization": f"Bearer {key}"},
            )
            assert resp.status_code == 502
            event = json.loads(ws.receive_text())
            assert event["status"] == "error"
            assert event["model"] == "m"


def test_ws_livetail_rejects_bad_token(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as tc:
        with pytest.raises(WebSocketDisconnect):
            with tc.websocket_connect("/admin/ws/livetail?token=wrong") as ws:
                ws.receive_text()