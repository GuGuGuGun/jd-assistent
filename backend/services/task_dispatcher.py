"""
任务派发器抽象。

设计意图：
1. API 路由只负责创建任务记录，不再直接依赖 FastAPI BackgroundTasks。
2. 本地模式继续用进程内异步任务兜底，保证 Windows / 单机开发环境零额外依赖。
3. Celery 模式只做“薄包装入队”，真正业务逻辑仍复用 run_optimize_task。
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

from ..config import celery_config, task_dispatcher_config
from .resume_service import run_optimize_task
from .task_store import task_store

logger = logging.getLogger("jd_assistent.task_dispatcher")


class TaskDispatcher(Protocol):
    """任务派发器接口。"""

    async def dispatch(self, task_id: str, resume_text: str, jd_text: str):
        """派发任务到对应执行后端。"""
        raise NotImplementedError


class LocalTaskDispatcher:
    """本地异步派发器。"""

    def __init__(self):
        self._running_tasks: set[asyncio.Task] = set()

    async def dispatch(self, task_id: str, resume_text: str, jd_text: str):
        task = asyncio.create_task(
            self._run_local_task(
                task_id=task_id,
                resume_text=resume_text,
                jd_text=jd_text,
            )
        )
        self._running_tasks.add(task)
        task.add_done_callback(self._running_tasks.discard)

    async def _run_local_task(self, task_id: str, resume_text: str, jd_text: str):
        # 设计意图：本地模式不依赖 FastAPI lifespan 或 Celery worker 启动流程，
        # 因此在每次真正执行任务前都补一次 task_store 预热，确保测试与脚本入口一致可用。
        await task_store.ensure_ready()
        await task_store.mark_task_started(task_id)
        await run_optimize_task(
            task_id=task_id, resume_text=resume_text, jd_text=jd_text
        )


class CeleryTaskDispatcher:
    """Celery 派发器。"""

    def __init__(self, celery_task):
        self._celery_task = celery_task

    async def dispatch(self, task_id: str, resume_text: str, jd_text: str):
        if not hasattr(self._celery_task, "apply_async"):
            raise RuntimeError(
                "当前环境未启用 Celery 运行时，无法使用 celery 调度模式。"
            )

        self._celery_task.apply_async(
            args=(task_id, resume_text, jd_text),
            task_id=task_id,
        )


def create_task_dispatcher(
    mode: str | None = None,
    broker_url: str | None = None,
    result_backend: str | None = None,
) -> TaskDispatcher:
    """根据配置创建任务派发器。"""
    normalized_mode = (mode or task_dispatcher_config.MODE).strip().lower() or "local"

    if normalized_mode != "celery":
        if normalized_mode != "local":
            logger.warning("未知任务派发模式 %s，已回退到 local", normalized_mode)
        return LocalTaskDispatcher()

    from ..worker.celery_app import configure_celery_app
    from ..worker.tasks import run_optimize_task_job

    configure_celery_app(
        broker_url=broker_url or celery_config.BROKER_URL,
        result_backend=result_backend or celery_config.RESULT_BACKEND,
    )
    return CeleryTaskDispatcher(celery_task=run_optimize_task_job)
