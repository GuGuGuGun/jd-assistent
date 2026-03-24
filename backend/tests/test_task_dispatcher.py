"""
任务派发器与提交流程测试。
"""

from __future__ import annotations

import io

import pytest

from ..api import routes
from ..config import auth_config
from ..db import database
from ..db.models import Task
from ..services.task_store import task_store


def test_dispatcher_factory_returns_local_dispatcher_by_default():
    """默认模式下，应返回无需 Celery/Redis 的本地派发器。"""
    from ..services.task_dispatcher import (
        LocalTaskDispatcher,
        create_task_dispatcher,
    )

    dispatcher = create_task_dispatcher(mode="local")

    assert isinstance(dispatcher, LocalTaskDispatcher)


def test_dispatcher_factory_returns_celery_dispatcher_with_business_task_id():
    """Celery 模式下，应构造使用业务 task_id 入队的派发器。"""
    from ..services.task_dispatcher import (
        CeleryTaskDispatcher,
        create_task_dispatcher,
    )

    dispatcher = create_task_dispatcher(
        mode="celery",
        broker_url="redis://127.0.0.1:6379/0",
        result_backend="redis://127.0.0.1:6379/1",
    )

    assert isinstance(dispatcher, CeleryTaskDispatcher)


@pytest.mark.asyncio
async def test_celery_dispatcher_reuses_business_task_id_when_enqueueing():
    """Celery 入队时，应显式复用业务 task_id 作为 Celery task_id。"""
    from ..services.task_dispatcher import CeleryTaskDispatcher

    captured: dict[str, object] = {}

    class FakeCeleryTask:
        def apply_async(self, args=None, task_id=None):
            captured["args"] = args
            captured["task_id"] = task_id

    dispatcher = CeleryTaskDispatcher(celery_task=FakeCeleryTask())

    await dispatcher.dispatch(
        task_id="55555555-5555-5555-5555-555555555555",
        resume_text="原始简历",
        jd_text="目标 JD",
    )

    assert captured == {
        "args": (
            "55555555-5555-5555-5555-555555555555",
            "原始简历",
            "目标 JD",
        ),
        "task_id": "55555555-5555-5555-5555-555555555555",
    }


@pytest.mark.asyncio
async def test_celery_dispatcher_enqueue_does_not_mark_task_processing_early():
    """Celery 入队本身不应提前把数据库状态改成 processing。"""
    from ..services.task_dispatcher import CeleryTaskDispatcher

    task_id = "56555555-5555-5555-5555-555555555555"
    await task_store.create_task(task_id, jd_text="需要可靠的异步任务调度。")

    class FakeCeleryTask:
        def apply_async(self, args=None, task_id=None):
            return None

    dispatcher = CeleryTaskDispatcher(celery_task=FakeCeleryTask())

    await dispatcher.dispatch(
        task_id=task_id,
        resume_text="原始简历",
        jd_text="目标 JD",
    )

    async with database.async_session_factory() as session:
        stored_task = await session.get(Task, task_id)

    assert stored_task is not None
    assert stored_task.status == "pending"


@pytest.mark.asyncio
async def test_local_dispatcher_marks_task_processing_when_execution_starts(
    monkeypatch,
):
    """本地执行入口启动时，应将数据库状态切到 processing。"""
    from ..services.task_dispatcher import LocalTaskDispatcher

    task_id = "57555555-5555-5555-5555-555555555555"
    observed_status: dict[str, str] = {}
    await task_store.create_task(task_id, jd_text="需要稳定的本地回退模式。")

    async def fake_run_optimize_task(*, task_id: str, resume_text: str, jd_text: str):
        async with database.async_session_factory() as session:
            stored_task = await session.get(Task, task_id)
        assert stored_task is not None
        observed_status["status"] = stored_task.status

    monkeypatch.setattr(
        "backend.services.task_dispatcher.run_optimize_task",
        fake_run_optimize_task,
    )

    dispatcher = LocalTaskDispatcher()
    await dispatcher._run_local_task(task_id, "原始简历", "目标 JD")

    assert observed_status == {"status": "processing"}


@pytest.mark.asyncio
async def test_worker_entry_marks_task_processing_when_execution_starts(monkeypatch):
    """Worker 真正开始执行任务时，应将数据库状态切到 processing。"""
    from ..worker import tasks as worker_tasks

    task_id = "58555555-5555-5555-5555-555555555555"
    observed_status: dict[str, str] = {}
    await task_store.create_task(task_id, jd_text="需要独立 worker 执行。")

    async def fake_run_optimize_task(*, task_id: str, resume_text: str, jd_text: str):
        async with database.async_session_factory() as session:
            stored_task = await session.get(Task, task_id)
        assert stored_task is not None
        observed_status["status"] = stored_task.status

    monkeypatch.setattr(worker_tasks, "run_optimize_task", fake_run_optimize_task)

    await worker_tasks._run_optimize_task_in_worker(task_id, "原始简历", "目标 JD")

    assert observed_status == {"status": "processing"}


def test_create_optimize_task_uses_dispatcher_and_keeps_response_contract(
    client,
    monkeypatch,
):
    """提交任务时，应保持既有响应结构，同时改由 dispatcher 负责后台派发。"""

    recorded: dict[str, str] = {}

    class FakeDispatcher:
        async def dispatch(self, task_id: str, resume_text: str, jd_text: str):
            recorded["task_id"] = task_id
            recorded["resume_text"] = resume_text
            recorded["jd_text"] = jd_text

    monkeypatch.setattr(routes, "task_dispatcher", FakeDispatcher())

    register_response = client.post(
        "/api/v1/auth/register",
        json={"email": "dispatcher@example.com", "password": "password123"},
    )
    assert register_response.status_code == 201
    access_token = register_response.json()["access_token"]

    response = client.post(
        "/api/v1/optimize",
        files={
            "resume_file": (
                "resume.txt",
                io.BytesIO("张三\n5 年后端经验".encode("utf-8")),
                "text/plain",
            )
        },
        data={"jd_text": "需要熟悉 FastAPI、异步任务调度与 SSE 进度推送。"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200

    payload = response.json()
    assert payload["task_id"] == recorded["task_id"]
    assert payload["status"] == "processing"
    assert payload["message"] == "任务已提交，正在处理中"
    assert recorded["resume_text"] == "张三\n5 年后端经验"
    assert recorded["jd_text"] == "需要熟悉 FastAPI、异步任务调度与 SSE 进度推送。"


def test_create_optimize_task_sanitizes_resume_and_jd_before_dispatch(
    client,
    monkeypatch,
):
    """提交任务时，应清洗原始文本中的 STAR 标记与异常转义残留。"""

    recorded: dict[str, str] = {}

    class FakeDispatcher:
        async def dispatch(self, task_id: str, resume_text: str, jd_text: str):
            recorded["task_id"] = task_id
            recorded["resume_text"] = resume_text
            recorded["jd_text"] = jd_text

    monkeypatch.setattr(routes, "task_dispatcher", FakeDispatcher())

    register_response = client.post(
        "/api/v1/auth/register",
        json={"email": "sanitize-dispatcher@example.com", "password": "password123"},
    )
    assert register_response.status_code == 201
    access_token = register_response.json()["access_token"]

    response = client.post(
        "/api/v1/optimize",
        files={
            "resume_file": (
                "resume.txt",
                io.BytesIO("【S】负责后端架构\n【R】性能提升 30%\\R".encode("utf-8")),
                "text/plain",
            )
        },
        data={"jd_text": "【T】需要熟悉分布式系统\\R以及 SQLAlchemy。"},
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    assert recorded["resume_text"] == "负责后端架构\n性能提升 30%"
    assert recorded["jd_text"] == "需要熟悉分布式系统\n以及 SQLAlchemy。"
