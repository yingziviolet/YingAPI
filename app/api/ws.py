"""控制台 WebSocket:实时请求流 live-tail。

浏览器 WebSocket 无法自定义 header,token 走查询参数(仅本机/内网控制台场景;
公网部署应放在反代 TLS 后面)。
"""
import json
import secrets

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.websocket("/admin/ws/livetail")
async def livetail_ws(websocket: WebSocket):
    settings = websocket.app.state.settings
    token = websocket.query_params.get("token", "")
    if not token or not secrets.compare_digest(
        token.encode("utf-8"), settings.admin_token.encode("utf-8")
    ):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    hub = websocket.app.state.livetail
    queue = hub.subscribe()
    try:
        while True:
            event = await queue.get()
            await websocket.send_text(json.dumps(event, ensure_ascii=False, default=str))
    except WebSocketDisconnect:
        pass
    finally:
        hub.unsubscribe(queue)
