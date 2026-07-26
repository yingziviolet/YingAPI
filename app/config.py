"""网关配置:全部环境变量以 GW_ 为前缀,支持 .env 文件。"""
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GW_", env_file=".env", extra="ignore")

    app_name: str = "llm-gateway"

    # 数据库:开发默认 SQLite,生产用 postgresql+asyncpg://...
    database_url: str = "sqlite+aiosqlite:///./gateway.db"
    # 开发便利:启动时自动建表;生产置 False 并用 alembic upgrade head
    auto_create_tables: bool = True

    # Fernet 密钥(base64,44 字符),用于渠道 API key 加密存储。
    # 为空时开发环境自动生成并持久化到 secret_key_file。
    secret_key: str = ""
    secret_key_file: str = ".gateway_secret"

    # 控制面管理 API 的 Bearer token
    admin_token: str = "change-me"
    # /metrics 抓取 token(可选):为空保持开放(仅本机/内网);对外部署应设置
    metrics_token: str = ""

    # 上游转发
    upstream_timeout_connect: float = 10.0
    upstream_timeout_read: float = 300.0
    upstream_timeout_write: float = 30.0
    # 上游连接池:SSE 长连接会占用连接直到生成结束,上限要按并发流量配
    upstream_max_connections: int = 1000
    upstream_max_keepalive: int = 100
    # 流式请求时向上游注入 stream_options.include_usage 以拿到 token 用量
    inject_stream_usage: bool = True

    # 精确匹配缓存
    cache_enabled: bool = True
    cache_ttl_seconds: int = 3600
    # 仅缓存 temperature 未指定或 <= 此值的请求(确定性意图)
    cache_max_temperature: float = 0.0

    # ---- P2 ----

    # 语义缓存:需要指定一个提供 /v1/embeddings 的渠道才会启用
    semantic_cache_enabled: bool = False
    embedding_channel: str = ""  # 渠道 name
    embedding_model: str = "text-embedding-3-small"
    semantic_threshold: float = 0.95  # 余弦相似度命中阈值
    semantic_max_candidates: int = 500  # 应用层余弦匹配的候选上限

    # 熔断器(进程内状态;多 worker 部署时各 worker 独立熔断,行为更保守可接受)
    cb_window_seconds: int = 60
    cb_min_requests: int = 10  # 窗口内至少这么多请求才评估错误率
    cb_error_threshold: float = 0.5  # 错误率 >= 阈值 -> OPEN
    cb_open_seconds: int = 30  # OPEN 冷却时间,到期进入 HALF_OPEN
    cb_half_open_probes: int = 3  # 半开状态放行的探测请求数

    # 滑动窗口限流(每虚拟 key 每分钟请求数;key 上可单独覆盖)
    default_rpm_limit: int | None = None  # None = 不限
    # Redis(可选):配置后限流用 Redis 实现,支持多 worker;为空用进程内实现(exe 单机模式)
    redis_url: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
