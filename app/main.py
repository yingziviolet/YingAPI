"""应用装配:app 工厂 + 生命周期(引擎/HTTP 连接池/计量器挂 app.state)。"""
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api import admin as admin_api
from app.api import v1 as v1_api
from app.config import Settings, get_settings
from app.db import build_engine, build_sessionmaker, create_all
from app.security import load_fernet
from app.services.usage import Meter
from app.trace import TraceIdMiddleware

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


def init_state(app: FastAPI, settings: Settings) -> None:
    """初始化共享资源。独立成函数便于测试直接调用(不经 lifespan)。"""
    app.state.settings = settings
    app.state.fernet = load_fernet(settings)
    app.state.engine = build_engine(settings)
    app.state.sessionmaker = build_sessionmaker(app.state.engine)
    # 上游共享连接池:复用连接降低握手开销;测试时可整体替换为 ASGITransport
    # SSE 流会占用连接直到生成结束,max_connections 必须 >= 预期并发流数
    app.state.upstream_client = httpx.AsyncClient(
        limits=httpx.Limits(
            max_connections=settings.upstream_max_connections,
            max_keepalive_connections=settings.upstream_max_keepalive,
        )
    )
    app.state.meter = Meter(app.state.sessionmaker)


async def dispose_state(app: FastAPI) -> None:
    await app.state.meter.drain()  # 排干在途计量,保证账不丢
    await app.state.upstream_client.aclose()
    await app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # 控制面掌管全部渠道密钥,已知默认 token 等于裸奔:启动即拒绝
        if settings.admin_token in ("", "change-me"):
            raise RuntimeError(
                "GW_ADMIN_TOKEN is unset or still the default 'change-me'; "
                "set a strong random token (env or .env) before starting the gateway"
            )
        if getattr(app.state, "engine", None) is None:
            init_state(app, settings)
        if settings.auto_create_tables:
            await create_all(app.state.engine)
        yield
        await dispose_state(app)
        app.state.engine = None

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.engine = None
    init_state(app, settings)
    app.add_middleware(TraceIdMiddleware)
    app.include_router(v1_api.router)
    app.include_router(admin_api.router)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # 数据面(/v1)统一 OpenAI 错误格式;控制面保持 FastAPI 默认 {"detail": ...}
        if request.url.path.startswith("/v1"):
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            return JSONResponse(status_code=exc.status_code, content={"error": detail})
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "app": settings.app_name}

    return app


app = create_app()
