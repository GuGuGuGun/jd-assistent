"""
API 路由处理器 — 核心业务端点。
"""

import json
import uuid
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import AsyncGenerator, Sequence

from fastapi import APIRouter, Depends, HTTPException, File, Form, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from ..auth.admin_guard import is_admin_email
from ..auth.dependencies import get_current_user, get_current_user_for_sse
from ..db import database
from ..db.models import CreditLedger, Task, UserProfile as UserProfileAsset
from ..db.models import User
from ..schemas.api import (
    DashboardCreditChartPoint,
    DashboardCreditChartResponse,
    DashboardProfileSummaryResponse,
    DashboardResponse,
    DashboardSummaryResponse,
    DashboardTaskHistoryItem,
    OptimizeRequest,  # No longer strictly needed for this route but kept for reference
    TaskCreatedResponse,
    TaskStatusResponse,
    ErrorResponse,
)
from ..services.billing_service import InsufficientCreditsError, billing_service
from ..services.task_store import task_store
from ..services.task_dispatcher import create_task_dispatcher
from ..utils.parser import parse_resume_file
from ..utils.text_sanitizer import sanitize_resume_payload, sanitize_resume_text

logger = logging.getLogger("jd_assistent.api")

router = APIRouter(prefix="/api/v1", tags=["简历优化"])
task_dispatcher = create_task_dispatcher()


def _to_timestamp(value: datetime | None) -> float | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.timestamp()


def _to_utc_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


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


def _build_credit_chart(
    ledger_entries: Sequence[CreditLedger], current_user: User
) -> DashboardCreditChartResponse:
    today = datetime.now(timezone.utc).date()
    window_dates = [today - timedelta(days=offset) for offset in range(6, -1, -1)]
    daily_delta_map: dict[str, int] = {day.isoformat(): 0 for day in window_dates}
    daily_reason_map: dict[str, list[str]] = {
        day.isoformat(): [] for day in window_dates
    }

    for entry in sorted(ledger_entries, key=lambda item: (item.created_at, item.id)):
        entry_date = _to_utc_datetime(entry.created_at).date().isoformat()
        if entry_date not in daily_delta_map:
            continue

        daily_delta_map[entry_date] += int(entry.delta)
        if entry.reason:
            daily_reason_map[entry_date].append(str(entry.reason))

    running_balance = int(current_user.credits)
    points_by_date: dict[str, DashboardCreditChartPoint] = {}
    for day in reversed(window_dates):
        key = day.isoformat()
        reasons = daily_reason_map[key]
        unique_reasons = list(dict.fromkeys(reasons))
        points_by_date[key] = DashboardCreditChartPoint(
            date=key,
            balance=running_balance,
            delta=daily_delta_map[key],
            reason=" / ".join(unique_reasons),
        )
        running_balance -= daily_delta_map[key]

    points = [points_by_date[day.isoformat()] for day in window_dates]

    return DashboardCreditChartResponse(
        metric_basis="balance_history",
        current_credits=current_user.credits,
        tier=current_user.tier,
        series=points,
    )


def _build_profile_summary(
    current_user: User,
    tasks: Sequence[Task],
    summary: DashboardSummaryResponse,
    profile: UserProfileAsset | None,
) -> DashboardProfileSummaryResponse:
    """构建最小真实画像摘要。"""

    last_completed_at = None
    for task in tasks:
        if task.completed_at is None:
            continue
        if last_completed_at is None or task.completed_at > last_completed_at:
            last_completed_at = task.completed_at

    top_skill_categories: list[str] = []
    experience_count = 0
    education_count = 0
    last_updated = None
    profile_ready = False

    if profile is not None:
        profile_ready = True
        experience_count = len(profile.raw_experiences or [])
        education_count = len(profile.education or [])
        top_skill_categories = [
            category
            for category, skills in sorted(
                dict(profile.skill_matrix or {}).items(),
                key=lambda item: (-len(item[1] or []), item[0]),
            )[:3]
        ]
        last_updated = _to_timestamp(profile.last_updated)

    return DashboardProfileSummaryResponse(
        profile_ready=profile_ready,
        email=current_user.email,
        tier=current_user.tier,
        credits_balance=current_user.credits,
        auth_provider=current_user.auth_provider,
        is_admin=is_admin_email(current_user.email),
        total_tasks=summary.total_tasks,
        completed_tasks=summary.completed_tasks,
        failed_tasks=summary.failed_tasks,
        processing_tasks=summary.processing_tasks,
        total_tokens=summary.total_tokens,
        total_llm_cost_usd=summary.total_llm_cost_usd,
        experience_count=experience_count,
        education_count=education_count,
        top_skill_categories=top_skill_categories,
        last_updated=last_updated,
        last_completed_at=_to_timestamp(last_completed_at),
    )


# ═══════════════════════════════════════
# POST /api/v1/optimize — 提交优化任务
# ═══════════════════════════════════════


@router.post(
    "/optimize",
    response_model=TaskCreatedResponse,
    summary="提交简历优化任务",
    description="上传纯文本、PDF、DOCX或MD格式的简历文件并粘贴目标 JD，系统将异步执行 Multi-Agent 优化流程。",
)
async def create_optimize_task(
    resume_file: UploadFile = File(..., description="上传的简历文件"),
    jd_text: str = Form(..., min_length=10, description="目标岗位的 JD 文本"),
    current_user: User = Depends(get_current_user),
):
    """创建简历优化任务并在后台异步执行。"""

    # 解析文件
    try:
        file_bytes = await resume_file.read()
        filename = resume_file.filename or "resume.txt"
        resume_text = parse_resume_file(filename, file_bytes)
        jd_text = sanitize_resume_text(jd_text)

        if not resume_text.strip():
            raise ValueError("解析后的简历为空，请检查文件内容")
    except ValueError as e:
        # parser 明确抛出的 ValueError
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"文件读取或解析异常: {e}")
        raise HTTPException(
            status_code=400, detail="文件读取或解析异常，请确保格式正确。"
        )

    task_id = str(uuid.uuid4())

    # 创建任务记录
    try:
        await billing_service.create_task_with_reservation(
            task_id=task_id,
            jd_text=jd_text,
            original_file=resume_file.filename or "resume.txt",
            user_id=current_user.id,
        )
    except InsufficientCreditsError as exc:
        raise HTTPException(
            status_code=status.HTTP_402_PAYMENT_REQUIRED,
            detail=str(exc),
        ) from exc

    # 设计意图：路由层只做任务建档与派发，不感知 local / Celery 的执行细节，
    # 从而保持 REST/SSE 契约稳定，并为跨进程 worker 扩展预留统一入口。
    try:
        await task_dispatcher.dispatch(
            task_id=task_id,
            resume_text=resume_text,
            jd_text=jd_text,
        )
    except Exception as exc:
        logger.exception("任务 %s 派发失败: %s", task_id, str(exc))
        await task_store.mark_task_failed(task_id, f"任务派发失败: {str(exc)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="任务派发失败，请稍后重试。",
        ) from exc

    logger.info("任务 %s 已创建", task_id)

    return TaskCreatedResponse(task_id=task_id)


# ═══════════════════════════════════════
# GET /api/v1/export/docx/{task_id} — 导出 Word 文档
# ═══════════════════════════════════════
from ..utils.docx_exporter import export_resume_to_docx
from urllib.parse import quote


@router.get(
    "/export/docx/{task_id}",
    summary="导出 Word 文档",
    description="将指定任务的最终排版结果渲染为 .docx 文件供下载。",
)
async def export_task_docx(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    record = await task_store.get_task_for_user(task_id, current_user.id)
    if not record or not record.result:
        raise HTTPException(status_code=404, detail="任务不存在或尚未完成生成")

    sanitized_result = sanitize_resume_payload(record.result)
    doc_stream = export_resume_to_docx(sanitized_result)

    # 兼容中文文件名下载
    name = sanitized_result.get("name", "Resume")
    filename = quote(f"{name}_Optimized.docx")

    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{filename}"}

    return StreamingResponse(
        doc_stream,
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers=headers,
    )


# ═══════════════════════════════════════
# GET /api/v1/tasks/{task_id} — 查询任务状态
# ═══════════════════════════════════════


@router.get(
    "/tasks/{task_id}",
    response_model=TaskStatusResponse,
    summary="查询任务状态与结果",
    responses={404: {"model": ErrorResponse}},
)
async def get_task_status(
    task_id: str,
    current_user: User = Depends(get_current_user),
):
    """查询指定任务的当前状态、节点日志和最终结果。"""
    record = await task_store.get_task_for_user(task_id, current_user.id)
    if not record:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    payload = record.to_dict()
    if payload.get("result"):
        payload["result"] = sanitize_resume_payload(payload["result"])
    return TaskStatusResponse(**payload)


@router.get(
    "/dashboard",
    response_model=DashboardResponse,
    summary="获取当前用户 Dashboard 数据",
)
async def get_dashboard_data(
    current_user: User = Depends(get_current_user),
):
    """返回当前用户的任务摘要与最近任务列表。"""
    await task_store.ensure_ready()

    async with database.async_session_factory() as session:
        tasks = (
            (
                await session.execute(
                    select(Task)
                    .where(Task.user_id == current_user.id)
                    .order_by(Task.created_at.desc(), Task.id.desc())
                )
            )
            .scalars()
            .all()
        )
        profile = (
            await session.execute(
                select(UserProfileAsset).where(
                    UserProfileAsset.user_id == current_user.id
                )
            )
        ).scalar_one_or_none()
        ledger_entries = (
            (
                await session.execute(
                    select(CreditLedger)
                    .where(CreditLedger.user_id == current_user.id)
                    .order_by(CreditLedger.created_at.asc(), CreditLedger.id.asc())
                )
            )
            .scalars()
            .all()
        )

    completed_tasks = 0
    processing_tasks = 0
    failed_tasks = 0
    total_tokens = 0
    total_llm_cost_usd = 0.0
    recent_tasks: list[DashboardTaskHistoryItem] = []

    for task in tasks:
        task_total_tokens, task_cost_usd = _extract_llm_metrics(task)
        total_tokens += task_total_tokens
        total_llm_cost_usd += task_cost_usd

        mapped_status = task_store._map_task_status(task.status)
        if mapped_status == "completed":
            completed_tasks += 1
        elif mapped_status == "failed":
            failed_tasks += 1
        else:
            processing_tasks += 1

        if len(recent_tasks) < 20:
            recent_tasks.append(
                DashboardTaskHistoryItem(
                    task_id=task.id,
                    status=mapped_status,
                    original_file=task.original_file,
                    created_at=_to_timestamp(task.created_at) or 0.0,
                    completed_at=_to_timestamp(task.completed_at),
                    duration_ms=task.duration_ms,
                    error=task.error_msg,
                    total_tokens=task_total_tokens,
                    llm_cost_usd=task_cost_usd,
                )
            )

    summary = DashboardSummaryResponse(
        total_tasks=len(tasks),
        completed_tasks=completed_tasks,
        processing_tasks=processing_tasks,
        failed_tasks=failed_tasks,
        total_tokens=total_tokens,
        total_llm_cost_usd=round(total_llm_cost_usd, 8),
    )

    return DashboardResponse(
        summary=summary,
        recent_tasks=recent_tasks,
        credit_chart=_build_credit_chart(ledger_entries, current_user),
        profile_summary=_build_profile_summary(current_user, tasks, summary, profile),
    )


# ═══════════════════════════════════════
# GET /api/v1/tasks/{task_id}/stream — SSE 进度推送
# ═══════════════════════════════════════


async def _sse_event_generator(task_id: str) -> AsyncGenerator[str, None]:
    """生成 SSE 事件流。"""
    record = await task_store.get_task(task_id)
    if not record:
        yield f"event: error\ndata: {json.dumps({'error': '任务不存在'})}\n\n"
        return

    replay_events = await task_store.replay_task_events(task_id)
    for event in replay_events:
        event_type = event.get("event", "message")
        event_data = json.dumps(event.get("data", {}), ensure_ascii=False)
        yield f"event: {event_type}\ndata: {event_data}\n\n"
        if event_type in ("complete", "error"):
            return

    subscriber = await task_store.subscribe_to_events(task_id)

    try:
        while True:
            try:
                # 设计意图：先基于数据库回放当前状态，再接入实时订阅，确保重连客户端可以恢复到最近视图。
                event = await asyncio.wait_for(subscriber.get(), timeout=30.0)

                event_type = event.get("event", "message")
                event_data = json.dumps(event.get("data", {}), ensure_ascii=False)
                yield f"event: {event_type}\ndata: {event_data}\n\n"

                if event_type in ("complete", "error"):
                    return

            except asyncio.TimeoutError:
                yield f"event: heartbeat\ndata: {json.dumps({'ping': True})}\n\n"

            except Exception as e:
                logger.error("SSE 事件流错误: %s", str(e))
                yield f"event: error\ndata: {json.dumps({'error': str(e)})}\n\n"
                return
    finally:
        await subscriber.close()


@router.get(
    "/tasks/{task_id}/stream",
    summary="SSE 实时进度推送",
    description="通过 Server-Sent Events 实时推送各节点的执行进度。",
    responses={404: {"model": ErrorResponse}},
)
async def stream_task_progress(
    task_id: str,
    current_user: User = Depends(get_current_user_for_sse),
):
    """SSE 实时推送任务进度。"""
    record = await task_store.get_task_for_user(task_id, current_user.id)
    if not record:
        raise HTTPException(status_code=404, detail=f"任务 {task_id} 不存在")

    return StreamingResponse(
        _sse_event_generator(task_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
