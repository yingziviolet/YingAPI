"""数据库引擎/会话工厂。资源挂在 app.state 上,便于测试注入与多进程部署。"""
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.config import Settings
from app.models import Base


def build_engine(settings: Settings) -> AsyncEngine:
    kwargs: dict = {"pool_pre_ping": True}
    if settings.database_url.startswith("sqlite"):
        kwargs = {}  # SQLite 不需要连接池预检
    elif settings.database_url.startswith("postgresql"):
        # 固定会话时区:date(timestamptz) 等按天截断的统计不受服务器时区影响,
        # 与 SQLite(按 UTC 存字符串)口径一致
        kwargs["connect_args"] = {"server_settings": {"timezone": "UTC"}}
    engine = create_async_engine(settings.database_url, **kwargs)
    if settings.database_url.startswith("sqlite"):
        # SQLite 默认不强制外键;打开以保持与 Postgres 一致的 ON DELETE 行为
        @event.listens_for(engine.sync_engine, "connect")
        def _enable_sqlite_fk(dbapi_conn, _record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine


def build_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def create_all(engine: AsyncEngine) -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
