"""
任务持久化行为测试。
"""

import asyncio

import pytest
from sqlalchemy import select

from ..auth.jwt_handler import create_access_token
from ..db import database
from ..db.models import Resume, Task
from ..services.task_store import SYSTEM_USER_ID, task_store


@pytest.mark.asyncio
async def test_task_store_persists_pending_status_at_creation_time():
    """任务刚创建时，数据库应落为 pending，但 API 视图仍兼容返回 processing。"""
    task_id = "10111111-1111-1111-1111-111111111111"

    record = await task_store.create_task(
        task_id,
        jd_text="需要熟悉异步执行链路。",
        original_file="resume.md",
    )

    assert record.status == "processing"
    assert all(log.status == "pending" for log in record.node_logs)

    async with database.async_session_factory() as session:
        stored_task = await session.get(Task, task_id)

    assert stored_task is not None
    assert stored_task.status == "pending"
    assert all(log["status"] == "pending" for log in stored_task.node_logs)


@pytest.mark.asyncio
async def test_task_store_persists_completed_task_lifecycle():
    """任务完成后，任务状态、节点日志与最终结果都应写入数据库。"""
    task_id = "11111111-1111-1111-1111-111111111111"
    result_payload = {
        "name": "张三",
        "contact": {"email": "zhangsan@example.com"},
        "summary": "拥有 5 年后端开发经验。",
        "sections": [],
    }

    await task_store.create_task(
        task_id,
        jd_text="需要熟悉 FastAPI、SQLAlchemy 与异步编程。",
        original_file="resume.md",
    )
    await task_store.mark_node_start(task_id, "profile_builder", "正在提取用户画像...")
    await task_store.mark_node_complete(task_id, "profile_builder")
    await task_store.mark_review_feedback(
        task_id, passed=False, feedback="请补充量化结果"
    )
    await task_store.mark_task_complete(task_id, result_payload)

    record = await task_store.get_task(task_id)

    assert record is not None
    assert record.status == "completed"
    assert record.result == result_payload
    assert record.error is None
    assert record.created_at > 0
    assert record.completed_at is not None

    reviewer_log = next(
        log for log in record.node_logs if log.node == "content_reviewer"
    )
    assert reviewer_log.message == "请补充量化结果"

    async with database.async_session_factory() as session:
        stored_task = await session.get(Task, task_id)
        stored_resume = (
            await session.execute(select(Resume).where(Resume.task_id == task_id))
        ).scalar_one()

    assert stored_task is not None
    assert stored_task.status == "completed"
    assert stored_task.target_jd == "需要熟悉 FastAPI、SQLAlchemy 与异步编程。"
    assert stored_task.original_file == "resume.md"
    assert stored_task.duration_ms is not None
    assert stored_task.node_logs[0]["status"] == "done"
    assert stored_resume.render_data == result_payload
    assert stored_resume.version == 1


@pytest.mark.asyncio
async def test_task_store_persists_failed_task_error():
    """任务失败时，应将失败状态和错误信息落盘。"""
    task_id = "22222222-2222-2222-2222-222222222222"

    await task_store.create_task(task_id, jd_text="需要具备问题定位能力。")
    await task_store.mark_node_start(
        task_id, "content_optimizer", "正在优化简历内容..."
    )
    await task_store.mark_node_error(task_id, "content_optimizer", "LLM 返回结构异常")
    await task_store.mark_task_failed(task_id, "工作流执行失败")

    record = await task_store.get_task(task_id)

    assert record is not None
    assert record.status == "failed"
    assert record.error == "工作流执行失败"
    assert record.result is None

    optimizer_log = next(
        log for log in record.node_logs if log.node == "content_optimizer"
    )
    assert optimizer_log.status == "error"
    assert optimizer_log.message == "LLM 返回结构异常"

    async with database.async_session_factory() as session:
        stored_task = await session.get(Task, task_id)

    assert stored_task is not None
    assert stored_task.status == "failed"
    assert stored_task.error_msg == "工作流执行失败"
    assert stored_task.completed_at is not None


@pytest.mark.asyncio
async def test_task_store_persists_node_level_token_usage():
    """R4 审计信息应按节点落入 Task.token_usage，且不影响既有结构。"""

    task_id = "23222222-2222-2222-2222-222222222222"
    audit_payload = {
        "provider": "anthropic",
        "model": "claude-3-5-sonnet",
        "usage": {
            "input_tokens": 120,
            "output_tokens": 45,
            "total_tokens": 165,
        },
        "cost_usd": 0.001035,
        "attempts": [],
        "fallback_used": True,
    }

    await task_store.create_task(task_id, jd_text="需要具备系统设计能力。")
    updated = await task_store.record_node_token_usage(
        task_id,
        "content_optimizer",
        audit_payload,
    )

    assert updated is True

    async with database.async_session_factory() as session:
        stored_task = await session.get(Task, task_id)

    assert stored_task is not None
    assert stored_task.token_usage["nodes"]["content_optimizer"] == audit_payload


def test_task_status_api_maps_database_fields_to_existing_contract(client):
    """状态查询接口应继续返回既有 API 字段，而不是直接暴露数据库字段名。"""
    task_id = "33333333-3333-3333-3333-333333333333"
    result_payload = {
        "name": "李四",
        "contact": {"email": "lisi@example.com"},
        "summary": "拥有 3 年数据分析经验。",
        "sections": [],
    }

    asyncio.run(task_store.create_task(task_id, jd_text="需要熟悉数据建模。"))
    asyncio.run(task_store.mark_task_complete(task_id, result_payload))
    access_token = create_access_token(subject=SYSTEM_USER_ID)

    response = client.get(
        f"/api/v1/tasks/{task_id}",
        headers={"Authorization": f"Bearer {access_token}"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["task_id"] == task_id
    assert payload["status"] == "completed"
    assert payload["result"] == result_payload
    assert payload["error"] is None
    assert isinstance(payload["created_at"], float)
    assert isinstance(payload["node_logs"], list)
