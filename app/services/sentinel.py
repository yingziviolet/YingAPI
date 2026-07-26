"""用量哨兵:后台巡检 -> 告警落库(去重)-> 可选 webhook 推送。

巡检项:
- 预算:虚拟 key 月度花费过 80% / 100%(每 key 每月每阈值只报一次)
- 熔断:渠道进入 OPEN(每次熔断事件报一次)
- 异常消耗:key 近 1 小时成本突增(疑似泄漏,能定位到具体 key)
- 错误率:近 5 分钟全局错误率超阈值
- 日报:每天一条昨日用量汇总
告警只增不删,控制台可确认(acknowledged);webhook 失败静默降级。
"""
import asyncio
import logging
from datetime import timedelta

import httpx
from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.models import Alert, RequestLog, VirtualKey, utcnow

logger = logging.getLogger("gateway.sentinel")


async def create_alert(
    sessionmaker: async_sessionmaker[AsyncSession],
    *,
    kind: str,
    severity: str,
    title: str,
    detail: str,
    dedupe_key: str,
    webhook_url: str = "",
    http_client: httpx.AsyncClient | None = None,
) -> bool:
    """落一条告警;dedupe_key 撞唯一索引说明已报过,返回 False。"""
    async with sessionmaker() as session:
        session.add(
            Alert(kind=kind, severity=severity, title=title, detail=detail, dedupe_key=dedupe_key)
        )
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            return False
    logger.warning("ALERT [%s/%s] %s — %s", kind, severity, title, detail)
    if webhook_url and http_client is not None:
        try:
            await http_client.post(
                webhook_url,
                json={"kind": kind, "severity": severity, "title": title, "detail": detail},
                timeout=httpx.Timeout(10.0),
            )
        except httpx.HTTPError as exc:
            logger.warning("alert webhook push failed: %r", exc)
    return True


class Sentinel:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        breaker,
        http_client: httpx.AsyncClient,
    ):
        self._settings = settings
        self._sessionmaker = sessionmaker
        self._breaker = breaker
        self._client = http_client
        # 记录上次看到的每渠道熔断次数,检测新熔断事件
        self._seen_opened_counts: dict[int, int] = {}

    async def _alert(self, **kwargs) -> bool:
        return await create_alert(
            self._sessionmaker,
            webhook_url=self._settings.alert_webhook_url,
            http_client=self._client,
            **kwargs,
        )

    async def check_budgets(self) -> None:
        async with self._sessionmaker() as session:
            keys = (
                (await session.execute(select(VirtualKey).where(VirtualKey.enabled == True)))  # noqa: E712
                .scalars()
                .all()
            )
            now = utcnow()
            month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            month_tag = now.strftime("%Y-%m")
            for key in keys:
                if not key.monthly_budget_usd:
                    continue
                spent = float(
                    (
                        await session.execute(
                            select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0)).where(
                                RequestLog.virtual_key_id == key.id,
                                RequestLog.created_at >= month_start,
                            )
                        )
                    ).scalar_one()
                )
                ratio = spent / key.monthly_budget_usd
                for threshold, severity in ((1.0, "critical"), (0.8, "warning")):
                    if ratio >= threshold:
                        await self._alert(
                            kind="budget",
                            severity=severity,
                            title=f"key「{key.name}」预算已用 {ratio * 100:.0f}%",
                            detail=f"本月花费 ${spent:.4f} / 预算 ${key.monthly_budget_usd:.2f}",
                            dedupe_key=f"budget-{int(threshold * 100)}-{key.id}-{month_tag}",
                        )
                        break  # 报最高档即可

    async def check_breakers(self) -> None:
        snapshot = self._breaker.snapshot()
        for channel_id, info in snapshot.items():
            opened = info.get("opened_count", 0)
            seen = self._seen_opened_counts.get(channel_id, 0)
            if opened > seen and info.get("state") == "open":
                await self._alert(
                    kind="breaker",
                    severity="critical",
                    title=f"渠道 #{channel_id} 已熔断(第 {opened} 次)",
                    detail=(
                        f"窗口错误率 {info.get('error_rate', 0) * 100:.0f}%,"
                        f"冷却 {info.get('cooldown_remaining_s', 0)}s 后进入半开探测"
                    ),
                    dedupe_key=f"breaker-{channel_id}-{opened}",
                )
            self._seen_opened_counts[channel_id] = max(seen, opened)

    async def check_anomalies(self) -> None:
        """key 消耗突增:近 1 小时成本 > max(下限, 因子 x 前 24h 小时均值)。"""
        async with self._sessionmaker() as session:
            now = utcnow()
            hour_ago = now - timedelta(hours=1)
            day_ago = now - timedelta(hours=25)
            rows = (
                await session.execute(
                    select(
                        RequestLog.virtual_key_id,
                        func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
                    )
                    .where(RequestLog.created_at >= hour_ago, RequestLog.virtual_key_id != None)  # noqa: E711
                    .group_by(RequestLog.virtual_key_id)
                )
            ).all()
            for key_id, last_hour_cost in rows:
                last_hour_cost = float(last_hour_cost)
                if last_hour_cost < self._settings.anomaly_min_usd:
                    continue
                baseline = float(
                    (
                        await session.execute(
                            select(func.coalesce(func.sum(RequestLog.cost_usd), 0.0)).where(
                                RequestLog.virtual_key_id == key_id,
                                RequestLog.created_at >= day_ago,
                                RequestLog.created_at < hour_ago,
                            )
                        )
                    ).scalar_one()
                ) / 24.0
                threshold = max(
                    self._settings.anomaly_min_usd, baseline * self._settings.anomaly_factor
                )
                if last_hour_cost >= threshold:
                    key = await session.get(VirtualKey, key_id)
                    name = key.name if key else str(key_id)
                    await self._alert(
                        kind="anomaly",
                        severity="critical",
                        title=f"key「{name}」消耗异常突增",
                        detail=(
                            f"近 1 小时 ${last_hour_cost:.4f},前 24h 小时均值 ${baseline:.4f}"
                            "——检查是否 key 泄漏"
                        ),
                        dedupe_key=f"anomaly-{key_id}-{now.strftime('%Y%m%d%H')}",
                    )

    async def check_error_rate(self) -> None:
        async with self._sessionmaker() as session:
            since = utcnow() - timedelta(minutes=5)
            total, errors = (
                await session.execute(
                    select(
                        func.count(RequestLog.id),
                        func.coalesce(
                            func.sum(case((RequestLog.status == "error", 1), else_=0)), 0
                        ),
                    ).where(RequestLog.created_at >= since)
                )
            ).one()
            if total >= self._settings.error_rate_alert_min_requests:
                rate = errors / total
                if rate >= self._settings.error_rate_alert_threshold:
                    await self._alert(
                        kind="error_rate",
                        severity="critical",
                        title=f"近 5 分钟错误率 {rate * 100:.0f}%",
                        detail=f"{errors}/{total} 请求失败——检查上游渠道状态",
                        dedupe_key=f"error-rate-{utcnow().strftime('%Y%m%d%H%M')[:-1]}",  # 10 分钟粒度
                    )

    async def daily_report(self) -> None:
        """每天一条昨日汇总(以本地系统日期滚动)。"""
        async with self._sessionmaker() as session:
            now = utcnow()
            yesterday = (now - timedelta(days=1)).date()
            start = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
            end = start + timedelta(days=1)
            total, cost, tokens, cache_hits = (
                await session.execute(
                    select(
                        func.count(RequestLog.id),
                        func.coalesce(func.sum(RequestLog.cost_usd), 0.0),
                        func.coalesce(func.sum(RequestLog.total_tokens), 0),
                        func.coalesce(
                            func.sum(case((RequestLog.cache_hit == True, 1), else_=0)), 0  # noqa: E712
                        ),
                    ).where(RequestLog.created_at >= start, RequestLog.created_at < end)
                )
            ).one()
            if total == 0:
                return
            await self._alert(
                kind="daily_report",
                severity="info",
                title=f"{yesterday} 日报:{total} 请求 / ${float(cost):.4f}",
                detail=(
                    f"token {int(tokens)},缓存命中 {int(cache_hits)} 次"
                    f"(命中率 {int(cache_hits) / total * 100:.1f}%)"
                ),
                dedupe_key=f"daily-report-{yesterday.isoformat()}",
            )

    async def run_once(self) -> None:
        for check in (
            self.check_budgets,
            self.check_breakers,
            self.check_anomalies,
            self.check_error_rate,
            self.daily_report,
        ):
            try:
                await check()
            except Exception:
                logger.exception("sentinel check failed: %s", check.__name__)

    async def run_forever(self) -> None:
        while True:
            await self.run_once()
            await asyncio.sleep(self._settings.sentinel_interval_seconds)
