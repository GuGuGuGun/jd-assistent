"""
LangGraph checkpoint 存储抽象。

设计意图：
1. 当前环境仅保证 `langgraph.checkpoint.memory.InMemorySaver` 可用，因此默认继续保持内存模式。
2. 当 Redis 可用时，用“内存 saver + Redis 线程级快照”的方式提供依赖安全的持久化能力，
   不额外引入 postgres/sqlite saver 扩展，也不改变现有 API / SSE 契约。
3. 所有 checkpoint 统一按业务 task_id 作为 LangGraph thread_id 进行隔离，便于后续恢复与排障。
"""

from __future__ import annotations

import logging
import pickle
from collections import defaultdict
from importlib import import_module
from types import SimpleNamespace
from typing import Any, Optional, cast

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import Checkpoint, CheckpointMetadata
from langgraph.checkpoint.memory import InMemorySaver

from ..config import checkpoint_config

logger = logging.getLogger("jd_assistent.checkpoint_store")

try:
    redis_module = import_module("redis")
    Redis = getattr(redis_module, "Redis")
    REDIS_AVAILABLE = True
except ModuleNotFoundError:  # pragma: no cover
    redis_module = SimpleNamespace(Redis=None)
    Redis = None
    REDIS_AVAILABLE = False


class RedisBackedCheckpointStore(InMemorySaver):
    """基于 Redis 快照的 LangGraph checkpoint 存储。

    设计说明：
    - LangGraph 运行时仍使用官方 `InMemorySaver` 的读写协议，避免自定义底层序列化协议与版本耦合。
    - 每次线程有新 checkpoint / writes 写入后，将该线程相关的内存索引整体快照到 Redis。
    - 新进程再次读取同一 `thread_id` 时，会先从 Redis 回填到内存，再交给 InMemorySaver 继续工作。
    """

    def __init__(self, *, redis_client, key_prefix: str):
        super().__init__()
        self._redis_client = redis_client
        self._key_prefix = key_prefix
        self._loaded_threads: set[str] = set()

    def _thread_key(self, thread_id: str) -> str:
        return f"{self._key_prefix}:{thread_id}"

    def _load_thread_if_needed(self, thread_id: str):
        if thread_id in self._loaded_threads:
            return

        raw_payload = self._redis_client.get(self._thread_key(thread_id))
        if raw_payload:
            payload = pickle.loads(raw_payload)

            # 设计意图：Redis 中只存单个 thread 的快照，恢复时只回填该 thread 相关的数据，
            # 这样既能保证 task 级隔离，也能避免在 worker 启动时把所有任务一次性加载到内存。
            thread_storage = defaultdict(dict)
            thread_storage.update(payload.get("storage", {}))
            self.storage[thread_id] = thread_storage

            for write_key, write_value in payload.get("writes", {}).items():
                self.writes[write_key] = write_value

            for blob_key, blob_value in payload.get("blobs", {}).items():
                self.blobs[blob_key] = blob_value

        self._loaded_threads.add(thread_id)

    def _build_thread_snapshot(self, thread_id: str) -> dict[str, Any]:
        return {
            "storage": dict(self.storage.get(thread_id, {})),
            "writes": {
                key: value for key, value in self.writes.items() if key[0] == thread_id
            },
            "blobs": {
                key: value for key, value in self.blobs.items() if key[0] == thread_id
            },
        }

    def _persist_thread_snapshot(self, thread_id: str):
        payload = self._build_thread_snapshot(thread_id)
        if not payload["storage"] and not payload["writes"] and not payload["blobs"]:
            self._redis_client.delete(self._thread_key(thread_id))
            return

        self._redis_client.set(
            self._thread_key(thread_id),
            pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL),
        )

    def _get_thread_id(self, config: RunnableConfig) -> str:
        configurable = config.get("configurable")
        if not configurable or "thread_id" not in configurable:
            raise ValueError("checkpoint config 缺少 configurable.thread_id")
        return str(configurable["thread_id"])

    def has_thread(self, thread_id: str) -> bool:
        self._load_thread_if_needed(thread_id)
        return bool(self.storage.get(thread_id))

    def get_tuple(self, config: RunnableConfig):
        thread_id = self._get_thread_id(config)
        self._load_thread_if_needed(thread_id)
        return super().get_tuple(config)

    def list(self, config, *, filter=None, before=None, limit=None):
        if config is not None:
            thread_id = self._get_thread_id(config)
            self._load_thread_if_needed(thread_id)
        yield from super().list(config, filter=filter, before=before, limit=limit)

    def put(
        self,
        config,
        checkpoint: Checkpoint,
        metadata: CheckpointMetadata,
        new_versions,
    ):
        thread_id = self._get_thread_id(config)
        self._load_thread_if_needed(thread_id)
        updated_config = super().put(config, checkpoint, metadata, new_versions)
        self._persist_thread_snapshot(thread_id)
        return updated_config

    def put_writes(self, config, writes, task_id: str, task_path: str = "") -> None:
        thread_id = self._get_thread_id(config)
        self._load_thread_if_needed(thread_id)
        super().put_writes(config, writes, task_id, task_path)
        self._persist_thread_snapshot(thread_id)

    def delete_thread(self, thread_id: str) -> None:
        self._load_thread_if_needed(thread_id)
        super().delete_thread(thread_id)
        self._persist_thread_snapshot(thread_id)
        self._loaded_threads.discard(thread_id)

    async def aget_tuple(self, config):
        return self.get_tuple(config)

    async def alist(self, config, *, filter=None, before=None, limit=None):
        for item in self.list(config, filter=filter, before=before, limit=limit):
            yield item

    async def aput(self, config, checkpoint, metadata, new_versions):
        return self.put(config, checkpoint, metadata, new_versions)

    async def aput_writes(self, config, writes, task_id: str, task_path: str = ""):
        self.put_writes(config, writes, task_id, task_path)

    async def adelete_thread(self, thread_id: str):
        self.delete_thread(thread_id)


def build_checkpoint_config(
    task_id: str,
    *,
    checkpoint_ns: str = "",
    checkpoint_id: Optional[str] = None,
) -> RunnableConfig:
    """构建 LangGraph checkpoint 配置。"""
    configurable = {
        "thread_id": task_id,
        "checkpoint_ns": checkpoint_ns,
    }
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    return cast(RunnableConfig, {"configurable": configurable})


def create_checkpoint_store(
    *,
    backend: str = "memory",
    redis_url: str = "",
    key_prefix: str = "jd-assistent:checkpoint",
):
    """按配置创建 checkpoint store，并在 Redis 不可用时自动回退。"""
    normalized_backend = (backend or "memory").strip().lower() or "memory"
    if normalized_backend != "redis":
        return InMemorySaver()

    if not redis_url:
        logger.warning("已启用 Redis checkpoint，但未提供 Redis 地址，改用内存模式")
        return InMemorySaver()

    if not REDIS_AVAILABLE or Redis is None:
        logger.warning("当前环境未安装 redis 依赖，checkpoint 改用内存模式")
        return InMemorySaver()

    redis_client = None
    try:
        redis_client = Redis.from_url(redis_url, decode_responses=False)
        redis_client.ping()
        logger.info("已启用 Redis checkpoint 持久化: %s", redis_url)
        return RedisBackedCheckpointStore(
            redis_client=redis_client,
            key_prefix=key_prefix,
        )
    except Exception as exc:
        logger.warning("Redis checkpoint 初始化失败，已回退到内存模式: %s", str(exc))
        if redis_client is not None:
            try:
                redis_client.close()
            except Exception:
                pass
        return InMemorySaver()


_checkpoint_store = None


def get_checkpoint_store():
    """获取全局 checkpoint store 单例。"""
    global _checkpoint_store
    if _checkpoint_store is None:
        _checkpoint_store = create_checkpoint_store(
            backend=checkpoint_config.BACKEND,
            redis_url=checkpoint_config.REDIS_URL,
            key_prefix=checkpoint_config.REDIS_KEY_PREFIX,
        )
    return _checkpoint_store


def has_persisted_checkpoint(task_id: str) -> bool:
    """判断指定任务是否已有可恢复 checkpoint。"""
    checkpoint_store = get_checkpoint_store()
    checkpoint_store_any = cast(Any, checkpoint_store)
    if hasattr(checkpoint_store_any, "has_thread"):
        return bool(checkpoint_store_any.has_thread(task_id))
    return checkpoint_store.get_tuple(build_checkpoint_config(task_id)) is not None


def reset_checkpoint_store_for_testing():
    """重置 checkpoint store 单例，供测试隔离使用。"""
    global _checkpoint_store
    if _checkpoint_store is not None and hasattr(_checkpoint_store, "_redis_client"):
        redis_client = getattr(_checkpoint_store, "_redis_client", None)
        if redis_client is not None and hasattr(redis_client, "close"):
            try:
                redis_client.close()
            except Exception:
                pass
    _checkpoint_store = None
