"""管理员 API。"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import distinct, func, select

from ..auth.admin_guard import is_admin_email, require_admin
from ..db import database
from ..db.models import Task, User
from ..schemas.api import (
    AdminCreditAdjustmentRequest,
    AdminStatsResponse,
    AdminTaskListItem,
    AdminTaskListResponse,
    AdminUserListItem,
    AdminUserListResponse,
    CurrentUserResponse,
)
from ..services.task_store import task_store
from ..services.billing_service import InsufficientCreditsError, billing_service

router = APIRouter(prefix="/api/v1/admin", tags=["管理员"])


def _to_timestamp(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _build_user_payload(user: User) -> CurrentUserResponse:
    return CurrentUserResponse(
        id=user.id,
        email=user.email,
        auth_provider=user.auth_provider,
        credits=user.credits,
        tier=user.tier,
        is_admin=is_admin_email(user.email),
    )


def _extract_llm_metrics(task: Task) -> tuple[int, float]:
    token_usage = dict(task.token_usage or {})
    nodes = dict(token_usage.get("nodes") or {})

    total_tokens = 0
    total_cost_usd = 0.0
    for node_payload in nodes.values():
        if not isinstance(node_payload, dict):
            continue

        usage = dict(node_payload.get("usage") or {})
        total_tokens += int(usage.get("total_tokens") or 0)
        total_cost_usd += float(node_payload.get("cost_usd") or 0.0)

    return total_tokens, round(total_cost_usd, 8)


@router.get(
    "/users", response_model=AdminUserListResponse, summary="管理员查看用户列表"
)
async def list_users(
    _: User = Depends(require_admin),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    email: str | None = Query(None),
    tier: str | None = Query(None),
    is_admin: bool | None = Query(None),
):
    await task_store.ensure_ready()
    offset = (page - 1) * page_size

    normalized_email = email.strip().lower() if email else None
    normalized_tier = tier.strip().lower() if tier else None

    def _matches_admin(user_email: str):
        is_admin_flag = is_admin_email(user_email)
        return is_admin is None or is_admin_flag is is_admin

    async with database.async_session_factory() as session:
        users_query = select(User).order_by(User.created_at.desc())
        if normalized_email:
            users_query = users_query.where(
                func.lower(User.email).contains(normalized_email)
            )
        if normalized_tier:
            users_query = users_query.where(func.lower(User.tier) == normalized_tier)

        candidate_users = (await session.execute(users_query)).scalars().all()
        filtered_users = [
            user for user in candidate_users if _matches_admin(user.email)
        ]
        total = len(filtered_users)
        users = filtered_users[offset : offset + page_size]

    return AdminUserListResponse(
        items=[
            AdminUserListItem(
                id=user.id,
                email=user.email,
                auth_provider=user.auth_provider,
                credits=user.credits,
                tier=user.tier,
                created_at=_to_timestamp(user.created_at) or 0.0,
                is_admin=is_admin_email(user.email),
            )
            for user in users
        ],
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.get(
    "/tasks", response_model=AdminTaskListResponse, summary="管理员查看任务列表"
)
async def list_tasks(
    _: User = Depends(require_admin),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None),
    user_email: str | None = Query(None),
    original_file: str | None = Query(None),
):
    await task_store.ensure_ready()
    offset = (page - 1) * page_size

    normalized_status = status.strip().lower() if status else None
    normalized_user_email = user_email.strip().lower() if user_email else None
    normalized_original_file = original_file.strip().lower() if original_file else None

    async with database.async_session_factory() as session:
        task_query = (
            select(Task, User.email)
            .join(User, User.id == Task.user_id)
            .order_by(Task.created_at.desc())
        )

        if normalized_status:
            task_query = task_query.where(func.lower(Task.status) == normalized_status)
        if normalized_user_email:
            task_query = task_query.where(
                func.lower(User.email).contains(normalized_user_email)
            )
        if normalized_original_file:
            task_query = task_query.where(
                func.lower(Task.original_file).contains(normalized_original_file)
            )

        all_rows = (await session.execute(task_query)).all()
        total = len(all_rows)
        rows = all_rows[offset : offset + page_size]

    items = [
        AdminTaskListItem(
            task_id=task.id,
            user_id=task.user_id,
            user_email=user_email,
            status=task.status,
            original_file=task.original_file,
            created_at=_to_timestamp(task.created_at) or 0.0,
            completed_at=_to_timestamp(task.completed_at),
            error=task.error_msg,
        )
        for task, user_email in rows
    ]

    return AdminTaskListResponse(
        items=items,
        total=int(total or 0),
        page=page,
        page_size=page_size,
    )


@router.get("/stats", response_model=AdminStatsResponse, summary="管理员查看平台统计")
async def get_admin_stats(_: User = Depends(require_admin)):
    await task_store.ensure_ready()
    seven_days_ago = datetime.now(timezone.utc) - timedelta(days=7)

    async with database.async_session_factory() as session:
        tasks = (
            (await session.execute(select(Task).order_by(Task.created_at.desc())))
            .scalars()
            .all()
        )
        total_users = (
            await session.execute(select(func.count()).select_from(User))
        ).scalar_one()
        active_users_7d = (
            await session.execute(
                select(func.count(distinct(Task.user_id))).where(
                    Task.created_at >= seven_days_ago
                )
            )
        ).scalar_one()

    total_tasks = len(tasks)
    completed_tasks = 0
    failed_tasks = 0
    total_llm_cost_usd = 0.0

    for task in tasks:
        _, task_cost_usd = _extract_llm_metrics(task)
        total_llm_cost_usd += task_cost_usd
        if task.status == "completed":
            completed_tasks += 1
        elif task.status == "failed":
            failed_tasks += 1

    return AdminStatsResponse(
        total_users=int(total_users or 0),
        total_tasks=int(total_tasks or 0),
        completed_tasks=int(completed_tasks or 0),
        failed_tasks=int(failed_tasks or 0),
        active_users_7d=int(active_users_7d or 0),
        llm_cost_usd=round(total_llm_cost_usd, 8),
    )


@router.post(
    "/users/{user_id}/credits",
    response_model=CurrentUserResponse,
    summary="管理员调整用户额度",
)
async def adjust_user_credits(
    user_id: str,
    payload: AdminCreditAdjustmentRequest,
    _: User = Depends(require_admin),
):
    """管理员手动调整用户额度。"""
    await task_store.ensure_ready()
    try:
        user = await billing_service.adjust_user_credits(
            user_id=user_id,
            delta=payload.delta,
            reason=payload.reason,
            created_by=_.id,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc

    return _build_user_payload(user)
