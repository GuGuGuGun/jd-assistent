"""R3 认证、权限与计费相关测试。"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import select

from ..config import auth_config
from ..db import database
from ..db.models import CreditLedger, Task, User
from ..services.billing_service import InsufficientCreditsError, billing_service
from ..services.task_store import task_store


def _register_user(client, email: str, password: str = "password123") -> dict:
    response = client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": password},
    )
    assert response.status_code == 201
    return response.json()


def _auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_register_login_and_me_flow_persists_hashed_password(client):
    """注册、登录与 /auth/me 应构成完整闭环，且密码只能以哈希形式入库。"""
    register_payload = _register_user(client, "user@example.com")

    assert register_payload["token_type"] == "bearer"
    assert register_payload["user"]["email"] == "user@example.com"
    assert register_payload["user"]["credits"] == 5
    assert register_payload["user"]["tier"] == "free"

    me_response = client.get(
        "/api/v1/auth/me",
        headers=_auth_headers(register_payload["access_token"]),
    )
    assert me_response.status_code == 200
    assert me_response.json()["email"] == "user@example.com"

    login_response = client.post(
        "/api/v1/auth/login",
        json={"email": "user@example.com", "password": "password123"},
    )
    assert login_response.status_code == 200
    assert login_response.json()["access_token"]

    async def _assert_password_hashed():
        async with database.async_session_factory() as session:
            user = (
                await session.execute(
                    select(User).where(User.email == "user@example.com")
                )
            ).scalar_one()
            ledger_entries = (
                (
                    await session.execute(
                        select(CreditLedger).where(CreditLedger.user_id == user.id)
                    )
                )
                .scalars()
                .all()
            )

        assert user.hashed_pw is not None
        assert user.hashed_pw != "password123"
        assert len(ledger_entries) == 1
        assert ledger_entries[0].reason == "initial_grant"

    asyncio.run(_assert_password_hashed())


def test_protected_routes_require_authentication(client):
    """受保护的业务路由在未登录时必须返回 401。"""
    response = client.get("/api/v1/tasks/non-existent-task")

    assert response.status_code == 401


def test_task_routes_are_scoped_to_current_user(client):
    """任务状态查询应只允许任务所属用户访问。"""
    owner_payload = _register_user(client, "owner@example.com")
    other_payload = _register_user(client, "other@example.com")
    owner_id = owner_payload["user"]["id"]
    task_id = "44444444-4444-4444-4444-444444444444"

    result_payload = {
        "name": "王五",
        "contact": {"email": "wangwu@example.com"},
        "summary": "拥有 6 年全栈开发经验。",
        "sections": [],
    }

    asyncio.run(
        task_store.create_task(
            task_id,
            jd_text="需要熟悉 FastAPI 与 Vue 3。",
            original_file="resume.txt",
            user_id=owner_id,
        )
    )
    asyncio.run(task_store.mark_task_complete(task_id, result_payload))

    owner_response = client.get(
        f"/api/v1/tasks/{task_id}",
        headers=_auth_headers(owner_payload["access_token"]),
    )
    assert owner_response.status_code == 200
    assert owner_response.json()["result"] == result_payload

    other_response = client.get(
        f"/api/v1/tasks/{task_id}",
        headers=_auth_headers(other_payload["access_token"]),
    )
    assert other_response.status_code == 404


def test_query_token_is_restricted_to_sse_route(client):
    """URL 中的 access_token 只允许 SSE 进度流使用，普通接口必须拒绝。"""
    owner_payload = _register_user(client, "sse-owner@example.com")
    task_id = "66666666-6666-6666-6666-666666666666"
    result_payload = {
        "name": "赵六",
        "contact": {"email": "zhaoliu@example.com"},
        "summary": "拥有 8 年分布式系统经验。",
        "sections": [],
    }

    asyncio.run(
        task_store.create_task(
            task_id,
            jd_text="需要熟悉 SSE 与权限隔离。",
            original_file="resume.txt",
            user_id=owner_payload["user"]["id"],
        )
    )
    asyncio.run(task_store.mark_task_complete(task_id, result_payload))

    forbidden_response = client.get(
        f"/api/v1/tasks/{task_id}?access_token={owner_payload['access_token']}"
    )
    assert forbidden_response.status_code == 401

    stream_response = client.get(
        f"/api/v1/tasks/{task_id}/stream?access_token={owner_payload['access_token']}"
    )
    assert stream_response.status_code == 200
    assert "text/event-stream" in stream_response.headers["content-type"]


def test_admin_routes_enforce_permission_boundary(client, monkeypatch):
    """管理员接口应拒绝普通用户，并允许白名单管理员访问。"""
    monkeypatch.setattr(auth_config, "ADMIN_EMAILS", ("admin@example.com",))

    normal_user = _register_user(client, "member@example.com")
    admin_user = _register_user(client, "admin@example.com")

    forbidden_response = client.get(
        "/api/v1/admin/users",
        headers=_auth_headers(normal_user["access_token"]),
    )
    assert forbidden_response.status_code == 403

    allowed_response = client.get(
        "/api/v1/admin/users",
        headers=_auth_headers(admin_user["access_token"]),
    )
    assert allowed_response.status_code == 200
    assert allowed_response.json()["total"] >= 2


def test_admin_user_list_supports_filters_and_pagination(client, monkeypatch):
    """管理员用户列表应支持 email/tier/is_admin 过滤与分页。"""
    monkeypatch.setattr(
        auth_config, "ADMIN_EMAILS", ("admin@example.com", "boss@example.com")
    )

    admin_payload = _register_user(client, "admin@example.com")
    _register_user(client, "boss@example.com")
    _register_user(client, "member@example.com")

    filtered_response = client.get(
        "/api/v1/admin/users",
        params={"email": "boss", "is_admin": "true", "page": 1, "page_size": 5},
        headers=_auth_headers(admin_payload["access_token"]),
    )

    assert filtered_response.status_code == 200
    payload = filtered_response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["email"] == "boss@example.com"
    assert payload["items"][0]["is_admin"] is True


def test_admin_task_list_supports_filters_and_pagination(client, monkeypatch):
    """管理员任务列表应支持状态、邮箱和文件名过滤。"""
    monkeypatch.setattr(auth_config, "ADMIN_EMAILS", ("admin@example.com",))
    admin_payload = _register_user(client, "admin@example.com")
    member_payload = _register_user(client, "member-task@example.com")

    async def _seed_tasks():
        await task_store.ensure_ready()
        await billing_service.create_task_with_reservation(
            task_id="11111111111111111111111111111111",
            jd_text="需要后端经验",
            original_file="alpha-resume.pdf",
            user_id=member_payload["user"]["id"],
        )
        await billing_service.create_task_with_reservation(
            task_id="22222222222222222222222222222222",
            jd_text="需要前端经验",
            original_file="beta-resume.pdf",
            user_id=admin_payload["user"]["id"],
        )
        await task_store.mark_task_failed(
            "22222222222222222222222222222222", "模拟失败"
        )

    asyncio.run(_seed_tasks())

    filtered_response = client.get(
        "/api/v1/admin/tasks",
        params={
            "status": "failed",
            "user_email": "admin@",
            "original_file": "beta",
            "page": 1,
            "page_size": 5,
        },
        headers=_auth_headers(admin_payload["access_token"]),
    )

    assert filtered_response.status_code == 200
    payload = filtered_response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["task_id"] == "22222222-2222-2222-2222-222222222222"
    assert payload["items"][0]["status"] == "failed"


def test_optimize_route_blocks_submission_when_credits_are_exhausted(client):
    """额度耗尽时，不应允许继续提交新任务。"""
    payload = _register_user(client, "lowcredits@example.com")
    user_id = payload["user"]["id"]

    async def _deplete_credits():
        async with database.async_session_factory() as session:
            user = await session.get(User, user_id)
            assert user is not None
            user.credits = 0
            await session.commit()

    asyncio.run(_deplete_credits())

    response = client.post(
        "/api/v1/optimize",
        data={"jd_text": "需要具备系统设计、FastAPI 和 Vue 经验。"},
        files={
            "resume_file": (
                "resume.txt",
                b"five years backend experience",
                "text/plain",
            )
        },
        headers=_auth_headers(payload["access_token"]),
    )

    assert response.status_code == 402
    assert "额度不足" in response.json()["detail"]


@pytest.mark.asyncio
async def test_billing_service_only_deducts_once_for_same_task():
    """同一任务重复完成回调时，额度只应扣减一次。"""
    await task_store.ensure_ready()

    async with database.async_session_factory() as session:
        user = User(
            email="billing@example.com",
            hashed_pw="hashed-value",
            credits=2,
            tier="free",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    task_id = "55555555-5555-5555-5555-555555555555"
    await billing_service.create_task_with_reservation(
        task_id=task_id,
        jd_text="需要具备资深后端开发经验。",
        original_file="resume.txt",
        user_id=user_id,
    )

    await asyncio.gather(
        billing_service.finalize_task_charge(task_id),
        billing_service.finalize_task_charge(task_id),
    )

    async with database.async_session_factory() as session:
        reloaded_user = await session.get(User, user_id)
        task = await session.get(Task, task_id)
        ledger_entries = (
            (
                await session.execute(
                    select(CreditLedger).where(CreditLedger.task_id == task_id)
                )
            )
            .scalars()
            .all()
        )

    assert reloaded_user is not None
    assert reloaded_user.credits == 1
    assert task is not None
    assert task.billing_status == "charged"
    assert task.billing_reservation_amount == 1
    assert task.billing_charged_at is not None
    assert task.billing_charge_amount == 1
    assert task.token_usage["billing"]["charged"] is True
    assert task.token_usage["billing"]["amount"] == 1
    assert len(ledger_entries) == 1
    assert ledger_entries[0].delta == -1
    assert ledger_entries[0].balance_after == 1
    assert ledger_entries[0].reason == "task_submission_reserve"


def test_register_and_admin_adjustment_write_credit_ledger(client, monkeypatch):
    """注册初始额度与管理员调额都应写入额度流水。"""

    monkeypatch.setattr(auth_config, "ADMIN_EMAILS", ("admin@example.com",))
    user_payload = _register_user(client, "ledger-user@example.com")
    admin_payload = _register_user(client, "admin@example.com")

    adjust_response = client.post(
        f"/api/v1/admin/users/{user_payload['user']['id']}/credits",
        json={"delta": 3, "reason": "补发测试额度"},
        headers=_auth_headers(admin_payload["access_token"]),
    )

    assert adjust_response.status_code == 200
    assert adjust_response.json()["credits"] == 8

    async def _assert_ledger_entries():
        async with database.async_session_factory() as session:
            entries = (
                (
                    await session.execute(
                        select(CreditLedger)
                        .where(CreditLedger.user_id == user_payload["user"]["id"])
                        .order_by(CreditLedger.created_at.asc())
                    )
                )
                .scalars()
                .all()
            )

        assert len(entries) == 2
        assert entries[0].reason == "initial_grant"
        assert entries[0].delta == 5
        assert entries[0].balance_after == 5
        assert entries[1].reason == "admin_adjustment"
        assert entries[1].delta == 3
        assert entries[1].balance_after == 8
        assert entries[1].note == "补发测试额度"

    asyncio.run(_assert_ledger_entries())


@pytest.mark.asyncio
async def test_atomic_admin_adjustments_do_not_lose_updates(client):
    """并发管理员调额时，应基于数据库原子更新累计结果。"""

    payload = _register_user(client, "atomic-admin@example.com")
    user_id = payload["user"]["id"]

    await asyncio.gather(
        billing_service.adjust_user_credits(
            user_id=user_id,
            delta=2,
            reason="批量补发额度",
            created_by=None,
        ),
        billing_service.adjust_user_credits(
            user_id=user_id,
            delta=3,
            reason="活动赠送额度",
            created_by=None,
        ),
    )

    async with database.async_session_factory() as session:
        reloaded_user = await session.get(User, user_id)
        entries = (
            (
                await session.execute(
                    select(CreditLedger)
                    .where(CreditLedger.user_id == user_id)
                    .order_by(CreditLedger.created_at.asc(), CreditLedger.id.asc())
                )
            )
            .scalars()
            .all()
        )

    assert reloaded_user is not None
    assert reloaded_user.credits == 10
    assert sorted(entry.delta for entry in entries) == [2, 3, 5]
    assert max(entry.balance_after for entry in entries) == 10


@pytest.mark.asyncio
async def test_atomic_task_charge_blocks_overdraft_across_tasks(client):
    """同一用户两条任务并发扣费时，不应因为竞争条件出现超扣。"""

    await task_store.ensure_ready()

    payload = _register_user(client, "atomic-charge@example.com")
    user_id = payload["user"]["id"]

    async with database.async_session_factory() as session:
        user = await session.get(User, user_id)
        assert user is not None
        user.credits = 1
        await session.commit()

    first_task_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    second_task_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    await billing_service.create_task_with_reservation(
        task_id=first_task_id,
        jd_text="task1",
        original_file="a.txt",
        user_id=user_id,
    )
    with pytest.raises(InsufficientCreditsError):
        await billing_service.create_task_with_reservation(
            task_id=second_task_id,
            jd_text="task2",
            original_file="b.txt",
            user_id=user_id,
        )

    await billing_service.finalize_task_charge(first_task_id)

    async with database.async_session_factory() as session:
        reloaded_user = await session.get(User, user_id)
        entries = (
            (
                await session.execute(
                    select(CreditLedger)
                    .where(CreditLedger.user_id == user_id)
                    .order_by(CreditLedger.created_at.asc(), CreditLedger.id.asc())
                )
            )
            .scalars()
            .all()
        )

    assert reloaded_user is not None
    assert reloaded_user.credits == 0
    reserve_entries = [
        entry for entry in entries if entry.reason == "task_submission_reserve"
    ]
    assert len(reserve_entries) == 1
    assert reserve_entries[0].balance_after == 0


@pytest.mark.asyncio
async def test_failed_task_releases_reserved_credit():
    """任务失败后，应释放已预留额度并写入释放流水。"""

    await task_store.ensure_ready()

    async with database.async_session_factory() as session:
        user = User(
            email="release@example.com", hashed_pw="hashed", credits=1, tier="free"
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    task_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
    await billing_service.create_task_with_reservation(
        task_id=task_id,
        jd_text="release",
        original_file="release.txt",
        user_id=user_id,
    )
    await task_store.mark_task_failed(task_id, "模拟失败")

    async with database.async_session_factory() as session:
        user = await session.get(User, user_id)
        task = await session.get(Task, task_id)
        entries = (
            (
                await session.execute(
                    select(CreditLedger)
                    .where(CreditLedger.user_id == user_id)
                    .order_by(CreditLedger.created_at.asc(), CreditLedger.id.asc())
                )
            )
            .scalars()
            .all()
        )

    assert user is not None
    assert user.credits == 1
    assert task is not None
    assert task.billing_status == "released"
    assert [entry.reason for entry in entries][-2:] == [
        "task_submission_reserve",
        "task_failure_release",
    ]


@pytest.mark.asyncio
async def test_finalize_and_release_race_keeps_ledger_consistent():
    """成功结算与失败释放并发时，只允许其中一种结果生效，不能错账。"""

    await task_store.ensure_ready()

    async with database.async_session_factory() as session:
        user = User(
            email="race@example.com", hashed_pw="hashed", credits=1, tier="free"
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        user_id = user.id

    task_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
    await billing_service.create_task_with_reservation(
        task_id=task_id,
        jd_text="race",
        original_file="race.txt",
        user_id=user_id,
    )

    await asyncio.gather(
        billing_service.finalize_task_charge(task_id),
        task_store.mark_task_failed(task_id, "并发失败"),
        return_exceptions=True,
    )

    async with database.async_session_factory() as session:
        user = await session.get(User, user_id)
        task = await session.get(Task, task_id)
        entries = (
            (
                await session.execute(
                    select(CreditLedger)
                    .where(CreditLedger.user_id == user_id)
                    .order_by(CreditLedger.created_at.asc(), CreditLedger.id.asc())
                )
            )
            .scalars()
            .all()
        )

    assert user is not None
    assert task is not None
    if task.billing_status == "charged":
        assert user.credits == 0
        assert task.billing_charged_at is not None
        assert all(entry.reason != "task_failure_release" for entry in entries)
    elif task.billing_status == "released":
        assert user.credits == 1
        assert task.billing_charged_at is None
        assert any(entry.reason == "task_failure_release" for entry in entries)
    else:
        raise AssertionError(f"unexpected billing status: {task.billing_status}")


def test_dispatch_failure_releases_reserved_credit(client, monkeypatch):
    """任务预留成功后如果派发立即失败，应自动回滚为失败并释放额度。"""

    payload = _register_user(client, "dispatch-fail@example.com")
    user_id = payload["user"]["id"]

    async def _failing_dispatch(**_kwargs):
        raise RuntimeError("dispatcher unavailable")

    monkeypatch.setattr(
        "backend.api.routes.task_dispatcher.dispatch",
        _failing_dispatch,
    )

    response = client.post(
        "/api/v1/optimize",
        data={"jd_text": "需要具备系统设计、FastAPI 和 Vue 经验。"},
        files={
            "resume_file": (
                "resume.txt",
                b"five years backend experience",
                "text/plain",
            )
        },
        headers=_auth_headers(payload["access_token"]),
    )

    assert response.status_code == 500

    async def _assert_dispatch_failure_release():
        async with database.async_session_factory() as session:
            user = await session.get(User, user_id)
            task = (
                (
                    await session.execute(
                        select(Task)
                        .where(Task.user_id == user_id)
                        .order_by(Task.created_at.desc())
                    )
                )
                .scalars()
                .first()
            )
            entries = (
                (
                    await session.execute(
                        select(CreditLedger)
                        .where(CreditLedger.user_id == user_id)
                        .order_by(CreditLedger.created_at.asc(), CreditLedger.id.asc())
                    )
                )
                .scalars()
                .all()
            )

        assert user is not None
        assert user.credits == 5
        assert task is not None
        assert task.status == "failed"
        assert task.billing_status == "released"
        reasons = [entry.reason for entry in entries]
        assert "task_submission_reserve" in reasons
        assert "task_failure_release" in reasons

    asyncio.run(_assert_dispatch_failure_release())
