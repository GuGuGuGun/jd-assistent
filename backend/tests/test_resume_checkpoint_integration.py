"""
恢复服务与 LangGraph checkpoint 集成测试。
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_run_optimize_task_passes_task_scoped_thread_id(monkeypatch):
    """运行工作流时，应将业务 task_id 透传为 LangGraph thread_id。"""
    from ..services import resume_service

    captured: dict[str, object] = {}

    class FakeWorkflow:
        async def astream(self, initial_state, config=None):
            captured["initial_state"] = initial_state
            captured["config"] = config
            yield {
                "final_typesetter": {
                    "final_resume": {
                        "name": "张三",
                        "contact": {},
                        "summary": "完成",
                        "sections": [],
                    }
                }
            }

    async def fake_mark_node_start(task_id: str, node_name: str, message: str = ""):
        return None

    async def fake_mark_node_complete(task_id: str, node_name: str):
        return None

    async def fake_mark_review_feedback(task_id: str, passed: bool, feedback: str = ""):
        return None

    async def fake_mark_task_complete(task_id: str, result: dict):
        captured["result"] = result

    monkeypatch.setattr(resume_service, "get_workflow", lambda: FakeWorkflow())
    monkeypatch.setattr(
        resume_service.task_store, "mark_node_start", fake_mark_node_start
    )
    monkeypatch.setattr(
        resume_service.task_store, "mark_node_complete", fake_mark_node_complete
    )
    monkeypatch.setattr(
        resume_service.task_store, "mark_review_feedback", fake_mark_review_feedback
    )
    monkeypatch.setattr(
        resume_service.task_store, "mark_task_complete", fake_mark_task_complete
    )

    await resume_service.run_optimize_task(
        task_id="task-thread-1",
        resume_text="原始简历",
        jd_text="目标 JD",
    )

    assert captured["config"] == {
        "configurable": {
            "thread_id": "task-thread-1",
            "checkpoint_ns": "",
        }
    }
