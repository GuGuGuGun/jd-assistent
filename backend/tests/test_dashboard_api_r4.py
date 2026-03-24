"""R4 Dashboard 用户接口测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from ..db import database
from ..db.models import CreditLedger, Task, UserProfile
from ..services.billing_service import billing_service
from ..services.task_store import task_store
from .test_auth_and_r3_api import _auth_headers, _register_user


def test_dashboard_route_requires_authentication(client):
    """Dashboard 数据接口应保持鉴权保护。"""

    response = client.get("/api/v1/dashboard")

    assert response.status_code == 401


def test_dashboard_returns_user_scoped_summary_history_chart_and_profile(client):
    """Dashboard 应返回当前用户的摘要、历史、近 7 天按日额度趋势与画像摘要。"""

    owner_payload = _register_user(client, "dashboard-owner@example.com")
    other_payload = _register_user(client, "dashboard-other@example.com")

    owner_task_id = "77777777-7777-7777-7777-777777777777"
    failed_task_id = "88888888-8888-8888-8888-888888888888"
    other_task_id = "99999999-9999-9999-9999-999999999999"

    asyncio.run(
        task_store.create_task(
            owner_task_id,
            jd_text="需要熟悉 Dashboard 指标设计。",
            original_file="owner-resume.pdf",
            user_id=owner_payload["user"]["id"],
        )
    )
    asyncio.run(
        task_store.record_node_token_usage(
            owner_task_id,
            "content_optimizer",
            {
                "provider": "openai",
                "model": "gpt-4o",
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                },
                "cost_usd": 0.0008,
                "attempts": [],
                "fallback_used": False,
            },
        )
    )
    asyncio.run(
        task_store.mark_task_complete(owner_task_id, {"name": "王五", "sections": []})
    )
    asyncio.run(billing_service.finalize_task_charge(owner_task_id))

    asyncio.run(
        task_store.create_task(
            failed_task_id,
            jd_text="需要熟悉失败态展示。",
            original_file="failed-resume.docx",
            user_id=owner_payload["user"]["id"],
        )
    )
    asyncio.run(task_store.mark_task_failed(failed_task_id, "模型限流"))

    async def _seed_profile_and_timestamps():
        async with database.async_session_factory() as session:
            owner_task = await session.get(Task, owner_task_id)
            failed_task = await session.get(Task, failed_task_id)

            assert owner_task is not None
            assert failed_task is not None

            owner_task.created_at = datetime.now(timezone.utc) - timedelta(days=1)
            owner_task.billing_charged_at = datetime.now(timezone.utc) - timedelta(
                days=1
            )
            failed_task.created_at = datetime.now(timezone.utc)

            session.add(
                UserProfile(
                    user_id=owner_payload["user"]["id"],
                    skill_matrix={
                        "backend": ["FastAPI", "SQLAlchemy", "Redis"],
                        "frontend": ["Vue", "TypeScript"],
                    },
                    raw_experiences=[
                        {"company": "A 公司", "title": "后端工程师"},
                        {"company": "B 公司", "title": "全栈工程师"},
                    ],
                    education=[
                        {"school": "测试大学", "degree": "本科", "major": "计算机科学"}
                    ],
                )
            )
            ledger_entries = (
                (
                    await session.execute(
                        select(CreditLedger).where(
                            CreditLedger.user_id == owner_payload["user"]["id"]
                        )
                    )
                )
                .scalars()
                .all()
            )
            for entry in ledger_entries:
                if entry.reason == "initial_grant":
                    entry.created_at = datetime.now(timezone.utc) - timedelta(days=2)
                elif entry.task_id == owner_task_id:
                    entry.created_at = datetime.now(timezone.utc) - timedelta(days=1)

            await session.commit()

    asyncio.run(_seed_profile_and_timestamps())

    asyncio.run(
        task_store.create_task(
            other_task_id,
            jd_text="需要隔离其它用户任务。",
            original_file="other-resume.txt",
            user_id=other_payload["user"]["id"],
        )
    )

    response = client.get(
        "/api/v1/dashboard",
        headers=_auth_headers(owner_payload["access_token"]),
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["summary"]["total_tasks"] == 2
    assert payload["summary"]["completed_tasks"] == 1
    assert payload["summary"]["failed_tasks"] == 1
    assert payload["summary"]["processing_tasks"] == 0
    assert payload["summary"]["total_tokens"] == 120
    assert payload["summary"]["total_llm_cost_usd"] == 0.0008
    assert [item["task_id"] for item in payload["recent_tasks"]] == [
        failed_task_id,
        owner_task_id,
    ]
    assert payload["recent_tasks"][1]["total_tokens"] == 120
    assert payload["recent_tasks"][1]["llm_cost_usd"] == 0.0008
    assert payload["recent_tasks"][0]["error"] == "模型限流"
    assert payload["credit_chart"]["metric_basis"] == "balance_history"
    assert payload["credit_chart"]["current_credits"] == 4
    assert payload["credit_chart"]["tier"] == "free"
    assert len(payload["credit_chart"]["series"]) == 7
    assert payload["credit_chart"]["series"][-1]["balance"] == 4
    assert payload["credit_chart"]["series"][-1]["delta"] == 0
    assert payload["credit_chart"]["series"][-1]["reason"] == ""
    assert payload["credit_chart"]["series"][-2]["balance"] == 4
    assert payload["credit_chart"]["series"][-2]["delta"] == -1
    assert payload["credit_chart"]["series"][-2]["reason"] == "task_completion_charge"
    assert payload["credit_chart"]["series"][-3]["balance"] == 5
    assert payload["credit_chart"]["series"][-3]["delta"] == 5
    assert payload["credit_chart"]["series"][-3]["reason"] == "initial_grant"
    assert payload["profile_summary"]["profile_ready"] is True
    assert payload["profile_summary"]["experience_count"] == 2
    assert payload["profile_summary"]["education_count"] == 1
    assert payload["profile_summary"]["top_skill_categories"] == ["backend", "frontend"]
    assert payload["profile_summary"]["email"] == "dashboard-owner@example.com"

    async def _assert_other_task_not_included():
        async with database.async_session_factory() as session:
            other_task = await session.get(Task, other_task_id)

        assert other_task is not None

    asyncio.run(_assert_other_task_not_included())


def test_dashboard_profile_summary_stays_null_safe_without_profile(client):
    """当用户还没有持久化画像时，Dashboard 仍应返回可渲染的空摘要。"""

    payload = _register_user(client, "profile-empty@example.com")

    response = client.get(
        "/api/v1/dashboard",
        headers=_auth_headers(payload["access_token"]),
    )

    assert response.status_code == 200
    data = response.json()
    assert data["profile_summary"]["profile_ready"] is False
    assert data["profile_summary"]["top_skill_categories"] == []
    assert data["profile_summary"]["experience_count"] == 0
    assert data["profile_summary"]["education_count"] == 0
    assert len(data["credit_chart"]["series"]) == 7
    assert data["credit_chart"]["series"][-1]["reason"] == "initial_grant"
    assert data["credit_chart"]["series"][-1]["balance"] == 5
    assert data["credit_chart"]["series"][0]["balance"] == 0
