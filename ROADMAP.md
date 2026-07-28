# 项目进度与待办(交接文档)

> 这份文档写给「未来的我 / 换一个模型接手 / 隔一段时间回来的自己」看。
> 记录**已完成什么、为什么这么设计、还剩什么、以及有意不做什么**。
> 架构总方案见 [智能网关-全栈架构.md](智能网关-全栈架构.md)(已收敛,勿重新发散选型)。

最后更新:2026-07-28

---

## 一、项目是什么

自研 LLM 网关基础设施,面试作品 + 自己每天真用:

- **数据面**:OpenAI 兼容 + Anthropic 兼容双协议入口,流式透传、语义缓存、难度感知路由、渠道熔断、token 级计量
- **控制面**:React 控制台(渠道管理、额度大盘、实时请求流、告警、订阅用量)
- **交付**:服务器 Docker Compose;Windows 单机 exe(原生窗口应用)

**差异化定位**:市面网关(one-api / new-api / LiteLLM)卷的是协议聚合和账号池,路由全是静态优先级;本项目的价值在**智能调度层**——语义缓存、难度路由、熔断、异常检测。

**性质红线(绝不越)**:
- 只管理**自己合法持有的 API key**
- 绝不做 sub2api(订阅转 API / 拼车共享)
- **不读取厂商登录 cookie/token,不逆向任何非公开接口**
- 所有额度数据要么产自本网关计量,要么来自厂商**公开文档化**的余额接口

---

## 二、已完成

仓库:`github.com/yingziviolet/YingAPI`;Windows 1.0 当前位于 `feat/ying-windows-1.0`,测试完成前不合并 `main`。

### P1 透明代理
- `POST /v1/chat/completions`:非流式 + SSE 流式透传(chunk 到达即转发,首 token 延迟不劣化)
- 虚拟 key 体系:`sk-gw-*` 只存 SHA-256 哈希,原文仅创建时返回一次;月度软预算
- 渠道注册表:多渠道静态优先级路由 + failover;渠道 API key 用 Fernet 加密落库,永不回显
- failover 语义:传输错误/超时/401/403/408/429/5xx → 换渠道;其余 4xx → 原样透传不重试
- token 计量:流式/非流式统一解析 usage(流式自动注入 `stream_options.include_usage`),按渠道价格表算成本,**异步落库不阻塞响应**
- 精确匹配缓存:规范化请求 SHA-256,仅缓存 `temperature<=0`,流式命中合成 SSE 回放(含 tool_calls)
- trace_id 全链路;控制面 API:渠道/key CRUD、统计聚合、请求日志(脱敏,只存元数据)

### P2 智能调度层
- **熔断器** `app/services/breaker.py`:滑动窗口错误率 → OPEN(快速失败+failover)→ HALF_OPEN 限量探测 → 恢复;控制台可观测 + 手动复位;探测名额泄漏有自愈兜底
- **限流** `app/services/ratelimit.py`:每 key 每分钟滑动窗口(双桶加权近似)。进程内实现零依赖(exe 单机模式);配 `GW_REDIS_URL` 切 Redis 实现支持多 worker,Redis 故障 fail-open
- **语义缓存** `app/services/semantic_cache.py`:请求文本 → embedding → 余弦相似度 ≥ 阈值命中。精确缓存未命中才查(embedding 有成本)。条目按 **(model, 生成参数指纹, embedding 模型)** 三元分区,避免误命中;含图片/工具调用的会话跳过
- **Prometheus** `/metrics`:请求量/延迟分布/首 token 延迟/token/成本/缓存/限流/熔断状态

### P2.5 Anthropic 协议入口(双协议网关)
- `POST /v1/messages`:Anthropic ↔ OpenAI 三层翻译(请求体、非流式响应、SSE 事件流状态机)
- `POST /v1/messages/count_tokens`;`/v1/models` 双协议兼容字段
- 鉴权同时接受 `Authorization: Bearer` 与 `x-api-key`
- **用途**:Claude Code 等 Anthropic 协议客户端配**自有 key** + `ANTHROPIC_BASE_URL` 指向网关即可接入(订阅 OAuth 流量不经网关,见红线)

### P3 React 控制台
Vite + React + TS + Tailwind v4 + ECharts,构建产物由 FastAPI 托管于 `/console/`:
- 额度大盘:总览卡、每日请求/成本曲线、按渠道/按模型、预算进度条 + 耗尽预测
- 渠道面板:健康灯、一键启停、优先级调整、连通测试、熔断状态 + 复位
- 虚拟 Key:发放(原文仅一次)、预算、限流、本月花费
- 实时请求流:WebSocket live-tail(`/admin/ws/livetail`),断线重连,历史+实时合流
- 智能层成绩单:缓存命中、省钱估算、熔断窗口明细

### P3.5 告警 + 订阅用量
- **哨兵** `app/services/sentinel.py`:后台巡检五类事件——预算 80%/100%、渠道熔断、key 消耗异常突增(近 1h vs 前 24h 均值,定位泄漏)、错误率突增、每日用量报告。`dedupe_key` 去重落库,可配 `GW_ALERT_WEBHOOK_URL` 推送
- **告警中心页**:列表/确认/全部确认/立即巡检
- **订阅用量页(cockpit)** `app/services/subscription.py`:只读解析本机 `~/.claude/projects/**/*.jsonl`,按日/按模型聚合 token,按 API 牌价折算"订阅帮你省了多少"。**只读本地文件,不碰厂商接口**

### P4 难度感知路由(核心部分)
- `app/services/downgrade.py`:启发式判定简单请求(短文本、无 tools、无代码块、少轮次、纯文本)→ 确定性降级到 `GW_DOWNGRADE_MAP` 配置的便宜模型
- 双协议入口都生效;响应带 `X-Gateway-Downgraded` 头;计量记 `downgraded_to`;成本按**实际路由的模型**计价;大盘统计降级次数

### 交付形态
- **Docker Compose**:Postgres(pgvector 镜像)+ Redis + 网关
- **Windows exe** `packaging/`:PyInstaller 打包,pywebview 原生窗口(WebView2),失败自动回退浏览器,单实例运行;新数据在 `%LOCALAPPDATA%\Ying`,旧版目录存在时继续复用
- **Windows 发布包**:`Ying-portable.zip`、`Ying-Setup-1.0.0.exe`、`SHA256SUMS.txt`;安装/卸载、快捷方式与用户数据保留均已实测
- CI:GitHub Actions 跑测试 + 前端构建 + Docker 构建

### 质量保障
- **128 个测试**全过(内嵌 ASGI 假上游,全链路不出进程)
- **两轮多智能体对抗性审查**:
  - 第一轮(P1):29 个发现 / 26 确认,全部修复
  - 第二轮(P2/P2.5):26 个发现 / 25 确认,全部修复
  - 修复的典型问题:语义缓存跨参数误命中、熔断器半开名额永久泄漏、Anthropic 流式断连泄漏上游连接、Redis 限流 check-then-act 竞态
- 数据库迁移链 0001→0007,每次都在干净库上验证过

---

## 三、Windows 1.0 分支与后续

### 本分支已完成
- 首次启动引导:粘贴上游 API key → 自动识别渠道 → 创建虚拟 key → 输出客户端配置
- Ying 品牌、应用图标、浅色/深色首次启动页
- pywebview 原生窗口、浏览器回退、单实例保护
- 便携 ZIP、Inno Setup 安装包、SHA-256 校验文件
- 全新配置启动、安装、快捷方式、卸载与用户数据保留烟雾测试

### 渠道余额查询(后端已完成,前端未接)
- ✅ `app/services/balance.py`:多探针自动探测余额接口
  - DeepSeek `GET /user/balance`
  - Moonshot/Kimi `GET /v1/users/me/balance`
  - SiliconFlow `GET /v1/user/info`
  - 通用 OpenAI 兼容中转站(one-api/new-api)`GET /v1/dashboard/billing/subscription` + `/usage`
  - 渠道可配 `balance_url` 显式指定
  - 响应归一成 `{total, used, remaining, currency}`
- ✅ 端点:`GET /admin/channels/{id}/balance`、`GET /admin/balances`(并发查全部)
- ✅ 模型加 `Channel.balance_url` 字段 + 迁移 0006 + schema
- ⬜ **待办**:
  - 前端渠道面板加「余额」列 + 刷新按钮
  - 大盘加余额汇总卡片
  - 创建渠道表单加 `balance_url` 输入
  - 写测试(mock 各家余额接口响应)

---

## 四、待办清单(按优先级)

### 高优先级(补完当前半成品)
1. **前端接余额显示**——渠道面板余额列、大盘汇总卡、创建表单加 balance_url
2. **全控制台主题走查**——七个业务页逐个看浅色/深色对比度与图表遮挡
3. **给 P3.5/P4/exe 这批新代码做第三轮对抗性审查**(前两轮只覆盖到 P2.5)

### 中优先级(路线图剩余项)
4. **GitHub Releases 自动发布流水线**——打 tag 触发 CI,自动构建 exe/安装包并挂到 Release 页面。当前 1.0 先用已验证的本地产物发布
5. **质量回评**——抽样用强模型复评被降级的答案,证明"降级不降质"
6. **语义缓存 pgvector 索引化**——现在是应用层余弦匹配(候选上限 500),数据量上来后迁到 pgvector 向量索引

### 低优先级 / 可选
7. 账单 CSV/JSON 导入——补「切到网关之前」的历史消费数据
8. 渠道权重路由(现在只有静态优先级)
9. macOS/Linux 打包(现在只做了 Windows)

---

## 五、有意不做的事(以及原因)

| 需求 | 为什么不做 | 替代方案 |
|---|---|---|
| **读订阅账号额度(网页登录态)** | 必须读取厂商登录 cookie/token 去调私有网页接口。既碰用户凭证又逆向非公开接口,是架构方案亲手划的红线;厂商改接口就坏,写进简历是减分项 | 「订阅用量」页:读本机 Claude Code 会话记录算真实消耗 + 牌价折算 |
| **sub2api / 订阅转 API** | 违反厂商 ToS 的灰产方向 | 只管理自己合法持有的 API key |
| **Claude Code 订阅登录走网关** | 订阅 OAuth 凭证只对官方端点有效,技术上就互斥 | Claude Code 配**自有 API key** + `ANTHROPIC_BASE_URL` 指向网关(P2.5 已支持) |
| Next.js | 控制台是纯 SPA,不需要 SSR | Vite + React |
| Celery | 单进程 asyncio 后台任务够用,少一个依赖 | `asyncio.create_task` + lifespan 管理 |

---

## 六、开发速查

### 起开发环境
```bash
pip install -e ".[test]"
cd console && npm install && npm run build && cd ..
```
```bash
GW_ADMIN_TOKEN=dev-token python -m uvicorn app.main:app --port 8080 --reload
```
控制台:http://127.0.0.1:8080/console/

### 前端热更新开发
```bash
cd console && npm run dev
```
(vite.config.ts 已配代理到 127.0.0.1:8080)

### 跑测试
```bash
python -m pytest tests -q
```
**注意**:必须在仓库根目录跑(测试用 `from tests.conftest import ...`)

### 演示环境(带假上游,可看真实数据)
```bash
python C:\Users\ADMINI~1\AppData\Local\Temp\claude\...\scratchpad\fake_upstream.py
```
假上游在 9909 端口,网关配 `GW_ADMIN_TOKEN=demo-admin-token`、`GW_EMBEDDING_CHANNEL=demo-upstream`

### 构建 Windows exe
```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```
产物:`release\Ying-portable.zip`、`release\Ying-Setup-1.0.0.exe`、`release\SHA256SUMS.txt`(需先装 Inno Setup 6)

### 数据库迁移
```bash
python -m alembic revision -m "描述"
```
```bash
python -m alembic upgrade head
```

---

## 七、关键配置项

全部环境变量以 `GW_` 为前缀,完整清单见 `app/config.py`。

| 变量 | 默认 | 说明 |
|---|---|---|
| `GW_ADMIN_TOKEN` | (必填) | 控制面 token,留空或 `change-me` 启动即拒绝 |
| `GW_SECRET_KEY` | (自动生成) | 渠道密钥加密的 Fernet key,生产必须显式配 |
| `GW_DATABASE_URL` | SQLite | 生产 `postgresql+asyncpg://...` |
| `GW_REDIS_URL` | (空) | 配了就用 Redis 限流(多 worker);空则进程内 |
| `GW_SEMANTIC_CACHE_ENABLED` | false | 语义缓存开关 |
| `GW_EMBEDDING_CHANNEL` | (空) | 提供 embedding 的渠道 name |
| `GW_DOWNGRADE_ENABLED` | false | 难度感知路由开关 |
| `GW_DOWNGRADE_MAP` | {} | `{"gpt-4o": "gpt-4o-mini"}` |
| `GW_SENTINEL_INTERVAL_SECONDS` | 60 | 哨兵巡检间隔,0 = 关闭 |
| `GW_ALERT_WEBHOOK_URL` | (空) | 告警推送地址 |
| `GW_METRICS_TOKEN` | (空) | /metrics 抓取鉴权,对外部署应设置 |

---

## 八、代码结构

```
app/
  main.py              # app 工厂 + lifespan(引擎/连接池/计量器/哨兵/缓存清理)
  config.py            # 全部配置项
  models.py            # Channel / VirtualKey / RequestLog / CacheEntry / SemanticCacheEntry / Alert
  security.py          # Fernet 加密、虚拟 key 生成与哈希
  trace.py             # X-Trace-Id 中间件
  deps.py              # 鉴权依赖
  metrics.py           # Prometheus 指标定义
  api/
    v1.py              # OpenAI 兼容入口
    anthropic.py       # Anthropic 兼容入口(P2.5)
    admin.py           # 控制面
    ws.py              # WebSocket live-tail
  services/
    forwarder.py       # 转发核心 + failover
    router.py          # 静态优先级路由
    cache.py           # 精确缓存
    semantic_cache.py  # 语义缓存
    breaker.py         # 熔断器
    ratelimit.py       # 限流(内存/Redis)
    downgrade.py       # 难度感知路由(P4)
    sentinel.py        # 告警哨兵(P3.5)
    subscription.py    # 订阅用量解析(P3.5)
    balance.py         # 渠道余额查询(新)
    usage.py           # 计量与成本
    stats.py           # 统计聚合
    livetail.py        # 实时流发布订阅
console/src/
  App.tsx              # 七页布局 + 主题切换
  api.ts / types.ts    # API 客户端
  theme.ts             # 主题切换
  components/          # ui.tsx(基础组件)、Chart.tsx(ECharts 封装)
  pages/               # Dashboard/Channels/Keys/LiveTail/Insights/Alerts/Subscription
packaging/
  launcher.py          # exe 入口:数据目录 + 原生窗口 + 托盘
  gateway.spec         # PyInstaller 配置
  installer.iss        # Inno Setup 安装包
  build.ps1            # 一键构建
tests/                 # 128 个测试
alembic/versions/      # 迁移 0001-0007
```
