"""FastAPI 依赖:数据库会话、虚拟 key 鉴权(数据面)、管理 token 鉴权(控制面)。"""
import secrets
from typing import AsyncIterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import VirtualKey
from app.security import VIRTUAL_KEY_PREFIX, hash_virtual_key


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def _bearer_token(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return None


async def get_virtual_key(
    request: Request, session: AsyncSession = Depends(get_session)
) -> VirtualKey:
    """数据面鉴权:Authorization: Bearer sk-gw-xxx。失败返回 OpenAI 风格 401。"""
    token = _bearer_token(request)
    if not token or not token.startswith(VIRTUAL_KEY_PREFIX):
        raise HTTPException(
            status_code=401,
            detail={
                "message": "missing or malformed API key, expected 'Authorization: Bearer sk-gw-...'",
                "type": "invalid_request_error",
                "code": "invalid_api_key",
            },
        )
    result = await session.execute(
        select(VirtualKey).where(VirtualKey.key_hash == hash_virtual_key(token))
    )
    vkey = result.scalar_one_or_none()
    if vkey is None or not vkey.enabled:
        raise HTTPException(
            status_code=401,
            detail={
                "message": "invalid or disabled API key",
                "type": "invalid_request_error",
                "code": "invalid_api_key",
            },
        )
    return vkey


async def require_admin(request: Request) -> None:
    """控制面鉴权:Bearer <GW_ADMIN_TOKEN> 或 X-Admin-Token 头。"""
    settings = request.app.state.settings
    token = _bearer_token(request) or request.headers.get("x-admin-token")
    if not token or not secrets.compare_digest(
        token.encode("utf-8"), settings.admin_token.encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="admin token required")
