"""Prometheus 指标:基础设施观测走 Grafana,业务操作走自研控制台(取舍见架构方案)。

用独立 Registry 而不是全局默认 registry:测试里会创建多个 app 实例,
全局 registry 会因重复注册报错。
"""
from prometheus_client import CollectorRegistry, Counter, Gauge, Histogram


class Metrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.requests_total = Counter(
            "gateway_requests_total",
            "Chat completion requests",
            labelnames=("model", "channel", "status", "cache"),
            registry=self.registry,
        )
        self.request_duration = Histogram(
            "gateway_request_duration_seconds",
            "End-to-end request latency",
            labelnames=("model", "stream"),
            buckets=(0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300),
            registry=self.registry,
        )
        self.first_token_seconds = Histogram(
            "gateway_first_token_seconds",
            "Time to first streamed token",
            labelnames=("model", "channel"),
            buckets=(0.1, 0.25, 0.5, 1, 2, 3, 5, 10, 20, 60),
            registry=self.registry,
        )
        self.tokens_total = Counter(
            "gateway_tokens_total",
            "Token usage",
            labelnames=("model", "channel", "direction"),  # direction: prompt/completion
            registry=self.registry,
        )
        self.cost_usd_total = Counter(
            "gateway_cost_usd_total",
            "Accumulated cost in USD",
            labelnames=("model", "channel"),
            registry=self.registry,
        )
        self.cache_events_total = Counter(
            "gateway_cache_events_total",
            "Cache lookups by outcome",
            labelnames=("kind", "outcome"),  # kind: exact/semantic, outcome: hit/miss
            registry=self.registry,
        )
        self.ratelimit_rejections_total = Counter(
            "gateway_ratelimit_rejections_total",
            "Requests rejected by rate limiting",
            labelnames=("key_name",),
            registry=self.registry,
        )
        self.circuit_state = Gauge(
            "gateway_circuit_state",
            "Circuit breaker state per channel (0=closed 1=half_open 2=open)",
            labelnames=("channel",),
            registry=self.registry,
        )
        self.failovers_total = Counter(
            "gateway_failovers_total",
            "Channel failover occurrences",
            labelnames=("from_channel",),
            registry=self.registry,
        )

    def record_request(
        self,
        model: str,
        channel: str,
        status: str,
        cache: str,
        stream: bool,
        duration_s: float,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        cost_usd: float | None = None,
        first_token_s: float | None = None,
    ) -> None:
        self.requests_total.labels(model=model, channel=channel, status=status, cache=cache).inc()
        self.request_duration.labels(model=model, stream=str(stream).lower()).observe(duration_s)
        if prompt_tokens:
            self.tokens_total.labels(model=model, channel=channel, direction="prompt").inc(
                prompt_tokens
            )
        if completion_tokens:
            self.tokens_total.labels(model=model, channel=channel, direction="completion").inc(
                completion_tokens
            )
        if cost_usd:
            self.cost_usd_total.labels(model=model, channel=channel).inc(cost_usd)
        if first_token_s is not None:
            self.first_token_seconds.labels(model=model, channel=channel).observe(first_token_s)


_STATE_VALUE = {"closed": 0, "half_open": 1, "open": 2}


def update_circuit_gauges(metrics: Metrics, snapshot: dict[int, dict], names: dict[int, str]) -> None:
    for channel_id, info in snapshot.items():
        name = names.get(channel_id, str(channel_id))
        metrics.circuit_state.labels(channel=name).set(_STATE_VALUE.get(info["state"], 0))
