"""实时请求流(live-tail):进程内发布/订阅,控制台通过 WebSocket 订阅。

计量器每落一笔账就向所有订阅者推一条事件;慢消费者队列满时丢弃(观测通道,
不承诺完整性——完整数据永远以 request_logs 表为准)。
"""
import asyncio
import time


class LiveTailHub:
    def __init__(self, queue_size: int = 200):
        self._queue_size = queue_size
        self._subscribers: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def publish(self, event: dict) -> None:
        payload = {"ts": time.time(), **event}
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                pass  # 慢消费者:丢弃本条,不阻塞数据面

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)
