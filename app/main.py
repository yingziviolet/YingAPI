"""应用装配:app 工厂 + 生命周期(引擎/HTTP 连接池/计量器挂 app.state)。"""
import asyncio
import logging
import secrets
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response

from pathlib import Path

from app.api import admin as admin_api
from app.api import anthropic as anthropic_api
from app.api import v1 as v1_api
from app.api import ws as ws_api
from app.config import Settings, get_settings
from app.db import build_engine, build_sessionmaker, create_all
from app.metrics import Metrics
from app.security import load_fernet
from app.services.breaker import CircuitBreaker
from app.services.livetail import LiveTailHub
from app.services.ratelimit import build_rate_limiter
from app.services.semantic_cache import SemanticCache
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
    app.state.livetail = LiveTailHub()
    app.state.meter = Meter(app.state.sessionmaker, on_record=app.state.livetail.publish)
    app.state.breaker = CircuitBreaker(settings)
    app.state.rate_limiter = build_rate_limiter(settings)
    app.state.metrics = Metrics()
    # client_getter 延迟解引用:测试会整体替换 upstream_client
    app.state.semantic_cache = SemanticCache(
        settings, lambda: app.state.upstream_client, app.state.fernet
    )


async def dispose_state(app: FastAPI) -> None:
    await app.state.meter.drain()  # 排干在途计量,保证账不丢
    await app.state.rate_limiter.aclose()
    await app.state.upstream_client.aclose()
    await app.state.engine.dispose()


async def _cache_purge_loop(app: FastAPI, settings: Settings) -> None:
    """定期清理两层缓存的过期条目(store 只增,不清会无限增长)。"""
    from sqlalchemy import delete

    from app.models import CacheEntry, utcnow

    interval = max(300, settings.cache_ttl_seconds)
    while True:
        try:
            async with app.state.sessionmaker() as session:
                removed_semantic = await app.state.semantic_cache.purge_expired(session)
                result = await session.execute(
                    delete(CacheEntry).where(CacheEntry.expires_at <= utcnow())
                )
                await session.commit()
                removed_exact = result.rowcount or 0
            if removed_semantic or removed_exact:
                logging.getLogger("gateway.cache").info(
                    "purged expired cache entries: exact=%d semantic=%d",
                    removed_exact,
                    removed_semantic,
                )
        except Exception:
            logging.getLogger("gateway.cache").exception("cache purge failed")
        await asyncio.sleep(interval)


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
        purge_task = asyncio.create_task(_cache_purge_loop(app, settings))
        sentinel_task = None
        if settings.sentinel_interval_seconds > 0:
            from app.services.sentinel import Sentinel

            app.state.sentinel = Sentinel(
                settings, app.state.sessionmaker, app.state.breaker, app.state.upstream_client
            )
            sentinel_task = asyncio.create_task(app.state.sentinel.run_forever())
        yield
        for task in (purge_task, sentinel_task):
            if task is None:
                continue
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        await dispose_state(app)
        app.state.engine = None

    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.state.engine = None
    init_state(app, settings)
    app.add_middleware(TraceIdMiddleware)
    app.include_router(v1_api.router)
    app.include_router(anthropic_api.router)
    app.include_router(admin_api.router)
    app.include_router(ws_api.router)

    # React 控制台构建产物(存在才挂载:后端可独立运行)
    console_dist = (
        Path(settings.console_dir)
        if settings.console_dir
        else Path(__file__).resolve().parent.parent / "console" / "dist"
    )
    if console_dist.exists():
        from fastapi.responses import RedirectResponse
        from fastapi.staticfiles import StaticFiles

        app.mount("/console", StaticFiles(directory=str(console_dist), html=True), name="console")

        @app.get("/", include_in_schema=False)
        async def index():
            return RedirectResponse("/console/")

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        # 数据面错误格式按协议区分:/v1/messages* 用 Anthropic 格式,
        # 其余 /v1 用 OpenAI 格式;控制面保持 FastAPI 默认 {"detail": ...}
        path = request.url.path
        if path.startswith("/v1/messages"):
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            err_type = "authentication_error" if exc.status_code == 401 else "invalid_request_error"
            return JSONResponse(
                status_code=exc.status_code,
                content={
                    "type": "error",
                    "error": {"type": err_type, "message": detail.get("message", "error")},
                },
            )
        if path.startswith("/v1"):
            detail = exc.detail if isinstance(exc.detail, dict) else {"message": str(exc.detail)}
            return JSONResponse(status_code=exc.status_code, content={"error": detail})
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok", "app": settings.app_name}

    # 注意路径不能挂在 /console 下:那里被 StaticFiles mount 整段接管,会 404
    @app.get("/bootstrap")
    async def console_bootstrap(request: Request):
        """本地免登录:回环地址访问时把 admin token 交给控制台,省去手工输入。

        单机 exe / 本地开发的默认体验。对外部署把 GW_LOCAL_AUTO_AUTH 置 false 即可关闭;
        非回环来源一律拒绝(反代场景请自行确保不透传伪造的 client host)。
        """
        client_host = request.client.host if request.client else ""
        if not settings.local_auto_auth or client_host not in ("127.0.0.1", "::1", "localhost"):
            return JSONResponse({"auto": False}, status_code=403)
        return {"auto": True, "token": settings.admin_token}

    @app.get("/metrics")
    async def metrics_endpoint(request: Request):
        from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
        from sqlalchemy import select

        from app.metrics import update_circuit_gauges
        from app.models import Channel as ChannelModel

        # 可选抓取鉴权(GW_METRICS_TOKEN):指标含渠道名/key 名/累计花费,对外部署应设置
        if settings.metrics_token:
            auth = request.headers.get("authorization", "")
            token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
            if not token or not secrets.compare_digest(
                token.encode("utf-8"), settings.metrics_token.encode("utf-8")
            ):
                raise HTTPException(status_code=401, detail="metrics token required")

        # 抓取时刷新熔断状态 gauge(拉模型,状态即时)
        async with app.state.sessionmaker() as session:
            rows = (await session.execute(select(ChannelModel.id, ChannelModel.name))).all()
        names = {cid: name for cid, name in rows}
        update_circuit_gauges(app.state.metrics, app.state.breaker.snapshot(), names)
        return Response(
            content=generate_latest(app.state.metrics.registry), media_type=CONTENT_TYPE_LATEST
        )

    return app


app = create_app()
