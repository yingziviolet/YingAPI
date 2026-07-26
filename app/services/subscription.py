"""订阅用量面板(cockpit)数据源:解析本机 Claude Code 的会话记录。

性质说明:只读取本机 ~/.claude/projects/**/*.jsonl(自己的数据,ccusage 同款思路),
不调用、不逆向厂商任何非公开接口——订阅 OAuth 流量本身不经网关,这里给它一个
本地遥测入口,让控制台一个面板看全所有 LLM 消耗。

"折算成本" = 这些 token 按 API 牌价要花多少钱(即订阅帮你省了多少),
价格表可用 GW_SUBSCRIPTION_PRICES 覆盖。
"""
import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import Settings

logger = logging.getLogger("gateway.subscription")

# 内置 API 牌价(美元/1M token,按模型名子串匹配,从上到下第一个命中生效)
# 缓存读按 0.1x 输入价、缓存写按 1.25x 输入价折算(与厂商定价规则一致的近似)
_DEFAULT_PRICES: list[tuple[str, dict[str, float]]] = [
    ("fable", {"input": 10.0, "output": 50.0}),
    ("mythos", {"input": 10.0, "output": 50.0}),
    ("opus", {"input": 5.0, "output": 25.0}),
    ("sonnet", {"input": 3.0, "output": 15.0}),
    ("haiku", {"input": 1.0, "output": 5.0}),
]


def _price_for(model: str, overrides: dict[str, dict[str, float]]) -> dict[str, float] | None:
    lowered = (model or "").lower()
    for needle, price in overrides.items():
        if needle.lower() in lowered:
            return price
    for needle, price in _DEFAULT_PRICES:
        if needle in lowered:
            return price
    return None


def claude_data_dir() -> Path:
    return Path.home() / ".claude" / "projects"


def scan_usage(settings: Settings, days: int = 7) -> dict:
    """扫描窗口期内有改动的会话文件,聚合订阅侧 token 用量。"""
    root = claude_data_dir()
    if not root.exists():
        return {"available": False, "reason": "未找到 ~/.claude/projects(本机没有 Claude Code 记录)"}

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    cutoff_ts = cutoff.timestamp()
    daily: dict[str, dict] = {}
    by_model: dict[str, dict] = {}
    totals = {
        "messages": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
    }
    est_cost = 0.0
    files_scanned = 0

    for path in root.glob("*/*.jsonl"):
        try:
            if path.stat().st_mtime < cutoff_ts:
                continue
        except OSError:
            continue
        files_scanned += 1
        try:
            with open(path, encoding="utf-8") as fh:
                for line in fh:
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    message = obj.get("message") or {}
                    usage = message.get("usage")
                    if not isinstance(usage, dict) or usage.get("output_tokens") is None:
                        continue
                    ts_raw = obj.get("timestamp", "")
                    try:
                        ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    except ValueError:
                        continue
                    if ts < cutoff:
                        continue
                    model = message.get("model") or "unknown"
                    day = ts.date().isoformat()
                    input_tokens = int(usage.get("input_tokens") or 0)
                    output_tokens = int(usage.get("output_tokens") or 0)
                    cache_read = int(usage.get("cache_read_input_tokens") or 0)
                    cache_creation = int(usage.get("cache_creation_input_tokens") or 0)

                    day_row = daily.setdefault(
                        day,
                        {"date": day, "messages": 0, "input_tokens": 0, "output_tokens": 0,
                         "cache_read_tokens": 0, "cache_creation_tokens": 0, "est_cost_usd": 0.0},
                    )
                    model_row = by_model.setdefault(
                        model,
                        {"model": model, "messages": 0, "input_tokens": 0, "output_tokens": 0,
                         "cache_read_tokens": 0, "cache_creation_tokens": 0, "est_cost_usd": 0.0},
                    )
                    for row in (day_row, model_row):
                        row["messages"] += 1
                        row["input_tokens"] += input_tokens
                        row["output_tokens"] += output_tokens
                        row["cache_read_tokens"] += cache_read
                        row["cache_creation_tokens"] += cache_creation
                    totals["messages"] += 1
                    totals["input_tokens"] += input_tokens
                    totals["output_tokens"] += output_tokens
                    totals["cache_read_tokens"] += cache_read
                    totals["cache_creation_tokens"] += cache_creation

                    price = _price_for(model, settings.subscription_prices)
                    if price:
                        cost = (
                            input_tokens * price["input"]
                            + output_tokens * price["output"]
                            + cache_read * price["input"] * 0.1
                            + cache_creation * price["input"] * 1.25
                        ) / 1_000_000
                        day_row["est_cost_usd"] += cost
                        model_row["est_cost_usd"] += cost
                        est_cost += cost
        except OSError:
            continue

    for row in daily.values():
        row["est_cost_usd"] = round(row["est_cost_usd"], 4)
    for row in by_model.values():
        row["est_cost_usd"] = round(row["est_cost_usd"], 4)

    return {
        "available": True,
        "window_days": days,
        "files_scanned": files_scanned,
        "totals": totals,
        "est_api_cost_usd": round(est_cost, 4),
        "daily": sorted(daily.values(), key=lambda r: r["date"]),
        "by_model": sorted(by_model.values(), key=lambda r: -r["est_cost_usd"]),
    }
