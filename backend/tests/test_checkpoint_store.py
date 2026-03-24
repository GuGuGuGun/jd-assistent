"""
LangGraph checkpoint 存储测试。
"""

from __future__ import annotations

from typing import cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint


def test_build_workflow_compiles_with_checkpoint_store(monkeypatch):
    """工作流编译时，应显式注入 checkpoint store。"""
    from ..graph import workflow

    captured: dict[str, object] = {}
    sentinel_checkpointer = object()

    def fake_compile(self, *args, **kwargs):
        captured.update(kwargs)
        return "compiled-workflow"

    monkeypatch.setattr(
        workflow, "get_checkpoint_store", lambda: sentinel_checkpointer, raising=False
    )
    monkeypatch.setattr(workflow.StateGraph, "compile", fake_compile)

    compiled = workflow.build_workflow()

    assert compiled == "compiled-workflow"
    assert captured["checkpointer"] is sentinel_checkpointer


def test_redis_checkpoint_store_persists_and_reloads_by_task_id():
    """Redis-backed store 应按 task_id/thread_id 维度持久化并支持重新加载。"""
    from ..services.checkpoint_store import (
        RedisBackedCheckpointStore,
        build_checkpoint_config,
    )

    class FakeRedis:
        def __init__(self):
            self.data: dict[str, bytes] = {}

        def get(self, key: str):
            return self.data.get(key)

        def set(self, key: str, value: bytes):
            self.data[key] = value

        def delete(self, key: str):
            self.data.pop(key, None)

        def ping(self):
            return True

        def close(self):
            return None

    redis_client = FakeRedis()
    first_store = RedisBackedCheckpointStore(
        redis_client=redis_client,
        key_prefix="test:checkpoint",
    )
    task_config = cast(RunnableConfig, build_checkpoint_config("task-redis-1"))

    checkpoint = empty_checkpoint()
    first_store.put(
        task_config,
        checkpoint,
        {"source": "input", "step": 1},
        {},
    )

    reloaded_store = RedisBackedCheckpointStore(
        redis_client=redis_client,
        key_prefix="test:checkpoint",
    )
    restored = reloaded_store.get_tuple(task_config)

    assert restored is not None
    restored_configurable = restored.config.get("configurable", {})
    assert restored_configurable.get("thread_id") == "task-redis-1"
    assert restored.metadata.get("source") == "input"
    assert restored.metadata.get("step") == 1
    assert (
        reloaded_store.get_tuple(
            cast(RunnableConfig, build_checkpoint_config("task-redis-2"))
        )
        is None
    )
