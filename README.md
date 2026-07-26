# LLM 智能网关(数据面 + 控制面)

> 🚀 **怎么启动、面试怎么讲、还剩什么** → 看 [INTERVIEW.md](INTERVIEW.md)
> 📋 **详细进度与待办(交接文档)** → 看 [ROADMAP.md](ROADMAP.md)

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
cd console && npm install && npm run build && cd ..
```

(不构建前端也能跑——控制台目录不存在时网关纯 API 运行)

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

## Windows 单机版(exe)

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

三步产物:控制台前端 → PyInstaller(`dist\LLMGateway\LLMGateway.exe`,免安装直接双击)→ Inno Setup 安装包(`packaging\output\`,机器上装了 Inno Setup 6 才生成)。

单机版行为:双击启动 → 系统托盘图标(打开控制台/复制 token/数据目录/退出)→ 自动开浏览器进控制台。数据(SQLite/密钥/token/日志)全部在 `%LOCALAPPDATA%\LLMGateway`,卸载不删用户数据,零外部依赖。

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
- [x] **P2** 语义缓存(embedding + 余弦相似度,精确未中才查)、渠道熔断器(滑动窗口错误率 → OPEN → 半开探测 → 恢复,控制台可观测/手动复位)、每 key 滑动窗口限流(Redis 可选,进程内兜底)、Prometheus `/metrics`
- [ ] **P2.5** Anthropic Messages API 入口(`/v1/messages` + 协议翻译),Claude Code 等客户端配自有 key 走网关
- [x] **P3** React 控制台(Vite+React+TS+Tailwind+ECharts):额度大盘(总览/每日曲线/按渠道/按模型/预算进度+耗尽预测)、渠道面板(健康灯/启停/优先级/连通测试/熔断状态+复位)、虚拟 key 管理(发放/预算/限流)、实时请求流(WebSocket live-tail)、智能层成绩单(缓存命中/省钱估算/熔断记录);构建产物由 FastAPI 托管于 `/console/`
- [x] **P3.5** 告警中心(哨兵巡检:预算 80%/100%、渠道熔断、key 异常消耗、错误率突增、日报;去重落库 + 可选 webhook 推送)+ 订阅用量面板(cockpit:只读解析本机 ~/.claude 记录,按 API 牌价折算,不碰厂商接口)
- [x] **P4(核心)** 难度感知路由:简单请求(短文本/无工具/无代码/少轮次)确定性降级到便宜模型,`X-Gateway-Downgraded` 头 + 计量标记 + 大盘统计;成本按实际路由模型计
- [ ] **P4(后段)** 质量回评(抽样强模型复评降级答案)、语义缓存 pgvector 索引化
- 交付形态:服务器 Docker Compose;单机 Windows exe 安装包(PyInstaller + Inno Setup,SQLite,零外部依赖)
