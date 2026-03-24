"""
任务事件总线抽象。

设计意图：
1. 将任务状态持久化与 SSE 传输解耦，便于后续扩展跨进程分发。
2. Redis 仅作为可选增强；不可用时自动回退到进程内实现，保证本地和单进程场景稳定可用。
"""

from __future__ import annotations

import asyncio
import json
import logging
from importlib import import_module
from types import SimpleNamespace
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

try:
    redis = import_module("redis.asyncio")
    REDIS_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    redis = SimpleNamespace(from_url=None)
    REDIS_AVAILABLE = False

logger = logging.getLogger("jd_assistent.event_bus")

EventPayload = dict[str, Any]


class EventSubscriber(Protocol):
    """事件订阅句柄。"""

    async def get(self) -> EventPayload:
        """等待并返回下一条事件。"""
        raise NotImplementedError

    async def close(self):
        """释放订阅资源。"""
        raise NotImplementedError


class EventBus(Protocol):
    """任务事件总线接口。"""

    async def publish(self, task_id: str, event: EventPayload):
        """发布任务事件。"""
        raise NotImplementedError

    async def subscribe(self, task_id: str) -> EventSubscriber:
        """订阅指定任务的实时事件。"""
        raise NotImplementedError

    async def close(self):
        """关闭事件总线。"""
        raise NotImplementedError


class InMemoryEventSubscriber:
    """进程内订阅器。"""

    def __init__(
        self,
        queue: asyncio.Queue[EventPayload],
        on_close: Callable[[], Awaitable[None]],
    ):
        self._queue = queue
        self._on_close = on_close
        self._closed = False

    async def get(self) -> EventPayload:
        return await self._queue.get()

    async def close(self):
        if self._closed:
            return
        self._closed = True
        await self._on_close()


class InMemoryEventBus:
    """单进程事件总线实现。"""

    def __init__(self):
        self._subscribers: dict[str, list[asyncio.Queue[EventPayload]]] = defaultdict(
            list
        )
        self._lock = asyncio.Lock()

    async def publish(self, task_id: str, event: EventPayload):
        async with self._lock:
            subscribers = list(self._subscribers.get(task_id, []))

        for queue in subscribers:
            queue.put_nowait(event)

    async def subscribe(self, task_id: str) -> EventSubscriber:
        queue: asyncio.Queue[EventPayload] = asyncio.Queue()

        async with self._lock:
            self._subscribers[task_id].append(queue)

        async def _remove_queue():
            async with self._lock:
                queues = self._subscribers.get(task_id, [])
                if queue in queues:
                    queues.remove(queue)
                if not queues and task_id in self._subscribers:
                    del self._subscribers[task_id]

        return InMemoryEventSubscriber(queue=queue, on_close=_remove_queue)

    async def close(self):
        async with self._lock:
            self._subscribers.clear()


class RedisEventSubscriber:
    """Redis Pub/Sub 订阅器。"""

    def __init__(self, pubsub, channel: str):
        self._pubsub = pubsub
        self._channel = channel
        self._closed = False

    async def get(self) -> EventPayload:
        while True:
            message = await self._pubsub.get_message(
                ignore_subscribe_messages=True,
                timeout=1.0,
            )
            if message is None:
                await asyncio.sleep(0.05)
                continue

            raw_data = message.get("data")
            if isinstance(raw_data, bytes):
                raw_data = raw_data.decode("utf-8")
            return json.loads(raw_data)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        await self._pubsub.unsubscribe(self._channel)
        await self._pubsub.aclose()


class RedisEventBus:
    """基于 Redis Pub/Sub 的事件总线。"""

    def __init__(self, client, channel_prefix: str = "jd-assistent:sse"):
        self._client = client
        self._channel_prefix = channel_prefix

    def _build_channel(self, task_id: str) -> str:
        return f"{self._channel_prefix}:{task_id}"

    async def publish(self, task_id: str, event: EventPayload):
        channel = self._build_channel(task_id)
        await self._client.publish(channel, json.dumps(event, ensure_ascii=False))

    async def subscribe(self, task_id: str) -> EventSubscriber:
        channel = self._build_channel(task_id)
        pubsub = self._client.pubsub()
        await pubsub.subscribe(channel)
        return RedisEventSubscriber(pubsub=pubsub, channel=channel)

    async def close(self):
        await self._client.aclose()


async def create_event_bus(
    backend: str = "memory",
    redis_url: str = "",
    channel_prefix: str = "jd-assistent:sse",
) -> EventBus:
    """按配置创建事件总线，并在 Redis 不可用时自动回退。"""
    normalized_backend = backend.strip().lower() or "memory"
    if normalized_backend != "redis":
        return InMemoryEventBus()

    if not redis_url:
        logger.warning("已启用 Redis 事件总线，但未提供 REDIS_URL，改用内存总线")
        return InMemoryEventBus()

    if not REDIS_AVAILABLE or getattr(redis, "from_url", None) is None:
        logger.warning("未安装 redis 依赖，改用内存事件总线")
        return InMemoryEventBus()

    client = None
    try:
        client = redis.from_url(redis_url, decode_responses=False)
        await client.ping()
        logger.info("已启用 Redis 事件总线: %s", redis_url)
        return RedisEventBus(client=client, channel_prefix=channel_prefix)
    except Exception as exc:
        # 设计意图：当前切片仍以 BackgroundTasks 为执行核心，传输层故障不能拖垮任务主链路。
        logger.warning("Redis 事件总线初始化失败，已回退到内存实现: %s", str(exc))
        if client is not None:
            try:
                await client.aclose()
            except Exception:
                pass
        return InMemoryEventBus()
