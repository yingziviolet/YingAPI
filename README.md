# LLM 智能网关(数据面 + 控制面)

自研 LLM 网关基础设施:**数据面**(OpenAI 兼容异步流式转发、精确缓存、静态优先级路由 + failover、token 级计量)+ **控制面**(渠道管理、虚拟 key、用量统计 API;React 控制台在 P3)。

完整架构与路线图见 [智能网关-全栈架构.md](智能网关-全栈架构.md)。当前进度:**P1 完成**。

## 当前能力(P1)

- **OpenAI 兼容入口** `POST /v1/chat/completions`:非流式 + SSE 流式透传(chunk 到达即转发,不缓冲)
- **虚拟 key 体系**:每个客户端独立 `sk-gw-*` key(只存哈希),可配月度预算,超预算自动 429
- **渠道注册表**:多渠道 + 静态优先级路由,渠道故障(超时/401/403/429/5xx)自动 failover 下一渠道;4xx 请求错误原样透传不重试
- **密钥安全**:上游 API key Fernet 加密落库,永不回显
- **token 计量**:流式/非流式统一解析 usage(流式自动向上游注入 `stream_options.include_usage`,客户端无感知),按渠道价格表计成本,**异步落库不阻塞响应**
- **精确匹配缓存**:规范化请求 SHA-256,仅缓存 `temperature<=0` 的确定性请求,TTL 可配;流式请求命中后合成 SSE 回放
- **trace_id 全链路**:透传/生成 `X-Trace-Id`,贯穿日志与计量
- **控制面 API**:渠道 CRUD + 连通性测试、key 管理、统计聚合(总览/按渠道/按模型/按日)、请求日志(脱敏:只存元数据不存消息内容)

## 快速开始(本地开发,SQLite)

```bash
pip install -e ".[test]"
```

```bash
GW_ADMIN_TOKEN=$(openssl rand -hex 24) python -m uvicorn app.main:app --port 8080
```

> 管理 token 必须显式设置(留空或默认值 `change-me` 会启动即拒绝——控制面掌管全部渠道密钥)。
> 平时把它写进 `.env` 即可。首次启动自动建表(`GW_AUTO_CREATE_TABLES=true` 默认开),
> Fernet 密钥未配置时自动生成到 `.gateway_secret`(0600 权限,仅限开发)。

```bash
# 1. 注册一个渠道(用你自己合法持有的 key;$GW_ADMIN_TOKEN 是你上面设置的值)
curl -s http://localhost:8080/admin/channels -H "Authorization: Bearer $GW_ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{
    "name": "deepseek",
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "sk-你的真实key",
    "models": ["deepseek-chat"],
    "prices": {"deepseek-chat": {"input": 0.27, "output": 1.1}},
    "priority": 10
  }'

# 2. 发一个虚拟 key(原文只返回这一次)
curl -s http://localhost:8080/admin/keys -H "Authorization: Bearer $GW_ADMIN_TOKEN" \
  -H "Content-Type: application/json" -d '{"name": "my-ide", "monthly_budget_usd": 10}'

# 3. 任意 OpenAI 兼容客户端把 base_url 指过来即可
curl -N http://localhost:8080/v1/chat/completions -H "Authorization: Bearer sk-gw-..." \
  -H "Content-Type: application/json" -d '{
    "model": "deepseek-chat",
    "messages": [{"role": "user", "content": "你好"}],
    "stream": true
  }'

# 4. 看账
curl -s "http://localhost:8080/admin/stats/overview?days=7" -H "Authorization: Bearer $GW_ADMIN_TOKEN"
```

## 部署(Docker Compose,Postgres + Redis)

```bash
cp .env.example .env   # 填 GW_SECRET_KEY 和 GW_ADMIN_TOKEN
docker compose up -d --build
```

容器内启动顺序:`alembic upgrade head` → uvicorn。Postgres 用 pgvector 镜像(P2 语义缓存直接可用),Redis 供 P2 限流/熔断。

## 配置

全部环境变量以 `GW_` 为前缀,支持 `.env`,完整清单见 [app/config.py](app/config.py)。关键项:

| 变量 | 默认 | 说明 |
|---|---|---|
| `GW_DATABASE_URL` | `sqlite+aiosqlite:///./gateway.db` | 生产用 `postgresql+asyncpg://...` |
| `GW_SECRET_KEY` | (空=自动生成) | 渠道密钥加密的 Fernet key,生产必须显式配置 |
| `GW_ADMIN_TOKEN` | (必须显式设置) | 控制面鉴权 token,留空/默认值启动即拒绝 |
| `GW_CACHE_TTL_SECONDS` | `3600` | 精确缓存 TTL |
| `GW_UPSTREAM_TIMEOUT_READ` | `300` | 上游读超时(秒) |
| `GW_UPSTREAM_MAX_CONNECTIONS` | `1000` | 上游连接池上限(SSE 流占用连接到生成结束,按并发流量配) |

预算是**软预算**:计量异步落库,非流式滞后秒级;流式请求到流结束才计量,并发长流可短暂超支。硬限流在 P2(Redis 滑动窗口)。

## 项目结构

```
app/
  main.py            # app 工厂 + 生命周期(引擎/HTTP 连接池/计量器)
  config.py          # 全部配置项
  models.py          # Channel / VirtualKey / RequestLog / CacheEntry
  security.py        # Fernet 加密、虚拟 key 生成与哈希
  trace.py           # X-Trace-Id 中间件 + contextvar
  deps.py            # 鉴权依赖(数据面虚拟 key / 控制面 admin token)
  api/
    v1.py            # 数据面:/v1/chat/completions、/v1/models
    admin.py         # 控制面:渠道/key/统计/日志
  services/
    forwarder.py     # 转发核心:非流式 + SSE 流式透传、failover
    router.py        # P1 静态优先级路由(P4 扩展难度感知)
    cache.py         # 精确匹配缓存(P2 升级 pgvector 语义缓存)
    usage.py         # usage 解析、成本计算、异步计量器
    stats.py         # 控制面统计聚合
tests/               # 42 个测试:内嵌假上游,全链路不出进程
alembic/             # 数据库迁移
```

## 测试

```bash
python -m pytest tests -q
```

测试用内嵌 ASGI 假上游(按 Host 头模拟多渠道),覆盖:鉴权、转发透传、模型改写、failover 语义、SSE 流式(含 usage 注入与吞吐)、缓存命中/过期/流式回放(含 tool_calls)、预算拒绝、统计聚合、密钥加密,以及一轮多智能体对抗性审查确认缺陷的回归测试(连接池耗尽快速 503、解密失败 failover、U+2028 切行、计量外键竞态等)。

## 路线图

- [x] **P1** 透明代理:兼容入口、渠道注册表、流式透传、计量落库、精确缓存、管理 API
- [ ] **P2** 语义缓存(pgvector)、熔断 + 半开探测、滑动窗口限流、Prometheus 指标
- [ ] **P3** React 控制台:渠道面板、额度大盘、WebSocket live-tail、告警中心
- [ ] **P4** 难度感知路由 + 质量回评、用量异常哨兵、日成本报告
