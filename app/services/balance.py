"""渠道余额查询:用渠道自己的 key 调它公开的余额接口。

只调各家文档化的公开端点(和你手动 curl 一样),不碰任何私有/网页接口。
自动探测:按 base_url 与 provider 猜最可能的端点,依次尝试,第一个成功即返回;
渠道可用 balance_url 显式指定(中转站自建端点)。
"""
import logging
from datetime import date, timedelta

import httpx
from cryptography.fernet import Fernet, InvalidToken

from app.models import Channel
from app.security import decrypt_api_key

logger = logging.getLogger("gateway.balance")


def _num(value) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _probe_openai_dashboard(base: str) -> list[dict]:
    """one-api / new-api / 绝大多数 OpenAI 兼容中转站的通用账单端点。"""
    today = date.today()
    start = (today - timedelta(days=99)).isoformat()
    end = (today + timedelta(days=1)).isoformat()
    return [
        {
            "name": "openai-dashboard",
            "url": f"{base}/dashboard/billing/subscription",
            "usage_url": f"{base}/dashboard/billing/usage?start_date={start}&end_date={end}",
        }
    ]


def _probes(channel: Channel) -> list[dict]:
    base = channel.base_url.rstrip("/")
    root = base[: -len("/v1")] if base.endswith("/v1") else base
    provider = (channel.provider or "").lower()
    lowered = base.lower()

    probes: list[dict] = []
    if channel.balance_url:  # 显式配置优先
        probes.append({"name": "custom", "url": channel.balance_url})

    if "deepseek" in lowered or provider == "deepseek":
        probes.append({"name": "deepseek", "url": f"{root}/user/balance"})
    if "moonshot" in lowered or "kimi" in lowered or provider in ("moonshot", "kimi"):
        probes.append({"name": "moonshot", "url": f"{base}/users/me/balance"})
    if "siliconflow" in lowered or provider == "siliconflow":
        probes.append({"name": "siliconflow", "url": f"{base}/user/info"})

    probes.extend(_probe_openai_dashboard(base))
    return probes


def _parse(name: str, data: dict, usage_data: dict | None) -> dict | None:
    """把各家不同的响应体归一成 {total, used, remaining, currency}。"""
    if name == "deepseek":
        infos = data.get("balance_infos") or []
        if infos:
            info = infos[0]
            remaining = _num(info.get("total_balance"))
            if remaining is not None:
                return {
                    "remaining": remaining,
                    "currency": info.get("currency", "CNY"),
                    "granted": _num(info.get("granted_balance")),
                    "topped_up": _num(info.get("topped_up_balance")),
                }
        return None

    if name == "moonshot":
        info = (data.get("data") or {})
        remaining = _num(info.get("available_balance"))
        if remaining is not None:
            return {"remaining": remaining, "currency": "CNY", "cash": _num(info.get("cash_balance"))}
        return None

    if name == "siliconflow":
        info = (data.get("data") or {})
        remaining = _num(info.get("totalBalance") or info.get("balance"))
        if remaining is not None:
            return {"remaining": remaining, "currency": "CNY"}
        return None

    # openai-dashboard / custom:字段名各家略有出入,尽量兜住
    total = _num(data.get("hard_limit_usd"))
    if total is None:
        total = _num(data.get("system_hard_limit_usd"))
    if total is None:  # 有些中转站直接返回 {"balance": x} 或 {"data": {"quota": x}}
        direct = _num(data.get("balance")) or _num((data.get("data") or {}).get("balance"))
        if direct is not None:
            return {"remaining": direct, "currency": data.get("currency", "USD")}
        return None
    used = None
    if usage_data:
        cents = _num(usage_data.get("total_usage"))
        if cents is not None:
            used = cents / 100.0
    out = {"total": total, "currency": "USD"}
    if used is not None:
        out["used"] = round(used, 4)
        out["remaining"] = round(total - used, 4)
    return out


async def fetch_balance(
    client: httpx.AsyncClient, channel: Channel, fernet: Fernet
) -> dict:
    """返回 {ok, source?, balance?, error?}。任何失败都是软失败(余额是附加信息)。"""
    try:
        key = decrypt_api_key(fernet, channel.api_key_encrypted)
    except InvalidToken:
        return {"ok": False, "error": "api key decryption failed"}
    headers = {"Authorization": f"Bearer {key}"}
    timeout = httpx.Timeout(10.0)
    last_error = "no probe succeeded"

    for probe in _probes(channel):
        try:
            resp = await client.get(probe["url"], headers=headers, timeout=timeout)
            if resp.status_code >= 400:
                last_error = f"{probe['name']}: HTTP {resp.status_code}"
                continue
            data = resp.json()
            if not isinstance(data, dict):
                last_error = f"{probe['name']}: unexpected body"
                continue
            usage_data = None
            if probe.get("usage_url"):
                try:
                    usage_resp = await client.get(
                        probe["usage_url"], headers=headers, timeout=timeout
                    )
                    if usage_resp.status_code < 400:
                        parsed = usage_resp.json()
                        usage_data = parsed if isinstance(parsed, dict) else None
                except (httpx.HTTPError, ValueError):
                    pass
            balance = _parse(probe["name"], data, usage_data)
            if balance:
                return {"ok": True, "source": probe["name"], "balance": balance}
            last_error = f"{probe['name']}: no balance field in response"
        except (httpx.HTTPError, ValueError) as exc:
            last_error = f"{probe['name']}: {exc!r}"
            continue
    return {"ok": False, "error": last_error}
