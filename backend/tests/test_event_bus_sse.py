"""
SSE 事件总线与回放行为测试。
"""

import asyncio
import json

import pytest

from ..api.routes import _sse_event_generator
from ..services import event_bus as event_bus_module
from ..services.task_store import task_store


def _parse_sse_chunk(chunk: str) -> dict:
    """将单个 SSE 文本块解析为便于断言的结构。"""
    parsed: dict[str, object] = {}
    for line in chunk.strip().splitlines():
        if line.startswith("event: "):
            parsed["event"] = line.removeprefix("event: ")
        if line.startswith("data: "):
            parsed["data"] = json.loads(line.removeprefix("data: "))
    return parsed


@pytest.mark.asyncio
async def test_sse_stream_replays_db_state_before_live_subscription():
    """SSE 建连时应先回放数据库中的当前任务状态，再进入实时订阅。"""
    task_id = "44444444-4444-4444-4444-444444444444"

    await task_store.create_task(task_id, jd_text="需要熟悉异步事件流处理。")
    await task_store.mark_node_start(task_id, "profile_builder", "正在提取用户画像...")
    await task_store.mark_node_complete(task_id, "profile_builder")
    await task_store.mark_review_feedback(
        task_id,
        passed=False,
        feedback="请补充与岗位相关的量化成果",
    )

    stream = _sse_event_generator(task_id)

    first_event = _parse_sse_chunk(await anext(stream))
    second_event = _parse_sse_chunk(await anext(stream))
    third_event = _parse_sse_chunk(await anext(stream))

    assert first_event == {
        "event": "node_start",
        "data": {"node": "profile_builder", "message": "正在提取用户画像..."},
    }
    assert second_event["event"] == "node_complete"
    assert second_event["data"]["node"] == "profile_builder"
    assert isinstance(second_event["data"]["duration_ms"], int)
    assert third_event == {
        "event": "review_feedback",
        "data": {
            "node": "content_reviewer",
            "passed": False,
            "feedback": "请补充与岗位相关的量化成果",
        },
    }

    next_event_task = asyncio.create_task(anext(stream))
    await asyncio.sleep(0)
    await task_store.mark_task_complete(
        task_id,
        {
            "name": "王五",
            "contact": {"email": "wangwu@example.com"},
            "summary": "拥有 6 年后端研发经验。",
            "sections": [],
        },
    )

    live_event = _parse_sse_chunk(await asyncio.wait_for(next_event_task, timeout=1.0))
    assert live_event == {
        "event": "complete",
        "data": {"task_id": task_id, "message": "简历优化完成"},
    }


@pytest.mark.asyncio
async def test_event_bus_factory_falls_back_to_in_memory_when_redis_unavailable(
    monkeypatch,
):
    """Redis 不可用时，应安全回退到进程内事件总线。"""

    class BrokenRedisClient:
        async def ping(self):
            raise RuntimeError("redis unavailable")

        async def aclose(self):
            return None

    def fake_from_url(*args, **kwargs):
        return BrokenRedisClient()

    original_available = event_bus_module.REDIS_AVAILABLE

    monkeypatch.setattr(event_bus_module, "REDIS_AVAILABLE", True)
    monkeypatch.setattr(
        event_bus_module.redis, "from_url", fake_from_url, raising=False
    )

    try:
        bus = await event_bus_module.create_event_bus(
            backend="redis",
            redis_url="redis://127.0.0.1:6399/15",
            channel_prefix="test-sse",
        )
        assert isinstance(bus, event_bus_module.InMemoryEventBus)

        subscriber = await bus.subscribe("task-fallback")
        await bus.publish(
            "task-fallback",
            {"event": "node_start", "data": {"node": "profile_builder"}},
        )

        received = await asyncio.wait_for(subscriber.get(), timeout=1.0)
        assert received == {
            "event": "node_start",
            "data": {"node": "profile_builder"},
        }
        await subscriber.close()
        await bus.close()
    finally:
        event_bus_module.REDIS_AVAILABLE = original_available
