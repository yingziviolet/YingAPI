"""批量导入:API Key 与历史账单。

Key 导入:接受 JSON / CSV / 纯文本(一行一个 key),自动识别厂商并预填
base_url、模型与价格;导入前逐个做连通性与余额探测,由前端确认后再落库。
账单导入:把厂商后台导出的消费记录合并进大盘(标记为 imported,与网关自身
计量区分开,不污染"网关转发了多少"这个口径)。
"""
import csv
import io
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

logger = logging.getLogger("gateway.importer")

# 常见厂商指纹 -> 预填配置。
# 顺序即优先级:明确的前缀特征排在前,靠长度猜的排在后(否则 sk-ant- 会被长度规则误吃)。
PROVIDER_PRESETS: list[dict] = [
    {
        "provider": "anthropic",
        "label": "Anthropic",
        "match": lambda k: k.startswith("sk-ant-"),
        "base_url": "https://api.anthropic.com/v1",
        "models": ["claude-sonnet-5", "claude-opus-5"],
        "prices": {},
    },
    {
        "provider": "openai",
        "label": "OpenAI",
        "match": lambda k: k.startswith("sk-proj-") or k.startswith("sk-svcacct-"),
        "base_url": "https://api.openai.com/v1",
        "models": ["gpt-4o", "gpt-4o-mini"],
        "prices": {
            "gpt-4o": {"input": 2.5, "output": 10.0},
            "gpt-4o-mini": {"input": 0.15, "output": 0.6},
        },
    },
    {
        "provider": "zhipu",
        "label": "智谱 GLM",
        "match": lambda k: "." in k and len(k.split(".")) == 2 and len(k) > 30,
        "base_url": "https://open.bigmodel.cn/api/paas/v4",
        "models": ["glm-4-plus", "glm-4-flash"],
        "prices": {},
    },
    # —— 以下按长度猜,只在没有明确前缀时兜底 ——
    {
        "provider": "deepseek",
        "label": "DeepSeek",
        "match": lambda k: k.startswith("sk-") and len(k) == 35,
        "base_url": "https://api.deepseek.com/v1",
        "models": ["deepseek-chat", "deepseek-reasoner"],
        "prices": {
            "deepseek-chat": {"input": 0.27, "output": 1.1},
            "deepseek-reasoner": {"input": 0.55, "output": 2.19},
        },
    },
    {
        "provider": "siliconflow",
        "label": "SiliconFlow",
        "match": lambda k: k.startswith("sk-") and len(k) == 51,
        "base_url": "https://api.siliconflow.cn/v1",
        "models": ["Qwen/Qwen2.5-72B-Instruct"],
        "prices": {},
    },
    {
        "provider": "moonshot",
        "label": "Moonshot / Kimi",
        "match": lambda k: k.startswith("sk-") and len(k) > 45,
        "base_url": "https://api.moonshot.cn/v1",
        "models": ["moonshot-v1-8k", "moonshot-v1-32k", "moonshot-v1-128k"],
        "prices": {"moonshot-v1-8k": {"input": 1.68, "output": 1.68}},
    },
]

KEY_RE = re.compile(r"(sk-[A-Za-z0-9_\-]{16,}|[A-Za-z0-9]{20,}\.[A-Za-z0-9]{10,})")


@dataclass
class ParsedKey:
    """从输入里解析出的一条待导入渠道。"""

    api_key: str
    name: str = ""
    provider: str = ""
    label: str = ""
    base_url: str = ""
    models: list[str] = field(default_factory=list)
    prices: dict = field(default_factory=dict)
    note: str = ""

    def to_dict(self) -> dict:
        return {
            "api_key": self.api_key,
            "api_key_masked": mask(self.api_key),
            "name": self.name,
            "provider": self.provider,
            "label": self.label,
            "base_url": self.base_url,
            "models": self.models,
            "prices": self.prices,
            "note": self.note,
        }


def mask(key: str) -> str:
    if len(key) <= 12:
        return key[:4] + "****"
    return f"{key[:8]}****{key[-4:]}"


def detect_provider(api_key: str) -> dict | None:
    for preset in PROVIDER_PRESETS:
        try:
            if preset["match"](api_key):
                return preset
        except Exception:
            continue
    return None


def _apply_preset(item: ParsedKey, index: int) -> ParsedKey:
    preset = detect_provider(item.api_key)
    if preset:
        item.provider = item.provider or preset["provider"]
        item.label = preset["label"]
        item.base_url = item.base_url or preset["base_url"]
        if not item.models:
            item.models = list(preset["models"])
        if not item.prices:
            item.prices = dict(preset["prices"])
    else:
        item.label = "未识别(需手工填 Base URL)"
    if not item.name:
        stem = item.provider or "channel"
        item.name = f"{stem}-{index}"
    return item


def parse_keys(text: str) -> list[dict]:
    """解析三种输入形态,统一成待导入列表。

    1) JSON:数组或 {channels:[...]},字段 api_key/key/name/base_url/models/prices
    2) CSV:表头含 key 或 api_key 列
    3) 纯文本:一行一个 key,或从任意文本里正则捞 key
    """
    text = (text or "").strip()
    if not text:
        return []

    items: list[ParsedKey] = []

    # —— JSON ——
    if text[0] in "[{":
        try:
            data = json.loads(text)
            rows = data if isinstance(data, list) else data.get("channels") or data.get("keys") or []
            for row in rows:
                if isinstance(row, str):
                    items.append(ParsedKey(api_key=row.strip()))
                elif isinstance(row, dict):
                    key = row.get("api_key") or row.get("key") or row.get("apiKey") or ""
                    if not key:
                        continue
                    items.append(
                        ParsedKey(
                            api_key=str(key).strip(),
                            name=str(row.get("name") or "").strip(),
                            provider=str(row.get("provider") or "").strip(),
                            base_url=str(row.get("base_url") or row.get("baseUrl") or "").strip(),
                            models=list(row.get("models") or []),
                            prices=dict(row.get("prices") or {}),
                            note=str(row.get("note") or "").strip(),
                        )
                    )
            if items:
                return [_apply_preset(it, i + 1).to_dict() for i, it in enumerate(items)]
        except ValueError:
            pass  # 不是合法 JSON,继续按 CSV/文本试

    # —— CSV ——
    if "," in text and "\n" in text:
        try:
            reader = csv.DictReader(io.StringIO(text))
            if reader.fieldnames:
                lowered = {(f or "").strip().lower(): f for f in reader.fieldnames}
                key_col = lowered.get("api_key") or lowered.get("key") or lowered.get("apikey")
                if key_col:
                    for row in reader:
                        key = (row.get(key_col) or "").strip()
                        if not key:
                            continue
                        models_raw = (row.get(lowered.get("models", "")) or "").strip()
                        items.append(
                            ParsedKey(
                                api_key=key,
                                name=(row.get(lowered.get("name", "")) or "").strip(),
                                provider=(row.get(lowered.get("provider", "")) or "").strip(),
                                base_url=(row.get(lowered.get("base_url", "")) or "").strip(),
                                models=[m.strip() for m in models_raw.split("|") if m.strip()],
                            )
                        )
                    if items:
                        return [_apply_preset(it, i + 1).to_dict() for i, it in enumerate(items)]
        except (csv.Error, ValueError):
            pass

    # —— 纯文本:一行一个,或正则捞 ——
    seen: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        for match in KEY_RE.findall(line):
            if match not in seen:
                seen.add(match)
                items.append(ParsedKey(api_key=match))
    return [_apply_preset(it, i + 1).to_dict() for i, it in enumerate(items)]


# ---------------- 历史账单导入 ----------------


def _parse_dt(value: str) -> datetime | None:
    value = (value or "").strip()
    if not value:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d", "%Y/%m/%d", "%Y/%m/%d %H:%M"):
        try:
            return datetime.strptime(value[:19], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _num(value, default: float = 0.0) -> float:
    try:
        return float(str(value).strip() or default)
    except (TypeError, ValueError):
        return default


def parse_billing(text: str) -> list[dict]:
    """解析厂商账单导出(CSV/JSON),归一成可落库的记录。

    识别列(大小写与常见别名均可):日期/时间、模型、请求数、输入/输出 token、成本。
    一行代表某天某模型的汇总;缺请求数时按 1 计。
    """
    text = (text or "").strip()
    if not text:
        return []

    rows: list[dict] = []

    def push(raw: dict):
        lowered = { (k or "").strip().lower(): v for k, v in raw.items() }

        def pick(*names, default=""):
            for n in names:
                if n in lowered and lowered[n] not in (None, ""):
                    return lowered[n]
            return default

        ts = _parse_dt(str(pick("date", "day", "timestamp", "created_at", "time", "日期", "时间")))
        if ts is None:
            return
        model = str(pick("model", "模型", default="imported")).strip() or "imported"
        requests = int(_num(pick("requests", "count", "calls", "请求数", "次数"), 1)) or 1
        pt = int(_num(pick("input_tokens", "prompt_tokens", "输入", "输入token")))
        ct = int(_num(pick("output_tokens", "completion_tokens", "输出", "输出token")))
        cost = _num(pick("cost", "amount", "cost_usd", "spend", "消费", "金额"))
        rows.append(
            {
                "created_at": ts,
                "model": model,
                "requests": requests,
                "prompt_tokens": pt,
                "completion_tokens": ct,
                "cost_usd": cost,
            }
        )

    if text[0] in "[{":
        try:
            data = json.loads(text)
            records = data if isinstance(data, list) else data.get("data") or data.get("records") or []
            for row in records:
                if isinstance(row, dict):
                    push(row)
            return rows
        except ValueError:
            pass

    try:
        reader = csv.DictReader(io.StringIO(text))
        for row in reader:
            push(row)
    except (csv.Error, ValueError) as exc:
        logger.warning("billing parse failed: %r", exc)
    return rows
