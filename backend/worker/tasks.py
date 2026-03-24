"""
Celery 任务定义。
"""

from __future__ import annotations

import asyncio

from ..services.resume_service import run_optimize_task
from ..services.task_store import task_store
from .celery_app import CELERY_AVAILABLE, celery_app


async def _run_optimize_task_in_worker(task_id: str, resume_text: str, jd_text: str):
    # 设计意图：Celery worker 是独立进程，不能依赖 FastAPI lifespan 已先执行；
    # 因此前置补齐 task_store 初始化，保持 DB 持久化与 SSE/回放链路兼容。
    await task_store.ensure_ready()
    await task_store.mark_task_started(task_id)
    await run_optimize_task(task_id=task_id, resume_text=resume_text, jd_text=jd_text)


def _run_optimize_task_job_impl(task_id: str, resume_text: str, jd_text: str):
    """Celery 同步入口，内部桥接到既有异步业务函数。"""
    asyncio.run(
        _run_optimize_task_in_worker(
            task_id=task_id,
            resume_text=resume_text,
            jd_text=jd_text,
        )
    )


def _run_optimize_task_job_unavailable(task_id: str, resume_text: str, jd_text: str):
    """无 Celery 依赖时的占位定义，供本地模式安全导入。"""
    raise RuntimeError("当前环境未安装 Celery，无法启动 Celery worker 任务。")


if CELERY_AVAILABLE and celery_app is not None:
    run_optimize_task_job = celery_app.task(name="backend.worker.run_optimize_task")(
        _run_optimize_task_job_impl
    )
else:
    run_optimize_task_job = _run_optimize_task_job_unavailable
