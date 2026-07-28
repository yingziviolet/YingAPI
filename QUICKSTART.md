# 快速开始

## Windows 单机版(推荐)

1. 下载 `LLMGateway-Setup-x.y.z.exe`(安装包)或 `LLMGateway-portable.zip`(免安装)
2. 双击运行 —— 应用窗口自动打开
3. 粘贴一个你自己的 API key,点「开始使用」
4. 把界面给出的两行配置复制到你的客户端

就这三步,没有别的。

数据全部保存在 `%LOCALAPPDATA%\LLMGateway`(SQLite 数据库、加密密钥、日志),
卸载不会删除,想彻底清理手动删这个目录即可。

### 接入客户端

配置完成后界面会直接给出可复制的配置,大致是这样:

**OpenAI 兼容客户端**(IDE 插件、脚本、各类 agent):

```bash
export OPENAI_BASE_URL=http://127.0.0.1:8080/v1
export OPENAI_API_KEY=sk-gw-网关发给你的虚拟key
```

**Anthropic 协议客户端**(Claude Code 等,需配自有 API key):

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:8080
export ANTHROPIC_AUTH_TOKEN=sk-gw-网关发给你的虚拟key
```

> 注意这里填的是**网关发的虚拟 key**(`sk-gw-` 开头),不是你上游厂商的真 key。
> 真 key 加密存在本机,由网关代为使用。

### 常见问题

**双击没反应?**
需要 Microsoft Edge WebView2 运行时(Win11 与新版 Win10 自带)。缺失时程序会弹窗询问,
选择去官网安装 Evergreen Bootstrapper,或选择用系统浏览器打开(功能完全一样)。

**8080 端口被占了?**
程序会自动往后找空闲端口,界面给出的配置里是实际端口,直接复制即可。

**想看日志?**
托盘图标右键 → 打开数据目录,里面的 `gateway.log`。

---

## 从源码运行

```bash
pip install -e ".[test]"
```

```bash
cd console && npm install && npm run build && cd ..
```

```bash
GW_ADMIN_TOKEN=dev-token python -m uvicorn app.main:app --port 8080 --reload
```

浏览器打开 http://127.0.0.1:8080/console/ —— 本地访问免登录。

前端热更新开发另开一个终端:

```bash
cd console && npm run dev
```

## 自行构建安装包

```powershell
powershell -ExecutionPolicy Bypass -File packaging\build.ps1
```

三步产物:前端构建 → PyInstaller 打包(`dist\LLMGateway\`)→ Inno Setup 安装包
(`packaging\output\`,需先安装 Inno Setup 6;没装则只出免安装版)。

## 服务器部署

```bash
cp .env.example .env   # 填 GW_SECRET_KEY 与 GW_ADMIN_TOKEN
docker compose up -d --build
```

对外部署务必设置:

```bash
GW_LOCAL_AUTO_AUTH=false   # 关闭本地免登录,强制 token 鉴权
GW_METRICS_TOKEN=xxx       # /metrics 抓取鉴权
```
