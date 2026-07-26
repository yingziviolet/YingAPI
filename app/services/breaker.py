"""渠道熔断器:滑动窗口错误率 -> OPEN(快速失败+failover)-> HALF_OPEN 放量探测 -> CLOSED。

状态机全程可通过控制面 API 观察(面试考点:比背《熔断器模式》八股强)。
状态存进程内:单机/exe 形态天然正确;多 worker 部署时各 worker 独立熔断,
行为上更保守(每个 worker 自己探测恢复),可接受。
"""
import time
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from app.config import Settings


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


@dataclass
class _ChannelCircuit:
    state: CircuitState = CircuitState.CLOSED
    # 滑动窗口:(时间戳, 是否失败)
    events: deque = field(default_factory=deque)
    opened_at: float = 0.0
    half_open_inflight: int = 0
    # 观测数据
    opened_count: int = 0  # 历史熔断次数
    last_error_rate: float = 0.0


class CircuitBreaker:
    """按 channel_id 维护熔断状态。线程模型:单事件循环内使用,无锁。"""

    def __init__(self, settings: Settings, clock=time.monotonic):
        self._settings = settings
        self._clock = clock
        self._circuits: dict[int, _ChannelCircuit] = {}

    def _circuit(self, channel_id: int) -> _ChannelCircuit:
        if channel_id not in self._circuits:
            self._circuits[channel_id] = _ChannelCircuit()
        return self._circuits[channel_id]

    def _prune(self, c: _ChannelCircuit, now: float) -> None:
        cutoff = now - self._settings.cb_window_seconds
        while c.events and c.events[0][0] < cutoff:
            c.events.popleft()

    def allow(self, channel_id: int) -> bool:
        """请求前询问:该渠道现在能不能打。OPEN 冷却期直接拒;到期转 HALF_OPEN 放探测。"""
        c = self._circuit(channel_id)
        now = self._clock()
        if c.state == CircuitState.CLOSED:
            return True
        if c.state == CircuitState.OPEN:
            if now - c.opened_at >= self._settings.cb_open_seconds:
                c.state = CircuitState.HALF_OPEN
                c.half_open_inflight = 0
            else:
                return False
        # HALF_OPEN:限量放行探测
        if c.half_open_inflight < self._settings.cb_half_open_probes:
            c.half_open_inflight += 1
            return True
        return False

    def record_success(self, channel_id: int) -> None:
        c = self._circuit(channel_id)
        now = self._clock()
        c.events.append((now, False))
        self._prune(c, now)
        if c.state == CircuitState.HALF_OPEN:
            # 探测成功即恢复(保守起见一次成功就闭合;失败会立刻重开)
            c.state = CircuitState.CLOSED
            c.events.clear()
            c.last_error_rate = 0.0

    def record_failure(self, channel_id: int) -> None:
        c = self._circuit(channel_id)
        now = self._clock()
        c.events.append((now, True))
        self._prune(c, now)
        if c.state == CircuitState.HALF_OPEN:
            # 探测失败:重新熔断,冷却期重算
            self._open(c, now)
            return
        if c.state == CircuitState.CLOSED:
            total = len(c.events)
            failures = sum(1 for _, failed in c.events if failed)
            error_rate = failures / total if total else 0.0
            c.last_error_rate = error_rate
            if total >= self._settings.cb_min_requests and error_rate >= self._settings.cb_error_threshold:
                self._open(c, now)

    def _open(self, c: _ChannelCircuit, now: float) -> None:
        c.state = CircuitState.OPEN
        c.opened_at = now
        c.opened_count += 1
        c.half_open_inflight = 0

    def reset(self, channel_id: int) -> None:
        """控制台手动复位。"""
        self._circuits.pop(channel_id, None)

    def snapshot(self) -> dict[int, dict]:
        """控制面观测:各渠道熔断状态。"""
        now = self._clock()
        out = {}
        for channel_id, c in self._circuits.items():
            self._prune(c, now)
            total = len(c.events)
            failures = sum(1 for _, failed in c.events if failed)
            out[channel_id] = {
                "state": c.state.value,
                "window_requests": total,
                "window_failures": failures,
                "error_rate": round(failures / total, 4) if total else 0.0,
                "opened_count": c.opened_count,
                "cooldown_remaining_s": (
                    max(0, round(self._settings.cb_open_seconds - (now - c.opened_at), 1))
                    if c.state == CircuitState.OPEN
                    else 0
                ),
            }
        return out

    def state_of(self, channel_id: int) -> CircuitState:
        return self._circuit(channel_id).state
